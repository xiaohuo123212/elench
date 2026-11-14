// 14_buffer.cpp —— 字节缓冲：追加、子串查找、替换（脱敏）、切片、可打印预览。
// 真实场景：把几段定长帧拼成一条记录，再把里面的密钥字段替换掉，最后打印摘要。
// 缓冲是 vector<unsigned char>，按字节改，不是 std::string。
#include <cstdio>
#include <string>
#include <vector>

using u64 = unsigned long long;
using u8 = unsigned char;
using Buf = std::vector<u8>;

static Buf from_text(const std::string& s) {
    Buf b;
    for (char c : s) b.push_back(c);
    return b;
}

static void append_text(Buf& b, const std::string& s) {
    for (char c : s) b.push_back(c);
}

// 把 src[lo, hi) 追加到 b 尾巴上。
static void append_range(Buf& b, const Buf& src, std::size_t lo, std::size_t hi) {
    for (std::size_t i = lo; i < hi; ++i) b.push_back(src[i]);
}

// 从 from 开始找 needle，返回起始下标；找不到返回 hay.size()
// （下标等于长度就是「不存在」，和 npos == n 一个意思）。
static u64 find_from(const Buf& hay, const Buf& needle, std::size_t from) {
    if (needle.size() == 0) return from;
    for (std::size_t i = from; i + needle.size() <= hay.size(); ++i) {
        bool ok = true;
        for (std::size_t k = 0; k < needle.size(); ++k) {
            if (hay[i + k] != needle[k]) ok = false;
        }
        if (ok) return i;
    }
    return hay.size();
}

static u64 count_byte(const Buf& b, u8 v) {
    u64 n = 0;
    for (std::size_t i = 0; i < b.size(); ++i) {
        if (b[i] == v) n += 1;
    }
    return n;
}

// 数 needle 出现了几次（不重叠）。每命中一次至少前进 needle.size() 个字节。
static u64 count_occurrences(const Buf& hay, const Buf& needle) {
    if (needle.size() == 0) return 0;
    u64 hits = 0;
    std::size_t i = 0;
    for (std::size_t step = 0; step < hay.size(); ++step) {
        u64 at = find_from(hay, needle, i);
        if (at == hay.size()) break;
        hits += 1;
        i = at + needle.size();
    }
    return hits;
}

static Buf slice(const Buf& b, std::size_t lo, std::size_t hi) {
    Buf out;
    append_range(out, b, lo, hi);
    return out;
}

// 把所有 from 换成 to，返回新缓冲。
static Buf replace_all(const Buf& src, const Buf& from, const Buf& to) {
    Buf out;
    std::size_t i = 0;
    for (std::size_t step = 0; step < src.size(); ++step) {
        u64 at = find_from(src, from, i);
        if (at == src.size()) break;
        append_range(out, src, i, at);
        append_range(out, to, 0, to.size());
        i = at + from.size();
    }
    append_range(out, src, i, src.size());
    return out;
}

// 调试预览：可打印字符原样打，其余打点（线上日志里最常见的写法）。
static void print_preview(const Buf& b) {
    for (std::size_t i = 0; i < b.size(); ++i) {
        u8 c = b[i];
        if (c >= ' ' && c <= '~') std::printf("%c", c);
        else std::printf(".");
    }
    std::printf("\n");
}

int main() {
    Buf frame;
    append_text(frame, "GET /health ");
    append_text(frame, "key=SECRET123 ");
    append_text(frame, "ua=curl/8");
    std::printf("size: %llu\n", frame.size());

    Buf key = from_text("key=");
    Buf secret = from_text("SECRET123");
    std::printf("key= at %llu\n", find_from(frame, key, 0));
    std::printf("secret at %llu, missing at %llu\n", find_from(frame, secret, 0), find_from(frame, from_text("PUT"), 0));

    // 脱敏：SECRET123 换成同长度星号（长度不变，下游字段偏移不用改）。
    Buf stars = from_text("*********");
    u64 hits = count_occurrences(frame, secret);
    Buf masked = replace_all(frame, secret, stars);
    std::printf("replaced: %llu, masked: %llu bytes\n", hits, masked.size());
    std::printf("stars: %llu\n", count_byte(masked, '*'));

    // 头 11 个字节是请求行，后面是参数；切开分别看。
    Buf head = slice(masked, 0, 11);
    Buf tail = slice(masked, 11, masked.size());
    std::printf("head: %llu bytes, tail: %llu bytes\n", head.size(), tail.size());
    print_preview(head);
    print_preview(tail);
    return 0;
}
