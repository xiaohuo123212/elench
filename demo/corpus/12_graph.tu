// 12_graph.tu —— `demo/corpus/12_graph.cpp` 的成本契约（模块 `12_graph`）
//
// 用法：
//     dsh check demo/corpus/12_graph.tu demo/corpus/12_graph.cpp --compat
//     dsh cost  demo/corpus/12_graph.tu demo/corpus/12_graph.cpp --compat
//
// 本语料是**容器套容器**（`vector<vector<u64>>`）+ 迭代邻居。
//
// **2026-11-13 复测：8 个函数全部有判决**（早先"只有 3 个算出具体 κ、其余 5 个是 ⊤"
// 的记录已过时）。两个关键词汇：
//   · `L(g)` = `max_i n(g[i])`（元素长度上界）——`g` 是 const 形参时它是全程上界，
//     所以 `g[v].size()` 那种**变量下标**的循环上界也能写出来；
//   · 树递归的激活数由 `recursion depth` 算出 (D+1)·B^D，不再退 ⊤。
//
// **target 的尺寸量集合必须与 κ 的完全一致**（少一个就退 E23）。注意 κ 里出现的是
// `n(g)` 与 `L(g)` 这**两个**量，`L(g)` 不等于 `n(g)`——写目标时别互相代替。

spec make_graph(u64 n) -> vector<vector<u64>> {
    effects { alloc };
    // κ = 3 + 2 + 1 + n + 2 → O(n)。`n` 是标量形参，target 里写 `n` 就是那个量。
    // 分配：建 n 行空邻居，语义上 n 次分配 → O(n)。
    target { time: { max: O(n) }, allocation: { max: O(n) }, };
}

spec add_edge(vector<vector<u64>>& g, u64 u, u64 v) -> () {
    effects { alloc };
    // κ = 7（一次 push_back）→ O(1)。分配：每次 push 至多 1 次 → 1。
    target { time: { max: O(1) }, allocation: { max: O(1) }, };
}

spec bfs(const vector<vector<u64>>& g, u64 src, vector<u64>& dist) -> () {
    effects { alloc };
    // κ 里三个尺寸量：`n(dist)`（出参）、`n(g)`、`L(g)` —— target 必须**全含**，
    // 少写一个就退 E23（尺寸量集合不一致）。
    target { time: { max: O(dist.size() + g.size() * L(g)) },
             allocation: { max: O(g.size() * L(g)) }, };
}

spec reachable(const vector<u64>& dist) -> u64 {
    effects {};
    // κ = 7 + 2 + n(dist)×20 + 20 + 2 → O(n(dist))。只读，不分配。
    target { time: { max: O(dist.size()) }, allocation: { max: 0 }, };
}

spec dfs_visit(const vector<vector<u64>>& g, u64 v, vector<u64>& color) -> bool {
    effects {};
    // 递归深度：8 = 本语料的顶点数（不写就报 E-Recursion-Unbounded，且 cost 拒绝出 κ）。
    recursion depth <= 8;
    // κ = Θ(n(color) × L(g)²)：访问守卫让激活数 ≤ n(color)（入口无条件 `color[v] = 1`、
    // 自调用被 `color[w] == 0` 支配、标记永不回 0）；每次激活扫一遍邻居（≤ L(g)），
    // 而外层 `for k < L(g)` 又把它整体乘了一次 L(g)。
    // **注意 target 里没有 `g.size()`**：κ 只用到 `L(g)` 与 `n(color)` 两个量，
    // 多写一个 `g.size()` 反而退 E23。分配：只写 color 的已有槽位 → 0。
    target { time: { max: O(color.size() * L(g) * L(g)) }, allocation: { max: 0 }, };
}

spec has_cycle(const vector<vector<u64>>& g) -> bool {
    effects { alloc };
    // κ = Θ(n(g)² × L(g)²)：外层扫 n(g) 个顶点、每个顶点调一次 dfs_visit
    // （n(color) = n(g)、且每次再乘 L(g)²）。
    // 历史（当时上面这条 target 不存在）：κ 是 ⊤，原因是"容器实参 `color` 长度推不出"
    // ——`color` 是 has_cycle 体内的**局部**容器。现在 dfs_visit 的 κ 已经能把它
    // 折成 `n(g)`，所以这里也能判了。
    target { time: { max: O(g.size() * g.size() * L(g) * L(g)) }, allocation: { max: 0 }, };
}

spec count_components(const vector<vector<u64>>& g) -> u64 {
    effects { alloc };
    // κ = Θ(n(g)² × L(g))：外层"挑一个未访问顶点"扫 n(g)，内层每次 bfs 又是
    // O(n(g) × L(g))。分配：seen / dist 两条 n(g) 长的表 + bfs 的队列 → 同阶。
    // 历史（当时上面这条 target 不存在）：κ = ⊤，因为 bfs 的 ⊤ 沿调用链传上来。
    target { time: { max: O(g.size() * g.size() * L(g)) },
             allocation: { max: O(g.size() * g.size() * L(g)) }, };
}

spec main() -> i64 {
    effects { io, alloc };
    // κ = 484428 是**常数**（图是文件内字面量构造的，没有尺寸量进 κ）⇒ O(1)。
    // 分配：κ = 64，这里只承诺时间。
    target { time: { max: O(1) }, };
}
