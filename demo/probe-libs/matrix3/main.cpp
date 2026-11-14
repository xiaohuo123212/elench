// matrix3 —— 3×3 整数矩阵：乘法 / 转置 / 行列式 / 单位阵。
// 覆盖点：定长数组嵌套（a[N][N]）、三层循环、中间量、成员函数、文件级常量。
#include <cstdio>
#include <string>

typedef long long i64;

const int N = 3;                       // 阶数：文件级常量，贯穿所有函数

struct Mat3 {
    i64 a[N][N];

    void fill(i64 v) {
        for (int r = 0; r < N; ++r) {
            for (int c = 0; c < N; ++c) {
                a[r][c] = v;
            }
        }
    }

    void set(int r, int c, i64 v) { a[r][c] = v; }
    i64 at(int r, int c) const { return a[r][c]; }

    i64 trace() const {
        i64 s = 0;
        for (int r = 0; r < N; ++r) {
            s += a[r][r];
        }
        return s;
    }

    Mat3 transposed() const {
        Mat3 t;
        t.fill(0);
        for (int r = 0; r < N; ++r) {
            for (int c = 0; c < N; ++c) {
                t.a[c][r] = a[r][c];
            }
        }
        return t;
    }

    // 行列式：沿第一行做余子式展开。第 c 列的余子式吃掉「跳过 c 之后
    // 剩下的两列」，这两列必须按原来的先后次序排，否则符号会翻错。
    i64 det() const {
        i64 total = 0;
        for (int c = 0; c < N; ++c) {
            int cols[2];
            int k = 0;
            for (int j = 0; j < N; ++j) {
                if (j != c) {
                    cols[k] = j;
                    k += 1;
                }
            }
            i64 minor = a[1][cols[0]] * a[2][cols[1]] - a[1][cols[1]] * a[2][cols[0]];
            i64 term = a[0][c] * minor;
            if (c % 2 == 0) {
                total += term;
            } else {
                total -= term;
            }
        }
        return total;
    }

    bool equals(const Mat3& o) const {
        for (int r = 0; r < N; ++r) {
            for (int c = 0; c < N; ++c) {
                if (a[r][c] != o.a[r][c]) {
                    return false;
                }
            }
        }
        return true;
    }
};

static Mat3 identity() {
    Mat3 m;
    m.fill(0);
    for (int r = 0; r < N; ++r) {
        m.set(r, r, 1);
    }
    return m;
}

// 矩阵乘法：三层循环，中间量 xik 提到最内层外面，免得重复取值。
static Mat3 mul(const Mat3& x, const Mat3& y) {
    Mat3 out;
    out.fill(0);
    for (int r = 0; r < N; ++r) {
        for (int k = 0; k < N; ++k) {
            i64 xik = x.at(r, k);
            for (int c = 0; c < N; ++c) {
                out.a[r][c] += xik * y.at(k, c);
            }
        }
    }
    return out;
}

static void dump(const std::string& tag, const Mat3& m) {
    std::printf("%s\n", tag.c_str());
    for (int r = 0; r < N; ++r) {
        std::printf("  %lld   %lld   %lld\n", m.at(r, 0), m.at(r, 1), m.at(r, 2));
    }
}

int main() {
    Mat3 m;
    m.fill(0);
    m.set(0, 0, 2); m.set(0, 1, -1); m.set(0, 2, 3);
    m.set(1, 0, 0); m.set(1, 1, 4); m.set(1, 2, 1);
    m.set(2, 0, 5); m.set(2, 1, 2); m.set(2, 2, -2);
    dump("M =", m);
    std::printf("trace(M) = %lld\n", m.trace());
    std::printf("det(M)   = %lld\n", m.det());

    Mat3 t = m.transposed();
    dump("M^T =", t);
    std::printf("det(M^T) = %lld\n", t.det());

    Mat3 id = identity();
    Mat3 mi = mul(m, id);
    std::printf("det(I)   = %lld\n", id.det());
    std::printf("trace(I) = %lld\n", id.trace());
    if (mi.equals(m)) {
        std::printf("M * I == M\n");
    } else {
        std::printf("M * I != M\n");
    }

    Mat3 mmt = mul(m, t);
    dump("M * M^T =", mmt);
    std::printf("trace(M*M^T) = %lld\n", mmt.trace());

    // 上三角矩阵：det 应当就是对角线乘积 1
    Mat3 u;
    u.fill(0);
    u.set(0, 0, 1); u.set(0, 1, 2); u.set(0, 2, 3);
    u.set(1, 0, 0); u.set(1, 1, 1); u.set(1, 2, 2);
    u.set(2, 0, 0); u.set(2, 1, 0); u.set(2, 2, 1);
    std::printf("det(U)   = %lld (expect 1)\n", u.det());
    std::printf("trace(U) = %lld (expect 3)\n", u.trace());
    if (u.equals(id)) {
        std::printf("U == I\n");
    } else {
        std::printf("U != I (det alone does not decide)\n");
    }
    return 0;
}
