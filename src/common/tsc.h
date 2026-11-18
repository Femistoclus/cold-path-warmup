#pragma once

#include "common/clock.h"

#include <cstdint>
#include <x86intrin.h>

namespace coldpath {

// Timing window lfence; rdtsc … rdtscp; lfence keeps neighbouring instructions outside.
inline uint64_t TscBegin()
{
    _mm_lfence();
    const uint64_t t = __rdtsc();
    _mm_lfence();
    return t;
}

inline uint64_t TscEnd()
{
    unsigned aux = 0;
    const uint64_t t = __rdtscp(&aux);
    _mm_lfence();
    return t;
}

inline double CalibrateTscPerNs()
{
    const uint64_t n0 = MonotonicNs();
    const uint64_t t0 = __rdtsc();
    uint64_t n1 = MonotonicNs();
    while (n1 - n0 < 200'000'000ULL) {
        n1 = MonotonicNs();
    }
    return static_cast<double>(__rdtsc() - t0) / static_cast<double>(n1 - n0);
}

} // namespace coldpath
