#include "bench/counters.h"

#include "common/print.h"

#include <cerrno>
#include <cstring>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <vector>

namespace coldpath::bench {
namespace {

struct TCounterSpec
{
    std::string_view Name;
    uint32_t Type;
    uint64_t Config;
};

constexpr uint64_t L1dReadMiss = uint64_t{PERF_COUNT_HW_CACHE_L1D} | (uint64_t{PERF_COUNT_HW_CACHE_OP_READ} << 8) |
    (uint64_t{PERF_COUNT_HW_CACHE_RESULT_MISS} << 16);

std::vector<TCounterSpec> GroupSpecs(char group)
{
    switch (group) {
        case 'b':
            return {{"cycles", PERF_TYPE_HARDWARE, PERF_COUNT_HW_CPU_CYCLES},
                {"ref_cycles", PERF_TYPE_HARDWARE, PERF_COUNT_HW_REF_CPU_CYCLES},
                {"l1_miss", PERF_TYPE_RAW, 0x08d1},
                {"l2_miss", PERF_TYPE_RAW, 0x10d1},
                {"l3_miss", PERF_TYPE_RAW, 0x20d1},
                {"ic_miss", PERF_TYPE_RAW, 0x0283}};
        case 'c':
            return {{"cycles", PERF_TYPE_HARDWARE, PERF_COUNT_HW_CPU_CYCLES},
                {"code_l2_miss", PERF_TYPE_RAW, 0x2424},
                {"ic_stall", PERF_TYPE_RAW, 0x0483},
                {"baclears", PERF_TYPE_RAW, 0x01e6},
                {"itlb_walk", PERF_TYPE_RAW, 0x0e85}};
        case 't':
            // Core cycles only: a precise timer where TSC ticks in coarse steps (~10 ns on AMD Zen).
            return {{"cycles", PERF_TYPE_HARDWARE, PERF_COUNT_HW_CPU_CYCLES}};
        case 'd':
            return {{"cycles", PERF_TYPE_HARDWARE, PERF_COUNT_HW_CPU_CYCLES},
                {"dsb_uops", PERF_TYPE_RAW, 0x0879},
                {"mite_uops", PERF_TYPE_RAW, 0x0479},
                {"dtlb_walk", PERF_TYPE_RAW, 0x0e08},
                {"hit_pre_sw", PERF_TYPE_RAW, 0x014c}};
        case 'e':
            // Disruptor signatures; codes for Skylake / Coffee Lake.
            return {{"cycles", PERF_TYPE_HARDWARE, PERF_COUNT_HW_CPU_CYCLES},
                {"br_miss_cond", PERF_TYPE_RAW, 0x01c5},
                {"br_miss", PERF_TYPE_RAW, 0x00c5},
                {"dtlb_stlb_hit", PERF_TYPE_RAW, 0x2008},
                {"itlb_stlb_hit", PERF_TYPE_RAW, 0x2085},
                {"recovery", PERF_TYPE_RAW, 0x010d}};
        default:
            return {{"l1d_miss", PERF_TYPE_HW_CACHE, L1dReadMiss},
                {"llc_miss", PERF_TYPE_HARDWARE, PERF_COUNT_HW_CACHE_MISSES},
                {"br_miss", PERF_TYPE_HARDWARE, PERF_COUNT_HW_BRANCH_MISSES}};
    }
}

} // namespace

std::optional<TCounters> TCounters::Open(char group, bool countKernel)
{
    TCounters counters;
    int leader = -1;
    for (const TCounterSpec& spec : GroupSpecs(group)) {
        perf_event_attr attr{};
        attr.size = sizeof(attr);
        attr.type = spec.Type;
        attr.config = spec.Config;
        attr.pinned = leader < 0 ? 1 : 0;
        attr.exclude_kernel = countKernel ? 0 : 1;
        attr.exclude_hv = 1;
        const auto fd = static_cast<int>(syscall(SYS_perf_event_open, &attr, 0, -1, leader, 0));
        void* page = fd < 0 ? MAP_FAILED
                            : mmap(nullptr, static_cast<size_t>(sysconf(_SC_PAGESIZE)), PROT_READ, MAP_SHARED, fd, 0);
        if (page == MAP_FAILED || static_cast<perf_event_mmap_page*>(page)->cap_user_rdpmc == 0) {
            Eprintln("counter {} unavailable via rdpmc: {}; continuing without counters",
                spec.Name,
                fd < 0 ? std::strerror(errno) : "no cap_user_rdpmc");
            return std::nullopt;
        }
        counters.Pages_[counters.Size_] = static_cast<perf_event_mmap_page*>(page);
        counters.Names_[counters.Size_] = spec.Name;
        ++counters.Size_;
        if (leader < 0) {
            leader = fd;
        }
    }
    return counters;
}

} // namespace coldpath::bench
