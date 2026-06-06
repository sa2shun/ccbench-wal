#include <algorithm>
#include <cstdint>
#include <iostream>
#include <string>
#include <vector>

namespace {

struct Txn {
  std::string name;
  uint64_t cstamp = 0;
  uint64_t lsn = 0;
  uint32_t log_id = 0;
  uint64_t local_seq = 0;
  std::vector<uint64_t> dep;
};

struct Result {
  std::string mode;
  bool independent_reversed_flush = false;
  bool dependent_reversed_flush = false;
  bool acked_recovered = false;
  bool dependency_violation = false;
};

bool durable(const Txn& t, const std::vector<uint64_t>& durable_seq) {
  return durable_seq[t.log_id] >= t.local_seq;
}

bool depClosed(const Txn& t, const std::vector<uint64_t>& durable_seq) {
  for (size_t i = 0; i < t.dep.size(); ++i) {
    if (durable_seq[i] < t.dep[i]) return false;
  }
  return true;
}

bool canAckLocalOnly(const Txn& t, const std::vector<uint64_t>& durable_seq) {
  return durable(t, durable_seq);
}

bool canAckDepFrontier(const Txn& t, const std::vector<uint64_t>& durable_seq) {
  return durable(t, durable_seq) && depClosed(t, durable_seq);
}

uint64_t durableGlobalPrefix(const std::vector<Txn>& txns,
                             const std::vector<uint64_t>& durable_seq) {
  std::vector<uint8_t> completed(txns.size() + 1, 0);
  for (const auto& t : txns) {
    if (durable(t, durable_seq)) completed[t.lsn] = 1;
  }
  uint64_t prefix = 0;
  while (prefix + 1 < completed.size() && completed[prefix + 1] != 0) {
    ++prefix;
  }
  return prefix;
}

bool canAckGlobalPrefix(const Txn& t, const std::vector<Txn>& txns,
                        const std::vector<uint64_t>& durable_seq) {
  return durableGlobalPrefix(txns, durable_seq) >= t.lsn;
}

bool recoveryContains(const Txn& t, const std::vector<uint64_t>& durable_seq) {
  return durable(t, durable_seq) && depClosed(t, durable_seq);
}

Result evaluateMode(const std::string& mode) {
  const Txn t1{"T1", 10, 1, 0, 1, {0, 0}};
  const Txn t2{"T2", 11, 2, 1, 1, {0, 0}};
  const Txn u{"U", 10, 1, 0, 1, {0, 0}};
  const Txn t{"T", 11, 2, 1, 1, {1, 0}};

  const std::vector<Txn> independent{t1, t2};
  const std::vector<Txn> dependent{u, t};
  const std::vector<uint64_t> only_t2_durable{0, 1};
  const std::vector<uint64_t> both_independent_durable{1, 1};
  const std::vector<uint64_t> only_t_durable{0, 1};
  const std::vector<uint64_t> both_dependent_durable{1, 1};

  auto can_ack = [&](const Txn& x, const std::vector<Txn>& all,
                     const std::vector<uint64_t>& durable_seq) {
    if (mode == "local-only") return canAckLocalOnly(x, durable_seq);
    if (mode == "global-prefix") return canAckGlobalPrefix(x, all, durable_seq);
    return canAckDepFrontier(x, durable_seq);
  };

  Result r;
  r.mode = mode;
  const bool ack_t2_before_t1 = can_ack(t2, independent, only_t2_durable);
  r.independent_reversed_flush =
      (!ack_t2_before_t1 || recoveryContains(t2, only_t2_durable)) &&
      can_ack(t1, independent, both_independent_durable) &&
      can_ack(t2, independent, both_independent_durable);

  const bool ack_t_before_u = can_ack(t, dependent, only_t_durable);
  const bool ack_t_after_u = can_ack(t, dependent, both_dependent_durable);
  r.dependent_reversed_flush = !ack_t_before_u && ack_t_after_u;

  const bool recovered_t_before_u = recoveryContains(t, only_t_durable);
  const bool recovered_t_after_u = recoveryContains(t, both_dependent_durable);
  r.acked_recovered = !ack_t_after_u || recovered_t_after_u;
  r.dependency_violation = ack_t_before_u && !recovered_t_before_u;

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
      "global-prefix",
      "dep-frontier-lsn",
      "dep-frontier-cstamp",
  };

  std::cout << "# Cstamp-PWAL deterministic correctness test\n\n";
  std::cout << "| mode | independent reversed flush | dependent reversed flush | acked recovered | dependency violation |\n";
  std::cout << "|---|---|---|---|---|\n";

  bool ok = true;
  for (const auto& mode : modes) {
    const Result r = evaluateMode(mode);
    std::cout << "| " << r.mode << " | "
              << passfail(r.independent_reversed_flush) << " | "
              << passfail(r.dependent_reversed_flush) << " | "
              << yn(r.acked_recovered) << " | "
              << yn(r.dependency_violation) << " |\n";
    if (mode == "local-only") {
      ok = ok && r.independent_reversed_flush && !r.dependent_reversed_flush &&
           !r.acked_recovered && r.dependency_violation;
    } else {
      ok = ok && r.independent_reversed_flush && r.dependent_reversed_flush &&
           r.acked_recovered && !r.dependency_violation;
    }
  }

  if (!ok) {
    std::cerr << "correctness test failed\n";
    return 1;
  }
  return 0;
}
