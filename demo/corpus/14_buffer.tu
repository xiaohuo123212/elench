// 14_buffer.tu —— `demo/corpus/14_buffer.cpp` 的成本契约（模块 `14_buffer`）
//
// 用法：
//     dsh check demo/corpus/14_buffer.tu demo/corpus/14_buffer.cpp --compat
//     dsh cost  demo/corpus/14_buffer.tu demo/corpus/14_buffer.cpp --compat
//
// 字节缓冲（`vector<unsigned char>`，不是 `std::string`）。`--compat` 把
// `std::string` 记成 `string`、`std::size_t` 记成 `u64`——签名照这个写。
//
// **2026-11-13 复测：10 个函数里 9 个有判决**。早先"4 个 κ = ⊤"的记录已过时：
// find_from / count_occurrences / replace_all 的 κ 都是具体式，main 的 κ 也变成了
// 具体式——但 main 的尺寸量是**局部**容器 `masked`，target 里没有这个名字可写，
// 所以只有它没有 target（原因见该 spec 上方）。

spec from_text(const string& s) -> vector<u8> {
    effects { alloc };
    // κ = 3 + n(s)×7 + 2 → O(n(s))；每字符 push_back 一次 → 分配 O(n(s))。
    target { time: { max: O(s.size()) }, allocation: { max: O(s.size()) }, };
}

spec append_text(vector<u8>& b, const string& s) -> () {
    effects { alloc };
    // κ = n(s)×7 → O(n(s))；分配 O(n(s))（追加到已有缓冲，b 本身不缩小）。
    target { time: { max: O(s.size()) }, allocation: { max: O(s.size()) }, };
}

spec append_range(vector<u8>& b, const vector<u8>& src, u64 lo, u64 hi) -> () {
    effects { alloc };
    // κ = 2 + hi×16 + 16 → O(hi)。循环上界记的是 `hi`（不是 `hi - lo`），
    // 所以 target 写 `hi`——这正是 ①取 κ 的阶、②宁宽勿紧。`hi` 是标量形参，绑得上。
    // 分配：至多 hi 次 push → O(hi)。
    target { time: { max: O(hi) }, allocation: { max: O(hi) }, };
}

spec find_from(const vector<u8>& hay, const vector<u8>& needle, u64 from) -> u64 {
    effects {};
    // `i + needle.size() <= hay.size()` 这种「归纳变量 + 尺寸量」由偏移上界规则
    // 覆盖（`<=` 给 `N+1`，宽松但保证是上界）⇒ κ = O(n(hay) × n(needle))。
    target { time: { max: O(hay.size() * needle.size()) }, allocation: { max: 0 }, };
}

spec count_byte(const vector<u8>& b, u8 v) -> u64 {
    effects {};
    // κ = 2 + 2 + n(b)×20 + 20 + 2 → O(n(b))；只读，不分配。
    target { time: { max: O(b.size()) }, allocation: { max: 0 }, };
}

spec count_occurrences(const vector<u8>& hay, const vector<u8>& needle) -> u64 {
    effects {};
    // κ = n(hay)² × n(needle)（外层扫一遍、每次调 find_from 又扫一遍），
    // 所以 target 必须写**两阶**——写 `O(hay*needle)` 会判真债务。
    target { time: { max: O(hay.size() * hay.size() * needle.size()) }, allocation: { max: 0 }, };
}

spec slice(const vector<u8>& b, u64 lo, u64 hi) -> vector<u8> {
    effects { alloc };
    // κ = 3 + 1 + 1 + 2 + hi×16 + 16 + 4 + 2 → O(hi)（内部调 append_range，
    // 上界同样是 hi）。分配：至多 hi 个字节 → O(hi)。
    target { time: { max: O(hi) }, allocation: { max: O(hi) }, };
}

spec replace_all(const vector<u8>& src, const vector<u8>& from, const vector<u8>& to) -> vector<u8> {
    effects { alloc };
    // κ = Θ(n(src)² × (n(from) + n(to)))：外层扫 n(src)，每次调 find_from
    // （O(n(src) × n(from))）再 append 一整段（≤ n(src) + n(to)）。
    // 三个尺寸量 `src` / `from` / `to` **都要写**（少一个退 E23）；宁宽勿紧，
    // 所以写成 `n(src)² × (n(from) + n(to))`，比逐项展开松一档也无妨。
    // 分配：κ_alloc 里有 `n(src) × ⊤`（append_range 的局部缓冲长度推不出）
    // ⇒ **不承诺分配**，只承诺时间。
    target { time: { max: O(src.size() * src.size() * (from.size() + to.size())) }, };
}

spec print_preview(const vector<u8>& b) -> () {
    effects { io };
    // κ = 2 + n(b)×35 + 35 + 4 → O(n(b))。printf → io；不分配（不建容器）。
    target { time: { max: O(b.size()) }, allocation: { max: 0 }, };
}

spec main() -> i64 {
    effects { io, alloc };
    // κ 是**具体式**（不再是 ⊤）：`816578 + … + n(masked)×16 + …`，尺寸量
    // `masked` 是体内**局部**容器 ⇒ target 里没有这个名字可写（E23）。
    // 写 `O(1)` 会被 H3 判成真债务（常数 target 对含尺寸量的 κ）。
    // 所以只声明效果：printf → io、建容器 / push_back → alloc。
    // **只承诺 time**：断言行里写着「写 O(1) 会被 H3 判成真债务」——那句话对 time 不成立
    // （κ_time 是常数），但**对 allocation 成立**：κ_alloc 含 `n(masked)`（体内局部容器），
    // `O(1)` 在 allocation 维按数值比 ⇒ 永远超标，是一条**假债务**。
    // 在 allocation 维能写出可判的上界之前，**不承诺它**（只声明 effects 里的 alloc）。
    target { time: { max: O(1) }, };
}
