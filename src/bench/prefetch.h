#pragma once

#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

namespace coldpath::bench {

// Sets: d — data (into L1d), c — code (into L2), dc — both, g — data known before the event + code, 0 — none.
class TPrefetcher
{
public:
    TPrefetcher(std::string_view set, int reps);

    [[gnu::noinline]] void Run() const;
    std::string Describe() const;

    // The event's data lines and the traced code lines (for clflush disruptors).
    const std::vector<const void*>& DataLines() const
    {
        return Data_;
    }

    std::vector<const void*> CodeLines() const;

private:
    std::string Set_;
    int Reps_ = 1;
    bool UseData_ = false;
    bool UseCode_ = false;
    bool GenericOnly_ = false;
    std::vector<const void*> Data_;
    size_t Generic_ = 0;
    std::vector<const char*> CodeStart_;
    std::vector<int> CodeLines_;
    size_t CodeLineTotal_ = 0;

    void TraceCode();
};

} // namespace coldpath::bench
