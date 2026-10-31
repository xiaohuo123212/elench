error Boom;

spec main() -> u64! {
    effects {};
    target { time: { max: O(1) }, allocation: { max: 0 }, };
}