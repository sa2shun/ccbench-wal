#include <algorithm>
#include <atomic>
#include <bitset>
#include <set>

#include "../../include/atomic_wrapper.hh"
#include "../../include/backoff.hh"
#include "../../include/masstree_wrapper.hh"
#include "include/common.hh"
#include "include/scan_callback.hh"
#include "include/transaction.hh"
#include "include/version.hh"

extern std::vector<Result> RCSSNResult;
extern bool chkClkSpan(const uint64_t start, const uint64_t stop,
                       const uint64_t threshold);

using namespace std;

/**
 * @brief Initialize function of transaction.
 * Allocate timestamp.
 * @return void
 */
void TxExecutor::begin() {
  TransactionTable *newElement, *tmt;

  tmt = loadAcquire(TMT[thid_]);
  uint32_t lastcstamp;
  if (this->status_ == TransactionStatus::aborted) {
    /**
     * If this transaction is retry by abort,
     * its lastcstamp is last one.
     */
    lastcstamp = this->txid_ = tmt->lastcstamp_.load(memory_order_acquire);
  } else {
    /**
     * If this transaction is after committed transaction,
     * its lastcstamp is that's one.
     */
    lastcstamp = this->txid_ = cstamp_;
  }

  if (gcobject_.reuse_TMT_element_from_gc_.empty()) {
    /**
     * If no cache,
     */
    newElement = new TransactionTable(0, 0, UINT32_MAX, lastcstamp,
                                      TransactionStatus::inflight);
#if ADD_ANALYSIS
    ++result_->local_TMT_element_malloc_;
#endif
  } else {
    /**
     * If it has cache, this transaction use it.
     */
    newElement = gcobject_.reuse_TMT_element_from_gc_.back();
    gcobject_.reuse_TMT_element_from_gc_.pop_back();
    newElement->set(0, 0, UINT32_MAX, lastcstamp, TransactionStatus::inflight);
#if ADD_ANALYSIS
    ++result_->local_TMT_element_reuse_;
#endif
  }

  /**
   * Check the latest commit timestamp
   */
  for (unsigned int i = 0; i < TotalThreadNum; ++i) {
    tmt = loadAcquire(TMT[i]);
    this->txid_ = max(this->txid_, tmt->lastcstamp_.load(memory_order_acquire));
  }
  this->txid_ += 1;
  newElement->txid_ = this->txid_;

  /**
   * Old object becomes cache object.
   */
  gcobject_.gcq_for_TMT_.emplace_back(loadAcquire(TMT[thid_]));
  /**
   * New object is registerd to transaction mapping table.
   */
  storeRelease(TMT[thid_], newElement);

  pstamp_ = 0;
  sstamp_ = UINT32_MAX;
  dep_vector_.fill(0);
  status_ = TransactionStatus::inflight;
}

void TxExecutor::addDurabilityDependency(uint32_t worker, uint32_t cstamp) {
#if CCBENCH_SSN_PWAL_WAIT_MODE != 0
  if (worker < dep_vector_.size() && cstamp != 0) {
    dep_vector_[worker] = max(dep_vector_[worker], cstamp);
  }
#else
  (void)worker;
  (void)cstamp;
#endif
}

void TxExecutor::mergeVersionDependency(Version *ver) {
#if CCBENCH_SSN_PWAL_WAIT_MODE != 0
  while (ver && ver->status_.load(memory_order_acquire) != VersionStatus::committed
      && ver->status_.load(memory_order_acquire) != VersionStatus::deleted) {
    ver = ver->prev_;
  }
  if (!ver) return;
  const uint32_t cstamp = ver->cstamp_.load(memory_order_acquire);
  const uint32_t worker = ver->creator_worker_.load(memory_order_acquire);
  addDurabilityDependency(worker, cstamp);
#if CCBENCH_SSN_PWAL_WAIT_MODE == 2
  for (size_t w = 0; w < dep_vector_.size(); ++w) {
    addDurabilityDependency(
        static_cast<uint32_t>(w),
        ver->durable_deps_[w].load(memory_order_acquire));
  }
#endif
#else
  (void)ver;
#endif
}

void TxExecutor::mergeReaderDependencies(Version *ver) {
#if CCBENCH_SSN_PWAL_WAIT_MODE != 0
  for (size_t w = 0; w < dep_vector_.size(); ++w) {
    addDurabilityDependency(
        static_cast<uint32_t>(w),
        ver->reader_deps_[w].load(memory_order_acquire));
  }
#else
  (void)ver;
#endif
}

void TxExecutor::publishReadDependencies() {
#if CCBENCH_SSN_PWAL_WAIT_MODE != 0
  ccbench::SsnPwalDLogger::DepVector published{};
#if CCBENCH_SSN_PWAL_WAIT_MODE == 2
  published = dep_vector_;
#endif
  published[thid_] = max(published[thid_], cstamp_);
  for (auto &read : read_set_) {
    for (size_t w = 0; w < published.size(); ++w) {
      uint32_t expected =
          read.ver_->reader_deps_[w].load(memory_order_acquire);
      while (expected < published[w] &&
             !read.ver_->reader_deps_[w].compare_exchange_weak(
                 expected, published[w], memory_order_acq_rel,
                 memory_order_acquire)) {
      }
    }
  }
#endif
}

void TxExecutor::publishWriteDependencies() {
#if CCBENCH_SSN_PWAL_WAIT_MODE != 0
  for (auto &write : write_set_) {
    write.ver_->creator_worker_.store(thid_, memory_order_relaxed);
#if CCBENCH_SSN_PWAL_WAIT_MODE == 2
    for (size_t w = 0; w < dep_vector_.size(); ++w) {
      write.ver_->durable_deps_[w].store(dep_vector_[w],
                                         memory_order_relaxed);
    }
#endif
  }
#endif
}

/**
 * @brief Transaction read function.
 * @param [in] key The key of key-value
 */
Status TxExecutor::read(Storage s, std::string_view key, TupleBody** body) {
#if ADD_ANALYSIS
  uint64_t start(rdtscp());
#endif

  /**
   * read-own-writes, re-read from previous read in the same tx.
   */
  SetElement<Tuple>* e = searchWriteSet(s, key);
  if (e) {
    if (e->op_ == OpType::DELETE) {
      // deleted by myself
      return Status::WARN_NOT_FOUND;
    }
    *body = &(e->ver_->body_);
    goto FINISH_READ;
  }

  /**
   * Search versions from data structure.
   */
  Tuple *tuple;
  tuple = Masstrees[get_storage(s)].get_value(key);
#if ADD_ANALYSIS
  ++result_->local_tree_traversal_;
#endif
  if (tuple == nullptr) return Status::WARN_NOT_FOUND;

  Version *ver;
  ver = read_internal(s, key, tuple);
  if (ver == nullptr || ver->status_.load(memory_order_acquire) == VersionStatus::deleted)
    return Status::WARN_NOT_FOUND;

  /**
   * read payload.
   */
  *body = &(ver->body_);
#if ADD_ANALYSIS
  ++result_->local_memcpys;
#endif

FINISH_READ:
#if ADD_ANALYSIS
  result_->local_read_latency_ += rdtscp() - start;
#endif
  return Status::OK;
}

Version* TxExecutor::read_internal(Storage s, std::string_view key, Tuple* tuple) {
  /**
   * Move to the points of this view.
   */
  Version *ver;
  ver = tuple->latest_.load(memory_order_acquire);
  while ((ver->status_.load(memory_order_acquire) != VersionStatus::committed
      && ver->status_.load(memory_order_acquire) != VersionStatus::deleted)) {
    ver = ver->prev_;
    if (ver == nullptr) {
      return nullptr;
    }
  }

  read_set_.emplace_back(s, key, tuple, ver);
  mergeVersionDependency(ver);
  uint32_t v_sstamp;
  v_sstamp = ver->psstamp_.atomicLoadSstamp();
  if (!(v_sstamp & TIDFLAG) && v_sstamp != (UINT32_MAX & ~(TIDFLAG))) {
    // update pi with r:w edge
    this->sstamp_ =
            min(this->sstamp_, (v_sstamp >> TIDFLAG));
  }
  upReadersBits(ver);

  verify_exclusion_or_abort();
  if (this->status_ == TransactionStatus::aborted) {
    return nullptr;
  }

  return ver;
}

Status TxExecutor::install_version(Tuple* tuple, Version* desired) {
  Version* expected = tuple->latest_.load(memory_order_acquire);
  for (;;) {
    // w-w conflict
    // first updater wins rule
    if (expected->status_.load(memory_order_acquire) ==
        VersionStatus::inflight) {
      this->status_ = TransactionStatus::aborted;
      TMT[thid_]->status_.store(TransactionStatus::aborted,
                                memory_order_release);
      gcobject_.reuse_version_from_gc_.emplace_back(desired);
      return Status::ERROR_CONCURRENT_WRITE_OR_DELETE;
    }

    desired->prev_ = expected;
    if (tuple->latest_.compare_exchange_strong(
            expected, desired, memory_order_acq_rel, memory_order_acquire)) {
      break;
    }
  }
  return Status::OK;
}


/**
 * @brief Transaction write function.
 * @param [in] key The key of key-value
 */
Status TxExecutor::update(Storage s, std::string_view key, TupleBody&& body) {
#if ADD_ANALYSIS
  uint64_t start = rdtscp();
#endif
  Status stat = Status::OK;

  /**
   * update local write set.
   */
  if (searchWriteSet(s, key)) goto FINISH_WRITE;

  /**
   * avoid false positive.
   */
  Tuple *tuple;
  tuple = nullptr;
  for (auto itr = read_set_.begin(); itr != read_set_.end(); ++itr) {
    if ((*itr).storage_ == s && (*itr).key_ == key) {
      downReadersBits((*itr).ver_);
      /**
       * If it can find record in read set, use this for high performance.
       */
      tuple = (*itr).rcdptr_;
      read_set_.erase(itr);
      break;
    }
  }

  /**
   * Search tuple from data structure.
   */
  if (!tuple) {
    tuple = Masstrees[get_storage(s)].get_value(key);
#if ADD_ANALYSIS
    ++result_->local_tree_traversal_;
#endif
  }
  if (tuple == nullptr) return Status::WARN_NOT_FOUND;

  /**
   * If v not in t.writes:
   * first-updater-wins rule
   * Forbid a transaction to update  a record that has a committed head version
   * later than its begin timestamp.
   */
  Version *desired;
  if (gcobject_.reuse_version_from_gc_.empty()) {
    desired = new Version();
#if ADD_ANALYSIS
    ++result_->local_version_malloc_;
#endif
  } else {
    desired = gcobject_.reuse_version_from_gc_.back();
    gcobject_.reuse_version_from_gc_.pop_back();
    desired->init();
#if ADD_ANALYSIS
    ++result_->local_version_reuse_;
#endif
  }
  desired->cstamp_.store(this->txid_, memory_order_relaxed);  // read operation, write operation,
  // it is also accessed by garbage collection.
  // scan() may inspect latest_->body_ even while this version is inflight.
  // Initialize the body before publishing desired via install_version().
  desired->body_ = std::move(body);

  stat = install_version(tuple, desired);
  if (stat != Status::OK) {
    goto FINISH_WRITE;
  }
  mergeVersionDependency(desired->prev_);

  /**
   * Insert my tid for ver->prev_->sstamp_ 
   */
  uint64_t tmpTID;
  tmpTID = thid_;
  tmpTID = tmpTID << 1;
  tmpTID |= 1;
  desired->prev_->psstamp_.atomicStoreSstamp(tmpTID);

  /**
   * Update eta with w:r edge
   */
  this->pstamp_ =
          max(this->pstamp_, desired->prev_->psstamp_.atomicLoadPstamp());
  write_set_.emplace_back(s, key, tuple, desired, OpType::UPDATE);

  verify_exclusion_or_abort();

FINISH_WRITE:
#if ADD_ANALYSIS
  result_->local_write_latency_ += rdtscp() - start;
#endif
  return stat;
}

Status TxExecutor::insert(Storage s, std::string_view key, TupleBody&& body) {
#if ADD_ANALYSIS
  uint64_t start = rdtscp();
#endif  // if ADD_ANALYSIS

  if (searchWriteSet(s, key)) return Status::WARN_ALREADY_EXISTS;

  Tuple* tuple = Masstrees[get_storage(s)].get_value(key);
#if ADD_ANALYSIS
  ++result_->local_tree_traversal_;
#endif
  if (tuple != nullptr) {
    return Status::WARN_ALREADY_EXISTS;
  }

  tuple = new Tuple();
  tuple->init(this->txid_, std::move(body));
  Version* ver = tuple->latest_.load(std::memory_order_acquire);
  typename MasstreeWrapper<Tuple>::insert_info_t insert_info;
  Status stat = Masstrees[get_storage(s)].insert_value(key, tuple, &insert_info);
  if (stat == Status::WARN_ALREADY_EXISTS) {
    delete tuple;
    return stat;
  }
  if (!insert_info.node) {
    ERR;
  }

  write_set_.emplace_back(s, key, tuple, ver, OpType::INSERT);

#if ADD_ANALYSIS
  result_->local_write_latency_ += rdtscp() - start;
#endif  // if ADD_ANALYSIS
  return Status::OK;
}

Status TxExecutor::delete_record(Storage s, std::string_view key) {
#if ADD_ANALYSIS
  uint64_t start = rdtscp();
#endif  // if ADD_ANALYSIS
  Status stat = Status::OK;

  // cancel previous write
  for (auto itr = write_set_.begin(); itr != write_set_.end(); ++itr) {
    if ((*itr).storage_ != s) continue;
    if ((*itr).key_ == key) {
      write_set_.erase(itr);
      // erase後のiteratorは無効なので、ここで必ず抜ける。
      break;
    }
  }

  Tuple* tuple = nullptr;
  for (auto itr = read_set_.begin(); itr != read_set_.end(); ++itr) {
    if ((*itr).storage_ == s && (*itr).key_ == key) {
      downReadersBits((*itr).ver_);
      tuple = (*itr).rcdptr_;
      read_set_.erase(itr);
      break;
    }
  }

  if (!tuple) {
    tuple = Masstrees[get_storage(s)].get_value(key);
#if ADD_ANALYSIS
    ++result_->local_tree_traversal_;
#endif  // if ADD_ANALYSIS
    if (tuple == nullptr) return Status::WARN_NOT_FOUND;
  }

  Version *desired;
  if (gcobject_.reuse_version_from_gc_.empty()) {
    desired = new Version();
#if ADD_ANALYSIS
    ++result_->local_version_malloc_;
#endif
  } else {
    desired = gcobject_.reuse_version_from_gc_.back();
    gcobject_.reuse_version_from_gc_.pop_back();
    desired->init();
#if ADD_ANALYSIS
    ++result_->local_version_reuse_;
#endif
  }
  desired->cstamp_.store(this->txid_, memory_order_relaxed);  // read operation, write operation,

  // it is also accessed by garbage collection.
  // scan() derives a tuple key from latest_->body_; DELETE versions need a key
  // before they become visible as latest_.
  desired->body_.set_key(key);

  stat = install_version(tuple, desired);
  if (stat != Status::OK) {
    goto FINISH_DELETE;
  }
  mergeVersionDependency(desired->prev_);

  /**
   * Insert my tid for ver->prev_->sstamp_ 
   */
  uint64_t tmpTID;
  tmpTID = thid_;
  tmpTID = tmpTID << 1;
  tmpTID |= 1;
  desired->prev_->psstamp_.atomicStoreSstamp(tmpTID);

  /**
   * Update eta with w:r edge
   */
  this->pstamp_ = max(this->pstamp_, desired->prev_->psstamp_.atomicLoadPstamp());
  write_set_.emplace_back(s, key, tuple, desired, OpType::DELETE);

  verify_exclusion_or_abort();

FINISH_DELETE:
#if ADD_ANALYSIS
  result_->local_write_latency_ += rdtscp() - start;
#endif
  return stat;
}

Status TxExecutor::scan(const Storage s,
                        std::string_view left_key, bool l_exclusive,
                        std::string_view right_key, bool r_exclusive,
                        std::vector<TupleBody*>& result) {
  return scan(s, left_key, l_exclusive, right_key, r_exclusive, result, -1);
}

Status TxExecutor::scan(const Storage s,
                        std::string_view left_key, bool l_exclusive,
                        std::string_view right_key, bool r_exclusive,
                        std::vector<TupleBody*>& result, int64_t limit) {
  result.clear();

  std::vector<Tuple*> scan_res;
  Masstrees[get_storage(s)].scan(
            left_key.empty() ? nullptr : left_key.data(), left_key.size(),
            l_exclusive, right_key.empty() ? nullptr : right_key.data(),
            right_key.size(), r_exclusive, &scan_res, limit,
            callback_);

  std::set<Version*> seen;
  for (auto &&itr : scan_res) {
    // TODO: Tuple should have key? Accessing key through the latest ver is ugly
    // Must be a copy to avoid buffer overflow when changing the latest
    std::string key(itr->latest_.load(memory_order_acquire)->body_.get_key());

    SetElement<Tuple>* we = searchWriteSet(s, key);
    if (we && seen.find(we->ver_) == seen.end()) {
      if (we->op_ != OpType::DELETE) {
        result.emplace_back(&(we->ver_->body_));
        seen.emplace(we->ver_);
      }
      continue;
    }

    Version* v = read_internal(s, key, itr);
    if (this->status_ == TransactionStatus::aborted)
      return Status::ERROR_PREEMPTIVE_ABORT;
    if (v == nullptr || v->status_.load(memory_order_acquire) == VersionStatus::deleted)
      continue;
    if (seen.find(v) == seen.end()) {
      result.emplace_back(&(v->body_));
      seen.emplace(v);
    }
  }

  return Status::OK;
}

/**
 * Serial validation (SSN)
 */
void TxExecutor::ssn_commit() {
  this->status_ = TransactionStatus::committing;
  TransactionTable *tmt = loadAcquire(TMT[thid_]);
  tmt->status_.store(TransactionStatus::committing);

  this->cstamp_ = ++Lsn;
  tmt->cstamp_.store(this->cstamp_, memory_order_release);

  // begin pre-commit
  SsnLock.lock();

  // finalize eta(T)
  for (auto itr = write_set_.begin(); itr != write_set_.end(); ++itr) {
    pstamp_ = max(pstamp_, (*itr).ver_->prev_->psstamp_.atomicLoadPstamp());
  }

  // finalize pi(T)
  sstamp_ = min(sstamp_, cstamp_);
  for (auto itr = read_set_.begin(); itr != read_set_.end(); ++itr) {
    uint32_t verSstamp = (*itr).ver_->psstamp_.atomicLoadSstamp();
    // if the lowest bit raise, the record was overwrited by other concurrent
    // transactions. but in serial SSN, the validation of the concurrent
    // transactions will be done after that of this transaction. So it can skip.
    // And if the lowest bit raise, the stamp is TID;
    if (verSstamp & 1) continue;
    sstamp_ = min(sstamp_, verSstamp >> 1);
  }

  // ssn_check_exclusion
  if (pstamp_ < sstamp_)
    status_ = TransactionStatus::committed;
  else {
    status_ = TransactionStatus::aborted;
    SsnLock.unlock();
    return;
  }

  // update eta
  for (auto itr = read_set_.begin(); itr != read_set_.end(); ++itr) {
    (*itr).ver_->psstamp_.atomicStorePstamp(
            max((*itr).ver_->psstamp_.atomicLoadPstamp(), cstamp_));
    // down readers bit
    downReadersBits((*itr).ver_);
  }

  // update pi
  uint64_t verSstamp = this->sstamp_;
  verSstamp = verSstamp << 1;
  verSstamp &= ~(1);

  uint64_t verCstamp = cstamp_;
  verCstamp = verCstamp << 1;
  verCstamp &= ~(1);

  for (auto itr = write_set_.begin(); itr != write_set_.end(); ++itr) {
    (*itr).ver_->prev_->psstamp_.atomicStoreSstamp(verSstamp);
    // initialize new version
    (*itr).ver_->psstamp_.atomicStorePstamp(cstamp_);
    (*itr).ver_->cstamp_ = verCstamp;
  }

  // logging
  //?*

  // status, inflight -> committed
  for (auto itr = write_set_.begin(); itr != write_set_.end(); ++itr) {
    // memcpy((*itr).ver_->val_, write_val_, VAL_SIZE);
#if ADD_ANALYSIS
    ++result_->local_memcpys;
#endif
    if ((*itr).op_ == OpType::DELETE) {
      Masstrees[get_storage((*itr).storage_)].remove_value((*itr).key_);
      (*itr).ver_->status_.store(VersionStatus::deleted, memory_order_release);
      gcobject_.gcq_for_record_.push_back((*itr).rcdptr_);
    } else {
      (*itr).ver_->status_.store(VersionStatus::committed, memory_order_release);
    }
  }

  this->status_ = TransactionStatus::committed;
  SsnLock.unlock();
  read_set_.clear();
  write_set_.clear();
  node_map_.clear();
  return;
}

/**
 * Parallel validation (latch-free SSN)
 */
void TxExecutor::ssn_parallel_commit() {
#if ADD_ANALYSIS
  uint64_t start(rdtscp());
#endif
  this->status_ = TransactionStatus::committing;
  TransactionTable *tmt = TMT[thid_];
  tmt->status_.store(TransactionStatus::committing);

  this->cstamp_ = ++Lsn;
  // this->cstamp_ = 2; test for effect of centralized counter in YCSB-C (read
  // only workload)

  tmt->cstamp_.store(this->cstamp_, memory_order_release);

  // begin pre-commit

  // finalize pi(T)
  this->sstamp_ = min(this->sstamp_, this->cstamp_);
  for (auto itr = read_set_.begin(); itr != read_set_.end(); ++itr) {
    uint32_t v_sstamp = (*itr).ver_->psstamp_.atomicLoadSstamp();
    // if lowest bits raise, it is TID
    if (v_sstamp & TIDFLAG) {
      // identify worker by using TID
      uint8_t worker = (v_sstamp >> TIDFLAG);
      /**
       * If worker is Inflight state, it will be committed with newer timestamp than this.
       * Then, the worker can't be pi, so skip.
       * If not, check.
       */
      tmt = loadAcquire(TMT[worker]);
      if (tmt->status_.load(memory_order_acquire) ==
          TransactionStatus::committing) {
        /**
         * Worker is in ssn_parallel_commit().
         * So it wait worker to get cstamp.
         */
        while (tmt->cstamp_.load(memory_order_acquire) == 0);
        /**
         * If worker->cstamp_ is less than this->cstamp_, the worker can be pi.
         */
        if (tmt->cstamp_.load(memory_order_acquire) < this->cstamp_) {
          /**
           * It wait worker to end parallel_commit (determine sstamp).
           */
          while (tmt->status_.load(memory_order_acquire) ==
                 TransactionStatus::committing);
          if (tmt->status_.load(memory_order_acquire) ==
              TransactionStatus::committed) {
            this->sstamp_ =
                    min(this->sstamp_, tmt->sstamp_.load(memory_order_acquire));
          }
        }
      }
    } else {
      this->sstamp_ = min(this->sstamp_, v_sstamp >> TIDFLAG);
    }
  }

  /**
   * finalize eta.
   */
  uint64_t one = 1;
  for (auto itr = write_set_.begin(); itr != write_set_.end(); ++itr) {
    if ((*itr).op_ == OpType::INSERT) continue;
    /**
     * for r in v.prev.readers
     */
    Version *ver = (*itr).ver_;
    while (ver->status_.load(memory_order_acquire) != VersionStatus::committed
        && ver->status_.load(memory_order_acquire) != VersionStatus::deleted)
      ver = ver->prev_;
    uint64_t rdrs = ver->readers_.load(memory_order_acquire);
    for (unsigned int worker = 0; worker < TotalThreadNum; ++worker) {
      if ((rdrs & (one << worker)) ? 1 : 0) {
        tmt = loadAcquire(TMT[worker]);
        /**
         * It can ignore if the reader is committing.
         * It can ignore if the reader is inflight because
         * the reader will get larger cstamp and it can't be eta of this.
         */
        if (tmt->status_.load(memory_order_acquire) ==
            TransactionStatus::committing) {
          while (tmt->cstamp_.load(memory_order_acquire) == 0);
          /**
           * If worker->cstamp_ is less than this->cstamp_, it can be eta.
           */
          if (tmt->cstamp_.load(memory_order_acquire) < this->cstamp_) {
            /**
             * Wait end of parallel_commit (determing sstamp).
             */
            while (tmt->status_.load(memory_order_acquire) ==
                   TransactionStatus::committing);
            if (tmt->status_.load(memory_order_acquire) ==
                TransactionStatus::committed) {
              this->pstamp_ =
                      max(this->pstamp_, tmt->cstamp_.load(memory_order_acquire));
            }
          }
        }
      }
    }
    /**
     * Re-read pstamp in case we missed any reader
     */
    this->pstamp_ = max(this->pstamp_, ver->psstamp_.atomicLoadPstamp());
    mergeReaderDependencies(ver);
  }

  tmt = TMT[thid_];
  /**
   * ssn_check_exclusion
   */
  if (pstamp_ < sstamp_) {
    tmt->sstamp_.store(this->sstamp_, memory_order_release);
  } else {
    status_ = TransactionStatus::aborted;
    tmt->status_.store(TransactionStatus::aborted, memory_order_release);
    ccbench::SsnPwalDLogger::instance().logAbortAndFlush(thid_, cstamp_);
    goto FINISH_PARALLEL_COMMIT;
  }

#if ADD_ANALYSIS
  result_->local_vali_latency_ += rdtscp() - start;
  start = rdtscp();
#endif

  // c(T) is also the commit LSN. The transaction is not exposed until its
  // own commit record has reached this worker's WAL file.
  ccbench::SsnPwalDLogger::instance().logCommitAndFlush(
      thid_, cstamp_, write_set_, dep_vector_);
  publishReadDependencies();
  publishWriteDependencies();

  /**
   * update eta.
   */
  for (auto itr = read_set_.begin(); itr != read_set_.end(); ++itr) {
    Psstamp pstmp;
    pstmp.obj_ = (*itr).ver_->psstamp_.atomicLoad();
    while (pstmp.pstamp_ < this->cstamp_) {
      if ((*itr).ver_->psstamp_.atomicCASPstamp(pstmp.pstamp_, this->cstamp_))
        break;
      pstmp.obj_ = (*itr).ver_->psstamp_.atomicLoad();
    }
    downReadersBits((*itr).ver_);
  }

  /**
   * update pi.
   */
  uint64_t verSstamp;
  verSstamp = this->sstamp_ << TIDFLAG;
  verSstamp &= ~(TIDFLAG);

  for (auto itr = write_set_.begin(); itr != write_set_.end(); ++itr) {
    if ((*itr).op_ == OpType::UPDATE) {
      Version *next_committed = (*itr).ver_->prev_;
      while (next_committed->status_.load(memory_order_acquire) != VersionStatus::committed
          && next_committed->status_.load(memory_order_acquire) != VersionStatus::deleted)
        next_committed = next_committed->prev_;
      next_committed->psstamp_.atomicStoreSstamp(verSstamp);
    }
    (*itr).ver_->cstamp_.store(this->cstamp_, memory_order_release);
    (*itr).ver_->psstamp_.atomicStorePstamp(this->cstamp_);
    (*itr).ver_->psstamp_.atomicStoreSstamp(UINT32_MAX & ~(TIDFLAG));
    // memcpy((*itr).ver_->val_, write_val_, VAL_SIZE);
#if ADD_ANALYSIS
    ++result_->local_memcpys;
#endif
    if ((*itr).op_ == OpType::DELETE) {
      Masstrees[get_storage((*itr).storage_)].remove_value((*itr).key_);
      (*itr).ver_->status_.store(VersionStatus::deleted, memory_order_release);
      gcobject_.gcq_for_record_.push_back((*itr).rcdptr_);
    } else {
      (*itr).ver_->status_.store(VersionStatus::committed, memory_order_release);
    }
    gcobject_.gcq_for_version_.emplace_back(
            GCElement((*itr).storage_, (*itr).key_, (*itr).rcdptr_, (*itr).ver_, this->cstamp_));
  }
  // After pushing this commit's GCElements, expose the (possibly new)
  // queue front cstamp so other threads' gcRecord can advance safely.
  gcobject_.publishMinQueuedCstamp();

  // Publish a validated, own-log-durable transaction before waiting for its
  // notification condition, so dependent transactions can form new edges.
  status_ = TransactionStatus::committed;
  tmt->status_.store(TransactionStatus::committed, memory_order_release);
  ccbench::SsnPwalDLogger::instance().waitForCommit(
      thid_, cstamp_, dep_vector_);

  read_set_.clear();
  write_set_.clear();
  node_map_.clear();
  TMT[thid_]->lastcstamp_.store(cstamp_, memory_order_release);

FINISH_PARALLEL_COMMIT:
#if ADD_ANALYSIS
  result_->local_commit_latency_ += rdtscp() - start;
#endif
  return;
}

/**
 * @brief function about abort.
 * clean-up local read/write set.
 * release conceptual lock.
 * @return void
 */
void TxExecutor::abort() {
  for (auto itr = write_set_.begin(); itr != write_set_.end(); ++itr) {
    if ((*itr).op_ == OpType::UPDATE || (*itr).op_ == OpType::DELETE) {
      Version *next_committed = (*itr).ver_->prev_;
      while (next_committed->status_.load(memory_order_acquire) != VersionStatus::committed
          && next_committed->status_.load(memory_order_acquire) != VersionStatus::deleted)
        next_committed = next_committed->prev_;
      /**
       * cancel successor mark(sstamp).
       */
      next_committed->psstamp_.atomicStoreSstamp(UINT32_MAX & ~(TIDFLAG));
    } else {
      // remove inserted records
      Masstrees[get_storage((*itr).storage_)].remove_value((*itr).key_);
      // FIXED_FOR_RC: abortしたINSERTのTupleを即deleteしない。
    // 他スレッドがMasstreeから取得済みのTuple*をまだ参照している可能性があるため。
    // TODO: GCで安全に回収する
      // delete (*itr).rcdptr_;
    }
    (*itr).ver_->status_.store(VersionStatus::aborted, memory_order_release);
  }
  write_set_.clear();

  /**
   * notify that this transaction finishes reading the version now.
   */
  for (auto itr = read_set_.begin(); itr != read_set_.end(); ++itr)
    downReadersBits((*itr).ver_);

  read_set_.clear();
  node_map_.clear();

#if BACK_OFF

#if ADD_ANALYSIS
  uint64_t start = rdtscp();
#endif

  Backoff::backoff(FLAGS_clocks_per_us);

#if ADD_ANALYSIS
  result_->local_backoff_latency_ += rdtscp() - start;
#endif

#endif
}

void TxExecutor::verify_exclusion_or_abort() {
  if (this->pstamp_ >= this->sstamp_) {
    this->status_ = TransactionStatus::aborted;
    TransactionTable *tmt = loadAcquire(TMT[thid_]);
    tmt->status_.store(TransactionStatus::aborted, memory_order_release);
  }
}

void TxExecutor::mainte() {
  gcstop_ = rdtscp();
  if (chkClkSpan(gcstart_, gcstop_, FLAGS_gc_inter_us * FLAGS_clocks_per_us)) {
    uint32_t loadThreshold = gcobject_.getGcThreshold();
    if (pre_gc_threshold_ != loadThreshold) {
#if ADD_ANALYSIS
      uint64_t start;
      start = rdtscp();
      ++result_->local_gc_counts_;
#endif
      gcobject_.gcTMTelement(result_);
      gcobject_.gcVersion(result_);
      // gcRecord uses MinQueuedCstamp[] (published by every thread on
      // push to and pop from gcq_for_version_) to defer Tuple free
      // until no thread can still hold a reference.
      gcobject_.gcRecord();
      pre_gc_threshold_ = loadThreshold;
      gcstart_ = gcstop_;
#if ADD_ANALYSIS
      result_->local_gc_latency_ += rdtscp() - start;
#endif
    }
  }
}

// TODO: enable this if we want to use
// void TxExecutor::dispWS() {
//   cout << "th " << this->thid_ << " : write set : ";
//   for (auto itr = write_set_.begin(); itr != write_set_.end(); ++itr) {
//     cout << "(" << (*itr).key_ << ", " << (*itr).ver_->val_ << "), ";
//   }
//   cout << endl;
// }

// void TxExecutor::dispRS() {
//   cout << "th " << this->thid_ << " : read set : ";
//   for (auto itr = read_set_.begin(); itr != read_set_.end(); ++itr) {
//     cout << "(" << (*itr).key_ << ", " << (*itr).ver_->val_ << "), ";
//   }
//   cout << endl;
// }

bool TxExecutor::commit() {
  ssn_parallel_commit();
  if (status_ == TransactionStatus::aborted)
    return false;

  /**
   * Maintenance phase
   */
  mainte();
  return true;
}

bool TxExecutor::isLeader() {
  return this->thid_ == 0;
}

void TxExecutor::leaderWork() {
  if (gcob.chkSecondRange()) {
    gcob.decideGcThreshold();
    gcob.mvSecondRangeToFirstRange();
  }
#if BACK_OFF
  leaderBackoffWork(backoff_, RCSSNResult);
#endif
}

void TxExecutor::reconnoiter_begin() {
  reconnoitering_ = true;
}

void TxExecutor::reconnoiter_end() {
  read_set_.clear();
  node_map_.clear();
  reconnoitering_ = false;
  begin();
}

void TxScanCallback::on_resp_node(const MasstreeWrapper<Tuple>::node_type *n, uint64_t version) {
  (void)n;
  (void)version;
}
