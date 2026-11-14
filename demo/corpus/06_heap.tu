// 06_heap.tu —— `demo/corpus/06_heap.cpp` 的成本契约（模块 `06_heap`）
//
// 用法：
//     dsh check demo/corpus/06_heap.tu demo/corpus/06_heap.cpp --compat
//     dsh cost  demo/corpus/06_heap.tu demo/corpus/06_heap.cpp --compat
//
// 这份语料是"手写二叉堆 + 定长预分配"，最值得写契约的地方是**堆高**：
// 上浮 / 下沉在 `--compat` 的写法里被改成了"跑 n 圈、每圈带 break"的循环
// （`for (step = 0; step < h.data.size(); ++step)`），所以 κ 是 **O(n)** 而不是
// O(log n)——这不是笔误，是**可静态推界的代价**（递减循环 `while (j > 0)` 推不出
// 上界，见 语法速查 §6）。
//
// `h.data` 是**字段链**：target 里写 `h.data.size()`，与 κ 侧的 `n(h.data)` 是
// 同一个尺寸量——这是本批语料里第一次把 struct 字段的尺寸写进契约。
//
// 6 个函数**全部有判决**（2026-11-13 复测）：`main` 的 κ 已退化成一个常数
// （驱动数据是文件内字面量），前面那句"κ 里同时有 cap 与 n(h.data)"已过时。

struct Job { prio: u64, id: u64, }
struct JobHeap { data: vector<Job>, size: u64, }

spec heap_init(JobHeap& h, u64 cap) -> () {
    effects { alloc };
    // κ = 4 + 2 + cap×15 + 15 → 线性；每轮 push 一个 Job → 分配 cap 次。
    target { time: { max: O(cap) }, allocation: { max: O(cap) }, };
}

spec sift_up(JobHeap& h, u64 i) -> () {
    effects {};
    // κ = 2 + n(h.data)×65 + 65 → O(n(h.data))（"跑 n 圈"的代价，见文件头）。
    target { time: { max: O(h.data.size()) }, allocation: { max: 0 }, };
}

spec sift_down(JobHeap& h, u64 i) -> () {
    effects {};
    // κ = 2 + n(h.data)×100 + 100 → O(n(h.data))。
    target { time: { max: O(h.data.size()) }, allocation: { max: 0 }, };
}

spec heap_push(JobHeap& h, Job job) -> () {
    effects {};
    // 写 `h.data[h.size]` 是**改已有槽位**（堆在 heap_init 里已经填满容量），
    // 不增长、不分配 → 分配 0。时间由内联的 sift_up 决定 → O(n(h.data))。
    target { time: { max: O(h.data.size()) }, allocation: { max: 0 }, };
}

spec heap_pop(JobHeap& h) -> Job {
    effects {};
    // 同上：`h.data[0] = h.data[h.size]` 是覆盖，不分配。→ O(n(h.data))。
    target { time: { max: O(h.data.size()) }, allocation: { max: 0 }, };
}

spec main() -> i64 {
    effects { io, alloc };
    // κ = 7725 是**常数**（容量与数据都是文件内字面量，没有尺寸量进 κ）⇒ O(1)。
    // 分配：κ = 14（常数，heap_init 的那一次）；这里只承诺时间——分配维度留给
    // 实现的形状，不在这条契约里加一个常数承诺。
    target { time: { max: O(1) }, };
}
