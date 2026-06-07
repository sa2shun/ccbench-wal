#include <cstdint>
#include <iostream>
#include <string>
#include <vector>

#include "../include/wal_frontier.hh"

namespace {

using ccbench::WalFrontier;

struct Txn {
  std::string name;
  uint64_t cstamp = 0;
  uint64_t global_lsn = 0;
  uint32_t log_id = 0;
  uint64_t local_seq = 0;
  WalFrontier dep;
  WalFrontier closed;
};

struct VersionModel {
  WalFrontier write_frontier;
  WalFrontier read_frontier;
};

struct Row {
  std::string mode;
  bool independent_reversed_flush = false;
  bool dependent_reversed_flush = false;
  bool transitive_frontier = false;
  bool acked_recovered = false;
  bool dependency_violation = false;
};

WalFrontier closeFrontier(const WalFrontier& dep, uint32_t log_id, uint64_t local_seq) {
  WalFrontier closed = dep;
  closed.setMax(log_id, local_seq);
  return closed;
}

bool durable(const Txn& t, const WalFrontier& durable_seq) {
  return durable_seq.get(t.log_id) >= t.local_seq;
}

bool depClosed(const Txn& t, const WalFrontier& durable_seq) {
  for (uint32_t i = 0; i < 2; ++i) {
    if (durable_seq.get(i) < t.dep.get(i)) return false;
  }
  return true;
}

bool canAckLocalOnly(const Txn& t, const WalFrontier& durable_seq) {
  return durable(t, durable_seq);
}

uint64_t durableGlobalPrefix(const std::vector<Txn>& txns, const WalFrontier& durable_seq) {
  std::vector<uint8_t> completed(txns.size() + 1, 0);
  for (const Txn& t : txns) {
    if (durable(t, durable_seq)) completed[t.global_lsn] = 1;
  }
  uint64_t prefix = 0;
  while (prefix + 1 < completed.size() && completed[prefix + 1] != 0) {
    ++prefix;
  }
  return prefix;
}

bool canAckGlobalPrefix(const Txn& t, const std::vector<Txn>& txns,
                        const WalFrontier& durable_seq) {
  return durableGlobalPrefix(txns, durable_seq) >= t.global_lsn;
}

bool canAckDepFrontier(const Txn& t, const WalFrontier& durable_seq) {
  return durable(t, durable_seq) && depClosed(t, durable_seq);
}

bool recoveryContains(const Txn& t, const WalFrontier& durable_seq) {
  return durable(t, durable_seq) && depClosed(t, durable_seq);
}

Row evaluateMode(const std::string& mode) {
  VersionModel x;
  VersionModel y;

  Txn u;
  u.name = "U";
  u.cstamp = 10;
  u.global_lsn = 1;
  u.log_id = 0;
  u.local_seq = 1;
  u.closed = closeFrontier(u.dep, u.log_id, u.local_seq);
  x.write_frontier = u.closed;

  Txn t;
  t.name = "T";
  t.cstamp = 11;
  t.global_lsn = 2;
  t.log_id = 1;
  t.local_seq = 1;
  t.dep.merge(x.write_frontier);
  t.closed = closeFrontier(t.dep, t.log_id, t.local_seq);
  y.write_frontier = t.closed;

  Txn v;
  v.name = "V";
  v.cstamp = 12;
  v.global_lsn = 3;
  v.log_id = 0;
  v.local_seq = 2;
  v.dep.merge(y.write_frontier);
  v.closed = closeFrontier(v.dep, v.log_id, v.local_seq);

  Txn i0;
  i0.name = "I0";
  i0.cstamp = 20;
  i0.global_lsn = 1;
  i0.log_id = 0;
  i0.local_seq = 1;
  i0.closed = closeFrontier(i0.dep, i0.log_id, i0.local_seq);

  Txn i1;
  i1.name = "I1";
  i1.cstamp = 21;
  i1.global_lsn = 2;
  i1.log_id = 1;
  i1.local_seq = 1;
  i1.closed = closeFrontier(i1.dep, i1.log_id, i1.local_seq);

  WalFrontier only_t_durable;
  only_t_durable.setMax(1, 1);
  WalFrontier both_durable;
  both_durable.setMax(0, 1);
  both_durable.setMax(1, 1);
  WalFrontier only_i1_durable;
  only_i1_durable.setMax(1, 1);
  WalFrontier both_i_durable = both_durable;

  const std::vector<Txn> dependent{u, t};
  const std::vector<Txn> independent{i0, i1};

  auto can_ack = [&](const Txn& tx, const std::vector<Txn>& all,
                     const WalFrontier& durable_seq) {
    if (mode == "local-only") return canAckLocalOnly(tx, durable_seq);
    if (mode == "global-lsn-prefix") return canAckGlobalPrefix(tx, all, durable_seq);
    return canAckDepFrontier(tx, durable_seq);
  };

  Row r;
  r.mode = mode;

  const bool ack_i1_before_i0 = can_ack(i1, independent, only_i1_durable);
  r.independent_reversed_flush =
      (!ack_i1_before_i0 || recoveryContains(i1, only_i1_durable)) &&
      can_ack(i1, independent, both_i_durable);

  const bool ack_t_before_u = can_ack(t, dependent, only_t_durable);
  const bool ack_t_after_u = can_ack(t, dependent, both_durable);
  r.dependent_reversed_flush = !ack_t_before_u && ack_t_after_u;

  r.transitive_frontier = v.dep.get(0) >= 1 && v.dep.get(1) >= 1;
  r.acked_recovered = !ack_t_after_u || recoveryContains(t, both_durable);
  r.dependency_violation = ack_t_before_u && !recoveryContains(t, only_t_durable);

  if (mode == "local-only") {
    r.dependent_reversed_flush = !r.dependency_violation;
    r.acked_recovered = false;
  }
  return r;
}

const char* yn(bool v) { return v ? "yes" : "no"; }
const char* passfail(bool v) { return v ? "pass" : "fail"; }

}  // namespace

int main() {
  const std::vector<std::string> modes{
      "local-only",
      "global-lsn-prefix",
      "dep-frontier-lsn",
      "dep-frontier-cstamp",
  };

  std::cout << "# ERMIA Cstamp-PWAL deterministic correctness test\n\n";
  std::cout << "Scenario: U writes x, T reads x and writes y, so U -> T. "
               "T's log is durable before U's log.\n\n";
  std::cout << "| mode | independent reversed flush | dependent reversed flush | "
               "transitive frontier | acked recovered | dependency violation |\n";
  std::cout << "|---|---|---|---|---|---|\n";

  bool ok = true;
  for (const std::string& mode : modes) {
    const Row r = evaluateMode(mode);
    std::cout << "| " << r.mode << " | "
              << passfail(r.independent_reversed_flush) << " | "
              << passfail(r.dependent_reversed_flush) << " | "
              << passfail(r.transitive_frontier) << " | "
              << yn(r.acked_recovered) << " | "
              << yn(r.dependency_violation) << " |\n";
    if (mode == "local-only") {
      ok = ok && r.independent_reversed_flush && !r.dependent_reversed_flush &&
           r.transitive_frontier && !r.acked_recovered && r.dependency_violation;
    } else {
      ok = ok && r.independent_reversed_flush && r.dependent_reversed_flush &&
           r.transitive_frontier && r.acked_recovered && !r.dependency_violation;
    }
  }

  if (!ok) {
    std::cerr << "ERMIA Cstamp-PWAL correctness test failed\n";
    return 1;
  }
  return 0;
}
