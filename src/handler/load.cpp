#include "handler/load.h"

#include "common/print.h"
#include "handler/table.h"

#include <algorithm>
#include <cstdlib>
#include <format>

namespace coldpath::handler {
namespace {

// Model: rows of a 1 MB table along a dependent chain, as features read from several books.
constexpr unsigned ModelLog2 = 14;
static_assert(COLDPATH_DATA_LINES <= (1 << ModelLog2) / 4, "the model table is too small for COLDPATH_DATA_LINES");
#if COLDPATH_DATA_LINES > 0
constexpr uint64_t ModelKey = 0x6d6f64656cULL;
TTable<ModelLog2> Model;
#endif
#if COLDPATH_CODE_KB > 0
// Fixed: the generated code takes the same path in real and dry runs.
constexpr uint64_t CodeState = 0x9e3779b97f4a7c15ULL;
#endif

const TLoad* Reported = nullptr;

void Report()
{
    Eprintln("[load] {}", Reported->Summary());
}

} // namespace

bool TLoad::Init([[maybe_unused]] unsigned seed)
{
    if (Initialized_) {
        return true;
    }
    MaxShares_ = 1'000'000;
    MaxPosition_ = 1'000'000'000;
    MaxNotional_ = 1'000'000'000'000'000;
    RefPrice_ = 1'000'000;
    MinPrice_ = RefPrice_ / 2;
    MaxPrice_ = RefPrice_ * 2;
#if COLDPATH_DATA_LINES > 0
    Model.Fill(seed);
#endif
#if COLDPATH_SEND
    if (!Sender_.Init()) {
        return false;
    }
#endif
    const auto* begin = reinterpret_cast<const char*>(this);
    for (const char* line = begin - (reinterpret_cast<uintptr_t>(begin) & 63U); line < begin + sizeof(*this);
        line += 64)
    {
        Lines_.push_back(line);
    }
#if COLDPATH_DATA_LINES > 0
    const std::vector<const void*> rows = Model.Rows<COLDPATH_DATA_LINES, true>(ModelKey);
    Lines_.insert(Lines_.end(), rows.begin(), rows.end());
#endif
    Initialized_ = true;
    Reported = this;
    std::atexit(Report);
    return true;
}

void TLoad::Run(int64_t signal, bool dryRun)
{
    const int64_t shares = std::min(signal < 0 ? -signal : signal, MaxShares_);
    const int64_t position = signal < 0 ? Position_ - shares : Position_ + shares;
    if (position > MaxPosition_ || position < -MaxPosition_ || shares * RefPrice_ > MaxNotional_) {
        ++Rejected_;
        return;
    }
    uint32_t price = RefPrice_;
#if COLDPATH_DATA_LINES > 0
    const TWalk walk = Model.Walk<COLDPATH_DATA_LINES, true>(ModelKey);
    price += static_cast<uint32_t>((walk.Bid - walk.Ask) & 15) * 100;
    Sink_ += walk.Big;
#endif
#if COLDPATH_CODE_KB > 0
    Sink_ += RunCode(CodeState);
#endif
    if (price < MinPrice_ || price > MaxPrice_) {
        ++Rejected_;
        return;
    }
#if COLDPATH_SEND
    if (!dryRun || Sender_.WarmMode() != EWarmSend::None) {
        Sender_.Encode(signal, price);
        Sender_.Send(dryRun);
    }
#else
    static_cast<void>(dryRun);
#endif
}

const std::vector<const void*>& TLoad::Lines() const
{
    return Lines_;
}

uint64_t TLoad::RealSends() const
{
#if COLDPATH_SEND
    return Sender_.RealSends();
#else
    return 0;
#endif
}

std::string TLoad::Describe() const
{
    std::string text = "load:";
    if (COLDPATH_CODE_KB > 0) {
        text += std::format(" code {} KB (gen-code.py);", COLDPATH_CODE_KB);
    }
    if (COLDPATH_DATA_LINES > 0) {
        text += std::format(" model {} lines ({} KB), dependent reads;", COLDPATH_DATA_LINES, COLDPATH_DATA_LINES / 16);
    }
#if COLDPATH_SEND
    text += " " + Sender_.Describe();
#else
    text += " no sending";
#endif
    return text;
}

std::string TLoad::Summary() const
{
#if COLDPATH_SEND
    return std::format("{}; rejected by risk {}", Sender_.Summary(), Rejected_);
#else
    return std::format("rejected by risk {}", Rejected_);
#endif
}

} // namespace coldpath::handler
