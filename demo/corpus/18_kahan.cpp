// 18_kahan.cpp —— 浮点累计的数值稳定性：同一批采样用三种方式求和，比较相对误差。
// 真实场景：采集卡把「大冲击 + 很多小台阶」的电压采样交给上层，朴素累加会把小项
// 直接吃掉；这里用 Kahan 补偿求和、两两求和各算一遍，再报误差和均值。
// 全程 f64，输出用 %f（定点 6 位），同一台机器上逐位可复现。
#include <cmath>
#include <cstdio>
#include <vector>

using f64 = double;
using u64 = unsigned long long;

// 朴素求和：从左到右一路加。量级差得远时，小项会被大项"吃掉"。
static f64 naive_sum(const std::vector<f64>& xs) {
    f64 sum = 0.0;
    for (std::size_t i = 0; i < xs.size(); ++i) sum += xs[i];
    return sum;
}

// Kahan 补偿求和：把每次加法丢掉的低位留在 c 里，下一轮再补回去。
static f64 kahan_sum(const std::vector<f64>& xs) {
    f64 sum = 0.0;
    f64 c = 0.0;
    for (std::size_t i = 0; i < xs.size(); ++i) {
        f64 y = xs[i] - c;
        f64 t = sum + y;
        c = (t - sum) - y;
        sum = t;
    }
    return sum;
}

// 两两求和（自底向上）：相邻两个先加，步长每轮翻倍，像锦标赛一样归约。
// 递归写法在这里会被成本工具判成环，所以写成同序的循环版本，结果与递归版只差舍入。
static f64 pairwise_sum(const std::vector<f64>& xs) {
    std::vector<f64> a;
    for (std::size_t i = 0; i < xs.size(); ++i) a.push_back(xs[i]);
    for (std::size_t stride = 1; stride < a.size(); stride = stride * 2) {
        for (std::size_t i = 0; i < a.size(); i += stride * 2) {
            if (i + stride < a.size()) a[i] = a[i] + a[i + stride];
        }
    }
    return a[0];
}

// 均值：用 Kahan 的和，避免"合计对、均值差一点"。
static f64 mean_of(const std::vector<f64>& xs) {
    f64 n = xs.size();
    return kahan_sum(xs) / n;
}

// 相对误差：|got - want| / |want|；want 为 0 时退化成绝对误差。
static f64 rel_error(f64 got, f64 want) {
    f64 d = std::abs(got - want);
    if (want == 0.0) return d;
    return d / std::abs(want);
}

// 极差：这一批采样的最大最小值之差（传感器看到的动态范围）。
static f64 spread(const std::vector<f64>& xs) {
    f64 lo = xs[0];
    f64 hi = xs[0];
    for (std::size_t i = 1; i < xs.size(); ++i) {
        if (xs[i] < lo) lo = xs[i];
        if (xs[i] > hi) hi = xs[i];
    }
    return hi - lo;
}

// 线性标定：把每个采样乘一个系数；结果逐位与手写循环相同。
static std::vector<f64> rescale(const std::vector<f64>& xs, f64 k) {
    std::vector<f64> out;
    for (std::size_t i = 0; i < xs.size(); ++i) out.push_back(xs[i] * k);
    return out;
}

int main() {
    // 一批电压采样：中间一记 1e16 的大冲击，前后各一百个 1.0 的小台阶。
    // 精确和 = 1e16 + 100 - 1e16 + 100 = 200，可以拿来当参照。
    const f64 kSpike = 1e16;
    std::vector<f64> xs;
    xs.push_back(kSpike);
    for (u64 i = 0; i < 100; ++i) xs.push_back(1.0);
    xs.push_back(-kSpike);
    for (u64 i = 0; i < 100; ++i) xs.push_back(1.0);

    const f64 kExact = 200.0;
    f64 naive = naive_sum(xs);
    f64 kahan = kahan_sum(xs);
    f64 pair = pairwise_sum(xs);

    std::printf("n %llu exact %f\n", xs.size(), kExact);
    std::printf("naive %f err %f\n", naive, rel_error(naive, kExact));
    std::printf("kahan %f err %f\n", kahan, rel_error(kahan, kExact));
    std::printf("pairwise %f err %f\n", pair, rel_error(pair, kExact));
    std::printf("mean %f\n", mean_of(xs));

    std::vector<f64> scaled = rescale(xs, 0.5);
    std::printf("scaled[0] %f scaled[last] %f\n", scaled[0], scaled[scaled.size() - 1]);
    std::printf("spread %f\n", spread(xs));

    // 两两求和真正擅长的形状：全是同号小量，没有抵消。
    // 精确和 = 1e16 + 1000（1000 是偶数，在 1e16 的间隔 2 上仍可精确表示）。
    std::vector<f64> same_sign;
    same_sign.push_back(kSpike);
    for (u64 i = 0; i < 1000; ++i) same_sign.push_back(1.0);
    f64 s1 = naive_sum(same_sign);
    f64 s2 = pairwise_sum(same_sign);
    f64 same_exact = kSpike + 1000.0;
    std::printf("same-sign naive lost %f pairwise lost %f\n",
                same_exact - s1, same_exact - s2);
    return 0;
}
