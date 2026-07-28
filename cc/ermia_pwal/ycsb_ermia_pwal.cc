
#include <ctype.h> //isdigit,
#include <pthread.h>
#include <string.h>      //strlen,
#include <sys/stat.h>    //mkdir
#include <sys/syscall.h> //syscall(SYS_gettid),
#include <sys/types.h>   //syscall(SYS_gettid),
#include <time.h>
#include <unistd.h> //syscall(SYS_gettid),
#include <algorithm>
#include <cmath>
#include <iostream>
#include <string> //string

#define GLOBAL_VALUE_DEFINE

#include "include/common.hh"
#include "include/garbage_collection.hh"
#include "include/result.hh"
#include "include/transaction.hh"
#include "include/util.hh"

#include "../../include/atomic_wrapper.hh"
#include "../../include/backoff.hh"
#include "../../include/cpu.hh"
#include "../../include/debug.hh"
#include "../../include/int64byte.hh"
#include "../../include/masstree_wrapper.hh"
#include "../../include/procedure.hh"
#include "../../include/random.hh"
#include "../../include/result.hh"
#include "../../include/tsc.hh"
#include "../../include/util.hh"
#include "../../include/zipf.hh"
#include "../../include/ycsb.hh"

using namespace std;

void worker(size_t thid, char& ready, const bool& start, const bool& quit) {
  Backoff backoff(FLAGS_clocks_per_us); // Cicada's backoff opt.
  TxExecutor trans(thid, backoff, (Result*) &ErmiaResult[thid], quit);
  YcsbWorkload workload;


#if MASSTREE_USE
  MasstreeWrapper<Tuple>::thread_init(int(thid));
#endif

#ifdef Linux
  setThreadAffinity(thid);
  // printf("Thread #%zu: on CPU %d\n", thid, sched_getcpu());
  // printf("sysconf(_SC_NPROCESSORS_CONF) %ld\n",
  // sysconf(_SC_NPROCESSORS_CONF));
#endif // Linux
  // printf("Thread #%d: on CPU %d\n", *myid, sched_getcpu());

  if (trans.isLeader()) trans.gcob.decideFirstRange();

  storeRelease(ready, 1);
  while (!loadAcquire(start)) _mm_pause();
  trans.gcstart_ = rdtscp();
  while (!loadAcquire(quit)) {
    workload.run<TxExecutor, TransactionStatus>(trans);
  }
  return;
}

// P-WAL 3役割分離: flusher / committer スレッド (メモ: three_role_plan.md)
std::atomic<bool> FlusherStop{false};
std::atomic<bool> CommitterSnapshot{false};  // 計測区間の締め指示
std::atomic<bool> CommitterStop{false};

void flusherLoop(size_t fid, size_t n_flushers) {
  // バックプレッシャーK < flush間隔N だと、ワーカがN件溜まる前に停止して
  // flushが永遠に発火しない相互待ちになるため、発火閾値は min(N, K) とする。
  uint64_t threshold = FLAGS_pwal_flush_ntx;
  if (FLAGS_pwal_backpressure > 0 && FLAGS_pwal_backpressure < threshold)
    threshold = FLAGS_pwal_backpressure;
  while (!FlusherStop.load(std::memory_order_acquire)) {
    for (size_t w = fid; w < TotalThreadNum; w += n_flushers) {
      // バックプレッシャーで停止中のワーカ(inflight >= K)は、バッファに
      // 閾値未満の端数ENDが残ったまま凍結し、そのflushedLSN経由で全体が
      // 連鎖停止しうる。停止中ワーカは閾値0で強制flushして解消する。
      uint64_t th_w = threshold;
      if (FLAGS_pwal_backpressure > 0 &&
          PwalWorkers[w]->inflight() >= FLAGS_pwal_backpressure)
        th_w = 0;
      PwalWorkers[w]->flushPending(th_w);
    }
  }
  // 停止時2パス: (1)残りを強制flush → (2)全バッファが空になった状態で
  // アイドル前進(currentMaxまで)を行い、min(flushedLSN)が全コミットLSNを
  // 跨げるようにする。これがないと最終バッチのLSNでminが止まり、
  // committerが残エントリを確定できず終了できない。
  for (size_t w = fid; w < TotalThreadNum; w += n_flushers) {
    PwalWorkers[w]->flushPending(0);
  }
  for (size_t w = fid; w < TotalThreadNum; w += n_flushers) {
    PwalWorkers[w]->flushPending(0);  // 空バッファ→アイドル前進
  }
}

void committerLoop() {
  bool snapshotted = false;
  for (;;) {
    if (!snapshotted && CommitterSnapshot.load(std::memory_order_acquire)) {
      for (size_t w = 0; w < TotalThreadNum; ++w)
        PwalWorkers[w]->snapshotMeasured();
      snapshotted = true;
    }
    // パスごとにminを1回計算して全キューへ適用
    uint64_t min_flushed = UINT64_MAX;
    for (size_t w = 0; w < TotalThreadNum; ++w) {
      uint64_t f = PwalWorkers[w]->flushedLsn();
      if (f < min_flushed) min_flushed = f;
    }
    auto now = std::chrono::steady_clock::now();
    for (size_t w = 0; w < TotalThreadNum; ++w) {
      PwalWorkers[w]->confirmUpTo(min_flushed, PwalWorkers[w]->flushedLsn(),
                                  now);
    }
    if (CommitterStop.load(std::memory_order_acquire)) {
      bool all_empty = true;
      for (size_t w = 0; w < TotalThreadNum; ++w) {
        if (!PwalWorkers[w]->queueEmpty()) { all_empty = false; break; }
      }
      if (all_empty) return;
    }
  }
}

int main(int argc, char* argv[]) try {
  gflags::SetUsageMessage("ERMIA benchmark.");
  gflags::ParseCommandLineFlags(&argc, &argv, true);
  chkArg();
  YcsbWorkload::displayWorkloadParameter();
  YcsbWorkload::makeDB<Tuple, void>(nullptr);

  // P-WAL: ログディレクトリと全スレッド分のWALワーカを用意する
  mkdir(FLAGS_pwal_dir.c_str(), 0755);
  PwalWorkers.resize(TotalThreadNum);
  for (size_t i = 0; i < TotalThreadNum; ++i) {
    PwalWorkers[i] = new pwal::Worker(
        FLAGS_pwal_dir + "/worker" + std::to_string(i) + ".wal",
        PwalLsnCounter);
  }

  alignas(CACHE_LINE_SIZE) bool start = false;
  alignas(CACHE_LINE_SIZE) bool quit = false;
  initResult();
  std::vector<char> readys(TotalThreadNum);
  std::vector<std::thread> thv;
  for (size_t i = 0; i < TotalThreadNum; ++i)
    thv.emplace_back(worker, i, std::ref(readys[i]), std::ref(start),
                     std::ref(quit));
  std::vector<std::thread> flushers;
  std::thread committer;
  if (!PwalSelfMode) {
    for (size_t f = 0; f < FLAGS_pwal_flushers; ++f)
      flushers.emplace_back(flusherLoop, f, (size_t) FLAGS_pwal_flushers);
    committer = std::thread(committerLoop);
  }

  waitForReady(readys);
  uint64_t start_tsc = rdtscp();
  storeRelease(start, true);
  for (size_t i = 0; i < FLAGS_extime; ++i) { sleepMs(1000); }
  storeRelease(quit, true);
  // 計測区間の締め: 以降にcommitterが確定する分は分位点から除外する
  CommitterSnapshot.store(true, std::memory_order_release);
  for (auto& th : thv) th.join();
  if (!PwalSelfMode) {
    // worker終了後: flusherが残りを永続化 → committerが残りを確定
    FlusherStop.store(true, std::memory_order_release);
    for (auto& th : flushers) th.join();
    CommitterStop.store(true, std::memory_order_release);
    committer.join();
  } else {
    // selfモードの終了: mainが最終flush(2パス)と残りの確定を行う
    for (int pass_ = 0; pass_ < 2; ++pass_)
      for (size_t w = 0; w < TotalThreadNum; ++w)
        PwalWorkers[w]->flushPending(0);
    for (size_t w = 0; w < TotalThreadNum; ++w)
      PwalWorkers[w]->snapshotMeasured();
    for (;;) {
      bool all_empty = true;
      uint64_t min_flushed = UINT64_MAX;
      for (size_t w = 0; w < TotalThreadNum; ++w) {
        uint64_t f = PwalWorkers[w]->flushedLsn();
        if (f < min_flushed) min_flushed = f;
      }
      auto now = std::chrono::steady_clock::now();
      for (size_t w = 0; w < TotalThreadNum; ++w) {
        PwalWorkers[w]->confirmUpTo(min_flushed, PwalWorkers[w]->flushedLsn(), now);
        if (!PwalWorkers[w]->queueEmpty()) all_empty = false;
      }
      if (all_empty) break;
    }
  }
  uint64_t end_tsc = rdtscp();
  long double actual_extime =
      round((end_tsc - start_tsc) /
            ((long double) FLAGS_clocks_per_us * powl(10.0, 6.0)));

  for (unsigned int i = 0; i < TotalThreadNum; ++i) {
    ErmiaResult[0].addLocalAllResult(ErmiaResult[i]);
  }
  ShowOptParameters();
  std::cout << "actual_extime:\t" << actual_extime << std::endl;
  ErmiaResult[0].displayAllResult(FLAGS_clocks_per_us, FLAGS_extime,
                                  TotalThreadNum, FLAGS_max_ope,
                                  FLAGS_batch_max_ope);

  // P-WAL: 全ワーカがflush済みなので残りのコミット待ちを確定し、通知合計を出す。
  // drain分のレイテンシは「全ワーカ終了待ち」を含むため分位点集計から除外する。
  uint64_t pwal_notified = 0;
  std::vector<uint64_t> lat;
  for (size_t i = 0; i < TotalThreadNum; ++i) {
    pwal_notified += PwalWorkers[i]->notified();
    const auto& ls = PwalWorkers[i]->latenciesNs();
    lat.insert(lat.end(), ls.begin(), ls.begin() + PwalWorkers[i]->measuredLat());
    const auto& ro = PwalWorkers[i]->roLatenciesNs();
    lat.insert(lat.end(), ro.begin(), ro.end());
  }
  std::cout << "pwal_notified_commits:\t" << pwal_notified << std::endl;
  std::sort(lat.begin(), lat.end());
  auto pct = [&](double p) -> uint64_t {
    if (lat.empty()) return 0;
    size_t rank = static_cast<size_t>(std::ceil(p * lat.size()));  // nearest-rank
    if (rank == 0) rank = 1;
    return lat[rank - 1];
  };
  double lat_avg = 0;
  for (uint64_t v : lat) lat_avg += static_cast<double>(v);
  if (!lat.empty()) lat_avg /= static_cast<double>(lat.size());
  std::cout << "pwal_lat_avg[ns]:\t" << lat_avg << std::endl;
  std::cout << "pwal_lat_p50[ns]:\t" << pct(0.50) << std::endl;
  std::cout << "pwal_lat_p95[ns]:\t" << pct(0.95) << std::endl;
  std::cout << "pwal_lat_p99[ns]:\t" << pct(0.99) << std::endl;
  std::cout << "pwal_lat_samples:\t" << lat.size() << std::endl;
  // レイテンシ内訳(書き込みtxのみ。停止後に確定した残余も含む全期間の平均。
  // 分位点(p50等)のみ計測締め時点で打ち切る)とfdatasync統計
  uint64_t pc = 0, pe = 0, pf = 0, pd = 0, sn = 0, sc = 0;
  for (size_t i = 0; i < TotalThreadNum; ++i) {
    pc += PwalWorkers[i]->phaseCount();
    pe += PwalWorkers[i]->phaseExecNs();
    pf += PwalWorkers[i]->phaseFlushWaitNs();
    pd += PwalWorkers[i]->phaseDepWaitNs();
    sn += PwalWorkers[i]->syncNs();
    sc += PwalWorkers[i]->syncCount();
  }
  if (pc > 0) {
    std::cout << "pwal_phase_exec_avg[ns]:\t" << (double) pe / pc << std::endl;
    std::cout << "pwal_phase_flushwait_avg[ns]:\t" << (double) pf / pc
              << std::endl;
    std::cout << "pwal_phase_depwait_avg[ns]:\t" << (double) pd / pc
              << std::endl;
    std::cout << "pwal_phase_count:\t" << pc << std::endl;
  }
  if (sc > 0) {
    std::cout << "pwal_sync_avg[ns]:\t" << (double) sn / sc << std::endl;
    std::cout << "pwal_sync_count:\t" << sc << std::endl;
  }
  for (auto* w : PwalWorkers) delete w;

  return 0;
} catch (const bad_alloc&) { ERR; }
