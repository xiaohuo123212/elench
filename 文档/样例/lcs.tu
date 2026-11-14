// lcs.tu —— 最长公共子序列（十个手写程序之十）的声明部分
//
// 考的四样本事：
//   1. 二维 DP 表用「扁平序列 + 宽度」表示（同 matmul），但这里要**原地写**：
//      `dp[(i + 1) * width + (j + 1)] = v`
//   2. 两层嵌套循环——上界分别是 `a.size()` 与 `b.size()`，
//      都是容器参数的尺寸，T13 来源①认得出
//   3. `array<u64, LCS_CAP>` 的容量是**真边界**：表格装不下就 Err(Capacity)，
//      不许静默丢格子
//   4. 只有 target、没有 ?!：LCS 没有"另一条路"值得测，
//      所以**不写 benchmodel**（有 benchmodel 而无 ?! 会被 W-Bench-Unused 警告）

// #region spec
error LcsError;

// 固定表容量。两条 63 字节的串要 (63+1)×(63+1) = 4096 格，刚好装满；
// 两条 64 字节的串要 65×65 = 4225 格，装不下 → 返回 Err(LcsError::Capacity)，
// 而不是让 `push` 抛容量异常（那条边界已在解释器里实测过）。
const LCS_CAP: u64 = 4096;

spec lcs(
         const vector<u8>& a,
             const vector<u8>& b) -> u64! {
    effects { alloc };
    requires LCS_CAP > 0;

    // 两层循环各跑一次长度 → 格数 A×B，真实界是 **O(a.size() * b.size())**。
    // v0.69+：target 支持两个尺寸相乘（`_target_cost` 的 `*`），两边尺寸量一致，能直接比。
    // 容量上界不靠 target——由 LCS_CAP 在实现里显式挡住。
    target { time: { max: O(a.size() * b.size()) },
             allocation: { max: O(a.size()) }, };
}
// #endregion spec
