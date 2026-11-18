#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <span>
#include <string_view>
#include <vector>

// The measured handler. The implementation is chosen at link time (cpptrader, synth, null), not through a
// virtual call: an indirect branch cools down on its own and would skew the measurement.
namespace coldpath::handler {

enum class EEvent : uint8_t {
    Real = 0,
    Other = 1,
};

bool Init(unsigned seed);
uint64_t Handle(EEvent event, bool dryRun);
std::string_view Info();

struct TPrefetchData
{
    std::vector<const void*> Lines;
    size_t Generic = 0;
};
TPrefetchData PrefetchData();

struct TBookSummary
{
    uint64_t Orders = 0;
    uint64_t Bids = 0;
    uint64_t Asks = 0;
    uint64_t BidPrice = 0;
    uint64_t BidVolume = 0;
    uint64_t AskPrice = 0;
    uint64_t AskVolume = 0;

    bool operator==(const TBookSummary&) const = default;
};

void LiveInit(std::string_view watch);
uint64_t ProcessMessage(std::span<const uint8_t> message);
bool Shadow();
uint64_t RealSignals();
std::optional<TBookSummary> Summary();
uint64_t BookHash();
size_t LivePrefetch(std::span<const void*> out);

} // namespace coldpath::handler
