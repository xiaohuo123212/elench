// merge.tu —— 区间合并（十个手写程序之五）的声明部分
//
// 考的三样本事：
//   1. `Vec<struct>` —— 结构体数组（现有三个样例都没有）
//   2. 原地写 `xs[i] = Interval { ... }` —— 排序必须原地动数组
//   3. 边界：相邻 / 重叠 / 完全包含 / 单元素 / 空
//
// **没有 `?!`，也没有 benchmodel**：
//   内置生成器只能造**整数序列**，造不出 `Vec<Interval>`
//   （见设计缺口 E14）。没有数据就测不了，所以这里只写 `target`。
//   这是诚实的降级：有 `?!` 而无 benchmodel 会被 MB-10 直接判失败。

// #region spec
error MergeError;

struct Interval { start: u64, finish: u64, } // 比较器的 spec：对应 .eln 里的 `func earlier`。
// `sort_by` 要求比较器具名，而具名函数在本语言里 MUST 有 spec（F-1）。
spec earlier(Interval a, Interval b) -> bool {
    effects { };
}

spec merge(Vec<Interval>& xs) -> Vec<Interval>! {
    effects { alloc };

    target { time: { max: O(xs.length() * xs.length()) }, allocation: { max: O(xs.length()) }, };
}
// #endregion spec
