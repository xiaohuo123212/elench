// 02_csv_stats.cpp —— 字符串切分 + 计数（std::string + map 的典型用法）
#include <cstdio>
#include <string>
#include <vector>
#include <map>

static std::vector<std::string> split(const std::string& s, char sep) {
    std::vector<std::string> out;
    std::string cur;
    for (std::size_t i = 0; i < s.size(); ++i) {
        if (s[i] == sep) { out.push_back(cur); cur.clear(); }
        else { cur.push_back(s[i]); }
    }
    out.push_back(cur);
    return out;
}

static std::map<std::string, int> count_fields(const std::vector<std::string>& lines) {
    std::map<std::string, int> freq;
    for (std::size_t i = 0; i < lines.size(); ++i) {
        std::vector<std::string> parts = split(lines[i], ',');
        if (parts.empty()) continue;
        freq[parts[0]] += 1;
    }
    return freq;
}

int main() {
    std::vector<std::string> lines{"a,1", "b,2", "a,3"};
    std::map<std::string, int> f = count_fields(lines);
    for (std::map<std::string, int>::iterator it = f.begin(); it != f.end(); ++it) {
        std::printf("%s %d\n", it->first.c_str(), it->second);
    }
    return 0;
}
