#include "recv/journal.h"

#include <cerrno>
#include <format>
#include <system_error>

namespace coldpath::recv {

TJournal::TJournal(const std::string& path, size_t capacity)
    : Out_(OpenFile(path, "wb"))
    , Hashes_(OpenFile(path + ".hash", "w"))
    , Records_(capacity)
{
    if (!Out_ || !Hashes_) {
        throw std::system_error(errno, std::generic_category(), path);
    }
}

void TJournal::Flush()
{
    std::fwrite(Records_.data(), sizeof(TPacketRecord), Size_, Out_.get());
    std::fflush(Out_.get());
    Total_ += Size_;
    Size_ = 0;
}

void TJournal::WriteHash(uint64_t seq, uint64_t hash)
{
    std::fputs(std::format("{} {:016x}\n", seq, hash).c_str(), Hashes_.get());
}

void TJournal::Finish(uint64_t lastSeq, std::optional<uint64_t> hash)
{
    Flush();
    if (hash) {
        std::fputs(std::format("end {} {:016x}\n", lastSeq, *hash).c_str(), Hashes_.get());
    }
    Out_.reset();
    Hashes_.reset();
}

} // namespace coldpath::recv
