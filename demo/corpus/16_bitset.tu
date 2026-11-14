// 16_bitset.tu —— `demo/corpus/16_bitset.cpp` 的成本契约（模块 `16_bitset`）
//
// 用法：
//     dsh check demo/corpus/16_bitset.tu demo/corpus/16_bitset.cpp --compat
//     dsh cost  demo/corpus/16_bitset.tu demo/corpus/16_bitset.cpp --compat
//
// 位图（`vector<u64>` 当位集合）+ 布隆式两位判重。口径与其余语料一致：
// target 取 κ 的阶、**宁宽勿紧**；每条都做过反向验收（收紧一档必须判 ▲ 债务）。
//
// 这份 `.tu` 是 2026-11-13 补的：16–19 号语料此前只有 `.cpp`，κ 算得出却没有
// spec ⇒ 永远不会有判决，而覆盖率的分母照样把它们算进去。

struct Bits { words: vector<u64>, }

spec bits_init(Bits& b, u64 nbits) -> () {
    effects { alloc };
    // κ = 7 + 2 + (nbits + 63)×15 + 15 → O(nbits)：循环上界 `words = (nbits+63)/64`
    // 由标量形参 `nbits` 决定，target 写 `nbits` 就绑得上。
    // 分配：κ_alloc = (nbits + 63)×1 → O(nbits)（每个字一次 push_back）。
    target { time: { max: O(nbits) }, allocation: { max: O(nbits) }, };
}

spec word_of(u64 i) -> u64 {
    effects {};
    // κ = 4（常数：一次移位）→ O(1)，不分配。
    target { time: { max: O(1) }, allocation: { max: 0 }, };
}

spec mask_of(u64 i) -> u64 {
    effects {};
    // κ = 9（常数：一个局部量 + 移位）→ O(1)，不分配。
    target { time: { max: O(1) }, allocation: { max: 0 }, };
}

spec bit_set(Bits& b, u64 i) -> () {
    effects {};
    // κ = 31（常数：两次下标 + 掩码 + 一次写）→ O(1)。只写已有槽位 → 分配 0。
    target { time: { max: O(1) }, allocation: { max: 0 }, };
}

spec bit_clear(Bits& b, u64 i) -> () {
    effects {};
    // κ = 32 → O(1)。同 bit_set，只写已有槽位 → 分配 0。
    target { time: { max: O(1) }, allocation: { max: 0 }, };
}

spec bit_get(const Bits& b, u64 i) -> bool {
    effects {};
    // κ = 25 → O(1)。只读 → 分配 0。
    target { time: { max: O(1) }, allocation: { max: 0 }, };
}

spec count_bits(const Bits& b) -> u64 {
    effects {};
    // κ = 2 + 2 + n(b.words)×928 + 928 + 2 → O(n(b.words))：外层按字扫一遍，
    // 内层 Kernighan 的 `while (x != 0)` 被折成常数 928（≤ 64 位）。
    target { time: { max: O(b.words.size()) }, allocation: { max: 0 }, };
}

spec xor_bits(const Bits& a, const Bits& b) -> Bits {
    effects { alloc };
    // κ = 2 + 2 + n(a.words)×27 + 27 + 3 → O(n(a.words))。循环上界是 `a.words.size()`，
    // 所以尺寸量只有 `n(a.words)`——**不要**顺手把 `b` 也写进去（多写退 E23）。
    // 分配：κ_alloc = n(a.words)×1 → O(n(a.words))（逐字 push_back）。
    target { time: { max: O(a.words.size()) }, allocation: { max: O(a.words.size()) }, };
}

spec mix64(u64 x) -> u64 {
    effects {};
    // κ = 23（三次混合，全是常数）→ O(1)，不分配。
    target { time: { max: O(1) }, allocation: { max: 0 }, };
}

spec dedup_count(const vector<u64>& ids, Bits& seen, u64 nbits) -> u64 {
    effects {};
    // κ = 2 + 2 + n(ids)×186 + 186 + 2 → O(n(ids))：每条事件两次位查询 / 置位都是
    // 常数（`% nbits` 与掩码不进 κ 的阶）。位图 `seen` 只写已有槽位 → 分配 0。
    target { time: { max: O(ids.size()) }, allocation: { max: 0 }, };
}

spec bits_from(const vector<u64>& xs, u64 nbits) -> Bits {
    effects { alloc };
    // κ = … + (nbits+63)×15 + … + n(xs)×49 + … → **两个**尺寸量：`nbits` 与 `n(xs)`。
    // target 必须两个都写（少一个退 E23）；宁宽勿紧写成加法。
    // 分配：κ_alloc = (nbits+63)×1 → O(nbits)（只由建位图那次决定）。
    target { time: { max: O(nbits + xs.size()) }, allocation: { max: O(nbits) }, };
}

spec main() -> i64 {
    effects { io, alloc };
    // κ = 2715859 是**常数**（位图大小 512、事件表都是文件内字面量）⇒ O(1)。
    // 分配：κ_alloc = 1735 是**字面常数**，而 allocation 维度按**数量**比较
    // （不是按阶）——写 `O(1)` 会被折成 1，1735 > 1 就报假债务；写 1735 又只是
    // 把测量值抄成承诺。所以这里只承诺时间（与 12_graph / 15_unionfind 的 main 同口径）。
    target { time: { max: O(1) }, };
}
