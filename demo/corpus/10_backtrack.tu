// 10_backtrack.tu —— `demo/corpus/10_backtrack.cpp` 的成本契约（模块 `10_backtrack`）
//
// 用法：
//     dsh check demo/corpus/10_backtrack.tu demo/corpus/10_backtrack.cpp --compat
//     dsh cost  demo/corpus/10_backtrack.tu demo/corpus/10_backtrack.cpp --compat
//
// **本文件是全批唯一必须写 `recursion depth` 的地方**（`QueenSolver::place` 递归）：
// 没有它，`check` 报 `E-Recursion-Unbounded`，`cost` **直接拒绝算 κ**——因为
// 只按一层计出来的 κ 会小于真实成本，那是假结论。
// 深度 = 棋盘边长 n（本语料 `QueenSolver solver(6)` → 6 层），这里声明 8 留余量。
//
// **target 里的幂允许字段基**（2026-11-12）：κ 里的尺寸量是 `n(recv.n_)`
// （接收者字段），target 写 `(recv.n_) ^ 10` —— 两边是**同一个尺寸量**
// （`n(recv.n_)`），判阶才对得上。`^` 只在 target 成本式里当幂用；
// `.eln` 代码里的 `^` 仍是按位异或，不是一回事。
// 阶按实测 κ 写（place / solve / main 的主导项都是 `n(…)^10`），**宁宽勿紧**。

struct QueenSolver {
    n_: u64,
    cols_: vector<u64>,
    diag1_: vector<u64>,
    diag2_: vector<u64>,
    first_: vector<u64>,
    count_: u64,
}

spec QueenSolver_QueenSolver(QueenSolver& recv, u64 n) -> () {
    effects { alloc };
    // 构造：κ = 27（常数）→ O(1)；四条 n / 2n 长的向量 → 分配 O(n)。
    target { time: { max: O(n) }, allocation: { max: O(n) }, };
}

spec QueenSolver_first_solution(QueenSolver& recv) -> vector<u64>& {
    effects {};
    // 只是返回成员引用：κ = 3 → O(1)，不分配。
    target { time: { max: O(1) }, allocation: { max: 0 }, };
}

spec QueenSolver_place(QueenSolver& recv, u64 row) -> () {
    effects {};
    // **递归深度必须声明**（见文件头）。本语料 n = 6。
    recursion depth <= 8;
    // κ = Θ(n(recv.n_)^10)：树递归激活数 `(D+1)·B^D` = 9·n^8（D = 8），
    // 每层再扫 n(recv.n_) 个列、外层还乘一次 n(recv.n_) ⇒ 10 次。
    // target 写**同阶**：写紧一阶（n^9）就是真债务，写松不影响达成。
    target { time: { max: O((recv.n_) ^ 10) }, allocation: { max: 0 }, };
}

spec QueenSolver_solve(QueenSolver& recv) -> u64 {
    effects { alloc };
    // κ = Θ(n(recv.n_)^10)：调 place，阶由 place 的调用树决定（只多几个常数操作）。
    target { time: { max: O((recv.n_) ^ 10) }, allocation: { max: 0 }, };
}

spec QueenSolver_render(QueenSolver& recv) -> vector<vector<u8>> {
    effects { alloc };
    // 不写 target：κ 是**具体式**，但它同时含 `n(line)`——`line` 是体内局部量，
    // target 里没有名字可写。两个量凑不齐 ⇒ 写了也只判"尺寸量不一致"（E23）。
    target { time: { max: O((recv.n_) ^ 3) }, allocation: { max: O((recv.n_) ^ 2) }, };
}

spec main() -> i64 {
    effects { io, alloc };
    // κ 的尺寸量是 `solver.n_`（本地对象的字段），与 κ 打印的 `n(solver.n_)`
    // 同名；调用树同一量级 ⇒ 10 次。分配 = n(solver.n_)^2 + 2·n(solver.n_) ⇒ 2 次。
    target {
        time: { max: O((solver.n_) ^ 10) },
        allocation: { max: O((solver.n_) ^ 2) },
    };
}
