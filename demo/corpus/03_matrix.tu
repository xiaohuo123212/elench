// 03_matrix.tu —— `demo/corpus/03_matrix.cpp` 的成本契约（模块 `03_matrix`）
//
// 用法：
//     dsh check demo/corpus/03_matrix.tu demo/corpus/03_matrix.cpp --compat
//     dsh cost  demo/corpus/03_matrix.tu demo/corpus/03_matrix.cpp --compat
//
// `using Mat = std::vector<std::vector<u64>>` 在 `--compat` 下就是
// `vector<vector<u64>>`（一层嵌套容器，本语言支持）。
//
// 3 个函数全部有可判 target（2026-11-13 复测：`matmul` 的局部标量 `n` 不再
// 漏进 κ 的尺寸量，原来的 E23 已经消失；`main` 的 κ 退化成常数）。

spec zeros(u64 n) -> vector<vector<u64>> {
    effects { alloc };
    // κ = 2 + 1 + n + 2 → 线性；`n` 是**标量形参**，target 里写 `n` 就是那个量本身。
    // 分配写 O(n) 而不是 0：静态模型把这条记成 0（返回值按移动记），但
    // 建 n 行的矩阵语义上是 O(n) 次分配，契约宁可宽。
    target { time: { max: O(n) }, allocation: { max: O(n) }, };
}

spec matmul(const vector<vector<u64>>& a, const vector<vector<u64>>& b) -> vector<vector<u64>> {
    effects { alloc };
    // κ = … + n(a)×(… n(a)×(… n(a)×17 …) …) → 三重循环，O(n(a)³)。
    // `b` 没进 κ（只按 b[k][j] 取值），所以 target 里也不必出现 b——**少写一个
    // 尺寸量会退 E23**，但这里不是漏写，是 κ 里真的没有它。
    // 分配：静态模型记 0（返回值按移动记账），语义上是 n(a)² 个元素 → O(n(a)²)。
    target { time: { max: O(a.size() * a.size() * a.size()) }, allocation: { max: O(a.size() * a.size()) }, };
}

spec main() -> i64 {
    effects { io, alloc };
    // κ = 782 是**常数**（矩阵尺寸全是文件内字面量，没有尺寸量进 κ）⇒ 阶 O(1)。
    // 分配：κ = 0 ⇒ 不承诺分配（只写时间）。
    target { time: { max: O(1) }, };
}
