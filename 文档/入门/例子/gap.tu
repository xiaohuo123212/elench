// 「区分度大」的分段例子：两条成本线在两端相差一个数量级以上。
//   by_scan_heavy —— 无固定开销，但每个元素做 30 次 max（每元素 ≈500 操作）
//   by_scan_setup —— 先 20000 次迭代准备（≈34 万操作），之后每元素 1 次 max（≈17）
// 交叉点在 n≈700：小 n 用 heavy（省掉 34 万准备），大 n 用 setup（斜率小 30 倍）。
error GapError;

spec top(const vector<u64>& xs) -> u64! {
    effects {};
    benchmodel {
        data { xs = random_u64s(); }
        scale { xs in range(8, 65536, *2); }
    };
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
    choose kernel(const vector<u64>& xs) -> u64! {
        over { by_scan_heavy, by_scan_setup };
        objective time;
    };
}
