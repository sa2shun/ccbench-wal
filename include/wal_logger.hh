#pragma once

#include <algorithm>
#include <array>
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <chrono>
#include <condition_variable>
#include <cstring>
#include <iostream>
#include <deque>
#include <filesystem>
#include <memory>
#include <mutex>
#include <queue>
#include <sstream>
#include <string>
#include <string_view>
#include <thread>
#include <vector>

#include <fcntl.h>
#include <sys/types.h>
#include <unistd.h>

#include "cpu.hh"
#include "op_element.hh"
#include "wal_frontier.hh"

namespace ccbench {

enum class WalMode {
  Shared,
  PerThread,
};

enum class WalDurableMode {
  Sync,
  GroupGlobalPrefix,
  AsyncGlobalLsnPrefix,
  AsyncDepFrontierLsn,
  AsyncDepFrontierCstamp,
  AsyncDepFrontierCstampNoPublish,
  AsyncDepFrontierCstampZeroDep,
  AsyncDepFrontierCstampPrealloc,
};

struct WalCommitResult {
  uint64_t commit_lsn = 0;
  uint32_t log_id = 0;
  uint64_t local_seq = 0;
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
    durable_mode_ = parseDurableMode();
    async_group_size_ = envUint64("CCBENCH_WAL_GROUP_SIZE", 8);
    async_flush_us_ = envUint64("CCBENCH_WAL_FLUSH_US", 100);
    max_pending_async_ = envUint64("CCBENCH_WAL_MAX_PENDING", 65536);
    logger_num_ = static_cast<uint32_t>(
        std::min<uint64_t>(envUint64("CCBENCH_WAL_LOGGER_NUM", thread_num),
                           kMaxWalWorkers));
    if (logger_num_ == 0) logger_num_ = 1;
    committer_num_ = static_cast<uint32_t>(
        std::min<uint64_t>(envUint64("CCBENCH_WAL_COMMITTER_NUM", 1),
                           logger_num_));
    if (committer_num_ == 0) committer_num_ = 1;
    skip_fdatasync_ = envBool("CCBENCH_WAL_SKIP_FDATASYNC", false);
    skip_read_only_ = envBool("CCBENCH_WAL_SKIP_READ_ONLY", false);
    straggler_logger_ = envInt("CCBENCH_WAL_STRAGGLER_LOGGER", -1);
    straggler_sleep_us_ = envUint64("CCBENCH_WAL_STRAGGLER_SLEEP_US", 0);
    base_dir_ = makeBaseDir(protocol_name_);
    std::filesystem::create_directories(base_dir_);

    if (mode_ == WalMode::Shared) {
      shared_fd_ = openFile(base_dir_ + "/shared.wal");
    } else {
      // Keep vectors stable for background flusher threads.
      worker_fds_.resize(kMaxWalWorkers, -1);
      buffers_.resize(kMaxWalWorkers);
      commit_queues_.resize(kMaxWalWorkers);
      if (isQueuedMode(durable_mode_)) {
        for (uint32_t i = 0; i < logger_num_; ++i) {
          commit_event_queues_[i] = std::make_unique<CommitEventQueue>();
        }
        startCommittersLocked();
      }
    }
    configured_.store(true, std::memory_order_release);
  }

  template <class WriteSet>
  uint64_t logCommit(uint32_t thid, uint32_t cstamp, const WriteSet& write_set) {
    return logCommitWithFrontier(thid, cstamp, write_set, nullptr).commit_lsn;
  }

  template <class WriteSet>
  WalCommitResult logCommitWithFrontier(uint32_t thid, uint32_t cstamp,
                                        const WriteSet& write_set,
                                        const WalFrontier* dep_frontier) {
    ensureConfigured(thid + 1);
    if (skip_read_only_ && write_set.empty() && isWorkerWaitMode(durable_mode_)) {
      stats_.commits.fetch_add(1, std::memory_order_relaxed);
      stats_.read_only_commits.fetch_add(1, std::memory_order_relaxed);
      recordAckLatency(0);
      return WalCommitResult{0, 0, 0};
    }
    if (mode_ == WalMode::Shared) {
      return WalCommitResult{logShared(thid, cstamp, write_set), 0, 0};
    }
    if (durable_mode_ == WalDurableMode::GroupGlobalPrefix) {
      return logPerThreadQueued(thid, cstamp, write_set, nullptr, true);
    }
    if (durable_mode_ == WalDurableMode::AsyncGlobalLsnPrefix ||
        usesDepAck(durable_mode_)) {
      return logPerThreadQueued(thid, cstamp, write_set, dep_frontier, false);
    }
    return WalCommitResult{logPerThread(thid, cstamp, write_set), thid, 0};
  }

  void ackReadOnlyWithFrontier(uint32_t thid, const WalFrontier* dep_frontier) {
    ensureConfigured(thid + 1);
    const uint64_t enqueue_ns = nowNs();
    stats_.commits.fetch_add(1, std::memory_order_relaxed);
    stats_.read_only_commits.fetch_add(1, std::memory_order_relaxed);
    if (!usesDepAck(durable_mode_) || !frontierCollectRequested(durable_mode_)) {
      recordAckLatency(0);
      stats_.async_acked_commits.fetch_add(1, std::memory_order_relaxed);
      return;
    }
    throttleAsyncPending();

    WalFrontier dep;
    const WalFrontier* dep_for_ack = &dep;
    if (dep_frontier) {
      if (durable_mode_ == WalDurableMode::AsyncDepFrontierCstampPrealloc) {
        thread_local WalFrontier tls_dep;
        tls_dep.reset(shardCount());
        tls_dep.merge(*dep_frontier, shardCount());
        dep_for_ack = &tls_dep;
      } else {
        dep = *dep_frontier;
        dep.ensureSize(shardCount());
      }
      stats_.dep_frontier_bytes.fetch_add(dep_for_ack->sizeBytes(shardCount()),
                                          std::memory_order_relaxed);
      stats_.dep_frontier_entries.fetch_add(dep_for_ack->entries(shardCount()),
                                            std::memory_order_relaxed);
      stats_.dep_frontier_nonzero_entries.fetch_add(
          dep_for_ack->nonzeroEntries(shardCount()),
          std::memory_order_relaxed);
    }
    registerDepAck(0, 0, *dep_for_ack, enqueue_ns, false);
  }

  static bool dependencyFrontierRequested() {
    WalDurableMode mode = parseDurableMode();
    return frontierCollectRequested(mode);
  }

  static bool frontierPublishRequested() {
    return frontierPublishRequested(parseDurableMode());
  }

  static bool zeroDependencyFrontierRequested() {
    return parseDurableMode() == WalDurableMode::AsyncDepFrontierCstampZeroDep;
  }

  void recordVersionInstall(uint64_t ns) {
    stats_.version_install_ns.fetch_add(ns, std::memory_order_relaxed);
  }

  void recordFrontierCollect(uint64_t ns) {
    stats_.frontier_collect_ns.fetch_add(ns, std::memory_order_relaxed);
  }

  void recordFrontierMerge(uint64_t ns) {
    stats_.frontier_merge_ns.fetch_add(ns, std::memory_order_relaxed);
  }

  void recordFrontierPublish(uint64_t ns) {
    stats_.frontier_publish_ns.fetch_add(ns, std::memory_order_relaxed);
  }

  void recordFrontierPublishMetadata(uint64_t read_updates,
                                     uint64_t write_updates,
                                     uint64_t alloc_count,
                                     uint64_t shared_ptr_count) {
    stats_.read_frontier_updates.fetch_add(read_updates,
                                           std::memory_order_relaxed);
    stats_.write_frontier_updates.fetch_add(write_updates,
                                            std::memory_order_relaxed);
    stats_.frontier_alloc_count.fetch_add(alloc_count,
                                          std::memory_order_relaxed);
    stats_.frontier_shared_ptr_count.fetch_add(shared_ptr_count,
                                               std::memory_order_relaxed);
  }

  uint32_t shardCount() const {
    uint32_t n = logger_num_;
    if (n == 0) n = static_cast<uint32_t>(envUint64("CCBENCH_WAL_LOGGER_NUM", 0));
    if (n == 0) n = kMaxWalWorkers;
    return std::min<uint32_t>(n, kMaxWalWorkers);
  }

  void shutdown() {
    std::lock_guard<std::mutex> guard(init_mutex_);
    if (!configured_.load(std::memory_order_acquire)) return;
    stopAsyncFlushersLocked();
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

  void markMeasurementStop() {
    bool expected = false;
    if (!measurement_stop_recorded_.compare_exchange_strong(
            expected, true, std::memory_order_acq_rel)) {
      return;
    }
    measured_logical_commits_.store(stats_.commits.load(std::memory_order_acquire),
                                    std::memory_order_release);
    measured_async_acked_commits_.store(
        stats_.async_acked_commits.load(std::memory_order_acquire),
        std::memory_order_release);
    measured_durable_global_lsn_.store(
        durable_prefix_lsn_.load(std::memory_order_acquire),
        std::memory_order_release);
    measured_ack_latency_samples_.store(
        ack_latency_samples_.load(std::memory_order_acquire),
        std::memory_order_release);
    measured_ack_latency_max_us_.store(
        ack_latency_max_us_.load(std::memory_order_acquire),
        std::memory_order_release);
    for (uint32_t i = 0; i < kLatencyBuckets; ++i) {
      measured_ack_latency_hist_[i].store(
          ack_latency_hist_[i].load(std::memory_order_acquire),
          std::memory_order_release);
    }
    if (isQueuedMode(durable_mode_)) {
      std::lock_guard<std::mutex> guard(async_commit_mutex_);
      measured_pending_commits_.store(pending_async_acks_.load(std::memory_order_acquire),
                                      std::memory_order_release);
    }
  }

  ~WalLogger() {
    shutdown();
    printStats();
  }

 private:
  static constexpr uint32_t kMaxWalWorkers = 256;
  static constexpr uint32_t kLatencyBuckets = 64;

  struct PendingCommit {
    uint64_t commit_lsn;
  };

  struct AsyncLogEntry {
    std::string payload;
    std::vector<uint64_t> lsns;
    WalFrontier dep_frontier;
    uint64_t commit_lsn = 0;
    uint64_t local_seq = 0;
    uint64_t enqueue_ns = 0;
  };

  struct AsyncQueue {
    std::mutex mutex;
    std::condition_variable cv;
    std::deque<AsyncLogEntry> queue;
    bool done = false;
  };

  struct DurableEvent {
    uint32_t logger_id = 0;
    uint64_t local_seq = 0;
    std::vector<uint64_t> lsns;
  };

  struct CommitEventQueue {
    std::mutex mutex;
    std::deque<DurableEvent> queue;
  };

  struct PendingAck {
    uint64_t commit_lsn = 0;
    uint64_t enqueue_ns = 0;
  };

  struct PendingAckGreater {
    bool operator()(const PendingAck& a, const PendingAck& b) const {
      return a.commit_lsn > b.commit_lsn;
    }
  };

  struct DepAckRequest {
    uint64_t enqueue_ns = 0;
    uint32_t remaining = 0;
  };

  struct ShardWaitEntry {
    uint64_t need = 0;
    std::shared_ptr<DepAckRequest> request;
  };

  struct ShardWaitEntryGreater {
    bool operator()(const ShardWaitEntry& a, const ShardWaitEntry& b) const {
      return a.need > b.need;
    }
  };

  struct Stats {
    std::atomic<uint64_t> commits{0};
    std::atomic<uint64_t> async_acked_commits{0};
    std::atomic<uint64_t> read_only_commits{0};
    std::atomic<uint64_t> payload_build_ns{0};
    std::atomic<uint64_t> lsn_alloc_ns{0};
    std::atomic<uint64_t> mutex_wait_ns{0};
    std::atomic<uint64_t> write_ns{0};
    std::atomic<uint64_t> fdatasync_ns{0};
    std::atomic<uint64_t> notify_wait_ns{0};
    std::atomic<uint64_t> committer_queue_wait_ns{0};
    std::atomic<uint64_t> worker_stall_ns{0};
    std::atomic<uint64_t> version_install_ns{0};
    std::atomic<uint64_t> frontier_collect_ns{0};
    std::atomic<uint64_t> frontier_merge_ns{0};
    std::atomic<uint64_t> frontier_publish_ns{0};
    std::atomic<uint64_t> wal_enqueue_ns{0};
    std::atomic<uint64_t> waitlist_registration_ns{0};
    std::atomic<uint64_t> committer_event_ns{0};
    std::atomic<uint64_t> ready_queue_push_ns{0};
    std::atomic<uint64_t> ack_process_ns{0};
    std::atomic<uint64_t> bytes{0};
    std::atomic<uint64_t> fdatasync_count{0};
    std::atomic<uint64_t> global_atomic_count{0};
    std::atomic<uint64_t> max_pending_commits{0};
    std::atomic<uint64_t> dep_frontier_bytes{0};
    std::atomic<uint64_t> dep_frontier_entries{0};
    std::atomic<uint64_t> dep_frontier_nonzero_entries{0};
    std::atomic<uint64_t> dep_wait_conditions{0};
    std::atomic<uint64_t> read_frontier_updates{0};
    std::atomic<uint64_t> write_frontier_updates{0};
    std::atomic<uint64_t> frontier_alloc_count{0};
    std::atomic<uint64_t> frontier_shared_ptr_count{0};
    std::atomic<uint64_t> waitlist_registrations{0};
    std::atomic<uint64_t> waitlist_pops{0};
    std::atomic<uint64_t> max_waitlist_len{0};
    std::atomic<uint64_t> waitlist_len_sum{0};
    std::atomic<uint64_t> waitlist_len_samples{0};
    std::atomic<uint64_t> max_ready_queue_len{0};
    std::atomic<uint64_t> ready_queue_len_sum{0};
    std::atomic<uint64_t> ready_queue_len_samples{0};
    std::atomic<uint64_t> flusher_batches{0};
    std::atomic<uint64_t> flushed_commits{0};
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
    const uint64_t acked = stats_.async_acked_commits.load(std::memory_order_acquire);
    const uint64_t read_only_commits =
        stats_.read_only_commits.load(std::memory_order_acquire);
    const uint64_t total_acked =
        isWorkerWaitMode(durable_mode_) ? commits : acked;
    const uint64_t payload = stats_.payload_build_ns.load(std::memory_order_acquire);
    const uint64_t lsn_alloc = stats_.lsn_alloc_ns.load(std::memory_order_acquire);
    const uint64_t mutex = stats_.mutex_wait_ns.load(std::memory_order_acquire);
    const uint64_t write = stats_.write_ns.load(std::memory_order_acquire);
    const uint64_t fsync = stats_.fdatasync_ns.load(std::memory_order_acquire);
    const uint64_t notify = stats_.notify_wait_ns.load(std::memory_order_acquire);
    const uint64_t committer_queue = stats_.committer_queue_wait_ns.load(std::memory_order_acquire);
    const uint64_t worker_stall = stats_.worker_stall_ns.load(std::memory_order_acquire);
    const uint64_t version_install = stats_.version_install_ns.load(std::memory_order_acquire);
    const uint64_t frontier_collect = stats_.frontier_collect_ns.load(std::memory_order_acquire);
    const uint64_t frontier_merge = stats_.frontier_merge_ns.load(std::memory_order_acquire);
    const uint64_t frontier_publish = stats_.frontier_publish_ns.load(std::memory_order_acquire);
    const uint64_t wal_enqueue = stats_.wal_enqueue_ns.load(std::memory_order_acquire);
    const uint64_t waitlist_registration =
        stats_.waitlist_registration_ns.load(std::memory_order_acquire);
    const uint64_t committer_event = stats_.committer_event_ns.load(std::memory_order_acquire);
    const uint64_t ready_queue_push = stats_.ready_queue_push_ns.load(std::memory_order_acquire);
    const uint64_t ack_process = stats_.ack_process_ns.load(std::memory_order_acquire);
    const uint64_t bytes = stats_.bytes.load(std::memory_order_acquire);
    const uint64_t fsync_count = stats_.fdatasync_count.load(std::memory_order_acquire);
    const uint64_t global_atomics = stats_.global_atomic_count.load(std::memory_order_acquire);
    const uint64_t max_pending = stats_.max_pending_commits.load(std::memory_order_acquire);
    const uint64_t dep_frontier_bytes = stats_.dep_frontier_bytes.load(std::memory_order_acquire);
    const uint64_t dep_frontier_entries = stats_.dep_frontier_entries.load(std::memory_order_acquire);
    const uint64_t dep_frontier_nonzero_entries =
        stats_.dep_frontier_nonzero_entries.load(std::memory_order_acquire);
    const uint64_t dep_wait_conditions = stats_.dep_wait_conditions.load(std::memory_order_acquire);
    const uint64_t read_frontier_updates =
        stats_.read_frontier_updates.load(std::memory_order_acquire);
    const uint64_t write_frontier_updates =
        stats_.write_frontier_updates.load(std::memory_order_acquire);
    const uint64_t frontier_alloc_count =
        stats_.frontier_alloc_count.load(std::memory_order_acquire);
    const uint64_t frontier_shared_ptr_count =
        stats_.frontier_shared_ptr_count.load(std::memory_order_acquire);
    const uint64_t waitlist_registrations =
        stats_.waitlist_registrations.load(std::memory_order_acquire);
    const uint64_t waitlist_pops = stats_.waitlist_pops.load(std::memory_order_acquire);
    const uint64_t max_waitlist_len = stats_.max_waitlist_len.load(std::memory_order_acquire);
    const uint64_t waitlist_len_sum = stats_.waitlist_len_sum.load(std::memory_order_acquire);
    const uint64_t waitlist_len_samples =
        stats_.waitlist_len_samples.load(std::memory_order_acquire);
    const uint64_t max_ready_queue_len =
        stats_.max_ready_queue_len.load(std::memory_order_acquire);
    const uint64_t ready_queue_len_sum =
        stats_.ready_queue_len_sum.load(std::memory_order_acquire);
    const uint64_t ready_queue_len_samples =
        stats_.ready_queue_len_samples.load(std::memory_order_acquire);
    const uint64_t flusher_batches = stats_.flusher_batches.load(std::memory_order_acquire);
    const uint64_t flushed_commits = stats_.flushed_commits.load(std::memory_order_acquire);
    const uint64_t ack_latency_samples =
        ack_latency_samples_.load(std::memory_order_acquire);
    const uint64_t total = payload + lsn_alloc + mutex + write + fsync + notify +
                           committer_queue + worker_stall + version_install +
                           frontier_collect + frontier_merge + frontier_publish +
                           wal_enqueue + waitlist_registration + committer_event +
                           ready_queue_push + ack_process;
    const bool has_measurement_stop =
        measurement_stop_recorded_.load(std::memory_order_acquire);
    const uint64_t measured_logical =
        has_measurement_stop ? measured_logical_commits_.load(std::memory_order_acquire) : commits;
    const uint64_t measured_async_acked =
        has_measurement_stop ? measured_async_acked_commits_.load(std::memory_order_acquire) : acked;
    const uint64_t measured_acked =
        isWorkerWaitMode(durable_mode_) ? measured_logical : measured_async_acked;
    const uint64_t measured_pending =
        has_measurement_stop ? measured_pending_commits_.load(std::memory_order_acquire) : 0;
    const uint64_t measured_durable_global_lsn =
        has_measurement_stop ? measured_durable_global_lsn_.load(std::memory_order_acquire)
                             : durable_prefix_lsn_.load(std::memory_order_acquire);
    std::cout << "wal_stats_durable_mode:\t" << durableModeName(durable_mode_) << std::endl;
    std::cout << "wal_stats_logger_num:\t" << logger_num_ << std::endl;
    std::cout << "wal_stats_committer_num:\t" << committer_num_ << std::endl;
    std::cout << "wal_stats_commits:	" << commits << std::endl;
    std::cout << "wal_stats_logical_commits:	" << commits << std::endl;
    std::cout << "wal_stats_acked_commits:	" << total_acked << std::endl;
    std::cout << "wal_stats_async_acked_commits:	" << acked << std::endl;
    std::cout << "wal_stats_read_only_commits:	" << read_only_commits << std::endl;
    std::cout << "wal_stats_logical_minus_acked:	"
              << (commits >= total_acked ? commits - total_acked : 0) << std::endl;
    std::cout << "wal_stats_measurement_stop_recorded:	" << (has_measurement_stop ? 1 : 0) << std::endl;
    std::cout << "wal_stats_measured_logical_commits:	" << measured_logical << std::endl;
    std::cout << "wal_stats_measured_acked_commits:	" << measured_acked << std::endl;
    std::cout << "wal_stats_measured_logical_minus_acked:	"
              << (measured_logical >= measured_acked ? measured_logical - measured_acked : 0)
              << std::endl;
    std::cout << "wal_stats_measurement_pending_commits:	" << measured_pending << std::endl;
    std::cout << "wal_stats_shutdown_drain_acked_commits:	"
              << (total_acked >= measured_acked ? total_acked - measured_acked : 0)
              << std::endl;
    std::cout << "wal_stats_bytes:	" << bytes << std::endl;
    std::cout << "wal_stats_payload_build_ns:	" << payload << std::endl;
    std::cout << "wal_stats_lsn_alloc_ns:	" << lsn_alloc << std::endl;
    std::cout << "wal_stats_mutex_wait_ns:	" << mutex << std::endl;
    std::cout << "wal_stats_write_ns:	" << write << std::endl;
    std::cout << "wal_stats_fdatasync_ns:	" << fsync << std::endl;
    std::cout << "wal_stats_fdatasync_count:	" << fsync_count << std::endl;
    std::cout << "wal_stats_notify_wait_ns:	" << notify << std::endl;
    std::cout << "wal_stats_committer_queue_wait_ns:	" << committer_queue << std::endl;
    std::cout << "wal_stats_worker_stall_ns:	" << worker_stall << std::endl;
    std::cout << "wal_stats_version_install_ns:	" << version_install << std::endl;
    std::cout << "wal_stats_frontier_collect_ns:	" << frontier_collect << std::endl;
    std::cout << "wal_stats_frontier_merge_ns:	" << frontier_merge << std::endl;
    std::cout << "wal_stats_frontier_publish_ns:	" << frontier_publish << std::endl;
    std::cout << "wal_stats_wal_enqueue_ns:	" << wal_enqueue << std::endl;
    std::cout << "wal_stats_waitlist_registration_ns:	" << waitlist_registration << std::endl;
    std::cout << "wal_stats_committer_event_ns:	" << committer_event << std::endl;
    std::cout << "wal_stats_ready_queue_push_ns:	" << ready_queue_push << std::endl;
    std::cout << "wal_stats_ack_process_ns:	" << ack_process << std::endl;
    std::cout << "wal_stats_global_atomic_count:	" << global_atomics << std::endl;
    std::cout << "wal_stats_max_pending_commits:	" << max_pending << std::endl;
    std::cout << "wal_stats_dep_frontier_bytes:	" << dep_frontier_bytes << std::endl;
    std::cout << "wal_stats_dep_frontier_entries:	" << dep_frontier_entries << std::endl;
    std::cout << "wal_stats_dep_frontier_nonzero_entries:	"
              << dep_frontier_nonzero_entries << std::endl;
    std::cout << "wal_stats_dep_wait_conditions:	" << dep_wait_conditions << std::endl;
    std::cout << "wal_stats_read_frontier_updates:	" << read_frontier_updates << std::endl;
    std::cout << "wal_stats_write_frontier_updates:	" << write_frontier_updates << std::endl;
    std::cout << "wal_stats_frontier_alloc_count:	" << frontier_alloc_count << std::endl;
    std::cout << "wal_stats_frontier_shared_ptr_count:	"
              << frontier_shared_ptr_count << std::endl;
    std::cout << "wal_stats_waitlist_registrations:	" << waitlist_registrations << std::endl;
    std::cout << "wal_stats_waitlist_pops:	" << waitlist_pops << std::endl;
    std::cout << "wal_stats_max_waitlist_len:	" << max_waitlist_len << std::endl;
    std::cout << "wal_stats_avg_waitlist_len:	"
              << (waitlist_len_samples ? waitlist_len_sum / waitlist_len_samples : 0)
              << std::endl;
    std::cout << "wal_stats_max_ready_queue_len:	" << max_ready_queue_len << std::endl;
    std::cout << "wal_stats_avg_ready_queue_len:	"
              << (ready_queue_len_samples ? ready_queue_len_sum / ready_queue_len_samples : 0)
              << std::endl;
    std::cout << "wal_stats_flusher_batches:	" << flusher_batches << std::endl;
    std::cout << "wal_stats_flushed_commits:	" << flushed_commits << std::endl;
    std::cout << "wal_stats_avg_batch_size:	"
              << (flusher_batches ? flushed_commits / flusher_batches : 0)
              << std::endl;
    std::cout << "wal_stats_ack_latency_samples:	" << ack_latency_samples << std::endl;
    std::cout << "wal_stats_ack_latency_p50_us:	" << ackLatencyPercentile(50) << std::endl;
    std::cout << "wal_stats_ack_latency_p90_us:	" << ackLatencyPercentile(90) << std::endl;
    std::cout << "wal_stats_ack_latency_p95_us:	" << ackLatencyPercentile(95) << std::endl;
    std::cout << "wal_stats_ack_latency_p99_us:	" << ackLatencyPercentile(99) << std::endl;
    std::cout << "wal_stats_ack_latency_p999_us:	" << ackLatencyPermille(999) << std::endl;
    std::cout << "wal_stats_ack_latency_max_us:	"
              << ack_latency_max_us_.load(std::memory_order_acquire) << std::endl;
    std::cout << "wal_stats_measured_ack_latency_p50_us:	"
              << measuredAckLatencyPercentile(50) << std::endl;
    std::cout << "wal_stats_measured_ack_latency_p90_us:	"
              << measuredAckLatencyPercentile(90) << std::endl;
    std::cout << "wal_stats_measured_ack_latency_p95_us:	"
              << measuredAckLatencyPercentile(95) << std::endl;
    std::cout << "wal_stats_measured_ack_latency_p99_us:	"
              << measuredAckLatencyPercentile(99) << std::endl;
    std::cout << "wal_stats_measured_ack_latency_p999_us:	"
              << measuredAckLatencyPermille(999) << std::endl;
    std::cout << "wal_stats_measured_ack_latency_max_us:	"
              << (has_measurement_stop
                      ? measured_ack_latency_max_us_.load(std::memory_order_acquire)
                      : ack_latency_max_us_.load(std::memory_order_acquire))
              << std::endl;
    std::cout << "wal_stats_durable_global_lsn:	"
              << durable_prefix_lsn_.load(std::memory_order_acquire) << std::endl;
    std::cout << "wal_stats_measured_durable_global_lsn:	"
              << measured_durable_global_lsn << std::endl;
    std::cout << "wal_stats_total_accounted_ns:	" << total << std::endl;
    std::cout << "wal_stats_avg_accounted_ns_per_commit:	" << (total / commits) << std::endl;
  }

  static const char* durableModeName(WalDurableMode mode) {
    switch (mode) {
      case WalDurableMode::Sync:
        return "sync";
      case WalDurableMode::GroupGlobalPrefix:
        return "group_global_prefix";
      case WalDurableMode::AsyncGlobalLsnPrefix:
        return "async_global_lsn_prefix";
      case WalDurableMode::AsyncDepFrontierLsn:
        return "async_dep_frontier_lsn";
      case WalDurableMode::AsyncDepFrontierCstamp:
        return "async_dep_frontier_cstamp";
      case WalDurableMode::AsyncDepFrontierCstampNoPublish:
        return "async_dep_frontier_cstamp_no_publish";
      case WalDurableMode::AsyncDepFrontierCstampZeroDep:
        return "async_dep_frontier_cstamp_zero_dep";
      case WalDurableMode::AsyncDepFrontierCstampPrealloc:
        return "async_dep_frontier_cstamp_prealloc";
    }
    return "unknown";
  }

  static WalDurableMode parseDurableMode() {
    const char* env = std::getenv("CCBENCH_WAL_DURABLE_MODE");
    if (!env) return WalDurableMode::Sync;
    const std::string value(env);
    if (value == "group_global_prefix" ||
        value == "pwal_group_global_prefix") {
      return WalDurableMode::GroupGlobalPrefix;
    }
    if (value == "async_global_lsn_prefix") return WalDurableMode::AsyncGlobalLsnPrefix;
    if (value == "async_dep_frontier_lsn") return WalDurableMode::AsyncDepFrontierLsn;
    if (value == "async_dep_frontier_cstamp") return WalDurableMode::AsyncDepFrontierCstamp;
    if (value == "async_dep_frontier_cstamp_no_publish") {
      return WalDurableMode::AsyncDepFrontierCstampNoPublish;
    }
    if (value == "async_dep_frontier_cstamp_zero_dep") {
      return WalDurableMode::AsyncDepFrontierCstampZeroDep;
    }
    if (value == "async_dep_frontier_cstamp_prealloc") {
      return WalDurableMode::AsyncDepFrontierCstampPrealloc;
    }
    if (value == "sync") return WalDurableMode::Sync;
    std::fprintf(stderr, "Unknown CCBENCH_WAL_DURABLE_MODE=%s\n", env);
    std::abort();
  }

  static bool usesDepAck(WalDurableMode mode) {
    return mode == WalDurableMode::AsyncDepFrontierLsn ||
           mode == WalDurableMode::AsyncDepFrontierCstamp ||
           mode == WalDurableMode::AsyncDepFrontierCstampNoPublish ||
           mode == WalDurableMode::AsyncDepFrontierCstampZeroDep ||
           mode == WalDurableMode::AsyncDepFrontierCstampPrealloc;
  }

  static bool usesCstampLogicalLsn(WalDurableMode mode) {
    return mode == WalDurableMode::AsyncDepFrontierCstamp ||
           mode == WalDurableMode::AsyncDepFrontierCstampNoPublish ||
           mode == WalDurableMode::AsyncDepFrontierCstampZeroDep ||
           mode == WalDurableMode::AsyncDepFrontierCstampPrealloc;
  }

  static bool frontierCollectRequested(WalDurableMode mode) {
    return mode == WalDurableMode::AsyncDepFrontierLsn ||
           mode == WalDurableMode::AsyncDepFrontierCstamp ||
           mode == WalDurableMode::AsyncDepFrontierCstampNoPublish ||
           mode == WalDurableMode::AsyncDepFrontierCstampPrealloc;
  }

  static bool frontierPublishRequested(WalDurableMode mode) {
    return mode == WalDurableMode::AsyncDepFrontierLsn ||
           mode == WalDurableMode::AsyncDepFrontierCstamp ||
           mode == WalDurableMode::AsyncDepFrontierCstampPrealloc;
  }

  static bool isQueuedMode(WalDurableMode mode) {
    return mode == WalDurableMode::GroupGlobalPrefix ||
           mode == WalDurableMode::AsyncGlobalLsnPrefix ||
           usesDepAck(mode);
  }

  static bool isWorkerWaitMode(WalDurableMode mode) {
    return mode == WalDurableMode::Sync ||
           mode == WalDurableMode::GroupGlobalPrefix;
  }

  static uint64_t envUint64(const char* name, uint64_t fallback) {
    const char* env = std::getenv(name);
    if (!env || !*env) return fallback;
    return std::strtoull(env, nullptr, 10);
  }

  static int envInt(const char* name, int fallback) {
    const char* env = std::getenv(name);
    if (!env || !*env) return fallback;
    return std::atoi(env);
  }

  static bool envBool(const char* name, bool fallback) {
    const char* env = std::getenv(name);
    if (!env || !*env) return fallback;
    return std::atoi(env) != 0;
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

  uint64_t allocateLsn() {
    const uint64_t start = nowNs();
    uint64_t lsn = next_lsn_.fetch_add(1, std::memory_order_acq_rel);
    stats_.lsn_alloc_ns.fetch_add(nowNs() - start, std::memory_order_relaxed);
    stats_.global_atomic_count.fetch_add(1, std::memory_order_relaxed);
    return lsn;
  }

  void recordAckLatency(uint64_t ns) {
    uint64_t us = (ns + 999) / 1000;
    updateMax(ack_latency_max_us_, us);
    uint32_t bucket = 0;
    while (us > 0 && bucket + 1 < kLatencyBuckets) {
      ++bucket;
      us >>= 1;
    }
    ack_latency_hist_[bucket].fetch_add(1, std::memory_order_relaxed);
    ack_latency_samples_.fetch_add(1, std::memory_order_relaxed);
  }

  uint64_t ackLatencyPercentile(uint32_t pct) const {
    return latencyPercentile(ack_latency_hist_, ack_latency_samples_, pct);
  }

  uint64_t measuredAckLatencyPercentile(uint32_t pct) const {
    if (!measurement_stop_recorded_.load(std::memory_order_acquire)) {
      return ackLatencyPercentile(pct);
    }
    return latencyPercentile(measured_ack_latency_hist_,
                             measured_ack_latency_samples_, pct);
  }

  uint64_t ackLatencyPermille(uint32_t permille) const {
    return latencyPermille(ack_latency_hist_, ack_latency_samples_, permille);
  }

  uint64_t measuredAckLatencyPermille(uint32_t permille) const {
    if (!measurement_stop_recorded_.load(std::memory_order_acquire)) {
      return ackLatencyPermille(permille);
    }
    return latencyPermille(measured_ack_latency_hist_,
                           measured_ack_latency_samples_, permille);
  }

  uint64_t latencyPercentile(
      const std::array<std::atomic<uint64_t>, kLatencyBuckets>& hist,
      const std::atomic<uint64_t>& sample_counter,
      uint32_t pct) const {
    const uint64_t samples = sample_counter.load(std::memory_order_acquire);
    if (samples == 0) return 0;
    const uint64_t target = (samples * pct + 99) / 100;
    uint64_t seen = 0;
    for (uint32_t i = 0; i < kLatencyBuckets; ++i) {
      seen += hist[i].load(std::memory_order_acquire);
      if (seen >= target) {
        if (i == 0) return 0;
        if (i >= 63) return UINT64_MAX;
        return 1ULL << (i - 1);
      }
    }
    return 0;
  }

  uint64_t latencyPermille(
      const std::array<std::atomic<uint64_t>, kLatencyBuckets>& hist,
      const std::atomic<uint64_t>& sample_counter,
      uint32_t permille) const {
    const uint64_t samples = sample_counter.load(std::memory_order_acquire);
    if (samples == 0) return 0;
    const uint64_t target = (samples * permille + 999) / 1000;
    uint64_t seen = 0;
    for (uint32_t i = 0; i < kLatencyBuckets; ++i) {
      seen += hist[i].load(std::memory_order_acquire);
      if (seen >= target) {
        if (i == 0) return 0;
        if (i >= 63) return UINT64_MAX;
        return 1ULL << (i - 1);
      }
    }
    return 0;
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
      const uint64_t lsn = allocateLsn();
      const uint64_t build_start = nowNs();
      const std::string line = makeRecordLine(lsn, thid, cstamp, we);
      stats_.payload_build_ns.fetch_add(nowNs() - build_start, std::memory_order_relaxed);
      const uint64_t write_start = nowNs();
      writeAll(shared_fd_, line);
      stats_.write_ns.fetch_add(nowNs() - write_start, std::memory_order_relaxed);
      bytes += line.size();
    }
    commit_lsn = allocateLsn();
    const uint64_t build_start = nowNs();
    const std::string line = makeCommitLine(commit_lsn, thid, cstamp);
    stats_.payload_build_ns.fetch_add(nowNs() - build_start, std::memory_order_relaxed);
    const uint64_t write_start = nowNs();
    writeAll(shared_fd_, line);
    stats_.write_ns.fetch_add(nowNs() - write_start, std::memory_order_relaxed);
    bytes += line.size();
    const uint64_t fsync_start = nowNs();
    if (!skip_fdatasync_) {
      fdatasync(shared_fd_);
      stats_.fdatasync_ns.fetch_add(nowNs() - fsync_start, std::memory_order_relaxed);
      stats_.fdatasync_count.fetch_add(1, std::memory_order_relaxed);
    }
    markDurableThrough(commit_lsn);
    stats_.bytes.fetch_add(bytes, std::memory_order_relaxed);
    stats_.commits.fetch_add(1, std::memory_order_relaxed);
    recordAckLatency(nowNs() - wait_start);
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
      const uint64_t lsn = allocateLsn();
      lsns.push_back(lsn);
      payload += makeRecordLine(lsn, thid, cstamp, we);
    }
    const uint64_t commit_lsn = allocateLsn();
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
      if (!skip_fdatasync_) {
        fdatasync(worker_fds_[thid]);
        stats_.fdatasync_ns.fetch_add(nowNs() - fsync_start, std::memory_order_relaxed);
        stats_.fdatasync_count.fetch_add(1, std::memory_order_relaxed);
      }
      for (uint64_t lsn : lsns) markDurable(lsn);
    }

    const uint64_t notify_start = nowNs();
    waitForCommit(thid, commit_lsn);
    stats_.notify_wait_ns.fetch_add(nowNs() - notify_start, std::memory_order_relaxed);
    stats_.bytes.fetch_add(payload.size(), std::memory_order_relaxed);
    stats_.commits.fetch_add(1, std::memory_order_relaxed);
    recordAckLatency(nowNs() - build_start);
    return commit_lsn;
  }

  uint64_t allocateLocalSeq(uint32_t thid) {
    return next_local_seq_[thid].fetch_add(1, std::memory_order_acq_rel) + 1;
  }

  uint32_t chooseLogId(uint32_t thid) const {
    const uint32_t n = logger_num_ ? logger_num_ : kMaxWalWorkers;
    return thid % n;
  }

  template <class WriteSet>
  WalCommitResult logPerThreadQueued(uint32_t thid, uint32_t cstamp,
                                     const WriteSet& write_set,
                                     const WalFrontier* dep_frontier,
                                     bool worker_wait) {
    const uint32_t log_id = chooseLogId(thid);
    ensureWorker(log_id);
    const bool dep_mode = usesDepAck(durable_mode_);
    const bool cstamp_mode = usesCstampLogicalLsn(durable_mode_);
    const bool zero_dep_mode =
        durable_mode_ == WalDurableMode::AsyncDepFrontierCstampZeroDep;
    const bool prealloc_frontier_mode =
        durable_mode_ == WalDurableMode::AsyncDepFrontierCstampPrealloc;
    if (!worker_wait) {
      throttleAsyncPending();
    }
    const uint64_t local_seq = allocateLocalSeq(log_id);
    std::vector<uint64_t> lsns;
    lsns.reserve(write_set.size() + 1);
    std::string payload;
    const uint64_t build_start = nowNs();
    uint64_t local_record_no = 0;
    for (const auto& we : write_set) {
      const uint64_t lsn = cstamp_mode
          ? (local_seq * 1000000ULL + (++local_record_no))
          : allocateLsn();
      if (!cstamp_mode) lsns.push_back(lsn);
      payload += makeRecordLine(lsn, thid, cstamp, we);
    }
    const uint64_t commit_lsn = cstamp_mode ? cstamp : allocateLsn();
    if (!cstamp_mode) lsns.push_back(commit_lsn);
    payload += makeCommitLine(commit_lsn, thid, cstamp);
    stats_.payload_build_ns.fetch_add(nowNs() - build_start, std::memory_order_relaxed);

    WalFrontier dep;
    const WalFrontier* dep_for_ack = &dep;
    if (dep_mode && dep_frontier && !zero_dep_mode) {
      if (prealloc_frontier_mode) {
        thread_local WalFrontier tls_dep;
        tls_dep.reset(shardCount());
        tls_dep.merge(*dep_frontier, shardCount());
        dep_for_ack = &tls_dep;
      } else {
        dep = *dep_frontier;
        dep.ensureSize(shardCount());
      }
      stats_.dep_frontier_bytes.fetch_add(dep_for_ack->sizeBytes(shardCount()),
                                          std::memory_order_relaxed);
      stats_.dep_frontier_entries.fetch_add(dep_for_ack->entries(shardCount()),
                                            std::memory_order_relaxed);
      stats_.dep_frontier_nonzero_entries.fetch_add(
          dep_for_ack->nonzeroEntries(shardCount()),
          std::memory_order_relaxed);
    }
    uint64_t enqueue_ns = 0;
    if (!worker_wait) {
      enqueue_ns = nowNs();
      if (dep_mode) {
        registerDepAck(log_id, local_seq, *dep_for_ack, enqueue_ns);
      } else {
        registerAsyncAck(commit_lsn, enqueue_ns);
      }
    } else {
      enqueue_ns = nowNs();
    }
    {
      const uint64_t enqueue_start = nowNs();
      AsyncQueue& q = *async_queues_[log_id];
      std::lock_guard<std::mutex> guard(q.mutex);
      q.queue.push_back(AsyncLogEntry{std::move(payload), std::move(lsns), dep,
                                      commit_lsn, local_seq, enqueue_ns});
      stats_.wal_enqueue_ns.fetch_add(nowNs() - enqueue_start,
                                      std::memory_order_relaxed);
    }
    async_queues_[log_id]->cv.notify_one();
    if (worker_wait) {
      const uint64_t notify_start = nowNs();
      waitForCommit(log_id, commit_lsn);
      stats_.notify_wait_ns.fetch_add(nowNs() - notify_start, std::memory_order_relaxed);
      recordAckLatency(nowNs() - enqueue_ns);
    }
    stats_.commits.fetch_add(1, std::memory_order_relaxed);
    return WalCommitResult{commit_lsn, log_id, local_seq};
  }

  void ensureWorker(uint32_t thid) {
    if (thid >= kMaxWalWorkers) {
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
    if (isQueuedMode(durable_mode_) && !async_queues_[thid]) {
      async_queues_[thid] = std::make_unique<AsyncQueue>();
      async_flusher_threads_[thid] =
          std::thread(&WalLogger::asyncFlusherLoop, this, thid);
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

  void registerAsyncAck(uint64_t commit_lsn, uint64_t enqueue_ns) {
    const uint64_t start = nowNs();
    std::lock_guard<std::mutex> guard(async_commit_mutex_);
    global_ack_waitlist_.push(PendingAck{commit_lsn, enqueue_ns});
    stats_.waitlist_registrations.fetch_add(1, std::memory_order_relaxed);
    pending_async_acks_.fetch_add(1, std::memory_order_relaxed);
    updateMax(stats_.max_pending_commits,
              pending_async_acks_.load(std::memory_order_relaxed));
    sampleWaitlistLenLocked();
    drainAsyncAcksLocked();
    stats_.waitlist_registration_ns.fetch_add(nowNs() - start,
                                              std::memory_order_relaxed);
  }

  void throttleAsyncPending() {
    if (max_pending_async_ == 0) return;
    const uint64_t wait_start = nowNs();
    bool waited = false;
    while (pending_async_acks_.load(std::memory_order_acquire) >= max_pending_async_) {
      waited = true;
      std::this_thread::yield();
    }
    if (waited) {
      const uint64_t waited_ns = nowNs() - wait_start;
      stats_.notify_wait_ns.fetch_add(waited_ns, std::memory_order_relaxed);
      stats_.worker_stall_ns.fetch_add(waited_ns, std::memory_order_relaxed);
    }
  }

  void registerDepAck(uint32_t log_id, uint64_t local_seq,
                      const WalFrontier& dep, uint64_t enqueue_ns,
                      bool include_self = true) {
    const uint64_t start = nowNs();
    std::lock_guard<std::mutex> guard(async_commit_mutex_);
    auto request = std::make_shared<DepAckRequest>();
    request->enqueue_ns = enqueue_ns;
    const uint32_t n = shardCount();
    for (uint32_t i = 0; i < n; ++i) {
      uint64_t need = dep.get(i);
      if (include_self && i == log_id) need = std::max<uint64_t>(need, local_seq);
      if (need == 0) continue;
      if (durable_local_seq_[i].load(std::memory_order_acquire) >= need) continue;
      ++request->remaining;
      dep_waitlists_[i].push(ShardWaitEntry{need, request});
      stats_.dep_wait_conditions.fetch_add(1, std::memory_order_relaxed);
      stats_.waitlist_registrations.fetch_add(1, std::memory_order_relaxed);
    }
    pending_async_acks_.fetch_add(1, std::memory_order_relaxed);
    updateMax(stats_.max_pending_commits,
              pending_async_acks_.load(std::memory_order_relaxed));
    sampleWaitlistLenLocked();
    if (request->remaining == 0) {
      ackAsyncRequestLocked(request->enqueue_ns);
    }
    stats_.waitlist_registration_ns.fetch_add(nowNs() - start,
                                              std::memory_order_relaxed);
  }

  void markDurableAsync(uint32_t logger_id, uint64_t local_seq,
                        const std::vector<uint64_t>& lsns) {
    std::lock_guard<std::mutex> guard(async_commit_mutex_);
    for (uint64_t lsn : lsns) {
      if (lsn >= completed_lsn_.size()) completed_lsn_.resize(lsn + 1024, 0);
      completed_lsn_[lsn] = 1;
    }
    uint64_t prefix = durable_prefix_lsn_.load(std::memory_order_acquire);
    while (prefix + 1 < completed_lsn_.size() && completed_lsn_[prefix + 1]) {
      ++prefix;
    }
    durable_prefix_lsn_.store(prefix, std::memory_order_release);
    durable_local_seq_[logger_id].store(local_seq, std::memory_order_release);
    drainAsyncAcksLocked();
    drainDepAcksLocked(logger_id, local_seq);
  }

  void drainAsyncAcksLocked() {
    const uint64_t durable_prefix = durable_prefix_lsn_.load(std::memory_order_acquire);
    while (!global_ack_waitlist_.empty() &&
           global_ack_waitlist_.top().commit_lsn <= durable_prefix) {
      const PendingAck ack = global_ack_waitlist_.top();
      global_ack_waitlist_.pop();
      stats_.waitlist_pops.fetch_add(1, std::memory_order_relaxed);
      ackAsyncRequestLocked(ack.enqueue_ns);
    }
    sampleWaitlistLenLocked();
  }

  void drainDepAcksLocked(uint32_t logger_id, uint64_t durable) {
    auto& waitlist = dep_waitlists_[logger_id];
    while (!waitlist.empty() && waitlist.top().need <= durable) {
      auto request = waitlist.top().request;
      waitlist.pop();
      stats_.waitlist_pops.fetch_add(1, std::memory_order_relaxed);
      if (request->remaining == 0) continue;
      --request->remaining;
      if (request->remaining == 0) {
        ackAsyncRequestLocked(request->enqueue_ns);
      }
    }
    sampleWaitlistLenLocked();
  }

  void ackAsyncRequestLocked(uint64_t enqueue_ns) {
    const uint64_t start = nowNs();
    const uint64_t now = nowNs();
    recordAckLatency(now - enqueue_ns);
    stats_.committer_queue_wait_ns.fetch_add(now - enqueue_ns,
                                             std::memory_order_relaxed);
    stats_.async_acked_commits.fetch_add(1, std::memory_order_relaxed);
    pending_async_acks_.fetch_sub(1, std::memory_order_relaxed);
    stats_.ack_process_ns.fetch_add(nowNs() - start, std::memory_order_relaxed);
  }

  void enqueueDurableEvent(uint32_t logger_id, uint64_t local_seq,
                           std::vector<uint64_t>&& lsns) {
    const uint64_t start = nowNs();
    if (!commit_event_queues_[logger_id]) {
      std::lock_guard<std::mutex> guard(init_mutex_);
      if (!commit_event_queues_[logger_id]) {
        commit_event_queues_[logger_id] = std::make_unique<CommitEventQueue>();
      }
    }
    {
      std::lock_guard<std::mutex> guard(commit_event_queues_[logger_id]->mutex);
      commit_event_queues_[logger_id]->queue.push_back(
          DurableEvent{logger_id, local_seq, std::move(lsns)});
      const uint64_t len = commit_event_queues_[logger_id]->queue.size();
      stats_.ready_queue_len_sum.fetch_add(len, std::memory_order_relaxed);
      stats_.ready_queue_len_samples.fetch_add(1, std::memory_order_relaxed);
      updateMax(stats_.max_ready_queue_len, len);
    }
    stats_.ready_queue_push_ns.fetch_add(nowNs() - start,
                                         std::memory_order_relaxed);
    committer_cv_.notify_all();
  }

  bool popDurableEventForCommitter(uint32_t committer_id, DurableEvent& event) {
    const uint32_t n = shardCount();
    const uint32_t step = std::max<uint32_t>(1, committer_num_);
    for (uint32_t logger_id = committer_id; logger_id < n; logger_id += step) {
      if (!commit_event_queues_[logger_id]) continue;
      std::lock_guard<std::mutex> guard(commit_event_queues_[logger_id]->mutex);
      if (commit_event_queues_[logger_id]->queue.empty()) continue;
      event = std::move(commit_event_queues_[logger_id]->queue.front());
      commit_event_queues_[logger_id]->queue.pop_front();
      const uint64_t len = commit_event_queues_[logger_id]->queue.size();
      stats_.ready_queue_len_sum.fetch_add(len, std::memory_order_relaxed);
      stats_.ready_queue_len_samples.fetch_add(1, std::memory_order_relaxed);
      return true;
    }
    return false;
  }

  bool hasDurableEventForCommitter(uint32_t committer_id) {
    const uint32_t n = shardCount();
    const uint32_t step = std::max<uint32_t>(1, committer_num_);
    for (uint32_t logger_id = committer_id; logger_id < n; logger_id += step) {
      if (!commit_event_queues_[logger_id]) continue;
      std::lock_guard<std::mutex> guard(commit_event_queues_[logger_id]->mutex);
      if (!commit_event_queues_[logger_id]->queue.empty()) return true;
    }
    return false;
  }

  void committerLoop(uint32_t committer_id) {
#ifdef Linux
    setThreadAffinity(static_cast<int>(thread_num_ + logger_num_ + committer_id));
#endif
    for (;;) {
      bool did_work = false;
      DurableEvent event;
      while (popDurableEventForCommitter(committer_id, event)) {
        const uint64_t event_start = nowNs();
        markDurableAsync(event.logger_id, event.local_seq, event.lsns);
        stats_.committer_event_ns.fetch_add(nowNs() - event_start,
                                            std::memory_order_relaxed);
        did_work = true;
      }
      if (did_work) continue;
      if (committers_done_.load(std::memory_order_acquire) &&
          !hasDurableEventForCommitter(committer_id)) {
        break;
      }
      std::unique_lock<std::mutex> lock(committer_cv_mutex_);
      committer_cv_.wait_for(lock, std::chrono::microseconds(100), [&] {
        return committers_done_.load(std::memory_order_acquire) ||
               hasDurableEventForCommitter(committer_id);
      });
    }
  }

  void startCommittersLocked() {
    if (!isQueuedMode(durable_mode_)) return;
    committers_done_.store(false, std::memory_order_release);
    for (uint32_t i = 0; i < committer_num_; ++i) {
      if (!committer_threads_[i].joinable()) {
        committer_threads_[i] = std::thread(&WalLogger::committerLoop, this, i);
      }
    }
  }

  void asyncFlusherLoop(uint32_t thid) {
#ifdef Linux
    setThreadAffinity(static_cast<int>(thread_num_ + thid));
#endif
    for (;;) {
      std::vector<AsyncLogEntry> batch;
      {
        AsyncQueue& q = *async_queues_[thid];
        std::unique_lock<std::mutex> lock(q.mutex);
        q.cv.wait_for(lock, std::chrono::microseconds(async_flush_us_), [&] {
          return q.done || q.queue.size() >= async_group_size_;
        });
        if (q.queue.empty()) {
          if (q.done) break;
          continue;
        }
        const uint64_t limit = std::max<uint64_t>(1, async_group_size_);
        while (!q.queue.empty() && batch.size() < limit) {
          batch.push_back(std::move(q.queue.front()));
          q.queue.pop_front();
        }
      }

      std::vector<uint64_t> durable_lsns;
      durable_lsns.reserve(batch.size() * 2);
      uint64_t bytes = 0;
      uint64_t durable_local_seq = 0;
      stats_.flusher_batches.fetch_add(1, std::memory_order_relaxed);
      stats_.flushed_commits.fetch_add(batch.size(), std::memory_order_relaxed);
      const uint64_t write_start = nowNs();
      for (const AsyncLogEntry& entry : batch) {
        writeAll(worker_fds_[thid], entry.payload);
        bytes += entry.payload.size();
        durable_lsns.insert(durable_lsns.end(), entry.lsns.begin(), entry.lsns.end());
        durable_local_seq = std::max(durable_local_seq, entry.local_seq);
      }
      stats_.write_ns.fetch_add(nowNs() - write_start, std::memory_order_relaxed);
      stats_.bytes.fetch_add(bytes, std::memory_order_relaxed);

      if (straggler_logger_ == static_cast<int>(thid) && straggler_sleep_us_ > 0) {
        std::this_thread::sleep_for(std::chrono::microseconds(straggler_sleep_us_));
      }

      const uint64_t fsync_start = nowNs();
      if (!skip_fdatasync_) {
        fdatasync(worker_fds_[thid]);
        stats_.fdatasync_ns.fetch_add(nowNs() - fsync_start, std::memory_order_relaxed);
        stats_.fdatasync_count.fetch_add(1, std::memory_order_relaxed);
      }
      enqueueDurableEvent(thid, durable_local_seq, std::move(durable_lsns));
    }
  }

  void stopAsyncFlushersLocked() {
    if (!isQueuedMode(durable_mode_)) return;
    for (uint32_t i = 0; i < kMaxWalWorkers; ++i) {
      if (!async_queues_[i]) continue;
      {
        std::lock_guard<std::mutex> guard(async_queues_[i]->mutex);
        async_queues_[i]->done = true;
      }
      async_queues_[i]->cv.notify_one();
    }
    for (uint32_t i = 0; i < kMaxWalWorkers; ++i) {
      if (async_flusher_threads_[i].joinable()) {
        async_flusher_threads_[i].join();
      }
    }
    committers_done_.store(true, std::memory_order_release);
    committer_cv_.notify_all();
    for (uint32_t i = 0; i < kMaxWalWorkers; ++i) {
      if (committer_threads_[i].joinable()) {
        committer_threads_[i].join();
      }
    }
    {
      std::lock_guard<std::mutex> guard(async_commit_mutex_);
      drainAsyncAcksLocked();
      for (uint32_t i = 0; i < shardCount(); ++i) {
        drainDepAcksLocked(i, durable_local_seq_[i].load(std::memory_order_acquire));
      }
    }
  }

  void sampleWaitlistLenLocked() {
    uint64_t len = global_ack_waitlist_.size();
    const uint32_t n = shardCount();
    for (uint32_t i = 0; i < n; ++i) {
      len += dep_waitlists_[i].size();
    }
    stats_.waitlist_len_sum.fetch_add(len, std::memory_order_relaxed);
    stats_.waitlist_len_samples.fetch_add(1, std::memory_order_relaxed);
    updateMax(stats_.max_waitlist_len, len);
  }

  static void updateMax(std::atomic<uint64_t>& target, uint64_t value) {
    uint64_t old = target.load(std::memory_order_relaxed);
    while (old < value &&
           !target.compare_exchange_weak(old, value, std::memory_order_relaxed)) {
    }
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
  WalDurableMode durable_mode_ = WalDurableMode::Sync;
  uint32_t thread_num_ = 0;
  uint32_t logger_num_ = 0;
  std::string protocol_name_;
  std::string base_dir_;
  uint64_t async_group_size_ = 8;
  uint64_t async_flush_us_ = 100;
  uint64_t max_pending_async_ = 65536;
  uint32_t committer_num_ = 1;
  bool skip_fdatasync_ = false;
  bool skip_read_only_ = false;
  int straggler_logger_ = -1;
  uint64_t straggler_sleep_us_ = 0;

  std::atomic<uint64_t> next_lsn_{1};
  std::atomic<uint64_t> durable_prefix_lsn_{0};
  std::array<std::atomic<uint64_t>, kMaxWalWorkers> next_local_seq_{};
  std::array<std::atomic<uint64_t>, kMaxWalWorkers> durable_local_seq_{};
  std::atomic<bool> measurement_stop_recorded_{false};
  std::atomic<uint64_t> measured_logical_commits_{0};
  std::atomic<uint64_t> measured_async_acked_commits_{0};
  std::atomic<uint64_t> measured_pending_commits_{0};
  std::atomic<uint64_t> measured_durable_global_lsn_{0};

  int shared_fd_ = -1;
  std::mutex shared_mutex_;

  std::vector<int> worker_fds_;
  std::vector<std::string> buffers_;
  std::vector<std::deque<PendingCommit>> commit_queues_;
  std::mutex worker_mutexes_[kMaxWalWorkers];

  std::array<std::unique_ptr<AsyncQueue>, kMaxWalWorkers> async_queues_;
  std::array<std::thread, kMaxWalWorkers> async_flusher_threads_;
  std::array<std::unique_ptr<CommitEventQueue>, kMaxWalWorkers> commit_event_queues_;
  std::array<std::thread, kMaxWalWorkers> committer_threads_;
  std::atomic<bool> committers_done_{false};
  std::mutex committer_cv_mutex_;
  std::condition_variable committer_cv_;
  std::mutex async_commit_mutex_;
  std::atomic<uint64_t> pending_async_acks_{0};
  std::vector<uint8_t> completed_lsn_{0};
  std::priority_queue<PendingAck, std::vector<PendingAck>, PendingAckGreater>
      global_ack_waitlist_;
  std::array<std::priority_queue<ShardWaitEntry, std::vector<ShardWaitEntry>,
                                 ShardWaitEntryGreater>, kMaxWalWorkers>
      dep_waitlists_;

  std::mutex durable_mutex_;
  std::vector<bool> durable_{false};
  std::array<std::atomic<uint64_t>, kLatencyBuckets> ack_latency_hist_{};
  std::atomic<uint64_t> ack_latency_samples_{0};
  std::atomic<uint64_t> ack_latency_max_us_{0};
  std::array<std::atomic<uint64_t>, kLatencyBuckets> measured_ack_latency_hist_{};
  std::atomic<uint64_t> measured_ack_latency_samples_{0};
  std::atomic<uint64_t> measured_ack_latency_max_us_{0};
  Stats stats_;
};

}  // namespace ccbench
