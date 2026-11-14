// 07_dp.cpp —— 二维动态规划：编辑距离与最长公共子序列。
// 真实场景：拼写纠错、日志行对齐。两张表都按 (i, j) 逐格填，
// 第 0 行 / 第 0 列是边界（空串变成前 i 个字符的代价）。
#include <cstdio>
#include <string>
#include <vector>

using u64 = unsigned long long;

static u64 min3(u64 a, u64 b, u64 c) {
    u64 m = a;
    if (b < m) m = b;
    if (c < m) m = c;
    return m;
}

// dp[i][j] = a 的前 i 个字符变成 b 的前 j 个字符的最少操作数。
static u64 edit_distance(const std::string& a, const std::string& b) {
    const std::size_t n = a.size();
    const std::size_t m = b.size();
    std::vector<std::vector<u64>> dp(n + 1, std::vector<u64>(m + 1, 0));
    for (std::size_t i = 0; i <= n; ++i) dp[i][0] = i;
    for (std::size_t j = 0; j <= m; ++j) dp[0][j] = j;
    for (std::size_t i = 1; i <= n; ++i) {
        for (std::size_t j = 1; j <= m; ++j) {
            u64 cost = 0;
            if (a[i - 1] != b[j - 1]) cost = 1;
            u64 del = dp[i - 1][j] + 1;
            u64 ins = dp[i][j - 1] + 1;
            u64 sub = dp[i - 1][j - 1] + cost;
            dp[i][j] = min3(del, ins, sub);
        }
    }
    return dp[n][m];
}

// dp[i][j] = a 的前 i 个字符与 b 的前 j 个字符的最长公共子序列长度。
static u64 lcs_length(const std::string& a, const std::string& b) {
    const std::size_t n = a.size();
    const std::size_t m = b.size();
    std::vector<std::vector<u64>> dp(n + 1, std::vector<u64>(m + 1, 0));
    for (std::size_t i = 1; i <= n; ++i) {
        for (std::size_t j = 1; j <= m; ++j) {
            if (a[i - 1] == b[j - 1]) {
                dp[i][j] = dp[i - 1][j - 1] + 1;
            } else {
                u64 up = dp[i - 1][j];
                u64 left = dp[i][j - 1];
                if (up > left) dp[i][j] = up;
                else dp[i][j] = left;
            }
        }
    }
    return dp[n][m];
}

int main() {
    std::string a = "kitten";
    std::string b = "sitting";
    std::string c = "flaw";
    std::string d = "lawn";

    u64 ed1 = edit_distance(a, b);
    u64 ed2 = edit_distance(c, d);
    u64 lcs = lcs_length(a, b);

    std::printf("edit(kitten,sitting) = %llu\n", ed1);
    std::printf("edit(flaw,lawn) = %llu\n", ed2);
    std::printf("lcs(kitten,sitting) = %llu\n", lcs);
    return 0;
}
