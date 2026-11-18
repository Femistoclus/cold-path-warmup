#pragma once

#include <chrono>
#include <cstdint>
#include <memory>
#include <openssl/ssl.h>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <unistd.h>

namespace coldpath::feed {

class TConnectionError : public std::runtime_error
{
public:
    using std::runtime_error::runtime_error;
};

class TSocketFd
{
public:
    explicit TSocketFd(int fd)
        : Fd_(fd)
    { }

    ~TSocketFd()
    {
        close(Fd_);
    }

    TSocketFd(const TSocketFd&) = delete;
    TSocketFd& operator=(const TSocketFd&) = delete;

    int Get() const
    {
        return Fd_;
    }

private:
    int Fd_;
};

class TWebSocket
{
public:
    TWebSocket(const std::string& host, uint16_t port, const std::string& path);

    void SendText(std::string_view text);
    std::optional<std::string> Receive(std::chrono::milliseconds timeout);

private:
    TSocketFd Fd_;
    std::unique_ptr<SSL_CTX, decltype(&SSL_CTX_free)> Context_;
    std::unique_ptr<SSL, decltype(&SSL_free)> Ssl_;
    std::string Buffer_;
    std::string Message_;
    std::chrono::steady_clock::time_point LastData_;

    void SendFrame(uint8_t opcode, std::string_view payload);
    void WriteAll(std::string_view data);
    bool ReadSome(std::chrono::milliseconds timeout);
    std::optional<std::string> TakeFrame();
};

} // namespace coldpath::feed
