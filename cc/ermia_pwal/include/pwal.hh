#pragma once
// P-WAL (並列WAL) ロギング層 — 3役割分離版 (worker / flusher / committer)。
// - worker: 実行とログ追記・コミットエントリpushのみ(I/Oも通知判定もしない)
// - flusher: 担当ワーカのバッファをNコミットごとにflush(write+fdatasync)
// - committer: 全ワーカのキューを巡回し、commitLSN <= min(全ワーカflushedLSN)
//   を満たす先頭から通知を確定する
// バッファとキューはper-worker mutexで保護(flusherはswapのみロック内、I/Oはロック外)。

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
// 値ペイロードは含めない(全手法で同一条件のため比較は公平)。
struct LogRecord {
  uint64_t lsn;
  uint64_t type;
  uint64_t cstamp;
  uint64_t key;
};
static_assert(sizeof(LogRecord) == 32, "LogRecord must be 32 bytes");

// 全ワーカ共有のP-WAL LSNカウンタ。Cstamp用の Lsn とは別の2本目。
class LsnCounter {
 public:
  uint64_t next() { return counter_.fetch_add(1, std::memory_order_relaxed); }
  // 採番済みの最大LSN(未採番なら0)。アイドルワーカのflushedLSN前進用。
  uint64_t currentMax() const {
    return counter_.load(std::memory_order_relaxed) - 1;
  }

 private:
  // LSNは1から始める(0は「未永続化」を表すために予約)
  alignas(CACHE_LINE_SIZE) std::atomic<uint64_t> counter_{1};
};

struct CommitEntry {
  uint64_t cstamp;
  uint64_t commit_lsn;
  std::chrono::steady_clock::time_point start;  // トランザクション開始時刻
  std::chrono::steady_clock::time_point t1;     // コミットログをバッファに積んだ時刻
  std::chrono::steady_clock::time_point t2{};   // flushedLSNが自分を跨いだのを
                                                // committerが初観測した時刻(近似)
};

// 1ワーカ分のP-WAL状態。
// スレッド役割: worker(append/pushCommit/notifyDirectlyRo)、
// flusher(flushPending/idleAdvance、ワーカごとに担当flusherは1人)、
// committer(キューの確定、1人)。
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

  // ログレコードにLSNを採番してバッファ末尾に追加し、そのLSNを返す。
  uint64_t append(LogType type, uint64_t cstamp, uint64_t key) {
    LogRecord rec;
    rec.type = static_cast<uint64_t>(type);
    rec.cstamp = cstamp;
    rec.key = key;
    std::lock_guard<std::mutex> g(buf_mtx_);
    rec.lsn = lsn_counter_.next();  // 採番と追記を同一ロック内で行う
    buffer_.push_back(rec);
    // pendingはENDがバッファに入った時点で加算する(flusherのswap/減算と
    // 同一ロック順序で整合し、pushCommitとの間のunderflowが起きない)
    if (type == LogType::End)
      pending_ntx_.fetch_add(1, std::memory_order_relaxed);
    return rec.lsn;
  }

  void pushCommit(uint64_t cstamp, uint64_t commit_lsn,
                  std::chrono::steady_clock::time_point start) {
    std::lock_guard<std::mutex> g(q_mtx_);
    commit_queue_.push_back(
        {cstamp, commit_lsn, start, std::chrono::steady_clock::now(), {}});
    inflight_.fetch_add(1, std::memory_order_relaxed);
  }

  // read-only(ログなし)txの即時通知。統計はworker専用フィールドへ。
  void notifyDirectlyRo(std::chrono::steady_clock::time_point start) {
    ro_latencies_ns_.push_back(static_cast<uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now() - start)
            .count()));
    ro_notified_++;
  }

  // ---- flusher専用 (このワーカの担当flusherは1人) ----

  uint64_t pendingNtx() const {
    return pending_ntx_.load(std::memory_order_relaxed);
  }

  // バッファをswapで回収し(ロックはswapの間のみ)、ロック外でwrite+fdatasyncする。
  // バッファが空ならアイドル前進のみ行う。未flushコミット数が min_commits 未満なら
  // 何もしない(min_commits=0 で強制flush)。
  void flushPending(uint64_t min_commits) {
    std::vector<LogRecord> batch;
    {
      std::lock_guard<std::mutex> g(buf_mtx_);
      if (!buffer_.empty() &&
          pending_ntx_.load(std::memory_order_relaxed) < min_commits) {
        return;
      }
      if (buffer_.empty()) {
        // 未flushログなし: 採番済み最大LSNまで前進してよい
        // (pwalはappend内で採番するため「予約済み未append」の窓はない)
        uint64_t cur = lsn_counter_.currentMax();
        if (cur > flushed_lsn_.load(std::memory_order_relaxed)) {
          flushed_lsn_.store(cur, std::memory_order_release);
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
    // バッチ内でLSNは単調増加なので末尾が永続化済み最大LSN
    flushed_lsn_.store(batch.back().lsn, std::memory_order_release);
    // 消化したコミット数(ENDレコード数)だけpendingを減らす
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

  // flushedLSNが跨いだ未スタンプのエントリにt2を記録する(キュー先頭からの接頭辞)。
  // その後、commit_lsn <= min_flushed の先頭から確定する。
  // 戻り値: 確定した件数。
  size_t confirmUpTo(uint64_t min_flushed, uint64_t own_flushed,
                     std::chrono::steady_clock::time_point now) {
    std::lock_guard<std::mutex> g(q_mtx_);
    for (size_t i = t2_stamped_; i < commit_queue_.size(); i++) {
      if (commit_queue_[i].commit_lsn > own_flushed) break;
      commit_queue_[i].t2 = now;
      t2_stamped_ = i + 1;
    }
    size_t n = 0;
    while (!commit_queue_.empty() &&
           commit_queue_.front().commit_lsn <= min_flushed) {
      recordConfirmed(commit_queue_.front(), now);
      commit_queue_.pop_front();
      inflight_.fetch_sub(1, std::memory_order_relaxed);
      if (t2_stamped_ > 0) t2_stamped_--;
      n++;
    }
    return n;
  }

  // バックプレッシャー用: 未確定の書き込みtx数
  uint64_t inflight() const { return inflight_.load(std::memory_order_relaxed); }

  bool queueEmpty() {
    std::lock_guard<std::mutex> g(q_mtx_);
    return commit_queue_.empty();
  }

  // 計測区間の締め: 以降に確定した分は分位点集計から除外する
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

  std::mutex buf_mtx_;  // worker(append)とflusher(swap/アイドル前進)の排他
  std::vector<LogRecord> buffer_;
  std::atomic<uint64_t> pending_ntx_{0};  // 未flushのコミット数

  std::mutex q_mtx_;  // worker(push)とcommitter(スタンプ/確定)の排他
  std::deque<CommitEntry> commit_queue_;
  std::atomic<uint64_t> inflight_{0};
  size_t t2_stamped_ = 0;  // キュー先頭からt2記録済みの件数

  // committer専用の統計(書き込みtx)
  std::vector<uint64_t> latencies_ns_;
  size_t measured_lat_ = 0;
  uint64_t notified_ = 0;
  uint64_t phase_count_ = 0, exec_ns_ = 0, flushw_ns_ = 0, depw_ns_ = 0;
  // flusher専用の統計
  uint64_t sync_ns_ = 0, sync_count_ = 0;
  // worker専用の統計(read-only即時通知)
  std::vector<uint64_t> ro_latencies_ns_;
  uint64_t ro_notified_ = 0;

  alignas(CACHE_LINE_SIZE) std::atomic<uint64_t> flushed_lsn_{0};
};

}  // namespace pwal
