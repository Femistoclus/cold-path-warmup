#include "feed/websocket.h"

#include <algorithm>
#include <array>
#include <cerrno>
#include <cstring>
#include <format>
#include <netdb.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <openssl/err.h>
#include <openssl/evp.h>
#include <openssl/rand.h>
#include <openssl/ssl.h>
#include <poll.h>
#include <sys/socket.h>
#include <unistd.h>
#include <utility>

namespace coldpath::feed {
namespace {

constexpr auto SilenceLimit = std::chrono::seconds(30);
constexpr uint8_t OpText = 0x1;
constexpr uint8_t OpClose = 0x8;
constexpr uint8_t OpPing = 0x9;
constexpr uint8_t OpPong = 0xA;

std::string OpenSslError()
{
    std::array<char, 256> text{};
    ERR_error_string_n(ERR_get_error(), text.data(), text.size());
    return text.data();
}

int Connect(const std::string& host, uint16_t port)
{
    addrinfo hints{};
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    addrinfo* found = nullptr;
    if (const int error = getaddrinfo(host.c_str(), std::to_string(port).c_str(), &hints, &found); error != 0) {
        throw TConnectionError(std::format("{}: {}", host, gai_strerror(error)));
    }
    int fd = -1;
    for (const addrinfo* a = found; a != nullptr && fd < 0; a = a->ai_next) {
        fd = socket(a->ai_family, a->ai_socktype, a->ai_protocol);
        if (fd >= 0 && connect(fd, a->ai_addr, a->ai_addrlen) != 0) {
            close(fd);
            fd = -1;
        }
    }
    freeaddrinfo(found);
    if (fd < 0) {
        throw TConnectionError(std::format("{}:{}: cannot connect: {}", host, port, std::strerror(errno)));
    }
    const int one = 1;
    setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
    return fd;
}

std::string HandshakeKey()
{
    std::array<unsigned char, 16> raw{};
    RAND_bytes(raw.data(), raw.size());
    std::array<unsigned char, 32> encoded{};
    const int length = EVP_EncodeBlock(encoded.data(), raw.data(), raw.size());
    return {reinterpret_cast<const char*>(encoded.data()), static_cast<size_t>(length)};
}

} // namespace

TWebSocket::TWebSocket(const std::string& host, uint16_t port, const std::string& path)
    : Fd_(Connect(host, port))
    , Context_(SSL_CTX_new(TLS_client_method()), &SSL_CTX_free)
    , Ssl_(nullptr, &SSL_free)
{
    if (!Context_) {
        throw TConnectionError("SSL_CTX_new: " + OpenSslError());
    }
    SSL_CTX_set_default_verify_paths(Context_.get());
    SSL_CTX_set_verify(Context_.get(), SSL_VERIFY_PEER, nullptr);
    Ssl_.reset(SSL_new(Context_.get()));
    SSL_set_fd(Ssl_.get(), Fd_.Get());
    SSL_set_tlsext_host_name(Ssl_.get(), host.c_str());
    SSL_set1_host(Ssl_.get(), host.c_str());
    if (SSL_connect(Ssl_.get()) != 1) {
        throw TConnectionError("TLS: " + OpenSslError());
    }

    WriteAll(std::format("GET {} HTTP/1.1\r\nHost: {}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                         "Sec-WebSocket-Key: {}\r\nSec-WebSocket-Version: 13\r\nUser-Agent: cold-path-research\r\n\r\n",
        path,
        host,
        HandshakeKey()));
    LastData_ = std::chrono::steady_clock::now();
    size_t headerEnd = std::string::npos;
    while ((headerEnd = Buffer_.find("\r\n\r\n")) == std::string::npos) {
        ReadSome(std::chrono::milliseconds(15000));
    }
    const std::string status = Buffer_.substr(0, Buffer_.find("\r\n"));
    if (status.find(" 101 ") == std::string::npos) {
        throw TConnectionError(status);
    }
    Buffer_.erase(0, headerEnd + 4);
}

void TWebSocket::SendText(std::string_view text)
{
    SendFrame(OpText, text);
}

std::optional<std::string> TWebSocket::Receive(std::chrono::milliseconds timeout)
{
    const auto deadline = std::chrono::steady_clock::now() + timeout;
    for (;;) {
        if (auto message = TakeFrame()) {
            return message;
        }
        const auto left =
            std::chrono::duration_cast<std::chrono::milliseconds>(deadline - std::chrono::steady_clock::now());
        if (left.count() <= 0 || !ReadSome(left)) {
            return std::nullopt;
        }
    }
}

// Clients must mask their frames (RFC 6455, 5.3).
void TWebSocket::SendFrame(uint8_t opcode, std::string_view payload)
{
    constexpr uint8_t Final = 0x80;
    constexpr uint8_t Masked = 0x80;
    std::string frame;
    frame += static_cast<char>(Final | opcode);
    if (payload.size() < 126) {
        frame += static_cast<char>(Masked | static_cast<uint8_t>(payload.size()));
    } else {
        frame += static_cast<char>(Masked | uint8_t{126});
        frame += static_cast<char>(static_cast<uint8_t>(payload.size() >> 8U));
        frame += static_cast<char>(static_cast<uint8_t>(payload.size()));
    }
    std::array<unsigned char, 4> mask{};
    RAND_bytes(mask.data(), mask.size());
    frame.append(reinterpret_cast<const char*>(mask.data()), mask.size());
    for (size_t i = 0; i < payload.size(); ++i) {
        frame += static_cast<char>(static_cast<unsigned char>(payload[i]) ^ mask[i % 4]);
    }
    WriteAll(frame);
}

void TWebSocket::WriteAll(std::string_view data)
{
    while (!data.empty()) {
        const int written = SSL_write(Ssl_.get(), data.data(), static_cast<int>(data.size()));
        if (written <= 0) {
            throw TConnectionError("SSL_write: " + OpenSslError());
        }
        data.remove_prefix(static_cast<size_t>(written));
    }
}

bool TWebSocket::ReadSome(std::chrono::milliseconds timeout)
{
    if (SSL_pending(Ssl_.get()) == 0) {
        pollfd ready{.fd = Fd_.Get(), .events = POLLIN, .revents = 0};
        const int polled = poll(&ready, 1, static_cast<int>(timeout.count()));
        if (polled < 0 && errno != EINTR) {
            throw TConnectionError(std::format("poll: {}", std::strerror(errno)));
        }
        if (polled <= 0) {
            if (std::chrono::steady_clock::now() - LastData_ > SilenceLimit) {
                throw TConnectionError("no data for 30 s");
            }
            return false;
        }
    }
    std::array<char, 65536> chunk{};
    const int n = SSL_read(Ssl_.get(), chunk.data(), static_cast<int>(chunk.size()));
    if (n <= 0) {
        throw TConnectionError(SSL_get_error(Ssl_.get(), n) == SSL_ERROR_ZERO_RETURN ? "connection closed"
                                                                                     : "SSL_read: " + OpenSslError());
    }
    Buffer_.append(chunk.data(), static_cast<size_t>(n));
    LastData_ = std::chrono::steady_clock::now();
    return true;
}

std::optional<std::string> TWebSocket::TakeFrame()
{
    for (;;) {
        if (Buffer_.size() < 2) {
            return std::nullopt;
        }
        const auto* p = reinterpret_cast<const uint8_t*>(Buffer_.data());
        const bool fin = (p[0] & 0x80U) != 0;
        const auto opcode = static_cast<uint8_t>(p[0] & 0x0FU);
        uint64_t length = p[1] & 0x7FU;
        size_t header = 2;
        if (length == 126 || length == 127) {
            const size_t bytes = length == 126 ? 2 : 8;
            if (Buffer_.size() < 2 + bytes) {
                return std::nullopt;
            }
            length = 0;
            for (size_t i = 0; i < bytes; ++i) {
                length = length << 8 | uint64_t{p[2 + i]};
            }
            header += bytes;
        }
        if (Buffer_.size() < header + length) {
            return std::nullopt;
        }
        std::string payload = Buffer_.substr(header, length);
        Buffer_.erase(0, header + length);
        if (opcode == OpPing) {
            SendFrame(OpPong, payload);
            continue;
        }
        if (opcode == OpPong) {
            continue;
        }
        if (opcode == OpClose) {
            throw TConnectionError(
                "server closed the connection: " + payload.substr(std::min<size_t>(2, payload.size()), 120));
        }
        Message_ += payload;
        if (fin) {
            return std::exchange(Message_, {});
        }
    }
}

} // namespace coldpath::feed
