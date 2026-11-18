#include "bench/prefetch.h"

#include "common/print.h"
#include "handler/handler.h"

#include <algorithm>
#include <csignal>
#include <format>
#include <ucontext.h>
#include <x86intrin.h>

namespace coldpath::bench {
namespace {

constexpr size_t TraceCapacity = 1 << 20;
std::vector<const char*> TraceLines;
volatile uint64_t TraceSink = 0;

void OnTrap(int /*signal*/, siginfo_t* /*info*/, void* context)
{
    if (TraceLines.size() < TraceCapacity) {
        const auto rip = static_cast<uintptr_t>(static_cast<ucontext_t*>(context)->uc_mcontext.gregs[REG_RIP]);
        // NOLINTNEXTLINE(performance-no-int-to-ptr): instruction address from the signal context.
        TraceLines.push_back(reinterpret_cast<const char*>(rip & ~uintptr_t{63}));
    }
}

// Trap flag: the kernel sends SIGTRAP after every instruction, and the handler records its cache line.
// The 128-byte offset keeps pushfq out of the stack red zone.
void SetTrapFlag()
{
    __asm__ volatile("sub $128, %%rsp\n\tpushfq\n\torq $0x100, (%%rsp)\n\tpopfq\n\tadd $128, %%rsp" ::: "memory", "cc");
}

void ClearTrapFlag()
{
    __asm__ volatile("sub $128, %%rsp\n\tpushfq\n\tandq $~0x100, (%%rsp)\n\tpopfq\n\tadd $128, %%rsp" ::
            : "memory", "cc");
}

} // namespace

TPrefetcher::TPrefetcher(std::string_view set, int reps)
    : Set_(set)
    , Reps_(reps)
    , UseData_(set.find_first_of("dg") != std::string_view::npos)
    , UseCode_(set.find_first_of("cg") != std::string_view::npos)
    , GenericOnly_(set.find('g') != std::string_view::npos)
{
    handler::TPrefetchData data = handler::PrefetchData();
    Data_ = std::move(data.Lines);
    Generic_ = data.Generic;
    TraceCode();
}

void TPrefetcher::TraceCode()
{
    TraceLines.clear();
    TraceLines.reserve(TraceCapacity);
    struct sigaction action{};
    struct sigaction previous{};
    action.sa_sigaction = OnTrap;
    action.sa_flags = SA_SIGINFO;
    sigaction(SIGTRAP, &action, &previous);
    SetTrapFlag();
    TraceSink = TraceSink + handler::Handle(handler::EEvent::Real, false);
    ClearTrapFlag();
    sigaction(SIGTRAP, &previous, nullptr);

    std::vector<const char*> lines = TraceLines;
    std::ranges::sort(lines);
    const auto [first, last] = std::ranges::unique(lines);
    lines.erase(first, last);
    CodeLineTotal_ = lines.size();
    for (const char* line : lines) {
        if (!CodeStart_.empty() && line == CodeStart_.back() + 64 * static_cast<ptrdiff_t>(CodeLines_.back())) {
            ++CodeLines_.back();
        } else {
            CodeStart_.push_back(line);
            CodeLines_.push_back(1);
        }
    }
    std::string ranges;
    for (size_t i = 0; i < std::min<size_t>(CodeStart_.size(), 32); ++i) {
        ranges += std::format(" {:x}+{}", reinterpret_cast<uintptr_t>(CodeStart_[i]), CodeLines_[i]);
    }
    Println("handle trace: {} instructions, {} code lines in {} ranges:{}",
        TraceLines.size(),
        CodeLineTotal_,
        CodeStart_.size(),
        ranges);
}

void TPrefetcher::Run() const
{
    for (int r = 0; r < Reps_; ++r) {
        if (UseData_) {
            const size_t count = GenericOnly_ ? Generic_ : Data_.size();
            for (size_t i = 0; i < count; ++i) {
                _mm_prefetch(static_cast<const char*>(Data_[i]), _MM_HINT_T0);
            }
            const auto* stack = static_cast<const char*>(__builtin_frame_address(0));
            for (ptrdiff_t i = 1; i <= 8; ++i) {
                _mm_prefetch(stack - 64 * i, _MM_HINT_T0);
            }
        }
        if (UseCode_) {
            for (size_t i = 0; i < CodeStart_.size(); ++i) {
                for (ptrdiff_t k = 0; k < CodeLines_[i]; ++k) {
                    _mm_prefetch(CodeStart_[i] + 64 * k, _MM_HINT_T1);
                }
            }
        }
    }
}

std::vector<const void*> TPrefetcher::CodeLines() const
{
    std::vector<const void*> lines;
    for (size_t i = 0; i < CodeStart_.size(); ++i) {
        for (ptrdiff_t k = 0; k < CodeLines_[i]; ++k) {
            lines.push_back(CodeStart_[i] + 64 * k);
        }
    }
    return lines;
}

std::string TPrefetcher::Describe() const
{
    return std::format("prefetch: set \"{}\", passes {}; data lines {} (known before the event {}), code lines {}",
        Set_,
        Reps_,
        Data_.size(),
        Generic_,
        CodeLineTotal_);
}

} // namespace coldpath::bench
