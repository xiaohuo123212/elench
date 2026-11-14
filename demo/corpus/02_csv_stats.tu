// 02_csv_stats.tu —— `demo/corpus/02_csv_stats.cpp` 的成本契约（模块 `02_csv_stats`）
//
// 用法：
//     dsh check demo/corpus/02_csv_stats.tu demo/corpus/02_csv_stats.cpp --compat
//     dsh cost  demo/corpus/02_csv_stats.tu demo/corpus/02_csv_stats.cpp --compat
//
// 本模块 3 个函数里只有 **main** 写了可判的 target（κ 是常数）。
// `split` / `count_fields` 的 κ 都是**具体式**（不是 ⊤，那已经过时），但写不出
// 判得了的 target，原因各自写在 spec 上方。

spec split(const vector<u8>& s, u8 sep) -> vector<vector<u8>> {
    effects { alloc };
    // ⚠ 这条 target **判不了**（E23「尺寸量不一致」）：
    //   κ = … + n(s)×(… + n(cur) + …) + …，其中 `cur` 是体内**局部**容器
    //   （正在组装的字段），而 target 只能引用**形参**。两边尺寸量集合不同，
    //   工具拒绝按"同规模假设"比比值——那是把不知道冒充成知道。
    //   写在这里的是 κ 的阶：n(s) × n(cur)，按同规模假设即 O(n²)。
    //   **不是**算法紧界（每个字符只被搬一次，真实是 O(n(s))）——但契约写紧
    //   就要被判债务，紧界应该等工具能证明 n(cur) ≤ n(s) 之后再改。
    target { time: { max: O(s.size() * s.size()) }, allocation: { max: O(s.size()) }, };
}

spec count_fields(const vector<vector<u8>>& lines) -> map<vector<u8>, i64> {
    effects { alloc };
    // ⚠ 这条 target **写不出判得了的形状**：κ 已具体，但它的两个尺寸量是
    //   `n(lines)` 与 `L(lines)`（每行长度的最大值），并且 κ 里还有 `log2(n(lines))`
    //   （map 的平衡树查找）。多尺寸量的结构判据只能展开**纯多项式**：含 log 就退
    //   "展开不了 ⇒ 无法判定"，而少写一个尺寸量又退 E23。两条都不通，所以不写
    //   ——写了只会是一条"判不了"的 target（比没写更误导）。
    //   分配（可判）：κ_alloc = n(lines)×(L(lines)×2 + 2 + 1) → O(n(lines)×L(lines))。
    // κ = n(lines)×(L(lines)² + log2 n(lines)) —— **多尺寸量 + log 项**。
    // 2026-11-13 起判据支持「多维 profile（次数 + 同维 log 次数）」，所以这条判得了。
    target { time: { max: O(lines.size() * (L(lines) * L(lines) + log2(lines.size()))) },
             allocation: { max: O(lines.size() * L(lines)) }, };
}

spec main() -> i64 {
    effects { io, alloc };
    // κ 是**常数**（文本是文件内字面量，没有尺寸量进 κ）⇒ 阶是 O(1)。
    // 分配：κ = ⊤（局部 map 的长度推不出）⇒ 只承诺时间。
    target { time: { max: O(1) }, };
}
