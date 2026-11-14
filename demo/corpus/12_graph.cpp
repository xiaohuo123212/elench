// 12_graph.cpp —— 邻接表上的图算法：BFS 求跳数、DFS 三色判环、数连通分量。
// 真实场景：构建系统 / 任务调度器里的依赖图——「入口到某个任务最少几步」
// 「哪些任务会跟着入口一起被拖起来」「图里有没有环形依赖」。
// 邻接表是容器套容器（vector<vector<u64>>），邻居只按下标访问。
#include <cstdio>
#include <vector>

using u64 = unsigned long long;
using Graph = std::vector<std::vector<u64>>;

static Graph make_graph(u64 n) {
    std::vector<u64> empty;   // 每个顶点先给一列空邻居
    Graph g(n, empty);
    return g;
}

static void add_edge(Graph& g, u64 u, u64 v) {
    g[u].push_back(v);
}

// 从 src 出发做 BFS：dist[v] 是最少跳数。
// 「到不了」用顶点数 n 当哨兵——合法编号最大是 n-1，n 是天然的越界值
// （和 pos == xs.size() 表示「没有这个位置」是同一个意思）。
static void bfs(const Graph& g, u64 src, std::vector<u64>& dist) {
    u64 n = g.size();
    for (std::size_t i = 0; i < dist.size(); ++i) dist[i] = n;
    dist[src] = 0;
    std::vector<u64> queue;
    queue.push_back(src);
    u64 head = 0;
    // 每个顶点最多入队一次，所以「出队」这件事最多做 n 次。
    for (std::size_t step = 0; step < g.size(); ++step) {
        if (head >= queue.size()) break;
        u64 v = queue[head];
        head += 1;
        for (std::size_t k = 0; k < g[v].size(); ++k) {
            u64 w = g[v][k];
            if (dist[w] != n) continue;
            dist[w] = dist[v] + 1;
            queue.push_back(w);
        }
    }
}

// 被 BFS 标过的顶点数（含起点自己）。
static u64 reachable(const std::vector<u64>& dist) {
    u64 n = dist.size();
    u64 seen = 0;
    for (std::size_t i = 0; i < dist.size(); ++i) {
        if (dist[i] != n) seen += 1;
    }
    return seen;
}

// DFS 三色判环：0 = 没进过，1 = 还在递归栈上，2 = 子树已经查完。
static bool dfs_visit(const Graph& g, u64 v, std::vector<u64>& color) {
    color[v] = 1;
    for (std::size_t k = 0; k < g[v].size(); ++k) {
        u64 w = g[v][k];
        if (color[w] == 1) return true;
        if (color[w] == 0) {
            if (dfs_visit(g, w, color)) return true;
        }
    }
    color[v] = 2;
    return false;
}

static bool has_cycle(const Graph& g) {
    std::vector<u64> color(g.size(), 0);
    for (std::size_t v = 0; v < g.size(); ++v) {
        if (color[v] != 0) continue;
        if (dfs_visit(g, v, color)) return true;
    }
    return false;
}

// 连通分量个数：每挑到一个没访问过的顶点，就把它那一整片刷掉。
static u64 count_components(const Graph& g) {
    u64 parts = 0;
    std::vector<u64> seen(g.size(), 0);
    std::vector<u64> dist(g.size(), 0);
    for (std::size_t v = 0; v < g.size(); ++v) {
        if (seen[v] != 0) continue;
        bfs(g, v, dist);
        for (std::size_t i = 0; i < seen.size(); ++i) {
            if (dist[i] != dist.size()) seen[i] = 1;
        }
        parts += 1;
    }
    return parts;
}

int main() {
    Graph g = make_graph(8);
    add_edge(g, 0, 1);
    add_edge(g, 0, 2);
    add_edge(g, 1, 3);
    add_edge(g, 2, 3);
    add_edge(g, 3, 4);
    add_edge(g, 5, 6);   // 顶点 7 是孤立任务：没人依赖它，它也不依赖别人

    std::vector<u64> dist(8, 0);
    bfs(g, 0, dist);
    std::printf("bfs from 0:");
    for (std::size_t v = 0; v < dist.size(); ++v) {
        if (dist[v] == dist.size()) std::printf(" -");
        else std::printf(" %llu", dist[v]);
    }
    std::printf("\n");
    std::printf("reachable from 0: %llu\n", reachable(dist));
    std::printf("components: %llu\n", count_components(g));
    if (has_cycle(g)) std::printf("has cycle: yes\n");
    else std::printf("has cycle: no\n");

    add_edge(g, 6, 5);   // 再补一条 6 -> 5，让第二个分量成环
    if (has_cycle(g)) std::printf("after edge 6->5, has cycle: yes\n");
    else std::printf("after edge 6->5, has cycle: no\n");
    return 0;
}
