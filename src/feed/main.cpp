#include "feed/feed.h"

#include <cstdio>
#include <string_view>

namespace {

constexpr std::string_view Usage =
    "feed live   [--out FILE.pkt] [--pairs a,b] [--solo a] [--duration S] [--start-paused]\n"
    "feed synth  [--out FILE.pkt] [--dt-ms 1,10,100] [--seed N] [--duration S] [--start-paused]\n"
    "feed replay FILE.pkt [--block K] [--solo a] [--speed X] [--limit-s S]\n"
    "feed blocks FILE.pkt\n"
    "feed dump   FILE.pkt";

int Run(int argc, char** argv)
{
    using namespace coldpath;
    using namespace coldpath::feed;
    const std::string_view mode = argc > 1 ? argv[1] : "";
    // TArgs skips the first element, which here is the mode name.
    const int restArgc = argc - 1;
    char** restArgv = argv + 1;
    InstallSignals();
    if (mode == "live") {
        return RunLive(
            TArgs(restArgc, restArgv, {.Values = {"out", "pairs", "solo", "duration"}, .Flags = {"start-paused"}}));
    }
    if (mode == "synth") {
        return RunSynth(
            TArgs(restArgc, restArgv, {.Values = {"out", "dt-ms", "seed", "duration"}, .Flags = {"start-paused"}}));
    }
    if (mode == "replay") {
        return RunReplay(TArgs(restArgc, restArgv, {.Values = {"block", "solo", "speed", "limit-s"}, .Flags = {}}));
    }
    if (mode == "blocks") {
        return RunBlocks(TArgs(restArgc, restArgv, {}));
    }
    if (mode == "dump") {
        return RunDump(TArgs(restArgc, restArgv, {}));
    }
    Eprintln("{}", Usage);
    return 2;
}

} // namespace

int main(int argc, char** argv)
{
    try {
        return Run(argc, argv);
    } catch (const std::exception& e) {
        std::fprintf(stderr, "feed: %s\n", e.what());
    } catch (...) {
        std::fprintf(stderr, "feed: unknown error\n");
    }
    return 2;
}
