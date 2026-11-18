#pragma once

#include "common/args.h"
#include "common/print.h"
#include "feed/recording.h"
#include "feed/sender.h"
#include "feed/translator.h"

#include <atomic>
#include <format>
#include <string>
#include <utility>
#include <vector>

// SIGUSR1 ends a block, SIGUSR2 starts one (directory and book snapshot with seq 0), SIGTERM ends it and exits.
namespace coldpath::feed {

struct TSignalFlags
{
    std::atomic<bool> Stop{false};
    std::atomic<bool> EndBlock{false};
    std::atomic<bool> StartBlock{false};
};

extern TSignalFlags Signals;

void InstallSignals();
std::string LocalClock(uint64_t realtimeNs);
const std::vector<std::string>& DefaultPairs();

template <class... TValues>
void Log(std::format_string<TValues...> format, TValues&&... values)
{
    Println("[feed] {} {}", LocalClock(0), std::format(format, std::forward<TValues>(values)...));
}

void SendBlockStart(const TSender& sender,
    TRecorder& recorder,
    const std::vector<std::string>& pairs,
    const TTranslator* translator);
void SendBlockEnd(const TSender& sender, TRecorder& recorder);

int RunLive(const TArgs& args);
int RunSynth(const TArgs& args);
int RunReplay(const TArgs& args);
int RunBlocks(const TArgs& args);
int RunDump(const TArgs& args);

} // namespace coldpath::feed
