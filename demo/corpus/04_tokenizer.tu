// 04_tokenizer.tu —— `demo/corpus/04_tokenizer.cpp` 的成本契约（模块 `04_tokenizer`）
//
// 用法：
//     dsh check demo/corpus/04_tokenizer.tu demo/corpus/04_tokenizer.cpp --compat
//     dsh cost  demo/corpus/04_tokenizer.tu demo/corpus/04_tokenizer.cpp --compat
//
// 3 个函数里 2 个有判决（count_digits / main）。`words` 的 κ 是具体的，但它的
// 尺寸量里有局部容器 `cur`，target 写不出来（见该 spec 上方）。

spec count_digits(const vector<u8>& s) -> i64 {
    effects {};
    // 一次 range-for：κ = 2 + n(s)×8 + 2 → 线性；不分配。
    target { time: { max: O(s.size()) }, allocation: { max: 0 }, };
}

spec words(const vector<u8>& s) -> vector<vector<u8>> {
    effects { alloc };
    // ⚠ 这条 target **判不了**（E23）：κ 里有局部容器 `cur` 的尺寸量
    //   （`n(cur)`），target 只能引用形参 ⇒ 尺寸量集合差一个 `cur`。
    //   写的是 κ 的阶（n(s) × n(cur)，同规模假设下 O(n²)），不是算法紧界
    //   （每个字符只被搬一次，真实 O(n(s)))。
    target { time: { max: O(s.size() * s.size()) }, allocation: { max: O(s.size()) }, };
}

spec main() -> i64 {
    effects { io, alloc };
    // κ = 525 是**常数**（输入是文件内字面量；`words` 的局部量 n(cur) 不再漏进
    // 调用点——那条旧说明已过时）⇒ 阶 O(1)。分配：κ = 0，这里只承诺时间。
    target { time: { max: O(1) }, };
}
