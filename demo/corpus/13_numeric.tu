// 13_numeric.tu —— `demo/corpus/13_numeric.cpp` 的成本契约（模块 `13_numeric`）
//
// 用法：
//     dsh check demo/corpus/13_numeric.tu demo/corpus/13_numeric.cpp --compat
//     dsh cost  demo/corpus/13_numeric.tu demo/corpus/13_numeric.cpp --compat
//
// 这是本批语料的 **κ 对照组**：8 个函数全部算出具体式（一个 ⊤ 都没有），
// 全程标量循环、没有容器套容器。7 条 target 里 **6 条判达成**，`stddev_milli`
// 判 E23（原因见该 spec 上方——不是 κ 为 ⊤，是 target 里写不出内联进来的 `x`），
// `main` 不写 target（见文件末尾）。

spec prefix_sums(const vector<u64>& xs) -> vector<u64> {
    effects { alloc };
    // κ = 3 + 2 + n(xs)×25 + 25 + 2 → O(n(xs))。一趟前缀和。
    // 分配：静态模型记 0（返回值按移动记账），语义上是建 n(xs)+1 个元素的表，
    // 按宽的写 O(n(xs))。
    target { time: { max: O(xs.size()) }, allocation: { max: O(xs.size()) }, };
}

spec range_sum(const vector<u64>& pre, u64 lo, u64 hi) -> u64 {
    effects {};
    // κ = 8（两次下标 + 一次减法）→ O(1)。只读，不分配。
    target { time: { max: O(1) }, allocation: { max: 0 }, };
}

spec clamp_u64(u64 x, u64 lo, u64 hi) -> u64 {
    effects {};
    // 两次比较：κ = 14 → O(1)。三个参数都是标量，不涉及尺寸量。
    target { time: { max: O(1) }, allocation: { max: 0 }, };
}

spec sat_add(u64 a, u64 b, u64 cap) -> u64 {
    effects {};
    // κ = 14（一次减法 + 一次比较）→ O(1)。`cap - a` 是标量算术，不是尺寸量。
    target { time: { max: O(1) }, allocation: { max: 0 }, };
}

spec mean_milli(const vector<u64>& xs) -> u64 {
    effects {};
    // 一趟求和：κ = 13 + 2 + n(xs)×16 + 16 + 9 → O(n(xs))。不分配。
    target { time: { max: O(xs.size()) }, allocation: { max: 0 }, };
}

spec isqrt(u64 x) -> u64 {
    effects {};
    // κ = 4 + 2 + (x + 1)×33 + 33 + 2 → O(x)。循环上界写的是 `step <= x`（兜底），
    // 所以 κ 的阶是 x 的一次方；真实迭代次数是 log2(x)，target 取 κ 的阶（宁宽勿紧）。
    // `x` 是**标量形参**，target 里直接写 `x` 就绑得上。
    target { time: { max: O(x) }, allocation: { max: 0 }, };
}

spec stddev_milli(const vector<u64>& xs) -> u64 {
    effects {};
    // ⚠ 这条 target **目前判不了**（E23「尺寸量不一致」）：
    //   κ = … + n(xs)×16 + … + n(xs)×33 + … + (x + 1)×33 + …，其中 `x` 是**内联进来的
    //   isqrt 的形参**——`isqrt(acc / xs.size())` 的实参没能把形参代换掉，原样留成自由
    //   符号（同 03_matrix.matmul 的局部标量 `n`、11_text.split 的局部容器 `cur` 一类）。
    //   `x` 不是 stddev_milli 的形参，target 里没有名字可写 ⇒ 尺寸量集合 {x, xs} ≠ {xs}。
    //   写在这里的是 κ 的阶（n(xs) 那一半，也是算法真界）；用 `requires x <= xs.size()`
    //   硬把 x 弱化到 n(xs) 能骗过判定，但那句不等式在本语料是假的（x = 129987109375，
    //   n(xs) = 8），不写。
    target { time: { max: O(xs.size()) }, allocation: { max: 0 }, };
}

spec main() -> i64 {
    effects { io, alloc };
    // 不写 target：main 没有形参，κ 里却有 `(x + 1)`（两次内联 isqrt 留下的自由符号）。
    // 写 `O(1)` 会被 H3 判成"常数 target vs κ 含尺寸量"→ 假债务；写别的又对不上集合。
    // 驱动程序不承诺渐近界（输入是文件内字面量），只声明效果：
    // printf → io，8 次 push_back 建向量 → alloc。
    // 2026-11-13：区间减半循环有了对数/位宽上界后，这里的内联 isqrt 成了常数 ⇒ κ 折成常数 6001。
    target { time: { max: O(1) }, allocation: { max: O(8) }, };
}
