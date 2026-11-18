#include "recv/socket.h"

#include "common/clock.h"

#include <arpa/inet.h>
#include <cerrno>
#include <cstring>
#include <netinet/in.h>
#include <system_error>
#include <unistd.h>

namespace coldpath::recv {
namespace {

void SetOption(int fd, int level, int name, int value)
{
    setsockopt(fd, level, name, &value, sizeof(value));
}

} // namespace

TMulticastSocket::TMulticastSocket(const std::string& group, uint16_t port)
    : Fd_(socket(AF_INET, SOCK_DGRAM, 0))
{
    if (Fd_ < 0) {
        throw std::system_error(errno, std::generic_category(), "socket");
    }
    SetOption(Fd_, SOL_SOCKET, SO_REUSEADDR, 1);
    const int buffer = 32 * 1024 * 1024;
    if (setsockopt(Fd_, SOL_SOCKET, SO_RCVBUFFORCE, &buffer, sizeof(buffer)) != 0) {
        SetOption(Fd_, SOL_SOCKET, SO_RCVBUF, buffer);
    }
    SetOption(Fd_, SOL_SOCKET, SO_TIMESTAMPNS, 1);

    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_port = htons(port);
    inet_pton(AF_INET, group.c_str(), &address.sin_addr);
    if (bind(Fd_, reinterpret_cast<const sockaddr*>(&address), sizeof(address)) != 0) {
        throw std::system_error(errno, std::generic_category(), "bind");
    }
    ip_mreq membership{};
    inet_pton(AF_INET, group.c_str(), &membership.imr_multiaddr);
    inet_pton(AF_INET, "127.0.0.1", &membership.imr_interface);
    if (setsockopt(Fd_, IPPROTO_IP, IP_ADD_MEMBERSHIP, &membership, sizeof(membership)) != 0) {
        throw std::system_error(errno, std::generic_category(), "IP_ADD_MEMBERSHIP");
    }

    Vector_ = {.iov_base = Packet_.data(), .iov_len = Packet_.size()};
    Header_.msg_iov = &Vector_;
    Header_.msg_iovlen = 1;
    Header_.msg_control = Control_.data();
    Header_.msg_controllen = Control_.size();
}

TMulticastSocket::~TMulticastSocket()
{
    close(Fd_);
}

} // namespace coldpath::recv
