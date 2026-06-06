#include <algorithm>
#include <atomic>
#include <chrono>
#include <cerrno>
#include <condition_variable>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <limits>
#include <iostream>
#include <mutex>
#include <numeric>
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
  std::string wal_dir = "results/no_cc_wal_files";
};

struct alignas(64) WorkerState {
  std::atomic<uint64_t> commits{0};
  std::atomic<uint64_t> bytes{0};
  std::atomic<uint64_t> fsync_count{0};
  std::atomic<uint64_t> local_seq{0};
  std::atomic<uint64_t> durable_seq{0};
};

struct Stats {
  std::atomic<uint64_t> payload_build_ns{0};
  std::atomic<uint64_t> mutex_wait_ns{0};
  std::atomic<uint64_t> write_ns{0};
  std::atomic<uint64_t> fdatasync_ns{0};
  std::atomic<uint64_t> prefix_wait_ns{0};
  std::atomic<uint64_t> bytes{0};
  std::atomic<uint64_t> fsync_count{0};
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
  if (opt.logger_num == 0 || opt.logger_num > opt.thread_num) {
    std::cerr << "--logger_num must be in [1,thread_num]\n";
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

uint32_t percentile(std::vector<uint32_t>& values, double p) {
  if (values.empty()) return 0;
  std::sort(values.begin(), values.end());
  const size_t idx = std::min(values.size() - 1, static_cast<size_t>((values.size() - 1) * p));
  return values[idx];
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
    const uint64_t fsync_start = nowNs();
    if (::fdatasync(fd) != 0) die("fdatasync");
    stats.fdatasync_ns.fetch_add(nowNs() - fsync_start, std::memory_order_relaxed);
    mutex.unlock();
    stats.fsync_count.fetch_add(1, std::memory_order_relaxed);
    worker.fsync_count.fetch_add(1, std::memory_order_relaxed);
    worker.commits.fetch_add(1, std::memory_order_relaxed);
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
    const uint64_t fsync_start = nowNs();
    if (::fdatasync(fd) != 0) die("fdatasync");
    stats.fdatasync_ns.fetch_add(nowNs() - fsync_start, std::memory_order_relaxed);
    worker.durable_seq.store(seq, std::memory_order_release);
    stats.fsync_count.fetch_add(1, std::memory_order_relaxed);
    worker.fsync_count.fetch_add(1, std::memory_order_relaxed);
    worker.commits.fetch_add(1, std::memory_order_relaxed);
    worker.bytes.fetch_add(record.size(), std::memory_order_relaxed);
    stats.bytes.fetch_add(record.size(), std::memory_order_relaxed);
    if ((seq & 0xff) == 0 && sample.micros.size() < 20000) {
      sample.micros.push_back(static_cast<uint32_t>((nowNs() - op_start) / 1000));
    }
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
                  WorkerState& logger_state, Stats& stats) {
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
    const uint64_t fsync_start = nowNs();
    if (::fdatasync(fd) != 0) die("fdatasync");
    stats.fdatasync_ns.fetch_add(nowNs() - fsync_start, std::memory_order_relaxed);
    logger_state.durable_seq.store(durable_to, std::memory_order_release);
    stats.fsync_count.fetch_add(1, std::memory_order_relaxed);
    logger_state.fsync_count.fetch_add(1, std::memory_order_relaxed);
    logger_state.bytes.fetch_add(data.size(), std::memory_order_relaxed);
    lock.lock();
    q.cv.notify_all();
  }
  (void)logger_id;
}

void runPwalGroupWorker(uint32_t thid, const Options& opt, GroupQueue& q,
                        WorkerState& app_worker, uint32_t logger_id,
                        std::vector<WorkerState>& loggers, Stats& stats,
                        std::atomic<bool>& start, std::atomic<bool>& stop,
                        LatencySample& sample) {
  while (!start.load(std::memory_order_acquire)) {}
  uint64_t seq = 0;
  const bool wait_global_prefix = opt.mode == Mode::PwalGroupCommit;
  while (!stop.load(std::memory_order_acquire)) {
    const uint64_t op_start = nowNs();
    const uint64_t build_start = nowNs();
    std::string record = makeRecord(thid, ++seq, opt);
    stats.payload_build_ns.fetch_add(nowNs() - build_start, std::memory_order_relaxed);
    const uint64_t wait_seq = loggers[logger_id].local_seq.fetch_add(1, std::memory_order_acq_rel) + 1;
    {
      const uint64_t wait_start = nowNs();
      std::lock_guard<std::mutex> guard(q.mutex);
      stats.mutex_wait_ns.fetch_add(nowNs() - wait_start, std::memory_order_relaxed);
      q.buffer += record;
      q.max_seq = std::max(q.max_seq, wait_seq);
    }
    q.cv.notify_one();
    const uint64_t prefix_start = nowNs();
    if (wait_global_prefix) {
      waitForGlobalPrefix(loggers, wait_seq);
    } else {
      waitForLocalDurable(loggers[logger_id], wait_seq);
    }
    stats.prefix_wait_ns.fetch_add(nowNs() - prefix_start, std::memory_order_relaxed);
    app_worker.local_seq.store(seq, std::memory_order_release);
    app_worker.commits.fetch_add(1, std::memory_order_relaxed);
    app_worker.bytes.fetch_add(record.size(), std::memory_order_relaxed);
    stats.bytes.fetch_add(record.size(), std::memory_order_relaxed);
    if ((seq & 0xff) == 0 && sample.micros.size() < 20000) {
      sample.micros.push_back(static_cast<uint32_t>((nowNs() - op_start) / 1000));
    }
  }
}

}  // namespace

int main(int argc, char** argv) {
  const Options opt = parseOptions(argc, argv);
  std::filesystem::create_directories(opt.wal_dir);

  std::vector<WorkerState> workers(opt.thread_num);
  std::vector<WorkerState> logger_states(opt.logger_num);
  std::vector<LatencySample> samples(opt.thread_num);
  Stats stats;
  std::atomic<bool> start{false};
  std::atomic<bool> stop{false};
  std::vector<std::thread> threads;
  std::vector<int> fds;
  std::vector<std::unique_ptr<GroupQueue>> queues;
  std::vector<std::thread> flushers;
  std::mutex shared_mutex;
  int shared_fd = -1;

  const std::string base = opt.wal_dir + "/" + modeName(opt.mode) + "_t" + std::to_string(opt.thread_num) +
                           "_pid" + std::to_string(static_cast<long long>(getpid()));
  std::filesystem::create_directories(base);

  if (opt.mode == Mode::SingleWal) {
    shared_fd = openWalFile(base + "/shared.wal", opt.prealloc_mb);
  } else if (opt.mode == Mode::SingleWalGroupCommit ||
             opt.mode == Mode::PwalGroupCommit ||
             opt.mode == Mode::PwalGroupCommitNoPrefix) {
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

  if (opt.mode == Mode::SingleWalGroupCommit ||
      opt.mode == Mode::PwalGroupCommit ||
      opt.mode == Mode::PwalGroupCommitNoPrefix) {
    queues.reserve(opt.logger_num);
    for (uint32_t i = 0; i < opt.logger_num; ++i) {
      queues.emplace_back(std::make_unique<GroupQueue>());
      flushers.emplace_back(groupFlusher, i, std::cref(opt), fds[i], std::ref(*queues.back()),
                            std::ref(logger_states[i]), std::ref(stats));
    }
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
                             std::ref(stats), std::ref(start), std::ref(stop), std::ref(samples[i]));
        break;
      case Mode::PwalPerTxnFdatasync:
        threads.emplace_back(runPwalPerTxnWorker, i, std::cref(opt), fds[i], std::ref(workers[i]),
                             std::ref(stats), std::ref(start), std::ref(stop), std::ref(samples[i]));
        break;
      case Mode::PwalGroupCommit:
      case Mode::PwalGroupCommitNoPrefix:
        threads.emplace_back(runPwalGroupWorker, i, std::cref(opt), std::ref(*queues[i % opt.logger_num]),
                             std::ref(workers[i]), i % opt.logger_num, std::ref(logger_states),
                             std::ref(stats), std::ref(start), std::ref(stop), std::ref(samples[i]));
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

  const double actual_sec = static_cast<double>(elapsedNs(begin)) / 1e9;

  if (shared_fd >= 0) {
    ::fdatasync(shared_fd);
    ::close(shared_fd);
  }
  for (int fd : fds) {
    if (fd >= 0) {
      ::fdatasync(fd);
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
  uint32_t p50 = percentile(all_lat, 0.50);
  uint32_t p99 = percentile(all_lat, 0.99);
  const uint64_t bytes = stats.bytes.load(std::memory_order_acquire);
  const uint64_t fsyncs = stats.fsync_count.load(std::memory_order_acquire);
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
  std::cout << "commits: " << commits << "\n";
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
  std::cout << "mutex_wait_ns: " << stats.mutex_wait_ns.load(std::memory_order_acquire) << "\n";
  std::cout << "write_ns: " << stats.write_ns.load(std::memory_order_acquire) << "\n";
  std::cout << "fdatasync_ns: " << stats.fdatasync_ns.load(std::memory_order_acquire) << "\n";
  std::cout << "prefix_wait_ns: " << stats.prefix_wait_ns.load(std::memory_order_acquire) << "\n";
  return 0;
}
