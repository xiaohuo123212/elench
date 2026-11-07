// insertion.tu —— 算法样例（κ vs ops 对拍用，2026-11-04 增）
error InsError;

spec insertion(const vector<u64>& xs) -> u64 {
    effects {};

    benchmodel {
        data {
            xs = random_u64s();
        }
        scale {
            xs in range(1, 4096, *2);
        }
        seed = 1;
    };

    target { time: { max: O(xs.length() * xs.length()) }, allocation: { max: 0 }, };
}
