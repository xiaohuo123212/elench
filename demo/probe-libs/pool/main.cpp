// pool/main.cpp —— 定长对象池：手写 free list（下标当指针）+ 容量耗尽的哨兵。
//
// 写法都是库风格：struct + 成员函数、静态辅助函数、不抛异常（失败靠哨兵返回值）、
// 初始化列表里填满 vector、释放链复用计数。
#include <cstdio>
#include <vector>

using u64 = unsigned long long;

//: 哨兵：空闲链末尾 / 分配失败。用 int 而不是 size_t，好让 -1 有意义。
static const int NIL = -1;

// 池里的一个槽。next 是「下一个空闲槽」的下标，NIL 表示链尾。
struct Slot {
    u64 payload;
    int next;
    u64 gen;     // 被分配过几次；gen > 0 的槽再次被分配 = 复用
};

// 定长对象池：alloc 从 free list 头取，release 塞回头部。
struct Pool {
    std::vector<Slot> slots;
    int free_head = NIL;
    u64 used = 0;
    u64 reuses = 0;
    u64 deny = 0;   // 容量耗尽次数

    explicit Pool(int cap) : slots(cap, Slot{0, NIL, 0}), free_head(0), used(0), reuses(0), deny(0) {
        for (int i = 0; i + 1 < cap; ++i) {
            slots[i].next = i + 1;
        }
    }

    int capacity() const { return (int)slots.size(); }
    u64 alive() const { return used; }

    // 分配一个槽；池空时返回 NIL（调用方查哨兵，不抛异常）。
    int alloc(u64 v) {
        if (free_head == NIL) {
            deny += 1;
            return NIL;
        }
        const int id = free_head;
        free_head = slots[id].next;
        slots[id].next = NIL;
        slots[id].payload = v;
        if (slots[id].gen > 0) reuses += 1;
        slots[id].gen += 1;
        used += 1;
        return id;
    }

    void release(int id) {
        slots[id].payload = 0;
        slots[id].next = free_head;
        free_head = id;
        used -= 1;
    }

    // 池里所有活槽的 payload 和：用来确认 free/alloc 没有串槽。
    u64 checksum() const {
        u64 sum = 0;
        for (int i = 0; i < capacity(); ++i) {
            if (slots[i].gen > 0 && slots[i].payload != 0) sum += slots[i].payload;
        }
        return sum;
    }
};

static void check(const char* what, bool ok) {
    std::printf("%-26s %s\n", what, ok ? "ok" : "FAIL");
}

int main() {
    Pool p(4);
    check("fresh pool empty", p.alive() == 0);

    const int a = p.alloc(100);
    const int b = p.alloc(200);
    const int c = p.alloc(300);
    const int d = p.alloc(400);
    check("four allocs distinct", a != b && b != c && c != d && a != d);
    check("pool full", p.alloc(500) == NIL);

    p.release(b);
    p.release(d);
    check("alive after release", p.alive() == 2);

    const int e = p.alloc(600);
    const int f = p.alloc(700);
    check("freelist reuse", (e == b || e == d) && (f == b || f == d));
    check("reuse counted", p.reuses == 2);
    check("capacity never grew", p.capacity() == 4);
    check("exhaustion counted", p.deny == 1);

    std::printf("pool cap=%d alive=%d reuses=%llu deny=%llu sum=%llu\n", p.capacity(),
                (int)p.alive(), p.reuses, p.deny, p.checksum());
    return 0;
}
