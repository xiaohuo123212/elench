// ring/main.cpp —— 环形缓冲：读写指针回绕 + 容量判定 + 按批写入。
//
// 库风格：满/空判定用 count 而不是 head/tail 相等（这样容量能占满）、
// 所有写入都走 push（满了返回 false）、批量写入按批截断并如实返回写入条数。
#include <cstdio>
#include <vector>

using u64 = unsigned long long;

struct Ring {
    std::vector<u64> buf;
    u64 head;    // 下一个要读的位置
    u64 count;   // 当前元素个数
    u64 dropped; // 因为满而丢掉的条数

    explicit Ring(u64 cap) : buf(cap, 0), head(0), count(0), dropped(0) {}

    u64 capacity() const { return buf.size(); }
    bool empty() const { return count == 0; }
    bool full() const { return count == buf.size(); }

    // 写入一条；满了丢最旧的一条还是拒绝？这里选择拒绝并计数（调用方能看见）。
    bool push(u64 v) {
        if (full()) {
            dropped += 1;
            return false;
        }
        const u64 tail = (head + count) % buf.size();
        buf[tail] = v;
        count += 1;
        return true;
    }

    // 读一条；空时返回 0 并且不动指针（调用方该先看 empty()）。
    u64 pop() {
        if (empty()) return 0;
        const u64 v = buf[head];
        head = (head + 1) % buf.size();
        count -= 1;
        return v;
    }

    // 按批写入：能塞多少塞多少，返回真正写进去的条数。
    u64 write_batch(const std::vector<u64>& vs) {
        u64 n = 0;
        for (auto v : vs) {
            if (!push(v)) break;
            n += 1;
        }
        return n;
    }

    // 按批读出：最多 n 条，返回实际读到的序列。
    std::vector<u64> read_batch(u64 n) {
        std::vector<u64> out;
        for (u64 i = 0; i < n && !empty(); ++i) out.push_back(pop());
        return out;
    }

    // 不变式：count 在 [0, cap]、head 在 [0, cap)、不变量成立就没有越界。
    bool invariant() const {
        return count <= buf.size() && head < buf.size() && buf.size() > 0;
    }
};

static u64 sum_of(const std::vector<u64>& xs) {
    u64 s = 0;
    for (auto x : xs) s += x;
    return s;
}

int main() {
    Ring r(5);
    std::printf("cap=%llu empty=%d full=%d\n", r.capacity(), r.empty() ? 1 : 0,
                r.full() ? 1 : 0);

    for (u64 i = 1; i <= 5; ++i) r.push(i);
    std::printf("after 5 pushes: count=%llu full=%d invariant=%d\n", r.count,
                r.full() ? 1 : 0, r.invariant() ? 1 : 0);
    // 注意：不要在同一条 printf 里先 push 再读 r.dropped —— 实参求值顺序未指定。
    const bool ok6 = r.push(6);
    std::printf("overflow push -> %d dropped=%llu\n", ok6 ? 1 : 0, r.dropped);

    // 回绕：读两条再写两条，tail 一定会绕过 0。
    const u64 p1 = r.pop();
    const u64 p2 = r.pop();
    std::printf("pop=%llu pop=%llu head=%llu count=%llu\n", p1, p2, r.head, r.count);
    r.push(60);
    r.push(70);
    std::printf("wrapped: head=%llu count=%llu invariant=%d\n", r.head, r.count,
                r.invariant() ? 1 : 0);

    Ring b(4);
    std::vector<u64> batch;
    batch.push_back(11);
    batch.push_back(22);
    batch.push_back(33);
    batch.push_back(44);
    batch.push_back(55);
    batch.push_back(66);
    const u64 wrote = b.write_batch(batch);
    std::printf("batch written=%llu dropped=%llu\n", wrote, b.dropped);

    std::vector<u64> got = b.read_batch(3);
    std::printf("read %d: sum=%llu\n", (int)got.size(), sum_of(got));
    // 同一条 printf 里先读 count 再 pop 是未指定求值顺序（两个编译器给的值不同），
    // 先存下来才叫确定输出。
    const u64 left = b.count;
    const u64 nxt = b.pop();
    std::printf("left count=%llu next=%llu\n", left, nxt);
    return 0;
}
