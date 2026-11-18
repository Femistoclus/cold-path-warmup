#pragma once

#include <cerrno>
#include <cstdint>
#include <ctime>

namespace coldpath {

constexpr uint64_t NsPerSecond = 1'000'000'000ULL;

inline uint64_t ClockNs(clockid_t clock)
{
    timespec ts{};
    clock_gettime(clock, &ts);
    return static_cast<uint64_t>(ts.tv_sec) * NsPerSecond + static_cast<uint64_t>(ts.tv_nsec);
}

inline uint64_t MonotonicNs()
{
    return ClockNs(CLOCK_MONOTONIC);
}

inline uint64_t RealtimeNs()
{
    return ClockNs(CLOCK_REALTIME);
}

inline timespec ToTimespec(uint64_t ns)
{
    return {.tv_sec = static_cast<time_t>(ns / NsPerSecond), .tv_nsec = static_cast<long>(ns % NsPerSecond)};
}

inline void SleepUntil(uint64_t monotonicNs)
{
    const timespec ts = ToTimespec(monotonicNs);
    while (clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &ts, nullptr) == EINTR) {
    }
}

} // namespace coldpath
