// 17_sortkey.tu —— `demo/corpus/17_sortkey.cpp` 的成本契约（模块 `17_sortkey`）
//
// 用法：
//     dsh check demo/corpus/17_sortkey.tu demo/corpus/17_sortkey.cpp --compat
//     dsh cost  demo/corpus/17_sortkey.tu demo/corpus/17_sortkey.cpp --compat
//
// 按 (主键, 二级键) 手写插入排序 + 顺序扫分组 + 按 key 二分。
// 口径与其余语料一致：target 取 κ 的阶、**宁宽勿紧**；每条都做过反向验收。

struct Entry { key: u64, seq: u64, bytes: u64, }
struct Group { key: u64, hits: u64, total: u64, }

spec entry_less(const Entry& a, const Entry& b) -> bool {
    effects {};
    // κ = 18（常数：两次比较，没有循环）→ O(1)，不分配。
    target { time: { max: O(1) }, allocation: { max: 0 }, };
}

spec group_less(const Group& a, const Group& b) -> bool {
    effects {};
    // κ = 18 → O(1)，不分配。
    target { time: { max: O(1) }, allocation: { max: 0 }, };
}

spec sort_entries(vector<Entry>& xs) -> () {
    effects {};
    // κ = 2 + n(xs)×(7 + 9 + n(xs)×44 + 44 + 8 + 5) + … → **Θ(n(xs)²)**：
    // 插入排序的内层 `while` 上界是 i（外层下标）⇒ 每轮再扫一遍 n(xs)。
    // 这个平方是**可静态推出来的**（不是笔误），target 按它写；收紧到 O(n) 判真债务。
    // 只搬数组里已有的元素 → 分配 0。
    target { time: { max: O(xs.size() * xs.size()) }, allocation: { max: 0 }, };
}

spec group_by_key(const vector<Entry>& xs) -> vector<Group> {
    effects { alloc };
    // κ = 3 + 2 + n(xs)×42 + 42 + 2 → O(n(xs))：顺序扫一遍，相邻 key 相同就累计。
    // 分配：κ_alloc = n(xs)×1 → O(n(xs))（每个新 key 一次 push_back）。
    target { time: { max: O(xs.size()) }, allocation: { max: O(xs.size()) }, };
}

spec pick_top(const vector<Group>& gs) -> Group {
    effects {};
    // κ = 7 + 2 + n(gs)×44 + 44 + 5 → O(n(gs))：一趟比大小，不建容器 → 分配 0。
    target { time: { max: O(gs.size()) }, allocation: { max: 0 }, };
}

spec lower_bound_by_key(const vector<Entry>& xs, u64 key) -> u64 {
    effects {};
    // 不写 target（唯一原因：外层 `while (lo < hi)` 的迭代上界被记成了**局部标量**
    // `hi`，见 κ = 7 + n(hi)×25 + 25 + 2）。`hi` 是体内的 `std::size_t hi = xs.size()`，
    // 不是形参；target 只能引用形参，写 `O(xs.size())` 会退 E23「尺寸量不一致」
    // （实测：`{hi} ≠ {xs}`），写 `O(hi)` 又因为 `hi` 不在作用域里绑不上。
    // 这是工具缺口（`hi` 由 `xs.size()` 初始化，理想形态能折成 `n(xs)`），不是语料写法问题。
    // κ 的阶按同规模假设是 O(n(xs))，但**不写进 target**——写了只会是一条判不了的 target。
    // 2026-11-13：`lo`/`hi` 区间减半现在被建模 ⇒ κ = 7 + (log2(n(xs))+1)×25 + 25 + 2，不再含 `hi`。
    target { time: { max: O(xs.size()) }, allocation: { max: O(0) }, };
}

spec main() -> i64 {
    effects { io, alloc };
    // 不写 target：κ 里带两次 `n(hi)×25`（`lower_bound_by_key` 被内联进来，
    // 它的局部标量 `hi` 成了 main 的**自由符号**）。main 没有形参，这个名字更没有
    // 地方可写 ⇒ 尺寸量集合 {hi} 对不齐任何 target。
    // 分配 κ_alloc = 14 是字面常数，allocation 维度按数量比较，写 `O(1)` 会折成 1
    // 反而报假债务 ⇒ 连 allocation 也不承诺。
    // 2026-11-13：二分调用点区间变成常数 ⇒ κ 折成常数。
    target { time: { max: O(1) }, allocation: { max: O(14) }, };
}
