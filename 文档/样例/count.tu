// count.tu —— 词频统计（十个手写程序之七）的声明部分
//
// 考的四样本事：
//   1. `vector<u8>` 逐字节扫描 + 内层循环
//   2. `array<u64, 256>` 当**桶数组**，`buckets[b] = buckets[b] + 1` 原地写
//   3. 两个候选**有真实交叉点**：桶法有 256 次固定初始化开销，
//      小规模时两两比较反而更快 → 这正是分档表要解决的问题
//   4. target 故意写得宽（O(n²)）：因为要允许慢候选存在，好让分档表有意义

// #region spec
error CountError;

// 桶的上界：字节值域 0..255
const BUCKET_COUNT: u64 = 256;

spec count(
           const vector<u8>& text) -> u64! {
    effects { alloc };

    benchmodel {
        data {
            text = make_texts();
        }
        scale {
            text in range(1, 1048576, *2);
        }
        seed = 1;
    };

    // 桶法是 O(n)，两两比较是 O(n²)。`?!` 承诺按**最慢候选**给（取 max），
    // 所以整个函数的上界是 O(n²) —— 这是诚实的契约（和 search 同理，会判债务）。
    // 两个尺寸相乘现在**成本分析认得出**了（E15 已修）。
    target { time: { max: O(text.size() * text.size()) }, allocation: { max: 0 }, };

    // #region choose
    choose freq_kernel(const vector<u8>& text) -> u64! {
        over {
            freq_bucket,
            freq_pairwise
        }
        ;
        objective time;
    };
    // #endregion choose
}
// #endregion spec
