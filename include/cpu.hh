#pragma once

#include <cpuid.h>
#include <cstdlib>
#include <sys/syscall.h>
#include <sys/types.h>
#include <unistd.h>
#include <vector>

#include "debug.hh"

#define CPUID(INFO, LEAF, SUBLEAF) \
  __cpuid_count(LEAF, SUBLEAF, INFO[0], INFO[1], INFO[2], INFO[3])

#define GETCPU(CPU)                                                 \
  {                                                                 \
    uint32_t CPUInfo[4];                                            \
    CPUID(CPUInfo, 1, 0);                                           \
    /* CPUInfo[1] is EBX, bits 24-31 are APIC ID */                 \
    if ((CPUInfo[3] & (1 << 9)) == 0) {                             \
      CPU = -1; /* no APIC on chip */                               \
    } else {                                                        \
      CPU = (unsigned)CPUInfo[1] >> 24;                             \
      /*unsigned int cores = ((unsigned)CPUInfo[1] >> 16) & 0xff;*/ \
      /*if ((CPUInfo[3] & (1 << 28)) == 1) printf("HTT\n");*/       \
      /*printf("total core number : %d\n", cores);*/                \
    }                                                               \
    if (CPU < 0) CPU = 0;                                           \
  }

#ifdef Linux
static std::vector<int> parseCpuAffinityList() {
  std::vector<int> cpus;
  const char* env = std::getenv("CCBENCH_CPU_LIST");
  if (!env || !*env) return cpus;
  const char* p = env;
  while (*p) {
    char* end = nullptr;
    long first = std::strtol(p, &end, 10);
    if (end == p) break;
    long last = first;
    if (*end == '-') {
      p = end + 1;
      last = std::strtol(p, &end, 10);
    }
    if (first <= last) {
      for (long cpu = first; cpu <= last; ++cpu) cpus.push_back(static_cast<int>(cpu));
    } else {
      for (long cpu = first; cpu >= last; --cpu) cpus.push_back(static_cast<int>(cpu));
    }
    p = end;
    if (*p == ',') ++p;
  }
  return cpus;
}

static void setThreadAffinity(const int myid) {
  pid_t pid = syscall(SYS_gettid);
  cpu_set_t cpu_set;
  static const std::vector<int> cpu_list = parseCpuAffinityList();
  const int cpu = cpu_list.empty()
      ? (myid % sysconf(_SC_NPROCESSORS_CONF))
      : cpu_list[static_cast<size_t>(myid) % cpu_list.size()];

  CPU_ZERO(&cpu_set);
  CPU_SET(cpu, &cpu_set);

  if (sched_setaffinity(pid, sizeof(cpu_set_t), &cpu_set) != 0) ERR;

  // printf("thread affinity (id==%d) [ok]\n", myid);
  return;
}
#endif  // Linux

inline int cached_sched_getcpu() {
    thread_local int value = ::sched_getcpu();
    return value;
}
