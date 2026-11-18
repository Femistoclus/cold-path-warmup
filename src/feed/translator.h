#pragma once

#include "common/itch.h"

#include <map>
#include <nlohmann/json.hpp>
#include <optional>
#include <string>
#include <string_view>
#include <unordered_map>
#include <utility>
#include <vector>

namespace coldpath::feed {

enum class EOrderEvent : uint8_t {
    Created,
    Deleted,
    Changed,
    Other,
};

struct TBitstampOrder
{
    uint64_t Id = 0;
    bool Buy = true;
    double Price = 0;
    double Amount = 0;
    double Traded = 0;
    double AmountAtCreate = 1;
};

// Bitstamp sends fields either as numbers or as strings.
double JsonNumber(const nlohmann::json& value);
uint64_t JsonId(const nlohmann::json& value);
TBitstampOrder ParseBitstampOrder(const nlohmann::json& data);
EOrderEvent ParseOrderEvent(std::string_view event);

// changed → E if amount_traded grew; X if the remainder shrank without a fill; U if the price changed
// or the remainder grew; D if the remainder is 0. The price and volume scale (10^k) comes from the pair's first event.
class TTranslator
{
public:
    explicit TTranslator(std::vector<std::string> pairs);

    std::optional<uint16_t> Locate(std::string_view pair) const;
    const std::string& Pair(uint16_t locate) const;
    const std::vector<std::string>& Pairs() const;

    std::vector<itch::TMessage> Translate(uint16_t locate, EOrderEvent event, const TBitstampOrder& order, uint64_t ns);
    std::vector<itch::TMessage> Snapshot(uint16_t locate, uint64_t ns) const;

    size_t OrderCount() const;
    uint64_t Unknown() const;
    uint64_t Skipped() const;
    const std::map<char, uint64_t>& Kinds() const;
    std::vector<std::pair<std::string, std::pair<int, int>>> Scales() const;

private:
    struct TScale
    {
        int Price = 0;
        int Amount = 0;
    };

    struct TOrder
    {
        uint64_t Ref = 0;
        char Side = 'B';
        uint32_t Price = 0;
        uint32_t Shares = 0;
        double Traded = 0;
        uint64_t Arrival = 0;
    };

    std::vector<std::string> Pairs_;
    std::vector<std::optional<TScale>> Scales_;
    std::vector<std::unordered_map<uint64_t, TOrder>> Orders_;
    uint64_t Ref_ = 0;
    uint64_t Match_ = 0;
    uint64_t Arrivals_ = 0;
    uint64_t Unknown_ = 0;
    uint64_t Skipped_ = 0;
    std::map<char, uint64_t> Kinds_;

    TScale ScaleFor(uint16_t locate, const TBitstampOrder& order);
};

} // namespace coldpath::feed
