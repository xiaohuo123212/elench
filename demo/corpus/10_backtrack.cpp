// 10_backtrack.cpp —— 递归 + 回溯：N 皇后计数，并收集第一组解。
// 真实场景：约束求解器的最小骨架。class 保存列 / 两条对角线的占用，
// 递归只做「放、试、撤」三步，走不通就剪枝返回。
#include <cstdio>
#include <string>
#include <vector>

using u64 = unsigned long long;

class QueenSolver {
public:
    explicit QueenSolver(u64 n)
        : n_(n), cols_(n, 0), diag1_(2 * n, 0), diag2_(2 * n, 0), count_(0) {
        first_.assign(n, 0);
    }

    u64 solve() {
        count_ = 0;
        place(0);
        return count_;
    }

    const std::vector<u64>& first_solution() const {
        return first_;
    }

    std::vector<std::string> render() const {
        std::vector<std::string> board;
        for (u64 row = 0; row < n_; ++row) {
            std::string line;
            for (u64 col = 0; col < n_; ++col) {
                if (first_[row] == col) line.push_back('Q');
                else line.push_back('.');
            }
            board.push_back(line);
        }
        return board;
    }

private:
    void place(u64 row) {
        if (row == n_) {
            count_ += 1;
            return;
        }
        for (u64 col = 0; col < n_; ++col) {
            u64 d1 = row + col;
            u64 d2 = row + n_ - 1 - col;
            if (cols_[col] != 0) continue;
            if (diag1_[d1] != 0) continue;
            if (diag2_[d2] != 0) continue;

            cols_[col] = 1;
            diag1_[d1] = 1;
            diag2_[d2] = 1;
            if (count_ == 0) first_[row] = col;

            place(row + 1);

            cols_[col] = 0;
            diag1_[d1] = 0;
            diag2_[d2] = 0;
        }
    }

    u64 n_;
    std::vector<u64> cols_;
    std::vector<u64> diag1_;
    std::vector<u64> diag2_;
    std::vector<u64> first_;
    u64 count_;
};

int main() {
    QueenSolver solver(6);
    u64 total = solver.solve();
    std::printf("6-queens solutions: %llu\n", total);

    const std::vector<u64>& first = solver.first_solution();
    std::printf("first solution:");
    for (auto col : first) {
        std::printf(" %llu", col);
    }
    std::printf("\n");

    std::vector<std::string> board = solver.render();
    for (const auto& line : board) {
        std::printf("%s\n", line.c_str());
    }
    return 0;
}
