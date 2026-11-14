// 04_tokenizer.cpp —— 字符分类 + 计数（range-for、auto、isdigit 的常见写法）
#include <cstdio>
#include <string>
#include <vector>
#include <cctype>

static int count_digits(const std::string& s) {
    int n = 0;
    for (auto c : s) {
        if (std::isdigit(static_cast<unsigned char>(c))) n += 1;
    }
    return n;
}

static std::vector<std::string> words(const std::string& s) {
    std::vector<std::string> out;
    std::string cur;
    for (auto c : s) {
        if (std::isspace(static_cast<unsigned char>(c))) {
            if (!cur.empty()) { out.push_back(cur); cur.clear(); }
        } else {
            cur.push_back(c);
        }
    }
    if (!cur.empty()) out.push_back(cur);
    return out;
}

int main() {
    std::string s = "ab 12 cd 34";
    std::printf("%d %zu\n", count_digits(s), words(s).size());
    return 0;
}
