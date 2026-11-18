#include "handler/send.h"

#include "common/print.h"

#include <algorithm>
#include <arpa/inet.h>
#include <bit>
#include <cerrno>
#include <charconv>
#include <cstdlib>
#include <cstring>
#include <format>
#include <netinet/in.h>
#include <string_view>
#include <sys/socket.h>
#include <unistd.h>

namespace coldpath::handler {
namespace {

constexpr int64_t MaxShares = 999999;
constexpr std::array<char, 8> Stock = {'C', 'O', 'L', 'D', 'P', 'A', 'T', 'H'};
constexpr std::array<char, 4> Firm = {'C', 'P', 'T', 'H'};

std::string EnvOr(const char* name, std::string_view fallback)
{
    const char* value = std::getenv(name);
    return value != nullptr ? std::string(value) : std::string(fallback);
}

// A UDP socket connected to "a.b.c.d:port". Only a directly connected destination is accepted (dummy0 after
// setup-tx.sh): through a gateway the orders would leave the host.
int OpenSocket(const std::string& address)
{
    sockaddr_in to{};
    to.sin_family = AF_INET;
    const size_t colon = address.rfind(':');
    uint16_t port = 0;
    const char* end = address.data() + address.size();
    if (colon == std::string::npos || inet_pton(AF_INET, address.substr(0, colon).c_str(), &to.sin_addr) != 1 ||
        std::from_chars(address.data() + colon + 1, end, port).ptr != end)
    {
        Eprintln("{}: expected an address like 10.255.0.2:9", address);
        return -1;
    }
    to.sin_port = htons(port);
    const int fd = socket(AF_INET, SOCK_DGRAM | SOCK_CLOEXEC, 0);
    if (fd < 0) {
        Eprintln("socket: {}", std::strerror(errno));
        return -1;
    }
    sockaddr_in from{};
    socklen_t size = sizeof(from);
    if (connect(fd, reinterpret_cast<const sockaddr*>(&to), sizeof(to)) != 0 ||
        getsockname(fd, reinterpret_cast<sockaddr*>(&from), &size) != 0)
    {
        Eprintln("{}: {}; run sudo ./setup-tx.sh", address, std::strerror(errno));
        close(fd);
        return -1;
    }
    if (((from.sin_addr.s_addr ^ to.sin_addr.s_addr) & htonl(0xFFFFFF00U)) != 0) {
        std::array<char, INET_ADDRSTRLEN> source{};
        inet_ntop(AF_INET, &from.sin_addr, source.data(), source.size());
        Eprintln("{}: route does not go through dummy0 (source address {}); run sudo ./setup-tx.sh",
            address,
            source.data());
        close(fd);
        return -1;
    }
    return fd;
}

} // namespace

TOrderSender::~TOrderSender()
{
    for (const int fd : {RealFd_, WarmFd_}) {
        if (fd >= 0) {
            close(fd);
        }
    }
}

bool TOrderSender::Init()
{
    const std::string mode = EnvOr("COLDPATH_WARM_SEND", "0");
    if (mode != "0" && mode != "1" && mode != "2") {
        Eprintln("COLDPATH_WARM_SEND={}: expected 0, 1 or 2", mode);
        return false;
    }
    WarmMode_ = static_cast<EWarmSend>(mode[0] - '0');
    RealAddress_ = EnvOr("COLDPATH_SEND_ADDR", "10.255.0.2:9");
    RealFd_ = OpenSocket(RealAddress_);
    if (RealFd_ < 0) {
        return false;
    }
    if (WarmMode_ == EWarmSend::Send) {
        WarmAddress_ = EnvOr("COLDPATH_WARM_ADDR", "10.255.0.3:9");
        if (WarmAddress_ == RealAddress_) {
            Eprintln("COLDPATH_WARM_ADDR equals COLDPATH_SEND_ADDR");
            return false;
        }
        WarmFd_ = OpenSocket(WarmAddress_);
        if (WarmFd_ < 0) {
            return false;
        }
    }
    return true;
}

// Sign → side, size → shares. The token is "CP" and the number of the next real order.
// NOLINTNEXTLINE(bugprone-easily-swappable-parameters): signal and price, different units.
void TOrderSender::Encode(int64_t signal, uint32_t price)
{
    const int64_t clamped = std::clamp(signal, -MaxShares, MaxShares);
    Order_.Type = 'O';
    Order_.Token[0] = 'C';
    Order_.Token[1] = 'P';
    uint64_t number = Real_ + 1;
    for (size_t i = Order_.Token.size() - 1; i >= 2; --i) {
        Order_.Token[i] = static_cast<char>('0' + number % 10);
        number /= 10;
    }
    Order_.Side = signal < 0 ? 'S' : 'B';
    Order_.Shares = std::byteswap(static_cast<uint32_t>(std::max<int64_t>(clamped < 0 ? -clamped : clamped, 1)));
    Order_.Stock = Stock;
    Order_.Price = std::byteswap(price);
    Order_.TimeInForce = 0;
    Order_.Firm = Firm;
    Order_.Display = 'Y';
    Order_.Capacity = 'P';
    Order_.IntermarketSweep = 'N';
    Order_.MinimumQuantity = 0;
    Order_.CrossType = 'N';
    Order_.CustomerType = 'R';
}

void TOrderSender::Send(bool dryRun)
{
    if (!dryRun) {
        Transmit(RealFd_);
        ++Real_;
        return;
    }
    if (WarmMode_ == EWarmSend::Send) {
        // Marked: an exchange would reject even a delivered warm order.
        std::memcpy(Order_.Token.data(), "WARM", 4);
        Order_.Shares = 0;
        Transmit(WarmFd_);
        ++Warm_;
    }
}

void TOrderSender::Transmit(int fd)
{
    if (sendto(fd, &Order_, sizeof(Order_), MSG_DONTWAIT, nullptr, 0) < 0) {
        ++Errors_;
        LastError_ = errno;
    }
}

std::string TOrderSender::Describe() const
{
    std::string warm;
    switch (WarmMode_) {
        case EWarmSend::None:
            warm = "W0, no encoding or sending";
            break;
        case EWarmSend::Encode:
            warm = "W1, encoding without sendto";
            break;
        case EWarmSend::Send:
            warm = std::format("W2, encoding and sendto to {}", WarmAddress_);
            break;
    }
    return std::format("send: OUCH Enter Order {} bytes, UDP to {}; dry run {}", sizeof(Order_), RealAddress_, warm);
}

std::string TOrderSender::Summary() const
{
    return std::format("orders sent {}, warm {}, sendto errors {}{}",
        Real_,
        Warm_,
        Errors_,
        Errors_ > 0 ? std::format(" (last: {})", std::strerror(LastError_)) : "");
}

} // namespace coldpath::handler
