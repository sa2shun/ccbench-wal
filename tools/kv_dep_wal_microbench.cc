#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <filesystem>
#include <iostream>
#include <memory>
#include <mutex>
#include <queue>
#include <string>
#include <string_view>
#include <thread>
#include <vector>

#include <fcntl.h>
#include <unistd.h>

namespace {

enum class Mode {
  WorkerWaitDepFrontier,
  AsyncGlobalLsnPrefix,
  AsyncLocalOnlyLsn,
  AsyncDepFrontierLsn,
  AsyncDepFrontierCstamp,
};

struct Options {
  Mode mode = Mode::AsyncDepFrontierCstamp;
  uint32_t thread_num = 8;
  uint32_t logger_num = 4;
  uint32_t seconds = 2;
  uint32_t keys_per_logger = 1024;
  uint32_t remote_read_prob_ppm = 0;
  uint32_t hot_prob_ppm = 0;
  uint32_t group_size = 8;
  uint32_t flush_us = 100;
  uint32_t max_inflight = 1024;
  uint32_t prealloc_mb = 0;
  bool skip_fdatasync = false;
  std::string wal_dir = "results/kv_dep_wal_files";
};

struct alignas(64) WorkerState {
  std::atomic<uint64_t> commits{0};
  std::atomic<uint64_t> logical_commits{0};
  std::atomic<uint64_t> bytes{0};
  std::atomic<uint64_t> outstanding{0};
};

struct alignas(64) LoggerState {
  std::atomic<uint64_t> local_seq{0};
  std::atomic<uint64_t> durable_seq{0};
  std::atomic<uint64_t> fsync_count{0};
  std::atomic<uint64_t> bytes{0};
};

struct Stats {
  std::atomic<uint64_t> txns{0};
  std::atomic<uint64_t> logical_commits{0};
  std::atomic<uint64_t> acked_commits{0};
  std::atomic<uint64_t> payload_build_ns{0};
  std::atomic<uint64_t> dependency_build_ns{0};
  std::atomic<uint64_t> cstamp_alloc_ns{0};
  std::atomic<uint64_t> lsn_alloc_ns{0};
  std::atomic<uint64_t> log_enqueue_ns{0};
  std::atomic<uint64_t> mutex_wait_ns{0};
  std::atomic<uint64_t> write_ns{0};
  std::atomic<uint64_t> fdatasync_ns{0};
  std::atomic<uint64_t> worker_wait_ns{0};
  std::atomic<uint64_t> committer_event_ns{0};
  std::atomic<uint64_t> committer_queue_wait_ns{0};
  std::atomic<uint64_t> worker_stall_ns{0};
  std::atomic<uint64_t> remote_reads{0};
  std::atomic<uint64_t> hot_accesses{0};
  std::atomic<uint64_t> dep_frontier_bytes{0};
  std::atomic<uint64_t> global_atomic_count{0};
  std::atomic<uint64_t> waitlist_registrations{0};
  std::atomic<uint64_t> waitlist_pops{0};
  std::atomic<uint64_t> ready_queue_pushes{0};
  std::atomic<uint64_t> max_pending_len{0};
  std::atomic<uint64_t> max_waitlist_len{0};
  std::atomic<uint64_t> max_global_waitlist_len{0};
  std::atomic<uint64_t> waiting_conditions{0};
  std::atomic<uint64_t> bytes{0};
  std::atomic<uint64_t> fsync_count{0};
};

struct Record {
  explicit Record(uint32_t logger_num) : read_frontier(logger_num, 0), write_frontier(logger_num, 0) {}

  std::mutex mutex;
  std::vector<uint64_t> read_frontier;
  std::vector<uint64_t> write_frontier;
};

struct LatencySample {
  std::vector<uint32_t> micros;
};

uint64_t nowNs() {
  return static_cast<uint64_t>(
      std::chrono::duration_cast<std::chrono::nanoseconds>(
          std::chrono::steady_clock::now().time_since_epoch())
          .count());
}

uint64_t elapsedNs(std::chrono::steady_clock::time_point start) {
  return static_cast<uint64_t>(
      std::chrono::duration_cast<std::chrono::nanoseconds>(
          std::chrono::steady_clock::now() - start)
          .count());
}

void die(std::string_view msg) {
  std::perror(std::string(msg).c_str());
  std::exit(1);
}

uint64_t nextRand(uint64_t& state) {
  state ^= state << 7;
  state ^= state >> 9;
  state ^= state << 8;
  return state;
}

void updateMax(std::atomic<uint64_t>& target, uint64_t value) {
  uint64_t current = target.load(std::memory_order_relaxed);
  while (current < value &&
         !target.compare_exchange_weak(current, value, std::memory_order_relaxed)) {}
}

void mergeFrontier(std::vector<uint64_t>& dst, const std::vector<uint64_t>& src) {
  for (size_t i = 0; i < dst.size(); ++i) {
    dst[i] = std::max(dst[i], src[i]);
  }
}

std::string modeName(Mode mode) {
  switch (mode) {
    case Mode::WorkerWaitDepFrontier:
      return "pwal_group_dep_frontier";
    case Mode::AsyncGlobalLsnPrefix:
      return "async_global_lsn_prefix";
    case Mode::AsyncLocalOnlyLsn:
      return "async_local_only_lsn";
    case Mode::AsyncDepFrontierLsn:
      return "async_dep_frontier_lsn";
    case Mode::AsyncDepFrontierCstamp:
      return "async_dep_frontier_cstamp";
  }
  return "unknown";
}

Mode parseMode(const std::string& s) {
  if (s == "pwal_group_dep_frontier") return Mode::WorkerWaitDepFrontier;
  if (s == "async_global_lsn_prefix") return Mode::AsyncGlobalLsnPrefix;
  if (s == "async_local_only_lsn") return Mode::AsyncLocalOnlyLsn;
  if (s == "async_dep_frontier_lsn") return Mode::AsyncDepFrontierLsn;
  if (s == "async_dep_frontier_cstamp") return Mode::AsyncDepFrontierCstamp;
  std::cerr << "unknown mode: " << s << "\n";
  std::exit(2);
}

bool startsWith(const char* arg, const char* prefix) {
  return std::strncmp(arg, prefix, std::strlen(prefix)) == 0;
}

std::string argValue(const char* arg, const char* prefix) {
  return std::string(arg + std::strlen(prefix));
}

Options parseOptions(int argc, char** argv) {
  Options opt;
  for (int i = 1; i < argc; ++i) {
    const char* a = argv[i];
    if (startsWith(a, "--mode=")) opt.mode = parseMode(argValue(a, "--mode="));
    else if (startsWith(a, "--thread_num=")) opt.thread_num = std::stoul(argValue(a, "--thread_num="));
    else if (startsWith(a, "--logger_num=")) opt.logger_num = std::stoul(argValue(a, "--logger_num="));
    else if (startsWith(a, "--seconds=")) opt.seconds = std::stoul(argValue(a, "--seconds="));
    else if (startsWith(a, "--keys_per_logger=")) opt.keys_per_logger = std::stoul(argValue(a, "--keys_per_logger="));
    else if (startsWith(a, "--remote_read_prob_ppm=")) opt.remote_read_prob_ppm = std::stoul(argValue(a, "--remote_read_prob_ppm="));
    else if (startsWith(a, "--hot_prob_ppm=")) opt.hot_prob_ppm = std::stoul(argValue(a, "--hot_prob_ppm="));
    else if (startsWith(a, "--group_size=")) opt.group_size = std::stoul(argValue(a, "--group_size="));
    else if (startsWith(a, "--flush_us=")) opt.flush_us = std::stoul(argValue(a, "--flush_us="));
    else if (startsWith(a, "--max_inflight=")) opt.max_inflight = std::stoul(argValue(a, "--max_inflight="));
    else if (startsWith(a, "--prealloc_mb=")) opt.prealloc_mb = std::stoul(argValue(a, "--prealloc_mb="));
    else if (startsWith(a, "--skip_fdatasync=")) opt.skip_fdatasync = std::stoul(argValue(a, "--skip_fdatasync=")) != 0;
    else if (startsWith(a, "--wal_dir=")) opt.wal_dir = argValue(a, "--wal_dir=");
    else {
      std::cerr << "unknown arg: " << a << "\n";
      std::exit(2);
    }
  }
  if (opt.thread_num == 0 || opt.logger_num == 0 || opt.logger_num > opt.thread_num) {
    std::cerr << "invalid thread/logger count\n";
    std::exit(2);
  }
  if (opt.remote_read_prob_ppm > 1000000 || opt.hot_prob_ppm > 1000000) {
    std::cerr << "probability must be in ppm [0,1000000]\n";
    std::exit(2);
  }
  return opt;
}

int openWalFile(const std::string& path, uint32_t prealloc_mb) {
  int fd = ::open(path.c_str(), O_CREAT | O_TRUNC | O_WRONLY | O_CLOEXEC, 0644);
  if (fd < 0) die(path);
  if (prealloc_mb != 0) {
    const off_t bytes = static_cast<off_t>(prealloc_mb) * 1024 * 1024;
    const int rc = ::posix_fallocate(fd, 0, bytes);
    if (rc != 0) {
      errno = rc;
      die("posix_fallocate");
    }
  }
  return fd;
}

void writeAll(int fd, const char* data, size_t size) {
  while (size > 0) {
    ssize_t n = ::write(fd, data, size);
    if (n < 0) die("write");
    data += n;
    size -= static_cast<size_t>(n);
  }
}

uint32_t percentile(std::vector<uint32_t>& values, double p) {
  if (values.empty()) return 0;
  std::sort(values.begin(), values.end());
  const size_t idx = std::min(values.size() - 1, static_cast<size_t>((values.size() - 1) * p));
  return values[idx];
}

struct GroupQueue {
  std::mutex mutex;
  std::condition_variable cv;
  std::string buffer;
  uint64_t max_seq = 0;
  bool done = false;
};

struct PendingTxn {
  uint32_t thid = 0;
  uint64_t cstamp = 0;
  uint64_t lsn = 0;
  uint32_t logger_id = 0;
  uint64_t local_seq = 0;
  uint64_t op_start_ns = 0;
  uint64_t enqueue_ns = 0;
  uint64_t bytes = 0;
  uint32_t remaining = 0;
  bool ready_enqueued = false;
  bool needs_global_lsn_prefix = false;
  std::vector<uint64_t> req;
};

struct WaitEntry {
  uint64_t need = 0;
  std::shared_ptr<PendingTxn> txn;
};

struct WaitEntryGreater {
  bool operator()(const WaitEntry& a, const WaitEntry& b) const {
    return a.need > b.need;
  }
};

using WaitList = std::priority_queue<WaitEntry, std::vector<WaitEntry>, WaitEntryGreater>;

struct CommitterState {
  explicit CommitterState(uint32_t logger_num) : waitlists(logger_num) {}

  std::mutex mutex;
  std::condition_variable cv;
  std::vector<WaitList> waitlists;
  WaitList global_waitlist;
  std::vector<uint8_t> completed_lsn;
  uint64_t durable_global_lsn = 0;
  std::deque<std::shared_ptr<PendingTxn>> ready;
  std::vector<uint32_t> ack_latency_samples;
  uint64_t sample_counter = 0;
  uint64_t pending_count = 0;
  bool done = false;
};

void pushReadyLocked(CommitterState& state, const std::shared_ptr<PendingTxn>& txn,
                     Stats& stats) {
  if (txn->ready_enqueued) return;
  txn->ready_enqueued = true;
  state.ready.push_back(txn);
  stats.ready_queue_pushes.fetch_add(1, std::memory_order_relaxed);
}

void drainGlobalWaitlistLocked(CommitterState& state, Stats& stats) {
  while (!state.global_waitlist.empty() &&
         state.global_waitlist.top().need <= state.durable_global_lsn) {
    std::shared_ptr<PendingTxn> txn = state.global_waitlist.top().txn;
    state.global_waitlist.pop();
    stats.waitlist_pops.fetch_add(1, std::memory_order_relaxed);
    pushReadyLocked(state, txn, stats);
  }
}

void markGlobalCompleteLocked(CommitterState& state,
                              const std::shared_ptr<PendingTxn>& txn,
                              Stats& stats) {
  if (txn->lsn == 0) return;
  if (state.completed_lsn.size() <= txn->lsn) {
    state.completed_lsn.resize(txn->lsn + 1, 0);
  }
  state.completed_lsn[txn->lsn] = 1;
  while (state.durable_global_lsn + 1 < state.completed_lsn.size() &&
         state.completed_lsn[state.durable_global_lsn + 1] != 0) {
    state.durable_global_lsn++;
  }
  drainGlobalWaitlistLocked(state, stats);
}

void onLocalClosedLocked(CommitterState& state,
                         const std::shared_ptr<PendingTxn>& txn,
                         Stats& stats) {
  if (txn->needs_global_lsn_prefix) {
    markGlobalCompleteLocked(state, txn, stats);
  } else {
    pushReadyLocked(state, txn, stats);
  }
}

void drainWaitlistLocked(CommitterState& state, uint32_t logger_id, uint64_t durable,
                         Stats& stats) {
  auto& waitlist = state.waitlists[logger_id];
  while (!waitlist.empty() && waitlist.top().need <= durable) {
    std::shared_ptr<PendingTxn> txn = waitlist.top().txn;
    waitlist.pop();
    stats.waitlist_pops.fetch_add(1, std::memory_order_relaxed);
    if (txn->remaining == 0) continue;
    --txn->remaining;
    if (txn->remaining == 0) {
      onLocalClosedLocked(state, txn, stats);
    }
  }
}

void registerTxn(CommitterState& state, const std::shared_ptr<PendingTxn>& txn,
                 const std::vector<LoggerState>& loggers, Stats& stats) {
  const uint64_t start = nowNs();
  bool ready = false;
  {
    std::lock_guard<std::mutex> guard(state.mutex);
    const size_t ready_before = state.ready.size();
    state.pending_count++;
    updateMax(stats.max_pending_len, state.pending_count);
    if (txn->needs_global_lsn_prefix) {
      if (state.completed_lsn.size() <= txn->lsn) {
        state.completed_lsn.resize(txn->lsn + 1, 0);
      }
      state.global_waitlist.push(WaitEntry{txn->lsn, txn});
      stats.waitlist_registrations.fetch_add(1, std::memory_order_relaxed);
      stats.waiting_conditions.fetch_add(1, std::memory_order_relaxed);
      updateMax(stats.max_global_waitlist_len, state.global_waitlist.size());
    }
    for (uint32_t i = 0; i < txn->req.size(); ++i) {
      const uint64_t need = txn->req[i];
      if (need == 0) continue;
      if (loggers[i].durable_seq.load(std::memory_order_acquire) >= need) continue;
      txn->remaining++;
      state.waitlists[i].push(WaitEntry{need, txn});
      stats.waitlist_registrations.fetch_add(1, std::memory_order_relaxed);
      stats.waiting_conditions.fetch_add(1, std::memory_order_relaxed);
      updateMax(stats.max_waitlist_len, state.waitlists[i].size());
    }
    for (uint32_t i = 0; i < txn->req.size(); ++i) {
      drainWaitlistLocked(state, i, loggers[i].durable_seq.load(std::memory_order_acquire), stats);
    }
    if (txn->remaining == 0) {
      onLocalClosedLocked(state, txn, stats);
    }
    ready = state.ready.size() != ready_before;
  }
  stats.committer_event_ns.fetch_add(nowNs() - start, std::memory_order_relaxed);
  if (ready) state.cv.notify_one();
}

void onDurableAdvanced(CommitterState* state, uint32_t logger_id, uint64_t durable,
                       Stats& stats) {
  if (state == nullptr) return;
  const uint64_t start = nowNs();
  bool ready = false;
  {
    std::lock_guard<std::mutex> guard(state->mutex);
    const size_t before = state->ready.size();
    drainWaitlistLocked(*state, logger_id, durable, stats);
    ready = state->ready.size() != before;
  }
  stats.committer_event_ns.fetch_add(nowNs() - start, std::memory_order_relaxed);
  if (ready) state->cv.notify_one();
}

void groupFlusher(uint32_t logger_id, const Options& opt, int fd, GroupQueue& q,
                  LoggerState& logger, Stats& stats, CommitterState* committer) {
  std::unique_lock<std::mutex> lock(q.mutex);
  while (!q.done || !q.buffer.empty()) {
    q.cv.wait_for(lock, std::chrono::microseconds(opt.flush_us), [&] {
      return q.done || q.max_seq - logger.durable_seq.load(std::memory_order_acquire) >= opt.group_size;
    });
    if (q.buffer.empty()) continue;
    std::string data;
    data.swap(q.buffer);
    const uint64_t durable_to = q.max_seq;
    lock.unlock();
    const uint64_t write_start = nowNs();
    writeAll(fd, data.data(), data.size());
    stats.write_ns.fetch_add(nowNs() - write_start, std::memory_order_relaxed);
    if (!opt.skip_fdatasync) {
      const uint64_t fsync_start = nowNs();
      if (::fdatasync(fd) != 0) die("fdatasync");
      stats.fdatasync_ns.fetch_add(nowNs() - fsync_start, std::memory_order_relaxed);
      stats.fsync_count.fetch_add(1, std::memory_order_relaxed);
      logger.fsync_count.fetch_add(1, std::memory_order_relaxed);
    }
    logger.durable_seq.store(durable_to, std::memory_order_release);
    logger.bytes.fetch_add(data.size(), std::memory_order_relaxed);
    onDurableAdvanced(committer, logger_id, durable_to, stats);
    lock.lock();
    q.cv.notify_all();
  }
}

void waitForLocalDurable(LoggerState& logger, uint64_t seq) {
  while (logger.durable_seq.load(std::memory_order_acquire) < seq) {
    std::this_thread::yield();
  }
}

void waitForDepDurable(std::vector<LoggerState>& loggers, const std::vector<uint64_t>& dep) {
  for (;;) {
    bool ok = true;
    for (size_t i = 0; i < dep.size(); ++i) {
      if (loggers[i].durable_seq.load(std::memory_order_acquire) < dep[i]) {
        ok = false;
        break;
      }
    }
    if (ok) return;
    std::this_thread::yield();
  }
}

uint32_t chooseKey(const Options& opt, uint32_t logger_id, uint64_t& rng, Stats& stats) {
  if ((nextRand(rng) % 1000000ULL) < opt.hot_prob_ppm) {
    stats.hot_accesses.fetch_add(1, std::memory_order_relaxed);
    return 0;
  }
  uint32_t shard = logger_id;
  if (opt.logger_num > 1 &&
      (nextRand(rng) % 1000000ULL) < opt.remote_read_prob_ppm) {
    shard = static_cast<uint32_t>(nextRand(rng) % (opt.logger_num - 1));
    if (shard >= logger_id) shard++;
    stats.remote_reads.fetch_add(1, std::memory_order_relaxed);
  }
  return shard * opt.keys_per_logger + static_cast<uint32_t>(nextRand(rng) % opt.keys_per_logger);
}

void readRecord(std::deque<Record>& records, uint32_t key,
                std::vector<uint64_t>& dep, std::vector<uint32_t>& read_set) {
  std::lock_guard<std::mutex> guard(records[key].mutex);
  mergeFrontier(dep, records[key].write_frontier);
  read_set.push_back(key);
}

void writeRecord(std::deque<Record>& records, uint32_t key,
                 std::vector<uint64_t>& dep, std::vector<uint32_t>& write_set) {
  std::lock_guard<std::mutex> guard(records[key].mutex);
  mergeFrontier(dep, records[key].write_frontier);
  mergeFrontier(dep, records[key].read_frontier);
  write_set.push_back(key);
}

void publishFrontiers(std::deque<Record>& records,
                      const std::vector<uint32_t>& read_set,
                      const std::vector<uint32_t>& write_set,
                      const std::vector<uint64_t>& closed_frontier) {
  for (uint32_t key : write_set) {
    std::lock_guard<std::mutex> guard(records[key].mutex);
    mergeFrontier(records[key].write_frontier, closed_frontier);
  }
  for (uint32_t key : read_set) {
    std::lock_guard<std::mutex> guard(records[key].mutex);
    mergeFrontier(records[key].read_frontier, closed_frontier);
  }
}

std::vector<uint64_t> buildReq(Mode mode, uint32_t logger_id, uint64_t local_seq,
                               const std::vector<uint64_t>& dep,
                               const std::vector<LoggerState>& loggers) {
  std::vector<uint64_t> req(loggers.size(), 0);
  if (mode == Mode::AsyncGlobalLsnPrefix || mode == Mode::AsyncLocalOnlyLsn) {
    req[logger_id] = local_seq;
    return req;
  }
  req = dep;
  req[logger_id] = std::max(req[logger_id], local_seq);
  return req;
}

void committerThread(CommitterState& state, std::vector<WorkerState>& workers, Stats& stats) {
  std::unique_lock<std::mutex> lock(state.mutex);
  for (;;) {
    state.cv.wait(lock, [&] { return !state.ready.empty() || (state.done && state.pending_count == 0); });
    if (state.done && state.pending_count == 0) return;
    if (state.ready.empty()) continue;
    auto txn = state.ready.front();
    state.ready.pop_front();
    lock.unlock();

    const uint64_t now = nowNs();
    stats.committer_queue_wait_ns.fetch_add(now - txn->enqueue_ns, std::memory_order_relaxed);
    stats.acked_commits.fetch_add(1, std::memory_order_relaxed);
    workers[txn->thid].commits.fetch_add(1, std::memory_order_relaxed);
    workers[txn->thid].outstanding.fetch_sub(1, std::memory_order_acq_rel);
    workers[txn->thid].bytes.fetch_add(txn->bytes, std::memory_order_relaxed);

    lock.lock();
    if ((++state.sample_counter & 0xff) == 0 && state.ack_latency_samples.size() < 20000) {
      state.ack_latency_samples.push_back(static_cast<uint32_t>((now - txn->op_start_ns) / 1000));
    }
    if (state.pending_count > 0) state.pending_count--;
    state.cv.notify_all();
  }
}

void workerThread(uint32_t thid, const Options& opt, std::deque<Record>& records,
                  GroupQueue& q, uint32_t logger_id, std::vector<LoggerState>& loggers,
                  std::vector<WorkerState>& workers, CommitterState& committer,
                  Stats& stats, std::atomic<uint64_t>& cstamp_alloc,
                  std::atomic<uint64_t>& lsn_alloc, std::atomic<bool>& start,
                  std::atomic<bool>& stop, LatencySample& sample) {
  while (!start.load(std::memory_order_acquire)) {}
  uint64_t seq = 0;
  uint64_t rng = 0x9e3779b97f4a7c15ULL ^ (static_cast<uint64_t>(thid) << 32);
  const bool is_async = opt.mode != Mode::WorkerWaitDepFrontier;
  const bool separate_lsn = opt.mode == Mode::AsyncGlobalLsnPrefix ||
                            opt.mode == Mode::AsyncLocalOnlyLsn ||
                            opt.mode == Mode::AsyncDepFrontierLsn;

  while (!stop.load(std::memory_order_acquire)) {
    const uint64_t stall_start = nowNs();
    while (is_async &&
           workers[thid].outstanding.load(std::memory_order_acquire) >= opt.max_inflight &&
           !stop.load(std::memory_order_acquire)) {
      std::this_thread::yield();
    }
    stats.worker_stall_ns.fetch_add(nowNs() - stall_start, std::memory_order_relaxed);
    if (stop.load(std::memory_order_acquire)) break;

    const uint64_t op_start = nowNs();
    const uint64_t dep_start = nowNs();
    std::vector<uint64_t> dep(opt.logger_num, 0);
    std::vector<uint32_t> read_set;
    std::vector<uint32_t> write_set;
    readRecord(records, chooseKey(opt, logger_id, rng, stats), dep, read_set);
    const uint32_t write_key = logger_id * opt.keys_per_logger +
                               static_cast<uint32_t>(nextRand(rng) % opt.keys_per_logger);
    writeRecord(records, write_key, dep, write_set);
    stats.dependency_build_ns.fetch_add(nowNs() - dep_start, std::memory_order_relaxed);
    stats.dep_frontier_bytes.fetch_add(dep.size() * sizeof(uint64_t), std::memory_order_relaxed);

    const uint64_t cstamp_start = nowNs();
    const uint64_t cstamp = cstamp_alloc.fetch_add(1, std::memory_order_acq_rel) + 1;
    stats.cstamp_alloc_ns.fetch_add(nowNs() - cstamp_start, std::memory_order_relaxed);
    stats.global_atomic_count.fetch_add(1, std::memory_order_relaxed);
    uint64_t lsn = 0;
    if (separate_lsn) {
      const uint64_t lsn_start = nowNs();
      lsn = lsn_alloc.fetch_add(1, std::memory_order_acq_rel) + 1;
      stats.lsn_alloc_ns.fetch_add(nowNs() - lsn_start, std::memory_order_relaxed);
      stats.global_atomic_count.fetch_add(1, std::memory_order_relaxed);
    }

    const uint64_t local_seq = loggers[logger_id].local_seq.fetch_add(1, std::memory_order_acq_rel) + 1;
    std::string record = "CSTAMP=" + std::to_string(cstamp);
    if (separate_lsn) record += " LSN=" + std::to_string(lsn);
    record += " log=" + std::to_string(logger_id) + " seq=" + std::to_string(local_seq) +
              " read=" + std::to_string(read_set[0]) + " write=" + std::to_string(write_key) + "\n";

    const uint64_t enqueue_start = nowNs();
    {
      const uint64_t wait_start = nowNs();
      std::lock_guard<std::mutex> guard(q.mutex);
      stats.mutex_wait_ns.fetch_add(nowNs() - wait_start, std::memory_order_relaxed);
      q.buffer += record;
      q.max_seq = std::max(q.max_seq, local_seq);
    }
    q.cv.notify_one();

    std::vector<uint64_t> closed = dep;
    closed[logger_id] = std::max(closed[logger_id], local_seq);
    publishFrontiers(records, read_set, write_set, closed);

    if (!is_async) {
      const uint64_t wait_start = nowNs();
      waitForLocalDurable(loggers[logger_id], local_seq);
      waitForDepDurable(loggers, dep);
      stats.worker_wait_ns.fetch_add(nowNs() - wait_start, std::memory_order_relaxed);
      workers[thid].commits.fetch_add(1, std::memory_order_relaxed);
      workers[thid].logical_commits.fetch_add(1, std::memory_order_relaxed);
      stats.logical_commits.fetch_add(1, std::memory_order_relaxed);
      stats.acked_commits.fetch_add(1, std::memory_order_relaxed);
      if ((++seq & 0xff) == 0 && sample.micros.size() < 20000) {
        sample.micros.push_back(static_cast<uint32_t>((nowNs() - op_start) / 1000));
      }
    } else {
      auto txn = std::make_shared<PendingTxn>();
      txn->thid = thid;
      txn->cstamp = cstamp;
      txn->lsn = lsn;
      txn->logger_id = logger_id;
      txn->local_seq = local_seq;
      txn->op_start_ns = op_start;
      txn->enqueue_ns = nowNs();
      txn->bytes = record.size();
      txn->needs_global_lsn_prefix = opt.mode == Mode::AsyncGlobalLsnPrefix;
      txn->req = buildReq(opt.mode, logger_id, local_seq, dep, loggers);
      workers[thid].outstanding.fetch_add(1, std::memory_order_acq_rel);
      workers[thid].logical_commits.fetch_add(1, std::memory_order_relaxed);
      stats.logical_commits.fetch_add(1, std::memory_order_relaxed);
      registerTxn(committer, txn, loggers, stats);
      ++seq;
    }

    stats.log_enqueue_ns.fetch_add(nowNs() - enqueue_start, std::memory_order_relaxed);
    stats.bytes.fetch_add(record.size(), std::memory_order_relaxed);
    stats.txns.fetch_add(1, std::memory_order_relaxed);
  }
}

}  // namespace

int main(int argc, char** argv) {
  const Options opt = parseOptions(argc, argv);
  std::filesystem::create_directories(opt.wal_dir);

  std::deque<Record> records;
  for (uint32_t i = 0; i < opt.logger_num * opt.keys_per_logger; ++i) {
    records.emplace_back(opt.logger_num);
  }

  Stats stats;
  std::vector<WorkerState> workers(opt.thread_num);
  std::vector<LoggerState> loggers(opt.logger_num);
  std::vector<LatencySample> samples(opt.thread_num);
  std::vector<std::unique_ptr<GroupQueue>> queues;
  std::vector<std::thread> flushers;
  std::vector<std::thread> threads;
  std::vector<int> fds(opt.logger_num, -1);
  CommitterState committer_state(opt.logger_num);
  std::thread committer;
  std::atomic<bool> start{false};
  std::atomic<bool> stop{false};
  std::atomic<uint64_t> cstamp_alloc{0};
  std::atomic<uint64_t> lsn_alloc{0};

  const std::string base = opt.wal_dir + "/" + modeName(opt.mode) + "_pid" +
                           std::to_string(static_cast<long long>(getpid()));
  std::filesystem::create_directories(base);
  for (uint32_t i = 0; i < opt.logger_num; ++i) {
    fds[i] = openWalFile(base + "/logger_" + std::to_string(i) + ".wal", opt.prealloc_mb);
    queues.emplace_back(std::make_unique<GroupQueue>());
    flushers.emplace_back(groupFlusher, i, std::cref(opt), fds[i],
                          std::ref(*queues.back()), std::ref(loggers[i]),
                          std::ref(stats),
                          opt.mode == Mode::WorkerWaitDepFrontier ? nullptr : &committer_state);
  }

  if (opt.mode != Mode::WorkerWaitDepFrontier) {
    committer = std::thread(committerThread, std::ref(committer_state),
                            std::ref(workers), std::ref(stats));
  }

  for (uint32_t i = 0; i < opt.thread_num; ++i) {
    threads.emplace_back(workerThread, i, std::cref(opt), std::ref(records),
                         std::ref(*queues[i % opt.logger_num]), i % opt.logger_num,
                         std::ref(loggers), std::ref(workers),
                         std::ref(committer_state), std::ref(stats),
                         std::ref(cstamp_alloc), std::ref(lsn_alloc),
                         std::ref(start), std::ref(stop), std::ref(samples[i]));
  }

  const auto begin = std::chrono::steady_clock::now();
  start.store(true, std::memory_order_release);
  std::this_thread::sleep_for(std::chrono::seconds(opt.seconds));
  stop.store(true, std::memory_order_release);
  for (auto& t : threads) t.join();

  for (auto& q : queues) {
    {
      std::lock_guard<std::mutex> guard(q->mutex);
      q->done = true;
    }
    q->cv.notify_all();
  }
  for (auto& t : flushers) t.join();

  if (opt.mode != Mode::WorkerWaitDepFrontier) {
    for (uint32_t i = 0; i < opt.logger_num; ++i) {
      onDurableAdvanced(&committer_state, i, loggers[i].durable_seq.load(std::memory_order_acquire), stats);
    }
    {
      std::lock_guard<std::mutex> guard(committer_state.mutex);
      committer_state.done = true;
    }
    committer_state.cv.notify_all();
    committer.join();
  }

  const double actual_sec = static_cast<double>(elapsedNs(begin)) / 1e9;
  for (int fd : fds) {
    if (fd >= 0) {
      if (!opt.skip_fdatasync) ::fdatasync(fd);
      ::close(fd);
    }
  }

  uint64_t commits = 0;
  uint64_t logical = 0;
  for (const auto& w : workers) {
    commits += w.commits.load(std::memory_order_acquire);
    logical += w.logical_commits.load(std::memory_order_acquire);
  }

  uint64_t fsyncs = 0;
  uint64_t logger_bytes = 0;
  uint64_t max_lag = 0;
  for (const auto& l : loggers) {
    fsyncs += l.fsync_count.load(std::memory_order_acquire);
    logger_bytes += l.bytes.load(std::memory_order_acquire);
    const uint64_t local = l.local_seq.load(std::memory_order_acquire);
    const uint64_t durable = l.durable_seq.load(std::memory_order_acquire);
    max_lag = std::max(max_lag, local - durable);
  }

  std::vector<uint32_t> all_lat;
  for (auto& s : samples) all_lat.insert(all_lat.end(), s.micros.begin(), s.micros.end());
  {
    std::lock_guard<std::mutex> guard(committer_state.mutex);
    all_lat.insert(all_lat.end(), committer_state.ack_latency_samples.begin(),
                   committer_state.ack_latency_samples.end());
  }
  const uint32_t p50 = percentile(all_lat, 0.50);
  const uint32_t p99 = percentile(all_lat, 0.99);

  std::cout << "mode: " << modeName(opt.mode) << "\n";
  std::cout << "thread_num: " << opt.thread_num << "\n";
  std::cout << "logger_num: " << opt.logger_num << "\n";
  std::cout << "seconds: " << opt.seconds << "\n";
  std::cout << "actual_sec: " << actual_sec << "\n";
  std::cout << "keys_per_logger: " << opt.keys_per_logger << "\n";
  std::cout << "remote_read_prob_ppm: " << opt.remote_read_prob_ppm << "\n";
  std::cout << "hot_prob_ppm: " << opt.hot_prob_ppm << "\n";
  std::cout << "group_size: " << opt.group_size << "\n";
  std::cout << "flush_us: " << opt.flush_us << "\n";
  std::cout << "max_inflight: " << opt.max_inflight << "\n";
  std::cout << "skip_fdatasync: " << (opt.skip_fdatasync ? 1 : 0) << "\n";
  std::cout << "commits: " << commits << "\n";
  std::cout << "logical_commits: " << logical << "\n";
  std::cout << "acked_commits: " << stats.acked_commits.load(std::memory_order_acquire) << "\n";
  std::cout << "logical_minus_acked: " << (logical >= commits ? logical - commits : 0) << "\n";
  std::cout << "throughput_tps: " << static_cast<uint64_t>(commits / actual_sec) << "\n";
  std::cout << "logical_throughput_tps: " << static_cast<uint64_t>(logical / actual_sec) << "\n";
  std::cout << "acked_throughput_tps: " << static_cast<uint64_t>(commits / actual_sec) << "\n";
  std::cout << "latency_p50_us: " << p50 << "\n";
  std::cout << "latency_p99_us: " << p99 << "\n";
  std::cout << "fdatasync_count: " << fsyncs << "\n";
  std::cout << "fdatasync_per_sec: " << static_cast<double>(fsyncs) / actual_sec << "\n";
  std::cout << "commits_per_fdatasync: " << (fsyncs ? static_cast<double>(commits) / fsyncs : 0.0) << "\n";
  std::cout << "logger_max_durable_lag: " << max_lag << "\n";
  std::cout << "logger_bytes_flushed: " << logger_bytes << "\n";
  std::cout << "payload_build_ns: " << stats.payload_build_ns.load(std::memory_order_acquire) << "\n";
  std::cout << "dependency_build_ns: " << stats.dependency_build_ns.load(std::memory_order_acquire) << "\n";
  std::cout << "cstamp_alloc_ns: " << stats.cstamp_alloc_ns.load(std::memory_order_acquire) << "\n";
  std::cout << "lsn_alloc_ns: " << stats.lsn_alloc_ns.load(std::memory_order_acquire) << "\n";
  std::cout << "log_enqueue_ns: " << stats.log_enqueue_ns.load(std::memory_order_acquire) << "\n";
  std::cout << "mutex_wait_ns: " << stats.mutex_wait_ns.load(std::memory_order_acquire) << "\n";
  std::cout << "write_ns: " << stats.write_ns.load(std::memory_order_acquire) << "\n";
  std::cout << "fdatasync_ns: " << stats.fdatasync_ns.load(std::memory_order_acquire) << "\n";
  std::cout << "worker_wait_ns: " << stats.worker_wait_ns.load(std::memory_order_acquire) << "\n";
  std::cout << "committer_event_ns: " << stats.committer_event_ns.load(std::memory_order_acquire) << "\n";
  std::cout << "committer_queue_wait_ns: " << stats.committer_queue_wait_ns.load(std::memory_order_acquire) << "\n";
  std::cout << "worker_stall_ns: " << stats.worker_stall_ns.load(std::memory_order_acquire) << "\n";
  std::cout << "remote_reads: " << stats.remote_reads.load(std::memory_order_acquire) << "\n";
  std::cout << "hot_accesses: " << stats.hot_accesses.load(std::memory_order_acquire) << "\n";
  std::cout << "dep_frontier_bytes: " << stats.dep_frontier_bytes.load(std::memory_order_acquire) << "\n";
  std::cout << "global_atomic_count: " << stats.global_atomic_count.load(std::memory_order_acquire) << "\n";
  std::cout << "waitlist_registrations: " << stats.waitlist_registrations.load(std::memory_order_acquire) << "\n";
  std::cout << "waitlist_pops: " << stats.waitlist_pops.load(std::memory_order_acquire) << "\n";
  std::cout << "ready_queue_pushes: " << stats.ready_queue_pushes.load(std::memory_order_acquire) << "\n";
  std::cout << "max_pending_len: " << stats.max_pending_len.load(std::memory_order_acquire) << "\n";
  std::cout << "max_waitlist_len: " << stats.max_waitlist_len.load(std::memory_order_acquire) << "\n";
  std::cout << "max_global_waitlist_len: " << stats.max_global_waitlist_len.load(std::memory_order_acquire) << "\n";
  std::cout << "waiting_conditions: " << stats.waiting_conditions.load(std::memory_order_acquire) << "\n";
  return 0;
}
