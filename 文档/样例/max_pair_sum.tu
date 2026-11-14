// max_pair_sum.tu —— 朴素「两数之和的最大值」：双层循环，O(n²)
//
// T-05 的**纯算法 demo**：不依赖任何语言特色（没有 `?!`、没有泛型、没有流），
// 读者一眼能读懂。它要展示的是「算法 + 成本契约 + 实测紧度」这条链。
error PairSumError;

spec max_pair_sum(const vector<u64>& xs) -> u64 {
    effects {};

    benchmodel {
        data {
            xs = random_u64s();
        }
        scale {
            xs in range(1, 1024, *2);
        }
        seed = 1;
    };

    target { time: { max: O(xs.size() * xs.size()) }, allocation: { max: 0 }, };
}
