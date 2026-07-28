#pragma once
// Ayame ロギング層 — 3役割分離版 (worker / flusher / committer)。
// - worker: 実行とログ追記・コミットエントリpushのみ(I/Oも通知判定もしない)
// - flusher: 担当ワーカのバッファをNコミットごとにflush(write+fdatasync)
// - committer: 全ワーカのキューを巡回し、
//     自ワーカflushedLSN >= commitLSN かつ 各依存先の confirmedLSN >= 依存LSN
//   を満たす先頭から通知を確定する(依存先の「通知済み」待ち。推移閉包を帰納的に保証)
// バッファとキューはper-worker mutexで保護(flusherはswapのみロック内、I/Oはロック外)。
//
// 注意(予約窓): ayameはcstamp採番時にLSNブロックを予約し、SSN検証後にappendする。
// この間にflusherが空バッファのアイドル前進で予約済み未appendのLSNを飛び越すと
// 不健全になるため、予約(reserveBlock)・ENDのappend・アイドル前進を同一の
// buf_mtx_ で相互排他し、reserved_base_ でガードする。

#include <errno.h>
#include <fcntl.h>
#include <unistd.h>

#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <deque>
#include <mutex>
#include <stdexcept>
#include <string>
#include <vector>

namespace pwal {

enum class LogType : uint64_t {
  Update = 1,
  End = 2,  // コミットログ
};

// ファイル上も同じ32バイト固定レイアウトで書く(パディングなし)。
struct LogRecord {
  uint64_t lsn;
  uint64_t type;
  uint64_t cstamp;
  uint64_t key;
};
static_assert(sizeof(LogRecord) == 32, "LogRecord must be 32 bytes");

// Ayame: cstampとWAL LSNを兼ねる統合カウンタ(グローバルカウンタはこれ1本)。
class LsnCounter {
 public:
  uint64_t reserve(uint64_t n) {
    return counter_.fetch_add(n, std::memory_order_relaxed);
  }
  uint64_t next() { return reserve(1); }
  uint64_t currentMax() const {
    return counter_.load(std::memory_order_relaxed) - 1;
  }

 private:
  alignas(CACHE_LINE_SIZE) std::atomic<uint64_t> counter_{1};
};

// 依存先: ワーカ thid のコミットログLSN lsn を持つtxの通知済みを待つ。
struct Dep {
  uint8_t thid;
  uint64_t lsn;
};

struct CommitEntry {
  uint64_t cstamp;
  uint64_t commit_lsn;
  std::chrono::steady_clock::time_point start;
  std::chrono::steady_clock::time_point t1;
  std::chrono::steady_clock::time_point t2{};  // committerによる近似スタンプ
  std::vector<Dep> deps;
};

class Worker {
 public:
  Worker(const std::string& wal_path, LsnCounter& lsn_counter)
      : lsn_counter_(lsn_counter) {
    fd_ = ::open(wal_path.c_str(), O_CREAT | O_WRONLY | O_TRUNC, 0644);
    if (fd_ < 0) throw std::runtime_error("pwal: open failed: " + wal_path);
    latencies_ns_.reserve(1 << 20);
    ro_latencies_ns_.reserve(1 << 20);
  }
  ~Worker() {
    if (fd_ >= 0) ::close(fd_);
  }
  Worker(const Worker&) = delete;
  Worker& operator=(const Worker&) = delete;

  // ---- worker専用 ----

  // LSNブロックの一括予約(cstamp採番)。アイドル前進との競合をbuf_mtx_で排他し、
  // 予約済み未appendの範囲をreserved_base_でガードする。
  uint64_t reserveBlock(uint64_t n) {
    std::lock_guard<std::mutex> g(buf_mtx_);
    uint64_t base = lsn_counter_.reserve(n);
    reserved_base_ = base;
    return base;
  }

  // abort時: 予約を解放する(予約LSNは欠番になる。欠番は安全: 本文2.1節)
  void clearReservation() {
    std::lock_guard<std::mutex> g(buf_mtx_);
    reserved_base_ = 0;
  }

  // 予約済みLSNのレコードを追記する。ENDの追記で予約ガードを解除する
  // (以降のレコードはすべてバッファ内にあり、アイドル前進は発生しない)。
  void append(uint64_t lsn, LogType type, uint64_t cstamp, uint64_t key) {
    LogRecord rec;
    rec.lsn = lsn;
    rec.type = static_cast<uint64_t>(type);
    rec.cstamp = cstamp;
    rec.key = key;
    std::lock_guard<std::mutex> g(buf_mtx_);
    buffer_.push_back(rec);
    if (type == LogType::End) {
      reserved_base_ = 0;
      // pendingはENDがバッファに入った時点で加算する(underflow防止)
      pending_ntx_.fetch_add(1, std::memory_order_relaxed);
    }
  }

  void pushCommit(uint64_t cstamp, uint64_t commit_lsn,
                  std::chrono::steady_clock::time_point start,
                  std::vector<Dep>&& deps) {
    std::lock_guard<std::mutex> g(q_mtx_);
    commit_queue_.push_back({cstamp, commit_lsn, start,
                             std::chrono::steady_clock::now(), {},
                             std::move(deps)});
    inflight_.fetch_add(1, std::memory_order_relaxed);
  }

  void notifyDirectlyRo(std::chrono::steady_clock::time_point start) {
    ro_latencies_ns_.push_back(static_cast<uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now() - start)
            .count()));
    ro_notified_++;
  }

  // ---- flusher専用 (このワーカの担当flusherは1人) ----

  void flushPending(uint64_t min_commits) {
    std::vector<LogRecord> batch;
    {
      std::lock_guard<std::mutex> g(buf_mtx_);
      if (!buffer_.empty() &&
          pending_ntx_.load(std::memory_order_relaxed) < min_commits) {
        return;
      }
      if (buffer_.empty()) {
        // アイドル前進: 予約済み未appendのLSNは跨いではならない
        uint64_t limit = reserved_base_ != 0 ? reserved_base_ - 1
                                             : lsn_counter_.currentMax();
        if (limit > flushed_lsn_.load(std::memory_order_relaxed)) {
          flushed_lsn_.store(limit, std::memory_order_release);
        }
        return;
      }
      batch.swap(buffer_);
    }
    writeAll(reinterpret_cast<const char*>(batch.data()),
             batch.size() * sizeof(LogRecord));
    auto sync_begin = std::chrono::steady_clock::now();
    if (::fdatasync(fd_) != 0) throw std::runtime_error("pwal: fdatasync failed");
    auto sync_end = std::chrono::steady_clock::now();
    sync_ns_ += static_cast<uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(sync_end -
                                                             sync_begin)
            .count());
    sync_count_++;
    flushed_lsn_.store(batch.back().lsn, std::memory_order_release);
    uint64_t ends = 0;
    for (const auto& r : batch) {
      if (r.type == static_cast<uint64_t>(LogType::End)) ends++;
    }
    pending_ntx_.fetch_sub(ends, std::memory_order_relaxed);
  }

  uint64_t flushedLsn() const {
    return flushed_lsn_.load(std::memory_order_acquire);
  }

  // ---- committer専用 (1人) ----

  uint64_t confirmedLsn() const {
    return confirmed_lsn_.load(std::memory_order_acquire);
  }

  // Ayameの通知条件で先頭から確定する。t2は自flushedLSNが跨いだ接頭辞に近似記録。
  size_t confirmAyame(const std::vector<Worker*>& workers,
                      std::chrono::steady_clock::time_point now) {
    std::lock_guard<std::mutex> g(q_mtx_);
    uint64_t own_flushed = flushed_lsn_.load(std::memory_order_acquire);
    for (size_t i = t2_stamped_; i < commit_queue_.size(); i++) {
      if (commit_queue_[i].commit_lsn > own_flushed) break;
      commit_queue_[i].t2 = now;
      t2_stamped_ = i + 1;
    }
    size_t n = 0;
    uint64_t last_confirmed = 0;
    while (!commit_queue_.empty()) {
      const CommitEntry& e = commit_queue_.front();
      if (e.commit_lsn > own_flushed) break;
      bool deps_ok = true;
      for (const Dep& d : e.deps) {
        if (workers[d.thid]->confirmedLsn() < d.lsn) {
          deps_ok = false;
          break;
        }
      }
      if (!deps_ok) break;
      recordConfirmed(e, now);
      last_confirmed = e.commit_lsn;
      commit_queue_.pop_front();
      inflight_.fetch_sub(1, std::memory_order_relaxed);
      if (t2_stamped_ > 0) t2_stamped_--;
      n++;
    }
    if (last_confirmed != 0) {
      confirmed_lsn_.store(last_confirmed, std::memory_order_release);
    }
    return n;
  }

  // バックプレッシャー用: 未確定の書き込みtx数
  uint64_t inflight() const { return inflight_.load(std::memory_order_relaxed); }

  bool queueEmpty() {
    std::lock_guard<std::mutex> g(q_mtx_);
    return commit_queue_.empty();
  }

  void snapshotMeasured() { measured_lat_ = latencies_ns_.size(); }

  // ---- 集計用 (全スレッドjoin後にmainが読む) ----
  uint64_t notified() const { return notified_ + ro_notified_; }
  size_t measuredLat() const { return measured_lat_; }
  const std::vector<uint64_t>& latenciesNs() const { return latencies_ns_; }
  const std::vector<uint64_t>& roLatenciesNs() const { return ro_latencies_ns_; }
  uint64_t phaseCount() const { return phase_count_; }
  uint64_t phaseExecNs() const { return exec_ns_; }
  uint64_t phaseFlushWaitNs() const { return flushw_ns_; }
  uint64_t phaseDepWaitNs() const { return depw_ns_; }
  uint64_t syncNs() const { return sync_ns_; }
  uint64_t syncCount() const { return sync_count_; }

 private:
  void recordConfirmed(const CommitEntry& e,
                       std::chrono::steady_clock::time_point now) {
    auto ns = [](auto a, auto b) {
      return static_cast<uint64_t>(
          std::chrono::duration_cast<std::chrono::nanoseconds>(b - a).count());
    };
    latencies_ns_.push_back(ns(e.start, now));
    exec_ns_ += ns(e.start, e.t1);
    if (e.t2.time_since_epoch().count() != 0) {
      flushw_ns_ += ns(e.t1, e.t2);
      depw_ns_ += ns(e.t2, now);
    }
    phase_count_++;
    notified_++;
  }

  void writeAll(const char* data, size_t size) {
    size_t done = 0;
    while (done < size) {
      ssize_t n = ::write(fd_, data + done, size - done);
      if (n < 0) throw std::runtime_error("pwal: write failed");
      done += static_cast<size_t>(n);
    }
  }

  LsnCounter& lsn_counter_;
  int fd_;

  std::mutex buf_mtx_;
  std::vector<LogRecord> buffer_;
  uint64_t reserved_base_ = 0;  // 予約済み未appendブロックの先頭(0=なし)
  std::atomic<uint64_t> pending_ntx_{0};

  std::mutex q_mtx_;
  std::deque<CommitEntry> commit_queue_;
  std::atomic<uint64_t> inflight_{0};
  size_t t2_stamped_ = 0;

  std::vector<uint64_t> latencies_ns_;
  size_t measured_lat_ = 0;
  uint64_t notified_ = 0;
  uint64_t phase_count_ = 0, exec_ns_ = 0, flushw_ns_ = 0, depw_ns_ = 0;
  uint64_t sync_ns_ = 0, sync_count_ = 0;
  std::vector<uint64_t> ro_latencies_ns_;
  uint64_t ro_notified_ = 0;

  alignas(CACHE_LINE_SIZE) std::atomic<uint64_t> flushed_lsn_{0};
  alignas(CACHE_LINE_SIZE) std::atomic<uint64_t> confirmed_lsn_{0};
};

}  // namespace pwal
