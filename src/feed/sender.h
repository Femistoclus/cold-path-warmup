#pragma once

#include <cstdint>
#include <netinet/in.h>
#include <optional>
#include <span>
#include <string>
#include <vector>

namespace coldpath::feed {

constexpr uint16_t Port = 31001;

// 239.192.0.1 carries the full feed, 239.192.0.(2 + i) the i-th solo pair; locate 0 goes to every group.
class TSender
{
public:
    TSender(const std::vector<std::string>& pairs, const std::vector<std::string>& solo);
    ~TSender();
    TSender(const TSender&) = delete;
    TSender& operator=(const TSender&) = delete;

    void Send(std::span<const uint8_t> packet, uint16_t locate) const;

private:
    int Fd_ = -1;
    sockaddr_in All_{};
    std::vector<sockaddr_in> Solo_;
    std::vector<std::optional<sockaddr_in>> SoloByLocate_;

    void SendTo(std::span<const uint8_t> packet, const sockaddr_in& group) const;
};

} // namespace coldpath::feed
