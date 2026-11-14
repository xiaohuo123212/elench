// bitvec —— 位向量：按 u64 字打包，set/clear/test/count/popcount + 跨字区间操作。
// 覆盖点：位运算密集 + 掩码 + 下标换算（位下标 ↔ 字第 + 字内偏移）。
#include <cstdio>
#include <vector>

typedef unsigned long long u64;

// 低 n 位全 1（n ≤ 64）；n == 64 时不能写 1 << 64。
static u64 low_mask(u64 n) {
    u64 all = ~0ULL;
    if (n >= 64) {
        return all;
    }
    u64 one = 1;
    return (one << n) - 1;
}

// 一个字里 [from, to) 的掩码（from ≤ 63，to ≤ 64）。
static u64 range_mask(u64 from, u64 to) {
    if (from >= to) {
        return 0;
    }
    u64 inner = low_mask(to) & (low_mask(64 - from) << from);
    return inner;
}

// Kernighan：每次清掉最低的一个 1，循环轮数就是 1 的个数。
static u64 popcount(u64 x) {
    u64 n = 0;
    while (x != 0) {
        x = x & (x - 1);
        n += 1;
    }
    return n;
}

struct BitVec {
    std::vector<u64> words;
    u64 nbits;

    void init(u64 n) {
        nbits = n;
        u64 w = (n + 63) >> 6;
        words.clear();
        for (u64 i = 0; i < w; ++i) {
            words.push_back(0);
        }
    }

    // 位下标 → 字第 / 字内偏移；这两个换算是整个库的地基。
    u64 word_of(u64 i) const { return i >> 6; }
    u64 bit_of(u64 i) const { return i & 63; }

    u64 mask_of(u64 i) const {
        u64 one = 1;
        return one << bit_of(i);
    }

    void set(u64 i) { words[word_of(i)] |= mask_of(i); }
    void clear(u64 i) { words[word_of(i)] &= ~mask_of(i); }
    void flip(u64 i) { words[word_of(i)] ^= mask_of(i); }
    bool test(u64 i) const { return (words[word_of(i)] & mask_of(i)) != 0; }

    // 整字取反（按 nbits 截断，末字多出来的位清零）
    void flip_all() {
        for (u64 w = 0; w < words.size(); ++w) {
            words[w] = ~words[w];
        }
        u64 tail = nbits & 63;
        if (tail != 0 && words.size() > 0) {
            u64 last = words.size() - 1;
            words[last] = words[last] & low_mask(tail);
        }
    }

    u64 count() const {
        u64 n = 0;
        for (u64 w = 0; w < words.size(); ++w) {
            n += popcount(words[w]);
        }
        return n;
    }

    // 跨字区间置位：首字补掩码、中间整字直接全 1、尾字补掩码。
    void set_range(u64 lo, u64 hi) {
        if (lo >= hi) {
            return;
        }
        u64 wl = lo >> 6;
        u64 wr = (hi - 1) >> 6;
        u64 tail = ((hi - 1) & 63) + 1;
        if (wl == wr) {
            words[wl] |= range_mask(lo & 63, tail);
            return;
        }
        words[wl] |= range_mask(lo & 63, 64);
        for (u64 w = wl + 1; w < wr; ++w) {
            words[w] = ~0ULL;
        }
        words[wr] |= range_mask(0, tail);
    }

    void clear_range(u64 lo, u64 hi) {
        if (lo >= hi) {
            return;
        }
        u64 wl = lo >> 6;
        u64 wr = (hi - 1) >> 6;
        u64 tail = ((hi - 1) & 63) + 1;
        if (wl == wr) {
            words[wl] &= ~range_mask(lo & 63, tail);
            return;
        }
        words[wl] &= ~range_mask(lo & 63, 64);
        for (u64 w = wl + 1; w < wr; ++w) {
            words[w] = 0;
        }
        words[wr] &= ~range_mask(0, tail);
    }

    // 区间内 1 的个数：同样按首字 / 整字 / 尾字三段走。
    u64 count_range(u64 lo, u64 hi) const {
        if (lo >= hi) {
            return 0;
        }
        u64 wl = lo >> 6;
        u64 wr = (hi - 1) >> 6;
        u64 tail = ((hi - 1) & 63) + 1;
        if (wl == wr) {
            return popcount(words[wl] & range_mask(lo & 63, tail));
        }
        u64 n = popcount(words[wl] & range_mask(lo & 63, 64));
        for (u64 w = wl + 1; w < wr; ++w) {
            n += popcount(words[w]);
        }
        n += popcount(words[wr] & range_mask(0, tail));
        return n;
    }

    // 展开成下标表：每次取最低的 1，它的位置就是 low 之后的 1 的个数。
    std::vector<u64> to_indices() const {
        std::vector<u64> out;
        for (u64 w = 0; w < words.size(); ++w) {
            u64 x = words[w];
            while (x != 0) {
                u64 low = x & (~x + 1);
                u64 off = popcount(low - 1);
                out.push_back((w << 6) + off);
                x = x & (x - 1);
            }
        }
        return out;
    }
};

static void dump(const char* tag, const BitVec& b) {
    std::printf("%s: words=%lld count=%lld\n", tag, b.words.size(), b.count());
}

int main() {
    BitVec b;
    b.init(200);
    dump("empty", b);

    b.set(0);
    b.set(63);
    b.set(64);
    b.set(199);
    std::printf("after 4 sets: %lld\n", b.count());
    std::printf("bit 0=%d 1=%d 63=%d 64=%d 199=%d 200-slot=%d\n",
                b.test(0), b.test(1), b.test(63), b.test(64), b.test(199), b.test(198));
    std::printf("words of 0/63/64/199 = %lld %lld %lld %lld\n",
                b.word_of(0), b.word_of(63), b.word_of(64), b.word_of(199));
    std::printf("bits of 0/63/64/199 = %lld %lld %lld %lld\n",
                b.bit_of(0), b.bit_of(63), b.bit_of(64), b.bit_of(199));

    b.clear(63);
    std::printf("after clear 63: %lld\n", b.count());
    b.flip(64);
    std::printf("after flip 64: %lld (bit 64 now %d)\n", b.count(), b.test(64));

    // 跨字置位：60..70 跨 word0/word1
    BitVec c;
    c.init(200);
    c.set_range(60, 70);
    std::printf("set_range(60,70): count=%lld cross=%lld\n", c.count(), c.count_range(60, 70));
    std::printf("  count_range(0,60)=%lld  (60,64)=%lld  (64,70)=%lld  (70,200)=%lld\n",
                c.count_range(0, 60), c.count_range(60, 64),
                c.count_range(64, 70), c.count_range(70, 200));
    c.set_range(0, 1);
    c.set_range(199, 200);
    std::printf("plus the two ends: count=%lld\n", c.count());
    c.flip_all();
    std::printf("flip_all: count=%lld (expect 200-12=188)\n", c.count());
    c.flip_all();
    std::printf("flip twice: count=%lld (expect 12)\n", c.count());

    c.clear_range(60, 70);
    std::printf("clear_range(60,70): count=%lld (expect 2)\n", c.count());

    // 全量置位再逐位清：掩码在整字 / 半字 / 单字边界都要对
    BitVec d;
    d.init(130);
    d.set_range(0, 130);
    std::printf("full: count=%lld words=%lld tail_mask=%lld\n",
                d.count(), d.words.size(), low_mask(2));
    d.clear_range(0, 128);
    std::printf("after clear 0..128: count=%lld\n", d.count());
    d.clear_range(128, 130);
    std::printf("after clear 128..130: count=%lld\n", d.count());

    BitVec e;
    e.init(64);
    e.set(3);
    e.set(5);
    e.set(62);
    std::printf("sparse: count=%lld indices:\n", e.count());
    std::vector<u64> idx = e.to_indices();
    for (std::size_t k = 0; k < idx.size(); ++k) {
        std::printf("  idx[%lld] = %lld\n", k, idx[k]);
    }
    std::printf("popcount(0)=%lld (1)=%lld (255)=%lld (all)=%lld\n",
                popcount(0), popcount(1), popcount(255), popcount(~0ULL));
    return 0;
}
