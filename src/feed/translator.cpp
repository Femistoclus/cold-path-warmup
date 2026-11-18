#include "feed/translator.h"

#include "common/args.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <stdexcept>

namespace coldpath::feed {
namespace {

constexpr std::array<double, 13> PowersOfTen = {1e0, 1e1, 1e2, 1e3, 1e4, 1e5, 1e6, 1e7, 1e8, 1e9, 1e10, 1e11, 1e12};

// Round half to even.
uint32_t Clip(double value)
{
    return static_cast<uint32_t>(std::clamp(std::nearbyint(value), 1.0, 4294967295.0));
}

} // namespace

double JsonNumber(const nlohmann::json& value)
{
    return value.is_string() ? ParseNumber<double>(value.get<std::string>()) : value.get<double>();
}

uint64_t JsonId(const nlohmann::json& value)
{
    return value.is_string() ? ParseNumber<uint64_t>(value.get<std::string>()) : value.get<uint64_t>();
}

TBitstampOrder ParseBitstampOrder(const nlohmann::json& data)
{
    TBitstampOrder order{.Id = JsonId(data.at("id")),
        .Buy = data.value("order_type", 0) == 0,
        .Price = JsonNumber(data.at("price")),
        .Amount = JsonNumber(data.at("amount"))};
    if (data.contains("amount_traded")) {
        order.Traded = JsonNumber(data["amount_traded"]);
    }
    if (const auto it = data.find("amount_at_create"); it != data.end()) {
        // A zero amount_at_create counts as 1.
        order.AmountAtCreate = it->is_number() && it->get<double>() == 0 ? 1 : JsonNumber(*it);
    }
    return order;
}

EOrderEvent ParseOrderEvent(std::string_view event)
{
    if (event == "order_created") {
        return EOrderEvent::Created;
    }
    if (event == "order_deleted") {
        return EOrderEvent::Deleted;
    }
    if (event == "order_changed") {
        return EOrderEvent::Changed;
    }
    return EOrderEvent::Other;
}

TTranslator::TTranslator(std::vector<std::string> pairs)
    : Pairs_(std::move(pairs))
    , Scales_(Pairs_.size() + 1)
    , Orders_(Pairs_.size() + 1)
{ }

std::optional<uint16_t> TTranslator::Locate(std::string_view pair) const
{
    const auto it = std::ranges::find(Pairs_, pair);
    if (it == Pairs_.end()) {
        return std::nullopt;
    }
    return static_cast<uint16_t>(it - Pairs_.begin() + 1);
}

const std::string& TTranslator::Pair(uint16_t locate) const
{
    return Pairs_.at(locate - 1U);
}

const std::vector<std::string>& TTranslator::Pairs() const
{
    return Pairs_;
}

TTranslator::TScale TTranslator::ScaleFor(uint16_t locate, const TBitstampOrder& order)
{
    std::optional<TScale>& scale = Scales_[locate];
    if (!scale) {
        const double amount = order.Amount > 0 ? order.Amount : order.AmountAtCreate;
        const auto price = static_cast<int>(std::floor(std::log10(2147483648.0 / 20 / std::max(order.Price, 1e-12))));
        const auto size = 5 - static_cast<int>(std::floor(std::log10(std::max(amount, 1e-12))));
        scale = TScale{.Price = std::clamp(price, 0, 12), .Amount = std::clamp(size, 0, 10)};
    }
    return *scale;
}

std::vector<itch::TMessage>
TTranslator::Translate(uint16_t locate, EOrderEvent event, const TBitstampOrder& order, uint64_t ns)
{
    const TScale scale = ScaleFor(locate, order);
    const uint32_t price = Clip(order.Price * PowersOfTen[static_cast<size_t>(scale.Price)]);
    const uint32_t shares = order.Amount <= 0 ? 0 : Clip(order.Amount * PowersOfTen[static_cast<size_t>(scale.Amount)]);
    auto& orders = Orders_[locate];
    const auto it = orders.find(order.Id);
    std::vector<itch::TMessage> out;

    switch (event) {
        case EOrderEvent::Created:
            if (it != orders.end() || shares == 0) {
                ++Skipped_;
                break;
            }
            ++Ref_;
            orders.emplace(order.Id,
                TOrder{.Ref = Ref_,
                    .Side = order.Buy ? 'B' : 'S',
                    .Price = price,
                    .Shares = shares,
                    .Traded = order.Traded,
                    .Arrival = ++Arrivals_});
            out.push_back(
                itch::AddOrder(ns, locate, Ref_, order.Buy ? 'B' : 'S', shares, itch::StockName(Pair(locate)), price));
            break;
        case EOrderEvent::Deleted:
            if (it == orders.end()) {
                ++Unknown_;
                break;
            }
            out.push_back(itch::OrderDelete(ns, locate, it->second.Ref));
            orders.erase(it);
            break;
        case EOrderEvent::Changed: {
            if (it == orders.end()) {
                ++Unknown_;
                break;
            }
            TOrder& known = it->second;
            if (shares == 0) {
                out.push_back(itch::OrderDelete(ns, locate, known.Ref));
                orders.erase(it);
            } else if (price == known.Price && shares < known.Shares) {
                if (order.Traded != known.Traded) {
                    out.push_back(itch::OrderExecuted(ns, locate, known.Ref, known.Shares - shares, ++Match_));
                } else {
                    out.push_back(itch::OrderCancel(ns, locate, known.Ref, known.Shares - shares));
                }
                known.Shares = shares;
                known.Traded = order.Traded;
            } else if (price == known.Price && shares == known.Shares) {
                ++Skipped_;
            } else {
                ++Ref_;
                out.push_back(itch::OrderReplace(ns,
                    locate,
                    {.OldRef = known.Ref, .NewRef = Ref_, .Shares = shares, .Price = price}));
                known.Ref = Ref_;
                known.Price = price;
                known.Shares = shares;
                known.Traded = order.Traded;
            }
            break;
        }
        case EOrderEvent::Other:
            ++Skipped_;
            break;
    }
    for (const itch::TMessage& message : out) {
        ++Kinds_[message.Type()];
    }
    return out;
}

std::vector<itch::TMessage> TTranslator::Snapshot(uint16_t locate, uint64_t ns) const
{
    std::vector<const TOrder*> orders;
    for (const auto& [id, order] : Orders_[locate]) {
        orders.push_back(&order);
    }
    std::ranges::sort(orders, {}, &TOrder::Arrival);
    std::vector<itch::TMessage> out;
    out.reserve(orders.size());
    const itch::TStockName name = itch::StockName(Pair(locate));
    for (const TOrder* order : orders) {
        out.push_back(itch::AddOrder(ns, locate, order->Ref, order->Side, order->Shares, name, order->Price));
    }
    return out;
}

size_t TTranslator::OrderCount() const
{
    size_t count = 0;
    for (const auto& orders : Orders_) {
        count += orders.size();
    }
    return count;
}

uint64_t TTranslator::Unknown() const
{
    return Unknown_;
}

uint64_t TTranslator::Skipped() const
{
    return Skipped_;
}

const std::map<char, uint64_t>& TTranslator::Kinds() const
{
    return Kinds_;
}

std::vector<std::pair<std::string, std::pair<int, int>>> TTranslator::Scales() const
{
    std::vector<std::pair<std::string, std::pair<int, int>>> out;
    for (size_t locate = 1; locate < Scales_.size(); ++locate) {
        if (const std::optional<TScale>& scale = Scales_[locate]) {
            out.emplace_back(Pairs_[locate - 1], std::pair(scale->Price, scale->Amount));
        }
    }
    return out;
}

} // namespace coldpath::feed
