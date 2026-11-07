// calculator.tu —— 本模块的全部声明（账本）
//
// 按规格 §0.5：所有声明都在 .tu，.ai 里零声明。实现见同目录 calculator.ai。
//
// v0.68 起：
//   error Name;        模块级错误类型，`T!` 函数的失败类型
//   func f(...) -> T!  参数后置（唯一刻意保留的非 C++ 处）
//   const T n = ...    不可变绑定，类型前置
//   T n = ...          可变绑定，类型前置
//   块只用 }           不写 end
//
// 填充方式：人写签名与 target，AI 补 effects / requires / model，人读一遍认账；cost 由编译器算。

// v0.68：容器已在语言内建（vector / array），不再需要外部 use；
// 这一行保留，用于演示 `use` 的写法与检查。
use core.mem.alloc;

const MAX_TOKENS: u64 = 4096;
const MAX_DEPTH: u64 = 64;
const MAX_DIGITS: u64 = 19;

// 字符字面量（G18）：'c' 是 u64 的语法糖，值等于该字符的码位。
const SPACE: u64 = ' ';
const STAR: u64 = '*';
const PLUS: u64 = '+';
const MINUS: u64 = '-';
const SLASH: u64 = '/';
const ZERO: u64 = '0';
const NINE: u64 = '9';
const OPEN_PAREN: u64 = '(';
const CLOSE_PAREN: u64 = ')';

enum Token {
    Number(value: i64),
    Plus,
    Minus,
    Star,
    Slash,
    OpenParenthesis,
    CloseParenthesis,
        }

// 注意 Arith(cause: Arithmetic) 这个变体：
// N4 要求 `?` 注入时，目标枚举里必须恰有一个变体，其载荷类型等于来源错误类型。
// 旧版把 Overflow 与 DivisionByZero 平铺成两个无载荷变体，导致 (a + b)? 无处可注入。
enum CalcError {
    Arith(cause: Arithmetic),
    UnexpectedByte(offset: u64),
    NumberTooLong,
    EmptyExpression,
    UnbalancedParenthesis,
    Capacity,
        }

// v0.68：模块级错误类型（`T!` 的失败类型统一到这里）
error CalcError;

// ─────────────────────────────────────────────────────────────
// 公开接口
// ─────────────────────────────────────────────────────────────

spec evaluate(
              const vector<u8>& text) -> i64! {
    effects { alloc };
    requires text.length() <= MAX_TOKENS;

    target {
        time: { max: O(text.length()) },
        allocation: { max: O(text.length()) },
    }
    ;
}

// ─────────────────────────────────────────────────────────────
// 内部函数（私有：可省 target，但 effects / 签名必须有）
// ─────────────────────────────────────────────────────────────

spec tokenize(
              const vector<u8>& text) -> array<Token, MAX_TOKENS>! {
    effects { alloc };
}

spec read_number(
                 const vector<u8>& text, u64 start) -> (i64, u64)! {
    effects { };
}

spec reduce(
            const array<Token, MAX_TOKENS>& tokens) -> i64! {
    effects { };
}

spec pop_until_open(array<i64, MAX_DEPTH>& values, array<Token,
                    MAX_DEPTH>& operators) -> bool! {
    effects { };
}

// 这个函数的 spec 多一条 repeats：
// 它被用作 while 条件，上界由 repeats 提供（规格 T13 情形二）。
spec should_reduce(
                   const array<Token, MAX_DEPTH>& operators, Token next) -> bool {
    effects { };
    repeats operators.length();
}

spec precedence(Token token) -> u64 {
    effects { };
}

spec apply_top(array<i64, MAX_DEPTH>& values, array<Token,
               MAX_DEPTH>& operators) -> i64! {
    effects { };
}

spec is_digit(u64 byte) -> bool {
    effects { };
    // 用作 while 条件的一员（`index < total and is_digit(...)`）：
    // 上界由另一侧的 `index < total` 给出，这里声明"最多数到剩余字节数"。
    repeats total;
}

spec digit_value(u64 byte) -> i64 {
    effects { };
}
