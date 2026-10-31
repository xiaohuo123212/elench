spec main() -> u64 {
    effects { io };
    target { time: { max: O(1) }, allocation: { max: 0 }, };
}
