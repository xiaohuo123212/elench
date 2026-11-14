// demo/probe-libs/lexer/main.cpp —— 小词法器：数字/标识符/符号/空白 + 关键字表；输入内嵌。
#include <cctype>
#include <cstdio>
#include <string>
#include <vector>

struct Token {
    int kind;              // 0=num, 1=ident, 2=sym, 3=eof, 9=bad, 100+=keyword
    std::string text;
    size_t value;
    size_t pos;
};

static const char *const KEYWORDS[] = {"if", "else", "while", "for", "return", "const"};
static const int KEYWORD_COUNT = 6;

static int keyword_kind(const std::string &word) {
    for (int k = 0; k < KEYWORD_COUNT; k++) {
        if (word == KEYWORDS[k]) {
            return 100 + k;
        }
    }
    return -1;
}

static bool is_symbol(char c) {
    return c == '+' || c == '-' || c == '=' || c == '(' || c == ')' || c == ';' || c == '<' ||
           c == '>' || c == '{' || c == '}';
}

struct Lexer {
    std::string src;
    size_t i;

    Lexer(const std::string &s) {
        src = s;
        i = 0;
    }

    bool at_end() const { return i >= src.size(); }

    char peek() const { return at_end() ? '\0' : src[i]; }

    void skip_space() {
        while (i < src.size() && std::isspace(static_cast<unsigned char>(src[i]))) {
            i = i + 1;
        }
    }

    Token blank(int kind) const {
        Token t;
        t.kind = kind;
        t.text = "";
        t.value = 0;
        t.pos = i;
        return t;
    }

    Token number() {
        const size_t start = i;
        size_t v = 0;
        while (i < src.size() && std::isdigit(static_cast<unsigned char>(src[i]))) {
            v = v * 10 + static_cast<size_t>(src[i] - '0');
            i = i + 1;
        }
        Token t;
        t.kind = 0;
        t.text = src.substr(start, i - start);
        t.value = v;
        t.pos = start;
        return t;
    }

    Token ident() {
        const size_t start = i;
        i = i + 1;
        while (i < src.size() &&
               (std::isalnum(static_cast<unsigned char>(src[i])) || src[i] == '_')) {
            i = i + 1;
        }
        Token t;
        t.text = src.substr(start, i - start);
        t.value = 0;
        t.pos = start;
        const int kw = keyword_kind(t.text);
        t.kind = kw >= 0 ? kw : 1;
        return t;
    }

    Token next() {
        skip_space();
        if (at_end()) {
            return blank(3);
        }
        const char c = peek();
        if (std::isdigit(static_cast<unsigned char>(c))) {
            return number();
        }
        if (std::isalpha(static_cast<unsigned char>(c))) {
            return ident();
        }
        if (is_symbol(c)) {
            Token t = blank(2);
            t.text = std::string(1, c);
            i = i + 1;
            return t;
        }
        Token t = blank(9);
        t.text = std::string(1, c);
        i = i + 1;
        return t;
    }

    std::vector<Token> run() {
        std::vector<Token> out;
        Token t = next();
        while (t.kind != 3) {
            out.push_back(t);
            t = next();
        }
        return out;
    }
};
static std::string kind_name(int kind) {
    if (kind == 0) return "num";
    if (kind == 1) return "ident";
    if (kind == 2) return "sym";
    if (kind == 3) return "eof";
    if (kind == 9) return "bad";
    return "keyword";
}

int main() {
    const std::string src = "if count1 = 42 + 7;\nwhile (count1 > 0) { count1 = count1 - 1; }";
    Lexer lx(src);
    const std::vector<Token> toks = lx.run();
    size_t nums = 0;
    size_t idents = 0;
    size_t syms = 0;
    size_t kws = 0;
    for (const Token &t : toks) {
        if (t.kind == 0) nums++;
        if (t.kind == 1) idents++;
        if (t.kind == 2) syms++;
        if (t.kind >= 100) kws++;
        std::printf("%zu %s %s %zu\n", t.pos, kind_name(t.kind).c_str(), t.text.c_str(), t.value);
    }
    std::printf("tokens=%zu nums=%zu idents=%zu syms=%zu keywords=%zu\n", toks.size(), nums,
                idents, syms, kws);
    return 0;
}
