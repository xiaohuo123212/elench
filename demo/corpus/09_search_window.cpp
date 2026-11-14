// 09_search_window.cpp —— 二分查找 + 滑动窗口：在排好序的时间戳里找第一个
// 不早于查询点的位置，再找一段「总耗时不超过预算」的最长连续任务。
// 真实场景：日志检索 / 限流窗口。返回 xs.size() 当哨兵，表示「没有这样的位置」。
#include <cstdio>
#include <vector>

using u64 = unsigned long long;

// 手写 lower_bound：返回第一个 >= target 的下标。
static u64 lower_bound_index(const std::vector<u64>& xs, u64 target) {
    u64 lo = 0;
    u64 hi = xs.size();
    // 二分最多 log2(n)+1 轮；这里用 n 兜底，保证循环有可写的上界。
    for (u64 step = 0; step < xs.size(); ++step) {
        if (lo >= hi) break;
        u64 mid = lo + (hi - lo) / 2;
        if (xs[mid] < target) {
            lo = mid + 1;
        } else {
            hi = mid;
        }
    }
    return lo;
}

// 滑动窗口：和不超过 limit 的最长连续区间长度（元素非负）。
static u64 longest_window(const std::vector<u64>& xs, u64 limit) {
    u64 left = 0;
    u64 sum = 0;
    u64 best = 0;
    for (u64 right = 0; right < xs.size(); ++right) {
        sum += xs[right];
        // left 最多右移 n 次，用显式计数让「窗口收缩」也有界。
        for (u64 guard = 0; guard < xs.size(); ++guard) {
            if (left > right) break;
            if (sum <= limit) break;
            sum -= xs[left];
            left += 1;
        }
        u64 len = right - left + 1;
        if (len > best) best = len;
    }
    return best;
}

// 在有序表里数一数有多少个元素 >= target（二分结果的另一种用法）。
static u64 count_at_least(const std::vector<u64>& xs, u64 target) {
    u64 pos = lower_bound_index(xs, target);
    if (pos >= xs.size()) return 0;
    return xs.size() - pos;
}

int main() {
    std::vector<u64> stamps;
    stamps.push_back(2);
    stamps.push_back(4);
    stamps.push_back(4);
    stamps.push_back(7);
    stamps.push_back(9);
    stamps.push_back(15);
    stamps.push_back(15);
    stamps.push_back(21);

    std::vector<u64> queries;
    queries.push_back(1);
    queries.push_back(4);
    queries.push_back(8);
    queries.push_back(22);
    for (auto q : queries) {
        u64 pos = lower_bound_index(stamps, q);
        std::printf("first >= %llu at %llu\n", q, pos);
    }
    std::printf("count >= 8: %llu\n", count_at_least(stamps, 8));

    std::vector<u64> costs;
    costs.push_back(3);
    costs.push_back(1);
    costs.push_back(4);
    costs.push_back(1);
    costs.push_back(5);
    costs.push_back(9);
    costs.push_back(2);
    costs.push_back(6);
    std::printf("longest window <= 10: %llu\n", longest_window(costs, 10));
    return 0;
}
