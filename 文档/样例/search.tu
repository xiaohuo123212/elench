// search.tu —— 二分查找样例的声明部分
//
// 候选由 choose 声明（写在宿主 spec 里），实现在 search.eln 的 `} kernels` 块里。
// 这份文件可以直接跑：dsh check / dsh show search_impl

// #region spec
error SearchError;

// 二分查找的循环条件。
// 循环体里区间每次至少减半，所以圈数上界是 log2(区间长度)——这是算法性质，
// 无法由条件形态推出（规格 T13 来源①），只能由谓词的 repeats 声明（来源②）。
spec in_range(u64 lo, u64 hi) -> bool {
    effects { };
    repeats log(hi - lo);
}

spec binary_search(
                   const vector<u64>& xs, u64 key) -> u64! {
    effects { alloc };
    requires is_sorted(xs);

    benchmodel {
        data {
            xs = sorted_u64s();
            key = any_of(xs);
        }
        scale {
            xs in range(1, 1048576, *2);
        }
        seed = 1;
    };

    target {
        time: { max: O(log(xs.length())) },
        allocation: { max: 0 },
    }
    ;

    // #region choose
    choose search_impl(const vector<u64>& xs, u64 key) -> u64! {
        over {
            search_linear,
            search_binary,
            search_interp pending
        }
        ;
        objective time;
    };
    // #endregion choose
}
// #endregion spec
