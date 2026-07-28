#pragma once

#include <atomic>
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
  alignas(CACHE_LINE_SIZE) Psstamp
      psstamp_; // Version access stamp, eta(V), Version successor stamp, pi(V)
  Version* prev_;                 // Pointer to overwritten version
  // 全readerのビットマップ。128bit(2ワード)でスレッド数128まで対応。
  // word = thid/64, bit = thid%64。旧実装はuint64 1本+int シフトで
  // thid>=31が未定義動作/ビット衝突になっていたため修正。
  std::atomic<uint64_t> readers_[2];
  std::atomic<uint32_t> cstamp_;  // Version creation stamp, c(V)
  std::atomic<VersionStatus> status_;
  // Ayame: このバージョンを書いたワーカのスレッドID。依存待ちの宛先特定に使う。
  // インストール時(書き手スレッド)に設定される。初期ロードのバージョンは
  // cstamp==0 のため依存として参照されず、初期値のままでよい。
  uint8_t writer_thid_ = 0;

  TupleBody body_;

  Version() { init(); }

  void init() {
    psstamp_.init(0, UINT32_MAX & ~(TIDFLAG));
    status_.store(VersionStatus::inflight, std::memory_order_release);
    readers_[0].store(0, std::memory_order_release);
    readers_[1].store(0, std::memory_order_release);
  }
};
