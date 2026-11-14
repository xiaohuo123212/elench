// insertion.tu —— 算法样例（κ vs ops 对拍用，2026-11-04 增）
error InsError;

// 原地插入排序：改的是调用方的向量，所以形参 MUST 是可变借用（可变借用不属于 target，target 一字未改）。
// `guarantees is_sorted(xs)` 是**结果**承诺（2026-11-09 加）：以前这里只承诺成本，
// 于是算法把 {5,2,9} 排成 9 5 9 也没人发现；现在 run 会校验（旧算法跑直接 E-Guarantee-Violated）。
spec insertion(vector<u64>& xs) -> u64 {
    effects {};
    guarantees is_sorted(xs);

    benchmodel {
        data {
            xs = random_u64s();
        }
        scale {
            xs in range(1, 4096, *2);
        }
        seed = 1;
    };

    target { time: { max: O(xs.size() * xs.size()) }, allocation: { max: 0 }, };
}
