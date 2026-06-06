#include <algorithm>
#include <atomic>
#include <chrono>
#include <cerrno>
#include <condition_variable>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <filesystem>
#include <limits>
#include <iostream>
#include <memory>
#include <mutex>
#include <numeric>
#include <queue>
#include <string>
#include <string_view>
#include <thread>
#include <vector>

#include <fcntl.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

namespace {

enum class Mode {
  NoDurability,
  SingleWal,
  SingleWalGroupCommit,
  PwalPerTxnFdatasync,
  PwalGroupCommit,
  PwalGroupCommitNoPrefix,
  PwalGroupDepFrontier,
  AsyncGlobalLsnPrefix,
  AsyncGlobalPrefixLsn,
  AsyncLocalOnlyLsn,
  AsyncDepFrontierLsn,
  AsyncDepFrontierCstamp,
};

struct Options {
  Mode mode = Mode::NoDurability;
  uint32_t thread_num = 1;
  uint32_t seconds = 3;
  uint32_t write_set_size = 10;
  uint32_t value_size = 32;
  uint32_t group_size = 32;
  uint32_t flush_us = 1000;
  uint32_t logger_num = 1;
  uint32_t prealloc_mb = 0;
  int32_t straggler_logger = -1;
  uint32_t straggler_sleep_us = 0;
  uint32_t straggler_extra_bytes = 0;
  uint32_t dep_prob_ppm = 0;
  uint32_t dep_fanout = 1;
  uint32_t max_inflight = 1024;
  uint32_t committer_poll_us = 50;
  bool skip_fdatasync = false;
  std::string wal_dir = "results/no_cc_wal_files";
};

struct alignas(64) WorkerState {
  std::atomic<uint64_t> commits{0};
  std::atomic<uint64_t> bytes{0};
  std::atomic<uint64_t> fsync_count{0};
  std::atomic<uint64_t> local_seq{0};
  std::atomic<uint64_t> durable_seq{0};
  std::atomic<uint64_t> outstanding{0};
};

struct Stats {
  std::atomic<uint64_t> payload_build_ns{0};
  std::atomic<uint64_t> dep_frontier_build_ns{0};
  std::atomic<uint64_t> cstamp_alloc_ns{0};
  std::atomic<uint64_t> lsn_alloc_ns{0};
  std::atomic<uint64_t> log_enqueue_ns{0};
  std::atomic<uint64_t> mutex_wait_ns{0};
  std::atomic<uint64_t> write_ns{0};
  std::atomic<uint64_t> fdatasync_ns{0};
  std::atomic<uint64_t> prefix_wait_ns{0};
  std::atomic<uint64_t> self_durable_wait_ns{0};
  std::atomic<uint64_t> dependency_wait_ns{0};
  std::atomic<uint64_t> committer_scan_ns{0};
  std::atomic<uint64_t> committer_event_ns{0};
  std::atomic<uint64_t> committer_queue_wait_ns{0};
  std::atomic<uint64_t> worker_stall_ns{0};
  std::atomic<uint64_t> logical_commits{0};
  std::atomic<uint64_t> acked_commits{0};
  std::atomic<uint64_t> dep_edges{0};
  std::atomic<uint64_t> dep_frontier_bytes{0};
  std::atomic<uint64_t> global_atomic_count{0};
  std::atomic<uint64_t> waitlist_registrations{0};
  std::atomic<uint64_t> waitlist_pops{0};
  std::atomic<uint64_t> ready_queue_pushes{0};
  std::atomic<uint64_t> max_ready_queue_len{0};
  std::atomic<uint64_t> max_waitlist_len{0};
  std::atomic<uint64_t> max_global_waitlist_len{0};
  std::atomic<uint64_t> max_pending_len{0};
  std::atomic<uint64_t> waiting_conditions{0};
  std::atomic<uint64_t> bytes{0};
  std::atomic<uint64_t> fsync_count{0};
};

struct LatencySample {
  std::vector<uint32_t> micros;
};

struct FrontierState {
  explicit FrontierState(uint32_t logger_num) : frontier(logger_num, 0) {}

  std::mutex mutex;
  std::vector<uint64_t> frontier;
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

std::string modeName(Mode mode) {
  switch (mode) {
    case Mode::NoDurability:
      return "no_durability";
    case Mode::SingleWal:
      return "single_wal";
    case Mode::SingleWalGroupCommit:
      return "single_wal_group_commit";
    case Mode::PwalPerTxnFdatasync:
      return "pwal_per_txn_fdatasync";
    case Mode::PwalGroupCommit:
      return "pwal_group_commit";
    case Mode::PwalGroupCommitNoPrefix:
      return "pwal_group_commit_no_prefix";
    case Mode::PwalGroupDepFrontier:
      return "pwal_group_dep_frontier";
    case Mode::AsyncGlobalLsnPrefix:
      return "async_global_lsn_prefix";
    case Mode::AsyncGlobalPrefixLsn:
      return "async_global_prefix_lsn";
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
  if (s == "no_durability") return Mode::NoDurability;
  if (s == "single_wal") return Mode::SingleWal;
  if (s == "single_wal_group_commit") return Mode::SingleWalGroupCommit;
  if (s == "pwal_per_txn_fdatasync") return Mode::PwalPerTxnFdatasync;
  if (s == "pwal_group_commit") return Mode::PwalGroupCommit;
  if (s == "pwal_group_commit_no_prefix") return Mode::PwalGroupCommitNoPrefix;
  if (s == "pwal_group_dep_frontier") return Mode::PwalGroupDepFrontier;
  if (s == "async_global_lsn_prefix") return Mode::AsyncGlobalLsnPrefix;
  if (s == "async_global_prefix_lsn") return Mode::AsyncGlobalPrefixLsn;
  if (s == "async_local_only_lsn") return Mode::AsyncLocalOnlyLsn;
  if (s == "async_dep_frontier_lsn") return Mode::AsyncDepFrontierLsn;
  if (s == "async_dep_frontier_cstamp") return Mode::AsyncDepFrontierCstamp;
  if (s == "cstamp_pwal_async_dep_frontier") return Mode::AsyncDepFrontierCstamp;
  std::cerr << "unknown mode: " << s << "\n";
  std::exit(2);
}

bool isAsyncMode(Mode mode) {
  return mode == Mode::AsyncGlobalLsnPrefix ||
         mode == Mode::AsyncGlobalPrefixLsn ||
         mode == Mode::AsyncLocalOnlyLsn ||
         mode == Mode::AsyncDepFrontierLsn ||
         mode == Mode::AsyncDepFrontierCstamp;
}

bool isGroupedWalMode(Mode mode) {
  return mode == Mode::SingleWalGroupCommit ||
         mode == Mode::PwalGroupCommit ||
         mode == Mode::PwalGroupCommitNoPrefix ||
         mode == Mode::PwalGroupDepFrontier ||
         isAsyncMode(mode);
}

bool asyncUsesDependencyFrontier(Mode mode) {
  return mode == Mode::AsyncDepFrontierLsn ||
         mode == Mode::AsyncDepFrontierCstamp;
}

bool asyncUsesSeparateLsn(Mode mode) {
  return mode == Mode::AsyncGlobalLsnPrefix ||
         mode == Mode::AsyncGlobalPrefixLsn ||
         mode == Mode::AsyncLocalOnlyLsn ||
         mode == Mode::AsyncDepFrontierLsn;
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
    else if (startsWith(a, "--seconds=")) opt.seconds = std::stoul(argValue(a, "--seconds="));
    else if (startsWith(a, "--write_set_size=")) opt.write_set_size = std::stoul(argValue(a, "--write_set_size="));
    else if (startsWith(a, "--value_size=")) opt.value_size = std::stoul(argValue(a, "--value_size="));
    else if (startsWith(a, "--group_size=")) opt.group_size = std::stoul(argValue(a, "--group_size="));
    else if (startsWith(a, "--flush_us=")) opt.flush_us = std::stoul(argValue(a, "--flush_us="));
    else if (startsWith(a, "--logger_num=")) opt.logger_num = std::stoul(argValue(a, "--logger_num="));
    else if (startsWith(a, "--prealloc_mb=")) opt.prealloc_mb = std::stoul(argValue(a, "--prealloc_mb="));
    else if (startsWith(a, "--straggler_logger=")) opt.straggler_logger = std::stoi(argValue(a, "--straggler_logger="));
    else if (startsWith(a, "--straggler_sleep_us=")) opt.straggler_sleep_us = std::stoul(argValue(a, "--straggler_sleep_us="));
    else if (startsWith(a, "--straggler_extra_bytes=")) opt.straggler_extra_bytes = std::stoul(argValue(a, "--straggler_extra_bytes="));
    else if (startsWith(a, "--dep_prob_ppm=")) opt.dep_prob_ppm = std::stoul(argValue(a, "--dep_prob_ppm="));
    else if (startsWith(a, "--dep_fanout=")) opt.dep_fanout = std::stoul(argValue(a, "--dep_fanout="));
    else if (startsWith(a, "--max_inflight=")) opt.max_inflight = std::stoul(argValue(a, "--max_inflight="));
    else if (startsWith(a, "--committer_poll_us=")) opt.committer_poll_us = std::stoul(argValue(a, "--committer_poll_us="));
    else if (startsWith(a, "--skip_fdatasync=")) opt.skip_fdatasync = std::stoul(argValue(a, "--skip_fdatasync=")) != 0;
    else if (startsWith(a, "--wal_dir=")) opt.wal_dir = argValue(a, "--wal_dir=");
    else {
      std::cerr << "unknown arg: " << a << "\n";
      std::exit(2);
    }
  }
  if (opt.thread_num == 0 || opt.thread_num > 256) {
    std::cerr << "--thread_num must be in [1,256]\n";
    std::exit(2);
  }
  if (opt.write_set_size == 0) {
    std::cerr << "--write_set_size must be > 0\n";
    std::exit(2);
  }
  if (opt.group_size == 0) {
    std::cerr << "--group_size must be > 0\n";
    std::exit(2);
  }
  if (opt.dep_prob_ppm > 1000000) {
    std::cerr << "--dep_prob_ppm must be in [0,1000000]\n";
    std::exit(2);
  }
  if (opt.logger_num == 0 || opt.logger_num > opt.thread_num) {
    std::cerr << "--logger_num must be in [1,thread_num]\n";
    std::exit(2);
  }
  if (opt.dep_fanout > opt.logger_num) {
    opt.dep_fanout = opt.logger_num;
  }
  if (opt.max_inflight == 0) {
    std::cerr << "--max_inflight must be > 0\n";
    std::exit(2);
  }
  if (opt.mode == Mode::SingleWalGroupCommit && opt.logger_num != 1) {
    std::cerr << "--logger_num must be 1 for single_wal_group_commit\n";
    std::exit(2);
  }
  if (opt.straggler_logger >= static_cast<int32_t>(opt.logger_num)) {
    std::cerr << "--straggler_logger must be -1 or in [0,logger_num)\n";
    std::exit(2);
  }
  return opt;
}

std::string makeRecord(uint32_t thid, uint64_t seq, const Options& opt) {
  std::string out;
  out.reserve(96 + opt.write_set_size * (48 + opt.value_size));
  out += "BEGIN thid=" + std::to_string(thid) + " seq=" + std::to_string(seq) + "\n";
  for (uint32_t i = 0; i < opt.write_set_size; ++i) {
    const uint64_t key = (static_cast<uint64_t>(thid) << 48) ^ (seq << 8) ^ i;
    out += "U key=" + std::to_string(key) + " val=";
    for (uint32_t j = 0; j < opt.value_size; ++j) {
      out.push_back(static_cast<char>('a' + ((key + j) % 26)));
    }
    out.push_back('\n');
  }
  out += "COMMIT thid=" + std::to_string(thid) + " seq=" + std::to_string(seq) + "\n";
  return out;
}

uint64_t minDurableSeq(const std::vector<WorkerState>& workers) {
  uint64_t m = UINT64_MAX;
  for (const auto& w : workers) {
    m = std::min(m, w.durable_seq.load(std::memory_order_acquire));
  }
  return m == UINT64_MAX ? 0 : m;
}

void waitForLocalDurable(WorkerState& worker, uint64_t seq) {
  while (worker.durable_seq.load(std::memory_order_acquire) < seq) {
    std::this_thread::yield();
  }
}

void waitForGlobalPrefix(std::vector<WorkerState>& workers, uint64_t seq) {
  while (minDurableSeq(workers) < seq) {
    std::this_thread::yield();
  }
}

uint64_t nextRand(uint64_t& state) {
  state ^= state << 7;
  state ^= state >> 9;
  state ^= state << 8;
  return state;
}

void mergeFrontier(std::vector<uint64_t>& dst, const std::vector<uint64_t>& src) {
  for (size_t i = 0; i < dst.size(); ++i) {
    dst[i] = std::max(dst[i], src[i]);
  }
}

std::vector<uint64_t> buildDependencyFrontier(
    uint32_t logger_id, const Options& opt,
    std::vector<std::unique_ptr<FrontierState>>& frontier_states,
    uint64_t& rng, Stats& stats) {
  std::vector<uint64_t> dep(opt.logger_num, 0);
  if (opt.logger_num <= 1 || opt.dep_prob_ppm == 0 || opt.dep_fanout == 0) {
    return dep;
  }
  if ((nextRand(rng) % 1000000ULL) >= opt.dep_prob_ppm) {
    return dep;
  }

  const uint32_t fanout = std::min<uint32_t>(opt.dep_fanout, opt.logger_num - 1);
  const uint32_t start = static_cast<uint32_t>(nextRand(rng) % (opt.logger_num - 1));
  for (uint32_t k = 0; k < fanout; ++k) {
    uint32_t target = (logger_id + 1 + start + k) % opt.logger_num;
    if (target == logger_id) target = (target + 1) % opt.logger_num;
    {
      std::lock_guard<std::mutex> guard(frontier_states[target]->mutex);
      mergeFrontier(dep, frontier_states[target]->frontier);
    }
    stats.dep_edges.fetch_add(1, std::memory_order_relaxed);
  }
  stats.dep_frontier_bytes.fetch_add(dep.size() * sizeof(uint64_t), std::memory_order_relaxed);
  return dep;
}

void publishFrontier(uint32_t logger_id, uint64_t local_seq,
                     const std::vector<uint64_t>& dep,
                     std::vector<std::unique_ptr<FrontierState>>& frontier_states) {
  std::lock_guard<std::mutex> guard(frontier_states[logger_id]->mutex);
  mergeFrontier(frontier_states[logger_id]->frontier, dep);
  frontier_states[logger_id]->frontier[logger_id] =
      std::max(frontier_states[logger_id]->frontier[logger_id], local_seq);
}

void waitForDependencyFrontier(std::vector<WorkerState>& loggers,
                               const std::vector<uint64_t>& dep) {
  for (;;) {
    bool closed = true;
    for (size_t i = 0; i < dep.size(); ++i) {
      if (loggers[i].durable_seq.load(std::memory_order_acquire) < dep[i]) {
        closed = false;
        break;
      }
    }
    if (closed) return;
    std::this_thread::yield();
  }
}

bool isDependencyFrontierClosed(const std::vector<WorkerState>& loggers,
                                const std::vector<uint64_t>& dep) {
  for (size_t i = 0; i < dep.size(); ++i) {
    if (loggers[i].durable_seq.load(std::memory_order_acquire) < dep[i]) {
      return false;
    }
  }
  return true;
}

std::vector<uint64_t> buildAsyncRequirement(Mode mode, uint32_t logger_id,
                                            uint64_t local_seq,
                                            const std::vector<uint64_t>& dep,
                                            const std::vector<WorkerState>& loggers) {
  std::vector<uint64_t> req(loggers.size(), 0);
  if (mode == Mode::AsyncGlobalLsnPrefix) {
    req[logger_id] = local_seq;
    return req;
  }
  if (mode == Mode::AsyncGlobalPrefixLsn) {
    for (uint32_t i = 0; i < loggers.size(); ++i) {
      req[i] = loggers[i].local_seq.load(std::memory_order_acquire);
    }
    req[logger_id] = std::max(req[logger_id], local_seq);
    return req;
  }
  if (mode == Mode::AsyncLocalOnlyLsn) {
    req[logger_id] = local_seq;
    return req;
  }
  req = dep;
  req[logger_id] = std::max(req[logger_id], local_seq);
  return req;
}

uint32_t percentile(std::vector<uint32_t>& values, double p) {
  if (values.empty()) return 0;
  std::sort(values.begin(), values.end());
  const size_t idx = std::min(values.size() - 1, static_cast<size_t>((values.size() - 1) * p));
  return values[idx];
}

void updateMax(std::atomic<uint64_t>& target, uint64_t value) {
  uint64_t current = target.load(std::memory_order_relaxed);
  while (current < value &&
         !target.compare_exchange_weak(current, value, std::memory_order_relaxed)) {}
}

void runNoDurabilityWorker(uint32_t thid, const Options& opt, WorkerState& worker,
                           Stats& stats, std::atomic<bool>& start,
                           std::atomic<bool>& stop, LatencySample& sample) {
  while (!start.load(std::memory_order_acquire)) {}
  uint64_t seq = 0;
  while (!stop.load(std::memory_order_acquire)) {
    const uint64_t op_start = nowNs();
    const uint64_t build_start = nowNs();
    std::string record = makeRecord(thid, ++seq, opt);
    stats.payload_build_ns.fetch_add(nowNs() - build_start, std::memory_order_relaxed);
    worker.commits.fetch_add(1, std::memory_order_relaxed);
    stats.logical_commits.fetch_add(1, std::memory_order_relaxed);
    stats.acked_commits.fetch_add(1, std::memory_order_relaxed);
    worker.bytes.fetch_add(record.size(), std::memory_order_relaxed);
    stats.bytes.fetch_add(record.size(), std::memory_order_relaxed);
    if ((seq & 0xff) == 0 && sample.micros.size() < 20000) {
      sample.micros.push_back(static_cast<uint32_t>((nowNs() - op_start) / 1000));
    }
  }
}

void runSingleWalWorker(uint32_t thid, const Options& opt, int fd, std::mutex& mutex,
                        WorkerState& worker, Stats& stats, std::atomic<bool>& start,
                        std::atomic<bool>& stop, LatencySample& sample) {
  while (!start.load(std::memory_order_acquire)) {}
  uint64_t seq = 0;
  while (!stop.load(std::memory_order_acquire)) {
    const uint64_t op_start = nowNs();
    const uint64_t build_start = nowNs();
    std::string record = makeRecord(thid, ++seq, opt);
    stats.payload_build_ns.fetch_add(nowNs() - build_start, std::memory_order_relaxed);
    const uint64_t wait_start = nowNs();
    mutex.lock();
    stats.mutex_wait_ns.fetch_add(nowNs() - wait_start, std::memory_order_relaxed);
    const uint64_t write_start = nowNs();
    writeAll(fd, record.data(), record.size());
    stats.write_ns.fetch_add(nowNs() - write_start, std::memory_order_relaxed);
    if (!opt.skip_fdatasync) {
      const uint64_t fsync_start = nowNs();
      if (::fdatasync(fd) != 0) die("fdatasync");
      stats.fdatasync_ns.fetch_add(nowNs() - fsync_start, std::memory_order_relaxed);
      stats.fsync_count.fetch_add(1, std::memory_order_relaxed);
      worker.fsync_count.fetch_add(1, std::memory_order_relaxed);
    }
    mutex.unlock();
    worker.commits.fetch_add(1, std::memory_order_relaxed);
    stats.logical_commits.fetch_add(1, std::memory_order_relaxed);
    stats.acked_commits.fetch_add(1, std::memory_order_relaxed);
    worker.bytes.fetch_add(record.size(), std::memory_order_relaxed);
    stats.bytes.fetch_add(record.size(), std::memory_order_relaxed);
    if ((seq & 0xff) == 0 && sample.micros.size() < 20000) {
      sample.micros.push_back(static_cast<uint32_t>((nowNs() - op_start) / 1000));
    }
  }
}

void runPwalPerTxnWorker(uint32_t thid, const Options& opt, int fd, WorkerState& worker,
                         Stats& stats, std::atomic<bool>& start, std::atomic<bool>& stop,
                         LatencySample& sample) {
  while (!start.load(std::memory_order_acquire)) {}
  uint64_t seq = 0;
  while (!stop.load(std::memory_order_acquire)) {
    const uint64_t op_start = nowNs();
    const uint64_t build_start = nowNs();
    std::string record = makeRecord(thid, ++seq, opt);
    stats.payload_build_ns.fetch_add(nowNs() - build_start, std::memory_order_relaxed);
    const uint64_t write_start = nowNs();
    writeAll(fd, record.data(), record.size());
    stats.write_ns.fetch_add(nowNs() - write_start, std::memory_order_relaxed);
    if (!opt.skip_fdatasync) {
      const uint64_t fsync_start = nowNs();
      if (::fdatasync(fd) != 0) die("fdatasync");
      stats.fdatasync_ns.fetch_add(nowNs() - fsync_start, std::memory_order_relaxed);
      stats.fsync_count.fetch_add(1, std::memory_order_relaxed);
      worker.fsync_count.fetch_add(1, std::memory_order_relaxed);
    }
    worker.durable_seq.store(seq, std::memory_order_release);
    worker.commits.fetch_add(1, std::memory_order_relaxed);
    stats.logical_commits.fetch_add(1, std::memory_order_relaxed);
    stats.acked_commits.fetch_add(1, std::memory_order_relaxed);
    worker.bytes.fetch_add(record.size(), std::memory_order_relaxed);
    stats.bytes.fetch_add(record.size(), std::memory_order_relaxed);
    if ((seq & 0xff) == 0 && sample.micros.size() < 20000) {
      sample.micros.push_back(static_cast<uint32_t>((nowNs() - op_start) / 1000));
    }
  }
}

struct PendingTxn {
  uint32_t thid = 0;
  uint64_t worker_seq = 0;
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
using GlobalWaitList = std::priority_queue<WaitEntry, std::vector<WaitEntry>, WaitEntryGreater>;

struct CommitterState {
  explicit CommitterState(uint32_t logger_num) : waitlists(logger_num) {}

  std::mutex mutex;
  std::condition_variable cv;
  std::vector<WaitList> waitlists;
  GlobalWaitList global_waitlist;
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
  updateMax(stats.max_ready_queue_len, state.ready.size());
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

void markGlobalLsnCompleteLocked(CommitterState& state,
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

void onLocalConditionsClosedLocked(CommitterState& state,
                                   const std::shared_ptr<PendingTxn>& txn,
                                   Stats& stats) {
  if (txn->needs_global_lsn_prefix) {
    markGlobalLsnCompleteLocked(state, txn, stats);
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
    if (txn->remaining == 0) {
      continue;
    }
    --txn->remaining;
    if (txn->remaining == 0) {
      onLocalConditionsClosedLocked(state, txn, stats);
    }
  }
}

void registerTxn(CommitterState& state, const std::shared_ptr<PendingTxn>& txn,
                 const std::vector<WorkerState>& loggers, Stats& stats) {
  const uint64_t event_start = nowNs();
  bool ready = false;
  {
    std::lock_guard<std::mutex> guard(state.mutex);
    const size_t ready_before = state.ready.size();
    txn->remaining = 0;
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
      if (loggers[i].durable_seq.load(std::memory_order_acquire) >= need) {
        continue;
      }
      txn->remaining++;
      state.waitlists[i].push(WaitEntry{need, txn});
      stats.waitlist_registrations.fetch_add(1, std::memory_order_relaxed);
      stats.waiting_conditions.fetch_add(1, std::memory_order_relaxed);
      updateMax(stats.max_waitlist_len, state.waitlists[i].size());
    }
    for (uint32_t i = 0; i < txn->req.size(); ++i) {
      const uint64_t durable = loggers[i].durable_seq.load(std::memory_order_acquire);
      drainWaitlistLocked(state, i, durable, stats);
    }
    if (txn->remaining == 0) {
      onLocalConditionsClosedLocked(state, txn, stats);
    }
    ready = state.ready.size() != ready_before;
  }
  stats.committer_event_ns.fetch_add(nowNs() - event_start, std::memory_order_relaxed);
  if (ready) {
    state.cv.notify_one();
  }
}

void onDurableAdvanced(CommitterState* state, uint32_t logger_id, uint64_t durable,
                       Stats& stats) {
  if (state == nullptr) return;
  const uint64_t event_start = nowNs();
  bool ready = false;
  {
    std::lock_guard<std::mutex> guard(state->mutex);
    const size_t before = state->ready.size();
    drainWaitlistLocked(*state, logger_id, durable, stats);
    ready = state->ready.size() != before;
  }
  stats.committer_event_ns.fetch_add(nowNs() - event_start, std::memory_order_relaxed);
  if (ready) {
    state->cv.notify_one();
  }
}

struct GroupQueue {
  std::mutex mutex;
  std::condition_variable cv;
  std::string buffer;
  uint64_t max_seq = 0;
  bool done = false;
};

void groupFlusher(uint32_t logger_id, const Options& opt, int fd, GroupQueue& q,
                  WorkerState& logger_state, Stats& stats,
                  CommitterState* committer_state) {
  std::unique_lock<std::mutex> lock(q.mutex);
  while (!q.done || !q.buffer.empty()) {
    q.cv.wait_for(lock, std::chrono::microseconds(opt.flush_us), [&] {
      return q.done || q.max_seq - logger_state.durable_seq.load(std::memory_order_acquire) >= opt.group_size;
    });
    if (q.buffer.empty()) continue;
    std::string data;
    data.swap(q.buffer);
    const uint64_t durable_to = q.max_seq;
    lock.unlock();
    if (static_cast<int32_t>(logger_id) == opt.straggler_logger) {
      if (opt.straggler_extra_bytes != 0) {
        data.append(opt.straggler_extra_bytes, 'x');
      }
      if (opt.straggler_sleep_us != 0) {
        std::this_thread::sleep_for(std::chrono::microseconds(opt.straggler_sleep_us));
      }
    }
    const uint64_t write_start = nowNs();
    writeAll(fd, data.data(), data.size());
    stats.write_ns.fetch_add(nowNs() - write_start, std::memory_order_relaxed);
    if (!opt.skip_fdatasync) {
      const uint64_t fsync_start = nowNs();
      if (::fdatasync(fd) != 0) die("fdatasync");
      stats.fdatasync_ns.fetch_add(nowNs() - fsync_start, std::memory_order_relaxed);
      stats.fsync_count.fetch_add(1, std::memory_order_relaxed);
      logger_state.fsync_count.fetch_add(1, std::memory_order_relaxed);
    }
    logger_state.durable_seq.store(durable_to, std::memory_order_release);
    onDurableAdvanced(committer_state, logger_id, durable_to, stats);
    logger_state.bytes.fetch_add(data.size(), std::memory_order_relaxed);
    lock.lock();
    q.cv.notify_all();
  }
  (void)logger_id;
}

void runPwalGroupWorker(uint32_t thid, const Options& opt, GroupQueue& q,
                        WorkerState& app_worker, uint32_t logger_id,
                        std::vector<WorkerState>& loggers, Stats& stats,
                        std::vector<std::unique_ptr<FrontierState>>& frontier_states,
                        std::atomic<bool>& start, std::atomic<bool>& stop,
                        LatencySample& sample) {
  while (!start.load(std::memory_order_acquire)) {}
  uint64_t seq = 0;
  const bool wait_global_prefix = opt.mode == Mode::PwalGroupCommit;
  const bool wait_dep_frontier = opt.mode == Mode::PwalGroupDepFrontier;
  uint64_t rng = 0x9e3779b97f4a7c15ULL ^ (static_cast<uint64_t>(thid) << 32);
  while (!stop.load(std::memory_order_acquire)) {
    const uint64_t op_start = nowNs();
    const uint64_t build_start = nowNs();
    std::string record = makeRecord(thid, ++seq, opt);
    stats.payload_build_ns.fetch_add(nowNs() - build_start, std::memory_order_relaxed);
    std::vector<uint64_t> dep;
    if (wait_dep_frontier) {
      const uint64_t dep_start = nowNs();
      dep = buildDependencyFrontier(logger_id, opt, frontier_states, rng, stats);
      stats.dep_frontier_build_ns.fetch_add(nowNs() - dep_start, std::memory_order_relaxed);
    }
    const uint64_t wait_seq = loggers[logger_id].local_seq.fetch_add(1, std::memory_order_acq_rel) + 1;
    {
      const uint64_t wait_start = nowNs();
      std::lock_guard<std::mutex> guard(q.mutex);
      stats.mutex_wait_ns.fetch_add(nowNs() - wait_start, std::memory_order_relaxed);
      q.buffer += record;
      q.max_seq = std::max(q.max_seq, wait_seq);
    }
    q.cv.notify_one();
    if (wait_dep_frontier) {
      publishFrontier(logger_id, wait_seq, dep, frontier_states);
    }
    const uint64_t prefix_start = nowNs();
    if (wait_global_prefix) {
      waitForGlobalPrefix(loggers, wait_seq);
    } else if (wait_dep_frontier) {
      const uint64_t self_start = nowNs();
      waitForLocalDurable(loggers[logger_id], wait_seq);
      stats.self_durable_wait_ns.fetch_add(nowNs() - self_start, std::memory_order_relaxed);
      const uint64_t dep_start = nowNs();
      waitForDependencyFrontier(loggers, dep);
      stats.dependency_wait_ns.fetch_add(nowNs() - dep_start, std::memory_order_relaxed);
    } else {
      const uint64_t self_start = nowNs();
      waitForLocalDurable(loggers[logger_id], wait_seq);
      stats.self_durable_wait_ns.fetch_add(nowNs() - self_start, std::memory_order_relaxed);
    }
    stats.prefix_wait_ns.fetch_add(nowNs() - prefix_start, std::memory_order_relaxed);
    app_worker.local_seq.store(seq, std::memory_order_release);
    app_worker.commits.fetch_add(1, std::memory_order_relaxed);
    stats.acked_commits.fetch_add(1, std::memory_order_relaxed);
    stats.logical_commits.fetch_add(1, std::memory_order_relaxed);
    app_worker.bytes.fetch_add(record.size(), std::memory_order_relaxed);
    stats.bytes.fetch_add(record.size(), std::memory_order_relaxed);
    if ((seq & 0xff) == 0 && sample.micros.size() < 20000) {
      sample.micros.push_back(static_cast<uint32_t>((nowNs() - op_start) / 1000));
    }
  }
}

void eventDrivenCommitterThread(CommitterState& state,
                                std::vector<WorkerState>& workers,
                                Stats& stats) {
  std::unique_lock<std::mutex> lock(state.mutex);
  for (;;) {
    state.cv.wait(lock, [&] {
      return !state.ready.empty() || (state.done && state.pending_count == 0);
    });
    if (state.done && state.pending_count == 0) return;
    if (state.ready.empty()) continue;

    std::shared_ptr<PendingTxn> txn = state.ready.front();
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
    if (state.pending_count > 0) {
      state.pending_count--;
    }
    state.cv.notify_all();
  }
}

void runAsyncWorker(
    uint32_t thid, const Options& opt, GroupQueue& q, WorkerState& app_worker,
    uint32_t logger_id, std::vector<WorkerState>& loggers, Stats& stats,
    std::vector<std::unique_ptr<FrontierState>>& frontier_states,
    CommitterState& committer_state, std::atomic<uint64_t>& cstamp_allocator,
    std::atomic<uint64_t>& lsn_allocator,
    std::atomic<bool>& start, std::atomic<bool>& stop) {
  while (!start.load(std::memory_order_acquire)) {}
  uint64_t seq = 0;
  uint64_t rng = 0x517cc1b727220a95ULL ^ (static_cast<uint64_t>(thid) << 32);
  const bool use_dep_frontier = asyncUsesDependencyFrontier(opt.mode);
  const bool use_separate_lsn = asyncUsesSeparateLsn(opt.mode);
  while (!stop.load(std::memory_order_acquire)) {
    const uint64_t stall_start = nowNs();
    while (app_worker.outstanding.load(std::memory_order_acquire) >= opt.max_inflight &&
           !stop.load(std::memory_order_acquire)) {
      std::this_thread::yield();
    }
    stats.worker_stall_ns.fetch_add(nowNs() - stall_start, std::memory_order_relaxed);
    if (stop.load(std::memory_order_acquire)) break;

    const uint64_t op_start = nowNs();
    const uint64_t build_start = nowNs();
    std::string record = makeRecord(thid, ++seq, opt);
    stats.payload_build_ns.fetch_add(nowNs() - build_start, std::memory_order_relaxed);

    std::vector<uint64_t> dep(opt.logger_num, 0);
    if (use_dep_frontier) {
      const uint64_t dep_start = nowNs();
      dep = buildDependencyFrontier(logger_id, opt, frontier_states, rng, stats);
      stats.dep_frontier_build_ns.fetch_add(nowNs() - dep_start, std::memory_order_relaxed);
    }

    const uint64_t cstamp_start = nowNs();
    const uint64_t cstamp = cstamp_allocator.fetch_add(1, std::memory_order_acq_rel) + 1;
    stats.cstamp_alloc_ns.fetch_add(nowNs() - cstamp_start, std::memory_order_relaxed);
    stats.global_atomic_count.fetch_add(1, std::memory_order_relaxed);
    uint64_t lsn = 0;
    if (use_separate_lsn) {
      const uint64_t lsn_start = nowNs();
      lsn = lsn_allocator.fetch_add(1, std::memory_order_acq_rel) + 1;
      stats.lsn_alloc_ns.fetch_add(nowNs() - lsn_start, std::memory_order_relaxed);
      stats.global_atomic_count.fetch_add(1, std::memory_order_relaxed);
    }
    const uint64_t local_seq = loggers[logger_id].local_seq.fetch_add(1, std::memory_order_acq_rel) + 1;
    std::string header = "CSTAMP=" + std::to_string(cstamp) + " ";
    if (use_separate_lsn) {
      header += "LSN=" + std::to_string(lsn) + " ";
    }
    const uint64_t enqueue_start = nowNs();
    {
      const uint64_t wait_start = nowNs();
      std::lock_guard<std::mutex> guard(q.mutex);
      stats.mutex_wait_ns.fetch_add(nowNs() - wait_start, std::memory_order_relaxed);
      q.buffer += header;
      q.buffer += record;
      q.max_seq = std::max(q.max_seq, local_seq);
    }
    q.cv.notify_one();
    if (use_dep_frontier) {
      publishFrontier(logger_id, local_seq, dep, frontier_states);
    }

    std::vector<uint64_t> req = buildAsyncRequirement(opt.mode, logger_id, local_seq, dep, loggers);
    auto txn = std::make_shared<PendingTxn>();
    txn->thid = thid;
    txn->worker_seq = seq;
    txn->cstamp = cstamp;
    txn->lsn = lsn;
    txn->logger_id = logger_id;
    txn->local_seq = local_seq;
    txn->op_start_ns = op_start;
    txn->enqueue_ns = nowNs();
    txn->bytes = header.size() + record.size();
    txn->needs_global_lsn_prefix = opt.mode == Mode::AsyncGlobalLsnPrefix;
    txn->req = std::move(req);
    app_worker.outstanding.fetch_add(1, std::memory_order_acq_rel);
    stats.logical_commits.fetch_add(1, std::memory_order_relaxed);
    app_worker.local_seq.store(seq, std::memory_order_release);
    stats.bytes.fetch_add(txn->bytes, std::memory_order_relaxed);
    registerTxn(committer_state, txn, loggers, stats);
    stats.log_enqueue_ns.fetch_add(nowNs() - enqueue_start, std::memory_order_relaxed);
  }
}

}  // namespace

int main(int argc, char** argv) {
  const Options opt = parseOptions(argc, argv);
  std::filesystem::create_directories(opt.wal_dir);

  std::vector<WorkerState> workers(opt.thread_num);
  std::vector<WorkerState> logger_states(opt.logger_num);
  std::vector<std::unique_ptr<FrontierState>> frontier_states;
  frontier_states.reserve(opt.logger_num);
  for (uint32_t i = 0; i < opt.logger_num; ++i) {
    frontier_states.emplace_back(std::make_unique<FrontierState>(opt.logger_num));
  }
  std::vector<LatencySample> samples(opt.thread_num);
  Stats stats;
  std::atomic<bool> start{false};
  std::atomic<bool> stop{false};
  std::atomic<uint64_t> cstamp_allocator{0};
  std::atomic<uint64_t> lsn_allocator{0};
  std::vector<std::thread> threads;
  std::vector<int> fds;
  std::vector<std::unique_ptr<GroupQueue>> queues;
  std::vector<std::thread> flushers;
  CommitterState committer_state(opt.logger_num);
  std::thread committer;
  std::mutex shared_mutex;
  int shared_fd = -1;

  const std::string base = opt.wal_dir + "/" + modeName(opt.mode) + "_t" + std::to_string(opt.thread_num) +
                           "_pid" + std::to_string(static_cast<long long>(getpid()));
  std::filesystem::create_directories(base);

  if (opt.mode == Mode::SingleWal) {
    shared_fd = openWalFile(base + "/shared.wal", opt.prealloc_mb);
  } else if (isGroupedWalMode(opt.mode)) {
    fds.resize(opt.logger_num, -1);
    for (uint32_t i = 0; i < opt.logger_num; ++i) {
      fds[i] = openWalFile(base + "/logger_" + std::to_string(i) + ".wal", opt.prealloc_mb);
    }
  } else if (opt.mode != Mode::NoDurability) {
    fds.resize(opt.thread_num, -1);
    for (uint32_t i = 0; i < opt.thread_num; ++i) {
      fds[i] = openWalFile(base + "/worker_" + std::to_string(i) + ".wal", opt.prealloc_mb);
    }
  }

  if (isGroupedWalMode(opt.mode)) {
    queues.reserve(opt.logger_num);
    for (uint32_t i = 0; i < opt.logger_num; ++i) {
      queues.emplace_back(std::make_unique<GroupQueue>());
      flushers.emplace_back(groupFlusher, i, std::cref(opt), fds[i], std::ref(*queues.back()),
                            std::ref(logger_states[i]), std::ref(stats),
                            isAsyncMode(opt.mode) ? &committer_state : nullptr);
    }
  }

  if (isAsyncMode(opt.mode)) {
    committer = std::thread(eventDrivenCommitterThread, std::ref(committer_state),
                            std::ref(workers), std::ref(stats));
  }

  for (uint32_t i = 0; i < opt.thread_num; ++i) {
    switch (opt.mode) {
      case Mode::NoDurability:
        threads.emplace_back(runNoDurabilityWorker, i, std::cref(opt), std::ref(workers[i]),
                             std::ref(stats), std::ref(start), std::ref(stop), std::ref(samples[i]));
        break;
      case Mode::SingleWal:
        threads.emplace_back(runSingleWalWorker, i, std::cref(opt), shared_fd, std::ref(shared_mutex),
                             std::ref(workers[i]), std::ref(stats), std::ref(start), std::ref(stop),
                             std::ref(samples[i]));
        break;
      case Mode::SingleWalGroupCommit:
        threads.emplace_back(runPwalGroupWorker, i, std::cref(opt), std::ref(*queues[0]),
                             std::ref(workers[i]), 0, std::ref(logger_states),
                             std::ref(stats), std::ref(frontier_states), std::ref(start),
                             std::ref(stop), std::ref(samples[i]));
        break;
      case Mode::PwalPerTxnFdatasync:
        threads.emplace_back(runPwalPerTxnWorker, i, std::cref(opt), fds[i], std::ref(workers[i]),
                             std::ref(stats), std::ref(start), std::ref(stop), std::ref(samples[i]));
        break;
      case Mode::PwalGroupCommit:
      case Mode::PwalGroupCommitNoPrefix:
      case Mode::PwalGroupDepFrontier:
        threads.emplace_back(runPwalGroupWorker, i, std::cref(opt), std::ref(*queues[i % opt.logger_num]),
                             std::ref(workers[i]), i % opt.logger_num, std::ref(logger_states),
                             std::ref(stats), std::ref(frontier_states), std::ref(start),
                             std::ref(stop), std::ref(samples[i]));
        break;
      case Mode::AsyncGlobalLsnPrefix:
      case Mode::AsyncGlobalPrefixLsn:
      case Mode::AsyncLocalOnlyLsn:
      case Mode::AsyncDepFrontierLsn:
      case Mode::AsyncDepFrontierCstamp:
        threads.emplace_back(runAsyncWorker, i, std::cref(opt),
                             std::ref(*queues[i % opt.logger_num]), std::ref(workers[i]),
                             i % opt.logger_num, std::ref(logger_states), std::ref(stats),
                             std::ref(frontier_states), std::ref(committer_state),
                             std::ref(cstamp_allocator), std::ref(lsn_allocator),
                             std::ref(start), std::ref(stop));
        break;
    }
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

  if (isAsyncMode(opt.mode)) {
    for (uint32_t i = 0; i < opt.logger_num; ++i) {
      const uint64_t durable = logger_states[i].durable_seq.load(std::memory_order_acquire);
      onDurableAdvanced(&committer_state, i, durable, stats);
    }
    {
      std::lock_guard<std::mutex> guard(committer_state.mutex);
      committer_state.done = true;
    }
    committer_state.cv.notify_all();
    if (committer.joinable()) committer.join();
  }

  const double actual_sec = static_cast<double>(elapsedNs(begin)) / 1e9;

  if (shared_fd >= 0) {
    if (!opt.skip_fdatasync) {
      ::fdatasync(shared_fd);
    }
    ::close(shared_fd);
  }
  for (int fd : fds) {
    if (fd >= 0) {
      if (!opt.skip_fdatasync) {
        ::fdatasync(fd);
      }
      ::close(fd);
    }
  }

  uint64_t commits = 0;
  uint64_t worker_fsyncs = 0;
  for (const auto& w : workers) {
    commits += w.commits.load(std::memory_order_acquire);
    worker_fsyncs += w.fsync_count.load(std::memory_order_acquire);
  }
  for (const auto& w : logger_states) {
    worker_fsyncs += w.fsync_count.load(std::memory_order_acquire);
  }
  uint64_t logger_min_local_seq = std::numeric_limits<uint64_t>::max();
  uint64_t logger_max_local_seq = 0;
  uint64_t logger_min_durable_seq = std::numeric_limits<uint64_t>::max();
  uint64_t logger_max_durable_seq = 0;
  uint64_t logger_max_durable_lag = 0;
  uint64_t logger_bytes_flushed = 0;
  for (const auto& w : logger_states) {
    const uint64_t local = w.local_seq.load(std::memory_order_acquire);
    const uint64_t durable = w.durable_seq.load(std::memory_order_acquire);
    logger_min_local_seq = std::min(logger_min_local_seq, local);
    logger_max_local_seq = std::max(logger_max_local_seq, local);
    logger_min_durable_seq = std::min(logger_min_durable_seq, durable);
    logger_max_durable_seq = std::max(logger_max_durable_seq, durable);
    logger_max_durable_lag = std::max(logger_max_durable_lag, local - durable);
    logger_bytes_flushed += w.bytes.load(std::memory_order_acquire);
  }
  if (logger_states.empty()) {
    logger_min_local_seq = 0;
    logger_min_durable_seq = 0;
  }
  std::vector<uint32_t> all_lat;
  for (auto& s : samples) {
    all_lat.insert(all_lat.end(), s.micros.begin(), s.micros.end());
  }
  {
    std::lock_guard<std::mutex> guard(committer_state.mutex);
    all_lat.insert(all_lat.end(), committer_state.ack_latency_samples.begin(),
                   committer_state.ack_latency_samples.end());
  }
  uint64_t durable_global_lsn = 0;
  {
    std::lock_guard<std::mutex> guard(committer_state.mutex);
    durable_global_lsn = committer_state.durable_global_lsn;
  }
  uint32_t p50 = percentile(all_lat, 0.50);
  uint32_t p99 = percentile(all_lat, 0.99);
  const uint64_t bytes = stats.bytes.load(std::memory_order_acquire);
  const uint64_t fsyncs = stats.fsync_count.load(std::memory_order_acquire);
  const uint64_t logical_commits = stats.logical_commits.load(std::memory_order_acquire);
  const uint64_t acked_commits = stats.acked_commits.load(std::memory_order_acquire);
  const double closed_loop_avg_latency_us =
      commits ? actual_sec * static_cast<double>(opt.thread_num) * 1e6 / static_cast<double>(commits) : 0.0;

  std::cout << "mode: " << modeName(opt.mode) << "\n";
  std::cout << "thread_num: " << opt.thread_num << "\n";
  std::cout << "seconds: " << opt.seconds << "\n";
  std::cout << "actual_sec: " << actual_sec << "\n";
  std::cout << "write_set_size: " << opt.write_set_size << "\n";
  std::cout << "value_size: " << opt.value_size << "\n";
  std::cout << "group_size: " << opt.group_size << "\n";
  std::cout << "flush_us: " << opt.flush_us << "\n";
  std::cout << "logger_num: " << opt.logger_num << "\n";
  std::cout << "prealloc_mb: " << opt.prealloc_mb << "\n";
  std::cout << "straggler_logger: " << opt.straggler_logger << "\n";
  std::cout << "straggler_sleep_us: " << opt.straggler_sleep_us << "\n";
  std::cout << "straggler_extra_bytes: " << opt.straggler_extra_bytes << "\n";
  std::cout << "dep_prob_ppm: " << opt.dep_prob_ppm << "\n";
  std::cout << "dep_fanout: " << opt.dep_fanout << "\n";
  std::cout << "max_inflight: " << opt.max_inflight << "\n";
  std::cout << "committer_poll_us: " << opt.committer_poll_us << "\n";
  std::cout << "skip_fdatasync: " << (opt.skip_fdatasync ? 1 : 0) << "\n";
  std::cout << "commits: " << commits << "\n";
  std::cout << "logical_commits: " << logical_commits << "\n";
  std::cout << "acked_commits: " << acked_commits << "\n";
  std::cout << "logical_minus_acked: " << (logical_commits >= acked_commits ? logical_commits - acked_commits : 0) << "\n";
  std::cout << "logical_throughput_tps: " << static_cast<uint64_t>(logical_commits / actual_sec) << "\n";
  std::cout << "acked_throughput_tps: " << static_cast<uint64_t>(acked_commits / actual_sec) << "\n";
  std::cout << "throughput_tps: " << static_cast<uint64_t>(commits / actual_sec) << "\n";
  std::cout << "closed_loop_avg_latency_us: " << closed_loop_avg_latency_us << "\n";
  std::cout << "latency_p50_us: " << p50 << "\n";
  std::cout << "latency_p99_us: " << p99 << "\n";
  std::cout << "bytes: " << bytes << "\n";
  std::cout << "log_bytes_per_sec: " << static_cast<uint64_t>(bytes / actual_sec) << "\n";
  std::cout << "fdatasync_count: " << fsyncs << "\n";
  std::cout << "fdatasync_per_sec: " << static_cast<double>(fsyncs) / actual_sec << "\n";
  std::cout << "commits_per_fdatasync: " << (fsyncs ? static_cast<double>(commits) / fsyncs : 0.0) << "\n";
  std::cout << "worker_fsync_count_check: " << worker_fsyncs << "\n";
  std::cout << "logger_min_local_seq: " << logger_min_local_seq << "\n";
  std::cout << "logger_max_local_seq: " << logger_max_local_seq << "\n";
  std::cout << "logger_min_durable_seq: " << logger_min_durable_seq << "\n";
  std::cout << "logger_max_durable_seq: " << logger_max_durable_seq << "\n";
  std::cout << "logger_max_durable_lag: " << logger_max_durable_lag << "\n";
  std::cout << "logger_bytes_flushed: " << logger_bytes_flushed << "\n";
  std::cout << "payload_build_ns: " << stats.payload_build_ns.load(std::memory_order_acquire) << "\n";
  std::cout << "dep_frontier_build_ns: " << stats.dep_frontier_build_ns.load(std::memory_order_acquire) << "\n";
  std::cout << "cstamp_alloc_ns: " << stats.cstamp_alloc_ns.load(std::memory_order_acquire) << "\n";
  std::cout << "lsn_alloc_ns: " << stats.lsn_alloc_ns.load(std::memory_order_acquire) << "\n";
  std::cout << "log_enqueue_ns: " << stats.log_enqueue_ns.load(std::memory_order_acquire) << "\n";
  std::cout << "mutex_wait_ns: " << stats.mutex_wait_ns.load(std::memory_order_acquire) << "\n";
  std::cout << "write_ns: " << stats.write_ns.load(std::memory_order_acquire) << "\n";
  std::cout << "fdatasync_ns: " << stats.fdatasync_ns.load(std::memory_order_acquire) << "\n";
  std::cout << "prefix_wait_ns: " << stats.prefix_wait_ns.load(std::memory_order_acquire) << "\n";
  std::cout << "self_durable_wait_ns: " << stats.self_durable_wait_ns.load(std::memory_order_acquire) << "\n";
  std::cout << "dependency_wait_ns: " << stats.dependency_wait_ns.load(std::memory_order_acquire) << "\n";
  std::cout << "committer_scan_ns: " << stats.committer_scan_ns.load(std::memory_order_acquire) << "\n";
  std::cout << "committer_event_ns: " << stats.committer_event_ns.load(std::memory_order_acquire) << "\n";
  std::cout << "committer_queue_wait_ns: " << stats.committer_queue_wait_ns.load(std::memory_order_acquire) << "\n";
  std::cout << "avg_committer_queue_wait_us: "
            << (acked_commits ? static_cast<double>(stats.committer_queue_wait_ns.load(std::memory_order_acquire)) /
                                    static_cast<double>(acked_commits) / 1000.0
                              : 0.0)
            << "\n";
  std::cout << "worker_stall_ns: " << stats.worker_stall_ns.load(std::memory_order_acquire) << "\n";
  std::cout << "dep_edges: " << stats.dep_edges.load(std::memory_order_acquire) << "\n";
  std::cout << "dep_frontier_bytes: " << stats.dep_frontier_bytes.load(std::memory_order_acquire) << "\n";
  std::cout << "global_atomic_count: " << stats.global_atomic_count.load(std::memory_order_acquire) << "\n";
  std::cout << "waitlist_registrations: " << stats.waitlist_registrations.load(std::memory_order_acquire) << "\n";
  std::cout << "waitlist_pops: " << stats.waitlist_pops.load(std::memory_order_acquire) << "\n";
  std::cout << "ready_queue_pushes: " << stats.ready_queue_pushes.load(std::memory_order_acquire) << "\n";
  std::cout << "max_ready_queue_len: " << stats.max_ready_queue_len.load(std::memory_order_acquire) << "\n";
  std::cout << "max_waitlist_len: " << stats.max_waitlist_len.load(std::memory_order_acquire) << "\n";
  std::cout << "max_global_waitlist_len: " << stats.max_global_waitlist_len.load(std::memory_order_acquire) << "\n";
  std::cout << "max_pending_len: " << stats.max_pending_len.load(std::memory_order_acquire) << "\n";
  std::cout << "waiting_conditions: " << stats.waiting_conditions.load(std::memory_order_acquire) << "\n";
  std::cout << "durable_global_lsn: " << durable_global_lsn << "\n";
  return 0;
}
