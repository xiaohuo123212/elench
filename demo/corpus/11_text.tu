// 11_text.tu —— `demo/corpus/11_text.cpp` 的成本契约（模块 `11_text`）
//
// 用法：
//     dsh check demo/corpus/11_text.tu demo/corpus/11_text.cpp --compat
//     dsh cost  demo/corpus/11_text.tu demo/corpus/11_text.cpp --compat

spec is_blank(u8 c) -> bool {
    effects {};
    // 三次比较：κ = 12（常数）→ O(1)。
    target { time: { max: O(1) }, allocation: { max: 0 }, };
}

spec trim(const vector<u8>& s) -> vector<u8> {
    effects { alloc };
    // 三趟扫描：κ = … + n(s)×33 + n(s)×35 + n(s)×27 + … → 线性；
    // 输出串最长 n(s) → 分配 O(n(s))。
    target { time: { max: O(s.size()) }, allocation: { max: O(s.size()) }, };
}

spec lower_key(const vector<u8>& s) -> vector<u8> {
    effects { alloc };
    // 一趟：κ = 3 + n(s)×21 + 2 → 线性；分配 O(n(s))。
    target { time: { max: O(s.size()) }, allocation: { max: O(s.size()) }, };
}

spec split(const vector<u8>& s, u8 sep) -> vector<vector<u8>> {
    effects { alloc };
    // ⚠ 这条 target **目前判不了**（E23）：κ 里有局部容器 `cur` 的尺寸量 n(cur)。
    //   写的是 κ 的阶（n(s) × n(cur)，同规模假设下 O(n²)），不是算法紧界。
    target { time: { max: O(s.size() * s.size()) }, allocation: { max: O(s.size()) }, };
}

spec main() -> i64 {
    effects { io, alloc };
    // 不写 target：main 无形参，κ 却是**具体式**、尺寸量全是体内**局部**容器 ——
    // `n(field)` / `n(fields)` / `n(lines)` / `n(text)`（2026-11-13 复测的实际集合）；
    // 名字写不出来 ⇒ 任何 target 都只能落 E23（或 H3 的假债务）。
    // 另注：本语料的 `for (const auto& kv : counts) { kv.first … }` 能过 check，
    // 但解释器执行到 `kv.first` 会报"未实现的字段访问"（README 里那条"假绿"）。
    // 2026-11-13：元素长度四跳链接上之后，`n(field)` 归一成常数
    // （`n(field) → L(fields) → n(line) → L(lines) → n(text) → 常数`），
    // 所以 main 的 κ 是**纯常数**、尺寸量为空 ⇒ 可以承诺 `O(1)`。
    // **不写 allocation 的 target**：κ_alloc = 410,459,616（常数，但 allocation 维的判据
    // 是 `compare_exact`——**常数按数值比**，不是比阶）。写任何小于它的数都是真债务；
    // 而写 410459616 这种数字等于把实现细节抄进契约。所以只承诺 time。
    target { time: { max: O(1) }, };
}
