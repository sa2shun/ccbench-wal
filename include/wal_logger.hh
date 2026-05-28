#pragma once

#include <algorithm>
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <chrono>
#include <cstring>
#include <iostream>
#include <deque>
#include <filesystem>
#include <mutex>
#include <sstream>
#include <string>
#include <string_view>
#include <thread>
#include <vector>

#include <fcntl.h>
#include <sys/types.h>
#include <unistd.h>

#include "op_element.hh"

namespace ccbench {

enum class WalMode {
  Shared,
  PerThread,
};

class WalLogger {
 public:
  static WalLogger& instance() {
    static WalLogger logger;
    return logger;
  }

  void configure(WalMode mode, uint32_t thread_num, std::string protocol_name) {
    std::lock_guard<std::mutex> guard(init_mutex_);
    if (configured_.load(std::memory_order_acquire)) return;

    mode_ = mode;
    thread_num_ = thread_num;
    protocol_name_ = std::move(protocol_name);
    base_dir_ = makeBaseDir(protocol_name_);
    std::filesystem::create_directories(base_dir_);

    if (mode_ == WalMode::Shared) {
      shared_fd_ = openFile(base_dir_ + "/shared.wal");
    } else {
      worker_fds_.resize(thread_num_, -1);
      buffers_.resize(thread_num_);
      commit_queues_.resize(thread_num_);
    }
    configured_.store(true, std::memory_order_release);
  }

  template <class WriteSet>
  uint64_t logCommit(uint32_t thid, uint32_t cstamp, const WriteSet& write_set) {
    ensureConfigured(thid + 1);
    if (mode_ == WalMode::Shared) {
      return logShared(thid, cstamp, write_set);
    }
    return logPerThread(thid, cstamp, write_set);
  }

  void shutdown() {
    std::lock_guard<std::mutex> guard(init_mutex_);
    if (shared_fd_ >= 0) {
      fdatasync(shared_fd_);
      close(shared_fd_);
      shared_fd_ = -1;
    }
    for (int& fd : worker_fds_) {
      if (fd >= 0) {
        fdatasync(fd);
        close(fd);
        fd = -1;
      }
    }
    configured_.store(false, std::memory_order_release);
  }

  uint64_t durablePrefixForDebug() const {
    return durable_prefix_lsn_.load(std::memory_order_acquire);
  }

  ~WalLogger() {
    printStats();
  }

 private:
  struct PendingCommit {
    uint64_t commit_lsn;
  };

  struct Stats {
    std::atomic<uint64_t> commits{0};
    std::atomic<uint64_t> payload_build_ns{0};
    std::atomic<uint64_t> mutex_wait_ns{0};
    std::atomic<uint64_t> write_ns{0};
    std::atomic<uint64_t> fdatasync_ns{0};
    std::atomic<uint64_t> notify_wait_ns{0};
    std::atomic<uint64_t> bytes{0};
  };

  WalLogger() = default;

  static uint64_t nowNs() {
    return static_cast<uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now().time_since_epoch()).count());
  }

  void printStats() const {
    const uint64_t commits = stats_.commits.load(std::memory_order_acquire);
    if (commits == 0) return;
    const uint64_t payload = stats_.payload_build_ns.load(std::memory_order_acquire);
    const uint64_t mutex = stats_.mutex_wait_ns.load(std::memory_order_acquire);
    const uint64_t write = stats_.write_ns.load(std::memory_order_acquire);
    const uint64_t fsync = stats_.fdatasync_ns.load(std::memory_order_acquire);
    const uint64_t notify = stats_.notify_wait_ns.load(std::memory_order_acquire);
    const uint64_t bytes = stats_.bytes.load(std::memory_order_acquire);
    const uint64_t total = payload + mutex + write + fsync + notify;
    std::cout << "wal_stats_commits:	" << commits << std::endl;
    std::cout << "wal_stats_bytes:	" << bytes << std::endl;
    std::cout << "wal_stats_payload_build_ns:	" << payload << std::endl;
    std::cout << "wal_stats_mutex_wait_ns:	" << mutex << std::endl;
    std::cout << "wal_stats_write_ns:	" << write << std::endl;
    std::cout << "wal_stats_fdatasync_ns:	" << fsync << std::endl;
    std::cout << "wal_stats_notify_wait_ns:	" << notify << std::endl;
    std::cout << "wal_stats_total_accounted_ns:	" << total << std::endl;
    std::cout << "wal_stats_avg_accounted_ns_per_commit:	" << (total / commits) << std::endl;
  }

  static std::string makeBaseDir(const std::string& protocol_name) {
    const char* env = std::getenv("CCBENCH_WAL_DIR");
    std::string root = env ? env : "/tmp/ccbench_wal";
    return root + "/" + protocol_name + "_" + std::to_string(static_cast<long long>(getpid()));
  }

  static int openFile(const std::string& path) {
    int fd = ::open(path.c_str(), O_CREAT | O_TRUNC | O_WRONLY | O_CLOEXEC, 0644);
    if (fd < 0) {
      perror(path.c_str());
      std::abort();
    }
    return fd;
  }

  void ensureConfigured(uint32_t min_threads) {
    if (configured_.load(std::memory_order_acquire)) return;
#if defined(CCBENCH_WAL_PWAL)
    configure(WalMode::PerThread, min_threads, CCBENCH_WAL_PROTOCOL_NAME);
#else
    configure(WalMode::Shared, min_threads, CCBENCH_WAL_PROTOCOL_NAME);
#endif
  }

  static void writeAll(int fd, std::string_view data) {
    const char* p = data.data();
    size_t left = data.size();
    while (left > 0) {
      ssize_t n = ::write(fd, p, left);
      if (n < 0) {
        perror("write wal");
        std::abort();
      }
      p += n;
      left -= static_cast<size_t>(n);
    }
  }

  static char opChar(OpType op) {
    switch (op) {
      case OpType::INSERT:
        return 'I';
      case OpType::UPDATE:
        return 'U';
      case OpType::DELETE:
        return 'D';
      default:
        return '?';
    }
  }

  template <class WriteElement>
  static std::string makeRecordLine(uint64_t lsn, uint32_t thid, uint32_t cstamp,
                                    const WriteElement& we) {
    std::ostringstream oss;
    const std::string_view key = we.key_;
    std::string_view val;
    if (we.op_ != OpType::DELETE) {
      val = we.ver_->body_.get_val();
    }
    oss << "LSN=" << lsn
        << " thid=" << thid
        << " cstamp=" << cstamp
        << " storage=" << static_cast<uint32_t>(we.storage_)
        << " op=" << opChar(we.op_)
        << " key_len=" << key.size()
        << " val_len=" << val.size()
        << " key=";
    oss.write(key.data(), static_cast<std::streamsize>(key.size()));
    if (!val.empty()) {
      oss << " val=";
      oss.write(val.data(), static_cast<std::streamsize>(val.size()));
    }
    oss << "\n";
    return oss.str();
  }

  static std::string makeCommitLine(uint64_t lsn, uint32_t thid, uint32_t cstamp) {
    std::ostringstream oss;
    oss << "LSN=" << lsn
        << " thid=" << thid
        << " cstamp=" << cstamp
        << " op=C\n";
    return oss.str();
  }

  template <class WriteSet>
  uint64_t logShared(uint32_t thid, uint32_t cstamp, const WriteSet& write_set) {
    const uint64_t wait_start = nowNs();
    shared_mutex_.lock();
    const uint64_t lock_acquired = nowNs();
    std::unique_lock<std::mutex> guard(shared_mutex_, std::adopt_lock);
    stats_.mutex_wait_ns.fetch_add(lock_acquired - wait_start, std::memory_order_relaxed);

    uint64_t commit_lsn = 0;
    uint64_t bytes = 0;
    for (const auto& we : write_set) {
      const uint64_t lsn = next_lsn_.fetch_add(1, std::memory_order_acq_rel);
      const uint64_t build_start = nowNs();
      const std::string line = makeRecordLine(lsn, thid, cstamp, we);
      stats_.payload_build_ns.fetch_add(nowNs() - build_start, std::memory_order_relaxed);
      const uint64_t write_start = nowNs();
      writeAll(shared_fd_, line);
      stats_.write_ns.fetch_add(nowNs() - write_start, std::memory_order_relaxed);
      bytes += line.size();
    }
    commit_lsn = next_lsn_.fetch_add(1, std::memory_order_acq_rel);
    const uint64_t build_start = nowNs();
    const std::string line = makeCommitLine(commit_lsn, thid, cstamp);
    stats_.payload_build_ns.fetch_add(nowNs() - build_start, std::memory_order_relaxed);
    const uint64_t write_start = nowNs();
    writeAll(shared_fd_, line);
    stats_.write_ns.fetch_add(nowNs() - write_start, std::memory_order_relaxed);
    bytes += line.size();
    const uint64_t fsync_start = nowNs();
    fdatasync(shared_fd_);
    stats_.fdatasync_ns.fetch_add(nowNs() - fsync_start, std::memory_order_relaxed);
    markDurableThrough(commit_lsn);
    stats_.bytes.fetch_add(bytes, std::memory_order_relaxed);
    stats_.commits.fetch_add(1, std::memory_order_relaxed);
    return commit_lsn;
  }

  template <class WriteSet>
  uint64_t logPerThread(uint32_t thid, uint32_t cstamp, const WriteSet& write_set) {
    ensureWorker(thid);
    std::vector<uint64_t> lsns;
    lsns.reserve(write_set.size() + 1);
    std::string payload;
    const uint64_t build_start = nowNs();
    for (const auto& we : write_set) {
      const uint64_t lsn = next_lsn_.fetch_add(1, std::memory_order_acq_rel);
      lsns.push_back(lsn);
      payload += makeRecordLine(lsn, thid, cstamp, we);
    }
    const uint64_t commit_lsn = next_lsn_.fetch_add(1, std::memory_order_acq_rel);
    lsns.push_back(commit_lsn);
    payload += makeCommitLine(commit_lsn, thid, cstamp);
    stats_.payload_build_ns.fetch_add(nowNs() - build_start, std::memory_order_relaxed);

    {
      const uint64_t wait_start = nowNs();
      worker_mutexes_[thid].lock();
      const uint64_t lock_acquired = nowNs();
      std::unique_lock<std::mutex> guard(worker_mutexes_[thid], std::adopt_lock);
      stats_.mutex_wait_ns.fetch_add(lock_acquired - wait_start, std::memory_order_relaxed);
      buffers_[thid] += payload;
      commit_queues_[thid].push_back(PendingCommit{commit_lsn});
      const uint64_t write_start = nowNs();
      writeAll(worker_fds_[thid], buffers_[thid]);
      stats_.write_ns.fetch_add(nowNs() - write_start, std::memory_order_relaxed);
      buffers_[thid].clear();
      const uint64_t fsync_start = nowNs();
      fdatasync(worker_fds_[thid]);
      stats_.fdatasync_ns.fetch_add(nowNs() - fsync_start, std::memory_order_relaxed);
      for (uint64_t lsn : lsns) markDurable(lsn);
    }

    const uint64_t notify_start = nowNs();
    waitForCommit(thid, commit_lsn);
    stats_.notify_wait_ns.fetch_add(nowNs() - notify_start, std::memory_order_relaxed);
    stats_.bytes.fetch_add(payload.size(), std::memory_order_relaxed);
    stats_.commits.fetch_add(1, std::memory_order_relaxed);
    return commit_lsn;
  }

  void ensureWorker(uint32_t thid) {
    if (thid >= 256) {
      std::fprintf(stderr, "P-WAL prototype supports up to 256 workers\n");
      std::abort();
    }
    std::lock_guard<std::mutex> guard(init_mutex_);
    if (thid >= worker_fds_.size()) {
      worker_fds_.resize(thid + 1, -1);
      buffers_.resize(thid + 1);
      commit_queues_.resize(thid + 1);
    }
    if (worker_fds_[thid] < 0) {
      worker_fds_[thid] = openFile(base_dir_ + "/worker_" + std::to_string(thid) + ".wal");
    }
  }

  void markDurableThrough(uint64_t lsn) {
    for (;;) {
      uint64_t current = durable_prefix_lsn_.load(std::memory_order_acquire);
      if (current >= lsn) return;
      if (durable_prefix_lsn_.compare_exchange_weak(
              current, lsn,
              std::memory_order_acq_rel, std::memory_order_acquire)) {
        return;
      }
    }
  }

  void markDurable(uint64_t lsn) {
    std::lock_guard<std::mutex> guard(durable_mutex_);
    if (lsn >= durable_.size()) durable_.resize(lsn + 1024, false);
    durable_[lsn] = true;
    uint64_t prefix = durable_prefix_lsn_.load(std::memory_order_acquire);
    while (prefix + 1 < durable_.size() && durable_[prefix + 1]) {
      ++prefix;
    }
    durable_prefix_lsn_.store(prefix, std::memory_order_release);
  }

  void waitForCommit(uint32_t thid, uint64_t commit_lsn) {
    for (;;) {
      controlNotification(thid);
      if (durable_prefix_lsn_.load(std::memory_order_acquire) >= commit_lsn) return;
      std::this_thread::yield();
    }
  }

  void controlNotification(uint32_t thid) {
    std::lock_guard<std::mutex> guard(worker_mutexes_[thid]);
    auto& q = commit_queues_[thid];
    const uint64_t durable_prefix = durable_prefix_lsn_.load(std::memory_order_acquire);
    while (!q.empty() && q.front().commit_lsn <= durable_prefix) {
      q.pop_front();
    }
  }

  std::mutex init_mutex_;
  std::atomic<bool> configured_{false};
  WalMode mode_ = WalMode::Shared;
  uint32_t thread_num_ = 0;
  std::string protocol_name_;
  std::string base_dir_;

  std::atomic<uint64_t> next_lsn_{1};
  std::atomic<uint64_t> durable_prefix_lsn_{0};

  int shared_fd_ = -1;
  std::mutex shared_mutex_;

  std::vector<int> worker_fds_;
  std::vector<std::string> buffers_;
  std::vector<std::deque<PendingCommit>> commit_queues_;
  std::mutex worker_mutexes_[256];

  std::mutex durable_mutex_;
  std::vector<bool> durable_{false};
  Stats stats_;
};

}  // namespace ccbench
