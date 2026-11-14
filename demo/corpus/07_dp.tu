// 07_dp.tu —— `demo/corpus/07_dp.cpp` 的成本契约（模块 `07_dp`）
//
// 用法：
//     dsh check demo/corpus/07_dp.tu demo/corpus/07_dp.cpp --compat
//     dsh cost  demo/corpus/07_dp.tu demo/corpus/07_dp.cpp --compat
//
// 两张 (n+1)×(m+1) 的表在体内建成局部 `vector<vector<u64>> dp`：以前它的尺寸量
// `n(dp)` 会留在 κ 里、把 target 逼成 E23；现在 κ 只用 `n(a)` / `n(b)` 写
// （局部表按形参尺寸归并），所以三条 target 全部判达成。
//
// 4 个函数**全部有判决**（2026-11-13 复测）。

spec min3(u64 a, u64 b, u64 c) -> u64 {
    effects {};
    // 两次比较：κ = 18（常数）→ O(1)，不分配。
    target { time: { max: O(1) }, allocation: { max: 0 }, };
}

spec edit_distance(const vector<u8>& a, const vector<u8>& b) -> u64 {
    effects { alloc };
    // κ = 10 + … + (n(a) + 1)×(… (n(b) + 1)×86 …) + … → 双层循环，
    // O(n(a) × n(b))（也是算法真界）。target 必须**两个尺寸量都写**——
    // 少写一个就退 E23。
    // 分配：静态模型记 0（表按返回值/移动记账），语义上是 Θ(n(a)×n(b))，按宽的写。
    target { time: { max: O(a.size() * b.size()) }, allocation: { max: O(a.size() * b.size()) }, };
}

spec lcs_length(const vector<u8>& a, const vector<u8>& b) -> u64 {
    effects { alloc };
    // 同 edit_distance：κ 的阶 = O(n(a) × n(b))。
    target { time: { max: O(a.size() * b.size()) }, allocation: { max: O(a.size() * b.size()) }, };
}

spec main() -> i64 {
    effects { io, alloc };
    // κ = 13581 是**常数**（两个串都是文件内字面量，局部表 n(dp) 不进 κ）⇒ O(1)。
    // 分配：κ = 0，这里只承诺时间。
    target { time: { max: O(1) }, };
}
