#include "feed/recording.h"

#include "common/itch.h"

#include <array>
#include <cerrno>
#include <cstring>
#include <fcntl.h>
#include <stdexcept>
#include <sys/mman.h>
#include <sys/stat.h>
#include <system_error>
#include <unistd.h>

namespace coldpath::feed {
namespace {

constexpr size_t HeaderSize = 20;

template <class T>
T ReadLittle(const uint8_t* p)
{
    T value{};
    std::memcpy(&value, p, sizeof(T));
    return value;
}

} // namespace

TRecorder::TRecorder(const std::string& path, const nlohmann::ordered_json& meta)
    : File_(path.empty() ? nullptr : OpenFile(path, "wb"))
{
    if (!path.empty() && !File_) {
        throw std::system_error(errno, std::generic_category(), path);
    }
    if (File_) {
        const std::string header = std::string(RecordingMagic) + meta.dump() + "\n";
        std::fwrite(header.data(), 1, header.size(), File_.get());
    }
}

void TRecorder::Write(const TRecordHeader& header, std::span<const uint8_t> packet)
{
    if (!File_) {
        return;
    }
    const std::span<const uint8_t> body = packet.subspan(itch::MoldHeaderSize);
    std::array<uint8_t, HeaderSize> bytes{};
    const auto length = static_cast<uint16_t>(body.size());
    std::memcpy(bytes.data(), &header.SentNs, 8);
    std::memcpy(bytes.data() + 8, &header.Seq, 8);
    std::memcpy(bytes.data() + 16, &header.Locate, 2);
    std::memcpy(bytes.data() + 18, &length, 2);
    std::fwrite(bytes.data(), 1, bytes.size(), File_.get());
    std::fwrite(body.data(), 1, body.size(), File_.get());
}

void TRecorder::Flush()
{
    if (File_) {
        std::fflush(File_.get());
    }
}

void TRecorder::Close()
{
    File_.reset();
}

TRecording::TRecording(const std::string& path)
{
    const int fd = open(path.c_str(), O_RDONLY | O_CLOEXEC);
    if (fd < 0) {
        throw std::system_error(errno, std::generic_category(), path);
    }
    struct stat info{};
    fstat(fd, &info);
    Size_ = static_cast<size_t>(info.st_size);
    void* data = Size_ > 0 ? mmap(nullptr, Size_, PROT_READ, MAP_PRIVATE, fd, 0) : MAP_FAILED;
    close(fd);
    if (data == MAP_FAILED) {
        throw std::runtime_error(path + ": mmap failed");
    }
    Data_ = static_cast<const uint8_t*>(data);
    const std::string_view text(reinterpret_cast<const char*>(Data_), Size_);
    const size_t metaEnd = text.find('\n', RecordingMagic.size());
    if (!text.starts_with(RecordingMagic) || metaEnd == std::string_view::npos) {
        throw std::runtime_error(path + ": not a feed recording");
    }
    Meta_ = nlohmann::json::parse(text.substr(RecordingMagic.size(), metaEnd - RecordingMagic.size()));
    DataBegin_ = metaEnd + 1;
}

TRecording::~TRecording()
{
    munmap(const_cast<uint8_t*>(Data_), Size_);
}

const nlohmann::json& TRecording::Meta() const
{
    return Meta_;
}

size_t TRecording::DataBegin() const
{
    return DataBegin_;
}

size_t TRecording::DataEnd() const
{
    return Size_;
}

std::optional<TRecordedPacket> TRecording::Next(size_t& offset) const
{
    if (offset + HeaderSize > Size_) {
        return std::nullopt;
    }
    const uint8_t* p = Data_ + offset;
    const auto length = ReadLittle<uint16_t>(p + 18);
    if (offset + HeaderSize + length > Size_) {
        return std::nullopt;
    }
    TRecordedPacket packet{.TimeNs = ReadLittle<uint64_t>(p),
        .Seq = ReadLittle<uint64_t>(p + 8),
        .Locate = ReadLittle<uint16_t>(p + 16),
        .Body = {p + HeaderSize, length}};
    offset += HeaderSize + length;
    return packet;
}

std::vector<TBlock> SplitBlocks(const TRecording& recording)
{
    std::vector<TBlock> blocks;
    std::optional<TBlock> current;
    uint64_t previousSeq = 0;
    const auto close = [&blocks, &current](size_t end) {
        current->End = end;
        if (current->RealPackets > 0) {
            blocks.push_back(*current);
        }
        current.reset();
    };
    size_t offset = recording.DataBegin();
    for (size_t at = offset; const auto packet = recording.Next(offset); at = offset) {
        if (packet->Seq == itch::SnapshotSequence && current && previousSeq != itch::SnapshotSequence) {
            close(at);
        }
        if (!current) {
            current = TBlock{.Begin = at};
        }
        if (packet->Seq != itch::SnapshotSequence && packet->Seq != itch::EndOfBlock) {
            if (current->RealPackets++ == 0) {
                current->FirstNs = packet->TimeNs;
            }
            current->LastNs = packet->TimeNs;
        }
        previousSeq = packet->Seq;
        if (packet->Seq == itch::EndOfBlock) {
            current->HasEnd = true;
            close(offset);
        }
    }
    if (current) {
        close(offset);
    }
    return blocks;
}

} // namespace coldpath::feed
