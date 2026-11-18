#include "common/itch.h"

#include <algorithm>
#include <cctype>
#include <limits>
#include <stdexcept>

namespace coldpath::itch {

TStockName StockName(std::string_view name)
{
    TStockName out;
    out.fill(' ');
    for (size_t i = 0; i < out.size() && i < name.size(); ++i) {
        out[i] = static_cast<char>(std::toupper(static_cast<unsigned char>(name[i])));
    }
    return out;
}

void TMessage::SetTimestamp(uint64_t ns)
{
    ns %= DayNs;
    for (size_t i = 0; i < 6; ++i) {
        Data_[TimestampOffset + i] = static_cast<uint8_t>(ns >> (8 * (5 - i)));
    }
}

TMessage TMessage::FromBytes(std::span<const uint8_t> bytes)
{
    if (bytes.size() > Capacity) {
        throw std::length_error("ITCH message longer than 40 bytes");
    }
    TMessage message;
    std::ranges::copy(bytes, message.Data_.begin());
    message.Size_ = static_cast<uint8_t>(bytes.size());
    return message;
}

TMessageWriter::TMessageWriter(char type, uint64_t ns, uint16_t locate)
{
    Char(type).Number<2>(locate).Number<2>(0).Number<6>(ns % DayNs);
}

TMessageWriter& TMessageWriter::Char(char value)
{
    Message_.Data_[Message_.Size_++] = static_cast<uint8_t>(value);
    return *this;
}

TMessageWriter& TMessageWriter::Stock(const TStockName& name)
{
    for (const char c : name) {
        Char(c);
    }
    return *this;
}

TMessage TMessageWriter::Finish() const
{
    return Message_;
}

TMessage StockDirectory(uint64_t ns, uint16_t locate, std::string_view stock)
{
    return TMessageWriter('R', ns, locate)
        .Stock(StockName(stock))
        .Char('Q')
        .Char('N')
        .Number<4>(1)
        .Char('N')
        .Char('Z')
        .Char('Z')
        .Char(' ')
        .Char('P')
        .Char('N')
        .Char(' ')
        .Char('1')
        .Char('N')
        .Number<4>(0)
        .Char('N')
        .Finish();
}

TMessage AddOrder(uint64_t ns,
    uint16_t locate,
    uint64_t ref,
    char side,
    uint32_t shares,
    const TStockName& stock,
    uint32_t price)
{
    return TMessageWriter('A', ns, locate)
        .Number<8>(ref)
        .Char(side)
        .Number<4>(shares)
        .Stock(stock)
        .Number<4>(price)
        .Finish();
}

TMessage OrderDelete(uint64_t ns, uint16_t locate, uint64_t ref)
{
    return TMessageWriter('D', ns, locate).Number<8>(ref).Finish();
}

TMessage OrderExecuted(uint64_t ns, uint16_t locate, uint64_t ref, uint32_t shares, uint64_t match)
{
    return TMessageWriter('E', ns, locate).Number<8>(ref).Number<4>(shares).Number<8>(match).Finish();
}

TMessage OrderCancel(uint64_t ns, uint16_t locate, uint64_t ref, uint32_t shares)
{
    return TMessageWriter('X', ns, locate).Number<8>(ref).Number<4>(shares).Finish();
}

TMessage OrderReplace(uint64_t ns, uint16_t locate, const TReplace& replace)
{
    return TMessageWriter('U', ns, locate)
        .Number<8>(replace.OldRef)
        .Number<8>(replace.NewRef)
        .Number<4>(replace.Shares)
        .Number<4>(replace.Price)
        .Finish();
}

std::vector<uint8_t> MoldPacket(uint64_t sequence, std::span<const TMessage> messages)
{
    std::vector<uint8_t> packet(Session.begin(), Session.end());
    const auto put = [&packet](uint64_t value, size_t bytes) {
        for (size_t i = bytes; i-- > 0;) {
            packet.push_back(static_cast<uint8_t>(value >> (8 * i)));
        }
    };
    put(sequence, 8);
    put(messages.size(), 2);
    for (const TMessage& message : messages) {
        put(message.Bytes().size(), 2);
        packet.insert(packet.end(), message.Bytes().begin(), message.Bytes().end());
    }
    return packet;
}

std::vector<TMessage> ParseMessages(std::span<const uint8_t> body)
{
    std::vector<TMessage> messages;
    ForEachMessage(body, std::numeric_limits<size_t>::max(), [&messages](std::span<const uint8_t> bytes) {
        messages.push_back(TMessage::FromBytes(bytes));
    });
    return messages;
}

} // namespace coldpath::itch
