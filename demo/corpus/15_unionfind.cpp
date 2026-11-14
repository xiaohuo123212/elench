// 15_unionfind.cpp —— 并查集：主机按链路合并成组，最后数连通组、查两两是否可达。
// 真实场景：发布系统把「同一批次的主机」并到一起（路径压缩 + 按秩合并），再按组统计规模。
#include <cstdio>
#include <vector>

using u64 = unsigned long long;

// 父数组：根的父亲指向自己；秩数组：只对根有意义，用来决定谁挂到谁下面。
struct Dsu {
    std::vector<u64> parent;
    std::vector<u64> rank;
};

static void dsu_init(Dsu& d, u64 n) {
    for (u64 i = 0; i < n; ++i) {
        d.parent.push_back(i);
        d.rank.push_back(0);
    }
}

// 找根：先沿父指针一路往上浮（不递归，链再长也只是循环次数），
// 再把 x 到根之间的每个点直接挂到根上（路径压缩）。
static u64 find_root(Dsu& d, u64 x) {
    u64 root = x;
    while (d.parent[root] != root) {
        root = d.parent[root];
    }
    while (d.parent[x] != root) {
        u64 next = d.parent[x];
        d.parent[x] = root;
        x = next;
    }
    return root;
}

// 合并：秩小的挂到秩大的下面；秩相同才把新的根加一。
// 返回 false 表示两边本来就在一组（调用方通常要数「省掉的重复边」）。
static bool unite(Dsu& d, u64 a, u64 b) {
    u64 ra = find_root(d, a);
    u64 rb = find_root(d, b);
    if (ra == rb) return false;
    if (d.rank[ra] < d.rank[rb]) {
        u64 tmp = ra;
        ra = rb;
        rb = tmp;
    }
    d.parent[rb] = ra;
    if (d.rank[ra] == d.rank[rb]) d.rank[ra] += 1;
    return true;
}

static u64 count_groups(Dsu& d) {
    u64 groups = 0;
    for (u64 i = 0; i < d.parent.size(); ++i) {
        if (d.parent[i] == i) groups += 1;
    }
    return groups;
}

// 每组多大：按根累加计数，再把 0（非根位置）滤掉。
static std::vector<u64> group_sizes(Dsu& d) {
    std::vector<u64> sizes(d.parent.size(), 0);
    for (u64 i = 0; i < d.parent.size(); ++i) {
        u64 r = find_root(d, i);
        sizes[r] += 1;
    }
    std::vector<u64> out;
    for (u64 i = 0; i < sizes.size(); ++i) {
        if (sizes[i] > 0) out.push_back(sizes[i]);
    }
    return out;
}

// 边表：一条链路把两台主机并到一起。真实输入里就是 (a, b) 两列。
struct Link {
    u64 a;
    u64 b;
};

int main() {
    Dsu d;
    dsu_init(d, 9);

    std::vector<Link> links;
    links.push_back(Link{0, 1});
    links.push_back(Link{1, 2});
    links.push_back(Link{3, 4});
    links.push_back(Link{5, 6});
    links.push_back(Link{6, 7});
    links.push_back(Link{7, 5});   // 组内成环：合并会说「已经在一组」
    links.push_back(Link{2, 1});   // 重复边，同上
    links.push_back(Link{8, 8});   // 自环

    u64 merged = 0;
    u64 skipped = 0;
    for (std::size_t i = 0; i < links.size(); ++i) {
        if (unite(d, links[i].a, links[i].b)) merged += 1;
        else skipped += 1;
    }
    std::printf("links: %llu, merged: %llu, skipped: %llu\n",
                links.size(), merged, skipped);
    std::printf("groups: %llu\n", count_groups(d));

    // 抽查两对：0-2 同组、0-3 不同组。
    if (find_root(d, 0) == find_root(d, 2)) std::printf("0-2: yes\n");
    else std::printf("0-2: no\n");
    if (find_root(d, 0) == find_root(d, 3)) std::printf("0-3: yes\n");
    else std::printf("0-3: no\n");

    std::vector<u64> sizes = group_sizes(d);
    std::printf("sizes:");
    for (u64 i = 0; i < sizes.size(); ++i) std::printf(" %llu", sizes[i]);
    std::printf("\n");

    // 合并之后再并一条跨组链路，组数应当减一，压缩后的深度仍然很浅。
    if (unite(d, 4, 7)) std::printf("merge 4-7: new group, groups: %llu\n", count_groups(d));
    else std::printf("merge 4-7: same group, groups: %llu\n", count_groups(d));
    return 0;
}
