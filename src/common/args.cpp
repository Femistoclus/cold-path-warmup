#include "common/args.h"

#include <algorithm>
#include <span>

namespace coldpath {

TArgs::TArgs(int argc, char** argv, const TOptionNames& names)
{
    const auto known = [](const std::vector<std::string_view>& list, std::string_view name) {
        return std::ranges::find(list, name) != list.end();
    };
    const std::span<char*> args(argv + 1, static_cast<size_t>(argc > 0 ? argc - 1 : 0));
    for (size_t i = 0; i < args.size(); ++i) {
        const std::string_view arg = args[i];
        if (!arg.starts_with("--")) {
            Positional_.push_back(arg);
            continue;
        }
        std::string_view name = arg.substr(2);
        std::optional<std::string_view> value;
        if (const size_t eq = name.find('='); eq != std::string_view::npos) {
            value = name.substr(eq + 1);
            name = name.substr(0, eq);
        }
        if (known(names.Flags, name) && !value) {
            Values_[std::string(name)] = "1";
        } else if (known(names.Values, name)) {
            if (!value) {
                if (i + 1 >= args.size()) {
                    throw std::invalid_argument("missing value for --" + std::string(name));
                }
                value = args[++i];
            }
            Values_[std::string(name)] = std::string(*value);
        } else {
            throw std::invalid_argument("unknown option --" + std::string(name));
        }
    }
}

bool TArgs::Flag(std::string_view name) const
{
    return Values_.contains(name);
}

std::optional<std::string_view> TArgs::Get(std::string_view name) const
{
    const auto it = Values_.find(name);
    if (it == Values_.end()) {
        return std::nullopt;
    }
    return it->second;
}

std::string TArgs::Text(std::string_view name, std::string_view fallback) const
{
    return std::string(Get(name).value_or(fallback));
}

int64_t TArgs::Int(std::string_view name, int64_t fallback) const
{
    const auto value = Get(name);
    return value ? ParseNumber<int64_t>(*value) : fallback;
}

double TArgs::Double(std::string_view name, double fallback) const
{
    const auto value = Get(name);
    return value ? ParseNumber<double>(*value) : fallback;
}

const std::vector<std::string_view>& TArgs::Positional() const
{
    return Positional_;
}

std::vector<std::string_view> SplitList(std::string_view text, char separator)
{
    std::vector<std::string_view> items;
    while (!text.empty()) {
        const size_t end = text.find(separator);
        if (end != 0) {
            items.push_back(text.substr(0, end));
        }
        if (end == std::string_view::npos) {
            break;
        }
        text.remove_prefix(end + 1);
    }
    return items;
}

} // namespace coldpath
