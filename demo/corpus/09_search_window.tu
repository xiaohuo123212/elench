// 09_search_window.tu —— `demo/corpus/09_search_window.cpp` 的成本契约（模块 `09_search_window`）
//
// 用法：
//     dsh check demo/corpus/09_search_window.tu demo/corpus/09_search_window.cpp --compat
//     dsh cost  demo/corpus/09_search_window.tu demo/corpus/09_search_window.cpp --compat
//
// 这份语料的两处"手写循环"都用了**显式计数守卫**（`for (step = 0; step < xs.size(); ++step)`
// 加 `break`），所以 κ 是 O(n) / O(n²) 而不是 O(log n) / O(n)：
// 递减或"谓词驱动"的循环推不出上界，作者用 n 圈兜底换"可静态推界"。

spec lower_bound_index(const vector<u64>& xs, u64 target) -> u64 {
    effects {};
    // κ = 7 + 2 + n(xs)×36 + 36 + 2 → 线性；不分配。
    // 注：真二分是 O(log n)，但**这条实现的循环上界是 n**，契约按 κ 写（宁宽勿紧）。
    //     要收紧到 O(log n)，得先让 while 的上界能由 `repeats` 声明（不在本轮范围）。
    target { time: { max: O(xs.size()) }, allocation: { max: 0 }, };
}

spec longest_window(const vector<u64>& xs, u64 limit) -> u64 {
    effects {};
    // κ = 6 + 2 + n(xs)×(… n(xs)×26 …) + … → 外层 n × 内层 n = O(n²)；不分配。
    target { time: { max: O(xs.size() * xs.size()) }, allocation: { max: 0 }, };
}

spec count_at_least(const vector<u64>& xs, u64 target) -> u64 {
    effects {};
    // κ = 1 + 1 + 7 + 2 + n(xs)×36 + 36 + 2 + 2 + 9 + 7 → 线性（与非内联的
    // lower_bound_index 同阶，这里 κ 是被内联进来算的）。
    target { time: { max: O(xs.size()) }, allocation: { max: 0 }, };
}

spec main() -> i64 {
    effects { io, alloc };
    // 驱动程序：κ = 4263（常数，输入是文件内字面量）→ O(1)。
    // 分配写 20（实测 κ_alloc = 20，常数按**数值**比，`O(1)` 会被判债务）。
    target { time: { max: O(1) }, allocation: { max: 20 }, };
}
