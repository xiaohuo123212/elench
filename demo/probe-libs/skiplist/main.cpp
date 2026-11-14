// skiplist/main.cpp —— 跳表：每层一条「指向下一节点的下标」链 + 确定性伪随机层高。
//
// 库风格：节点用 vector<u64> 存多层 next（数组当层级）、层高由固定种子 LCG 决定、
// 查找沿层下降、删除只断链不回收（定长 arena，下标当指针）。
#include <cstdio>
#include <vector>

using u64 = unsigned long long;

//: 空链哨兵：和 pool 那边一样，用 -1 表示「没有下一个」。
static const int NONE = -1;

struct Node {
    u64 key;
    u64 value;
    std::vector<int> next;   // next[L] = 第 L 层的下一个节点下标
};

// 跳表：nodes[0] 是头节点（key 不用），level 是当前最高层数。
struct SkipList {
    std::vector<Node> nodes;
    u64 level;
    u64 seed;
    u64 explored;   // 查找走过多少步（把「快」也量出来）

    explicit SkipList(u64 cap, u64 max_level) : level(1), seed(20261113), explored(0) {
        Node head;
        head.key = 0;
        head.value = 0;
        for (u64 i = 0; i < max_level; ++i) head.next.push_back(NONE);
        nodes.push_back(head);
    }

    // 固定种子 LCG：同一份输入永远得到同一组层高。
    u64 next_rand() {
        seed = (seed * 1103515245 + 12345) % 2147483648;
        return seed;
    }

    u64 roll_level() {
        u64 lv = 1;
        while (lv < nodes[0].next.size() && (next_rand() >> 16) % 2 == 0) {
            lv += 1;
        }
        return lv;
    }

    int find(u64 key) {
        int cur = 0;
        for (u64 lv = level; lv > 0; --lv) {
            u64 l = lv - 1;
            int nx = nodes[cur].next[l];
            while (nx != NONE && nodes[nx].key < key) {
                cur = nx;
                explored += 1;
                nx = nodes[cur].next[l];
            }
            explored += 1;
        }
        const int cand = nodes[cur].next[0];
        if (cand != NONE && nodes[cand].key == key) return cand;
        return NONE;
    }

    bool insert(u64 key, u64 value) {
        if (find(key) != NONE) return false;
        const u64 lv = roll_level();
        if (lv > level) level = lv;

        Node n;
        n.key = key;
        n.value = value;
        for (u64 i = 0; i < lv; ++i) n.next.push_back(NONE);
        const int id = (int)nodes.size();
        nodes.push_back(n);

        // 从最高层往下找「每层最后一个比 key 小的节点」，再把新节点接进去。
        int cur = 0;
        for (u64 lv2 = level; lv2 > 0; --lv2) {
            const u64 l = lv2 - 1;
            int nx = nodes[cur].next[l];
            while (nx != NONE && nodes[nx].key < key) {
                cur = nx;
                nx = nodes[cur].next[l];
            }
            if (l < lv) {
                nodes[id].next[l] = nodes[cur].next[l];
                nodes[cur].next[l] = id;
            }
        }
        return true;
    }

    bool erase(u64 key) {
        const int id = find(key);
        if (id == NONE) return false;
        int cur = 0;
        for (u64 lv = level; lv > 0; --lv) {
            const u64 l = lv - 1;
            int nx = nodes[cur].next[l];
            while (nx != NONE && nodes[nx].key < key) {
                cur = nx;
                nx = nodes[cur].next[l];
            }
            if (nodes[cur].next[l] == id) nodes[cur].next[l] = nodes[id].next[l];
        }
        return true;
    }

    // 第 0 层就是有序全表：按序遍历拿来对拍。
    std::vector<u64> keys() const {
        std::vector<u64> out;
        int cur = nodes[0].next[0];
        while (cur != NONE) {
            out.push_back(nodes[cur].key);
            cur = nodes[cur].next[0];
        }
        return out;
    }
};

static u64 lcg_seq(u64 i) { return (i * 37 + 11) % 100; }

int main() {
    SkipList sl(64, 4);
    for (u64 i = 0; i < 12; ++i) {
        sl.insert(lcg_seq(i), i + 1);
    }
    sl.insert(42, 99);
    const bool dup = sl.insert(42, 100);

    std::printf("level=%llu dup_rejected=%d\n", sl.level, dup ? 0 : 1);
    std::printf("found 42 -> %d\n", sl.find(42));
    std::printf("missing 500 -> %d\n", sl.find(500));
    std::printf("erased 42 -> %d\n", sl.erase(42) ? 1 : 0);
    std::printf("found 42 after erase -> %d\n", sl.find(42));

    std::vector<u64> ks = sl.keys();
    std::printf("keys(%d):", (int)ks.size());
    for (auto k : ks) std::printf(" %llu", k);
    std::printf("\nexplored=%llu nodes=%d\n", sl.explored, (int)sl.nodes.size());
    return 0;
}
