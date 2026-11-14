// 15_unionfind.tu —— `demo/corpus/15_unionfind.cpp` 的成本契约（模块 `15_unionfind`）
//
// 用法：
//     dsh check demo/corpus/15_unionfind.tu demo/corpus/15_unionfind.cpp --compat
//     dsh cost  demo/corpus/15_unionfind.tu demo/corpus/15_unionfind.cpp --compat
//
// **这份 `.tu` 是补契约覆盖（KPI②）时新加的**：15–19 号语料原先只有 `.cpp`，
// 它们的每个函数都算得出具体 κ（`cost <模块>.eln`），但没有 spec ⇒ 永远不会有
// 判决，而 KPI② 的**分母**照样把它们算作"可算 κ"。补上 spec 之后这 6 个函数
// 才有判决。
//
// 口径与其余语料一致：target 取 κ 的阶、**宁宽勿紧**；每条都做过反向验收
// （把 target 收紧一档，`cost` 必须判 ▲ 债务）。

struct Dsu { parent: vector<u64>, rank: vector<u64>, }

spec dsu_init(Dsu& d, u64 n) -> () {
    effects { alloc };
    // κ = 2 + n×21 + 21 → O(n)。`n` 是标量形参，target 里直接写 n 就绑得上。
    // 两个 push_back（parent / rank）→ 分配 O(n)。
    target { time: { max: O(n) }, allocation: { max: O(n) }, };
}

spec find_root(Dsu& d, u64 x) -> u64 {
    effects {};
    // κ = 2 + n(d.parent)×13 + 13 + n(d.parent)×21 + 21 + 2 → O(n(d.parent))。
    // 两趟都沿父链上浮（第二趟是路径压缩）；循环上界是字段链 `d.parent.size()`，
    // target 写同一个名字就是同一个尺寸量。不建容器 → 分配 0。
    target { time: { max: O(d.parent.size()) }, allocation: { max: 0 }, };
}

spec unite(Dsu& d, u64 a, u64 b) -> bool {
    effects {};
    // κ = … + 2×(n(d.parent)×13 + 13 + n(d.parent)×21 + 21) + … → O(n(d.parent))：
    // 两次 find_root 各 O(n)，其余是常数。只改已有槽位 → 分配 0。
    target { time: { max: O(d.parent.size()) }, allocation: { max: 0 }, };
}

spec count_groups(Dsu& d) -> u64 {
    effects {};
    // κ = 2 + 2 + n(d.parent)×22 + 22 + 2 → O(n(d.parent))。一趟数根，不分配。
    target { time: { max: O(d.parent.size()) }, allocation: { max: 0 }, };
}

spec group_sizes(Dsu& d) -> vector<u64> {
    effects { alloc };
    // κ = … + n(d.parent)×(… n(d.parent)×13 … n(d.parent)×21 …) + … → **O(n²)**：
    // 每个 i 都调一次 find_root，而 find_root 自身是 O(n)。这个平方是**可静态
    // 推出来的**（不是笔误），target 按它写；收紧到 O(n) 判真债务。
    // 分配：κ_alloc 的尺寸量是**局部** `sizes`（n(sizes)），target 里没有这个名字
    // ⇒ 不承诺分配，只承诺时间。
    target { time: { max: O(d.parent.size() * d.parent.size()) }, };
}

spec main() -> i64 {
    effects { io, alloc };
    // κ = 13939 是**常数**（驱动数据是文件内字面量：9 个点、8 条边）⇒ O(1)。
    // 分配：κ_alloc 里带局部 `sizes` 的量 ⇒ 只承诺时间。
    target { time: { max: O(1) }, };
}
