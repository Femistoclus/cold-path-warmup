#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace coldpath::bench {

// Targeted disruption right before the event. Spec: cells separated by ",", a cell is
// "item+item+…" run in the written order, an item is "name[:dose]"; "base" is the empty cell.
class TDisruptor
{
public:
    struct TConfig
    {
        std::string_view Spec;
        uint64_t SettleNs = 1000;
        double TscPerNs = 1;
        int Cpu = -1;
        std::vector<const void*> DataLines;
        std::vector<const void*> CodeLines;
    };

    explicit TDisruptor(const TConfig& config);
    ~TDisruptor();
    TDisruptor(const TDisruptor&) = delete;
    TDisruptor& operator=(const TDisruptor&) = delete;

    // Cells that can run on this machine (MSR writes may be refused).
    const std::vector<size_t>& Available() const
    {
        return Available_;
    }

    std::string_view Label(size_t cell) const
    {
        return Cells_[cell].Label;
    }

    std::string Describe() const;

    // Disrupts, fences, waits SettleNs without memory accesses; returns the disruption time in TSC ticks.
    [[gnu::noinline]] uint64_t Run(size_t cell);

private:
    enum class EKind : uint8_t {
        Data,
        FlushData,
        FlushCode,
        Dtlb,
        Itlb,
        Mprotect,
        Line,
        Jumpy,
        Dirw,
        Dirn,
        Msr,
        Sys,
    };

    struct TItem
    {
        EKind Kind = EKind::Sys;
        uint64_t Dose = 0;
        uint32_t Msr = 0;
        uint64_t Value = 0;
        uint8_t* Entry = nullptr;
    };

    struct TCell
    {
        std::string Label;
        std::vector<TItem> Items{};
        std::string Missing{};
    };

    std::vector<TCell> Cells_;
    std::vector<size_t> Available_;
    std::vector<const void*> DataLines_;
    std::vector<const void*> CodeLines_;
    std::vector<std::pair<void*, size_t>> Maps_;
    const uint8_t* Data_ = nullptr;
    size_t DataBytes_ = 0;
    const uint8_t* DtlbPages_ = nullptr;
    uint8_t* ItlbPages_ = nullptr;
    uint8_t* ProtPages_ = nullptr;
    size_t CodeBytes_ = 0;
    bool Huge_ = true;
    int MsrFd_ = -1;
    std::string MsrNote_;
    uint64_t SettleTicks_ = 0;
    uint64_t SettleNs_ = 0;
    uint64_t State_ = 0x9e3779b97f4a7c15ULL;

    static TItem ParseItem(std::string_view text);
    void Allocate();
    void CheckMsr(int cpu);
    uint8_t* MapPrivate(size_t bytes, bool huge);
    uint8_t* MapAliases(size_t pages, bool executable);
    void RunItem(const TItem& item);
};

} // namespace coldpath::bench
