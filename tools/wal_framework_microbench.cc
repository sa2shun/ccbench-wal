#include <array>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <iostream>
#include <mutex>
#include <sstream>
#include <string>
#include <string_view>
#include <thread>
#include <vector>

#include <fcntl.h>
#include <unistd.h>

namespace {

constexpr uint32_t kMaxWorkers = 256;

enum class Mode { kSharedWal, kPerThreadWal };

struct Config {
  Mode mode = Mode::kSharedWal;
  uint32_t threads = 1;
  uint32_t seconds = 10;
  uint32_t writes_per_tx = 1;
  uint32_t payload_bytes = 128;
  std::string output_root = "/tmp/ccbench_wal_framework";
};

uint32_t number(std::string_view arg, std::string_view prefix) {
  return static_cast<uint32_t>(std::stoul(std::string(arg.substr(prefix.size()))));
}

Config parseArgs(int argc, char** argv) {
  Config cfg;
  for (int i = 1; i < argc; ++i) {
    std::string_view arg(argv[i]);
    if (arg == "--mode=wal") cfg.mode = Mode::kSharedWal;
    else if (arg == "--mode=pwal") cfg.mode = Mode::kPerThreadWal;
    else if (arg.starts_with("--threads=")) cfg.threads = number(arg, "--threads=");
    else if (arg.starts_with("--seconds=")) cfg.seconds = number(arg, "--seconds=");
    else if (arg.starts_with("--writes_per_tx=")) cfg.writes_per_tx = number(arg, "--writes_per_tx=");
    else if (arg.starts_with("--payload_bytes=")) cfg.payload_bytes = number(arg, "--payload_bytes=");
    else if (arg.starts_with("--output_root=")) cfg.output_root = std::string(arg.substr(14));
    else { std::cerr << "unknown argument: " << arg << "\n"; std::exit(2); }
  }
  if (cfg.threads == 0 || cfg.threads > kMaxWorkers || cfg.seconds == 0 || cfg.writes_per_tx == 0) {
    std::cerr << "invalid arguments\n";
    std::exit(2);
  }
  return cfg;
}

class WalFrameworkBench {
 public:
  explicit WalFrameworkBench(Config cfg) : cfg_(std::move(cfg)) {
    fds_.fill(-1);
    commits_.fill(0);
    bytes_.fill(0);
    payload_build_ns_.fill(0);
    mutex_wait_ns_.fill(0);
    write_ns_.fill(0);
    fdatasync_ns_.fill(0);
    std::filesystem::create_directories(cfg_.output_root);
    output_dir_ = cfg_.output_root + "/" + modeName() + "_" + std::to_string(getpid());
    std::filesystem::create_directories(output_dir_);
    if (cfg_.mode == Mode::kSharedWal) {
      shared_fd_ = openFile(output_dir_ + "/shared.wal");
    } else {
      for (uint32_t worker = 0; worker < cfg_.threads; ++worker) {
        fds_[worker] = openFile(output_dir_ + "/worker_" + std::to_string(worker) + ".wal");
      }
    }
  }

  ~WalFrameworkBench() {
    if (shared_fd_ >= 0) close(shared_fd_);
    for (int fd : fds_) if (fd >= 0) close(fd);
  }

  void run() {
    std::vector<std::thread> workers;
    for (uint32_t worker = 0; worker < cfg_.threads; ++worker) {
      workers.emplace_back([this, worker] { workerMain(worker); });
    }
    start_.store(true, std::memory_order_release);
    std::this_thread::sleep_for(std::chrono::seconds(cfg_.seconds));
    stop_.store(true, std::memory_order_release);
    for (auto& worker : workers) worker.join();

    const uint64_t commits = total(commits_);
    const uint64_t bytes = total(bytes_);
    std::cout << "mode: " << modeName() << "\n";
    std::cout << "threads: " << cfg_.threads << "\n";
    std::cout << "seconds: " << cfg_.seconds << "\n";
    std::cout << "writes_per_tx: " << cfg_.writes_per_tx << "\n";
    std::cout << "payload_bytes: " << cfg_.payload_bytes << "\n";
    std::cout << "commit_count: " << commits << "\n";
    std::cout << "throughput_tps: " << commits / cfg_.seconds << "\n";
    std::cout << "bytes_written: " << bytes << "\n";
    std::cout << "write_mib_per_sec: " << (bytes / 1048576.0 / cfg_.seconds) << "\n";
    const uint64_t payload_ns = total(payload_build_ns_);
    const uint64_t mutex_ns = total(mutex_wait_ns_);
    const uint64_t write_ns = total(write_ns_);
    const uint64_t fsync_ns = total(fdatasync_ns_);
    const uint64_t accounted_ns = payload_ns + mutex_ns + write_ns + fsync_ns;
    std::cout << "wal_dir: " << output_dir_ << "\n";
    std::cout << "cost_payload_build_ns: " << payload_ns << "\n";
    std::cout << "cost_mutex_wait_ns: " << mutex_ns << "\n";
    std::cout << "cost_write_ns: " << write_ns << "\n";
    std::cout << "cost_fdatasync_ns: " << fsync_ns << "\n";
    std::cout << "cost_total_accounted_ns: " << accounted_ns << "\n";
    std::cout << "cost_avg_accounted_ns_per_commit: " << (commits ? accounted_ns / commits : 0) << "\n";
  }

 private:
  std::string modeName() const { return cfg_.mode == Mode::kSharedWal ? "wal" : "pwal"; }

  static int openFile(const std::string& path) {
    int fd = ::open(path.c_str(), O_CREAT | O_TRUNC | O_WRONLY | O_CLOEXEC, 0644);
    if (fd < 0) { perror(path.c_str()); std::abort(); }
    return fd;
  }

  static void writeAll(int fd, std::string_view data) {
    const char* p = data.data();
    size_t left = data.size();
    while (left > 0) {
      ssize_t n = ::write(fd, p, left);
      if (n < 0) { perror("write wal_framework"); std::abort(); }
      p += n;
      left -= static_cast<size_t>(n);
    }
  }

  static uint64_t nowNs() {
    return static_cast<uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now().time_since_epoch()).count());
  }

  std::string makePayload(uint32_t worker, uint64_t txid) {
    std::ostringstream out;
    const uint64_t base_key = static_cast<uint64_t>(worker) << 48;
    for (uint32_t i = 0; i < cfg_.writes_per_tx; ++i) {
      const uint64_t lsn = next_lsn_.fetch_add(1, std::memory_order_acq_rel);
      out << "LSN=" << lsn << " thid=" << worker << " tx=" << txid
          << " op=U key=" << (base_key + txid * cfg_.writes_per_tx + i)
          << " val=" << std::string(cfg_.payload_bytes, 'x') << "\n";
    }
    const uint64_t commit_lsn = next_lsn_.fetch_add(1, std::memory_order_acq_rel);
    out << "LSN=" << commit_lsn << " thid=" << worker << " tx=" << txid << " op=C\n";
    return out.str();
  }

  void workerMain(uint32_t worker) {
    while (!start_.load(std::memory_order_acquire)) std::this_thread::yield();
    uint64_t local_txid = 0;
    while (!stop_.load(std::memory_order_acquire)) {
      const uint64_t build_start = nowNs();
      std::string payload = makePayload(worker, local_txid++);
      payload_build_ns_[worker] += nowNs() - build_start;
      if (cfg_.mode == Mode::kSharedWal) {
        const uint64_t wait_start = nowNs();
        shared_mutex_.lock();
        mutex_wait_ns_[worker] += nowNs() - wait_start;
        std::unique_lock<std::mutex> guard(shared_mutex_, std::adopt_lock);
        const uint64_t write_start = nowNs();
        writeAll(shared_fd_, payload);
        write_ns_[worker] += nowNs() - write_start;
        const uint64_t fsync_start = nowNs();
        if (::fdatasync(shared_fd_) != 0) { perror("fdatasync shared wal"); std::abort(); }
        fdatasync_ns_[worker] += nowNs() - fsync_start;
      } else {
        const uint64_t write_start = nowNs();
        writeAll(fds_[worker], payload);
        write_ns_[worker] += nowNs() - write_start;
        const uint64_t fsync_start = nowNs();
        if (::fdatasync(fds_[worker]) != 0) { perror("fdatasync pwal"); std::abort(); }
        fdatasync_ns_[worker] += nowNs() - fsync_start;
      }
      ++commits_[worker];
      bytes_[worker] += payload.size();
    }
  }

  template <class Array>
  uint64_t total(const Array& values) const {
    uint64_t sum = 0;
    for (uint32_t i = 0; i < cfg_.threads; ++i) sum += values[i];
    return sum;
  }

  Config cfg_;
  std::string output_dir_;
  int shared_fd_ = -1;
  std::array<int, kMaxWorkers> fds_;
  std::mutex shared_mutex_;
  std::array<uint64_t, kMaxWorkers> commits_;
  std::array<uint64_t, kMaxWorkers> bytes_;
  std::array<uint64_t, kMaxWorkers> payload_build_ns_;
  std::array<uint64_t, kMaxWorkers> mutex_wait_ns_;
  std::array<uint64_t, kMaxWorkers> write_ns_;
  std::array<uint64_t, kMaxWorkers> fdatasync_ns_;
  std::atomic<uint64_t> next_lsn_{1};
  std::atomic<bool> start_{false};
  std::atomic<bool> stop_{false};
};

}  // namespace

int main(int argc, char** argv) {
  WalFrameworkBench bench(parseArgs(argc, argv));
  bench.run();
  return 0;
}
