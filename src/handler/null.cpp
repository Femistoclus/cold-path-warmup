// Empty handler: its time is the overhead of the timing window and the call.
#include "handler/handler.h"

namespace coldpath::handler {

bool Init(unsigned /*seed*/)
{
    return true;
}

[[gnu::noinline]] uint64_t Handle(EEvent event, bool dryRun)
{
    __asm__ volatile("" ::: "memory");
    return static_cast<uint64_t>(event) + static_cast<uint64_t>(dryRun);
}

std::string_view Info()
{
    return "empty handler (measurement window calibration)";
}

TPrefetchData PrefetchData()
{
    return {};
}

} // namespace coldpath::handler
