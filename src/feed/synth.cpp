// S0 bridge: empty packets spaced by Δt; a receiver with --synth runs the bench event on each one.
#include "common/clock.h"
#include "common/itch.h"
#include "common/process.h"
#include "feed/feed.h"

#include <algorithm>
#include <nlohmann/json.hpp>
#include <random>

namespace coldpath::feed {

int RunSynth(const TArgs& args)
{
    const std::vector<std::string> pairs = {"s0"};
    const std::string dtText = args.Text("dt-ms", "1,10,100");
    const TSender sender(pairs, {});
    TRecorder recorder(args.Text("out", ""),
        nlohmann::ordered_json{{"source", "synth"},
            {"pairs", pairs},
            {"solo", nlohmann::ordered_json::array()},
            {"dt_ms", dtText},
            {"start_ns", RealtimeNs()}});
    SetTimerSlack(1000);

    std::vector<double> dts;
    for (const std::string_view item : SplitList(dtText)) {
        dts.push_back(ParseNumber<double>(item));
    }
    std::mt19937_64 random(static_cast<uint64_t>(args.Int("seed", 1)));
    const double duration = args.Double("duration", 0);
    const uint64_t endNs = duration > 0 ? MonotonicNs() + static_cast<uint64_t>(duration * 1e9) : UINT64_MAX;
    bool paused = args.Flag("start-paused");
    uint64_t seq = 0;
    uint64_t blocks = 0;
    if (!paused) {
        SendBlockStart(sender, recorder, pairs, nullptr);
        blocks = 1;
    }
    std::vector<double> plan;
    while (!Signals.Stop.load() && MonotonicNs() < endNs) {
        if (Signals.EndBlock.exchange(false) && !paused) {
            SendBlockEnd(sender, recorder);
            paused = true;
            Log("end of block {}, packets {}", blocks, seq);
        }
        if (Signals.StartBlock.exchange(false) && paused) {
            SendBlockStart(sender, recorder, pairs, nullptr);
            paused = false;
            ++blocks;
            Log("start of block {}", blocks);
        }
        if (paused) {
            SleepUntil(MonotonicNs() + 50'000'000ULL);
            continue;
        }
        if (plan.empty()) {
            for (const double dt : dts) {
                plan.insert(plan.end(), 50, dt);
            }
            std::ranges::shuffle(plan, random);
        }
        SleepUntil(MonotonicNs() + static_cast<uint64_t>(plan.back() * 1e6));
        plan.pop_back();
        ++seq;
        const auto packet = itch::MoldPacket(seq, {});
        const uint64_t sentAt = RealtimeNs();
        sender.Send(packet, 1);
        recorder.Write({.SentNs = sentAt, .Seq = seq, .Locate = 1}, packet);
    }
    if (!paused) {
        SendBlockEnd(sender, recorder);
    }
    recorder.Close();
    Log("S0 done: packets {}, blocks {}", seq, blocks);
    return 0;
}

} // namespace coldpath::feed
