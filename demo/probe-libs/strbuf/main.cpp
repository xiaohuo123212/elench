// demo/probe-libs/strbuf/main.cpp —— 字符串构造器：追加/切分/trim/大小写/重复拼接。
#include <cctype>
#include <cstdio>
#include <string>
#include <vector>

struct StrBuf {
    std::string data;

    StrBuf() { data = ""; }

    size_t size() const { return data.size(); }

    bool empty() const { return data.empty(); }

    void append(const std::string &s) { data = data + s; }

    void append_char(char c) { data.push_back(c); }

    void repeat(const std::string &s, size_t n) {
        for (size_t k = 0; k < n; k++) {
            data = data + s;
        }
    }

    std::string str() const { return data; }
};

static std::string slice(const std::string &s, size_t a, size_t b) {
    if (a >= s.size()) return "";
    if (b > s.size()) b = s.size();
    if (b <= a) return "";
    return s.substr(a, b - a);
}

static std::string trim(const std::string &s) {
    size_t a = 0;
    size_t b = s.size();
    while (a < b && std::isspace(static_cast<unsigned char>(s[a]))) {
        a = a + 1;
    }
    while (b > a && std::isspace(static_cast<unsigned char>(s[b - 1]))) {
        b = b - 1;
    }
    return slice(s, a, b);
}

static std::vector<std::string> split(const std::string &s, char sep) {
    std::vector<std::string> out;
    size_t start = 0;
    size_t i = 0;
    while (i < s.size()) {
        if (s[i] == sep) {
            out.push_back(slice(s, start, i));
            start = i + 1;
        }
        i = i + 1;
    }
    out.push_back(slice(s, start, s.size()));
    return out;
}

static std::string upper(const std::string &s) {
    StrBuf b;
    for (size_t i = 0; i < s.size(); i++) {
        b.append_char(static_cast<char>(std::toupper(static_cast<unsigned char>(s[i]))));
    }
    return b.str();
}

static std::string join(const std::vector<std::string> &parts, char sep) {
    StrBuf b;
    for (size_t i = 0; i < parts.size(); i++) {
        if (i > 0) b.append_char(sep);
        b.append(parts[i]);
    }
    return b.str();
}

static size_t count_char(const std::string &s, char c) {
    size_t n = 0;
    for (size_t i = 0; i < s.size(); i++) {
        if (s[i] == c) n++;
    }
    return n;
}

int main() {
    const std::string raw = "   Alpha,Beta , Gamma ,delta  ";
    const std::string cleaned = trim(raw);
    const std::vector<std::string> parts = split(cleaned, ',');
    std::vector<std::string> tight;
    for (size_t i = 0; i < parts.size(); i++) {
        tight.push_back(trim(parts[i]));
    }

    StrBuf b;
    b.append("raw=");
    b.append(raw);
    b.append_char('\n');
    b.repeat("-", 3);
    const std::string sep_line = b.str();

    std::printf("raw_len=%zu\n", raw.size());
    std::printf("trim=[%s]\n", cleaned.c_str());
    std::printf("parts=%zu\n", parts.size());
    for (size_t i = 0; i < parts.size(); i++) {
        std::printf("part%zu=[%s]\n", i, parts[i].c_str());
    }
    std::printf("tight=%s\n", join(tight, '|').c_str());
    std::printf("upper=%s\n", upper(cleaned).c_str());
    std::printf("sep_line=[%s]\n", sep_line.c_str());
    std::printf("commas=%zu\n", count_char(cleaned, ','));
    std::printf("repeat=%s\n", join(tight, '/').c_str());
    std::printf("empty=[%s] len=%zu\n", trim("   ").c_str(), trim("   ").size());
    return 0;
}
