#include "common/clock.h"
#include "common/itch.h"
#include "feed/feed.h"

#include <algorithm>
#include <csignal>
#include <ctime>
#include <span>

namespace coldpath::feed {
namespace {

void OnSignal(int signal)
{
    if (signal == SIGUSR1) {
        Signals.EndBlock.store(true);
    } else if (signal == SIGUSR2) {
        Signals.StartBlock.store(true);
    } else {
        Signals.Stop.store(true);
    }
}

constexpr size_t SnapshotMessagesPerPacket = 40;

} // namespace

TSignalFlags Signals;

void InstallSignals()
{
    // SA_RESTART keeps TLS reads and writes going on block signals; poll() is interrupted regardless.
    struct sigaction action{};
    action.sa_handler = OnSignal;
    action.sa_flags = SA_RESTART;
    for (const int signal : {SIGTERM, SIGINT, SIGUSR1, SIGUSR2}) {
        sigaction(signal, &action, nullptr);
    }
}

std::string LocalClock(uint64_t realtimeNs)
{
    const auto seconds = static_cast<time_t>((realtimeNs != 0 ? realtimeNs : RealtimeNs()) / NsPerSecond);
    tm local{};
    localtime_r(&seconds, &local);
    return std::format("{:02}:{:02}:{:02}", local.tm_hour, local.tm_min, local.tm_sec);
}

const std::vector<std::string>& DefaultPairs()
{
    static const std::vector<std::string> pairs = {"btcusd",
        "ethusd",
        "xrpusd",
        "solusd",
        "dogeusd",
        "uniusd",
        "btceur",
        "etheur",
        "xrpeur",
        "linkusd",
        "soleur",
        "ltcusd",
        "aaveusd",
        "avaxusd",
        "bchusd",
        "adausd",
        "btcusdc",
        "xlmusd",
        "dotusd",
        "btcusdt",
        "eurusd",
        "pepeusd",
        "ethgbp",
        "ethbtc",
        "btcgbp",
        "ltcbtc",
        "usdcusd",
        "shibusd",
        "gbpusd",
        "usdtusd",
        "xrpbtc"};
    return pairs;
}

void SendBlockStart(const TSender& sender,
    TRecorder& recorder,
    const std::vector<std::string>& pairs,
    const TTranslator* translator)
{
    const uint64_t ns = RealtimeNs();
    for (size_t i = 0; i < pairs.size(); ++i) {
        const auto locate = static_cast<uint16_t>(i + 1);
        std::vector<itch::TMessage> messages = {itch::StockDirectory(ns, locate, pairs[i])};
        if (translator != nullptr) {
            const auto book = translator->Snapshot(locate, ns);
            messages.insert(messages.end(), book.begin(), book.end());
        }
        for (size_t first = 0; first < messages.size(); first += SnapshotMessagesPerPacket) {
            const size_t count = std::min(SnapshotMessagesPerPacket, messages.size() - first);
            const auto packet = itch::MoldPacket(itch::SnapshotSequence, std::span(messages).subspan(first, count));
            sender.Send(packet, locate);
            recorder.Write({.SentNs = RealtimeNs(), .Seq = itch::SnapshotSequence, .Locate = locate}, packet);
        }
    }
}

void SendBlockEnd(const TSender& sender, TRecorder& recorder)
{
    const auto packet = itch::MoldPacket(itch::EndOfBlock, {});
    // UDP may drop it, so send twice; receivers stop on the first one.
    for (int i = 0; i < 3; ++i) {
        sender.Send(packet, 0);
    }
    recorder.Write({.SentNs = RealtimeNs(), .Seq = itch::EndOfBlock, .Locate = 0}, packet);
}

} // namespace coldpath::feed
