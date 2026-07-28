#pragma once

#include <mutex>
#include <vector>

#include "../../../include/cache_line_size.hh"
#include "../../../include/int64byte.hh"
#include "../../../include/lock.hh"
#include "../../../include/masstree_wrapper.hh"

#include "pwal.hh"
#include "transaction_table.hh"
#include "tuple.hh"


#include "gflags/gflags.h"
#include "glog/logging.h"

#ifdef GLOBAL_VALUE_DEFINE
#define GLOBAL
GLOBAL std::atomic<uint64_t> Lsn(0);
#if MASSTREE_USE
alignas(CACHE_LINE_SIZE) GLOBAL MasstreeWrapper<Tuple> MT;
#endif
#else
#define GLOBAL extern
GLOBAL std::atomic<uint64_t> Lsn;
#if MASSTREE_USE
alignas(CACHE_LINE_SIZE) GLOBAL MasstreeWrapper<Tuple> MT;
#endif
#endif

#ifdef GLOBAL_VALUE_DEFINE
DEFINE_uint64(clocks_per_us, 2100,
              "CPU_MHz. Use this info for measuring time.");
DEFINE_uint64(extime, 3, "Execution time[sec].");
DEFINE_uint64(gc_inter_us, 10, "GC interval[us].");
DEFINE_uint64(max_ope, 10,
              "Total number of operations per single transaction.");
DEFINE_uint64(
    pre_reserve_tmt_element, 100,
    "Pre-allocating memory for the transaction mapping table elements.");
DEFINE_uint64(pre_reserve_version, 10000,
              "Pre-allocating memory for the version.");
DEFINE_bool(rmw, false,
            "True means read modify write, false means blind write.");
DEFINE_uint64(rratio, 50, "read ratio of single transaction.");
DEFINE_uint64(thread_num, 10, "Number of regular worker threads.");
DEFINE_uint64(tuple_num, 1000000, "Total number of records.");
DEFINE_bool(ycsb, true,
            "True uses zipf_skew, false uses faster random generator.");
DEFINE_double(zipf_skew, 0, "zipf skew. 0 ~ 0.999...");
DEFINE_uint64(ronly_ratio, 0, "ratio of read-only online transaction.");
DEFINE_uint64(batch_th_num, 0, "Number of batch worker threads.");
DEFINE_uint64(batch_ratio, 0, "ratio of batch transaction.");
DEFINE_uint64(batch_max_ope, 1000,
              "Total number of operations per single batch transaction.");
DEFINE_uint64(batch_rratio, 100, "read ratio of single batch transaction.");
DEFINE_uint64(batch_tuples, 0,
              "Number of update-only records for batch transaction.");
DEFINE_bool(batch_simple_rr, false,
            "No one touches update-only records of batch transaction.");
DEFINE_uint64(pwal_flush_ntx, 10,
              "P-WAL: flush WAL buffer every this many committed txs.");
DEFINE_string(pwal_dir, "pwal_logs", "P-WAL: directory for WAL files.");
DEFINE_uint64(pwal_flushers, 0,
              "P-WAL: number of dedicated flusher threads. "
              "0 = auto (clamp(thread_num/2, 2, 32); W:F≈2:1 が実測最適).");
DEFINE_string(pwal_mode, "pipeline",
              "P-WAL mode: 'self' (worker flushes/notifies itself) or "
              "'pipeline' (dedicated flusher/committer threads).");
DEFINE_uint64(pwal_backpressure, 0,
              "P-WAL pipeline mode: worker stalls while its unconfirmed "
              "write-tx count >= this value (0 = unlimited).");
#else
DECLARE_uint64(clocks_per_us);
DECLARE_uint64(extime);
DECLARE_uint64(gc_inter_us);
DECLARE_uint64(max_ope);
DECLARE_uint64(pre_reserve_tmt_element);
DECLARE_uint64(pre_reserve_version);
DECLARE_bool(rmw);
DECLARE_uint64(rratio);
DECLARE_uint64(thread_num);
DECLARE_uint64(tuple_num);
DECLARE_bool(ycsb);
DECLARE_double(zipf_skew);
DECLARE_uint64(ronly_ratio);
DECLARE_uint64(batch_th_num);
DECLARE_uint64(batch_ratio);
DECLARE_uint64(batch_max_ope);
DECLARE_uint64(batch_rratio);
DECLARE_uint64(batch_tuples);
DECLARE_bool(batch_simple_rr);
DECLARE_uint64(pwal_flush_ntx);
DECLARE_string(pwal_dir);
DECLARE_uint64(pwal_flushers);
DECLARE_string(pwal_mode);
DECLARE_uint64(pwal_backpressure);
#endif

GLOBAL uint64_t TotalThreadNum;

alignas(CACHE_LINE_SIZE) GLOBAL Tuple* Table;
alignas(CACHE_LINE_SIZE) GLOBAL
    TransactionTable** TMT; // Transaction Mapping Table

// See si/include/common.hh for the rationale; same EBR-style guard
// that prevents gcRecord from freeing a Tuple while another thread
// still has it in its per-thread gcq_for_version_.
alignas(CACHE_LINE_SIZE) GLOBAL std::atomic<uint32_t>* MinQueuedCstamp;

GLOBAL std::mutex SsnLock;

// P-WAL: 共有LSNカウンタ(Cstamp用のLsnとは別)と全ワーカのレジストリ。
// PwalWorkersはmainがスレッド起動前にresizeし、各ワーカが自スロットに登録する。
GLOBAL pwal::LsnCounter PwalLsnCounter;
GLOBAL std::vector<pwal::Worker*> PwalWorkers;
GLOBAL bool PwalSelfMode;  // chkArgで-pwal_modeから設定(毎コミットの文字列比較を回避)
