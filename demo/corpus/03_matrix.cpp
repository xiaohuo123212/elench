// 03_matrix.cpp —— 二维容器 + 三重循环（数值内核的骨架）
#include <cstdio>
#include <vector>

using u64 = unsigned long long;
using Mat = std::vector<std::vector<u64>>;

static Mat zeros(std::size_t n) {
    Mat m(n, std::vector<u64>(n, 0));
    return m;
}

static Mat matmul(const Mat& a, const Mat& b) {
    std::size_t n = a.size();
    Mat c = zeros(n);
    for (std::size_t i = 0; i < n; ++i) {
        for (std::size_t k = 0; k < n; ++k) {
            u64 aik = a[i][k];
            for (std::size_t j = 0; j < n; ++j) {
                c[i][j] += aik * b[k][j];
            }
        }
    }
    return c;
}

int main() {
    Mat a = zeros(2);
    Mat b = zeros(2);
    a[0][0] = 1; a[0][1] = 2; a[1][0] = 3; a[1][1] = 4;
    b[0][0] = 1; b[1][0] = 1; b[0][1] = 0; b[1][1] = 1;
    Mat c = matmul(a, b);
    std::printf("%llu %llu %llu %llu\n", c[0][0], c[0][1], c[1][0], c[1][1]);
    return 0;
}
