#pragma once

#include <cstdio>
#include <format>
#include <string>
#include <utility>

namespace coldpath {

template <class... TValues>
void Print(std::FILE* out, std::format_string<TValues...> format, TValues&&... values)
{
    const std::string text = std::format(format, std::forward<TValues>(values)...);
    std::fwrite(text.data(), 1, text.size(), out);
    std::fflush(out);
}

template <class... TValues>
void Println(std::format_string<TValues...> format, TValues&&... values)
{
    Print(stdout, "{}\n", std::format(format, std::forward<TValues>(values)...));
}

template <class... TValues>
void Eprintln(std::format_string<TValues...> format, TValues&&... values)
{
    Print(stderr, "{}\n", std::format(format, std::forward<TValues>(values)...));
}

} // namespace coldpath
