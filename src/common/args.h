#pragma once

#include <charconv>
#include <cstdint>
#include <map>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace coldpath {

struct TOptionNames
{
    std::vector<std::string_view> Values;
    std::vector<std::string_view> Flags;
};

class TArgs
{
public:
    TArgs(int argc, char** argv, const TOptionNames& names);

    bool Flag(std::string_view name) const;
    std::optional<std::string_view> Get(std::string_view name) const;
    std::string Text(std::string_view name, std::string_view fallback) const;
    int64_t Int(std::string_view name, int64_t fallback) const;
    double Double(std::string_view name, double fallback) const;
    const std::vector<std::string_view>& Positional() const;

private:
    std::map<std::string, std::string, std::less<>> Values_;
    std::vector<std::string_view> Positional_;
};

std::vector<std::string_view> SplitList(std::string_view text, char separator = ',');

template <class T>
T ParseNumber(std::string_view text)
{
    T value{};
    const auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), value);
    if (error != std::errc{} || end != text.data() + text.size()) {
        throw std::invalid_argument("not a number: " + std::string(text));
    }
    return value;
}

} // namespace coldpath
