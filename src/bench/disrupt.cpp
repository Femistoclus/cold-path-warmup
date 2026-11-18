#include "bench/disrupt.h"

#include "common/args.h"

#include <algorithm>
#include <array>
#include <cerrno>
#include <charconv>
#include <cpuid.h>
#include <cstdint>
#include <cstring>
#include <fcntl.h>
#include <format>
#include <sched.h>
#include <span>
#include <stdexcept>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <x86intrin.h>

namespace coldpath::bench {
namespace {

constexpr size_t PageSize = 4096;
constexpr size_t HugePageSize = 2U << 20;
constexpr size_t Window = 32;
constexpr size_t DirnSites = 16;
constexpr uint32_t MsrPredCmd = 0x49;
constexpr uint32_t MsrFlushCmd = 0x10b;
constexpr uint64_t LcgIncrement = 1442695040888963407ULL;

// JIT fragments, x86-64. rdi holds the LCG state, rsi the odd increment, rdx the loop count (dirn only).
constexpr std::array<uint8_t, 4> LeaRax = {0x48, 0x8d, 0x40, 0x01}; // lea rax, [rax + 1]
constexpr std::array<uint8_t, 4> LeaRcx = {0x48, 0x8d, 0x49, 0x01}; // lea rcx, [rcx + 1]
constexpr std::array<uint8_t, 4> LeaRdx = {0x48, 0x8d, 0x52, 0x01}; // lea rdx, [rdx + 1]
constexpr std::array<uint8_t, 4> LeaR8 = {0x4d, 0x8d, 0x40, 0x01};  // lea r8, [r8 + 1]
// imul rdi, rdi, 0x5851f42d; add rdi, rsi — one LCG step; its sign bit is the random branch outcome.
constexpr std::array<uint8_t, 10> LcgStep = {0x48, 0x69, 0xff, 0x2d, 0xf4, 0x51, 0x58, 0x48, 0x01, 0xf7};
constexpr std::array<uint8_t, 4> JmpNextWindow = {0xeb, 0x02, 0xcc, 0xcc}; // jmp +2 over two int3
constexpr std::array<uint8_t, 2> JsNextWindow = {0x78, 0x14};              // js +20
constexpr std::array<uint8_t, 2> JsSkipLea = {0x78, 0x04};                 // js +4
constexpr std::array<uint8_t, 3> DecRdx = {0x48, 0xff, 0xca};
constexpr std::array<uint8_t, 4> Return = {0x48, 0x89, 0xf8, 0xc3}; // mov rax, rdi; ret

using TJitCode = uint64_t (*)(uint64_t state, uint64_t increment, uint64_t loops);
using TRetPage = void (*)();

volatile uint64_t DisruptSink = 0;

size_t RoundUp(size_t value, size_t step)
{
    return (value + step - 1) / step * step;
}

void Put(std::vector<uint8_t>& code, std::span<const uint8_t> bytes)
{
    code.insert(code.end(), bytes.begin(), bytes.end());
}

// 32-byte windows, 8 µops each: straight-line (line), with a taken jmp to the next window (jumpy), or with a
// random-outcome js to the next window (dirw). Entering at "end − S" runs exactly S bytes.
std::vector<uint8_t> WindowCode(size_t bytes, bool jumpy, bool random)
{
    const std::array<std::span<const uint8_t>, 4> lea = {LeaRax, LeaRcx, LeaRdx, LeaR8};
    std::vector<uint8_t> code;
    code.reserve(bytes + Return.size());
    for (size_t w = 0; w < bytes / Window; ++w) {
        if (random) {
            Put(code, LcgStep);
            Put(code, JsNextWindow);
            for (int i = 0; i < 5; ++i) {
                Put(code, LeaRax);
            }
            continue;
        }
        for (size_t i = 0; i < (jumpy ? 7U : 8U); ++i) {
            Put(code, lea[i % lea.size()]);
        }
        if (jumpy) {
            Put(code, JmpNextWindow);
        }
    }
    Put(code, Return);
    return code;
}

// dirn: a loop over 16 random-outcome branch sites, 16 bytes each; rdx iterations.
std::vector<uint8_t> DirnCode()
{
    std::vector<uint8_t> code;
    for (size_t i = 0; i < DirnSites; ++i) {
        Put(code, LcgStep);
        Put(code, JsSkipLea);
        Put(code, LeaRax);
    }
    Put(code, DecRdx);
    const auto back = -static_cast<int32_t>(code.size() + 6);
    std::array<uint8_t, 6> jnz = {0x0f, 0x85};
    std::memcpy(jnz.data() + 2, &back, sizeof(back));
    Put(code, jnz);
    Put(code, Return);
    return code;
}

uint64_t ParseDose(std::string_view text)
{
    uint64_t scale = 1;
    if (text.ends_with('k') || text.ends_with('K')) {
        scale = 1024;
        text.remove_suffix(1);
    } else if (text.ends_with('m') || text.ends_with('M')) {
        scale = uint64_t{1} << 20U;
        text.remove_suffix(1);
    }
    const uint64_t dose = ParseNumber<uint64_t>(text) * scale;
    if (dose == 0) {
        throw std::invalid_argument("zero disruption dose");
    }
    return dose;
}

uint32_t ParseHex(std::string_view text)
{
    uint32_t value = 0;
    const auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), value, 16);
    if (error != std::errc{} || end != text.data() + text.size()) {
        throw std::invalid_argument("not a hex number: " + std::string(text));
    }
    return value;
}

} // namespace

TDisruptor::TItem TDisruptor::ParseItem(std::string_view text)
{
    const size_t colon = text.find(':');
    const std::string_view name = text.substr(0, colon);
    const std::string_view dose = colon == std::string_view::npos ? std::string_view{} : text.substr(colon + 1);
    const auto doseOr = [dose](uint64_t fallback) {
        return dose.empty() ? fallback : ParseDose(dose);
    };
    if (name == "d1" || name == "d2" || name == "d3") {
        const uint64_t fallback = name == "d1" ? 64U << 10 : name == "d2" ? 1U << 20 : 64U << 20;
        return {.Kind = EKind::Data, .Dose = RoundUp(doseOr(fallback), 64)};
    }
    if (name == "clf" && (dose.starts_with('d') || dose.starts_with('c'))) {
        const std::string_view lines = dose.substr(1);
        return {.Kind = dose[0] == 'd' ? EKind::FlushData : EKind::FlushCode,
            .Dose = lines.empty() ? SIZE_MAX : ParseDose(lines.substr(lines.starts_with(':') ? 1 : 0))};
    }
    if (name == "dtlb" || name == "itlb") {
        return {.Kind = name == "dtlb" ? EKind::Dtlb : EKind::Itlb, .Dose = doseOr(3072)};
    }
    if (name == "tlbflush" || name == "mprot1") {
        // 64 pages exceed the kernel's tlb_single_page_flush_ceiling (33): the whole TLB is flushed.
        return {.Kind = EKind::Mprotect, .Dose = name == "mprot1" ? 1 : doseOr(64)};
    }
    if (name == "line" || name == "jumpy" || name == "dirw") {
        const EKind kind = name == "line" ? EKind::Line : name == "jumpy" ? EKind::Jumpy : EKind::Dirw;
        return {.Kind = kind, .Dose = std::max(Window, doseOr(64U << 10) / Window * Window)};
    }
    if (name == "dirn") {
        return {.Kind = EKind::Dirn, .Dose = doseOr(16U << 10)};
    }
    if (name == "ibpb" && dose.empty()) {
        return {.Kind = EKind::Msr, .Msr = MsrPredCmd, .Value = 1};
    }
    if (name == "l1dflush" && dose.empty()) {
        return {.Kind = EKind::Msr, .Msr = MsrFlushCmd, .Value = 1};
    }
    if (name == "wr0") {
        // Control: the same syscall path as ibpb / l1dflush, writing 0 (no flush).
        return {.Kind = EKind::Msr, .Msr = dose.empty() ? MsrPredCmd : ParseHex(dose), .Value = 0};
    }
    if (name == "sys" && dose.empty()) {
        return {.Kind = EKind::Sys};
    }
    throw std::invalid_argument("unknown disruption " + std::string(text));
}

TDisruptor::TDisruptor(const TConfig& config)
    : DataLines_(config.DataLines)
    , CodeLines_(config.CodeLines)
    , SettleTicks_(static_cast<uint64_t>(static_cast<double>(config.SettleNs) * config.TscPerNs))
    , SettleNs_(config.SettleNs)
{
    const std::vector<std::string_view> cells = SplitList(config.Spec);
    for (const std::string_view cell : cells) {
        TCell parsed{.Label = std::string(cell)};
        const std::vector<std::string_view> items = SplitList(cell, '+');
        for (const std::string_view item : items) {
            if (item != "base") {
                parsed.Items.push_back(ParseItem(item));
            }
        }
        Cells_.push_back(std::move(parsed));
    }
    if (Cells_.empty()) {
        throw std::invalid_argument("empty --disrupt");
    }
    Allocate();
    CheckMsr(config.Cpu);
    for (size_t i = 0; i < Cells_.size(); ++i) {
        if (Cells_[i].Missing.empty()) {
            Available_.push_back(i);
        }
    }
}

TDisruptor::~TDisruptor()
{
    if (MsrFd_ >= 0) {
        close(MsrFd_);
    }
    for (const auto& [address, size] : Maps_) {
        munmap(address, size);
    }
}

uint8_t* TDisruptor::MapPrivate(size_t bytes, bool huge)
{
    constexpr int prot = PROT_READ | PROT_WRITE;
    if (huge) {
        const size_t size = RoundUp(bytes, HugePageSize);
        void* p = mmap(nullptr, size, prot, MAP_PRIVATE | MAP_ANONYMOUS | MAP_HUGETLB | MAP_POPULATE, -1, 0);
        if (p != MAP_FAILED) {
            Maps_.emplace_back(p, size);
            return static_cast<uint8_t*>(p);
        }
        Huge_ = false;
    }
    const size_t size = RoundUp(bytes, PageSize);
    void* p = mmap(nullptr, size, prot, MAP_PRIVATE | MAP_ANONYMOUS | MAP_POPULATE, -1, 0);
    if (p == MAP_FAILED) {
        throw std::runtime_error(std::format("mmap {} bytes: {}", size, std::strerror(errno)));
    }
    Maps_.emplace_back(p, size);
    return static_cast<uint8_t*>(p);
}

// N virtual pages backed by ONE physical page: they load the TLB but share a single cache line.
// Executable pages hold a single ret.
uint8_t* TDisruptor::MapAliases(size_t pages, bool executable)
{
    const uint8_t firstByte = executable ? 0xc3 : 0;
    const int prot = executable ? PROT_READ | PROT_EXEC : PROT_READ;
    const int fd = memfd_create("coldpath-disrupt", MFD_CLOEXEC);
    if (fd < 0 || ftruncate(fd, PageSize) != 0 || pwrite(fd, &firstByte, 1, 0) != 1) {
        const std::string error = std::strerror(errno);
        if (fd >= 0) {
            close(fd);
        }
        throw std::runtime_error("memfd: " + error);
    }
    const size_t size = pages * PageSize;
    void* base = mmap(nullptr, size, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
    if (base == MAP_FAILED) {
        close(fd);
        throw std::runtime_error(std::format("mmap {} pages: {}", pages, std::strerror(errno)));
    }
    Maps_.emplace_back(base, size);
    for (size_t i = 0; i < pages; ++i) {
        void* at = static_cast<uint8_t*>(base) + i * PageSize;
        if (mmap(at, PageSize, prot, MAP_SHARED | MAP_FIXED | MAP_POPULATE, fd, 0) == MAP_FAILED) {
            close(fd);
            throw std::runtime_error(std::format("mmap page {}: {} (vm.max_map_count?)", i, std::strerror(errno)));
        }
    }
    close(fd);
    return static_cast<uint8_t*>(base);
}

void TDisruptor::Allocate()
{
    uint64_t dtlb = 0;
    uint64_t itlb = 0;
    uint64_t prot = 0;
    uint64_t line = 0;
    uint64_t jumpy = 0;
    uint64_t dirw = 0;
    bool dirn = false;
    for (const TCell& cell : Cells_) {
        for (const TItem& item : cell.Items) {
            switch (item.Kind) {
                case EKind::Data:
                    DataBytes_ = std::max(DataBytes_, item.Dose);
                    break;
                case EKind::Dtlb:
                    dtlb = std::max(dtlb, item.Dose);
                    break;
                case EKind::Itlb:
                    itlb = std::max(itlb, item.Dose);
                    break;
                case EKind::Mprotect:
                    prot = std::max(prot, item.Dose);
                    break;
                case EKind::Line:
                    line = std::max(line, item.Dose);
                    break;
                case EKind::Jumpy:
                    jumpy = std::max(jumpy, item.Dose);
                    break;
                case EKind::Dirw:
                    dirw = std::max(dirw, item.Dose);
                    break;
                case EKind::Dirn:
                    dirn = true;
                    break;
                default:
                    break;
            }
        }
    }

    if (DataBytes_ > 0) {
        uint8_t* data = MapPrivate(DataBytes_, true);
        std::memset(data, 1, DataBytes_);
        Data_ = data;
    }
    if (dtlb > 0) {
        DtlbPages_ = MapAliases(dtlb, false);
    }
    if (itlb > 0) {
        ItlbPages_ = MapAliases(itlb, true);
    }
    if (prot > 0) {
        void* p = mmap(nullptr, prot * PageSize, PROT_READ, MAP_PRIVATE | MAP_ANONYMOUS | MAP_POPULATE, -1, 0);
        if (p == MAP_FAILED) {
            throw std::runtime_error(std::format("mmap: {}", std::strerror(errno)));
        }
        Maps_.emplace_back(p, prot * PageSize);
        ProtPages_ = static_cast<uint8_t*>(p);
    }

    // One code image, each kind on its own pages; a cell enters its kind at "end − dose".
    std::vector<uint8_t> image;
    std::array<size_t, 4> start{};
    const std::array<uint64_t, 3> sizes = {line, jumpy, dirw};
    for (size_t k = 0; k < sizes.size(); ++k) {
        if (sizes[k] > 0) {
            image.resize(RoundUp(image.size(), PageSize), 0xcc);
            start[k] = image.size();
            Put(image, WindowCode(sizes[k], k == 1, k == 2));
        }
    }
    if (dirn) {
        image.resize(RoundUp(image.size(), PageSize), 0xcc);
        start[3] = image.size();
        Put(image, DirnCode());
    }
    if (image.empty()) {
        return;
    }
    uint8_t* code = MapPrivate(image.size(), true);
    std::memcpy(code, image.data(), image.size());
    if (mprotect(code, Maps_.back().second, PROT_READ | PROT_EXEC) != 0) {
        throw std::runtime_error(std::format("mprotect PROT_EXEC: {}", std::strerror(errno)));
    }
    CodeBytes_ = image.size();
    for (TCell& cell : Cells_) {
        for (TItem& item : cell.Items) {
            const auto k = static_cast<size_t>(item.Kind) - static_cast<size_t>(EKind::Line);
            if (k < sizes.size()) {
                item.Entry = code + start[k] + sizes[k] - item.Dose;
            } else if (item.Kind == EKind::Dirn) {
                item.Entry = code + start[3];
            }
        }
    }
}

void TDisruptor::CheckMsr(int cpu)
{
    std::vector<std::pair<uint32_t, uint64_t>> writes;
    for (const TCell& cell : Cells_) {
        for (const TItem& item : cell.Items) {
            if (item.Kind == EKind::Msr && std::ranges::find(writes, std::pair{item.Msr, item.Value}) == writes.end()) {
                writes.emplace_back(item.Msr, item.Value);
            }
        }
    }
    if (writes.empty()) {
        return;
    }
    unsigned eax = 0;
    unsigned ebx = 0;
    unsigned ecx = 0;
    unsigned edx = 0;
    __get_cpuid_count(7, 0, &eax, &ebx, &ecx, &edx);
    MsrNote_ = std::format("CPUID IBPB {}, L1D_FLUSH {}",
        ((edx >> 26U) & 1U) != 0 ? "yes" : "no",
        ((edx >> 28U) & 1U) != 0 ? "yes" : "no");
    const std::string path = std::format("/dev/cpu/{}/msr", cpu >= 0 ? cpu : sched_getcpu());
    MsrFd_ = open(path.c_str(), O_WRONLY | O_CLOEXEC);
    if (MsrFd_ < 0) {
        MsrNote_ += std::format("; {}: {} (needs root and modprobe msr)", path, std::strerror(errno));
    }
    // One trial write per register and value: a refused write drops the cells that need it.
    for (const auto& [msr, value] : writes) {
        std::string why = MsrNote_;
        if (MsrFd_ >= 0) {
            if (pwrite(MsrFd_, &value, sizeof(value), msr) == static_cast<ssize_t>(sizeof(value))) {
                continue;
            }
            why = std::format("write {:#x} <- {}: {}", msr, value, std::strerror(errno));
            MsrNote_ += "; " + why;
        }
        for (TCell& cell : Cells_) {
            const bool uses = std::ranges::any_of(cell.Items, [msr, value](const TItem& item) {
                return item.Kind == EKind::Msr && item.Msr == msr && item.Value == value;
            });
            if (uses && cell.Missing.empty()) {
                cell.Missing = why;
            }
        }
    }
}

uint64_t TDisruptor::Run(size_t cell)
{
    _mm_lfence();
    const uint64_t start = __rdtsc();
    for (const TItem& item : Cells_[cell].Items) {
        RunItem(item);
    }
    _mm_mfence();
    _mm_lfence();
    const uint64_t end = __rdtsc();
    // Settle: outstanding misses and hardware prefetch streams finish; the loop touches only registers.
    const uint64_t settle = SettleTicks_;
    while (__rdtsc() - end < settle) {
        _mm_pause();
    }
    return end - start;
}

void TDisruptor::RunItem(const TItem& item)
{
    switch (item.Kind) {
        case EKind::Data: {
            const volatile uint8_t* data = Data_;
            uint8_t sum = 0;
            for (size_t i = 0; i < item.Dose; i += 64) {
                sum ^= data[i];
            }
            DisruptSink = DisruptSink + sum;
            break;
        }
        case EKind::FlushData:
        case EKind::FlushCode: {
            const std::vector<const void*>& lines = item.Kind == EKind::FlushData ? DataLines_ : CodeLines_;
            const size_t count = std::min<size_t>(lines.size(), item.Dose);
            for (size_t i = 0; i < count; ++i) {
                _mm_clflush(lines[i]);
            }
            break;
        }
        case EKind::Dtlb: {
            const volatile uint8_t* pages = DtlbPages_;
            uint8_t sum = 0;
            for (size_t i = 0; i < item.Dose; ++i) {
                sum ^= pages[i * PageSize];
            }
            DisruptSink = DisruptSink + sum;
            break;
        }
        case EKind::Itlb:
            for (size_t i = 0; i < item.Dose; ++i) {
                reinterpret_cast<TRetPage>(ItlbPages_ + i * PageSize)();
            }
            break;
        case EKind::Mprotect:
            mprotect(ProtPages_, item.Dose * PageSize, PROT_NONE);
            mprotect(ProtPages_, item.Dose * PageSize, PROT_READ);
            break;
        case EKind::Line:
        case EKind::Jumpy:
        case EKind::Dirw:
            State_ = reinterpret_cast<TJitCode>(item.Entry)(State_, LcgIncrement, 0);
            break;
        case EKind::Dirn:
            State_ = reinterpret_cast<TJitCode>(
                item.Entry)(State_, LcgIncrement, std::max<uint64_t>(1, item.Dose / DirnSites));
            break;
        case EKind::Msr:
            static_cast<void>(pwrite(MsrFd_, &item.Value, sizeof(item.Value), item.Msr));
            break;
        case EKind::Sys:
            syscall(SYS_getppid);
            break;
    }
}

std::string TDisruptor::Describe() const
{
    std::string text = std::format("disruption: cells {}, available {}; settle {} ns; data {} KB, code {} KB; hp={} "
                                   "(disruption data and code on {})",
        Cells_.size(),
        Available_.size(),
        SettleNs_,
        DataBytes_ >> 10U,
        CodeBytes_ >> 10U,
        Huge_ ? 1 : 0,
        Huge_ ? "2 MB pages" : "4 KB pages, no huge pages");
    if (!MsrNote_.empty()) {
        text += "; MSR: " + MsrNote_;
    }
    for (const TCell& cell : Cells_) {
        if (!cell.Missing.empty()) {
            text += std::format("\n  cell {} skipped: {}", cell.Label, cell.Missing);
        }
    }
    return text;
}

} // namespace coldpath::bench
