#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <span>
#include <string_view>
#include <vector>

// MoldUDP64: session (10 bytes), sequence number (8), message count (2), then ITCH messages with a 2-byte length.
namespace coldpath::itch {

constexpr uint64_t DayNs = 86'400'000'000'000ULL;
constexpr size_t MoldHeaderSize = 20;
constexpr uint64_t SnapshotSequence = 0;
constexpr uint64_t EndOfBlock = UINT64_MAX;
constexpr std::string_view Session = "COLDPATH01";
constexpr size_t TimestampOffset = 5;

inline uint64_t ReadBigEndian(const uint8_t* data, size_t bytes)
{
    uint64_t value = 0;
    for (size_t i = 0; i < bytes; ++i) {
        value = value << 8 | uint64_t{data[i]};
    }
    return value;
}

using TStockName = std::array<char, 8>;
TStockName StockName(std::string_view name);

class TMessage
{
public:
    static constexpr size_t Capacity = 40;

    std::span<const uint8_t> Bytes() const
    {
        return {Data_.data(), Size_};
    }

    char Type() const
    {
        return static_cast<char>(Data_[0]);
    }

    void SetTimestamp(uint64_t ns);
    static TMessage FromBytes(std::span<const uint8_t> bytes);

private:
    friend class TMessageWriter;
    std::array<uint8_t, Capacity> Data_{};
    uint8_t Size_ = 0;
};

class TMessageWriter
{
public:
    TMessageWriter(char type, uint64_t ns, uint16_t locate);
    template <size_t Bytes>
    TMessageWriter& Number(uint64_t value)
    {
        for (size_t i = Bytes; i-- > 0;) {
            Message_.Data_[Message_.Size_++] = static_cast<uint8_t>(value >> (8 * i));
        }
        return *this;
    }

    TMessageWriter& Char(char value);
    TMessageWriter& Stock(const TStockName& name);
    TMessage Finish() const;

private:
    TMessage Message_;
};

TMessage StockDirectory(uint64_t ns, uint16_t locate, std::string_view stock);
TMessage AddOrder(uint64_t ns,
    uint16_t locate,
    uint64_t ref,
    char side,
    uint32_t shares,
    const TStockName& stock,
    uint32_t price);
TMessage OrderDelete(uint64_t ns, uint16_t locate, uint64_t ref);
TMessage OrderExecuted(uint64_t ns, uint16_t locate, uint64_t ref, uint32_t shares, uint64_t match);
TMessage OrderCancel(uint64_t ns, uint16_t locate, uint64_t ref, uint32_t shares);

struct TReplace
{
    uint64_t OldRef = 0;
    uint64_t NewRef = 0;
    uint32_t Shares = 0;
    uint32_t Price = 0;
};
TMessage OrderReplace(uint64_t ns, uint16_t locate, const TReplace& replace);

std::vector<uint8_t> MoldPacket(uint64_t sequence, std::span<const TMessage> messages);

struct TMoldHeader
{
    uint64_t Sequence = 0;
    uint16_t Count = 0;
};

inline TMoldHeader ReadMoldHeader(const uint8_t* packet)
{
    return {.Sequence = ReadBigEndian(packet + 10, 8), .Count = static_cast<uint16_t>(ReadBigEndian(packet + 18, 2))};
}

template <class TCallback>
void ForEachMessage(std::span<const uint8_t> body, size_t count, TCallback&& onMessage)
{
    const uint8_t* p = body.data();
    const uint8_t* end = p + body.size();
    for (size_t i = 0; i < count && p + 2 <= end; ++i) {
        const size_t length = ReadBigEndian(p, 2);
        if (p + 2 + length > end) {
            break;
        }
        onMessage(std::span<const uint8_t>(p + 2, length));
        p += 2 + length;
    }
}

std::vector<TMessage> ParseMessages(std::span<const uint8_t> body);

} // namespace coldpath::itch
