// 11_text.cpp —— 文本处理：把一段内置的配置文本按行、按逗号切开，
// 去掉首尾空白、键名转小写，再统计每个键出现的次数（std::map 有序输出）。
// 真实场景：读进来的 .ini / .csv 片段做归一化与计数。
#include <cstdio>
#include <map>
#include <string>
#include <vector>

using u64 = unsigned long long;

static bool is_blank(char c) {
    return c == ' ' || c == '\t' || c == '\r';
}

// 去掉首尾空白；全是空白时返回空串。
static std::string trim(const std::string& s) {
    std::size_t begin = s.size();
    std::size_t end = 0;
    for (std::size_t i = 0; i < s.size(); ++i) {
        if (!is_blank(s[i])) {
            begin = i;
            break;
        }
    }
    for (std::size_t i = 0; i < s.size(); ++i) {
        if (!is_blank(s[i])) end = i + 1;
    }
    std::string out;
    for (std::size_t i = 0; i < s.size(); ++i) {
        if (i >= begin && i < end) out.push_back(s[i]);
    }
    return out;
}

// 键名只做 ASCII 小写，不碰数字和下划线。
static std::string lower_key(const std::string& s) {
    std::string out;
    for (auto c : s) {
        if (c >= 'A' && c <= 'Z') {
            u64 offset = c - 'A';
            out.push_back('a' + offset);
        } else {
            out.push_back(c);
        }
    }
    return out;
}

// 按一个字节分隔符切分；空字段也保留（与 C++ 的 getline 一致）。
static std::vector<std::string> split(const std::string& s, char sep) {
    std::vector<std::string> out;
    std::string cur;
    for (auto c : s) {
        if (c == sep) {
            out.push_back(cur);
            cur.clear();
        } else {
            cur.push_back(c);
        }
    }
    out.push_back(cur);
    return out;
}

int main() {
    std::string text;
    text = text + "  Name = alpha , size=3\n";
    text = text + "name=beta, SIZE = 5\n";
    text = text + "  tag=x\n";

    std::vector<std::string> lines = split(text, '\n');
    std::map<std::string, u64> counts;
    for (const auto& line : lines) {
        if (line.empty()) continue;
        std::vector<std::string> fields = split(line, ',');
        for (const auto& field : fields) {
            std::size_t eq = 0;
            bool has_eq = false;
            for (std::size_t i = 0; i < field.size(); ++i) {
                if (field[i] == '=') {
                    eq = i;
                    has_eq = true;
                    break;
                }
            }
            if (!has_eq) continue;
            std::string key;
            for (std::size_t i = 0; i < field.size(); ++i) {
                if (i >= eq) break;
                key.push_back(field[i]);
            }
            key = lower_key(trim(key));
            counts[key] += 1;
        }
    }

    for (const auto& kv : counts) {
        std::printf("%s %llu\n", kv.first.c_str(), kv.second);
    }
    return 0;
}
