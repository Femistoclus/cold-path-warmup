#include "bench/counters.h"
#include "bench/disrupt.h"
#include "bench/prefetch.h"
#include "common/args.h"
#include "common/clock.h"
#include "common/file.h"
#include "common/print.h"
#include "common/process.h"
#include "common/tsc.h"
#include "handler/handler.h"

#include <algorithm>
#include <array>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <fcntl.h>
#include <iterator>
#include <memory>
#include <optional>
#include <string>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <vector>

namespace coldpath::bench {
namespace {

constexpr int CallsAfterPause = 8;
constexpr size_t EvictBytes = 64U << 20;

enum class ECondition : uint8_t {
    Hot,
    Cold,
    Sys,
    Spin,
    SpinShadow,
    None,
    Empty,
    Shadow,
    Prewake,
    PfWake,
    PfEvent,
    SpinPf,
    Dis,
};

// Pause modes are described in README.md.
constexpr std::array<std::string_view, 13> ConditionNames = {"hot",
    "cold",
    "sys",
    "spin",
    "spinshadow",
    "none",
    "empty",
    "shadow",
    "prewake",
    "pfwake",
    "pfevent",
    "spinpf",
    "dis"};
constexpr size_t NoCell = SIZE_MAX;

std::string_view Name(ECondition condition)
{
    return ConditionNames[static_cast<size_t>(condition)];
}

bool HasPause(ECondition condition)
{
    return condition >= ECondition::Spin && condition != ECondition::Dis;
}

std::vector<ECondition> ParseConditions(std::string_view list)
{
    std::vector<ECondition> conditions;
    for (const std::string_view item : SplitList(list)) {
        const auto it = std::ranges::find(ConditionNames, item);
        if (it == ConditionNames.end()) {
            throw std::invalid_argument("unknown mode " + std::string(item));
        }
        conditions.push_back(static_cast<ECondition>(it - ConditionNames.begin()));
    }
    return conditions;
}

struct TOptions
{
    int Cpu = -1;
    std::string Out = "calls.csv";
    int Trials = 1000;
    int HotCalls = 64;
    std::vector<int> DtMs = {1, 10, 100};
    std::vector<ECondition> Conditions;
    uint64_t PeriodUs = 1000;
    uint64_t PrewakeUs = 200;
    std::optional<uint64_t> LeadUs;
    bool ShadowSameEvent = true;
    bool SpinPause = true;
    bool WarmSyscall = false;
    bool Counters = true;
    char CounterGroup = 'a';
    unsigned Seed = 1;
    std::string PrefetchSet = "dc";
    int PrefetchReps = 1;
    bool RandomPhase = false;
    int WarmReps = 1;
    bool CountKernel = false;
    std::string Disrupt;
    uint64_t SettleNs = 1000;
    std::vector<ECondition> AfterPause;
    std::string NeighbourCounter;
    std::string Ready;

    uint64_t LeadOrPrewakeUs() const
    {
        return LeadUs && *LeadUs < PrewakeUs ? *LeadUs : PrewakeUs;
    }
};

TOptions ParseOptions(int argc, char** argv)
{
    const TArgs args(argc,
        argv,
        {.Values = {"cpu",
             "out",
             "trials",
             "dt-ms",
             "conds",
             "period-us",
             "shadow-event",
             "seed",
             "hot-calls",
             "prewake-us",
             "group",
             "warm-lead-us",
             "pf",
             "pf-reps",
             "event-phase",
             "warm-reps",
             "disrupt",
             "settle-ns",
             "after-pause",
             "neighbour-counter",
             "ready"},
            .Flags = {"no-counters", "spin-busy", "warm-syscall", "count-kernel"}});
    TOptions o;
    o.Cpu = static_cast<int>(args.Int("cpu", -1));
    o.Out = args.Text("out", o.Out);
    o.Trials = static_cast<int>(args.Int("trials", o.Trials));
    o.HotCalls = std::max(1, static_cast<int>(args.Int("hot-calls", o.HotCalls)));
    if (const auto dt = args.Get("dt-ms")) {
        o.DtMs.clear();
        for (const std::string_view item : SplitList(*dt)) {
            o.DtMs.push_back(ParseNumber<int>(item));
        }
    }
    // A named string: GCC 13 does not extend temporaries in a range-for initializer (C++23 P2718).
    const std::string conds =
        args.Text("conds", "hot,cold,sys,spin,spinshadow,none,empty,shadow,prewake,pfwake,pfevent,spinpf");
    o.Conditions = ParseConditions(conds);
    o.PeriodUs = static_cast<uint64_t>(args.Int("period-us", 1000));
    o.PrewakeUs = static_cast<uint64_t>(args.Int("prewake-us", 200));
    if (const auto lead = args.Get("warm-lead-us")) {
        o.LeadUs = ParseNumber<uint64_t>(*lead);
    }
    o.ShadowSameEvent = args.Text("shadow-event", "same") != "other";
    o.SpinPause = !args.Flag("spin-busy");
    o.WarmSyscall = args.Flag("warm-syscall");
    o.Counters = !args.Flag("no-counters");
    const std::string group = args.Text("group", "a");
    o.CounterGroup = group.size() == 1 && std::string_view("abcdet").contains(group[0]) ? group[0] : 'a';
    o.Seed = static_cast<unsigned>(args.Int("seed", 1));
    o.PrefetchSet = args.Text("pf", o.PrefetchSet);
    o.PrefetchReps = std::max(1, static_cast<int>(args.Int("pf-reps", 1)));
    o.RandomPhase = args.Text("event-phase", "grid") == "random";
    o.WarmReps = std::max(1, static_cast<int>(args.Int("warm-reps", 1)));
    o.CountKernel = args.Flag("count-kernel");
    o.Disrupt = args.Text("disrupt", "");
    o.SettleNs = static_cast<uint64_t>(args.Int("settle-ns", 1000));
    const std::string after = args.Text("after-pause", "");
    o.AfterPause = ParseConditions(after);
    if (o.Disrupt.empty() && (!o.AfterPause.empty() || std::ranges::contains(o.Conditions, ECondition::Dis))) {
        throw std::invalid_argument("mode dis and --after-pause require --disrupt");
    }
    if (!std::ranges::all_of(o.AfterPause, HasPause)) {
        throw std::invalid_argument("--after-pause: only modes with a pause");
    }
    o.NeighbourCounter = args.Text("neighbour-counter", "");
    o.Ready = args.Text("ready", "");
    return o;
}

// Work counter of the background load (src/neighbour), mapped read-only. Read before the pause and after the
// last measured call, never inside a window.
class TNeighbourCounter
{
public:
    explicit TNeighbourCounter(const std::string& path)
    {
        const int fd = open(path.c_str(), O_RDONLY);
        void* map = fd >= 0 ? mmap(nullptr, sizeof(uint64_t), PROT_READ, MAP_SHARED, fd, 0) : MAP_FAILED;
        if (fd >= 0) {
            close(fd);
        }
        if (map == MAP_FAILED) {
            throw std::runtime_error("cannot open neighbour counter " + path);
        }
        Value_ = static_cast<const volatile uint64_t*>(map);
    }

    ~TNeighbourCounter()
    {
        munmap(const_cast<uint64_t*>(Value_), sizeof(uint64_t));
    }

    TNeighbourCounter(const TNeighbourCounter&) = delete;
    TNeighbourCounter& operator=(const TNeighbourCounter&) = delete;

    uint64_t Get() const
    {
        return *Value_;
    }

private:
    const volatile uint64_t* Value_ = nullptr;
};

struct TTrial
{
    ECondition Condition = ECondition::Hot;
    int DtMs = 0;
    uint64_t PhaseNs = 0;
    size_t Cell = NoCell;
};

// Disruptor cells: one cell per spec in dis (Δt = 0) and per spec and Δt after each --after-pause mode.
std::vector<TTrial> PlanTrials(const TOptions& options, const std::vector<size_t>& cells)
{
    std::vector<TTrial> trials;
    for (int k = 0; k < static_cast<int>(ConditionNames.size()); ++k) {
        const auto condition = static_cast<ECondition>(k);
        if (std::ranges::find(options.Conditions, condition) == options.Conditions.end()) {
            continue;
        }
        if (condition == ECondition::Dis) {
            for (const size_t cell : cells) {
                for (int i = 0; i < options.Trials; ++i) {
                    trials.push_back({.Condition = condition, .Cell = cell});
                }
            }
            continue;
        }
        const std::vector<int> dts = HasPause(condition) ? options.DtMs : std::vector<int>{0};
        for (const int dt : dts) {
            for (int i = 0; i < options.Trials; ++i) {
                trials.push_back({.Condition = condition, .DtMs = dt});
            }
        }
    }
    for (const ECondition condition : options.AfterPause) {
        for (const int dt : options.DtMs) {
            for (const size_t cell : cells) {
                for (int i = 0; i < options.Trials; ++i) {
                    trials.push_back({.Condition = condition, .DtMs = dt, .Cell = cell});
                }
            }
        }
    }
    // Fisher–Yates on glibc rand: the trial order depends only on the seed.
    std::srand(options.Seed);
    for (size_t i = trials.size(); i-- > 1;) {
        std::swap(trials[i], trials[static_cast<size_t>(std::rand()) % (i + 1)]);
    }
    // Random phase: the event no longer lands exactly one period after the last warm-up, so it can collide with one.
    if (options.RandomPhase) {
        for (TTrial& trial : trials) {
            trial.PhaseNs =
                HasPause(trial.Condition) ? static_cast<uint64_t>(std::rand()) % (options.PeriodUs * 1000) : 0;
        }
    }
    return trials;
}

volatile uint64_t Sink = 0;

void Consume(uint64_t value)
{
    Sink = Sink + value;
}

struct TPauseResult
{
    uint64_t WarmNs = 0;
    int Wakes = 0;
    uint64_t GapAt = 0;
    uint64_t Deadline = 0;
    uint64_t DisNs = 0;
};

class TPause
{
public:
    TPause(const TOptions& options, const TPrefetcher& prefetcher, TDisruptor* disruptor, double tscPerNs)
        : Options_(options)
        , Prefetcher_(prefetcher)
        , Disruptor_(disruptor)
        , TscPerNs_(tscPerNs)
        , WarmEvent_(options.ShadowSameEvent ? handler::EEvent::Real : handler::EEvent::Other)
    {
        EvictBuffer_.assign(EvictBytes, 1);
    }

    // Inlined into the trial loop so the stack prefetch in pfwake and spinpf covers the frame Handle uses.
    [[gnu::always_inline]] TPauseResult Run(const TTrial& trial)
    {
        const uint64_t periodNs = Options_.PeriodUs * 1000;
        const uint64_t start = MonotonicNs();
        const uint64_t deadline = start + static_cast<uint64_t>(trial.DtMs) * 1'000'000ULL + trial.PhaseNs;
        Start_ = start;
        Result_ = {.Deadline = deadline};
        switch (trial.Condition) {
            case ECondition::Hot:
                break;
            case ECondition::Cold:
                EvictCaches();
                break;
            case ECondition::Sys:
                syscall(SYS_getppid);
                break;
            case ECondition::Spin:
                SpinUntil(deadline);
                break;
            case ECondition::SpinShadow:
                for (uint64_t next = start + periodNs; next < deadline; next += periodNs) {
                    SpinUntil(next);
                    ++Result_.Wakes;
                    const uint64_t w = MonotonicNs();
                    if (Options_.WarmSyscall) {
                        syscall(SYS_getppid);
                    } else {
                        Consume(handler::Handle(WarmEvent_, true));
                    }
                    Result_.WarmNs += MonotonicNs() - w;
                }
                SpinUntil(deadline);
                break;
            case ECondition::None:
            case ECondition::PfEvent:
                SleepUntil(deadline);
                break;
            case ECondition::Empty:
            case ECondition::Shadow:
                for (uint64_t next = start + periodNs; next < deadline; next += periodNs) {
                    SleepUntil(next);
                    ++Result_.Wakes;
                    if (trial.Condition == ECondition::Shadow) {
                        const uint64_t w = MonotonicNs();
                        Consume(handler::Handle(WarmEvent_, true));
                        Result_.WarmNs += MonotonicNs() - w;
                    }
                }
                SleepUntil(deadline);
                break;
            case ECondition::Prewake:
                SleepToLead();
                {
                    const uint64_t w = MonotonicNs();
                    for (int k = 0; k < Options_.WarmReps; ++k) {
                        Consume(handler::Handle(WarmEvent_, true));
                    }
                    Result_.WarmNs = MonotonicNs() - w;
                }
                Result_.Wakes = 1;
                SpinUntil(deadline);
                break;
            case ECondition::PfWake:
                SleepToLead();
                {
                    const uint64_t w = __rdtsc();
                    Prefetcher_.Run();
                    Result_.WarmNs = CyclesToNs(__rdtsc() - w);
                }
                Result_.Wakes = 1;
                SpinUntil(deadline);
                break;
            case ECondition::SpinPf: {
                uint64_t cycles = 0;
                for (uint64_t next = start + periodNs; next < deadline; next += periodNs) {
                    SpinUntil(next);
                    ++Result_.Wakes;
                    const uint64_t w = __rdtsc();
                    Prefetcher_.Run();
                    cycles += __rdtsc() - w;
                }
                Result_.WarmNs = CyclesToNs(cycles);
                SpinUntil(deadline);
                break;
            }
            case ECondition::Dis:
                break;
        }
        if (trial.Cell != NoCell) {
            Result_.DisNs = CyclesToNs(Disruptor_->Run(trial.Cell));
        }
        return Result_;
    }

private:
    const TOptions& Options_;
    const TPrefetcher& Prefetcher_;
    TDisruptor* Disruptor_ = nullptr;
    double TscPerNs_ = 1;
    handler::EEvent WarmEvent_;
    std::vector<uint8_t> EvictBuffer_;
    TPauseResult Result_;
    uint64_t Start_ = 0;

    uint64_t CyclesToNs(uint64_t cycles) const
    {
        return static_cast<uint64_t>(static_cast<double>(cycles) / TscPerNs_);
    }

    void EvictCaches()
    {
        for (size_t i = 0; i < EvictBuffer_.size(); i += 64) {
            ++EvictBuffer_[i];
        }
    }

    void SpinUntil(uint64_t t)
    {
        uint64_t prev = MonotonicNs();
        for (uint64_t now = MonotonicNs(); now < t; now = MonotonicNs()) {
            if (now - prev > 1000) {
                Result_.GapAt = now;
            }
            prev = now;
            if (Options_.SpinPause) {
                _mm_pause();
            }
        }
    }

    void SleepToLead()
    {
        const uint64_t deadline = Result_.Deadline;
        const uint64_t wakeAt = deadline - Options_.PrewakeUs * 1000;
        if (wakeAt > Start_) {
            SleepUntil(wakeAt);
        }
        if (Options_.LeadUs && *Options_.LeadUs < Options_.PrewakeUs) {
            SpinUntil(deadline - *Options_.LeadUs * 1000);
        }
    }
};

struct TCall
{
    double Ns = 0;
    TCounters::TValues Counters{};
};

std::string CounterDescription(const TOptions& options, bool enabled)
{
    return enabled ? std::format("group {}, rdpmc", options.CounterGroup) : "none";
}

int Run(int argc, char** argv)
{
    const TOptions options = ParseOptions(argc, argv);
    PinToCpu(options.Cpu);
    MakeRealtime();
    SetTimerSlack();
    LockMemory();
    if (!handler::Init(options.Seed)) {
        return 1;
    }
    const double tscPerNs = CalibrateTscPerNs();
    const TPrefetcher prefetcher(options.PrefetchSet, options.PrefetchReps);
    Println("{}", prefetcher.Describe());
    const std::optional<TCounters> counters =
        options.Counters ? TCounters::Open(options.CounterGroup, options.CountKernel) : std::nullopt;
    std::optional<TDisruptor> disruptor;
    if (!options.Disrupt.empty()) {
        disruptor.emplace(TDisruptor::TConfig{.Spec = options.Disrupt,
            .SettleNs = options.SettleNs,
            .TscPerNs = tscPerNs,
            .Cpu = options.Cpu,
            .DataLines = prefetcher.DataLines(),
            .CodeLines = prefetcher.CodeLines()});
        Println("{}", disruptor->Describe());
    }
    std::optional<TNeighbourCounter> neighbour;
    if (!options.NeighbourCounter.empty()) {
        neighbour.emplace(options.NeighbourCounter);
    }
    TPause pause(options, prefetcher, disruptor ? &*disruptor : nullptr, tscPerNs);
    const std::vector<TTrial> trials = PlanTrials(options, disruptor ? disruptor->Available() : std::vector<size_t>{});

    const TFilePtr out = OpenFile(options.Out, "w");
    if (!out) {
        std::perror(options.Out.c_str());
        return 1;
    }
    static std::array<char, 1 << 20> outBuffer;
    std::setvbuf(out.get(), outBuffer.data(), _IOFBF, outBuffer.size());
    std::string line = "trial,cond,dt_ms,call,ns,late_us,wakes,warm_us,pre_ns,gap_us";
    if (counters) {
        for (const std::string_view name : counters->Names()) {
            line += std::format(",{}", name);
        }
    }
    if (disruptor) {
        line += ",dis,dis_ns";
    }
    if (neighbour) {
        line += ",nb_work";
    }
    line += '\n';
    std::fputs(line.c_str(), out.get());

    Println("core {}, TSC {:.3f} GHz, counters: {}",
        options.Cpu,
        tscPerNs,
        CounterDescription(options, counters.has_value()));
    Println("handler: {}", handler::Info());
    Println("warming: shadow every {} us, prewake: wake {} us ahead, warm {} us ahead, warm-up event {}; "
            "hot calls before the pause: {}; {}wait loop: {}",
        options.PeriodUs,
        options.PrewakeUs,
        options.LeadOrPrewakeUs(),
        options.ShadowSameEvent ? "same" : "other",
        options.HotCalls,
        options.WarmSyscall ? "spinshadow calls getppid instead of warming; " : "",
        options.SpinPause ? "with pause" : "without pause");
    Println("event phase: {}; prewake passes: {}; counters include the kernel: {}",
        options.RandomPhase ? "random within the period" : "on the period grid",
        options.WarmReps,
        options.CountKernel ? "yes" : "no");
    Println("trials: {} ({} per cell), writing to {}", trials.size(), options.Trials, options.Out);
    if (neighbour) {
        Println("neighbour: work counter {} (now {})", options.NeighbourCounter, neighbour->Get());
    }
    // The neighbour waits for this file, so it does not share the core while the order book loads.
    if (!options.Ready.empty() && !OpenFile(options.Ready, "w")) {
        std::perror(options.Ready.c_str());
    }

    const uint64_t startedAt = MonotonicNs();
    const size_t progressStep = std::max<size_t>(1, trials.size() / 10);
    std::array<TCall, CallsAfterPause> calls{};
    for (size_t i = 0; i < trials.size(); ++i) {
        const TTrial& trial = trials[i];

        for (int k = 0; k < options.HotCalls - 1; ++k) {
            Consume(handler::Handle(handler::EEvent::Real, false));
        }
        const uint64_t h0 = TscBegin();
        Consume(handler::Handle(handler::EEvent::Real, false));
        const double preNs = static_cast<double>(TscEnd() - h0) / tscPerNs;

        const uint64_t nbBefore = neighbour ? neighbour->Get() : 0;
        const TPauseResult paused = pause.Run(trial);
        const uint64_t woke = MonotonicNs();
        const double lateUs =
            HasPause(trial.Condition) ? static_cast<double>(static_cast<int64_t>(woke - paused.Deadline)) / 1e3 : 0.0;
        const double gapUs = paused.GapAt != 0 ? static_cast<double>(woke - paused.GapAt) / 1e3 : -1.0;

        for (size_t c = 0; c < calls.size(); ++c) {
            TCounters::TValues before;
            TCounters::TValues after;
            const bool prefetchNow = c == 0 && trial.Condition == ECondition::PfEvent;
            if (counters) {
                counters->Read(before);
            }
            const uint64_t a = TscBegin();
            if (prefetchNow) {
                prefetcher.Run();
            }
            Consume(handler::Handle(handler::EEvent::Real, false));
            const uint64_t b = TscEnd();
            if (counters) {
                counters->Read(after);
            }
            calls[c].Ns = static_cast<double>(b - a) / tscPerNs;
            if (counters) {
                for (size_t k = 0; k < counters->Names().size(); ++k) {
                    calls[c].Counters[k] = after[k] - before[k];
                }
            }
        }
        const uint64_t nbWork = neighbour ? neighbour->Get() - nbBefore : 0;

        for (size_t c = 0; c < calls.size(); ++c) {
            line.clear();
            std::format_to(std::back_inserter(line),
                "{},{},{},{},{:.1f},{:.1f},{},{:.1f},{:.1f},{:.1f}",
                i,
                Name(trial.Condition),
                trial.DtMs,
                c + 1,
                calls[c].Ns,
                lateUs,
                paused.Wakes,
                static_cast<double>(paused.WarmNs) / 1e3,
                preNs,
                gapUs);
            if (counters) {
                for (size_t k = 0; k < counters->Names().size(); ++k) {
                    std::format_to(std::back_inserter(line), ",{}", calls[c].Counters[k]);
                }
            }
            if (disruptor) {
                std::format_to(std::back_inserter(line),
                    ",{},{}",
                    trial.Cell != NoCell ? disruptor->Label(trial.Cell) : std::string_view("-"),
                    paused.DisNs);
            }
            if (neighbour) {
                std::format_to(std::back_inserter(line), ",{}", nbWork);
            }
            line += '\n';
            std::fputs(line.c_str(), out.get());
        }

        if ((i + 1) % progressStep == 0) {
            Println("  {:3}%  ({:.0f} s)",
                (i + 1) * 100 / trials.size(),
                static_cast<double>(MonotonicNs() - startedAt) / 1e9);
        }
    }
    Println("done in {:.0f} s", static_cast<double>(MonotonicNs() - startedAt) / 1e9);
    return 0;
}

} // namespace
} // namespace coldpath::bench

int main(int argc, char** argv)
{
    try {
        return coldpath::bench::Run(argc, argv);
    } catch (const std::exception& e) {
        std::fprintf(stderr, "bench: %s\n", e.what());
    } catch (...) {
        std::fprintf(stderr, "bench: unknown error\n");
    }
    return 2;
}
