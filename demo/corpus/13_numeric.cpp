// 13_numeric.cpp —— 定点数值摘要：前缀和 + 区间和、均值与标准差（千分位整数）、
// 夹取、饱和加法。全程标量循环，没有容器套容器。
// 真实场景：一批毫伏读数进来后要立刻给出「平均 / 抖动 / 越界计数」，
// 用整数定点而不是浮点，结果在任何机器上都逐位相同。
#include <cstdio>
#include <vector>

using u64 = unsigned long long;

// 前缀和：pre[i] = 前 i 个读数之和，pre.size() == xs.size() + 1。
static std::vector<u64> prefix_sums(const std::vector<u64>& xs) {
    std::vector<u64> pre(xs.size() + 1, 0);
    for (std::size_t i = 0; i < xs.size(); ++i) pre[i + 1] = pre[i] + xs[i];
    return pre;
}

// 半开区间 [lo, hi) 的和，O(1)；调用方保证 lo <= hi <= n。
static u64 range_sum(const std::vector<u64>& pre, std::size_t lo, std::size_t hi) {
    return pre[hi] - pre[lo];
}

// 夹取：报警阈值、绘图坐标都要先把读数压进窗口。
static u64 clamp_u64(u64 x, u64 lo, u64 hi) {
    if (x < lo) return lo;
    if (x > hi) return hi;
    return x;
}

// 饱和加法：撞到上界就停在 cap，不回绕（计数器最怕回绕成一个小数）。
// 调用方保证 a 已经被夹到 cap 以内，所以 cap - a 不会下溢。
static u64 sat_add(u64 a, u64 b, u64 cap) {
    u64 room = cap - a;
    if (b > room) return cap;
    return a + b;
}

// 均值，放大 kMilli 倍（千分位）后取整（截断）。
static u64 mean_milli(const std::vector<u64>& xs) {
    const u64 kMilli = 1000;   // 定点单位：1 表示 1/1000
    if (xs.size() == 0) return 0;
    u64 sum = 0;
    for (std::size_t i = 0; i < xs.size(); ++i) sum += xs[i];
    return sum * kMilli / xs.size();
}

// 整数平方根（二分）：均方差要开方，浮点 sqrt 会把「逐位相同」弄丢。
static u64 isqrt(u64 x) {
    u64 lo = 0;
    u64 hi = x;
    // lo < hi 每轮至少缩小一半，所以循环次数是 log2(x)+1；用 x+1 兜底。
    for (u64 step = 0; step <= x; ++step) {
        if (lo >= hi) break;
        u64 mid = lo + (hi - lo) / 2;
        if (mid * mid < x) lo = mid + 1;
        else hi = mid;
    }
    return lo;
}

// 标准差（定点，千分位）：先算均值，再累加偏差平方，最后开方。
// 本文件的读数都在 10^4 以内、条数很少，平方和不会溢出 u64。
static u64 stddev_milli(const std::vector<u64>& xs) {
    const u64 kMilli = 1000;
    if (xs.size() == 0) return 0;
    u64 mean = mean_milli(xs);
    u64 acc = 0;
    for (std::size_t i = 0; i < xs.size(); ++i) {
        u64 v = xs[i] * kMilli;
        u64 d = 0;
        if (v > mean) d = v - mean;
        else d = mean - v;
        acc += d * d;
    }
    return isqrt(acc / xs.size());
}

int main() {
    const u64 kCounterCap = 4294967295;   // 32 位计数器的饱和上界
    std::vector<u64> xs;
    xs.push_back(120);
    xs.push_back(340);
    xs.push_back(355);
    xs.push_back(400);
    xs.push_back(420);
    xs.push_back(900);
    xs.push_back(1000);
    xs.push_back(1200);

    std::vector<u64> pre = prefix_sums(xs);
    std::printf("total: %llu\n", pre[pre.size() - 1]);
    std::printf("sum[2,5): %llu\n", range_sum(pre, 2, 5));
    std::printf("sum[5,8): %llu\n", range_sum(pre, 5, 8));
    std::printf("mean_milli: %llu\n", mean_milli(xs));
    std::printf("stddev_milli: %llu\n", stddev_milli(xs));

    // 把读数夹到 [100, 800]，再数一数有多少个被压过、多少个顶到报警线。
    u64 clamped = 0;
    u64 alarm = 0;
    for (std::size_t i = 0; i < xs.size(); ++i) {
        u64 v = clamp_u64(xs[i], 100, 800);
        if (v != xs[i]) clamped += 1;
        if (v >= 800) alarm = sat_add(alarm, 1, kCounterCap);
    }
    std::printf("clamped: %llu\n", clamped);
    std::printf("alarms: %llu\n", alarm);

    // 饱和加法：计数器快到上界时再加，必须停在 cap 而不是回绕。
    std::printf("sat: %llu\n", sat_add(kCounterCap - 2, 5, kCounterCap));
    std::printf("isqrt(144): %llu\n", isqrt(144));
    return 0;
}
