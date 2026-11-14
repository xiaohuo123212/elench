// 19_ring2d.cpp —— 二维网格的原地状态更新：生命游戏 + 热扩散，双缓冲轮流读写。
// 真实场景：LED 面板 / 热仿真格子。每轮只读旧缓冲、往新缓冲写自己，写满再换角色（读写同一张会把刚写过的邻居当成旧值）。
#include <cstdio>
#include <vector>

using u64 = unsigned long long;
using Grid = std::vector<std::vector<u64>>;

static Grid make_grid(u64 rows, u64 cols) {
    Grid g;
    for (u64 r = 0; r < rows; ++r) {
        std::vector<u64> row;
        for (u64 c = 0; c < cols; ++c) row.push_back(0);
        g.push_back(row);
    }
    return g;
}

// 标准滑翔机：5 个活细胞，每四代斜着挪一格。
static void seed_life(Grid& g) {
    g[0][1] = 1;
    g[1][2] = 1;
    g[2][0] = 1;
    g[2][1] = 1;
    g[2][2] = 1;
}

// 八邻域：越界的位置跳过，最后减掉自己。
static u64 neighbors(const Grid& g, std::size_t r, std::size_t c) {
    u64 n = 0;
    for (u64 dr = 0; dr < 3; ++dr) {
        for (u64 dc = 0; dc < 3; ++dc) {
            if (dr == 1 && dc == 1) continue;
            if (dr == 0 && r == 0) continue;
            if (dc == 0 && c == 0) continue;
            std::size_t rr = r + dr - 1;
            std::size_t cc = c + dc - 1;
            if (rr >= g.size()) continue;
            if (cc >= g[rr].size()) continue;
            n += g[rr][cc];
        }
    }
    return n;
}

static void life_step(const Grid& cur, Grid& next) {
    for (std::size_t r = 0; r < cur.size(); ++r) {
        for (std::size_t c = 0; c < cur[r].size(); ++c) {
            u64 n = neighbors(cur, r, c);
            u64 live = 0;
            if (cur[r][c] != 0) {
                if (n == 2 || n == 3) live = 1;
            } else {
                if (n == 3) live = 1;
            }
            next[r][c] = live;
        }
    }
}

// 5 点平均：自己权重 4，上下左右各 1，再除以 8；边界外贴着自己的值。
static void diffuse_once(const Grid& cur, Grid& next) {
    for (std::size_t r = 0; r < cur.size(); ++r) {
        for (std::size_t c = 0; c < cur[r].size(); ++c) {
            u64 up = cur[r][c];
            u64 down = cur[r][c];
            u64 left = cur[r][c];
            u64 right = cur[r][c];
            if (r > 0) up = cur[r - 1][c];
            if (r + 1 < cur.size()) down = cur[r + 1][c];
            if (c > 0) left = cur[r][c - 1];
            if (c + 1 < cur[r].size()) right = cur[r][c + 1];
            next[r][c] = (cur[r][c] * 4 + up + down + left + right) / 8;
        }
    }
}

static u64 count_nonzero(const Grid& g) {
    u64 n = 0;
    for (std::size_t r = 0; r < g.size(); ++r) {
        for (std::size_t c = 0; c < g[r].size(); ++c) {
            if (g[r][c] != 0) n += 1;
        }
    }
    return n;
}

static void print_grid(const Grid& g) {
    for (std::size_t r = 0; r < g.size(); ++r) {
        for (std::size_t c = 0; c < g[r].size(); ++c) {
            if (g[r][c] != 0) std::printf("#");
            else std::printf(".");
        }
        std::printf("\n");
    }
}

int main() {
    const u64 kRows = 8;
    const u64 kCols = 10;
    Grid a = make_grid(kRows, kCols);
    seed_life(a);
    Grid b = make_grid(kRows, kCols);
    life_step(a, b);          // a 是旧状态，b 是新一代
    life_step(b, a);          // 换角色：b 旧、a 新
    life_step(a, b);          // 再换回来，最终状态在 b
    std::printf("alive %llu\n", count_nonzero(b));
    print_grid(b);

    Grid hot = make_grid(kRows, kCols);
    hot[3][4] = 80;
    hot[3][5] = 80;
    hot[4][4] = 80;
    hot[4][5] = 80;
    Grid cool = make_grid(kRows, kCols);
    diffuse_once(hot, cool);  // hot 旧、cool 新
    diffuse_once(cool, hot);  // 换回来，最终状态在 hot
    std::printf("heat center %llu cells %llu\n", hot[3][4], count_nonzero(hot));
    return 0;
}
