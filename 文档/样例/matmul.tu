// matmul.tu —— 矩阵乘法（十个手写程序之八）的声明部分
//
// 考的四样本事：
//   1. 二维数据用「扁平序列 + 边长」表示（本语言没有嵌套 Vec 的语法糖）
//   2. 三层嵌套循环——E4 合并时，嵌套循环的符号上界是最容易错的地方
//   3. 输出按行主序 push（不预付长度）
//   4. 只有 target、没有 ?!：矩阵乘法没有"另一条路"更值得测
//      所以也**不写 benchmodel**（MB-11：有 benchmodel 而无 ?! 只是警告，
//      但没有 ?! 就没有可测的候选，写它没有意义）

// #region spec
error MatError;

spec matmul(
            const Vec<u64>& a,
                const Vec<u64>& b, u64 dim) -> Vec<u64>! {
    effects { alloc };

    // 三层循环各跑 dim 次 → dim³ 次乘法；`dim` 是标量形参，上界可写。
    target { time: { max: O(dim * dim * dim) }, allocation: { max: O(dim * dim) }, };
}
// #endregion spec
