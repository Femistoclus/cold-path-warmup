#pragma once

#include <array>
#include <cstdint>
#include <string>

// Order sending for the size axis: an OUCH 4.2 Enter Order over UDP to a blackhole (dummy0, see setup-tx.sh).
namespace coldpath::handler {

// What a dry run does with the order (COLDPATH_WARM_SEND).
enum class EWarmSend : uint8_t {
    None = 0,   // W0: neither encode nor send
    Encode = 1, // W1: encode into the same buffer, skip sendto
    Send = 2,   // W2: encode and sendto on a second socket to the warm address
};

struct [[gnu::packed]] TEnterOrder
{
    char Type;
    std::array<char, 14> Token;
    char Side;
    uint32_t Shares;
    std::array<char, 8> Stock;
    uint32_t Price;
    uint32_t TimeInForce;
    std::array<char, 4> Firm;
    char Display;
    char Capacity;
    char IntermarketSweep;
    uint32_t MinimumQuantity;
    char CrossType;
    char CustomerType;
};
static_assert(sizeof(TEnterOrder) == 49);

class TOrderSender
{
public:
    TOrderSender() = default;
    TOrderSender(const TOrderSender&) = delete;
    TOrderSender& operator=(const TOrderSender&) = delete;
    ~TOrderSender();

    // Reads COLDPATH_WARM_SEND, COLDPATH_SEND_ADDR, COLDPATH_WARM_ADDR; prints the reason on failure.
    bool Init();
    void Encode(int64_t signal, uint32_t price);
    void Send(bool dryRun);

    EWarmSend WarmMode() const
    {
        return WarmMode_;
    }

    uint64_t RealSends() const
    {
        return Real_;
    }

    const void* Buffer() const
    {
        return &Order_;
    }

    std::string Describe() const;
    std::string Summary() const;

private:
    alignas(64) TEnterOrder Order_{};
    EWarmSend WarmMode_ = EWarmSend::None;
    int RealFd_ = -1;
    int WarmFd_ = -1;
    int LastError_ = 0;
    uint64_t Real_ = 0;
    uint64_t Warm_ = 0;
    uint64_t Errors_ = 0;
    std::string RealAddress_;
    std::string WarmAddress_;

    void Transmit(int fd);
};

} // namespace coldpath::handler
