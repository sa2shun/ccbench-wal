#pragma once

#include "ycsb.hh"

#ifdef GLOBAL_VALUE_DEFINE
DEFINE_uint64(ycsb_remote_read_prob_ppm, 0,
              "Remote read probability in ppm for partitioned YCSB.");
#else
DECLARE_uint64(ycsb_remote_read_prob_ppm);
#endif

inline static void makePartitionedProcedure(std::vector<Procedure>& pro,
                                            Xoroshiro128Plus& rnd,
                                            FastZipf& zipf,
                                            uint64_t thid,
                                            uint64_t thread_num) {
  pro.clear();
  bool ronly_flag = true;
  bool wonly_flag = true;
  const uint64_t workers = std::max<uint64_t>(1, thread_num);
  const uint64_t partition_size = std::max<uint64_t>(1, FLAGS_ycsb_tuple_num / workers);
  const uint64_t begin = std::min<uint64_t>(FLAGS_ycsb_tuple_num, thid * partition_size);
  const uint64_t end = (thid + 1 == workers)
      ? FLAGS_ycsb_tuple_num
      : std::min<uint64_t>(FLAGS_ycsb_tuple_num, begin + partition_size);
  const uint64_t span = std::max<uint64_t>(1, end - begin);

  for (size_t i = 0; i < FLAGS_ycsb_max_ope; ++i) {
    if ((rnd.next() % 100) < FLAGS_ycsb_rratio) {
      uint64_t target_begin = begin;
      uint64_t target_span = span;
      if (workers > 1 &&
          (rnd.next() % 1000000) < FLAGS_ycsb_remote_read_prob_ppm) {
        uint64_t remote = rnd.next() % (workers - 1);
        if (remote >= thid) ++remote;
        const uint64_t remote_begin =
            std::min<uint64_t>(FLAGS_ycsb_tuple_num, remote * partition_size);
        const uint64_t remote_end = (remote + 1 == workers)
            ? FLAGS_ycsb_tuple_num
            : std::min<uint64_t>(FLAGS_ycsb_tuple_num, remote_begin + partition_size);
        target_begin = remote_begin;
        target_span = std::max<uint64_t>(1, remote_end - remote_begin);
      }
      const uint64_t tmpkey = target_begin + (zipf() % target_span);
      wonly_flag = false;
      pro.emplace_back(Ope::READ, tmpkey);
    } else {
      const uint64_t tmpkey = begin + (zipf() % span);
      ronly_flag = false;
      if (FLAGS_ycsb_rmw) {
        pro.emplace_back(Ope::READ_MODIFY_WRITE, tmpkey);
      } else {
        pro.emplace_back(Ope::WRITE, tmpkey);
      }
    }
  }

  (*pro.begin()).ronly_ = ronly_flag;
  (*pro.begin()).wonly_ = wonly_flag;
#if KEY_SORT
  std::sort(pro.begin(), pro.end());
#endif
}

class PartitionedYcsbWorkload {
 public:
  Xoroshiro128Plus rnd_;
  FastZipf zipf_;

  PartitionedYcsbWorkload() {
    rnd_.init();
    FastZipf zipf(&rnd_, FLAGS_ycsb_zipf_skew, FLAGS_ycsb_tuple_num);
    zipf_ = zipf;
  }

  template <typename TxExecutor, typename TransactionStatus>
  void run(TxExecutor& tx) {
#if ADD_ANALYSIS
    uint64_t start = rdtscp();
#endif
    makePartitionedProcedure(tx.pro_set_, rnd_, zipf_, tx.thid_, FLAGS_thread_num);
#if ADD_ANALYSIS
    tx.result_->local_make_procedure_latency_ += rdtscp() - start;
#endif
    tx.is_ronly_ = (*tx.pro_set_.begin()).ronly_;

RETRY:
    if (tx.isLeader()) tx.leaderWork();
    if (loadAcquire(tx.quit_)) return;

    tx.begin();
    SimpleKey<8> key[tx.pro_set_.size()];
    HeapObject obj[tx.pro_set_.size()];
    uint64_t i = 0;
    for (auto& pro : tx.pro_set_) {
      YCSB::CreateKey(pro.key_, key[i].ptr());
      if (pro.ope_ == Ope::READ) {
        TupleBody* body;
        tx.read(Storage::YCSB, key[i].view(), &body);
        if (tx.status_ != TransactionStatus::aborted) {
          [[maybe_unused]] YCSB& t = body->get_value().cast_to<YCSB>();
        }
      } else if (pro.ope_ == Ope::WRITE) {
        obj[i].template allocate<YCSB>();
        [[maybe_unused]] YCSB& t = obj[i].ref();
        tx.update(Storage::YCSB, key[i].view(), TupleBody(key[i].view(), std::move(obj[i])));
      } else if (pro.ope_ == Ope::READ_MODIFY_WRITE) {
        TupleBody* body;
        tx.read(Storage::YCSB, key[i].view(), &body);
        if (tx.status_ != TransactionStatus::aborted) {
          YCSB& old_tuple = body->get_value().cast_to<YCSB>();
          obj[i].template allocate<YCSB>();
          YCSB& new_tuple = obj[i].ref();
          memcpy(new_tuple.val_, old_tuple.val_, VAL_SIZE);
          tx.update(Storage::YCSB, key[i].view(), TupleBody(key[i].view(), std::move(obj[i])));
        }
      } else {
        ERR;
      }

      if (tx.status_ == TransactionStatus::aborted) {
        tx.abort();
        ++tx.result_->local_abort_counts_;
#if ADD_ANALYSIS
        ++tx.result_->local_early_aborts_;
#endif
        goto RETRY;
      }
      ++i;
    }

    if (!tx.commit()) {
      tx.abort();
      ++tx.result_->local_abort_counts_;
      goto RETRY;
    }
    storeRelease(tx.result_->local_commit_counts_,
                 loadAcquire(tx.result_->local_commit_counts_) + 1);
  }

  template <typename Tuple, typename Param>
  static void makeDB(Param* p) {
    YcsbWorkload::makeDB<Tuple, Param>(p);
  }

  static void displayWorkloadParameter() {
    YcsbWorkload::displayWorkloadParameter();
    cout << "#FLAGS_ycsb_partitioned_abort0:\ttrue" << endl;
    cout << "#FLAGS_ycsb_remote_read_prob_ppm:\t"
         << FLAGS_ycsb_remote_read_prob_ppm << endl;
  }
};
