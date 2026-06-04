#pragma once

#include <atomic>
#include <chrono>
#include <cstdint>
#include <iostream>

namespace ccbench {

class TxBreakdownProfiler {
 public:
  enum class Phase : uint8_t {
    Read,
    Update,
    Insert,
    DeleteRecord,
    AbortCleanup,
    Maintenance,
    SsnFinalizePi,
    SsnFinalizeEta,
    SsnExclusion,
    NodeValidation,
    WalLog,
    VersionInstall,
    CommitCleanup,
    Count,
  };

  static TxBreakdownProfiler& instance() {
    static TxBreakdownProfiler profiler;
    return profiler;
  }

  static uint64_t nowNs() {
    return static_cast<uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now().time_since_epoch()).count());
  }

  void add(Phase phase, uint64_t ns) {
    values_[static_cast<size_t>(phase)].fetch_add(ns, std::memory_order_relaxed);
  }

  class ScopedTimer {
   public:
    explicit ScopedTimer(Phase phase) : phase_(phase), start_(nowNs()) {}
    ~ScopedTimer() { TxBreakdownProfiler::instance().add(phase_, nowNs() - start_); }

   private:
    Phase phase_;
    uint64_t start_;
  };

  ~TxBreakdownProfiler() { printStats(); }

 private:
  TxBreakdownProfiler() = default;

  static const char* name(Phase phase) {
    switch (phase) {
      case Phase::Read:
        return "tx_breakdown_read_ns";
      case Phase::Update:
        return "tx_breakdown_update_ns";
      case Phase::Insert:
        return "tx_breakdown_insert_ns";
      case Phase::DeleteRecord:
        return "tx_breakdown_delete_ns";
      case Phase::AbortCleanup:
        return "tx_breakdown_abort_cleanup_ns";
      case Phase::Maintenance:
        return "tx_breakdown_maintenance_ns";
      case Phase::SsnFinalizePi:
        return "tx_breakdown_ssn_finalize_pi_ns";
      case Phase::SsnFinalizeEta:
        return "tx_breakdown_ssn_finalize_eta_ns";
      case Phase::SsnExclusion:
        return "tx_breakdown_ssn_exclusion_ns";
      case Phase::NodeValidation:
        return "tx_breakdown_node_validation_ns";
      case Phase::WalLog:
        return "tx_breakdown_wal_log_ns";
      case Phase::VersionInstall:
        return "tx_breakdown_version_install_ns";
      case Phase::CommitCleanup:
        return "tx_breakdown_commit_cleanup_ns";
      case Phase::Count:
        break;
    }
    return "tx_breakdown_unknown_ns";
  }

  void printStats() const {
    uint64_t total = 0;
    for (size_t i = 0; i < static_cast<size_t>(Phase::Count); ++i) {
      total += values_[i].load(std::memory_order_acquire);
    }
    if (total == 0) return;
    for (size_t i = 0; i < static_cast<size_t>(Phase::Count); ++i) {
      const auto phase = static_cast<Phase>(i);
      std::cout << name(phase) << ":\t"
                << values_[i].load(std::memory_order_acquire) << std::endl;
    }
    std::cout << "tx_breakdown_total_accounted_ns:\t" << total << std::endl;
  }

  std::atomic<uint64_t> values_[static_cast<size_t>(Phase::Count)]{};
};

}  // namespace ccbench
