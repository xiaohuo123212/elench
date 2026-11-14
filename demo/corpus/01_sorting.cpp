// 01_sorting.cpp —— 插入排序 + 归并排序（最常见的 C++ 内核形状）
#include <cstdio>
#include <vector>
#include <cstddef>

using u64 = unsigned long long;

static void insertion_sort(std::vector<u64>& xs) {
    for (std::size_t i = 1; i < xs.size(); ++i) {
        u64 key = xs[i];
        std::size_t j = i;
        while (j > 0 && xs[j - 1] > key) {
            xs[j] = xs[j - 1];
            --j;
        }
        xs[j] = key;
    }
}

static void merge_halves(std::vector<u64>& xs, std::size_t lo, std::size_t mid, std::size_t hi) {
    std::vector<u64> tmp;
    std::size_t i = lo, j = mid;
    while (i < mid && j < hi) {
        if (xs[i] <= xs[j]) { tmp.push_back(xs[i]); ++i; }
        else { tmp.push_back(xs[j]); ++j; }
    }
    while (i < mid) { tmp.push_back(xs[i]); ++i; }
    while (j < hi) { tmp.push_back(xs[j]); ++j; }
    for (std::size_t k = 0; k < tmp.size(); ++k) xs[lo + k] = tmp[k];
}

static void merge_sort(std::vector<u64>& xs, std::size_t lo, std::size_t hi) {
    if (hi - lo < 2) return;
    std::size_t mid = lo + (hi - lo) / 2;
    merge_sort(xs, lo, mid);
    merge_sort(xs, mid, hi);
    merge_halves(xs, lo, mid, hi);
}

int main() {
    std::vector<u64> xs{5, 3, 9, 1, 7, 3};
    insertion_sort(xs);
    merge_sort(xs, 0, xs.size());
    for (std::size_t i = 0; i < xs.size(); ++i) std::printf("%llu ", xs[i]);
    std::printf("\n");
    return 0;
}
