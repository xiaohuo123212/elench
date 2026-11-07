// sum.tu —— 求和样例的声明部分
//
// 用途：演示「两个候选复杂度相同（都是 O(n)），成本模型分不出高下，只能靠测量分档」。
// 与 search 样例分工：search 是不同阶（n vs log n），讲的是"取最慢候选 → 债务"；
// 这份是同阶，讲的是"dispatch 表为什么必须存在"。

// #region spec
error CalcError;

spec average(
             const vector<u8>& scores) -> u64! {
    effects { alloc };

    benchmodel {
        data {
            scores = make_u8s();
        }
        scale {
            scores in range(1, 1048576, *2);
        }
        seed = 1;
    };

    target { time: { max: O(scores.length()) }, allocation: { max: 0 }, };

    // #region choose
    choose sum_kernel(const vector<u8>& scores) -> u64! {
        over {
            sum_by_index,
            sum_by_iter
        }
        ;
        objective time;
    };
    // #endregion choose
}
// #endregion spec
