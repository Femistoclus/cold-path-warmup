#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <vector>

// Order table of the synthetic model and of the load model: one row per cache line, rows chosen by an LCG.
namespace coldpath::handler {

struct alignas(64) TRow
{
    uint64_t Id = 0;
    int64_t Quantity = 0;
    uint32_t Price = 0;
    uint8_t Side = 0;
};
static_assert(sizeof(TRow) == 64);

struct TWalk
{
    int64_t Bid = 0;
    int64_t Ask = 0;
    unsigned Big = 0;
};

template <unsigned Log2>
class TTable
{
public:
    static constexpr size_t Size = size_t{1} << Log2;

    void Fill(unsigned seed)
    {
        std::srand(seed);
        for (size_t i = 0; i < Size; ++i) {
            Rows_[i] = {.Id = i,
                .Quantity = std::rand() % 1000,
                .Price = 10000 + static_cast<uint32_t>(std::rand() % 200),
                .Side = static_cast<uint8_t>(std::rand() % 2)};
        }
    }

    // With Chain, the next row depends on the value read: the loads cannot overlap.
    template <int Lines, bool Chain>
    TWalk Walk(uint64_t key) const
    {
        TWalk walk;
        uint64_t h = key;
        for (int i = 0; i < Lines; ++i) {
            h = Step(h);
            const TRow& row = Rows_[h >> (64 - Log2)];
            // The empty asm keeps this a real conditional branch instead of cmov.
            if (row.Side == 0) {
                walk.Bid += row.Quantity;
                __asm__ volatile("");
            } else {
                walk.Ask += row.Quantity;
            }
            if (row.Quantity > 900) {
                ++walk.Big;
            }
            if constexpr (Chain) {
                h += static_cast<uint64_t>(row.Quantity);
            }
        }
        return walk;
    }

    // Distinct rows of Walk in visit order.
    template <int Lines, bool Chain>
    std::vector<const void*> Rows(uint64_t key) const
    {
        std::vector<const void*> out;
        std::vector<bool> seen(Size);
        uint64_t h = key;
        for (int i = 0; i < Lines; ++i) {
            h = Step(h);
            const size_t index = h >> (64 - Log2);
            if (!seen[index]) {
                seen[index] = true;
                out.push_back(&Rows_[index]);
            }
            if constexpr (Chain) {
                h += static_cast<uint64_t>(Rows_[index].Quantity);
            }
        }
        return out;
    }

private:
    std::array<TRow, Size> Rows_;

    static uint64_t Step(uint64_t h)
    {
        return h * 6364136223846793005ULL + 1442695040888963407ULL;
    }
};

} // namespace coldpath::handler
