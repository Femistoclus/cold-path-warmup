#include "common/clock.h"
#include "common/itch.h"
#include "common/process.h"
#include "feed/feed.h"

#include <algorithm>
#include <map>

namespace coldpath::feed {
namespace {

std::string RecordPath(const TArgs& args)
{
    if (args.Positional().size() != 1) {
        throw std::invalid_argument("expected one path to a .pkt recording");
    }
    return std::string(args.Positional()[0]);
}

std::vector<std::string> Strings(const nlohmann::json& list)
{
    return list.is_array() ? list.get<std::vector<std::string>>() : std::vector<std::string>{};
}

template <class T>
T Percentile(std::vector<T>& sorted, double q)
{
    return sorted[std::min(sorted.size() - 1, static_cast<size_t>(q * static_cast<double>(sorted.size())))];
}

} // namespace

int RunReplay(const TArgs& args)
{
    const TRecording recording(RecordPath(args));
    size_t begin = recording.DataBegin();
    size_t end = recording.DataEnd();
    if (const auto block = args.Get("block")) {
        const std::vector<TBlock> blocks = SplitBlocks(recording);
        const auto k = ParseNumber<size_t>(*block);
        if (k < 1 || k > blocks.size()) {
            throw std::invalid_argument(std::format("recording has {} blocks, no block {}", blocks.size(), k));
        }
        begin = blocks[k - 1].Begin;
        end = blocks[k - 1].End;
    }
    const double limitS = args.Double("limit-s", 0);
    const double speed = args.Double("speed", 1.0);
    std::vector<std::string> solo = Strings(recording.Meta().value("solo", nlohmann::json::array()));
    if (const auto given = args.Get("solo")) {
        const std::vector<std::string_view> items = SplitList(*given);
        solo.assign(items.begin(), items.end());
    }
    const TSender sender(Strings(recording.Meta().at("pairs")), solo);
    SetTimerSlack(1000);

    std::optional<uint64_t> firstReal;
    std::optional<uint64_t> t0;
    const uint64_t s0 = MonotonicNs();
    std::vector<int64_t> lateNs;
    bool endSent = false;
    for (size_t offset = begin; offset < end && !Signals.Stop.load();) {
        const auto packet = recording.Next(offset);
        if (!packet) {
            break;
        }
        const bool real = packet->Seq != itch::SnapshotSequence && packet->Seq != itch::EndOfBlock;
        if (real && !firstReal) {
            firstReal = packet->TimeNs;
        }
        if (limitS > 0 && real && static_cast<double>(packet->TimeNs - *firstReal) > limitS * 1e9) {
            continue;
        }
        t0 = t0.value_or(packet->TimeNs);
        if (real) {
            const uint64_t target = s0 + static_cast<uint64_t>(static_cast<double>(packet->TimeNs - *t0) / speed);
            // Sleep, not spin: spinning would keep the connector core in C0 and change how sleeping receivers behave.
            if (target > MonotonicNs()) {
                SleepUntil(target);
            }
            lateNs.push_back(static_cast<int64_t>(MonotonicNs() - target));
        }
        const uint64_t ns = RealtimeNs();
        std::vector<itch::TMessage> messages = itch::ParseMessages(packet->Body);
        for (itch::TMessage& message : messages) {
            message.SetTimestamp(ns);
        }
        const auto out = itch::MoldPacket(packet->Seq, messages);
        if (packet->Seq == itch::EndOfBlock) {
            for (int i = 0; i < 3; ++i) {
                sender.Send(out, 0);
            }
            endSent = true;
        } else {
            sender.Send(out, packet->Locate);
            endSent = false;
        }
    }
    if (!endSent) {
        const auto out = itch::MoldPacket(itch::EndOfBlock, {});
        for (int i = 0; i < 3; ++i) {
            sender.Send(out, 0);
        }
    }
    if (!lateNs.empty()) {
        std::ranges::sort(lateNs);
        Log("replayed events {}; send lateness, us: p50 {:.0f}, p99 {:.0f}, max {:.0f}",
            lateNs.size(),
            static_cast<double>(Percentile(lateNs, 0.5)) / 1e3,
            static_cast<double>(Percentile(lateNs, 0.99)) / 1e3,
            static_cast<double>(lateNs.back()) / 1e3);
    }
    return 0;
}

int RunBlocks(const TArgs& args)
{
    const TRecording recording(RecordPath(args));
    const std::vector<TBlock> blocks = SplitBlocks(recording);
    for (size_t i = 0; i < blocks.size(); ++i) {
        const TBlock& b = blocks[i];
        Println("block {}: events {}, {:.1f} min, start {}, end of block {}",
            i + 1,
            b.RealPackets,
            static_cast<double>(b.LastNs - b.FirstNs) / 60e9,
            LocalClock(b.FirstNs),
            b.HasEnd ? "yes" : "no");
    }
    return 0;
}

int RunDump(const TArgs& args)
{
    const std::string path = RecordPath(args);
    const TRecording recording(path);
    const std::vector<std::string> pairs = Strings(recording.Meta().at("pairs"));
    std::map<uint16_t, std::vector<uint64_t>> times;
    std::map<char, uint64_t> kinds;
    uint64_t real = 0;
    uint64_t first = 0;
    uint64_t last = 0;
    for (size_t offset = recording.DataBegin(); const auto packet = recording.Next(offset);) {
        if (packet->Seq == itch::SnapshotSequence || packet->Seq == itch::EndOfBlock) {
            continue;
        }
        first = real++ == 0 ? packet->TimeNs : first;
        last = packet->TimeNs;
        times[packet->Locate].push_back(packet->TimeNs);
        for (const itch::TMessage& message : itch::ParseMessages(packet->Body)) {
            ++kinds[message.Type()];
        }
    }
    const double duration = static_cast<double>(last - first) / 1e9;
    Println("{}: {} events in {:.1f} min, blocks {}, pairs {}, solo {}",
        path,
        real,
        duration / 60,
        SplitBlocks(recording).size(),
        pairs.size(),
        recording.Meta().value("solo", nlohmann::json::array()).dump());
    std::string kindText;
    for (const auto& [type, count] : kinds) {
        kindText += std::format(" {}={}", type, count);
    }
    Println("message types:{}", kindText);
    Println("  {:<10}{:>9}{:>8}{:>10}{:>10}{:>9}", "pair", "events", "per s", "p50, ms", "p90, ms", "max, s");
    std::vector<std::pair<uint16_t, std::vector<uint64_t>>> byCount(times.begin(), times.end());
    std::ranges::sort(byCount, [](const auto& a, const auto& b) {
        return a.second.size() > b.second.size();
    });
    for (const auto& [locate, ts] : byCount) {
        if (ts.size() < 3) {
            continue;
        }
        std::vector<uint64_t> gaps;
        for (size_t i = 1; i < ts.size(); ++i) {
            gaps.push_back(ts[i] - ts[i - 1]);
        }
        std::ranges::sort(gaps);
        const std::string& name = locate >= 1 && locate <= pairs.size() ? pairs[locate - 1U] : "?";
        Println("  {:<10}{:>9}{:>8.1f}{:>10.1f}{:>10.1f}{:>9.1f}",
            name,
            ts.size(),
            static_cast<double>(ts.size()) / std::max(duration, 1e-9),
            static_cast<double>(Percentile(gaps, 0.5)) / 1e6,
            static_cast<double>(Percentile(gaps, 0.9)) / 1e6,
            static_cast<double>(gaps.back()) / 1e9);
    }
    return 0;
}

} // namespace coldpath::feed
