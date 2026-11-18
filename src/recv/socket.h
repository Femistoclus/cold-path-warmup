#pragma once

#include <array>
#include <cstdint>
#include <cstring>
#include <ctime>
#include <span>
#include <string>
#include <sys/socket.h>
#include <sys/types.h>

namespace coldpath::recv {

class TMulticastSocket
{
public:
    TMulticastSocket(const std::string& group, uint16_t port);
    ~TMulticastSocket();
    TMulticastSocket(const TMulticastSocket&) = delete;
    TMulticastSocket& operator=(const TMulticastSocket&) = delete;

    int Fd() const
    {
        return Fd_;
    }

    ssize_t Receive(int flags)
    {
        Header_.msg_controllen = Control_.size();
        return recvmsg(Fd_, &Header_, flags);
    }

    uint64_t KernelTimestampNs()
    {
        uint64_t ns = 0;
        for (cmsghdr* c = CMSG_FIRSTHDR(&Header_); c != nullptr; c = CMSG_NXTHDR(&Header_, c)) {
            if (c->cmsg_level == SOL_SOCKET && c->cmsg_type == SCM_TIMESTAMPNS) {
                timespec ts{};
                std::memcpy(&ts, CMSG_DATA(c), sizeof(ts));
                ns = static_cast<uint64_t>(ts.tv_sec) * 1'000'000'000ULL + static_cast<uint64_t>(ts.tv_nsec);
            }
        }
        return ns;
    }

    std::span<const uint8_t> Packet(size_t size) const
    {
        return {Packet_.data(), size};
    }

private:
    int Fd_ = -1;
    std::array<uint8_t, 65536> Packet_{};
    std::array<char, 256> Control_{};
    iovec Vector_{};
    msghdr Header_{};
};

} // namespace coldpath::recv
