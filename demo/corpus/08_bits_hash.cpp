// 08_bits_hash.cpp —— 位运算 + 哈希表：把一批设备 ID 按高 8 位分桶，
// 同时统计命令词出现次数（unordered_map<string, u64> 是 C++ 里最常见的计数器）。
// 真实场景：设备上报的 64 位 ID 里，高字节是机房号，低字节是序列号。
#include <cstdio>
#include <string>
#include <unordered_map>
#include <vector>

using u64 = unsigned long long;

// 数 1 的个数：固定 64 次，不依赖编译器内建。
static u64 popcount64(u64 x) {
    u64 n = 0;
    for (u64 k = 0; k < 64; ++k) {
        if ((x >> k) & 1) n += 1;
    }
    return n;
}

// 循环左移，r >= 64 时先取模。
static u64 rotl64(u64 x, u64 r) {
    r = r % 64;
    if (r == 0) return x;
    return (x << r) | (x >> (64 - r));
}

// 一个便宜的混合函数，让相邻 ID 落到不同桶。
static u64 mix64(u64 x) {
    x = x ^ (x >> 33);
    x = x * 3;
    x = x ^ (x >> 29);
    return x;
}

static u64 bucket_of(u64 key) {
    return (key >> 56) & 255;
}

static void count_words(const std::vector<std::string>& words,
                        std::unordered_map<std::string, u64>& freq) {
    for (const auto& w : words) {
        freq[w] += 1;
    }
}

int main() {
    // 0x0123456789ABCDEF / 0x0100000000000001 / 0xFF00000000000000
    std::vector<u64> ids;
    ids.push_back(81985529216486895);
    ids.push_back(72057594037927937);
    ids.push_back(18374686479671623680);

    u64 ones = 0;
    for (auto id : ids) {
        ones += popcount64(id);
    }
    u64 mixed = mix64(ids[0]);
    u64 rot = rotl64(ids[1], 12);
    std::printf("ones: %llu\n", ones);
    std::printf("mix: %llu, rot: %llu\n", mixed, rot);
    std::printf("buckets: %llu %llu %llu\n",
                bucket_of(ids[0]), bucket_of(ids[1]), bucket_of(ids[2]));

    std::vector<std::string> cmds;
    cmds.push_back("get");
    cmds.push_back("put");
    cmds.push_back("get");
    cmds.push_back("del");
    cmds.push_back("get");
    cmds.push_back("put");
    std::unordered_map<std::string, u64> freq;
    count_words(cmds, freq);
    for (std::unordered_map<std::string, u64>::iterator it = freq.begin();
         it != freq.end(); ++it) {
        std::printf("%s -> %llu\n", it->first.c_str(), it->second);
    }
    return 0;
}
