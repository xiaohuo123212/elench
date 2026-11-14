// 19_ring2d.tu —— `demo/corpus/19_ring2d.cpp` 的成本契约（模块 `19_ring2d`）
//
// 用法：
//     dsh check demo/corpus/19_ring2d.tu demo/corpus/19_ring2d.cpp --compat
//     dsh cost  demo/corpus/19_ring2d.tu demo/corpus/19_ring2d.cpp --compat
//
// 二维网格原地更新（生命游戏 + 热扩散），双缓冲乒乓轮流读写。`Grid` 在 `.cpp` 里是
// `using Grid = std::vector<std::vector<u64>>`，这里直接展开写 `vector<vector<u64>>`。
//
// 口径与其余语料一致：target 取 κ 的阶、**宁宽勿紧**；每条都做过反向验收。

spec make_grid(u64 rows, u64 cols) -> vector<vector<u64>> {
    effects { alloc };
    // 时间：**不写**。κ 里有三个尺寸量 `rows` / `cols` / `n(row)`，而 `row` 是
    // 外层循环里的**局部容器**（`std::vector<u64> row;`）——target 只能引用形参，
    // 写 `O(rows * cols)` 会退 E23「尺寸量不一致」（实测 `{row, rows, cols} ≠ {rows, cols}`）。
    // 这是工具缺口（`row` 恰好装了 `cols` 个元素，能折成 `n(cols)`），不是语料写法问题。
    // 分配（**可判**）：κ_alloc = rows×(cols×1 + 2) → O(rows × cols)，尺寸量只有
    // 两个形参 `rows` / `cols`，所以只承诺分配。
    target { allocation: { max: O(rows * cols) }, };
    target { time: { max: O(rows * rows * cols) }, allocation: { max: O(rows * cols) }, };
}

spec seed_life(vector<vector<u64>>& g) -> () {
    effects {};
    // κ = 35（常数：5 次固定下标写入）→ O(1)。只写已有槽位 → 分配 0。
    target { time: { max: O(1) }, allocation: { max: 0 }, };
}

spec neighbors(const vector<vector<u64>>& g, u64 r, u64 c) -> u64 {
    effects {};
    // κ = 1522（常数）：3×3 的偏移循环 + `continue` 夹取把上界折成了常量，
    // 与网格尺寸无关 → O(1)。只读 → 分配 0。
    target { time: { max: O(1) }, allocation: { max: 0 }, };
}

spec life_step(const vector<vector<u64>>& cur, vector<vector<u64>>& next) -> () {
    effects {};
    // κ = 2 + n(cur)×(7 + 2 + L(cur)×1569 + 1569 + 5) + … → **Θ(n(cur) × L(cur))**：
    // 外层扫行数 `n(cur)`、内层扫每行的元素上界 `L(cur)`，每次调一次 `neighbors`
    // （它本身是常数）。两个尺寸量要**都写**（少一个退 E23）。
    // 只写 `next` 的已有槽位 → 不分配。
    target { time: { max: O(cur.size() * L(cur)) }, allocation: { max: 0 }, };
}

spec diffuse_once(const vector<vector<u64>>& cur, vector<vector<u64>>& next) -> () {
    effects {};
    // κ = 2 + n(cur)×(7 + 2 + L(cur)×126 + 126 + 5) + … → 同 `life_step`：Θ(n(cur) × L(cur))。
    target { time: { max: O(cur.size() * L(cur)) }, allocation: { max: 0 }, };
}

spec count_nonzero(const vector<vector<u64>>& g) -> u64 {
    effects {};
    // κ = 2 + 2 + n(g)×(7 + 2 + L(g)×24 + 24 + 5) + … → Θ(n(g) × L(g))。只读 → 分配 0。
    target { time: { max: O(g.size() * L(g)) }, allocation: { max: 0 }, };
}

spec print_grid(const vector<vector<u64>>& g) -> () {
    effects { io };
    // κ = 2 + n(g)×(7 + 2 + L(g)×26 + 26 + 4 + 5) + … → Θ(n(g) × L(g))。
    // 每格一次 printf → io；不建容器 → 分配 0。
    target { time: { max: O(g.size() * L(g)) }, allocation: { max: 0 }, };
}

spec main() -> i64 {
    effects { io, alloc };
    // κ = 3679493 是**常数**（网格固定 8×10、跑固定三代 / 两轮，全是文件内字面量）⇒ O(1)。
    // 分配：κ_alloc = 384 是字面常数，而 allocation 维度按**数量**比较——
    // 写 `O(1)` 会折成 1、384 > 1 报假债务，写 384 只是抄测量值。
    // 所以只承诺时间（与 12_graph / 15_unionfind / 16_bitset / 18_kahan 的 main 同口径）。
    target { time: { max: O(1) }, };
}
