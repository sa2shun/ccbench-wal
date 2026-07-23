#pragma once

#include <atomic>
#include <cstdint>

namespace ccbench {

// Multi-word reader bitmap. The historical single uint64_t readers_ field
// silently broke for thid >= 32 ("1 << thid_" is an int shift) and cannot
// represent more than 64 workers at all. This supports kMaxReaderThreads.
inline constexpr uint32_t kMaxReaderThreads = 256;

class ReadersBitmap {
 public:
  static constexpr uint32_t kWords = kMaxReaderThreads / 64;

  void reset() {
    for (auto& w : words_) w.store(0, std::memory_order_release);
  }

  void set(uint32_t thid) {
    words_[thid >> 6].fetch_or(1ULL << (thid & 63), std::memory_order_acq_rel);
  }

  void clear(uint32_t thid) {
    words_[thid >> 6].fetch_and(~(1ULL << (thid & 63)),
                                std::memory_order_acq_rel);
  }

  bool test(uint32_t thid) const {
    return words_[thid >> 6].load(std::memory_order_acquire) &
           (1ULL << (thid & 63));
  }

  bool any() const {
    for (const auto& w : words_) {
      if (w.load(std::memory_order_acquire) != 0) return true;
    }
    return false;
  }

  uint64_t word(uint32_t idx) const {
    return words_[idx].load(std::memory_order_acquire);
  }

 private:
  std::atomic<uint64_t> words_[kWords] = {};
};

}  // namespace ccbench
