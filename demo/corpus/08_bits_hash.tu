// 08_bits_hash.tu —— `demo/corpus/08_bits_hash.cpp` 的成本契约（模块 `08_bits_hash`）
//
// 用法：
//     dsh check demo/corpus/08_bits_hash.tu demo/corpus/08_bits_hash.cpp --compat
//     dsh cost  demo/corpus/08_bits_hash.tu demo/corpus/08_bits_hash.cpp --compat
//
// 前四个是"纯位运算 / 固定圈数"的内核，κ 全是**常数**——这类函数最值得写 O(1)：
// 它是能被反例打穿的（把 popcount64 的 `k < 64` 改成 `k < x` 就会报债务）。
//
// 6 个函数**全部有判决**（2026-11-13 复测）：`count_words` 的尺寸量现在写成
// `L(words)`（元素长度上界），`main` 的 κ 退化成常数。

spec popcount64(u64 x) -> u64 {
    effects {};
    // 固定 64 圈：κ = 1176（常数）→ O(1)。改一圈都必须改契约。
    target { time: { max: O(1) }, allocation: { max: 0 }, };
}

spec rotl64(u64 x, u64 r) -> u64 {
    effects {};
    // κ = 24 → O(1)。
    target { time: { max: O(1) }, allocation: { max: 0 }, };
}

spec mix64(u64 x) -> u64 {
    effects {};
    // κ = 23 → O(1)。
    target { time: { max: O(1) }, allocation: { max: 0 }, };
}

spec bucket_of(u64 key) -> u64 {
    effects {};
    // κ = 7 → O(1)。
    target { time: { max: O(1) }, allocation: { max: 0 }, };
}

spec count_words(const vector<vector<u8>>& words, unordered_map<vector<u8>, u64>& freq) -> () {
    effects { alloc };
    // κ = n(words)×(2 + 2 + 3 + n(freq) + L(words) + 3 + n(freq) + L(words) + 1)
    //      → O(n(words) × (n(freq) + L(words)))。
    // 三个尺寸量**全都要写**（`words` / `freq` / `L(words)`）：
    //   · `L(words)` = 每个词长度的上界（`for (const auto& w : words)` 里的
    //     `n(w)` 被折回到根容器的元素长度量上）；
    //   · 少写任何一个 ⇒ E23「尺寸量不一致」（2026-11-12 之前这里只有
    //     `words.size() * freq.size()`，正是那样被判不了的）。
    // 顺序写在 product 里：不用 `^`，`a * b * c` 的阶就是相乘。
    target { time: { max: O(words.size() * L(words) * freq.size()) },
             allocation: { max: O(words.size()) }, };
}

spec main() -> i64 {
    effects { io, alloc };
    // κ = 4170 是**常数**（数据是文件内字面量；`unordered_map` 的键长也不再漏进来，
    // 旧说明里那句"κ = ⊤"已过时）⇒ 阶 O(1)。分配：κ = 16，这里只承诺时间。
    target { time: { max: O(1) }, };
}
