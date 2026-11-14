// 18_kahan.tu —— `demo/corpus/18_kahan.cpp` 的成本契约（模块 `18_kahan`）
//
// 用法：
//     dsh check demo/corpus/18_kahan.tu demo/corpus/18_kahan.cpp --compat
//     dsh cost  demo/corpus/18_kahan.tu demo/corpus/18_kahan.cpp --compat
//
// f64 密集循环：朴素和 / Kahan 补偿和 / 自底向上两两求和。口径与其余语料一致：
// target 取 κ 的阶、**宁宽勿紧**；每条都做过反向验收（收紧一档必须判 ▲ 债务）。

spec naive_sum(const vector<f64>& xs) -> f64 {
    effects {};
    // κ = 2 + 2 + n(xs)×16 + 16 + 2 → O(n(xs))：一趟从左到右累加，不分配。
    target { time: { max: O(xs.size()) }, allocation: { max: 0 }, };
}

spec kahan_sum(const vector<f64>& xs) -> f64 {
    effects {};
    // κ = 4 + 2 + n(xs)×33 + 33 + 2 → O(n(xs))：每轮 4 个 f64 运算，仍是线性。
    target { time: { max: O(xs.size()) }, allocation: { max: 0 }, };
}

spec pairwise_sum(const vector<f64>& xs) -> f64 {
    effects { alloc };
    // κ = … + n(xs)×19 + … + log2(n(xs))×(7 + 2 + n(xs)×33 + 33 + 5) + …
    //   → **Θ(n(xs)·log2 n(xs))**：外层步长每轮翻倍（log2 轮），每轮扫一遍数组。
    // 尺寸量只有 `n(xs)` 一个；`log2(...)` 在 target 里认（与 02_csv_stats 的
    // `log2(n(lines))` 同一套词汇）。收紧到 O(xs.size()) 判真债务。
    // 分配：κ_alloc = n(xs)×1（先复制一份 a）→ O(n(xs))。
    target { time: { max: O(xs.size() * log2(xs.size())) },
             allocation: { max: O(xs.size()) }, };
}

spec mean_of(const vector<f64>& xs) -> f64 {
    effects {};
    // κ = 5 + … + 2 + n(xs)×33 + 33 + 2 + 1 + 1 → O(n(xs))（内部调 kahan_sum，
    // 一次线性求和）。只读，不分配。
    target { time: { max: O(xs.size()) }, allocation: { max: 0 }, };
}

spec rel_error(f64 got, f64 want) -> f64 {
    effects {};
    // κ = 18（常数：两次减法 + 除法）→ O(1)，不分配。
    target { time: { max: O(1) }, allocation: { max: 0 }, };
}

spec spread(const vector<f64>& xs) -> f64 {
    effects {};
    // κ = 8 + 2 + n(xs)×34 + 34 + 4 → O(n(xs))：一趟同时求 min / max。
    target { time: { max: O(xs.size()) }, allocation: { max: 0 }, };
}

spec rescale(const vector<f64>& xs, f64 k) -> vector<f64> {
    effects { alloc };
    // κ = 3 + 2 + n(xs)×21 + 21 + 2 → O(n(xs))。尺寸量只有 `n(xs)`——系数 `k`
    // 是标量、不进尺寸量。分配：κ_alloc = n(xs)×1 → O(n(xs))。
    target { time: { max: O(xs.size()) }, allocation: { max: O(xs.size()) }, };
}

spec main() -> i64 {
    effects { io, alloc };
    // κ 是**常数**（采样表全是文件内字面量：1e16 + 100 + 100 与 1e16 + 1000，
    // 连 `log2(202)` / `log2(1001)` 都折成了常数）⇒ O(1)。
    // 分配：κ_alloc = 2608 也是字面常数，而 allocation 维度按**数量**比较——
    // 写 `O(1)` 会折成 1、2608 > 1 报假债务，写 2608 只是抄测量值。
    // 所以只承诺时间（与 12_graph / 15_unionfind / 16_bitset 的 main 同口径）。
    target { time: { max: O(1) }, };
}
