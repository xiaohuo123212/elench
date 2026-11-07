// 静态分析就能分段的例子。
// 两个候选**同阶**（都 O(n)），但一个「小 n 赢」、一个「大 n 赢」：
//   by_scan_heavy —— 没有固定开销，每个元素做 6 次 max（斜率大）
//   by_scan_setup —— 先花 3000 次迭代做准备（固定开销大），之后每个元素 1 次 max
// 两条成本线在 n≈2200 处相交，`elench dispatch crossover` 不用实测就能给出两段。
error CrossError;

spec top(const vector<u64>& xs) -> u64! {
    effects {};
    benchmodel {
        data { xs = random_u64s(); }
        scale { xs in range(1, 4096, *2); }
    };
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
    choose kernel(const vector<u64>& xs) -> u64! {
        over { by_scan_heavy, by_scan_setup };
        objective time;
    };
}
