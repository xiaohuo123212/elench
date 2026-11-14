// 01_sorting.tu —— `demo/corpus/01_sorting.cpp` 的成本契约（模块名 `01_sorting`）
//
// 用法（C++ 那一侧不改，只把文件名当模块名）：
//     dsh check demo/corpus/01_sorting.tu demo/corpus/01_sorting.cpp --compat
//     dsh cost  demo/corpus/01_sorting.tu demo/corpus/01_sorting.cpp --compat
// （`tools/rename_rate.py` 把 .cpp 复制成 .eln 之后，就是同一对文件名。）
//
// target 的口径：**取 κ 的阶，宁宽勿紧**——target 是上界，写紧了报债务是
// 契约方的错，不是实现的错。
//
// 本模块实测到的 κ 形状（`dsh cost`）：
//   插入排序   2 + n(xs)×(7 + 6 + n(xs)×26 + 26 + 5 + 5) + …   ← 双层循环 → O(n²)
//   合并一步   … + max(mid, hi)×28 + mid×16 + hi×16 + …          ← 只碰 [lo,hi) → 线性
//   归并排序   树递归（一条路径上两个自调用点）：激活数由声明的 `recursion depth`
//              算出 `(D+1)·B^D`，κ 的尺寸量是**区间长度** `(hi - lo)`（一个尺寸差）。
//              写 target 要配 `requires lo <= hi;`，而 `check` 不认这种形状
//              （`E-Require-NotCheckable`）⇒ 这一条只留说明、不给承诺。
//   驱动程序   数据全是文件内字面量 ⇒ κ 是**常数**（1455 + …），阶只能写 O(1)。

spec insertion_sort(vector<u64>& xs) -> () {
    effects {};
    // 原地排序：不碰堆，分配 0。
    target { time: { max: O(xs.size() * xs.size()) }, allocation: { max: 0 }, };
}

spec merge_halves(vector<u64>& xs, u64 lo, u64 mid, u64 hi) -> () {
    effects { alloc };
    // 临时缓冲从空 push 到 hi-lo 个元素：时间与分配都随这一段的长度长。
    // `mid` / `hi` 是**标量形参**，target 里可以直接写（`O(hi)` 也成立，更松）。
    target { time: { max: O(max(mid, hi)) }, allocation: { max: O(max(mid, hi)) }, };
}

spec merge_sort(vector<u64>& xs, u64 lo, u64 hi) -> () {
    effects { alloc };
    // 递归深度：每次折半，n ≤ 2^32 时深度 ≤ 33。**必须写**，否则
    // `E-Recursion-Unbounded`（κ 只按一层计会小于真实成本）。
    recursion depth <= 32;
    // κ = Θ((hi - lo)·log(hi - lo))——树递归的激活数 `(D+1)·B^D` 由上面的 depth 算出，
    // 尺寸量是**区间长度** `(hi - lo)`（一个尺寸差），不是 `hi` / `lo` 两个独立量。
    //
    // **2026-11-13 起这条 target 补上了**（原来"补不了"的两个原因都已消失）：
    //   ① `requires lo <= hi;` 现在被 `check` 接受，并在**调用点用整数线性算术证明**
    //      （`mid = lo + (hi-lo)/2` ⇒ `lo <= mid <= hi`；`merge_sort(xs,0,xs.size())`
    //      由内建 `0 <= n(xs)` 证出）。
    //   ② 判阶现在按层求和：κ = (s+2)·A + B·((s+1)/2)·(log2((s+1)/2)+1)，s = hi - lo。
    // 分配仍是 ⊤（临时缓冲长度推不出）⇒ 不承诺分配。
    requires lo <= hi;
    target { time: { max: O((hi - lo) * log(hi - lo)) }, allocation: { max: 0 }, };
}

spec main() -> i64 {
    effects { io, alloc };
    // κ = 1455 + … 是**常数**（main 的数据全是文件内字面量，没有尺寸量进 κ）。
    // 常数 κ 的阶就是 O(1)；写更松的阶也一样达成（规则如此），但没有理由放水。
    // 分配：κ = ⊤（局部 vector 的长度推不出）⇒ 不承诺分配。
    target { time: { max: O(1) }, };
}
