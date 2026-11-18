#include "common/args.h"
#include "common/clock.h"
#include "common/itch.h"
#include "common/print.h"
#include "common/process.h"
#include "common/tsc.h"
#include "handler/handler.h"
#include "recv/journal.h"
#include "recv/socket.h"

#include <algorithm>
#include <array>
#include <atomic>
#include <cerrno>
#include <csignal>
#include <cstdio>
#include <cstring>
#include <poll.h>
#include <stdexcept>
#include <string>
#include <x86intrin.h>

namespace coldpath::recv {
namespace {

// spin polls with non-blocking recvmsg (a syscall), like an ordinary program without kernel bypass.
enum class EMode : uint8_t {
    Sleep,
    Spin,
    SpinShadow,
    SleepShadow,
    SpinPf,
};

constexpr std::array<std::string_view, 5> ModeNames = {"sleep", "spin", "spinshadow", "sleepshadow", "spinpf"};
constexpr size_t JournalCapacity = 4U << 20;
constexpr uint64_t IrqGapNs = 3000;
constexpr uint64_t HashEveryPackets = 1000;

std::atomic<bool> StopRequested{false};

void OnSignal(int /*signal*/)
{
    StopRequested.store(true, std::memory_order_relaxed);
}

bool Stopping()
{
    return StopRequested.load(std::memory_order_relaxed);
}

volatile uint64_t Sink = 0;

uint32_t Cap32(uint64_t value)
{
    return value > UINT32_MAX ? UINT32_MAX : static_cast<uint32_t>(value);
}

struct TOptions
{
    EMode Mode = EMode::Sleep;
    int Cpu = -1;
    std::string Watch;
    std::string Out;
    std::string Group = "239.192.0.1";
    uint16_t Port = 31001;
    double PeriodUs = 50;
    double DurationS = 0;
    std::string Ready;
    bool Check = false;
    bool Synth = false;

    std::string_view ModeName() const
    {
        return ModeNames[static_cast<size_t>(Mode)];
    }
};

TOptions ParseOptions(int argc, char** argv)
{
    const TArgs args(argc,
        argv,
        {.Values = {"mode", "cpu", "watch", "out", "group", "port", "period-us", "duration", "ready"},
            .Flags = {"check", "synth"}});
    TOptions o;
    const auto mode = std::ranges::find(ModeNames, args.Text("mode", ""));
    o.Cpu = static_cast<int>(args.Int("cpu", -1));
    o.Watch = args.Text("watch", "");
    o.Out = args.Text("out", "");
    o.Synth = args.Flag("synth");
    if (mode == ModeNames.end() || o.Cpu < 0 || (o.Watch.empty() && !o.Synth) || o.Out.empty()) {
        throw std::invalid_argument(
            "recv --mode sleep|spin|spinshadow|sleepshadow|spinpf --cpu N --watch PAIR --out FILE\n"
            "     [--group 239.192.0.1] [--port 31001] [--period-us 50] [--duration S] [--ready FILE]\n"
            "     [--synth] [--check]");
    }
    o.Mode = static_cast<EMode>(mode - ModeNames.begin());
    o.Group = args.Text("group", o.Group);
    o.Port = static_cast<uint16_t>(args.Int("port", o.Port));
    o.PeriodUs = args.Double("period-us", o.PeriodUs);
    o.DurationS = args.Double("duration", 0);
    o.Ready = args.Text("ready", "");
    o.Check = args.Flag("check");
    return o;
}

enum class EWait : uint8_t {
    Packet,
    Retry,
    Stop,
};

class TReceiver
{
public:
    explicit TReceiver(const TOptions& options)
        : Options_(options)
        , PeriodNs_(static_cast<uint64_t>(options.PeriodUs * 1000))
        , Socket_(options.Group, options.Port)
        , Journal_(options.Out, JournalCapacity)
    { }

    void Run()
    {
        EndNs_ = Options_.DurationS > 0 ? MonotonicNs() + static_cast<uint64_t>(Options_.DurationS * 1e9) : UINT64_MAX;
        LastActivity_ = MonotonicNs();
        switch (Options_.Mode) {
            case EMode::Sleep:
                Loop<EMode::Sleep>();
                break;
            case EMode::Spin:
                Loop<EMode::Spin>();
                break;
            case EMode::SpinShadow:
                Loop<EMode::SpinShadow>();
                break;
            case EMode::SleepShadow:
                Loop<EMode::SleepShadow>();
                break;
            case EMode::SpinPf:
                Loop<EMode::SpinPf>();
                break;
        }
        Journal_.Finish(LastSeq_, Options_.Synth ? std::nullopt : std::optional(handler::BookHash()));
    }

    void Report() const
    {
        Eprintln("[recv {} cpu{}] packets {} (+{} snapshot), gaps {}, warms {}, signals {}, book mismatches {}{}",
            Options_.ModeName(),
            Options_.Cpu,
            Journal_.Total(),
            Snapshots_,
            Gaps_,
            WarmsTotal_,
            handler::RealSignals(),
            CheckFailures_,
            Ended_ ? ", end of block" : "");
    }

private:
    const TOptions& Options_;
    uint64_t PeriodNs_;
    TMulticastSocket Socket_;
    TJournal Journal_;
    std::array<const void*, 4096> PrefetchLines_{};
    uint64_t EndNs_ = UINT64_MAX;
    uint64_t LastActivity_ = 0;
    uint64_t LastWarm_ = 0;
    uint64_t LastIrq_ = 0;
    uint64_t LastSeq_ = 0;
    uint64_t RealPackets_ = 0;
    uint16_t Warms_ = 0;
    uint64_t WarmsTotal_ = 0;
    uint64_t Snapshots_ = 0;
    uint64_t Gaps_ = 0;
    uint64_t CheckFailures_ = 0;
    uint8_t NextFlags_ = 0;
    bool Ended_ = false;

    template <EMode Mode>
    void Loop()
    {
        while (!Stopping() && !Ended_) {
            ssize_t n = -1;
            const EWait wait = Wait<Mode>(n);
            if (wait == EWait::Stop) {
                break;
            }
            if (wait == EWait::Packet) {
                OnPacket(static_cast<size_t>(n));
            }
        }
    }

    // Save errno right after recvmsg: the clock read and warm-up before the check may overwrite it.
    template <EMode Mode>
    EWait Wait(ssize_t& n)
    {
        int error = 0;
        if constexpr (Mode == EMode::Sleep) {
            n = Socket_.Receive(0);
            error = errno;
        } else if constexpr (Mode == EMode::SleepShadow) {
            const uint64_t now = MonotonicNs();
            const uint64_t due = LastActivity_ + PeriodNs_;
            if (now >= EndNs_) {
                return EWait::Stop;
            }
            if (now >= due) {
                if (WarmChecked<Mode>()) {
                    ++Warms_;
                    ++WarmsTotal_;
                    LastWarm_ = MonotonicNs();
                }
                LastActivity_ = MonotonicNs();
                return EWait::Retry;
            }
            pollfd ready{.fd = Socket_.Fd(), .events = POLLIN, .revents = 0};
            const timespec timeout = ToTimespec(due - now);
            if (ppoll(&ready, 1, &timeout, nullptr) <= 0) {
                return EWait::Retry;
            }
            n = Socket_.Receive(MSG_DONTWAIT);
            error = errno;
        } else {
            uint64_t prev = MonotonicNs();
            for (;;) {
                n = Socket_.Receive(MSG_DONTWAIT);
                error = errno;
                if (n >= 0 || error != EAGAIN || Stopping()) {
                    break;
                }
                uint64_t now = MonotonicNs();
                if (now - prev > IrqGapNs) {
                    LastIrq_ = now;
                }
                if constexpr (Mode != EMode::Spin) {
                    if (now - LastActivity_ >= PeriodNs_) {
                        if (WarmChecked<Mode>()) {
                            ++Warms_;
                            ++WarmsTotal_;
                        }
                        LastWarm_ = LastActivity_ = now = MonotonicNs();
                    }
                }
                if (now >= EndNs_) {
                    break;
                }
                prev = now;
                _mm_pause();
            }
            if (n < 0 && error == EAGAIN) {
                return EWait::Stop;
            }
        }
        if (n < 0) {
            if (error == EINTR || error == EAGAIN) {
                return Mode == EMode::Sleep && MonotonicNs() >= EndNs_ ? EWait::Stop : EWait::Retry;
            }
            Eprintln("recvmsg: {}", std::strerror(error));
            return EWait::Stop;
        }
        return EWait::Packet;
    }

    template <EMode Mode>
    bool Warm()
    {
        if constexpr (Mode == EMode::SpinPf) {
            size_t count = 0;
            if (Options_.Synth) {
                const handler::TPrefetchData data = handler::PrefetchData();
                count = std::min(data.Lines.size(), PrefetchLines_.size());
                std::copy_n(data.Lines.begin(), count, PrefetchLines_.begin());
            } else {
                count = handler::LivePrefetch(PrefetchLines_);
            }
            for (size_t i = 0; i < count; ++i) {
                _mm_prefetch(static_cast<const char*>(PrefetchLines_[i]), _MM_HINT_T0);
            }
            return count > 0;
        } else {
            if (Options_.Synth) {
                Sink = Sink + handler::Handle(handler::EEvent::Real, true);
                return true;
            }
            return handler::Shadow();
        }
    }

    template <EMode Mode>
    bool WarmChecked()
    {
        if (!Options_.Check || Options_.Synth) {
            return Warm<Mode>();
        }
        const auto before = handler::Summary();
        const bool warmed = Warm<Mode>();
        if (before && handler::Summary() != before) {
            ++CheckFailures_;
        }
        return warmed;
    }

    void OnPacket(size_t size)
    {
        const uint64_t userNs = RealtimeNs();
        const uint64_t kernelNs = Socket_.KernelTimestampNs();
        if (size < itch::MoldHeaderSize) {
            return;
        }
        const std::span<const uint8_t> packet = Socket_.Packet(size);
        const itch::TMoldHeader header = itch::ReadMoldHeader(packet.data());
        if (header.Sequence == itch::EndOfBlock) {
            Ended_ = true;
            return;
        }
        const std::span<const uint8_t> body = packet.subspan(itch::MoldHeaderSize);
        if (header.Sequence == itch::SnapshotSequence) {
            if (!Options_.Synth) {
                itch::ForEachMessage(body, header.Count, [](std::span<const uint8_t> message) {
                    handler::ProcessMessage(message);
                });
            }
            ++Snapshots_;
            LastActivity_ = MonotonicNs();
            return;
        }

        const uint64_t signalsBefore = handler::RealSignals();
        const uint64_t c0 = TscBegin();
        if (Options_.Synth) {
            Sink = Sink + handler::Handle(handler::EEvent::Real, false);
        } else {
            itch::ForEachMessage(body, header.Count, [](std::span<const uint8_t> message) {
                handler::ProcessMessage(message);
            });
        }
        const uint64_t c1 = TscEnd();
        const uint64_t now = MonotonicNs();

        const bool hasMessages = header.Count > 0;
        const bool gap = LastSeq_ > 0 && header.Sequence != LastSeq_ + 1;
        TPacketRecord& record = Journal_.Append();
        record.Seq = header.Sequence;
        record.KernelNs = kernelNs;
        record.UserNs = userNs;
        record.SendNs = hasMessages ? itch::ReadBigEndian(body.data() + 2 + itch::TimestampOffset, 6) : 0;
        record.Cycles = Cap32(c1 - c0);
        record.WarmAgoNs = LastWarm_ != 0 ? Cap32(now - LastWarm_) : 0;
        record.IrqAgoNs = LastIrq_ != 0 ? Cap32(now - LastIrq_) : 0;
        record.Locate = hasMessages ? static_cast<uint16_t>(itch::ReadBigEndian(body.data() + 3, 2)) : 0;
        record.Warms = Warms_;
        record.Type = hasMessages ? body[2] : 0;
        record.Messages = static_cast<uint8_t>(std::min<uint16_t>(header.Count, 255));
        record.Signal = Options_.Synth || handler::RealSignals() != signalsBefore ? 1 : 0;
        record.Flags = gap ? NextFlags_ | FlagSequenceGap : NextFlags_;
        Gaps_ += gap ? 1 : 0;
        LastSeq_ = header.Sequence;
        NextFlags_ = 0;
        Warms_ = 0;
        LastActivity_ = now;
        ++RealPackets_;

        if (!Options_.Synth && RealPackets_ % HashEveryPackets == 0) {
            Journal_.WriteHash(header.Sequence, handler::BookHash());
            NextFlags_ = NextFlags_ | FlagBookHashed;
            LastActivity_ = MonotonicNs();
        }
        if (Journal_.Full()) {
            Journal_.Flush();
            NextFlags_ = NextFlags_ | FlagFlushed;
            LastActivity_ = MonotonicNs();
        }
    }
};

void InstallSignals()
{
    // No SA_RESTART: a blocking recvmsg returns EINTR and the loop sees the stop.
    struct sigaction action{};
    action.sa_handler = OnSignal;
    sigaction(SIGTERM, &action, nullptr);
    sigaction(SIGINT, &action, nullptr);
}

int Run(int argc, char** argv)
{
    const TOptions options = ParseOptions(argc, argv);
    PinToCpu(options.Cpu);
    MakeRealtime();
    SetTimerSlack();
    InstallSignals();
    const double tscPerNs = CalibrateTscPerNs();
    if (options.Synth) {
        if (!handler::Init(1)) {
            return 1;
        }
    } else {
        handler::LiveInit(options.Watch);
    }
    TReceiver receiver(options);
    LockMemory();
    if (!options.Ready.empty()) {
        if (std::FILE* ready = std::fopen(options.Ready.c_str(), "w"); ready != nullptr) {
            std::fclose(ready);
        } else {
            std::perror(options.Ready.c_str());
        }
    }
    Eprintln("[recv {} cpu{}] group {}, {} {}, T = {:.0f} us, TSC {:.4f} GHz",
        options.ModeName(),
        options.Cpu,
        options.Group,
        options.Synth ? "S0, event EV_REAL;" : "watching",
        options.Synth ? "" : options.Watch,
        options.PeriodUs,
        tscPerNs);
    receiver.Run();
    receiver.Report();
    return 0;
}

} // namespace
} // namespace coldpath::recv

int main(int argc, char** argv)
{
    try {
        return coldpath::recv::Run(argc, argv);
    } catch (const std::exception& e) {
        std::fprintf(stderr, "recv: %s\n", e.what());
    } catch (...) {
        std::fprintf(stderr, "recv: unknown error\n");
    }
    return 2;
}
