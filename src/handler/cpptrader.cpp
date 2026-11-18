// ITCH → MarketManager glue, as in CppTrader/performance/market_manager.cpp (CppTrader, MIT).
#include "handler/handler.h"

#include "common/args.h"
#include "common/clock.h"
#include "common/itch.h"
#include "common/print.h"
#include "handler/load.h"
#include "trader/matching/market_manager.h"
#include "trader/providers/nasdaq/itch_handler.h"

#include <algorithm>
#include <array>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <format>
#include <optional>
#include <span>
#include <stdexcept>
#include <string>
#include <unistd.h>
#include <utility>

namespace coldpath::handler {
namespace {

using CppTrader::Matching::LevelNode;
using CppTrader::Matching::MarketHandler;
using CppTrader::Matching::MarketManager;
using CppTrader::Matching::Order;
using CppTrader::Matching::OrderBook;
using CppTrader::Matching::OrderNode;
using CppTrader::Matching::OrderSide;
using CppTrader::Matching::Symbol;
namespace Nasdaq = CppTrader::ITCH;

constexpr uint32_t NoSymbol = UINT32_MAX;
// Feed order ids start at 1, so the warm-up order id never collides with them.
constexpr uint64_t ShadowRef = 1ULL << 62;

class TStrategy : public MarketHandler
{
public:
    uint32_t Watch = NoSymbol;
    bool DryRun = false;
    uint64_t Checksum = 0;
    uint64_t Signals = 0;
    uint64_t RealSignals = 0;
#if COLDPATH_LOAD
    int64_t LastSignal = 0;
#endif

protected:
    void onUpdateOrderBook(const OrderBook& book, bool top) override
    {
        if (!top || book.symbol().Id != Watch) {
            return;
        }
        const LevelNode* bid = book.best_bid();
        const LevelNode* ask = book.best_ask();
        if (bid == nullptr || ask == nullptr) {
            return;
        }
        const int64_t signal = static_cast<int64_t>(bid->TotalVolume) - static_cast<int64_t>(ask->TotalVolume);
        Checksum += static_cast<uint64_t>(signal);
#if COLDPATH_LOAD
        LastSignal = signal;
#endif
        ++Signals;
        if (!DryRun) {
            ++RealSignals;
        }
        if (!DryRun && signal > 0) {
            Sent_[SentCount_++ & 1023U] = signal;
        }
    }

private:
    std::array<int64_t, 1024> Sent_{};
    unsigned SentCount_ = 0;
};

class TFeed : public Nasdaq::ITCHHandler
{
public:
    explicit TFeed(MarketManager& market)
        : Market_(market)
    { }

    size_t Messages = 0;
    size_t Errors = 0;
    uint64_t MaxRef = 0;
    uint64_t LastTs = 0;
    uint64_t Until = 0;
    bool SessionEnd = false;
    std::vector<uint32_t> Adds = std::vector<uint32_t>(65536);
    std::optional<itch::TStockName> WatchName;
    uint32_t WatchLocate = NoSymbol;

protected:
    bool onMessage(const Nasdaq::SystemEventMessage& m) override
    {
        if (Until != 0 && m.EventCode == 'M') {
            SessionEnd = true;
        }
        return Seen(m.Timestamp);
    }

    bool onMessage(const Nasdaq::StockDirectoryMessage& m) override
    {
        const Symbol symbol(m.StockLocate, m.Stock);
        Market_.AddSymbol(symbol);
        Market_.AddOrderBook(symbol);
        if (WatchName && std::memcmp(m.Stock, WatchName->data(), WatchName->size()) == 0) {
            WatchLocate = m.StockLocate;
        }
        return Seen(m.Timestamp);
    }

    bool onMessage(const Nasdaq::StockTradingActionMessage& m) override
    {
        return Seen(m.Timestamp);
    }

    bool onMessage(const Nasdaq::RegSHOMessage& m) override
    {
        return Seen(m.Timestamp);
    }

    bool onMessage(const Nasdaq::MarketParticipantPositionMessage& m) override
    {
        return Seen(m.Timestamp);
    }

    bool onMessage(const Nasdaq::MWCBDeclineMessage& m) override
    {
        return Seen(m.Timestamp);
    }

    bool onMessage(const Nasdaq::MWCBStatusMessage& m) override
    {
        return Seen(m.Timestamp);
    }

    bool onMessage(const Nasdaq::IPOQuotingMessage& m) override
    {
        return Seen(m.Timestamp);
    }

    bool onMessage(const Nasdaq::AddOrderMessage& m) override
    {
        Add(m.OrderReferenceNumber, m.StockLocate, m.BuySellIndicator, m.Price, m.Shares);
        return Seen(m.Timestamp);
    }

    bool onMessage(const Nasdaq::AddOrderMPIDMessage& m) override
    {
        Add(m.OrderReferenceNumber, m.StockLocate, m.BuySellIndicator, m.Price, m.Shares);
        return Seen(m.Timestamp);
    }

    bool onMessage(const Nasdaq::OrderExecutedMessage& m) override
    {
        Market_.ExecuteOrder(m.OrderReferenceNumber, m.ExecutedShares);
        return Seen(m.Timestamp);
    }

    bool onMessage(const Nasdaq::OrderExecutedWithPriceMessage& m) override
    {
        Market_.ExecuteOrder(m.OrderReferenceNumber, m.ExecutionPrice, m.ExecutedShares);
        return Seen(m.Timestamp);
    }

    bool onMessage(const Nasdaq::OrderCancelMessage& m) override
    {
        Market_.ReduceOrder(m.OrderReferenceNumber, m.CanceledShares);
        return Seen(m.Timestamp);
    }

    bool onMessage(const Nasdaq::OrderDeleteMessage& m) override
    {
        Market_.DeleteOrder(m.OrderReferenceNumber);
        return Seen(m.Timestamp);
    }

    bool onMessage(const Nasdaq::OrderReplaceMessage& m) override
    {
        Market_.ReplaceOrder(m.OriginalOrderReferenceNumber, m.NewOrderReferenceNumber, m.Price, m.Shares);
        if (m.NewOrderReferenceNumber > MaxRef) {
            MaxRef = m.NewOrderReferenceNumber;
        }
        return Seen(m.Timestamp);
    }

    bool onMessage(const Nasdaq::TradeMessage& m) override
    {
        return Seen(m.Timestamp);
    }

    bool onMessage(const Nasdaq::CrossTradeMessage& m) override
    {
        return Seen(m.Timestamp);
    }

    bool onMessage(const Nasdaq::BrokenTradeMessage& m) override
    {
        return Seen(m.Timestamp);
    }

    bool onMessage(const Nasdaq::NOIIMessage& m) override
    {
        return Seen(m.Timestamp);
    }

    bool onMessage(const Nasdaq::RPIIMessage& m) override
    {
        return Seen(m.Timestamp);
    }

    bool onMessage(const Nasdaq::LULDAuctionCollarMessage& m) override
    {
        return Seen(m.Timestamp);
    }

    bool onMessage(const Nasdaq::UnknownMessage& /*m*/) override
    {
        ++Errors;
        return true;
    }

private:
    MarketManager& Market_;

    // Returning false stops Process. CppTrader reads only the upper 3 of the 6 timestamp bytes
    // (ts >> 24), so shift it back; precision is ~17 ms.
    bool Seen(uint64_t ts)
    {
        ++Messages;
        LastTs = ts << 24;
        return !(Until != 0 && (SessionEnd || LastTs >= Until));
    }

    void Add(uint64_t ref, uint16_t locate, char side, uint32_t price, uint32_t shares)
    {
        Market_.AddOrder(Order::Limit(ref, locate, side == 'B' ? OrderSide::BUY : OrderSide::SELL, price, shares));
        ++Adds[locate];
        if (ref > MaxRef) {
            MaxRef = ref;
        }
    }
};

// Fixed-size event (55 bytes): it occupies the same cache lines on every call.
struct TEvent
{
    std::array<uint8_t, 36> Add;
    std::array<uint8_t, 19> Delete;
};

// Plain globals rather than function-local statics: no guard check in the measured code.
TStrategy Strategy;
MarketManager Market(Strategy); // NOLINT(bugprone-throwing-static-initialization)
TFeed Feed(Market);             // NOLINT(bugprone-throwing-static-initialization)
std::array<TEvent, 2> Events;
const OrderBook* EventBook = nullptr;
uint64_t EventRef = 0;
std::string InfoText;
#if COLDPATH_LOAD
TLoad Load;
constexpr size_t PrefetchCapacity = 128 + 16 + COLDPATH_DATA_LINES;
#else
constexpr size_t PrefetchCapacity = 128;
#endif

void Process(std::span<const uint8_t> message)
{
    Feed.ProcessMessage(const_cast<uint8_t*>(message.data()), message.size());
}

TEvent MakeEvent(const Symbol& symbol, uint64_t ref, uint32_t price, uint64_t ts)
{
    itch::TStockName name;
    std::memcpy(name.data(), symbol.Name, name.size());
    const auto locate = static_cast<uint16_t>(symbol.Id);
    TEvent event{};
    std::ranges::copy(itch::AddOrder(ts, locate, ref, 'B', 100, name, price).Bytes(), event.Add.begin());
    std::ranges::copy(itch::OrderDelete(ts, locate, ref).Bytes(), event.Delete.begin());
    return event;
}

TBookSummary Summarize(const OrderBook& book)
{
    return {.Orders = Market.orders().size(),
        .Bids = book.bids().size(),
        .Asks = book.asks().size(),
        .BidPrice = book.best_bid()->Price,
        .BidVolume = book.best_bid()->TotalVolume,
        .AskPrice = book.best_ask()->Price,
        .AskVolume = book.best_ask()->TotalVolume};
}

const OrderBook* WatchedBook()
{
    if (Strategy.Watch == NoSymbol) {
        return nullptr;
    }
    const OrderBook* book = Market.GetOrderBook(Strategy.Watch);
    return book != nullptr && book->best_bid() != nullptr && book->best_ask() != nullptr ? book : nullptr;
}

class TLineCollector
{
public:
    explicit TLineCollector(std::span<const void*> out)
        : Out_(out)
    { }

    void Add(const void* p, size_t size)
    {
        if (p == nullptr) {
            return;
        }
        const auto* begin = static_cast<const char*>(p);
        const auto offset = static_cast<ptrdiff_t>(reinterpret_cast<uintptr_t>(p) & 63U);
        for (const char* line = begin - offset; line < begin + size && Size_ < Out_.size(); line += 64) {
            const auto used = Out_.first(Size_);
            if (std::ranges::find(used, line) == used.end()) {
                Out_[Size_++] = line;
            }
        }
    }

    void AddBookPath(const OrderBook& book, bool fullAskPath)
    {
        const uint32_t id = book.symbol().Id;
        Add(&Strategy, 64);
        Add(&Feed, sizeof(Feed));
        Add(&Feed.Adds[id], sizeof(uint32_t));
        Add(&Market, sizeof(Market));
        Add(*reinterpret_cast<const void* const*>(&Feed), 256);
        Add(*reinterpret_cast<const void* const*>(&Strategy), 128);
        Add(static_cast<const void*>(&Market.order_books()[id]), sizeof(void*));
        Add(&book, sizeof(OrderBook));
        for (const LevelNode* level = book.best_bid(); level != nullptr; level = level->parent) {
            Add(level, sizeof(LevelNode));
        }
        if (fullAskPath) {
            for (const LevelNode* level = book.best_ask(); level != nullptr; level = level->parent) {
                Add(level, sizeof(LevelNode));
            }
        } else {
            Add(book.best_ask(), sizeof(LevelNode));
        }
    }

    size_t Size() const
    {
        return Size_;
    }

private:
    std::span<const void*> Out_;
    size_t Size_ = 0;
};

uint64_t ParseUntil(std::string_view text)
{
    const auto parts = SplitList(text, ':');
    if (parts.size() < 2 || parts.size() > 3) {
        throw std::invalid_argument("ITCH_UNTIL: expected HH:MM or HH:MM:SS");
    }
    uint64_t seconds = 0;
    for (const std::string_view part : parts) {
        seconds = seconds * 60 + ParseNumber<uint64_t>(part);
    }
    if (parts.size() == 2) {
        seconds *= 60;
    }
    return seconds * NsPerSecond;
}

std::string EnvOr(const char* name, std::string_view fallback)
{
    const char* value = std::getenv(name);
    return value != nullptr ? std::string(value) : std::string(fallback);
}

bool ReplayFile(const std::string& path)
{
    const int fd = open(path.c_str(), O_RDONLY | O_CLOEXEC);
    if (fd < 0) {
        std::perror(path.c_str());
        Eprintln("no ITCH data: run ./get-itch.sh or set ITCH_FILE");
        return false;
    }
    std::vector<uint8_t> buffer(1U << 20);
    for (ssize_t n = read(fd, buffer.data(), buffer.size()); n > 0; n = read(fd, buffer.data(), buffer.size())) {
        if (!Feed.Process(buffer.data(), static_cast<size_t>(n))) {
            break;
        }
    }
    close(fd);
    return true;
}

} // namespace

// The event is an Add Order at the best bid and a Delete of that order: the book ends up unchanged.
bool Init([[maybe_unused]] unsigned seed)
{
    const std::string path = EnvOr("ITCH_FILE", "data/20190730.BX_ITCH_50");
    try {
        Feed.Until = ParseUntil(EnvOr("ITCH_UNTIL", "10:00:00"));
    } catch (const std::exception& e) {
        Eprintln("{}", e.what());
        return false;
    }
    if (!ReplayFile(path)) {
        return false;
    }
    Feed.Until = 0;
    if (Feed.Messages == 0 || Feed.Errors > 0) {
        Eprintln("{}: messages {}, parse errors {}", path, Feed.Messages, Feed.Errors);
        return false;
    }

    const OrderBook* book = nullptr;
    uint32_t bestAdds = 0;
    for (uint32_t id = 0; id < Feed.Adds.size(); ++id) {
        const OrderBook* candidate = Market.GetOrderBook(id);
        if (candidate != nullptr && candidate->best_bid() != nullptr && candidate->best_ask() != nullptr &&
            Feed.Adds[id] > bestAdds)
        {
            book = candidate;
            bestAdds = Feed.Adds[id];
        }
    }
    if (book == nullptr) {
        Eprintln("no symbol in the book has both bid and ask");
        return false;
    }

    // The "other" event is $0.10 below the best bid: best price and volume stay the same.
    const Symbol& symbol = book->symbol();
    const auto bid = static_cast<uint32_t>(book->best_bid()->Price);
    const uint32_t far = bid > 2000 ? bid - 1000 : bid / 2;
    Events[std::to_underlying(EEvent::Real)] = MakeEvent(symbol, Feed.MaxRef + 1, bid, Feed.LastTs);
    Events[std::to_underlying(EEvent::Other)] = MakeEvent(symbol, Feed.MaxRef + 2, far, Feed.LastTs);
    Strategy.Watch = symbol.Id;
    EventBook = book;
    EventRef = Feed.MaxRef + 1;

#if COLDPATH_LOAD
    if (!Load.Init(seed)) {
        return false;
    }
    const uint64_t sends = Load.RealSends();
#endif
    const TBookSummary before = Summarize(*book);
    for (int i = 0; i < 3; ++i) {
        Handle(EEvent::Real, true);
        Handle(EEvent::Other, true);
    }
    if (Summarize(*book) != before || Strategy.Signals == 0) {
        Eprintln("check failed: the book changed or the strategy did not fire");
        return false;
    }
#if COLDPATH_LOAD
    if (Load.RealSends() != sends) {
        Eprintln("check failed: a dry run sent a real order");
        return false;
    }
#endif

    const std::string_view file = std::string_view(path).substr(path.rfind('/') + 1);
    std::string_view name(symbol.Name, sizeof(symbol.Name));
    name = name.substr(0, name.find_last_not_of(' ') + 1);
    const uint64_t t = Feed.LastTs / NsPerSecond;
    InfoText = std::format("CppTrader; {}: {} messages up to {:02}:{:02}:{:02}; symbol {} (Add Order: {}), "
                           "bid {:.4f} x {} / ask {:.4f} x {}, bid/ask levels {}/{}, orders in all books {}",
        file,
        Feed.Messages,
        t / 3600,
        t / 60 % 60,
        t % 60,
        name,
        bestAdds,
        static_cast<double>(before.BidPrice) / 1e4,
        before.BidVolume,
        static_cast<double>(before.AskPrice) / 1e4,
        before.AskVolume,
        before.Bids,
        before.Asks,
        before.Orders);
#if COLDPATH_LOAD
    InfoText += "; " + Load.Describe();
#endif
    return true;
}

uint64_t Handle(EEvent event, bool dryRun)
{
    Strategy.DryRun = dryRun;
    const TEvent& e = Events[std::to_underlying(event)];
    Process(e.Add);
    Process(e.Delete);
#if COLDPATH_LOAD
    Load.Run(Strategy.LastSignal, dryRun);
#endif
    return Strategy.Checksum;
}

std::string_view Info()
{
    return InfoText;
}

// Addresses are stable: the book is unchanged after the event, and the order reuses the same pool block and hash slot.
TPrefetchData PrefetchData()
{
    std::array<const void*, PrefetchCapacity> lines{};
    TLineCollector collector(lines);
    const OrderBook& book = *EventBook;
    collector.AddBookPath(book, false);
    collector.Add(book.best_bid()->OrderList.back(), sizeof(OrderNode));
#if COLDPATH_LOAD
    for (const void* line : Load.Lines()) {
        collector.Add(line, 1);
    }
#endif

    const TEvent& e = Events[std::to_underlying(EEvent::Real)];
    const bool saved = Strategy.DryRun;
    Strategy.DryRun = true;
    Process(e.Add);
    if (const Order* order = Market.GetOrder(EventRef); order != nullptr) {
        // The pool node starts 16 bytes before the order itself.
        collector.Add(reinterpret_cast<const char*>(order) - 16, sizeof(OrderNode) + 16);
    }
    const size_t generic = collector.Size();
    if (const auto it = Market.orders().find(EventRef); it != Market.orders().end()) {
        collector.Add(&*it, 32);
    }
    Process(e.Delete);
    Strategy.DryRun = saved;
    collector.Add(&e, sizeof(e));
    return {.Lines = std::vector<const void*>(lines.begin(), lines.begin() + static_cast<ptrdiff_t>(collector.Size())),
        .Generic = generic};
}

void LiveInit(std::string_view watch)
{
    Feed.WatchName = itch::StockName(watch);
    InfoText = std::format("CppTrader, online; watching {}", std::string_view(Feed.WatchName->data(), 8));
#if COLDPATH_LOAD
    if (!Load.Init(1)) {
        throw std::runtime_error("handler load failed to initialize");
    }
    InfoText += "; " + Load.Describe();
#endif
}

uint64_t ProcessMessage(std::span<const uint8_t> message)
{
    Strategy.DryRun = false;
#if COLDPATH_LOAD
    const uint64_t signals = Strategy.Signals;
#endif
    Process(message);
    if (Strategy.Watch == NoSymbol && Feed.WatchLocate != NoSymbol) {
        Strategy.Watch = Feed.WatchLocate;
    }
#if COLDPATH_LOAD
    if (Strategy.Signals != signals) {
        Load.Run(Strategy.LastSignal, false);
    }
#endif
    return Strategy.Checksum;
}

bool Shadow()
{
    const OrderBook* book = WatchedBook();
    if (book == nullptr) {
        return false;
    }
    const TEvent e = MakeEvent(book->symbol(), ShadowRef, static_cast<uint32_t>(book->best_bid()->Price), Feed.LastTs);
    Strategy.DryRun = true;
    Process(e.Add);
    Process(e.Delete);
#if COLDPATH_LOAD
    Load.Run(Strategy.LastSignal, true);
#endif
    Strategy.DryRun = false;
    return true;
}

uint64_t RealSignals()
{
    return Strategy.RealSignals;
}

std::optional<TBookSummary> Summary()
{
    const OrderBook* book = WatchedBook();
    if (book == nullptr) {
        return std::nullopt;
    }
    return Summarize(*book);
}

uint64_t BookHash()
{
    if (Strategy.Watch == NoSymbol) {
        return 0;
    }
    const OrderBook* book = Market.GetOrderBook(Strategy.Watch);
    if (book == nullptr) {
        return 0;
    }
    uint64_t hash = 1469598103934665603ULL;
    const auto mix = [&hash](uint64_t value) {
        for (unsigned i = 0; i < 8; ++i) {
            hash = (hash ^ ((value >> (8 * i)) & 0xFFU)) * 1099511628211ULL;
        }
    };
    for (const auto& level : book->bids()) {
        mix(level.Price);
        mix(level.TotalVolume);
        mix(level.Orders);
    }
    mix(0xB1D5);
    for (const auto& level : book->asks()) {
        mix(level.Price);
        mix(level.TotalVolume);
        mix(level.Orders);
    }
    return hash;
}

size_t LivePrefetch(std::span<const void*> out)
{
    if (Strategy.Watch == NoSymbol) {
        return 0;
    }
    const OrderBook* book = Market.GetOrderBook(Strategy.Watch);
    if (book == nullptr || book->best_bid() == nullptr) {
        return 0;
    }
    TLineCollector collector(out);
    collector.AddBookPath(*book, true);
    collector.Add(book->best_bid()->OrderList.back(), sizeof(OrderNode));
    if (book->best_ask() != nullptr) {
        collector.Add(book->best_ask()->OrderList.back(), sizeof(OrderNode));
    }
#if COLDPATH_LOAD
    for (const void* line : Load.Lines()) {
        collector.Add(line, 1);
    }
#endif
    return collector.Size();
}

} // namespace coldpath::handler
