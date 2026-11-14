// stats.tu —— 统计量（十个手写程序之四）的声明部分
//
// 考的四样本事：
//   1. `struct` 聚合返回（现有三个样例都没有 struct）
//   2. 两遍扫描（先求均值，再求方差）
//   3. `?!` 的两条路**同阶不同常数**（与 sum 同一种：分档表才有意义）
//   4. 每个中间量都非负——本语言的 `i64` 负数尚未修好（E8），
//      所以方差用 |x - mean| 而不是 (x - mean)，**从写法上避开负数**

// #region spec
error StatsError;

struct Summary { mean: u64, variance: u64, min_value: u64, max_value: u64, }
spec summarize(
               const vector<u64>& xs) -> Summary! {
    effects { alloc };

    benchmodel {
        data {
            xs = random_u64s();
        }
        scale {
            xs in range(1, 1048576, *2);
        }
        seed = 1;
    };

    target { time: { max: O(xs.size()) }, allocation: { max: 0 }, };

    // #region choose
    // 两候选同阶（都是 O(n)），只在遍历方向上不同——
    // 这正是「成本模型分不出高下，只能靠测量分档」的场景。
    choose sum_kernel(const vector<u64>& xs) -> u64! {
        over {
            sum_forward,
            sum_backward
        }
        ;
        objective time;
    };
    // #endregion choose
}
// #endregion spec
