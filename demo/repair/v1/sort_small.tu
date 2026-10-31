// 演示用：**同一份声明，两种实现**
//
// v1（错误）：手写插入排序，κ = O(n²) → 与声明的 O(n log n) 相比**超标**
// v2（修好）：改用库里的 sort（语言保证 O(n log n)）→ 达标
//
// 演示脚本：python tools/demo_repair.py

spec sort_small(Vec<u64>& xs) -> u64 {
    effects {};
    target {
        time: { max: O(xs.length() * log(xs.length())) },
        allocation: { max: 0 },
    };
}
