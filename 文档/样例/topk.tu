// topk.tu —— 第 k 大（十个手写程序之九）的声明部分
//
// 考的四样本事：
//   1. `vector<u64>` + 整数标量形参 `k`：两种循环上界来源都用到——
//      `const n = xs.size()`（尺寸量）与 `k` 本身（整数形参）
//   2. `array<u64, HEAP_CAP>` 当**最小堆**：堆顶就是第 k 大
//   3. 两个候选**有真实阶差**：堆法 O(n·log k)，反复扫描法 O(n·k)
//   4. `i64` 负数仍未修好（E8），所以所有「比大小」都靠**两支 if**，
//      不用 `-1` 之类的哨兵值——从写法上避开负数

// #region spec
error TopkError;

// 堆容量：先挡住 k，再开堆。定长数组不会自己长大，
// 所以 `k > HEAP_CAP` MUST 由程序自己返回错误（与 paren 挡住 65 层同理）。
const HEAP_CAP: u64 = 64;

// 堆的层数上界 = sift-up / sift-down 的圈数上界。
// **递减循环 `while (j > 0)` 推不出上界**（T13 来源③），
// 所以上浮/下沉都改写成「固定圈数 + 位置守卫」的增序循环。
// 6 = log2(HEAP_CAP)：堆里最多 64 个节点，最深的下标 63 到根正好 6 步。
// 这个「用常数圈数代替 log k」是语言逼出来的——代价见 .eln 里的说明。
const HEAP_LEVELS: u64 = 6;

spec topk(
          const vector<u64>& xs, u64 k) -> u64! {
    effects { alloc };

    benchmodel {
        data {
            xs = random_u64s();
            // 标量形参也必须给 data（MB-1：每个形参都要说值从哪来）。
            // **标量没有生成器**——内置只有容器生成器 + `any_of`；
            // 所以标量写**字面量**当默认值。要让它随规模变化，就把该参数写进 `scale`
            // （2026-11-07 起：`scale` 会覆盖字面量；此前字面量永远赢，表是假的）。
            k = 32;
        }
        scale {
            xs in range(1, 1048576, *2);
        }
        seed = 1;
    };

    // `?!` 按**最慢候选**给上界：topk_heap 是 O(n·log k)、topk_select 是 O(n·k)，
    // 取 max 之后是 **O(n·k)**。
    //
    // v0.69+：target 里**可以**写两个尺寸相乘了（`_target_cost` 支持 `*`），
    // 所以这里写真实界 `O(xs.size() * k)`；两边尺寸量一致（`{xs, k}`），能直接比。
    // 早先的注释说"认不出"，那是旧版本的实现缺口（E15），已关闭。
    target { time: { max: O(xs.size() * k) }, allocation: { max: O(1) }, };

    // #region choose
    choose topk_kernel(const vector<u64>& xs, u64 k) -> u64! {
        over {
            topk_heap,
            topk_select
        }
        ;
        objective time;
    };
    // #endregion choose
}
// #endregion spec
