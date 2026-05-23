#pragma once

#include <array>
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <mutex>
#include <sstream>
#include <string>
#include <string_view>
#include <thread>
#include <vector>

#include <fcntl.h>
#include <unistd.h>

#include "op_element.hh"

namespace ccbench {

constexpr size_t kSsnPwalMaxWorkers = 64;

class SsnPwalDLogger {
 public:
  using DepVector = std::array<uint32_t, kSsnPwalMaxWorkers>;

  static SsnPwalDLogger& instance() {
    static SsnPwalDLogger logger;
    return logger;
  }

  template <class WriteSet>
  void logCommitAndFlush(uint32_t thid, uint32_t commit_lsn,
                         const WriteSet& write_set, const DepVector& deps) {
    ensureWorker(thid);
    std::string payload;
    uint32_t seq = 0;
    for (const auto& we : write_set) {
      payload += makeRecordLine(commit_lsn, seq++, thid, we);
    }
    payload += makeCommitLine(commit_lsn, thid, deps);
    flushPayload(thid, commit_lsn, payload);
  }

  void logAbortAndFlush(uint32_t thid, uint32_t reserved_lsn) {
    ensureWorker(thid);
    std::ostringstream oss;
    oss << "LSN=" << reserved_lsn << " thid=" << thid << " op=A\n";
    flushPayload(thid, reserved_lsn, oss.str());
  }

  void waitForCommit(uint32_t thid, uint32_t commit_lsn,
                     const DepVector& deps) {
#if CCBENCH_SSN_PWAL_WAIT_MODE == 0
    (void)thid;
    (void)deps;
    while (durable_prefix_.load(std::memory_order_acquire) < commit_lsn) {
      std::this_thread::yield();
    }
#else
    while (flushed_lsn_[thid].load(std::memory_order_acquire) < commit_lsn) {
      std::this_thread::yield();
    }
    for (size_t worker = 0; worker < deps.size(); ++worker) {
      while (flushed_lsn_[worker].load(std::memory_order_acquire) <
             deps[worker]) {
        std::this_thread::yield();
      }
    }
#endif
  }

 private:
  SsnPwalDLogger() {
    for (auto& flushed : flushed_lsn_) {
      flushed.store(0, std::memory_order_relaxed);
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

  static std::string baseDir() {
    const char* env = std::getenv("CCBENCH_WAL_DIR");
    std::string root = env ? env : "/tmp/ccbench_wal";
    return root + "/" + CCBENCH_WAL_PROTOCOL_NAME + "_" +
           std::to_string(static_cast<long long>(getpid()));
  }

  static int openFile(const std::string& path) {
    int fd = ::open(path.c_str(), O_CREAT | O_TRUNC | O_WRONLY | O_CLOEXEC,
                    0644);
    if (fd < 0) {
      perror(path.c_str());
      std::abort();
    }
    return fd;
  }

  void ensureWorker(uint32_t thid) {
    if (thid >= kSsnPwalMaxWorkers) {
      std::fprintf(stderr, "SSN-PWAL-D supports up to 64 workers\n");
      std::abort();
    }
    std::lock_guard<std::mutex> guard(init_mutex_);
    if (base_dir_.empty()) {
      base_dir_ = baseDir();
      std::filesystem::create_directories(base_dir_);
      fds_.fill(-1);
    }
    if (fds_[thid] < 0) {
      fds_[thid] =
          openFile(base_dir_ + "/worker_" + std::to_string(thid) + ".wal");
    }
  }

  static void writeAll(int fd, std::string_view data) {
    const char* p = data.data();
    size_t left = data.size();
    while (left > 0) {
      ssize_t n = ::write(fd, p, left);
      if (n < 0) {
        perror("write ssn-pwal-d");
        std::abort();
      }
      p += n;
      left -= static_cast<size_t>(n);
    }
  }

  template <class WriteElement>
  static std::string makeRecordLine(uint32_t commit_lsn, uint32_t seq,
                                    uint32_t thid, const WriteElement& we) {
    std::ostringstream oss;
    const std::string_view key = we.key_;
    std::string_view val;
    if (we.op_ != OpType::DELETE) val = we.ver_->body_.get_val();
    oss << "LSN=" << commit_lsn << " seq=" << seq << " thid=" << thid
        << " storage=" << static_cast<uint32_t>(we.storage_)
        << " op=" << opChar(we.op_) << " key=";
    oss.write(key.data(), static_cast<std::streamsize>(key.size()));
    if (!val.empty()) {
      oss << " val=";
      oss.write(val.data(), static_cast<std::streamsize>(val.size()));
    }
    oss << "\n";
    return oss.str();
  }

  static std::string makeCommitLine(uint32_t commit_lsn, uint32_t thid,
                                    const DepVector& deps) {
    std::ostringstream oss;
    oss << "LSN=" << commit_lsn << " thid=" << thid << " op=C deps=";
    bool first = true;
    for (size_t worker = 0; worker < deps.size(); ++worker) {
      if (deps[worker] == 0) continue;
      if (!first) oss << ",";
      oss << worker << ":" << deps[worker];
      first = false;
    }
    oss << "\n";
    return oss.str();
  }

  void flushPayload(uint32_t thid, uint32_t lsn, const std::string& payload) {
    std::lock_guard<std::mutex> guard(worker_mutexes_[thid]);
    writeAll(fds_[thid], payload);
    fdatasync(fds_[thid]);
    flushed_lsn_[thid].store(lsn, std::memory_order_release);
    markDurable(lsn);
  }

  void markDurable(uint32_t lsn) {
    std::lock_guard<std::mutex> guard(durable_mutex_);
    if (durable_.size() <= lsn) durable_.resize(lsn + 1024, false);
    durable_[lsn] = true;
    uint32_t prefix = durable_prefix_.load(std::memory_order_acquire);
    while (prefix + 1 < durable_.size() && durable_[prefix + 1]) ++prefix;
    durable_prefix_.store(prefix, std::memory_order_release);
  }

  std::mutex init_mutex_;
  std::string base_dir_;
  std::array<int, kSsnPwalMaxWorkers> fds_{};
  std::array<std::mutex, kSsnPwalMaxWorkers> worker_mutexes_;
  std::array<std::atomic<uint32_t>, kSsnPwalMaxWorkers> flushed_lsn_;
  std::mutex durable_mutex_;
  std::vector<bool> durable_{true};
  std::atomic<uint32_t> durable_prefix_{0};
};

}  // namespace ccbench
