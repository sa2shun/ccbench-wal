#pragma once

#include <atomic>
#include <cstdint>
#include <memory>

#include "../../../include/cache_line_size.hh"
#include "../../../include/tuple_body.hh"
#include "../../../include/wal_frontier.hh"

#define TIDFLAG 1

enum class VersionStatus : uint8_t {
  inflight,
  committed,
  aborted,
  deleted,
};

struct Psstamp {
  union {
    uint64_t obj_;
    struct {
      uint32_t pstamp_;
      uint32_t sstamp_;
    };
  };

  Psstamp() { obj_ = 0; }

  void init(uint32_t pstamp, uint32_t sstamp) {
    pstamp_ = pstamp;
    sstamp_ = sstamp;
  }

  uint64_t atomicLoad() {
    Psstamp expected;
    expected.obj_ = __atomic_load_n(&obj_, __ATOMIC_ACQUIRE);
    return expected.obj_;
  }

  bool atomicCASPstamp(uint32_t expectedPstamp, uint32_t desiredPstamp) {
    Psstamp expected, desired;
    expected.obj_ = __atomic_load_n(&obj_, __ATOMIC_ACQUIRE);
    expected.pstamp_ = expectedPstamp;
    desired = expected;
    desired.pstamp_ = desiredPstamp;
    if (__atomic_compare_exchange_n(&obj_, &expected.obj_, desired.obj_, false,
                                    __ATOMIC_ACQ_REL, __ATOMIC_RELAXED))
      return true;
    else
      return false;
  }

  uint32_t atomicLoadPstamp() {
    Psstamp expected;
    expected.obj_ = __atomic_load_n(&obj_, __ATOMIC_ACQUIRE);
    return expected.pstamp_;
  }

  uint32_t atomicLoadSstamp() {
    Psstamp expected;
    expected.obj_ = __atomic_load_n(&obj_, __ATOMIC_ACQUIRE);
    return expected.sstamp_;
  }

  void atomicStorePstamp(uint32_t newpstamp) {
    Psstamp expected, desired;
    expected.obj_ = __atomic_load_n(&obj_, __ATOMIC_ACQUIRE);
    for (;;) {
      desired = expected;
      desired.pstamp_ = newpstamp;
      if (__atomic_compare_exchange_n(&obj_, &expected.obj_, desired.obj_,
                                      false, __ATOMIC_ACQ_REL,
                                      __ATOMIC_ACQUIRE))
        break;
    }
  }

  void atomicStoreSstamp(uint32_t newsstamp) {
    Psstamp expected, desired;
    expected.obj_ = __atomic_load_n(&obj_, __ATOMIC_ACQUIRE);
    for (;;) {
      desired = expected;
      desired.sstamp_ = newsstamp;
      if (__atomic_compare_exchange_n(&obj_, &expected.obj_, desired.obj_,
                                      false, __ATOMIC_ACQ_REL,
                                      __ATOMIC_ACQUIRE))
        break;
    }
  }
};

class Version {
public:
  alignas(CACHE_LINE_SIZE) Psstamp
          psstamp_;  // Version access stamp, eta(V), Version successor stamp, pi(V)
  Version *prev_;                  // Pointer to overwritten version
  std::atomic <uint64_t> readers_;  // summarize all of V's readers.
  std::atomic <uint32_t> cstamp_;   // Version creation stamp, c(V)
  std::atomic <VersionStatus> status_;

  TupleBody body_;

  // Inline durability-dependency frontier (Ayame dependency-frontier mode).
  // Each shard entry is an independent, monotonically increasing durable-LSN
  // requirement, so lock-free per-entry atomic access is correct: a reader only
  // needs each shard's requirement (there is no cross-shard snapshot invariant)
  // and the merge is an element-wise max.  This replaces the per-version
  // std::shared_ptr<WalFrontier> (atomic ref-count + libstdc++ global spinlock)
  // and avoids any reader/writer spin, which otherwise starved the flusher and
  // committer threads under oversubscription.  Kept after the hot SSN fields and
  // the tuple body so it does not displace them from cache lines.
  std::atomic<uint64_t> write_frontier_[ccbench::kInlineFrontierShards];
  std::atomic<uint64_t> read_frontier_[ccbench::kInlineFrontierShards];

  Version() { init(); }

  void init() {
    psstamp_.init(0, UINT32_MAX & ~(TIDFLAG));
    status_.store(VersionStatus::inflight, std::memory_order_release);
    readers_.store(0, std::memory_order_release);
    for (uint32_t i = 0; i < ccbench::kInlineFrontierShards; ++i) {
      write_frontier_[i].store(0, std::memory_order_relaxed);
      read_frontier_[i].store(0, std::memory_order_relaxed);
    }
  }

  // Lock-free per-entry accessors.
  uint64_t loadWriteFrontier(uint32_t i) const {
    return write_frontier_[i].load(std::memory_order_acquire);
  }
  uint64_t loadReadFrontier(uint32_t i) const {
    return read_frontier_[i].load(std::memory_order_acquire);
  }
  // Set write_frontier_[i] (single publisher: the version's creator at commit).
  void storeWriteFrontier(uint32_t i, uint64_t v) {
    write_frontier_[i].store(v, std::memory_order_release);
  }
  // Monotonic max into read_frontier_[i] (multiple readers may publish).
  void maxReadFrontier(uint32_t i, uint64_t v) {
    uint64_t cur = read_frontier_[i].load(std::memory_order_relaxed);
    while (cur < v &&
           !read_frontier_[i].compare_exchange_weak(
               cur, v, std::memory_order_release, std::memory_order_relaxed)) {
    }
  }
};
