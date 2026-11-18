#include "feed/sender.h"

#include <algorithm>
#include <arpa/inet.h>
#include <cerrno>
#include <format>
#include <stdexcept>
#include <sys/socket.h>
#include <system_error>
#include <unistd.h>

namespace coldpath::feed {
namespace {

sockaddr_in GroupAddress(const std::string& group)
{
    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_port = htons(Port);
    if (inet_pton(AF_INET, group.c_str(), &address.sin_addr) != 1) {
        throw std::invalid_argument("bad group address " + group);
    }
    return address;
}

} // namespace

TSender::TSender(const std::vector<std::string>& pairs, const std::vector<std::string>& solo)
    : Fd_(socket(AF_INET, SOCK_DGRAM, 0))
    , All_(GroupAddress("239.192.0.1"))
    , SoloByLocate_(pairs.size() + 1)
{
    if (Fd_ < 0) {
        throw std::system_error(errno, std::generic_category(), "socket");
    }
    const int buffer = 4 * 1024 * 1024;
    setsockopt(Fd_, SOL_SOCKET, SO_SNDBUF, &buffer, sizeof(buffer));
    in_addr loopback{};
    inet_pton(AF_INET, "127.0.0.1", &loopback);
    setsockopt(Fd_, IPPROTO_IP, IP_MULTICAST_IF, &loopback, sizeof(loopback));
    const unsigned char loop = 1;
    const unsigned char ttl = 0;
    setsockopt(Fd_, IPPROTO_IP, IP_MULTICAST_LOOP, &loop, sizeof(loop));
    setsockopt(Fd_, IPPROTO_IP, IP_MULTICAST_TTL, &ttl, sizeof(ttl));
    for (size_t i = 0; i < solo.size(); ++i) {
        const auto pair = std::ranges::find(pairs, solo[i]);
        if (pair == pairs.end()) {
            throw std::invalid_argument("--solo " + solo[i] + ": pair not in --pairs");
        }
        const sockaddr_in group = GroupAddress(std::format("239.192.0.{}", 2 + i));
        Solo_.push_back(group);
        SoloByLocate_[static_cast<size_t>(pair - pairs.begin()) + 1] = group;
    }
}

TSender::~TSender()
{
    close(Fd_);
}

void TSender::Send(std::span<const uint8_t> packet, uint16_t locate) const
{
    SendTo(packet, All_);
    if (locate == 0) {
        for (const sockaddr_in& group : Solo_) {
            SendTo(packet, group);
        }
    } else if (locate < SoloByLocate_.size()) {
        if (const std::optional<sockaddr_in>& group = SoloByLocate_[locate]) {
            SendTo(packet, *group);
        }
    }
}

void TSender::SendTo(std::span<const uint8_t> packet, const sockaddr_in& group) const
{
    sendto(Fd_, packet.data(), packet.size(), 0, reinterpret_cast<const sockaddr*>(&group), sizeof(group));
}

} // namespace coldpath::feed
