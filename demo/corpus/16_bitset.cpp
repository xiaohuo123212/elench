// 16_bitset.cpp —— 位集合 / 布隆式去重：用 u64 数组当位图，按位或、与、异或。
// 真实场景：事件去重。每条事件算两个哈希位，查位图判断「大概率见过」，没见过就置位并
// 计数。位图是 vector<u64>，一个字存 64 位——容器里做位运算，每条记录只碰常数个位。
#include <cstdio>
#include <vector>

using u64 = unsigned long long;

struct Bits {
    std::vector<u64> words;
};

static void bits_init(Bits& b, u64 nbits) {
    u64 words = (nbits + 63) / 64;   // 向上取整到 64 位的倍数
    for (u64 i = 0; i < words; ++i) b.words.push_back(0);
}

static u64 word_of(u64 i) { return i >> 6; }

// 一位的掩码。字面量后缀（1ULL）方言不收，用局部量兜住移位宽度。
static u64 mask_of(u64 i) {
    u64 one = 1;
    return one << (i & 63);
}

static void bit_set(Bits& b, u64 i) { b.words[word_of(i)] = b.words[word_of(i)] | mask_of(i); }
static void bit_clear(Bits& b, u64 i) { b.words[word_of(i)] = b.words[word_of(i)] & ~mask_of(i); }

static bool bit_get(const Bits& b, u64 i) {
    return (b.words[word_of(i)] & mask_of(i)) != 0;
}

// 数 1 的个数：Kernighan 写法，每次清掉最低的一位，循环次数就是 1 的个数。
static u64 count_bits(const Bits& b) {
    u64 n = 0;
    for (u64 w = 0; w < b.words.size(); ++w) {
        u64 x = b.words[w];
        while (x != 0) {
            x = x & (x - 1);
            n += 1;
        }
    }
    return n;
}

// 异或：两天的事件集合做对称差——「只有其中一天出现过」的记录。
static Bits xor_bits(const Bits& a, const Bits& b) {
    Bits out;
    for (u64 w = 0; w < a.words.size(); ++w) out.words.push_back(a.words[w] ^ b.words[w]);
    return out;
}

static u64 mix64(u64 x) {
    x = x ^ (x >> 33);
    x = x * 3;
    x = x ^ (x >> 29);
    return x;
}

// 布隆式去重：每条事件算两个独立哈希位，两位都命中才算「大概率见过」
// （会有假阳性，但这里只用来省一次真正的查表）。返回「第一次见到」的条数，
// 见过的事件 ID 顺手记进位图。
static u64 dedup_count(const std::vector<u64>& ids, Bits& seen, u64 nbits) {
    u64 fresh = 0;
    for (std::size_t i = 0; i < ids.size(); ++i) {
        u64 h = mix64(ids[i]);
        u64 p = h % nbits;
        u64 q = (h >> 32) % nbits;
        if (bit_get(seen, p) && bit_get(seen, q)) continue;
        bit_set(seen, p);
        bit_set(seen, q);
        fresh += 1;
    }
    return fresh;
}

// 从下标表建一张位图：两次批量导入各一份。
static Bits bits_from(const std::vector<u64>& xs, u64 nbits) {
    Bits b;
    bits_init(b, nbits);
    for (std::size_t i = 0; i < xs.size(); ++i) bit_set(b, xs[i]);
    return b;
}

int main() {
    const u64 kBits = 512;
    Bits seen;
    bits_init(seen, kBits);

    std::vector<u64> ids;
    ids.push_back(1001);
    ids.push_back(1002);
    ids.push_back(1001);
    ids.push_back(1003);
    ids.push_back(1002);
    std::printf("events: %llu, fresh: %llu\n", ids.size(), dedup_count(ids, seen, kBits));
    std::printf("bits set: %llu of %llu\n", count_bits(seen), kBits);

    // 两天的记录集合：模拟两次批量导入。
    std::vector<u64> day1;
    day1.push_back(1);
    day1.push_back(300);
    std::vector<u64> day2;
    day2.push_back(7);
    day2.push_back(300);
    day2.push_back(511);
    Bits monday = bits_from(day1, kBits);
    Bits tuesday = bits_from(day2, kBits);

    std::printf("monday: %llu, tuesday: %llu\n", count_bits(monday), count_bits(tuesday));
    std::printf("only one day: %llu\n", count_bits(xor_bits(monday, tuesday)));

    // 位图边界：清掉最高位（第 511 位），tuesday 应当只剩两个 1。
    bit_clear(tuesday, 511);
    std::printf("after clear 511: %llu\n", count_bits(tuesday));
    if (bit_get(tuesday, 511)) std::printf("511 still set\n");
    else std::printf("511 cleared\n");
    std::printf("words: %llu\n", monday.words.size());
    return 0;
}
