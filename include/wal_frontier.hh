#pragma once

#include <algorithm>
#include <cstdint>
#include <vector>

namespace ccbench {

constexpr uint32_t kWalFrontierMaxShards = 256;

// Per-version inline frontier capacity (Ayame dependency-frontier mode).  The
// frontier is indexed by WAL shard (= flusher), and Ayame uses far fewer shards
// than worker threads, so a small fixed inline array can replace the heap
// std::shared_ptr.  Must be >= the largest flusher count used with the
// dependency-frontier acknowledgment.
constexpr uint32_t kInlineFrontierShards = 64;

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

}  // namespace ccbench
