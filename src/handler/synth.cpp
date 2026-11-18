// Synthetic model sized for i7-8700: a 1 MB table (larger than L2), 64 KB touched per event (larger than L1d).
#include "handler/handler.h"
#include "handler/table.h"

#include <array>
#include <format>
#include <string>

namespace coldpath::handler {
namespace {

#ifndef COLDPATH_SYNTH_TOUCH
#define COLDPATH_SYNTH_TOUCH 1024
#endif
// Dependent reads: the next row depends on the value read (d64, d512); 0 keeps the independent walk.
#ifndef COLDPATH_SYNTH_CHAIN
#define COLDPATH_SYNTH_CHAIN 0
#endif
#ifndef COLDPATH_SYNTH_TABLE_LOG2
#define COLDPATH_SYNTH_TABLE_LOG2 14
#endif
// Non-empty PrefetchData (the rows of the real event); off for the original bench-synth.
#ifndef COLDPATH_SYNTH_PREFETCH
#define COLDPATH_SYNTH_PREFETCH 0
#endif
constexpr int Touch = COLDPATH_SYNTH_TOUCH;
constexpr bool Chain = COLDPATH_SYNTH_CHAIN != 0;

struct TSynthEvent
{
    uint64_t Key;
    int64_t Quantity;
};

constexpr std::array<TSynthEvent, 2> SynthEvents = {{{12345, 10}, {67890, 10}}};
TTable<COLDPATH_SYNTH_TABLE_LOG2> Orders;
std::array<uint64_t, 1024> Sent;
unsigned SentCount = 0;
std::string InfoText;

} // namespace

bool Init(unsigned seed)
{
    Orders.Fill(seed);
    InfoText = std::format("synthetic: table {} KB, {} lines per event ({} KB){}",
        Orders.Size * 64 / 1024,
        Touch,
        Touch * 64 / 1024,
        Chain ? ", dependent reads (chain)" : "");
    return true;
}

uint64_t Handle(EEvent event, bool dryRun)
{
    const TSynthEvent& e = SynthEvents[static_cast<size_t>(event)];
    const TWalk walk = Orders.Walk<Touch, Chain>(e.Key);
    const int64_t signal = walk.Bid - walk.Ask + e.Quantity;
    if (!dryRun && signal > 0) {
        Sent[SentCount++ & 1023U] = static_cast<uint64_t>(signal);
    }
    return static_cast<uint64_t>(signal) + walk.Big;
}

std::string_view Info()
{
    return InfoText;
}

// The rows depend only on the event key and the table, so they are known before the event.
TPrefetchData PrefetchData()
{
    if constexpr (COLDPATH_SYNTH_PREFETCH != 0) {
        std::vector<const void*> lines = Orders.Rows<Touch, Chain>(SynthEvents[static_cast<size_t>(EEvent::Real)].Key);
        const size_t generic = lines.size();
        return {.Lines = std::move(lines), .Generic = generic};
    }
    return {};
}

} // namespace coldpath::handler
