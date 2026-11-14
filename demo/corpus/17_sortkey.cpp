// 17_sortkey.cpp —— 按 key 排序 + 分组统计：日志先按 (用户, 到达序号) 排一遍，
// 再顺序扫过去做分组，最后挑出流量最大的用户。比较器是独立的自由函数，
// 排序手写（插入排序），因为这里要的是主键 + 二级键的细规则，不是容器默认的字典序。
#include <cstdio>
#include <vector>

using u64 = unsigned long long;

struct Entry {
    u64 key;     // 用户 id：分组键，也是排序主键
    u64 seq;     // 到达序号：同一用户内的二级键，保证结果稳定
    u64 bytes;   // 这次请求传输的字节数
};

struct Group {
    u64 key;
    u64 hits;
    u64 total;
};

// 主键小的在前；主键相同按到达序号，保证「同一份输入永远同样的顺序」。
static bool entry_less(const Entry& a, const Entry& b) {
    if (a.key != b.key) return a.key < b.key;
    return a.seq < b.seq;
}

// 总字节多的在前；总额相同按 key 升序，避免输出顺序随插入顺序漂移。
static bool group_less(const Group& a, const Group& b) {
    if (a.total != b.total) return a.total > b.total;
    return a.key < b.key;
}

// 手写插入排序：几百条数据时够快，比较器还是具名函数，规则一眼能查。
static void sort_entries(std::vector<Entry>& xs) {
    for (std::size_t i = 1; i < xs.size(); ++i) {
        Entry cur = xs[i];
        std::size_t j = i;
        while (j > 0 && entry_less(cur, xs[j - 1])) {
            xs[j] = xs[j - 1];
            j -= 1;
        }
        xs[j] = cur;
    }
}

// 分组：排好序以后同一个 key 的记录必然相邻，顺序扫一遍就够。
static std::vector<Group> group_by_key(const std::vector<Entry>& xs) {
    std::vector<Group> out;
    for (std::size_t i = 0; i < xs.size(); ++i) {
        if (i == 0 || xs[i].key != xs[i - 1].key) {
            out.push_back(Group{xs[i].key, 1, xs[i].bytes});
        } else {
            std::size_t last = out.size() - 1;
            out[last].hits += 1;
            out[last].total += xs[i].bytes;
        }
    }
    return out;
}

// 挑流量最大的组：走同一个 group_less 规则，结果与插入顺序无关。
static Group pick_top(const std::vector<Group>& gs) {
    Group best = gs[0];
    for (std::size_t i = 1; i < gs.size(); ++i) {
        if (group_less(gs[i], best)) best = gs[i];
    }
    return best;
}

// 按 key 二分：排好序的数组上找第一个 key 不小于给定值的位置
// （key 存在时就是它第一次出现的地方；不存在时是插入位置）。
static u64 lower_bound_by_key(const std::vector<Entry>& xs, u64 key) {
    std::size_t lo = 0;
    std::size_t hi = xs.size();
    while (lo < hi) {
        std::size_t mid = lo + (hi - lo) / 2;
        if (xs[mid].key < key) lo = mid + 1;
        else hi = mid;
    }
    return lo;
}

int main() {
    std::vector<Entry> xs;
    xs.push_back(Entry{42, 1, 700});
    xs.push_back(Entry{7, 2, 120});
    xs.push_back(Entry{42, 3, 300});
    xs.push_back(Entry{7, 4, 900});
    xs.push_back(Entry{19, 5, 450});
    xs.push_back(Entry{19, 6, 250});
    xs.push_back(Entry{5, 7, 1000});

    sort_entries(xs);
    std::printf("sorted by key:");
    for (std::size_t i = 0; i < xs.size(); ++i) std::printf(" %llu/%llu", xs[i].key, xs[i].seq);
    std::printf("\n");

    std::vector<Group> gs = group_by_key(xs);
    std::printf("groups: %llu\n", gs.size());
    for (std::size_t i = 0; i < gs.size(); ++i) {
        std::printf("key %llu: hits %llu, bytes %llu\n", gs[i].key, gs[i].hits, gs[i].total);
    }

    Group top = pick_top(gs);
    std::printf("top: key %llu, %llu bytes\n", top.key, top.total);

    // 23 不存在：lower_bound 给的是插入位置（第一个比它大的 key），不是 size()。
    std::printf("key 19 at %llu, key 23 at %llu\n",
                lower_bound_by_key(xs, 19), lower_bound_by_key(xs, 23));

    u64 total = 0;
    for (std::size_t i = 0; i < xs.size(); ++i) total += xs[i].bytes;
    std::printf("total bytes: %llu\n", total);
    return 0;
}
