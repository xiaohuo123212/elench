// 05_ring_buffer.tu —— `demo/corpus/05_ring_buffer.cpp` 的成本契约（模块 `05_ring_buffer`）
//
// 用法：
//     dsh check demo/corpus/05_ring_buffer.tu demo/corpus/05_ring_buffer.cpp --compat
//     dsh cost  demo/corpus/05_ring_buffer.tu demo/corpus/05_ring_buffer.cpp --compat
//
// `struct Ring` 与它的**成员函数**在这份语料里写成 `.tu` 的 struct + 三个自由函数
// （`Ring_Ring` / `Ring_push` / `Ring_pop`，`--compat` 的 C++→本语言映射）：
// 本语言没有成员函数，第一个形参就是接收者 `recv`。

struct Ring { buf: vector<i64>, head: u64, count: u64, }

spec Ring_Ring(Ring& recv, u64 cap) -> () {
    effects { alloc };
    // 构造：κ = 12（常数）→ O(1)；`buf` 按 cap 填满 → 分配 O(cap)。
    target { time: { max: O(1) }, allocation: { max: O(cap) }, };
}

spec Ring_push(Ring& recv, i64 v) -> () {
    effects {};
    // 写一个已有槽位 + 一次取模：κ = 40，与 cap 无关 → O(1)，不分配。
    target { time: { max: O(1) }, allocation: { max: 0 }, };
}

spec Ring_pop(Ring& recv) -> i64 {
    effects {};
    // κ = 24 → O(1)，不分配。
    target { time: { max: O(1) }, allocation: { max: 0 }, };
}

spec main() -> i64 {
    effects { io, alloc };
    // 驱动程序：κ = 252（常数，输入是文件内字面量）→ O(1)；
    // 构造一次 Ring → 分配 O(1)（写 1 也行，这里按阶写）。
    target { time: { max: O(1) }, allocation: { max: O(1) }, };
}
