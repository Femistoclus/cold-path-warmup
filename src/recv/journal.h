#pragma once

#include "common/file.h"

#include <cstdint>
#include <cstdio>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace coldpath::recv {

// online.py reads this layout: change fields or their order only together with it.
struct TPacketRecord
{
    uint64_t Seq = 0;
    uint64_t KernelNs = 0;
    uint64_t UserNs = 0;
    uint64_t SendNs = 0;
    uint32_t Cycles = 0;
    uint32_t WarmAgoNs = 0;
    uint32_t IrqAgoNs = 0;
    uint16_t Locate = 0;
    uint16_t Warms = 0;
    uint8_t Type = 0;
    uint8_t Messages = 0;
    uint8_t Signal = 0;
    uint8_t Flags = 0;
};
static_assert(sizeof(TPacketRecord) == 56);

constexpr uint8_t FlagFlushed = 1;
constexpr uint8_t FlagSequenceGap = 2;
// Hashing the book warms it, so analysis drops the next packet.
constexpr uint8_t FlagBookHashed = 4;

class TJournal
{
public:
    TJournal(const std::string& path, size_t capacity);

    TPacketRecord& Append()
    {
        return Records_[Size_++];
    }

    bool Full() const
    {
        return Size_ == Records_.size();
    }

    void Flush();
    void WriteHash(uint64_t seq, uint64_t hash);
    void Finish(uint64_t lastSeq, std::optional<uint64_t> hash);

    size_t Total() const
    {
        return Total_ + Size_;
    }

private:
    TFilePtr Out_;
    TFilePtr Hashes_;
    std::vector<TPacketRecord> Records_;
    size_t Size_ = 0;
    size_t Total_ = 0;
};

} // namespace coldpath::recv
