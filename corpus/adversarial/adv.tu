spec partition(vector<u64>& xs, u64 lo, u64 hi) -> u64 { requires hi <= xs.size(); effects { }; target { time: { max: O(xs.size()) }, allocation: { max: 0 }, }; }
spec two_pointer_dedup(vector<u64>& xs) -> u64 { effects { }; target { time: { max: O(n) }, allocation: { max: 0 }, }; }
spec linear_probe(vector<u64>& table, u64 key, u64 cap) -> u64 { effects { }; target { time: { max: O(1) }, allocation: { max: 0 }, }; }
spec sliding_sum_max(vector<u64>& xs, u64 k) -> u64 { requires k <= xs.size(); effects { }; target { time: { max: O(n) }, allocation: { max: 0 }, }; }
spec heapify_up(vector<u64>& h, u64 i) -> u64 { requires i <= h.size(); effects { }; target { time: { max: O(log(h.size())) }, allocation: { max: 0 }, }; }
spec gcd_loop(u64 a, u64 b) -> u64 { effects { }; target { time: { max: O(log(a)) }, allocation: { max: 0 }, }; }
spec probe_len(vector<u64>& table, u64 key) -> u64 { effects { }; target { time: { max: O(table.size()) }, allocation: { max: 0 }, }; }
