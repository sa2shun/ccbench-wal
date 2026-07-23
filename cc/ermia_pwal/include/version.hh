#pragma once

#include <atomic>
#include <cstdint>
#include <memory>

#include "../../../include/cache_line_size.hh"
#include "../../../include/readers_bitmap.hh"
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
  ccbench::ReadersBitmap readers_;  // summarize all of V's readers.
  std::atomic <uint32_t> cstamp_;   // Version creation stamp, c(V)
  std::atomic <VersionStatus> status_;
  // Allocation-free inline frontiers (see InlineFrontier in wal_frontier.hh
  // for why per-entry atomic max needs no seqlock or shared_ptr).
  ccbench::InlineFrontier write_frontier_;
  ccbench::InlineFrontier read_frontier_;

  TupleBody body_;

  Version() { init(); }

  void init() {
    psstamp_.init(0, UINT32_MAX & ~(TIDFLAG));
    status_.store(VersionStatus::inflight, std::memory_order_release);
    readers_.reset();
    write_frontier_.reset();
    read_frontier_.reset();
    // (InlineFrontier::reset is a fixed-size relaxed-store loop; no free/alloc)
  }
};
