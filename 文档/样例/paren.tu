// paren.tu —— 括号匹配（十个手写程序之六）的声明部分
//
// 考的三样本事：
//   1. `array<T, N>` 当栈用 —— **定长数组**，超过容量 MUST 报错
//   2. `requires` 声明前置条件（pop 前非空）
//   3. 单遍扫描 O(n)；没有 ?! —— 括号匹配没有"另一条路"

// #region spec
error ParenError;

const OPEN_PAREN: u64 = '(';
const CLOSE_PAREN: u64 = ')';
const STACK_CAP: u64 = 64;

spec paren(
           const vector<u8>& text) -> u64! {
    effects { alloc };
    requires STACK_CAP > 0;

    target { time: { max: O(text.size()) }, allocation: { max: 0 }, };
}
// #endregion spec
