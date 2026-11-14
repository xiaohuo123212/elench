// demo/probe-libs/iniparse/main.cpp —— 简易 INI 解析：按行切、按 '=' 切、去注释、存 map。
#include <cstdio>
#include <map>
#include <string>
#include <vector>

static std::string slice(const std::string &s, size_t a, size_t b) {
    if (a >= s.size()) return "";
    if (b > s.size()) b = s.size();
    if (b <= a) return "";
    return s.substr(a, b - a);
}

static std::vector<std::string> split_lines(const std::string &text) {
    std::vector<std::string> out;
    size_t start = 0;
    size_t i = 0;
    while (i < text.size()) {
        if (text[i] == '\n') {
            out.push_back(slice(text, start, i));
            start = i + 1;
        }
        i = i + 1;
    }
    out.push_back(slice(text, start, text.size()));
    return out;
}

static std::string strip_comment(const std::string &line) {
    size_t i = 0;
    while (i < line.size()) {
        if (line[i] == ';' || line[i] == '#') return slice(line, 0, i);
        i = i + 1;
    }
    return line;
}

static std::string trim(const std::string &s) {
    size_t a = 0;
    size_t b = s.size();
    while (a < b && (s[a] == ' ' || s[a] == '\t' || s[a] == '\r')) {
        a = a + 1;
    }
    while (b > a && (s[b - 1] == ' ' || s[b - 1] == '\t' || s[b - 1] == '\r')) {
        b = b - 1;
    }
    return slice(s, a, b);
}

static size_t first_index(const std::string &s, char c) {
    for (size_t i = 0; i < s.size(); i++) {
        if (s[i] == c) return i;
    }
    return s.size();
}

struct Ini {
    std::map<std::string, std::string> items;
    std::string section;
    size_t kept;      // 真正写进 map 的键数（含覆盖）
    size_t skipped;   // 空行 / 注释 / 缺 '=' 的行

    Ini() {
        section = "";
        kept = 0;
        skipped = 0;
    }

    std::string qualify(const std::string &key) const {
        if (section.empty()) return key;
        return section + "." + key;
    }

    void parse(const std::string &text) {
        const std::vector<std::string> lines = split_lines(text);
        for (size_t i = 0; i < lines.size(); i++) {
            const std::string body = trim(strip_comment(lines[i]));
            if (body.empty()) {
                skipped = skipped + 1;
                continue;
            }
            if (body[0] == '[') {
                section = trim(slice(body, 1, body.size() - 1));
                continue;
            }
            const size_t eq = first_index(body, '=');
            if (eq == body.size()) {
                skipped = skipped + 1;
                continue;
            }
            const std::string key = qualify(trim(slice(body, 0, eq)));
            const std::string val = trim(slice(body, eq + 1, body.size()));
            if (key.empty()) {
                skipped = skipped + 1;
                continue;
            }
            items[key] = val;
            kept = kept + 1;
        }
    }

    bool has(const std::string &key) const { return items.find(key) != items.end(); }

    std::string get(const std::string &key, const std::string &fallback) const {
        const std::map<std::string, std::string>::const_iterator it = items.find(key);
        if (it == items.end()) return fallback;
        return it->second;
    }

    size_t size() const { return items.size(); }
};

int main() {
    const std::string cfg =
        "name = demo\n; 整行注释\ncount=3\n\nretry = 5\ncount = 4\n[net]\nhost=localhost\n"
        "port = 8080  # 行尾注释\nbroken line\n";
    Ini ini;
    ini.parse(cfg);

    std::printf("cfg_lines=%zu\n", split_lines(cfg).size());
    std::printf("size=%zu kept=%zu skipped=%zu\n", ini.size(), ini.kept, ini.skipped);
    std::printf("name=[%s]\n", ini.get("name", "?").c_str());
    std::printf("count=[%s]\n", ini.get("count", "?").c_str());
    std::printf("retry=[%s]\n", ini.get("retry", "?").c_str());
    std::printf("net.host=[%s]\n", ini.get("net.host", "?").c_str());
    std::printf("net.port=[%s]\n", ini.get("net.port", "?").c_str());
    std::printf("missing=[%s]\n", ini.get("nope", "default-value").c_str());
    std::printf("has_name=%d has_nope=%d\n", ini.has("name") ? 1 : 0, ini.has("nope") ? 1 : 0);
    std::printf("section=[%s]\n", ini.section.c_str());
    return 0;
}
