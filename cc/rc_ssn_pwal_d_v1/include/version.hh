#pragma once

#include <atomic>
#include <array>
#include <cstdint>

#include "../../../include/cache_line_size.hh"
#include "../../../include/tuple_body.hh"

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
  static constexpr size_t kDurabilityWorkers = 64;
  alignas(CACHE_LINE_SIZE) Psstamp
          psstamp_;  // Version access stamp, eta(V), Version successor stamp, pi(V)
  Version *prev_;                  // Pointer to overwritten version
  std::atomic <uint64_t> readers_;  // summarize all of V's readers.
  std::atomic <uint32_t> cstamp_;   // Version creation stamp, c(V)
  std::atomic <VersionStatus> status_;
#if CCBENCH_SSN_PWAL_WAIT_MODE != 0
  std::atomic<uint32_t> creator_worker_;
  std::array<std::atomic<uint32_t>, kDurabilityWorkers> durable_deps_;
  std::array<std::atomic<uint32_t>, kDurabilityWorkers> reader_deps_;
#endif

  TupleBody body_;

  Version() { init(); }

  void init() {
    psstamp_.init(0, UINT32_MAX & ~(TIDFLAG));
    status_.store(VersionStatus::inflight, std::memory_order_release);
    readers_.store(0, std::memory_order_release);
#if CCBENCH_SSN_PWAL_WAIT_MODE != 0
    creator_worker_.store(UINT32_MAX, std::memory_order_relaxed);
    for (size_t worker = 0; worker < kDurabilityWorkers; ++worker) {
      durable_deps_[worker].store(0, std::memory_order_relaxed);
      reader_deps_[worker].store(0, std::memory_order_relaxed);
    }
#endif
  }
};
