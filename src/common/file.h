#pragma once

#include <cstdio>
#include <memory>
#include <string>

namespace coldpath {

struct TFileCloser
{
    void operator()(std::FILE* file) const
    {
        std::fclose(file);
    }
};

using TFilePtr = std::unique_ptr<std::FILE, TFileCloser>;

inline TFilePtr OpenFile(const std::string& path, const char* mode)
{
    return TFilePtr(std::fopen(path.c_str(), mode));
}

} // namespace coldpath
