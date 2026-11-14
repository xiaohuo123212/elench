// quantity —— 带单位的物理量（定点表示）：value 是整数，scale 是 10 的幂次。
// 覆盖点：值语义 + 多字段 struct + 断言式校验 + 加减乘除 + 比较 + 单位换算。
#include <cstdio>
#include <string>

typedef long long i64;

const int MAX_SCALE = 9;               // 定点表示最多 9 位小数

// 错误码（不用异常：运算失败就返回码）
const int E_NONE = 0;
const int E_SCALE = 1;                 // 精度越界
const int E_DIVZERO = 2;               // 除零
const int E_NOTVALID = 3;              // 参与运算的量本身非法

struct Quantity {
    i64 value;                         // 以 10^-scale 为单位的整数
    int scale;                         // 小数点后位数

    bool valid() const { return scale >= 0 && scale <= MAX_SCALE; }

    i64 pow10(int k) const {
        i64 unit = 1;
        for (int i = 0; i < k; ++i) {
            unit *= 10;
        }
        return unit;
    }

    // 取反：精度不变
    Quantity negated() const {
        Quantity r;
        r.value = -value;
        r.scale = scale;
        return r;
    }

    // 和另一个量比大小：对齐后比整数部分，返回 -1 / 0 / 1。
    int compare(const Quantity& o) const {
        int s = scale > o.scale ? scale : o.scale;
        i64 x = value * pow10(s - scale);
        i64 y = o.value * o.pow10(s - o.scale);
        if (x < y) {
            return -1;
        }
        if (x > y) {
            return 1;
        }
        return 0;
    }
};

// 一次运算的结果：成功就带着量，失败就带着错误码。
struct QResult {
    bool ok;
    int code;
    Quantity q;
};

static QResult fail(int code) {
    QResult r;
    r.ok = false;
    r.code = code;
    r.q.value = 0;
    r.q.scale = 0;
    return r;
}

static QResult good(i64 value, int scale) {
    QResult r;
    r.ok = true;
    r.code = E_NONE;
    r.q.value = value;
    r.q.scale = scale;
    return r;
}

// 换到目标精度：放大是精确的，缩小按四舍五入（远离零）。
static QResult rescale(const Quantity& a, int target) {
    if (!a.valid() || target < 0 || target > MAX_SCALE) {
        return fail(E_SCALE);
    }
    QResult r;
    if (target >= a.scale) {
        r = good(a.value * a.pow10(target - a.scale), target);
    } else {
        i64 unit = a.pow10(a.scale - target);
        i64 half = unit / 2;
        i64 v = 0;
        if (a.value >= 0) {
            v = (a.value + half) / unit;
        } else {
            v = -((-a.value + half) / unit);
        }
        r = good(v, target);
    }
    return r;
}

// 加减：先对齐到较大精度再加。
static QResult add(const Quantity& a, const Quantity& b) {
    if (!a.valid() || !b.valid()) {
        return fail(E_NOTVALID);
    }
    int s = a.scale > b.scale ? a.scale : b.scale;
    QResult x = rescale(a, s);
    QResult y = rescale(b, s);
    if (!x.ok || !y.ok) {
        return fail(E_SCALE);
    }
    return good(x.q.value + y.q.value, s);
}

static QResult sub(const Quantity& a, const Quantity& b) {
    return add(a, b.negated());
}

// 乘：精度相加。
static QResult mul(const Quantity& a, const Quantity& b) {
    if (!a.valid() || !b.valid()) {
        return fail(E_NOTVALID);
    }
    int s = a.scale + b.scale;
    if (s > MAX_SCALE) {
        return fail(E_SCALE);
    }
    return good(a.value * b.value, s);
}

// 除以一个整数：除零是错误码，不是异常。
static QResult div_int(const Quantity& a, i64 d) {
    if (!a.valid()) {
        return fail(E_NOTVALID);
    }
    if (d == 0) {
        return fail(E_DIVZERO);
    }
    return good(a.value / d, a.scale);
}

// 定点渲染：不依赖 printf 的宽度 / 精度，自己补前导零。
static void print_q(const std::string& tag, const Quantity& q) {
    i64 unit = q.pow10(q.scale);
    i64 abs_v = q.value;
    if (abs_v < 0) {
        abs_v = -abs_v;
    }
    i64 ip = abs_v / unit;
    i64 fp = abs_v % unit;
    std::printf("%s = ", tag.c_str());
    if (q.value < 0 && abs_v != 0) {
        std::printf("-");
    }
    std::printf("%lld", ip);
    if (q.scale > 0) {
        std::printf(".");
        i64 u = unit / 10;
        while (u > 0) {
            std::printf("%lld", (fp / u) % 10);
            u /= 10;
        }
    }
    std::printf(" (value=%lld scale=%d)\n", q.value, q.scale);
}

// 断言式校验：失败留一条记录，最后汇总。
static void check(bool cond, const std::string& what, int& passed, int& failed) {
    if (cond) {
        passed += 1;
    } else {
        failed += 1;
        std::printf("FAIL: %s\n", what.c_str());
    }
}

int main() {
    int passed = 0;
    int failed = 0;

    Quantity a;
    a.value = 12345;
    a.scale = 2;                       // 123.45
    Quantity b;
    b.value = 6789;
    b.scale = 1;                       // 678.9
    print_q("a", a);
    print_q("b", b);
    check(a.valid() && b.valid(), "both quantities are valid", passed, failed);

    QResult s = add(a, b);
    check(s.ok, "add ok", passed, failed);
    print_q("a+b", s.q);
    check(s.q.scale == 2, "add keeps the finer scale", passed, failed);
    check(s.q.value == 12345 + 67890, "add aligns before adding", passed, failed);

    QResult d = sub(b, a);
    check(d.ok, "sub ok", passed, failed);
    check(d.q.value == 67890 - 12345, "sub aligns before subtracting", passed, failed);
    print_q("b-a", d.q);

    QResult p = mul(a, b);
    check(p.ok, "mul ok", passed, failed);
    check(p.q.scale == 3, "mul adds scales", passed, failed);
    print_q("a*b", p.q);

    QResult q4 = div_int(a, 4);
    check(q4.ok, "div ok", passed, failed);
    print_q("a/4", q4.q);
    QResult z = div_int(a, 0);
    check(!z.ok && z.code == E_DIVZERO, "divide by zero reports E_DIVZERO", passed, failed);
    check(z.q.value == 0, "failed result carries a zero quantity", passed, failed);

    // 单位换算：精度 2 → 4（放大精确），2 → 0（四舍五入）
    QResult finer = rescale(a, 4);
    check(finer.ok, "rescale up ok", passed, failed);
    check(finer.q.value == 1234500, "rescaling up is exact", passed, failed);
    print_q("a at scale 4", finer.q);
    QResult coarse = rescale(a, 0);
    check(coarse.ok, "rescale down ok", passed, failed);
    check(coarse.q.value == 123, "rescaling down rounds", passed, failed);
    print_q("a at scale 0", coarse.q);

    check(a.compare(b) < 0, "a < b", passed, failed);
    check(b.compare(a) > 0, "b > a", passed, failed);
    Quantity copy = a;
    check(copy.compare(a) == 0, "copy compares equal", passed, failed);
    copy.value = 999;
    check(a.value == 12345, "copy is a value, not a reference", passed, failed);

    Quantity n;
    n.value = -405;
    n.scale = 1;
    print_q("neg", n);
    QResult naive_add = add(n, a);
    check(naive_add.ok, "negative add ok", passed, failed);
    print_q("neg+a", naive_add.q);
    check(naive_add.q.value == -4050 + 12345, "negative add aligns too", passed, failed);

    Quantity bad;
    bad.value = 1;
    bad.scale = 12;
    check(!bad.valid(), "scale 12 is rejected by valid()", passed, failed);
    check(!rescale(bad, 2).ok, "rescale of an invalid quantity fails", passed, failed);
    check(!add(bad, a).ok, "add with an invalid quantity fails", passed, failed);
    check(!mul(a, bad).ok, "mul with an invalid quantity fails", passed, failed);

    std::printf("checks: %d passed, %d failed\n", passed, failed);
    if (failed == 0) {
        return 0;
    }
    return 1;
}
