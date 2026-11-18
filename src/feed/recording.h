#pragma once

#include "common/file.h"

#include <cstdint>
#include <cstdio>
#include <memory>
#include <nlohmann/json.hpp>
#include <optional>
#include <span>
#include <string>
#include <vector>

// Recording: MAGIC, a JSON line, then per packet [send time u64, seq u64, locate u16, length u16] (little-endian)
// and the packet body without the MoldUDP64 header.
namespace coldpath::feed {

constexpr std::string_view RecordingMagic = "CPFEED1\n";

struct TRecordHeader
{
    uint64_t SentNs = 0;
    uint64_t Seq = 0;
    uint16_t Locate = 0;
};

class TRecorder
{
public:
    TRecorder(const std::string& path, const nlohmann::ordered_json& meta);

    void Write(const TRecordHeader& header, std::span<const uint8_t> packet);
    void Flush();
    void Close();

private:
    TFilePtr File_;
};

struct TRecordedPacket
{
    uint64_t TimeNs = 0;
    uint64_t Seq = 0;
    uint16_t Locate = 0;
    std::span<const uint8_t> Body;
};

class TRecording
{
public:
    explicit TRecording(const std::string& path);
    ~TRecording();
    TRecording(const TRecording&) = delete;
    TRecording& operator=(const TRecording&) = delete;

    const nlohmann::json& Meta() const;
    size_t DataBegin() const;
    size_t DataEnd() const;
    std::optional<TRecordedPacket> Next(size_t& offset) const;

private:
    const uint8_t* Data_ = nullptr;
    size_t Size_ = 0;
    size_t DataBegin_ = 0;
    nlohmann::json Meta_;
};

// A block runs from the seq-0 packet after an end-of-block through the next end-of-block.
struct TBlock
{
    size_t Begin = 0;
    size_t End = 0;
    uint64_t RealPackets = 0;
    uint64_t FirstNs = 0;
    uint64_t LastNs = 0;
    bool HasEnd = false;
};

std::vector<TBlock> SplitBlocks(const TRecording& recording);

} // namespace coldpath::feed
