#include "common/clock.h"
#include "common/itch.h"
#include "feed/feed.h"
#include "feed/websocket.h"

#include <algorithm>
#include <chrono>
#include <fstream>
#include <memory>
#include <nlohmann/json.hpp>
#include <set>
#include <thread>
#include <zlib.h>

namespace coldpath::feed {
namespace {

using nlohmann::json;
using nlohmann::ordered_json;

constexpr std::string_view LiveOrders = "live_orders_";
constexpr std::string_view OrderBook = "detail_order_book_";
constexpr size_t MaxLagSamples = 200000;

std::string Subscription(std::string_view action, std::string_view channel)
{
    return ordered_json{{"event", std::format("bts:{}", action)}, {"data", {{"channel", channel}}}}.dump();
}

double Median(std::vector<double> values)
{
    std::ranges::sort(values);
    const size_t n = values.size();
    return n % 2 == 1 ? values[n / 2] : (values[n / 2 - 1] + values[n / 2]) / 2;
}

std::string RecordingBase(const std::string& out)
{
    return out.ends_with(".pkt") ? out.substr(0, out.size() - 4) : out;
}

class TLiveFeed
{
public:
    explicit TLiveFeed(const TArgs& args)
        : Pairs_(PairsFrom(args))
        , Solo_(ToStrings(SplitList(args.Text("solo", ""))))
        , Out_(args.Text("out", ""))
        , Translator_(Pairs_)
        , Sender_(Pairs_, Solo_)
        , Meta_({{"source", "bitstamp live_orders"}, {"pairs", Pairs_}, {"solo", Solo_}, {"start_ns", RealtimeNs()}})
        , Recorder_(Out_, Meta_)
        , Raw_(Out_.empty() ? nullptr : gzopen((RecordingBase(Out_) + ".jsonl.gz").c_str(), "wb1"), &gzclose)
        , Paused_(args.Flag("start-paused"))
    {
        const double duration = args.Double("duration", 0);
        EndNs_ = duration > 0 ? MonotonicNs() + static_cast<uint64_t>(duration * 1e9) : UINT64_MAX;
    }

    void Run()
    {
        if (!Paused_) {
            SendBlockStart(Sender_, Recorder_, Pairs_, &Translator_);
            Blocks_ = 1;
        }
        while (Running()) {
            ++Reconnects_;
            try {
                Session();
            } catch (const std::exception& e) {
                Log("disconnected: {}", e.what());
                std::this_thread::sleep_for(std::chrono::seconds(1));
            }
        }
        if (!Paused_) {
            SendBlockEnd(Sender_, Recorder_);
        }
        Recorder_.Close();
        Raw_.reset();
        WriteMeta();
        Log("done: events {}, blocks {}, unknown {}, reconnects {}", Seq_, Blocks_, Translator_.Unknown(), Reconnects_);
    }

private:
    std::vector<std::string> Pairs_;
    std::vector<std::string> Solo_;
    std::string Out_;
    TTranslator Translator_;
    TSender Sender_;
    ordered_json Meta_;
    TRecorder Recorder_;
    std::unique_ptr<gzFile_s, decltype(&gzclose)> Raw_;
    bool Paused_ = false;
    uint64_t EndNs_ = UINT64_MAX;
    uint64_t Seq_ = 0;
    int64_t Reconnects_ = -1;
    uint64_t Blocks_ = 0;
    std::vector<double> LagMs_;
    std::set<std::string> SnapshotsLoaded_;
    uint64_t LastReportNs_ = MonotonicNs();

    static std::vector<std::string> ToStrings(const std::vector<std::string_view>& items)
    {
        return {items.begin(), items.end()};
    }

    static std::vector<std::string> PairsFrom(const TArgs& args)
    {
        const auto pairs = args.Get("pairs");
        return pairs && !pairs->empty() ? ToStrings(SplitList(*pairs)) : DefaultPairs();
    }

    bool Running() const
    {
        return !Signals.Stop.load() && MonotonicNs() < EndNs_;
    }

    void Session()
    {
        TWebSocket socket("ws.bitstamp.net", 443, "/");
        for (const std::string& pair : Pairs_) {
            socket.SendText(Subscription("subscribe", std::string(LiveOrders) + pair));
            socket.SendText(Subscription("subscribe", std::string(OrderBook) + pair));
        }
        std::set<std::string> wantSnapshot(Pairs_.begin(), Pairs_.end());
        while (Running()) {
            HandleBlockSignals();
            const auto text = socket.Receive(std::chrono::milliseconds(100));
            if (!text) {
                continue;
            }
            const uint64_t ns = RealtimeNs();
            if (Raw_) {
                const std::string line = std::format("{}\t{}\n", ns, *text);
                gzwrite(Raw_.get(), line.data(), static_cast<unsigned>(line.size()));
            }
            const json message = json::parse(*text);
            const std::string event = message.value("event", "");
            const std::string channel = message.value("channel", "");
            if (event == "bts:request_reconnect") {
                throw TConnectionError("exchange requested a reconnect");
            }
            if (channel.starts_with(OrderBook) && event == "data") {
                const std::string pair = channel.substr(OrderBook.size());
                if (wantSnapshot.erase(pair) != 0) {
                    socket.SendText(Subscription("unsubscribe", channel));
                    LoadSnapshot(pair, message.at("data"), ns);
                }
                continue;
            }
            if (!event.starts_with("order_") || !channel.starts_with(LiveOrders)) {
                continue;
            }
            const auto locate = Translator_.Locate(channel.substr(LiveOrders.size()));
            if (!locate) {
                continue;
            }
            const json& data = message.at("data");
            const auto messages = Translator_.Translate(*locate, ParseOrderEvent(event), ParseBitstampOrder(data), ns);
            if (messages.empty() || Paused_) {
                continue;
            }
            if (LagMs_.size() < MaxLagSamples && data.contains("microtimestamp")) {
                LagMs_.push_back(static_cast<double>(ns) / 1e6 - JsonNumber(data["microtimestamp"]) / 1e3);
            }
            Publish(*locate, messages);
            ReportEveryMinute();
        }
    }

    void LoadSnapshot(const std::string& pair, const json& data, uint64_t ns)
    {
        const auto locate = Translator_.Locate(pair);
        if (!locate) {
            return;
        }
        for (const auto& [side, key] : {std::pair(true, "bids"), std::pair(false, "asks")}) {
            for (const json& level : data.value(key, json::array())) {
                const TBitstampOrder order{.Id = JsonId(level.at(2)),
                    .Buy = side,
                    .Price = JsonNumber(level.at(0)),
                    .Amount = JsonNumber(level.at(1))};
                const auto messages = Translator_.Translate(*locate, EOrderEvent::Created, order, ns);
                if (!messages.empty() && !Paused_) {
                    Publish(*locate, messages);
                }
            }
        }
        SnapshotsLoaded_.insert(pair);
    }

    void Publish(uint16_t locate, const std::vector<itch::TMessage>& messages)
    {
        ++Seq_;
        const auto packet = itch::MoldPacket(Seq_, messages);
        const uint64_t sentAt = RealtimeNs();
        Sender_.Send(packet, locate);
        Recorder_.Write({.SentNs = sentAt, .Seq = Seq_, .Locate = locate}, packet);
    }

    void HandleBlockSignals()
    {
        if (Signals.EndBlock.exchange(false) && !Paused_) {
            SendBlockEnd(Sender_, Recorder_);
            Paused_ = true;
            Log("end of block {}, events {}", Blocks_, Seq_);
        }
        if (Signals.StartBlock.exchange(false) && Paused_) {
            SendBlockStart(Sender_, Recorder_, Pairs_, &Translator_);
            Paused_ = false;
            ++Blocks_;
            Log("start of block {}: orders in snapshot {}", Blocks_, Translator_.OrderCount());
        }
    }

    void ReportEveryMinute()
    {
        if (MonotonicNs() - LastReportNs_ < 60 * NsPerSecond) {
            return;
        }
        LastReportNs_ = MonotonicNs();
        Recorder_.Flush();
        Log("events {}, block {}{}, unknown {}, orders {}, snapshots {}/{}, reconnects {}",
            Seq_,
            Blocks_,
            Paused_ ? " (paused)" : "",
            Translator_.Unknown(),
            Translator_.OrderCount(),
            SnapshotsLoaded_.size(),
            Pairs_.size(),
            Reconnects_);
    }

    void WriteMeta()
    {
        if (Out_.empty()) {
            return;
        }
        ordered_json kinds;
        for (const auto& [type, count] : Translator_.Kinds()) {
            kinds[std::string(1, type)] = count;
        }
        ordered_json scales;
        for (const auto& [pair, scale] : Translator_.Scales()) {
            scales[pair] = {scale.first, scale.second};
        }
        Meta_["end_ns"] = RealtimeNs();
        Meta_["events"] = Seq_;
        Meta_["blocks"] = Blocks_;
        Meta_["unknown"] = Translator_.Unknown();
        Meta_["skipped"] = Translator_.Skipped();
        Meta_["kinds"] = kinds;
        Meta_["reconnects"] = Reconnects_;
        Meta_["scales"] = scales;
        Meta_["lag_ms_p50"] = LagMs_.empty() ? ordered_json(nullptr) : ordered_json(Median(LagMs_));
        std::ofstream(RecordingBase(Out_) + ".meta.json") << Meta_.dump(1) << '\n';
    }
};

} // namespace

int RunLive(const TArgs& args)
{
    TLiveFeed(args).Run();
    return 0;
}

} // namespace coldpath::feed
