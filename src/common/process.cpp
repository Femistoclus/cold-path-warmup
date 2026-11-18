#include "common/process.h"

#include <cstdio>
#include <sched.h>
#include <sys/mman.h>
#include <sys/prctl.h>

namespace coldpath {

void PinToCpu(int cpu)
{
    if (cpu < 0) {
        return;
    }
    cpu_set_t set;
    CPU_ZERO(&set);
    CPU_SET(cpu, &set);
    if (sched_setaffinity(0, sizeof(set), &set) != 0) {
        std::perror("sched_setaffinity");
    }
}

void MakeRealtime(int priority)
{
    const sched_param param{.sched_priority = priority};
    if (sched_setscheduler(0, SCHED_FIFO, &param) != 0) {
        std::perror("SCHED_FIFO (needs root)");
    }
}

void SetTimerSlack(unsigned long ns)
{
    // Otherwise nanosleep may wake up to ~50 µs late.
    if (prctl(PR_SET_TIMERSLACK, ns) != 0) {
        std::perror("PR_SET_TIMERSLACK");
    }
}

void LockMemory()
{
    if (mlockall(MCL_CURRENT | MCL_FUTURE) != 0) {
        std::perror("mlockall");
    }
}

} // namespace coldpath
