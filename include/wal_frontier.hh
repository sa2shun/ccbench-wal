#pragma once

#include <algorithm>
#include <atomic>
#include <cstdint>
#include <vector>

namespace ccbench {

constexpr uint32_t kWalFrontierMaxShards = 256;

// Capacity of the per-version inline frontier arrays. Must be >= the runtime
// shard count (CCBENCH_WAL_LOGGER_NUM) when a dep-frontier mode is used;
// WalLogger::configure() enforces this. Override at build time with
// -DCCBENCH_INLINE_FRONTIER_SHARDS=<n> if you need more shards (each version
// carries 2 * 8 * n bytes of frontier storage).
#ifndef CCBENCH_INLINE_FRONTIER_SHARDS
#define CCBENCH_INLINE_FRONTIER_SHARDS 128
#endif
constexpr uint32_t kWalFrontierInlineShards = CCBENCH_INLINE_FRONTIER_SHARDS;

struct WalFrontier {
  std::vector<uint64_t> seq;

  WalFrontier() = default;
  explicit WalFrontier(uint32_t shard_count) { reset(shard_count); }

  void reset(uint32_t shard_count = 0) {
    const uint32_t n = std::min<uint32_t>(shard_count, kWalFrontierMaxShards);
    seq.assign(n, 0);
  }

  void ensureSize(uint32_t shard_count) {
    const uint32_t n = std::min<uint32_t>(shard_count, kWalFrontierMaxShards);
    if (seq.size() < n) seq.resize(n, 0);
  }

  uint64_t get(uint32_t shard) const {
    return shard < seq.size() ? seq[shard] : 0;
  }

  size_t sizeBytes(uint32_t shard_count = 0) const {
    uint32_t n = shard_count ? shard_count : static_cast<uint32_t>(seq.size());
    n = std::min<uint32_t>(n, kWalFrontierMaxShards);
    return static_cast<size_t>(std::min<uint32_t>(
        n, static_cast<uint32_t>(seq.size()))) * sizeof(uint64_t);
  }

  uint32_t entries(uint32_t shard_count = 0) const {
    uint32_t n = shard_count ? shard_count : static_cast<uint32_t>(seq.size());
    n = std::min<uint32_t>(n, kWalFrontierMaxShards);
    return std::min<uint32_t>(n, static_cast<uint32_t>(seq.size()));
  }

  uint32_t nonzeroEntries(uint32_t shard_count = 0) const {
    const uint32_t n = entries(shard_count);
    uint32_t count = 0;
    for (uint32_t i = 0; i < n; ++i) {
      if (seq[i] != 0) ++count;
    }
    return count;
  }

  bool allZero(uint32_t shard_count = 0) const {
    return nonzeroEntries(shard_count) == 0;
  }

  bool covers(const WalFrontier& other, uint32_t shard_count = 0) const {
    uint32_t n = shard_count;
    if (n == 0) {
      n = static_cast<uint32_t>(std::max(seq.size(), other.seq.size()));
    }
    n = std::min<uint32_t>(n, kWalFrontierMaxShards);
    for (uint32_t i = 0; i < n; ++i) {
      if (get(i) < other.get(i)) return false;
    }
    return true;
  }

  void merge(const WalFrontier& other, uint32_t shard_count = 0) {
    uint32_t n = shard_count;
    if (n == 0) {
      n = static_cast<uint32_t>(std::max(seq.size(), other.seq.size()));
    }
    n = std::min<uint32_t>(n, kWalFrontierMaxShards);
    ensureSize(n);
    for (uint32_t i = 0; i < n; ++i) {
      seq[i] = std::max(seq[i], other.get(i));
    }
  }

  void setMax(uint32_t shard, uint64_t value) {
    if (shard >= kWalFrontierMaxShards) return;
    ensureSize(shard + 1);
    seq[shard] = std::max(seq[shard], value);
  }
};

// Allocation-free per-version frontier, replacing the previous
// shared_ptr<const WalFrontier> publish/collect scheme (whose refcount
// traffic and per-publish heap allocation dominated the collect cost).
//
// No seqlock is needed: every entry is an independently monotone per-shard
// high-water mark ("shard i must be durable up to seq[i]"), and no invariant
// spans multiple entries. A reader that observes some entries before and some
// after a concurrent merge still gets a valid frontier — each entry it reads
// is a value some publisher legitimately required. Ordering with respect to
// version visibility comes from the existing status_ release/acquire pairs
// (frontiers are published before status_ is set to committed).
class InlineFrontier {
 public:
  void reset() {
    for (auto& e : entries_) e.store(0, std::memory_order_relaxed);
  }

  // Publisher that exclusively owns the version (write frontier, published
  // once before the version becomes visible): plain stores.
  void storeFrom(const WalFrontier& src, uint32_t shard_count) {
    const uint32_t n = capped(shard_count);
    for (uint32_t i = 0; i < n; ++i) {
      entries_[i].store(src.get(i), std::memory_order_relaxed);
    }
  }

  // Concurrent monotone merge (read frontier: many committing readers can
  // publish into the same version). Per-entry CAS-max, no lock.
  void mergeFrom(const WalFrontier& src, uint32_t shard_count) {
    const uint32_t n = capped(shard_count);
    for (uint32_t i = 0; i < n; ++i) {
      const uint64_t v = src.get(i);
      if (v == 0) continue;
      uint64_t cur = entries_[i].load(std::memory_order_relaxed);
      while (cur < v &&
             !entries_[i].compare_exchange_weak(cur, v,
                                                std::memory_order_release,
                                                std::memory_order_relaxed)) {
      }
    }
  }

  bool covers(const WalFrontier& other, uint32_t shard_count) const {
    const uint32_t n = capped(shard_count);
    for (uint32_t i = 0; i < n; ++i) {
      if (entries_[i].load(std::memory_order_acquire) < other.get(i)) {
        return false;
      }
    }
    return true;
  }

  // Collect: fold this frontier into dst (dst[i] = max(dst[i], entries_[i])).
  void mergeInto(WalFrontier& dst, uint32_t shard_count) const {
    const uint32_t n = capped(shard_count);
    dst.ensureSize(n);
    for (uint32_t i = 0; i < n; ++i) {
      const uint64_t v = entries_[i].load(std::memory_order_acquire);
      if (v > dst.seq[i]) dst.seq[i] = v;
    }
  }

 private:
  static uint32_t capped(uint32_t shard_count) {
    return std::min(shard_count, kWalFrontierInlineShards);
  }

  std::atomic<uint64_t> entries_[kWalFrontierInlineShards] = {};
};

}  // namespace ccbench
