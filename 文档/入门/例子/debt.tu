spec total(const vector<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(xs.size()) }, allocation: { max: 0 }, };
}
