#pragma once

#include <array>
#include <cstdint>
#include <linux/perf_event.h>
#include <optional>
#include <span>
#include <string_view>
#include <x86intrin.h>

namespace coldpath::bench {

// rdpmc without a syscall: read() itself evicted part of the caches (Liu & Weaver 2017).
class TCounters
{
public:
    static constexpr size_t MaxCounters = 6;
    using TValues = std::array<uint64_t, MaxCounters>;

    static std::optional<TCounters> Open(char group, bool countKernel);

    std::span<const std::string_view> Names() const
    {
        return {Names_.data(), Size_};
    }

    void Read(TValues& values) const
    {
        for (size_t i = 0; i < Size_; ++i) {
            const perf_event_mmap_page* page = Pages_[i];
            uint32_t seq = 0;
            do {
                seq = page->lock;
                __asm__ volatile("" ::: "memory");
                const uint32_t index = page->index;
                auto pmc = index != 0 ? static_cast<int64_t>(__rdpmc(static_cast<int>(index) - 1)) : 0;
                const unsigned shift = 64U - page->pmc_width;
                // NOLINTNEXTLINE(bugprone-signed-bitwise)
                pmc = static_cast<int64_t>(static_cast<uint64_t>(pmc) << shift) >> shift;
                values[i] = static_cast<uint64_t>(page->offset) + static_cast<uint64_t>(pmc);
                __asm__ volatile("" ::: "memory");
            } while (page->lock != seq);
        }
    }

private:
    std::array<const perf_event_mmap_page*, MaxCounters> Pages_{};
    std::array<std::string_view, MaxCounters> Names_{};
    size_t Size_ = 0;
};

} // namespace coldpath::bench
