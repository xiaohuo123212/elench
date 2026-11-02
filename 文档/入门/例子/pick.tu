error PickError;

spec rank(
          const Vec<u64>& xs) -> u64! {
    effects { };
    benchmodel {
        data {
            xs = random_u64s();
        }
        scale {
            xs in range(1, 4096, *2);
        }
    }
    ;
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
    choose kernel(const Vec<u64>& xs) -> u64! {
        // 候选必须同阶：承诺取 max，混入高阶候选会把 κ 抬到那个阶
        over {
            by_scan,
            by_scan_rev
        }
        ;
        objective time;
    }
    ;
}
