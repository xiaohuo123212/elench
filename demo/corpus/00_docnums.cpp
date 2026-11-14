// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Elench contributors
//
// docnums —— 文档数字绑定检查器（C++17，零第三方依赖）
//
// 与 `tools/doc_numbers.py` **同语义**：同一份 `tools/doc_numbers.json`、
// 同一套标记 `数字<!--dn:key-->`、同样的退出码。C++ 版用于在**没有 Python**
// 的环境里跑 CI（也顺便验证"这个检查器足够小，可以随便移植"）。
//
// 用法：
//   build/docnums --list
//   build/docnums --check [文档...]
//   build/docnums --scan  文档...
//   build/docnums --check --root <仓库根> [文档...]
//
// 退出码：0 一致 / 1 有不一致 / 2 用法或环境错误。

#include <algorithm>
#include <cctype>
#ifdef _WIN32
#include <windows.h>
#include <shellapi.h>
#endif
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <regex>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

namespace {

// ---------------------------------------------------------------- 文件

//: 取命令行参数（UTF-8）。
//:
//: **不能直接用 `argv`**：Windows 上它是 ANSI 代码页（中文系统 = GBK）字节，
//: 中文路径传进来会被当成非法 UTF-8，`filesystem` 直接抛异常。
//: 这里走 `GetCommandLineW` + `CommandLineToArgvW`，再转 UTF-8。
std::vector<std::string> get_args(int argc, char** argv) {
    std::vector<std::string> out;
#ifdef _WIN32
    int n = 0;
    LPWSTR* w = CommandLineToArgvW(GetCommandLineW(), &n);
    if (w) {
        for (int i = 0; i < n; ++i) {
            int len = WideCharToMultiByte(CP_UTF8, 0, w[i], -1, nullptr, 0, nullptr, nullptr);
            std::string s(len > 0 ? static_cast<size_t>(len - 1) : 0, '\0');
            if (len > 0)
                WideCharToMultiByte(CP_UTF8, 0, w[i], -1, &s[0], len, nullptr, nullptr);
            out.push_back(s);
        }
        LocalFree(w);
        return out;
    }
#endif
    for (int i = 0; i < argc; ++i) out.push_back(argv[i]);
    return out;
}

bool read_file(const std::string& path, std::string& out) {
    // **必须走 filesystem::u8path**：Windows 的窄字符流按 ANSI 代码页解释路径，
    // 中文目录（`文档/其他/预印本/…`）会直接打不开，而 Python 版没这个问题。
    std::ifstream in(std::filesystem::u8path(path), std::ios::binary);
    if (!in) return false;
    std::ostringstream ss;
    ss << in.rdbuf();
    out = ss.str();
    return true;
}

std::string dirname_of(const std::string& p) {
    size_t pos = p.find_last_of("/\\");
    return pos == std::string::npos ? std::string(".") : p.substr(0, pos);
}

std::string join_path(const std::string& a, const std::string& b) {
    if (a.empty()) return b;
    if (b.size() > 1 && b[1] == ':') return b;  // Windows 绝对路径
    char last = a.back();
    if (last == '/' || last == '\\') return a + b;
    return a + "/" + b;
}

bool is_abs(const std::string& p) {
    if (p.size() > 1 && p[1] == ':') return true;
    return !p.empty() && (p[0] == '/' || p[0] == '\\');
}

// ---------------------------------------------------------------- JSON
//
// 只支持对象/数组/字符串/数字/true/false/null——够读 `doc_numbers.json`
// 与 `audit_numbers.py --json` 的输出。**保留对象键的插入顺序**，
// 因为 `--list` 的输出顺序要和清单一致。

struct Json {
    enum Kind { Null, Bool, Num, Str, Arr, Obj } kind = Null;
    bool b = false;
    double num = 0;
    std::string str;
    std::vector<Json> arr;
    std::vector<std::pair<std::string, Json>> obj;

    const Json* get(const std::string& key) const {
        for (const auto& kv : obj)
            if (kv.first == key) return &kv.second;
        return nullptr;
    }
};

class JsonParser {
 public:
    explicit JsonParser(const std::string& s) : s_(s) {}
    Json parse(bool& ok) {
        Json v;
        skip_ws();
        ok = value(v);
        if (ok) skip_ws();
        return v;
    }

 private:
    const std::string& s_;
    size_t i_ = 0;

    void skip_ws() {
        while (i_ < s_.size() && std::isspace(static_cast<unsigned char>(s_[i_]))) ++i_;
    }
    bool eat(char c) {
        if (i_ < s_.size() && s_[i_] == c) { ++i_; return true; }
        return false;
    }
    bool value(Json& v) {
        skip_ws();
        if (i_ >= s_.size()) return false;
        char c = s_[i_];
        if (c == '{') return object(v);
        if (c == '[') return array(v);
        if (c == '"') { v.kind = Json::Str; return string(v.str); }
        if (c == 't') { if (s_.compare(i_, 4, "true") == 0) { i_ += 4; v.kind = Json::Bool; v.b = true; return true; } return false; }
        if (c == 'f') { if (s_.compare(i_, 5, "false") == 0) { i_ += 5; v.kind = Json::Bool; v.b = false; return true; } return false; }
        if (c == 'n') { if (s_.compare(i_, 4, "null") == 0) { i_ += 4; v.kind = Json::Null; return true; } return false; }
        return number(v);
    }
    bool object(Json& v) {
        v.kind = Json::Obj;
        if (!eat('{')) return false;
        skip_ws();
        if (eat('}')) return true;
        for (;;) {
            skip_ws();
            std::string key;
            if (!string(key)) return false;
            skip_ws();
            if (!eat(':')) return false;
            Json item;
            if (!value(item)) return false;
            v.obj.emplace_back(key, std::move(item));
            skip_ws();
            if (eat(',')) continue;
            if (eat('}')) return true;
            return false;
        }
    }
    bool array(Json& v) {
        v.kind = Json::Arr;
        if (!eat('[')) return false;
        skip_ws();
        if (eat(']')) return true;
        for (;;) {
            Json item;
            if (!value(item)) return false;
            v.arr.push_back(std::move(item));
            skip_ws();
            if (eat(',')) continue;
            if (eat(']')) return true;
            return false;
        }
    }
    bool string(std::string& out) {
        if (!eat('"')) return false;
        out.clear();
        while (i_ < s_.size()) {
            char c = s_[i_++];
            if (c == '"') return true;
            if (c != '\\') { out += c; continue; }
            if (i_ >= s_.size()) return false;
            char e = s_[i_++];
            switch (e) {
                case 'n': out += '\n'; break;
                case 't': out += '\t'; break;
                case 'r': out += '\r'; break;
                case '"': out += '"'; break;
                case '\\': out += '\\'; break;
                case '/': out += '/'; break;
                case 'u': {
                    if (i_ + 4 > s_.size()) return false;
                    unsigned cp = static_cast<unsigned>(std::stoul(s_.substr(i_, 4), nullptr, 16));
                    i_ += 4;
                    if (cp < 0x80) {
                        out += static_cast<char>(cp);
                    } else if (cp < 0x800) {
                        out += static_cast<char>(0xC0 | (cp >> 6));
                        out += static_cast<char>(0x80 | (cp & 0x3F));
                    } else {
                        out += static_cast<char>(0xE0 | (cp >> 12));
                        out += static_cast<char>(0x80 | ((cp >> 6) & 0x3F));
                        out += static_cast<char>(0x80 | (cp & 0x3F));
                    }
                    break;
                }
                default: out += e; break;
            }
        }
        return false;
    }
    bool number(Json& v) {
        size_t start = i_;
        if (i_ < s_.size() && (s_[i_] == '-' || s_[i_] == '+')) ++i_;
        while (i_ < s_.size() && (std::isdigit(static_cast<unsigned char>(s_[i_])) ||
                                  s_[i_] == '.' || s_[i_] == 'e' || s_[i_] == 'E' ||
                                  s_[i_] == '-' || s_[i_] == '+'))
            ++i_;
        if (i_ == start) return false;
        try {
            v.kind = Json::Num;
            v.num = std::stod(s_.substr(start, i_ - start));
        } catch (...) {
            return false;
        }
        return true;
    }
};

// ---------------------------------------------------------------- 格式

std::string fmt(double v) {
    long long iv = static_cast<long long>(v < 0 ? v - 0.5 : v + 0.5);
    bool neg = iv < 0;
    std::string digits = std::to_string(neg ? -iv : iv);
    std::string out;
    int count = 0;
    for (auto it = digits.rbegin(); it != digits.rend(); ++it) {
        if (count && count % 3 == 0) out += ',';
        out += *it;
        ++count;
    }
    if (neg) out += '-';
    std::reverse(out.begin(), out.end());
    return out;
}

//: UTF-8 码点数（对齐要按**字符**算，不是按字节——否则中文列会歪）
size_t u8len(const std::string& s) {
    size_t n = 0;
    for (unsigned char c : s)
        if ((c & 0xC0) != 0x80) ++n;
    return n;
}

std::string pad_left(const std::string& s, size_t width) {
    size_t n = u8len(s);
    return n >= width ? s : std::string(width - n, ' ') + s;
}

std::string pad_right(const std::string& s, size_t width) {
    size_t n = u8len(s);
    return n >= width ? s : s + std::string(width - n, ' ');
}

std::string basename_of(const std::string& p) {
    size_t pos = p.find_last_of("/\\");
    return pos == std::string::npos ? p : p.substr(pos + 1);
}

std::string strip_commas(std::string s) {
    s.erase(std::remove(s.begin(), s.end(), ','), s.end());
    return s;
}

// ---------------------------------------------------------------- 子进程

std::string run_capture(const std::string& cmd, int& rc) {
    rc = -1;
    std::string out;
#ifdef _WIN32
    FILE* f = _popen(cmd.c_str(), "r");
#else
    FILE* f = popen(cmd.c_str(), "r");
#endif
    if (!f) return out;
    char buf[4096];
    while (fgets(buf, sizeof(buf), f)) out += buf;
#ifdef _WIN32
    rc = _pclose(f);
#else
    rc = pclose(f);
#endif
    return out;
}

// ---------------------------------------------------------------- 清单

struct Spec {
    std::string type;
    std::string file;
    std::string path;
    std::string field;
    double value = 0;
};

struct Manifest {
    std::vector<std::pair<std::string, Spec>> items;
    std::vector<std::string> docs;

    const Spec* find(const std::string& key) const {
        for (const auto& kv : items)
            if (kv.first == key) return &kv.second;
        return nullptr;
    }
};

bool load_manifest(const std::string& path, Manifest& man) {
    std::string text;
    if (!read_file(path, text)) {
        std::cerr << "✗ 找不到绑定清单：" << path << "\n";
        return false;
    }
    JsonParser jp(text);
    bool parsed = false;
    Json root = jp.parse(parsed);
    if (!parsed || root.kind != Json::Obj) {
        std::cerr << "✗ 绑定清单不是合法 JSON：" << path << "\n";
        return false;
    }
    for (const auto& kv : root.obj) {
        if (kv.first == "_docs") {
            if (kv.second.kind == Json::Arr)
                for (const auto& d : kv.second.arr)
                    if (d.kind == Json::Str) man.docs.push_back(d.str);
            continue;
        }
        if (!kv.first.empty() && kv.first[0] == '_') continue;
        if (kv.second.kind != Json::Obj) continue;
        Spec s;
        if (const Json* v = kv.second.get("type")) s.type = v->str;
        if (const Json* v = kv.second.get("file")) s.file = v->str;
        if (const Json* v = kv.second.get("path")) s.path = v->str;
        if (const Json* v = kv.second.get("field")) s.field = v->str;
        if (const Json* v = kv.second.get("value")) s.value = v->num;
        man.items.emplace_back(kv.first, std::move(s));
    }
    return true;
}

const Json* dig(const Json& root, const std::string& path) {
    const Json* cur = &root;
    std::stringstream ss(path);
    std::string part;
    while (std::getline(ss, part, '.')) {
        if (part.empty()) continue;
        if (cur->kind == Json::Arr) {
            size_t idx = static_cast<size_t>(std::stoul(part));
            if (idx >= cur->arr.size()) return nullptr;
            cur = &cur->arr[idx];
        } else if (cur->kind == Json::Obj) {
            const Json* nxt = cur->get(part);
            if (!nxt) return nullptr;
            cur = nxt;
        } else {
            return nullptr;
        }
    }
    return cur;
}

struct Resolved {
    bool ok = false;
    double value = 0;
    std::string why;
};

Resolved resolve(const Spec& s, const std::string& root_dir, const std::string& tools_dir) {
    Resolved r;
    if (s.type == "const") {
        r.ok = true;
        r.value = s.value;
        r.why = "常量";
        return r;
    }
    if (s.type == "audit") {
        const char* py = std::getenv("DOCNUMS_PYTHON");
        std::string python = py ? py : "python";
        int rc = -1;
        std::string out = run_capture(python + " \"" + join_path(tools_dir, "audit_numbers.py") +
                                          "\" --json",
                                      rc);
        if (rc != 0 || out.empty()) {
            r.why = "audit_numbers.py 失败";
            return r;
        }
        JsonParser jp(out);
        bool parsed = false;
        Json root = jp.parse(parsed);
        if (!parsed) {
            r.why = "audit 输出不是 JSON";
            return r;
        }
        const Json* v = root.get(s.field);
        if (!v || v->kind != Json::Num) {
            r.why = "audit 没有字段 " + s.field;
            return r;
        }
        r.ok = true;
        r.value = v->num;
        r.why = "audit." + s.field;
        return r;
    }
    std::string text;
    if (!read_file(join_path(root_dir, s.file), text)) {
        r.why = "缺少来源文件 " + s.file;
        return r;
    }
    JsonParser jp(text);
    bool parsed = false;
    Json root = jp.parse(parsed);
    if (!parsed) {
        r.why = s.file + " 不是合法 JSON";
        return r;
    }
    if (s.type == "field") {
        const Json* v = dig(root, s.path);
        if (!v || v->kind != Json::Num) {
            r.why = s.file + ":" + s.path + " 不是数字";
            return r;
        }
        r.ok = true;
        r.value = v->num;
        r.why = s.file + ":" + s.path;
        return r;
    }
    if (s.type == "count") {
        const Json* v = dig(root, s.path);
        if (!v) {
            r.why = s.file + ":" + s.path + " 不存在";
            return r;
        }
        r.ok = true;
        r.value = static_cast<double>(v->kind == Json::Arr ? v->arr.size() : v->obj.size());
        r.why = s.file + ":" + s.path + ".len";
        return r;
    }
    if (s.type == "sum") {
        const Json* v = dig(root, s.path);
        if (!v || v->kind != Json::Arr) {
            r.why = s.file + ":" + s.path + " 不是数组";
            return r;
        }
        double total = 0;
        for (const auto& row : v->arr) {
            const Json* f = row.get(s.field);
            if (f && f->kind == Json::Num) total += f->num;
        }
        r.ok = true;
        r.value = total;
        r.why = "Σ " + s.file + ":" + s.path + "[]." + s.field;
        return r;
    }
    r.why = "未知的 type=" + s.type;
    return r;
}

// ---------------------------------------------------------------- 命令

int cmd_list(const Manifest& man, const std::string& root_dir, const std::string& tools_dir) {
    std::printf("%s%s   %s\n", pad_right("key", 24).c_str(),
                pad_left("当前值", 16).c_str(), "来源");
    for (const auto& kv : man.items) {
        Resolved r = resolve(kv.second, root_dir, tools_dir);
        std::printf("%s%s   %s\n", pad_right(kv.first, 24).c_str(),
                    pad_left(r.ok ? fmt(r.value) : std::string("—"), 16).c_str(),
                    r.why.c_str());
    }
    return 0;
}

int cmd_check(const Manifest& man, const std::vector<std::string>& docs,
              const std::string& root_dir, const std::string& tools_dir) {
    const std::regex bound(R"(([0-9][0-9,]*)[ \t]*<!--dn:([A-Za-z0-9_.-]+)-->)");
    int bad = 0, total = 0;
    for (const auto& doc : docs) {
        std::string p = is_abs(doc) ? doc : join_path(root_dir, doc);
        std::string text;
        if (!read_file(p, text)) {
            std::cerr << "✗ 文档不存在：" << doc << "\n";
            ++bad;
            continue;
        }
        for (auto it = std::sregex_iterator(text.begin(), text.end(), bound);
             it != std::sregex_iterator(); ++it) {
            ++total;
            std::string num = strip_commas((*it)[1].str());
            std::string key = (*it)[2].str();
            const Spec* spec = man.find(key);
            if (!spec) {
                std::printf("  ? %s: 未知绑定键 %s\n", doc.c_str(), key.c_str());
                ++bad;
                continue;
            }
            Resolved r = resolve(*spec, root_dir, tools_dir);
            long long got = std::atoll(num.c_str());
            if (!r.ok) {
                std::printf("  ✗ %s: 来源不可用（%s）\n", key.c_str(), r.why.c_str());
                ++bad;
            } else if (static_cast<long long>(r.value + 0.5) != got) {
                std::printf("  ✗ %s: 文档写 %lld，来源 %s（%s）\n", key.c_str(), got,
                            fmt(r.value).c_str(), r.why.c_str());
                ++bad;
            }
        }
    }
    std::printf("\n绑定检查：%d 处，", total);
    if (bad == 0) {
        std::printf("全部一致\n");
    } else {
        std::printf("%d 处不一致\n", bad);
    }
    return bad == 0 ? 0 : 1;
}

//: 数字两侧不能紧贴标识符字符（等价于 Python 版的 lookaround）
bool bounded(const std::string& s, size_t begin, size_t end) {
    auto ident = [](char c) {
        return std::isalnum(static_cast<unsigned char>(c)) || c == '_' || c == '.' ||
               c == '/' || c == '\\' || c == '-';
    };
    if (begin > 0 && ident(s[begin - 1])) return false;
    if (end < s.size() && ident(s[end])) return false;
    return true;
}

int cmd_scan(const Manifest& man, const std::vector<std::string>& docs,
             const std::string& root_dir, const std::string& tools_dir) {
    std::vector<long long> known;
    for (const auto& kv : man.items) {
        Resolved r = resolve(kv.second, root_dir, tools_dir);
        if (r.ok) known.push_back(static_cast<long long>(r.value + 0.5));
    }
    // 块内可能还有反引号（如 ```bash ... `elench cost` ... ```），
    // 所以用惰性匹配 `[\s\S]*?`，不能用 `[^`]*`。
    const std::regex code_block(R"([`]{3}[\s\S]*?[`]{3})");
    const std::regex number_re(R"(([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{4,}))");
    for (const auto& doc : docs) {
        std::string p = is_abs(doc) ? doc : join_path(root_dir, doc);
        std::string text;
        if (!read_file(p, text)) continue;
        text = std::regex_replace(text, code_block, "");
        std::vector<std::pair<int, std::string>> suspects;
        for (auto it = std::sregex_iterator(text.begin(), text.end(), number_re);
             it != std::sregex_iterator(); ++it) {
            size_t b = static_cast<size_t>(it->position(0));
            size_t e = b + static_cast<size_t>(it->length(0));
            if (!bounded(text, b, e)) continue;
            std::string raw = (*it)[1].str();
            long long val = std::atoll(strip_commas(raw).c_str());
            if (std::find(known.begin(), known.end(), val) != known.end()) continue;
            int line = 1 + static_cast<int>(std::count(text.begin(), text.begin() + b, '\n'));
            suspects.emplace_back(line, raw);
        }
        std::printf("=== %s：%zu 个没有来源的数字\n", basename_of(doc).c_str(),
                    suspects.size());
        for (size_t k = 0; k < suspects.size() && k < 40; ++k)
            std::printf("  行 %5d  %s\n", suspects[k].first, suspects[k].second.c_str());
    }
    return 0;
}

}  // namespace

int main(int argc, char** argv) {
    std::vector<std::string> args = get_args(argc, argv);
    std::string self = args.empty() ? "docnums" : args[0];
    std::string self_dir = dirname_of(self);
    std::string root_dir;

    // 默认仓库根：可执行文件在 cpp/docnums/build/ 时往上三级
    for (size_t i = 1; i + 1 < args.size(); ++i) {
        if (args[i] == "--root") root_dir = args[i + 1];
    }
    // 默认仓库根：从可执行文件所在目录**向上找**含 `tools/doc_numbers.json` 的目录。
    // 比"固定上三级"稳：在 build/、out/ 或任意子目录里都能用。
    if (root_dir.empty()) {
        std::string cur = self_dir;
        for (int depth = 0; depth < 6; ++depth) {
            std::ifstream probe(std::filesystem::u8path(join_path(cur, "tools/doc_numbers.json")));
            if (probe.good()) {
                root_dir = cur;
                break;
            }
            std::string up = dirname_of(cur);
            if (up == cur || up.empty()) break;
            cur = up;
        }
        if (root_dir.empty()) root_dir = ".";
    }

    Manifest man;
    std::string tools_dir = join_path(root_dir, "tools");
    bool ok = load_manifest(join_path(tools_dir, "doc_numbers.json"), man);
    if (!ok) ok = load_manifest(join_path(self_dir, "doc_numbers.json"), man);
    if (!ok) return 2;

    bool do_list = false, do_scan = false;
    std::vector<std::string> docs;
    for (size_t i = 1; i < args.size(); ++i) {
        const std::string& a = args[i];
        if (a == "--list") do_list = true;
        else if (a == "--scan") do_scan = true;
        else if (a == "--root") ++i;
        else if (!a.empty() && a[0] == '-') continue;
        else docs.push_back(a);
    }
    if (docs.empty()) docs = man.docs;
    if (docs.empty()) docs.push_back("文档/其他/预印本/预印本-v0.1.md");

    if (do_list) return cmd_list(man, root_dir, tools_dir);
    if (do_scan) return cmd_scan(man, docs, root_dir, tools_dir);
    return cmd_check(man, docs, root_dir, tools_dir);
}
