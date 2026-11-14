#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""
parser.py —— AI 语言 M0 解析器（宽松超集）

目标（M0 验收 ⑦）：
  · 完整解析 样例/calculator.tu 与 样例/calculator.eln
  · 对非法输入给出带 span 的诊断，并能恢复到下一个可解析位置

设计取舍：
  · 「宽松超集」——接受规格允许的一切，额外接受少量将来可能出现的形状，
    真正收紧靠 lint（L3/L4/L5），不靠解析失败。
  · 注释是 trivia，不进 token 流（CST 归属由后续 formatter 处理）。
  · 这里产出 AST（不含注释位置）；CST 留给 fmt 阶段。

用法：
  python parser.py <file.tu|file.eln>
  python parser.py --json <file>
"""

from __future__ import annotations

import copy
import json
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import compat as _compat
from lexer import Kind, Lexer, REMOVED_KEYWORDS, Token
from typenames import canon as _canon


# ---------------------------------------------------------------- 源码读取

class SourceEncodingError(Exception):
    """源文件不是 UTF-8（批次 5 第 22 条）。

    以前 `open(..., encoding="utf-8")` 直接把 `UnicodeDecodeError` 抛给
    用户：`elench check` 打一整段 traceback，连"哪个文件"都要自己从栈里找。
    源文件**只接受 UTF-8**（含无 BOM）；GBK / UTF-16 一律给一句可读的话。
    """

    def __init__(self, path: str, detail: str = ""):
        self.path = path
        self.detail = detail
        super().__init__(f"文件不是 UTF-8 编码：{path}")


def read_source_text(path: str) -> str:
    """按 UTF-8 读源码；失败时转成 `SourceEncodingError`（带路径）。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except UnicodeDecodeError as e:
        raise SourceEncodingError(path, str(e)) from e


# ---------------------------------------------------------------- AST

@dataclass
class Node:
    """统一的 AST 节点。kind 是字符串，便于打印与测试。"""
    kind: str
    line: int = 0
    col: int = 0
    name: Optional[str] = None
    value: object = None
    children: List["Node"] = field(default_factory=list)
    #: **只有 `for` 节点用**（2026-11-10 夜）：range-for 的绑定类别。
    #:
    #: | 值 | 源码 | 语义 |
    #: |---|---|---|
    #: | `"mut"` | `for (auto& x : xs)` / `for (T& x : xs)` | 可变引用，**不拷贝** |
    #: | `"const"` | `for (const auto& x : xs)` / `for (const T& x : xs)` | 只读引用，**不拷贝** |
    #: | `None` | `for (auto x : xs)` / `for (T x : xs)` | 按值，每轮拷一份 |
    #:
    #: **为什么不放进 `value`**：`value` 是通用字段（`for` 上曾被已删除的
    #: 带下标写法占用），而 `ref` 是**语义**、要能被 interp / 成本域 / 类型检查
    #: 各自读到同一个字段。`dump()` / `to_dict()` 不打印它——AST 形状没变，
    #: 测试里的快照保持可比。
    ref: Optional[str] = None

    def add(self, *nodes: "Node") -> "Node":
        for n in nodes:
            if n is not None:
                self.children.append(n)
        return self

    def dump(self, depth: int = 0) -> str:
        pad = "  " * depth
        head = f"{pad}{self.kind}"
        if self.name:
            head += f" {self.name}"
        if self.value is not None:
            head += f" = {self.value!r}"
        head += f"   @{self.line}:{self.col}"
        out = [head]
        for c in self.children:
            out.append(c.dump(depth + 1))
        return "\n".join(out)

    def to_dict(self) -> dict:
        d = {"kind": self.kind, "line": self.line, "col": self.col}
        if self.name:
            d["name"] = self.name
        if self.value is not None:
            d["value"] = self.value
        if self.children:
            d["children"] = [c.to_dict() for c in self.children]
        return d


#: **"已声明，但类型推不出"** 的哨兵（第 13 条的接收者推断用）。
#:
#: 与 `None` 的区别很重要：
#:   · `None` = 这个名字在本文件里**根本没登记过**（可能是漏掉的成员方法
#:     → `E-Compat-MethodUnresolved`）；
#:   · `_OPAQUE_TY` = 登记过、类型待定（`auto x = …;` 的 `x`）——**不能猜**，
#:     交给下游既有诊断，宁可少报也不误报。
_OPAQUE_TY = object()


@dataclass
class Diagnostic:
    code: str
    message: str
    line: int
    col: int
    start: int = 0
    end: int = 0
    #: **档位**（2026-11-11，P0 兼容层）：默认 `error`，与以前逐字节等价。
    #: `--compat` 下"接受但要说一句"的拼写（如 `W-Compat-Alias`）用 `warning`。
    #: `to_dict()` **不输出它**——`parse --json` 的形状保持原样。
    severity: str = "error"

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message,
                "line": self.line, "col": self.col,
                "start": self.start, "end": self.end}


@dataclass
class ParseResult:
    module: Optional[Node] = None
    diagnostics: List[Diagnostic] = field(default_factory=list)
    tokens: List[Token] = field(default_factory=list)
    # 宽松超集下"能接受但不规范"的发现（L3 等）。默认不算错误，严格模式才提升。
    soft: List[Diagnostic] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.diagnostics


class ParseError(Exception):
    def __init__(self, code: str, message: str, tok: Token):
        super().__init__(message)
        self.code = code
        self.message = message
        self.tok = tok


# ---------------------------------------------------------------- 表

ITEM_START = {
    Kind.USE, Kind.CONST, Kind.ENUM, Kind.STRUCT, Kind.TRAIT, Kind.IMPL,
    Kind.SPEC, Kind.FN, Kind.PUB, Kind.REWRITE, Kind.EXTERN, Kind.EXTERNAL,
}

# 必须在块末尾出现 `end <kind>` 的构造
END_KIND = {
    "fn": "fn",
    "spec": "spec",
    "enum": "enum",
    "struct": "struct",
    "trait": "trait",
    "impl": "impl",
    "choose": "choose",
    "if": "if",
    "while": "while",
    "for": "for",
    "match": "match",
}

#: v0.68 下线关键字的**改法**（码在 `lexer.REMOVED_KEYWORDS`）。
#:
#: **判据是位置，不是词形**（2026-11-10）：这批词同时是合法标识符
#: （`auto it = xs.end();` / `size_t end` / `long long val`），词法器只出
#: `IDENT`，由这里按语法位置报：
#:
#: | 词 | 报错位置 | 判据 |
#: |---|---|---|
#: | `end` | 块终结符 | 紧跟块收尾 `}`，或后跟种类名（`end fn` / `end if` …） |
#: | `val` / `var` / `let` | 语句起始的声明形状 | 后面跟一个标识符 |
#: | `fn` | 同上（旧函数声明） | 后面跟一个标识符 |
RETIRED_HINT = {
    "fn": "v0.73 起函数声明只写 C++ 形：`返回类型 名字(形参) { … }`"
          "（`fn` 与 `func` 都已下线）",
    "val": "v0.68 起用 `const 类型 名 = 值;`（类型前置）",
    "var": "v0.68 起用 `类型 名 = 值;`（类型前置，不写关键字）",
    "let": "改用 `类型 名 = 值;`（如 `u64 n = 7;`，可变）"
           "或 `const 类型 名 = 值;`（不可变）",
    "end": "v0.68 起块只用 `}` 收尾，不再写 `end`",
}

#: **`func f(...) -> T` 形已下线**（P4-0，2026-11-11）：只留 C++ 形。
#:
#: 为什么：C++ 形少 5 个 token/函数（`func` + `->` 与空格的代价），
#: 而且 AI 与 C++ 工具对 `T f(...)` 更熟——这条直接服务"AI 一次通过率"。
FUNC_REMOVED_HINT = (
    "v0.73 起函数声明只写 C++ 形：`返回类型 名字(形参) { … }`。\n"
    "改法：旧写法 `func add(u64 a, u64 b) -> u64 { … }` 改成 "
    "`u64 add(u64 a, u64 b) { … }`；\n"
    "无返回值写 `void f() { … }`；可能失败写 `T! f() { … }`。\n"
    "（`->` 后置返回也不再需要；`auto f(…) -> T` 仍收，但推荐类型前置。）")

#: 语句起始的**旧声明关键字**（`val x = 1;` / `var x = 1;` / `let x = 1;`）。
OLD_DECL_WORDS = ("val", "var", "let")

#: 顶层**带名字**的 item 种类：`namespace` 展平时按这几种查重名。
#: 本语言一个文件一个全局作用域，同名 item 会互相遮蔽（checker 只查
#: `fn` / `spec`），所以 `struct` / `enum` / `const` 也必须在这里拦。
NAMED_TOP_ITEMS = ("fn", "spec", "struct", "enum", "const", "extern")

#: C++ 迭代器接口的方法名（2026-11-10）：本语言**没有迭代器**。
#:
#: 以前同一个缺口有两种说法：`xs.end()` 撞上「`end` 已下线」的退役关键字诊断
#: （完全无关——读者会去查 `end` 关键字，而不是意识到"没有迭代器"），
#: `xs.rbegin()` 却报一句「期望 'in'」。这里统一成一条 `E-Call-UnknownMethod`：
#: **一个缺口一种说法**。2026-11-10 晚些时候词法器改成只出 `IDENT`，
#: 退役关键字的判据整个移到 parser 的**位置**判断（见 `_at_end_terminator`），
#: 成员位置本来也不会再撞上它。
ITERATOR_METHODS = ("begin", "end", "rbegin", "rend")

ITERATOR_METHODS_MSG = (
    "本语言没有迭代器：`begin`/`end`/`rbegin`/`rend`/`*it`/`++it` 都不存在"
    "（`{name}` 是 C++ 的迭代器接口）；遍历写 `for (auto& x : xs)`（不拷贝）"
    "或 `for (auto x : xs)`（按值拷贝），"
    "倒序写 `for (u64 i = 0; i < xs.length(); i = i + 1)` 里取 "
    "`xs[xs.length() - 1 - i]`")

#: 三段式 `for` 里的迭代器（2026-11-10 晚）：**三段式本身已经接受**，
#: 但 `auto it = xs.begin()` 这类 init 仍然解析不了——语言里没有迭代器，
#: 也没有 `*it` / `++it` 的取值语义。这条诊断只在**这个形状**上报，
#: `E-For-NoIterator` 这个码因此保留下来（不再覆盖整个三段式）。
FOR_C_STYLE_ITERATOR_MSG = (
    "本语言没有迭代器：三段式 `for (初始化; 条件; 推进)` 的**语法是接受的**，"
    "但 `begin` / `end` / `rbegin` / `rend` 不存在（它们是 C++ 的迭代器接口；"
    "语言里没有 `*it` / `++it` 的取值语义）。\n"
    "改法：遍历容器写 `for (auto& x : xs)`（不拷贝）或 `for (auto x : xs)`"
    "（按值拷贝）；要下标写 "
    "`for (u64 i = 0; i < xs.length(); i = i + 1)`。\n"
    "例：`for (auto it = xs.begin(); it != xs.end(); ++it) { … }` → "
    "`for (auto& x : xs) { … }`。")

#: **`for (x in xs)` 已删除**（2026-11-10 夜，用户定：与 C++ range-for 重合）。
#:
#: 三种形式一起删：`for (x in xs)` / `for (i, x in xs)` / `for (i in 0..n)`，
#: `..` 区间也一并删（没有别的用途）。**删除功能的唯一补偿是报错教替代写法**，
#: 所以这条消息里三种替代都写全（遍历 / 下标 / 区间）。
FOR_IN_REMOVED_MSG = (
    "`for (x in xs)` 已删除（与 C++ range-for 重合）。\n"
    "  写 `for (auto& x : xs)`（不拷贝）或 `for (auto x : xs)`（按值拷贝）。\n"
    "  要下标写 `for (u64 i = 0; i < xs.length(); i = i + 1) { … xs.at(i) … }`。\n"
    "  要区间写 `for (u64 i = 0; i < n; i = i + 1)`。")

# ---------------------------------------------------------------- 第 12 条诊断
#
# 三段式 for 的**唯一标准迭代器形状**只在 `--compat` 下降级成索引循环
# （见 `Parser._compat_iter_for` 与 `compat.py` 第 12 条）。
# 下面每条拒绝**都说清"改写成本语言哪种 for"**——不允许只说"不支持"。

#: 唯一标准形状（每条拒绝的第一句都复述一遍，读者不用翻文档）。
FOR_ITER_ONLY_SHAPE = (
    "`--compat` 只认一种迭代器 for："
    "`for (auto it = xs.begin(); it != xs.end(); ++it) { … }`"
    "（`auto` 可换成具体类型；`it` 也可以在外声明）。")

#: 三条**本语言已有的**出路（每条拒绝都带，保证"教改法"不是空话）。
FOR_ITER_HOWTO = (
    "改法（三选一，都是本语言已有的写法）：\n"
    "· 遍历：`for (auto& x : xs) { … }`（不拷贝）或 `for (auto x : xs) { … }`（按值拷贝）；\n"
    "· 下标：`for (u64 i = 0; i < xs.size(); i = i + 1) { … xs[i] … }`；\n"
    "· 倒序：同一个下标循环里取 `xs[xs.size() - 1 - i]`。")

FOR_ITER_TERM_MSG = (
    "迭代器 for 的**终止条件**只支持 `it != xs.end()`（`end()` 的接收者必须与 "
    "`begin()` 的**同一个变量**），这里是 `%(text)s`。\n"
    "`it < xs.end()` / `it != xs.end() - 1` 这类写法要拿迭代器做比较或偏移；"
    "本语言没有迭代器，**无法等价降级**。\n" + FOR_ITER_HOWTO)

FOR_ITER_REVERSE_MSG = (
    "**反向迭代**（`rbegin` / `rend` / `--it`）不支持：降级目标只有正向索引循环 "
    "`idx = 0 → xs.size()`。\n"
    "改法：`for (u64 i = 0; i < xs.size(); i = i + 1) { … xs[xs.size() - 1 - i] … }`"
    "——第 `i` 轮取的是倒数第 `i` 个元素。")

FOR_ITER_ARITH_MSG = (
    "**迭代器不能参与算术**（`%(text)s`）：本语言没有迭代器，降级只定义了 "
    "`++it` → `idx = idx + 1` 这一种推进。\n"
    "改法：要按 2 / n 步跳就在**下标**上做算术："
    "`for (u64 i = 0; i < xs.size(); i = i + 2) { … xs[i] … }`；"
    "要从中间开始就把 init 写成 `u64 i = k`。")

FOR_ITER_MUTATE_MSG = (
    "循环体里改了正在遍历的容器 `%(name)s`（`%(m)s`）：C++ 里这会让迭代器失效"
    "（UB），本语言**直接拒绝**，不给可能悄悄错的降级。\n"
    "改法：先把新元素收进另一个容器，循环结束后再 `insert` / `push_back`；"
    "要删除就**倒着**用索引循环（`i` 从 `size()` 往下，删一个减一个）；"
    "如果只是想读元素，循环写回 `for (auto& x : %(name)s) { … }` 就没有这个问题。")

FOR_ITER_ESCAPE_MSG = (
    "迭代器 `%(name)s` 不能在 for 之外使用：`--compat` 把它降级成**下标**"
    "（循环开头新导入的 `u64`），循环结束后 `%(name)s` 里仍是循环前的旧值——"
    "C++ 里它在这里也已经离开作用域。\n"
    "改法：循环后还要用这个位置，就自己写索引循环 "
    "`u64 i = 0; while (i < xs.size()) { … i = i + 1; }`（`i` 在循环外可见）；"
    "只想知道「有没有命中」，在循环外先写 `u64 hits = 0;`，体里命中就 "
    "`hits = hits + 1;`。")

FOR_ITER_BARE_MSG = (
    "循环体里的 `%(name)s` 只能出现在 `*%(name)s`（取值）或 `%(name)s->f` / "
    "`%(name)s.f`（取字段）里：降级后它就是一个下标，**没有「迭代器值」这个概念**，"
    "比较 / 传参 / 打印 `%(name)s` 都无法等价降级。\n" + FOR_ITER_HOWTO)

FOR_ITER_SHADOW_MSG = (
    "循环体里重新声明了 `%(name)s`：`--compat` 把它当同名迭代器降级，"
    "遮蔽会让两处 `%(name)s` 悄悄变成不同的东西，所以直接拒绝。\n"
    "改法：把体里那个变量改个名字；或者把遍历写成 "
    "`for (auto& x : xs) { … }`——那样循环里根本没有 `%(name)s`，"
    "也就不存在遮蔽。")

FOR_ITER_SHAPE_MSG = (
    "这不是 `--compat` 认的迭代器 for 形状：%(detail)s\n"
    + FOR_ITER_ONLY_SHAPE + "\n" + FOR_ITER_HOWTO)


#: 第 12 条 b（`map` / `set`）：接收者推得出是键值容器、但**键类型**推不出来
#: （`map` 没写类型实参 / 类型来自 `auto`）。降级要声明 `vector<K>`，没有 K 就
#: 无法写出一条"与手写快照循环同形"的 AST——**不猜**，报清楚。
FOR_ITER_KEYTYPE_MSG = (
    "`%s` 是 `%s`，但**推不出键类型**（类型实参不全，或来自 `auto`）："
    "降级要先写 `vector<K> ks = %s.keys();`，没有 K 就写不出这一行。\n"
    "改法：把容器类型写全（`map<K, V> %s;` / `std::map<K, V> %s;`），"
    "或直接按位置取第 k 个键：先 `vector<K> ks = %s.keys();` 再对 `ks` 写"
    "三段式 for。")

#: 第 12 条 b：`map` 的迭代元素是 `(键, 值)` 二元组（C++ 的 `pair<const K,V>`），
#: **只有 first / second 两个字段**（= 本语言元组的 `.0` / `.1`）。
FOR_ITER_MAP_FIELD_MSG = (
    "`%(name)s` 是 `map` 的迭代器：降级后一轮元素是 `(键, 值)` 二元组，"
    "只有 `->first` / `->second`（= 元组的 `.0` / `.1`），没有 `%(field)s`。\n"
    "改法：`%(name)s->first` 取键、`%(name)s->second` 取值；要整体拿走就写 "
    "`(键, 值) kv = *%(name)s;` 然后 `kv.0` / `kv.1`。")

#: 第 12 条 b：`set` 的迭代元素就是元素本身，**没有值**（C++ 的 `*it` 是
#: `const T&`，`set` 的迭代器也没有 `->first` / `->second`）。
FOR_ITER_SET_FIELD_MSG = (
    "`%(name)s` 是 `set` 的迭代器：`set` 的元素就是**元素本身**，没有值，"
    "所以 `->first` / `->second` 都不存在（C++ 的 `set` 迭代器也一样）。\n"
    "改法：`*%(name)s` 就是元素；要键值对请用 `map`。")

#: 第 12 条 b：写进 `map` / `set` 的迭代元素。C++ 里 `it->second = v` 改的是
#: 容器里的值，而我们的降级把 `it->second` 映成 `m.get(键)`（一次**读**），
#: 写进去只会丢写——**拒绝**，不静默。
FOR_ITER_KEYED_WRITE_MSG = (
    "循环体里给 `%(name)s` 的元素赋值：`map` / `set` 的迭代元素是每轮现造的"
    "**只读视图**（`it->first` → 快照里的键、`it->second` → `m.get(键)`），"
    "写进去只会丢写。\n"
    "改法：改表请写 `m[键] = 新值` / `m.put(键, 新值)`；`set` 的元素不可改，"
    "要改就先 `remove` 再 `insert`（那是改容器，循环里同样要拒绝——见 "
    "`E-For-IteratorMutate`）。")

#: **语句起始的「本语言没有的 C++ 构造」诊断表**（2026-11-10，诊断质量轮）。
#:
#: ## 为什么要有这张表
#:
#: 语句起始的标识符会被 `_at_type_prefixed_decl()` 无条件当成**类型名**：
#: `static int counter = 0;` 于是报「期望 ';'，实际是 'ident'」——读者只会
#: 以为自己少了个分号，看不到「本语言没有 static」。`goto` / `do` / 标签
#: 今天已经单独修过；这里把**同一条根因**的其余关键字一次收齐：
#: **一个缺口一种说法，且说法里带改法。**
#:
#: ## 判据（宁窄勿宽）
#:
#: 只在**语句 / 顶层 item 起点**、且形状**不可能是**下列合法写法时拦：
#:
#: * `T x;` / `T x = …;`（`T` 是**未知类型名**——交给 `E-Type-Unknown`，
#:   这条表里没有的词一律不碰）；
#: * `x = …;` / `x += …;`（变量恰好叫 `new` / `this` 也是合法赋值）；
#: * `x.f = …;`（成员访问）。
#:
#: 其余形状（`IDENT {`、`IDENT :`、`IDENT IDENT`、`IDENT *`、`word (…) {`、
#: `word 1;` ……）在现有文法里都不是合法声明，拦下来只换成一条更准的诊断。
#:
#: ## 词 → (本语言没有的东西, 可照抄的改法)
#:
#: 顶层与语句共用同一张表（`typedef` / `using` / `class` 多半写在 `.tu` 顶层，
#: 那里同样会被当成非法 item）。`public` / `private` / `protected` 不在这里：
#: 它们是 C++ 的**成员访问说明符**，`struct` 体里的 `public:` 由 `parse_struct`
#: 接受并忽略；顶层的 `public func f()` 在 `parse_item` 里单独报
#: `E-Keyword-LongForm`（应写 `pub`）。`namespace` 也不在这里：顶层
#: `namespace X { … }` 由 `parse_namespace` **接受并展平**。
UNSUPPORTED_STMT_WORDS = {
    "auto": ("没有 `auto` 类型推断",
             "每个绑定都写全类型：`u64 x = 1;`；结构化绑定写 `const (u64, u64) t = f();` "
             "再取 `t.0` / `t.1`。`auto` 只出现在**函数声明的后置返回**写法里："
             "`auto f(const u64& x) -> u64 { … }`"),
    "throw": ("没有异常（`throw`）",
              "失败用返回值表达：`u64! f(u64 x) { if (x == 0) { return Err(E::Bad); } "
              "return x; }`，调用处 `?` 传播或 `match` 处理"),
    "try": ("没有异常（`try`）",
            "失败用返回值表达：`-> T!` + `return Err(...)`；调用处写 "
            "`match (r) { Ok(v) => { … } Err(e) => { … } _ => { … } }`"),
    "catch": ("没有异常（`catch`）",
              "用 `Result`（`-> T!`）的 `match` 分支处理错误：没有 `catch` 块，"
              "`Err` 必须显式 `match` 或 `?`"),
    "delete": ("没有手动内存管理（`delete`）",
               "容器自己管理内存：`vector<u64> xs = {1, 2, 3};`，离开作用域自动释放；"
               "没有 `new` / `delete`"),
    "new": ("没有手动内存管理（`new`）",
            "直接声明容器：`vector<u64> xs = {1, 2, 3};`（不需要 `new`，也没有 `delete`）；"
            "要定长写 `array<u64, 4>`"),
    "switch": ("没有 `switch`",
               "用 `match`：`match (n) { 0 => { … } 1 => { … } _ => { … } }`"
               "（MUST 以 `_` 兜底，否则 `E-Match-NoDefault`）"),
    "case": ("没有 `case`",
             "用 `match` 的臂：`match (n) { 0 => { … } _ => { … } }`"),
    "default": ("没有 `default:` 标签",
                "用 `match` 的 `_` 兜底臂：`match (n) { 0 => { … } _ => { … } }`"),
    "using": ("没有类型别名（`using`）",
              "类型写全：`vector<u64>`；泛型直接写在名字后："
              "`T pick<T>(T a, T b) { return a; }`"),
    "typedef": ("没有类型别名（`typedef`）",
                "类型写全：`vector<u64>`；泛型直接写在名字后："
                "`T pick<T>(T a, T b) { return a; }`"),
    "template": ("没有 `template<…>`",
                 "泛型直接写在名字后：`T pick<T>(T a, T b) { return a; }`；"
                 "类型参数写在 `spec` / `func` 名后的 `<>` 里"),
    "typename": ("没有 `typename`",
                 "泛型参数直接写 `T`：`func pick<T>(T a, T b) -> T`；"
                 "类型名写全，不需要 `typename` 消歧"),
    "static": ("没有 `static` 局部变量 / 静态存储",
               "计数器当形参传：`u64 tick(u64& n) { n = n + 1; return n; }`；"
               "常量放 `.tu` 顶层：`const N: u64 = 10;`"),
    "extern": ("`extern` 只能写在模块顶层",
               "函数体里没有 `extern`；外部函数在模块顶层声明："
               "`extern strlen(const vector<u8>&) -> u64 from \"ucrtbase\";`，"
               "调用放 `unsafe { … }`"),
    "sizeof": ("没有 `sizeof`",
               "宽度写在类型名里（`u64` 是 8 字节、`u32` 是 4）；容器长度用 `.length()`；"
               "定长容量用 `array<T, N>` 的 `N`"),
    "decltype": ("没有 `decltype` / 类型推断",
                 "每个绑定都写全类型：`u64 y = x;`——显式类型优于类型推断"),
    "union": ("没有 `union`",
              "用带载荷的 `enum` + `match`：`enum V { Num(u64), Text(vector<u8>), }`"),
    "class": ("没有 `class`",
              "用 `struct`：`struct Point { x: u64, y: u64, }`；"
              "方法写成自由函数：`u64 sum1(Point p) { return p.x + p.y; }`"),
    "private": ("没有访问控制（`private`）",
                "`struct` 体里的 `private:` **接受并被忽略**（字段本来就全部公开）；"
                "在语句 / 顶层位置没有 `private`：直接写 `struct P { x: u64, }`，"
                "内部约定用命名（`_x`）表达"),
    "protected": ("没有访问控制（`protected`）",
                  "`struct` 体里的 `protected:` **接受并被忽略**（没有继承，也没有"
                  "访问控制）；其余位置直接写 `struct P { x: u64, }`"),
    "virtual": ("没有虚函数 / 继承",
                "用带载荷的 `enum` + `match` 表达多态："
                "`enum Shape { Circle(u64), Square(u64), }`"),
    "override": ("没有继承 / `override`",
                 "同上：用 `enum` + `match`，没有基类可覆盖"),
    "friend": ("没有 `friend`",
               "写成自由函数并接收对象：`u64 area(Point p) { return p.x * p.y; }`"),
    "operator": ("没有运算符重载",
                 "写成具名函数：`Point add(Point a, Point b) { … }`；"
                 "排序比较器写 `func by_x(Point a, Point b) -> bool`"),
    "this": ("没有成员函数 / `this`",
             "把对象显式传进来：`u64 area(Point p) { return p.x * p.y; }`"),
    # `nullptr` **是值**（2026-11-10 起指针存在），但**不是语句**：
    # 单独一句 `nullptr;` 没有效果，也不该落到「未声明的名字」。
    #（`nullptr` 的**语句态**由 `parse_stmt` 里一条专门分支报，不走这里的模板：
    #  模板开头是「本语言没有 `nullptr`」——而 `nullptr` 现在是合法的**值**，
    #  那句话会自相矛盾。）
    "NULL": ("没有空指针（`NULL`）",
             "可缺值用 `optional<T>`：`option_none()` / `option_some(v)`，"
             "用 `option_is_some` / `option_unwrap_or` 取值"),
}

#: `++` / `--` **只收独立语句**（2026-11-10）。表达式位置一律明确拒绝：
#: 本语言的表达式没有「取值的同时自增」这层语义，静默解释成别的东西更糟。
INC_DEC_NOT_EXPR = (
    "`++` / `--` 只能作独立语句（`++i;` / `i++;` / `--i;` / `i--;`），不能当表达式用："
    "本语言没有「取值的同时自增」这层语义。要这个效果就拆成两句："
    "`x = i; i = i + 1;`")

# 二元运算符：kind -> (左结合优先级, 右结合优先级)
#
# **位运算按 C 的层次**（2026-11-09）：
#   or < and < | < ^ < & < == != < 关系 < << >> < + - < * / % < ** < |>
# 现有语言把 `==`/`!=` 与关系运算放在同一层；这里按 C 拆开了
# （`a == b < c` 现在是 `a == (b < c)`）。`|>` 仍是最紧的二元运算。
# **`..` 区间已删除**（2026-11-10 夜）：它只服务于 `for (i in 0..n)`，
# 而 `in` 遍历整个删除了（与 C++ range-for 重合）。现在 `..` 不是运算符，
# 语言里没有别的地方要它。
BINARY = {
    # **逻辑运算符只有符号写法**（2026-11-11）：`||` / `&&`。词形 `or` / `and`
    # 已退役（词法器报 `E-Logic-WordOp`），所以这里没有 `Kind.OR` / `Kind.AND`
    # 两行——同一个概念只留一种写法，优先级表也只留一行。
    Kind.PIPEPIPE: (1, 2),
    Kind.AMPAMP: (3, 4),
    Kind.PIPE: (5, 6),
    Kind.CARET: (7, 8),
    Kind.AMP: (9, 10),
    Kind.EQEQ: (11, 12), Kind.NE: (11, 12),
    Kind.LT: (13, 14), Kind.LE: (13, 14), Kind.GT: (13, 14), Kind.GE: (13, 14),
    Kind.SHL: (17, 18),
    Kind.PLUS: (19, 20), Kind.MINUS: (19, 20), Kind.OMINUS: (19, 20),
    Kind.STAR: (21, 22), Kind.SLASH: (21, 22), Kind.PERCENT: (21, 22),
    # **`**` 是右结合的幂**（2026-11-10）：`l_bp > r_bp` 才是右结合，
    # 于是 `2 ** 3 ** 2` = `2 ** (3 ** 2)` = 512（Python/Ruby 同款）。
    # 层次在 `* / %` 之上、一元前缀（`PREFIX_BP`）之下——所以 `-2 ** 2`
    # 是 `(-2) ** 2` = 4（一元先绑定），`2 ** -1` 也仍能解析
    # （右操作数走 `parse_prefix`，负号在那边吃掉）。
    # 以前没有这个 token：`a ** b` 被读成 `a * (*b)`，前缀 `*` 是恒等解引用，
    # 于是 `2 ** 10` 静默得 `20`。
    Kind.STAR_STAR: (24, 23),
    Kind.PIPE_GT: (23, 24),
}

#: 一元运算符的操作数绑定力。**必须大于所有二元层**（原来是写死的 15，
#: 表变大后要跟着走；集中一处避免再漂移）。
PREFIX_BP = 25

#: 运算符 token → 本语言关键写法的 **AST 值**（2026-11-11）。
#:
#: 下游按 `binary.value` 认逻辑运算符。**规范化名就是源码写法**（`"&&"` /
#: `"||"`），不是旧词形 `"and"` / `"or"`——词形运算符已退役，AST 里再留旧名
#: 会让「AST 值 == 源码记号」这层对应断掉（转译 / 报告 / 成本日志都要多一次
#: 映射）。`!` 的一元节点同理（`value="!"`）。
LOGIC_ALIAS = {Kind.AMPAMP: "&&", Kind.PIPEPIPE: "||"}

#: `>>` 不是词法 token：`Vec<Vec<u8>>` 的 `>>` 是**两个 `>`**，把它合成
#: 一个 token 会打断嵌套泛型。所以在表达式里把**相邻**的两个 `>` 合成右移。
SHR_BP = (17, 18)

#: **三目 `c ? a : b`**（2026-11-13）的绑定力。
#:
#: 比所有二元层都松、比赋值紧——与 C/C++ 一致：`a + b ? c : d` =
#: `(a + b) ? c : d`。选 **0**（而不是负数）是因为 `parse_expr` 的默认
#: `min_bp` 就是 0：所有"整条表达式"的位置都能接三目，而
#: `parse_expr(右结合 bp > 0)` 的位置（二元运算符的右操作数、一元前缀的
#: 操作数）**自动**把它挡在外面。右结合由"else 支用同一个 0 递归"得到：
#: `a ? b : c ? d : e` = `a ? b : (c ? d : e)`。
TERNARY_BP = 0

# 这些关键字可以开始一个表达式（用于错误恢复与 `if` 条件判断）
EXPR_START = {
    Kind.IDENT, Kind.INT, Kind.FLOAT, Kind.CHAR, Kind.STRING,
    Kind.LPAREN, Kind.LBRACKET, Kind.SELF, Kind.TRUE, Kind.FALSE,
    Kind.BANG, Kind.MINUS, Kind.STAR, Kind.AMP, Kind.TILDE, Kind.DYN,
    # `&&` / `||` 现在只能作二元运算符（词形 `and` / `or` 已退役）。
    Kind.AMPAMP, Kind.PIPEPIPE,
}

# ---------------------------------------------------------------- C++ 兼容层
#
# **R1-A（2026-11-10；依据 `文档/设计/目前设计/审核-命名与语法对照.md` §8.2/§8.3/
# §8.10/§8.11）**。产品目标：**一个 C++ 程序只改文件名就能当 `.eln` 用**。
#
# 这一层只做**名字归一**，不做语义扩展：
#
#   · 函数声明用 C++ 形状（`u64 f(const u64& x) { … }` / `auto f(…) -> u64 { … }`），
#     `func` **仍然接受**（旧代码不用改），但不再必需；
#   · `std::` 前缀与短名是**同一个类型**（§8.11 的原文要求）；
#   · `int` / `unsigned` / `long` / `size_t` / `char` … 映射到已有的宽度类型；
#   · `uint32_t` / `int16_t` 这类定长名**按位宽精确**映射（不放大）。
#
# **纪律**：只改「写出来的是哪个名字」，不改值域。`int` 在这里是 **64 位**——
# 与 C++ 的 32 位 `int` **溢出语义不同**，这条差异写在 `文档/能力边界.md`，
# 不允许只在代码注释里说。

#: C++ 标量拼写的词（可以连写：`unsigned long long` = 3 个词）。
#: `char` 在词法层是 `Kind.CHAR`（与字符字面量共用），扫描时也算一个词。
CXX_SCALAR_WORDS = frozenset({
    "signed", "unsigned", "short", "long", "int", "char", "double", "float", "bool",
})

#: C++ 标量拼写 → 本语言类型名（**方案甲：整数一律升到 64 位**）。
#:
#: 为什么是 64 位而不是 32 位：解释器的 `i8/i16/i32` 只在**赋值/返回/入容器**时
#: 按位宽回绕，中间表达式仍是 64 位 Python 整数（`src/interp.py` 的 `_convert_scalar`）；
#: 而 C++ 的有符号溢出本来就是 **UB**，映射到 64 位落在「C++ 不保证」的区间里。
#: 反例只有**无符号**：`unsigned` 的 32 位回绕是**有定义**的，映射到 `u64` 就
#: 静默不同——所以 `unsigned` 只承诺「名字认」，不承诺 32 位回绕；
#: 要按 32 位回绕就写 `uint32_t`（→ `u32`，UInt 子类真的按位宽回绕）。
CXX_SCALAR_TYPES = {
    "int": "i64", "signed": "i64", "signed int": "i64",
    "unsigned": "u64", "unsigned int": "u64",
    "short": "i64", "short int": "i64", "signed short": "i64",
    "signed short int": "i64",
    "unsigned short": "u64", "unsigned short int": "u64",
    "long": "i64", "long int": "i64", "long long": "i64", "long long int": "i64",
    "unsigned long": "u64", "unsigned long int": "u64",
    "unsigned long long": "u64", "unsigned long long int": "u64",
    "char": "u8", "signed char": "i8", "unsigned char": "u8",
    "float": "f32", "double": "f64", "long double": "f64",
    "bool": "bool",
}

#: 定长 / 标准库宽度名 → 本语言类型（**按位宽精确**）。
#: 定长名是 C++ 里「我就是要 32 位」的表达，放大到 64 位会改变**有定义**的回绕，
#: 所以这里不跟着方案甲走：`uint32_t` 就是 `u32`（`UInt` 子类按 32 位回绕）。
CXX_WIDTH_ALIASES = {
    "int8_t": "i8", "int16_t": "i16", "int32_t": "i32", "int64_t": "i64",
    "uint8_t": "u8", "uint16_t": "u16", "uint32_t": "u32", "uint64_t": "u64",
    "size_t": "u64", "ssize_t": "i64", "ptrdiff_t": "i64",
    "intptr_t": "i64", "uintptr_t": "u64",
}

#: **`std::` 下本语言认识的名字**（§8.3 的清单 + 宽度别名）。
#: 表外的 `std::X` 给一条「不认识」的诊断（不是静默当成用户类型名）。
STD_TYPE_NAMES = frozenset({
    "vector", "map", "set", "unordered_map", "unordered_set", "array",
    "optional", "expected", "string", "pair", "stack", "queue", "deque",
}) | frozenset(CXX_WIDTH_ALIASES)

#: **短名必须写在 `using namespace std;` 之后**（§8.3 的定稿）。
#:
#: **R1-A 里默认关闭**：`文档/样例/` 里 16/17 个 `.eln` 与 16/17 个 `.tu` 都写
#: 短名且**没有** `using`（更不用说 tests 与文档），一打开就全红。按用户定的
#: 「超过 5 个样例受影响就报告后等指示」这条纪律，先**接受短名**、把开关留在
#: 这里；样例迁移完把 True 打开（诊断文案已经写好，见 `_normalize_type_path`）。
REQUIRE_USING_FOR_SHORT_NAMES = False

#: `using namespace std;` 之后才允许短名的那批名字（**不含宽度别名**：
#: `size_t` / `uint32_t` 不是 std 容器，写短名不需要 `using`）。
STD_GATED_NAMES = frozenset({
    "vector", "map", "set", "unordered_map", "unordered_set", "array",
    "optional", "expected", "string", "pair", "stack", "queue", "deque",
})

#: **parser 判「左边的名字是不是类型名」用的词表**（指针消歧，2026-11-10②）。
#:
#: 以前 `u64 * p` 只能靠**紧贴**判：`u64* p` 是指针、`u64 * p` 是乘法。
#: 但 C++ 里 `u64 * p;` **就是声明**（而且格式化工具普遍这么排），
#: "改名即用"会在指针声明这一行断。现在按 C++ 自己的规则：看**左边的名字**
#: 是不是一个已知类型名——是就按声明解析（空格无所谓），不是才回到表达式。
#:
#: **为什么 parser 自带一份词表**：`parser` 不能 import `typecheck`（会成环），
#: 而这一步只需要"名字在不在"，不需要 `Ty`。表与
#: `typecheck.BUILTIN_TYPE_NAMES` 同口径（含 `typenames.CANON` 的旧名）；
#: 用户类型由 `parse_struct` / `parse_enum` / `parse_error_decl` 在**解析到
#: 它的那一刻**登记，所以前向引用看不见——与 C++ 一致。
BUILTIN_TYPE_NAMES = frozenset({
    # 标量
    "u8", "u16", "u32", "u64", "i8", "i16", "i32", "i64", "f32", "f64",
    "bool", "void",
    # 容器 / 结果（含旧名：Vec / FixedVec / Map / Set / Option）
    # `expected` 是 `Result` 的**唯一用户写法**（P4-0，2026-11-11）。
    "vector", "array", "fixedvec", "map", "set", "optional", "expected",
    "Result",
    "unordered_map", "unordered_set", "stack", "queue", "deque", "HashMap",
    "Vec", "FixedVec", "Map", "Set", "Option",
})


# ------------------------------------------------ 第 18/19 条（P0 兼容层）
#
# 这两条都是**纯拼写**：整数字面量后缀（`1ULL`）与复合赋值（`|=` `&=` …）。
# 判据只有一条——剥掉拼写之后必须是**同一个程序**（AST / κ / run 逐字节）。
# 只在 `--compat` 下生效；`compat=False` 时下面的函数与表一个分支都不进。

#: 第 19 条：复合赋值的 **token 序列**（恰好两个 token，且必须**相邻**）。
#:
#: 词法器没有这些复合 token，token 的落点是：
#:
#: | 源码 | token |
#: |---|---|
#: | `\|=` `&=` `^=` `%=` `<<=` | `<对应二元运算符>` + `=` |
#: | `>>=` | **`>` + `>=`** |
#:
#: `>>=` 不是 `>` `>` `=`：词法器在第二个 `>` 处按最长匹配先命中 `>=`
#: （`x >> 2` 才是两个相邻的 `>`，由 `parse_expr` 的 `SHR_BP` 分支合成右移）。
#: 这一格写错就会把 `x >>= 2` 认成别的东西，所以专门钉在测试里。
_COMPAT_COMPOUND_TOKENS = {
    (Kind.PIPE, Kind.EQ): "|",
    (Kind.AMP, Kind.EQ): "&",
    (Kind.CARET, Kind.EQ): "^",
    (Kind.PERCENT, Kind.EQ): "%",
    (Kind.SHL, Kind.EQ): "<<",
    (Kind.GT, Kind.GE): ">>",
}


def _compat_strip_literal_suffixes(tokens: List[Token]) -> List[Token]:
    """第 18 条：把 `INT` + **紧邻的**整数字面量后缀合成一个 `INT` token。

    `1ULL` 在词法层是 `INT "1"` + `IDENT "ULL"` 两个 token（词法器不认后缀，
    而它是**共享**的：`compat=False` 时一个字都不能变）。这里在 parser 入口
    统一归并一次，好处是下游（`lit-int` / `pattern-literal` / `_idents_in_file`）
    看到的 token 流与手写 `1` **完全一样**——不需要在每个消费点各加一次判断，
    也就不会出现"这里认、那里不认"的半成品。

    **值不变**：`value` 原样保留；`text` 只留数值部分（`1` / `0xFF`），因为
    `lit-float` 与 `pattern-literal` 下游会拿 `text` 再解析一次——把后缀留在
    `text` 里会让它们炸在 `int("1ULL")` 上。`end` 仍覆盖后缀（源码跨度完整）。

    **相邻**是硬判据：`1 ULL`（中间有空格）不合并，照旧报语法错误——后缀是
    **同一个字面量的词**，不是两个 token。
    """
    out: List[Token] = []
    i = 0
    n = len(tokens)
    while i < n:
        t = tokens[i]
        if (t.kind == Kind.INT and i + 1 < n
                and tokens[i + 1].kind == Kind.IDENT
                and t.end == tokens[i + 1].start
                and (tokens[i + 1].text or "").lower()
                in _compat.LITERAL_INT_SUFFIXES):
            out.append(Token(t.kind, t.text, t.line, t.col, t.start,
                             tokens[i + 1].end, t.value))
            i += 2
            continue
        out.append(t)
        i += 1
    return out


# ---------------------------------------------------------------- 解析器

class Parser:
    def __init__(self, tokens: List[Token], src_name: str = "<input>",
                 src: str = ""):
        # 丢掉 EOF 之前的一切照常处理，EOF 保留作哨兵
        self.toks = tokens
        self.i = 0
        self.src_name = src_name
        #: **源码原文**（2026-11-13）：只用于**诊断改写文本**——把用户的
        #: `(int)xs.size()` 原样抄进消息，给出可直接复制的 `xs.size() as i32`。
        #: 空串表示"没有原文"（合成 token 流）：那时消息退回通用模式，
        #: **不影响解析**（解析只看 token）。
        self.src = src
        self.diags: List[Diagnostic] = []
        self.soft: List[Diagnostic] = []
        #: 本文件是否写了模块级 `using namespace std;`（§8.3：写了才允许短名）。
        self.using_std = False
        #: 本文件里**已经看得见的类型名**（内置 + 前面声明过的 struct / enum /
        #: error）。语句起点判「`T * name` 是声明还是乘法」用它——见
        #: `_at_unknown_spaced_ptr_decl`。
        self.known_types = set(BUILTIN_TYPE_NAMES)
        #: 当前函数的泛型类型参数名（`func id<T>` 的 `T`）——也当类型名。
        self.type_params = set()
        #: 本文件里**已经看得见的值名**（形参 / 局部变量 / 常量 / 函数名）。
        #: `a * b` 的 `a` 在这里面 → 乘法；否则才可能是"未知类型 + 指针声明"。
        self.value_names = set()
        #: 解析类型时是否允许**有空格**的 `*`（`u64 * p`）。
        #:
        #: 类型位置（形参 / 返回类型 / 字段 / 已判定为声明的语句）没有歧义，
        #: 一律允许；只有**语句起点的试解**要关掉，让 `_at_type_prefixed_decl`
        #: 先按「已知类型名」判一次（`u64 * p` 仍要在那里被认成声明）。
        self._spaced_ptr_ok = True
        # ---- P0 兼容层（`--compat`）：**十条只看这一个布尔** ----------------
        #:
        #: 在 `Parser.__init__` 快照一次（而不是每次读 `compat.enabled()`）：
        #: 一个文件里的十条判据必须看**同一个值**——中途被别的线程改掉会让
        #: 一半按 C++ 拼写、一半按本语言解析。
        self.compat = _compat.enabled()
        # **第 18 条**：整数字面量后缀（`1ULL` → `1`）。归并在**语法判据之前**
        # 做一次，下游拿到的 token 流与手写无后缀字面量逐字节相同。
        if self.compat:
            self.toks = _compat_strip_literal_suffixes(self.toks)
        #: 第 2 条：`using X = Y;` / `typedef X Y;` 的**别名表**。
        #: **不进 AST**：`using` / `typedef` 不产出任何节点，别名在
        #: `parse_type` 里就已经展开成目标类型节点——所以"剥掉语法糖之后"
        #: 剩下的 AST 与手写目标类型**同形**。
        self.type_aliases: dict = {}
        #: 第 4 条：本文件里**已声明 struct 的字段名顺序**（`T{a, b}` 要靠它
        #: 变成 `T{x: a, y: b}`）。前向引用看不见——与 C++ 一致。
        self.struct_fields: dict = {}
        #: 第 5 条：正在解析**实参列表**的层数（只在里面才允许 `xs.begin()`）。
        #: `auto it = xs.begin();` 不在这层里 → 仍然报"本语言没有迭代器"。
        self._iter_arg_depth = 0
        #: 第 12 条：整份 token 流里出现过的**标识符**。
        #:
        #: 降级时新导入的下标变量要从这里**避开**：名字一旦进 AST 就全程可见，
        #: 直接排除整份文件的标识符，既不会撞上后面的声明，也不会遮蔽外层
        #: 同名变量（遮蔽会**静默改写**用户的程序）。
        self._idents_in_file = {t.text for t in self.toks
                                if t.kind == Kind.IDENT and t.text}
        #: 第 12 条：本次解析里已经用掉的降级下标名（多个循环互不重复）。
        self._compat_idx_used: set = set()
        #: 第 12 条：形状③（`it` 在外声明）降级后 `it` **不再被写**——
        #: 循环之后任何一次读到的都是循环前的旧值（静默错），所以要拒绝。
        #: 名字 → 那个循环的 token；在函数边界清空（见 `parse_fn`）。
        self._iter_escapes: dict = {}
        #: **第 13 条**（本轮）：本文件里 struct 的**成员**——成员函数 / 构造函数 /
        #: 字段类型。只认**同一个文件**里声明的 struct（与 `struct_fields` 同口径：
        #: `.tu` 里的 struct 由另一个 Parser 解析，`.eln` 这边看不见）。
        #:
        #: `名 -> {"fields": [字段名…], "types": {字段名: 类型节点},
        #:         "methods": {方法名: fn 节点}, "ctors": [fn 节点…]}`
        self.struct_decls: Dict[str, dict] = {}
        #: **第 13 条**：正在解析**成员函数体**时的接收者信息
        #: `(接收者形参名, 字段名集合, struct 名)`；不在成员体里时是 None。
        #: 非 None 时 `parse_primary` 把字段名改写成 `接收者.字段`（见 `_is_member_field`）。
        self._member_ctx: Optional[Tuple[str, frozenset, str]] = None
        #: **第 13 条**：变量名 → 类型节点。**只**服务"接收者是什么类型"这一个问题
        #: （`r.push(1)` 要知道 `r` 是 `Ring`）。作用域栈，`parse_block` 进出各一次。
        #: 推不出就返回 None —— 由 `E-Compat-MethodUnresolved` 报清楚，不猜。
        self._scopes: List[Dict[str, Optional[Node]]] = []

    # -- token 访问 --
    def peek(self, off: int = 0) -> Token:
        j = self.i + off
        if j >= len(self.toks):
            return self.toks[-1]
        return self.toks[j]

    def kind(self, off: int = 0) -> Kind:
        return self.peek(off).kind

    def at(self, k: Kind) -> bool:
        return self.kind() == k

    def advance(self) -> Token:
        t = self.peek()
        if t.kind != Kind.EOF:
            self.i += 1
        return t

    def accept(self, k: Kind) -> Optional[Token]:
        if self.at(k):
            return self.advance()
        return None

    def expect(self, k: Kind, what: str) -> Token:
        if self.at(k):
            return self.advance()
        t = self.peek()
        raise ParseError("E-Parse-Expected",
                         f"期望 {what}，实际是 {t.kind.value!r}", t)

    def expect_ident(self, what: str) -> Token:
        return self.expect(Kind.IDENT, what)

    # -- 诊断 --
    def error(self, code: str, msg: str, tok: Token):
        self.diags.append(Diagnostic(
            code=code, message=msg, line=tok.line, col=tok.col,
            start=tok.start, end=tok.end))

    def warn(self, code: str, msg: str, tok: Token):
        """**接受但要说一句**的发现（`--compat` 下用，`W-` 前缀）。

        与 `error` 分开而不是传参数：`compat=False` 时一条都不会被调用，
        所以默认路径的诊断流**逐字节不变**。
        """
        self.diags.append(Diagnostic(
            code=code, message=msg, line=tok.line, col=tok.col,
            start=tok.start, end=tok.end, severity="warning"))

    # ============================================================ P0 兼容层
    #
    # 这十条**都只看 `self.compat`**（唯一布尔在 `compat.py`，由 `dsh` 的
    # `--compat` 一行决定）。`compat=False` 时下面每个分支都走不进去，
    # 所以默认路径的 AST 与诊断**逐字节不变**。
    #
    # 纪律（"剥掉语法糖之后必须还是同一个程序"）：
    #   · 能落到**已有节点**的一律落已有节点（`struct-literal` / `list-init` /
    #     `call` / `type-name`），不新增语义；
    #   · 只能靠猜的一律**报错并教改法**（`auto` 推断不出、别名认不出、
    #     迭代器不配平、`std::` 表外名）。
    # ----------------------------------------------------------------------

    @staticmethod
    def _compat_head_of(ty: Optional[Node]) -> Optional[str]:
        """类型节点的**头名**（穿过 `T&` / `T*`；`std.vector` → `vector`）。"""
        n = ty
        while n is not None and getattr(n, "kind", "") in ("type-ref", "type-ptr"):
            n = n.children[0] if n.children else None
        if n is None or getattr(n, "kind", "") != "type-name":
            return None
        return (n.name or "").split(".")[-1]

    def _compat_register_alias(self, name_tok: Token, ty: Node) -> None:
        """第 2 条：登记 `using X = Y;` / `typedef Y X;` 的别名。

        **不进 AST**：这里只往 `self.type_aliases` 放一个已经解析好的类型节点，
        用别名的地方由 `parse_type` 当场替换——所以"糖剥掉"之后剩下的 AST
        与直接写目标类型**同形**（κ / run 因此逐字节相同）。

        **认不出的别名只 warning 不 error**（用户定）：`.eln` 里
        `using P = Point;` 的 `Point` 可能声明在 `.tu`，parser 看不见；
        硬报 error 会把合法程序判死。真错了由类型检查报 `E-Type-Unknown`。
        """
        name = (name_tok.text or "").strip()
        if not name:
            return
        if name in self.type_aliases:
            self.error("E-Name-Duplicate",
                       f"类型别名 `{name}` 在这个文件里重复定义（本语言禁止遮蔽）",
                       name_tok)
            return
        self.type_aliases[name] = ty
        # 别名本身也是一个**类型名**：`U64Vec * p` 要按声明解析，不是乘法。
        self.known_types.add(name)
        head = self._compat_head_of(ty)
        if head and not self._type_name_known(head) and head not in self.type_aliases:
            self.warn(
                "W-Compat-Alias",
                f"类型别名 `{name}` 指向 `{head}`，本语言在这个文件里不认识 `{head}`。\n"
                f"别名的语义就是**改名**：`{name}` 出现的地方按 `{head}` 解析，"
                f"所以类型检查仍会按 `{head}` 判（这里不猜、不造类型）。\n"
                f"改法：① 让 `{head}` 是本文件里声明过的 `struct` / `enum` 或内置类型；"
                f"② 把别名指向内置类型（`vector<u64>` / `u64` …）；"
                f"③ 如果 `{head}` 声明在 `.tu` 里，这条只是提示，`check` 会按两边一起看。",
                name_tok)

    # ---- 第 8 条：函数声明开头的 `static` --------------------------------

    def _compat_static_fn_ahead(self) -> bool:
        """当前位置是 `static` 且**后面紧跟 C++ 形状的函数声明**（第 8 条）。

        判据直接复用 `_at_cxx_fn_decl()`（跳过 `static` 后再试）：
        `static void f(…) {` / `static u64 g(…) {` 是；`static u64 counter = 0;`
        （静态局部变量）与 `static u64 f(…);`（原型）都不是——它们仍按
        `UNSUPPORTED_STMT_WORDS["static"]` 拒绝。
        """
        if not (self.compat and self.at(Kind.IDENT)
                and (self.peek().text or "") == "static"):
            return False
        save_i = self.i
        self.i += 1                              # 跳过 `static` 再试形状
        try:
            return self._at_cxx_fn_decl()
        finally:
            self.i = save_i

    def _compat_parse_alias_using(self) -> Optional[Node]:
        """`using X = Y;`（第 2 条）。**返回 None**：不进 AST。"""
        name_tok = self.expect_ident("别名")
        self.expect(Kind.EQ, "'='")
        ty = self.parse_type()
        self._reject_ptr_or_variadic()
        self.expect(Kind.SEMI, "';'")
        self._compat_register_alias(name_tok, ty)
        return None

    def parse_typedef(self) -> Optional[Node]:
        """`typedef …`（第 2 条，C 的老写法）。

        ## 只支持一种形状：`typedef struct … X;`（2026-11-13）

        三种拼写都认，产物**与手写 `struct X { … }` 同形**：

        | 写法 | 产物 |
        |---|---|
        | `typedef struct { … } X;` | `struct X { … }` |
        | `typedef struct X { … } X;` | `struct X { … }` |
        | `typedef struct Tag { … } X;` | `struct X { … }`（`Tag` 只是 C 的标签拼写） |

        为什么是这三条而不是更宽：它们**只是命名**——本语言已有 `struct` 声明，
        缺的只是"名字写在体后面"这层 C 的拼写。真实 C 库（cJSON / inih /
        tinyexpr）里 `typedef struct` 一旦挡路，后面成百上千行全都解析不了；
        把它降级成已有节点是零语义风险的。

        ## 其余 typedef：不做，但**明确诊断**

        - `--compat` 下 `typedef Y X;` 仍走别名表（既有行为，`using X = Y;` 同一套）；
        - 非 `--compat` 下给 `E-Keyword-Unsupported` + 改法（**不是**裸 parse 错）。
          函数指针 / 数组 / 多声明符 / 枚举别名一律落在这里或
          `_parse_typedef_struct` 的形状校验里，每条都说清"这一种没做"。
        """
        t = self.advance()                       # IDENT `typedef`
        if self.at(Kind.STRUCT):
            return self._parse_typedef_struct(t)
        if self.at(Kind.ENUM):
            self._typedef_skip_to_semi()
            raise ParseError(
                "E-Keyword-Unsupported",
                "`typedef enum { … } E;` **暂不支持**：本语言的 enum 声明即得名"
                "（写 `enum E { A, B }`，然后用 `E`），没有标签命名空间，"
                "这层 typedef 是多余的。\n"
                "改法：去掉 `typedef` 与结尾的名字，直接写 `enum E { A, B }`；"
                "要数值映射写函数 `u64 code(E e) { … }`。", t)
        if not self.compat:
            raise ParseError(
                "E-Keyword-Unsupported",
                "本语言没有类型别名（`typedef`）。\n"
                "改法：类型写全（`vector<u64>`）；泛型直接写在名字后："
                "`T pick<T>(T a, T b) { return a; }`。\n"
                "（**唯一例外**是 `typedef struct { … } X;` / "
                "`typedef struct Tag { … } X;`：它等价于 `struct X { … }`，"
                "所以接受；函数指针 / 基本类型别名 / 多声明符都还没做。）", t)
        ty = self.parse_type()
        self._reject_ptr_or_variadic()
        # **函数指针 / 数组**（`typedef u64 (*Fn)(u64);` / `typedef u64 Arr[4];`）：
        # `parse_type` 之后不是名字，而 `(` / `[`。这里给**一条**明确诊断
        # （不做），不让它落成「期望 别名，实际是 '('」的裸 parse 错。
        if self.at(Kind.LPAREN) or self.at(Kind.LBRACKET):
            bad = self.peek()
            self._typedef_skip_to_semi()
            raise ParseError(
                "E-Keyword-Unsupported",
                "只支持 `typedef 目标类型 名字;`（单个名字）。\n"
                "**函数指针 / 数组 / 多声明符暂不支持**："
                "`typedef u64 (*Fn)(u64);` / `typedef u64 Arr[4];` / `A, *PA;`。\n"
                "改法：① 函数指针 → 写成 `spec` 里的外部函数声明，或直接用具名函数；"
                "② 数组 → `array<T, N>` / `vector<T>`；③ 多名字 → 一次写一个。", bad)
        name_tok = self.expect_ident("别名")
        if not self.at(Kind.SEMI):
            bad = self.peek()
            self._typedef_skip_to_semi()
            raise ParseError(
                "E-Keyword-Unsupported",
                f"`typedef … {name_tok.text}` 后面只能跟 `;`："
                "**多声明符暂不支持**（`X, *PX;`）。\n"
                "改法：一次只写一个名字；指针 / 数组在用的地方写 `X*` / `array<X, N>`。",
                bad)
        self.expect(Kind.SEMI, "';'")
        self._compat_register_alias(name_tok, ty)
        return None

    def _typedef_skip_to_semi(self):
        """吃掉**当前 typedef 声明**到 `;`（含），只为错误恢复。

        **为什么必须先吃掉再抛**：`struct` / `enum` 是 `ITEM_START`，`recover_item`
        一看到它就停住、一个 token 都不消费，于是同一段声明会被**再解析一遍**，
        在同一个位置多报一条「期望 ';'」——一条错的诊断会变成两条。跳过到 `;`
        之后，恢复从下一条声明开始，诊断数与"读得懂的地方"一一对应。

        **按花括号配对跳**：`typedef struct { int x; } A, *PA;` 里的 `;` 是**字段**
        的分号，不是声明结束；只看 `;` 会把跳过停在结构体中间。
        """
        depth = 0
        while not self.at(Kind.EOF):
            k = self.kind()
            if k in (Kind.LBRACE, Kind.LPAREN, Kind.LBRACKET):
                depth += 1
            elif k in (Kind.RBRACE, Kind.RPAREN, Kind.RBRACKET):
                if depth > 0:
                    depth -= 1
            elif k == Kind.SEMI and depth == 0:
                self.advance()
                return
            self.advance()

    def _parse_typedef_struct(self, t: Token) -> Node:
        """`typedef struct [Tag] { … } X;` 的形状校验，然后交给 `parse_struct`。

        这里**只做形状校验**（名字在读体之前拿不到，而结构体名在解析体之前就要
        登记），真正的解析 / 降级与 `struct` 声明是**同一份代码**。结构体名取
        **typedef 名**（`Tag` 只消费、不登记，见 `parse_struct` 的 docstring）。

        - 必须有 struct 体：`typedef struct Tag X;`（只有标签、没有体）是 C 的
          另一种写法，本语言没有"只声明的 struct"，所以明确拒绝；
        - 声明符必须**恰好一个名字**：`X, *PX;` / `X[4];` / 函数指针都不做。

        允许的三种形状见 `parse_typedef` 的表。
        """
        # 形状：`struct` [Tag] `{ … }` `X` `;`
        j = self.i + 1
        if self.kind_at(j) == Kind.IDENT:
            j += 1
        if self.kind_at(j) != Kind.LBRACE:
            bad = self.toks[min(j, len(self.toks) - 1)]
            self._typedef_skip_to_semi()
            raise ParseError(
                "E-Keyword-Unsupported",
                "`typedef struct` 只支持**带定义**的形状："
                "`typedef struct { … } X;` / `typedef struct Tag { … } X;` / "
                "`typedef struct X { … } X;`。\n"
                "这一种没有 struct 体（`typedef struct X X;` / "
                "`typedef struct X* PX;`），**暂不支持**：本语言没有单独的"
                "「struct 前向声明」语法。\n"
                "改法：把 struct 体写出来（`typedef struct { … } X;` 或 "
                "`struct X { … }`）；指针类型在用的地方写 `X*`。", bad)
        depth = 0
        n = len(self.toks)
        while j < n:
            k = self.kind_at(j)
            if k == Kind.LBRACE:
                depth += 1
            elif k == Kind.RBRACE:
                depth -= 1
                if depth == 0:
                    j += 1
                    break
            elif k == Kind.EOF:
                break
            j += 1
        if self.kind_at(j) != Kind.IDENT:
            bad = self.toks[min(j, len(self.toks) - 1)]
            self._typedef_skip_to_semi()
            raise ParseError(
                "E-Keyword-Unsupported",
                "`typedef struct { … }` 后面要跟**恰好一个**类型名："
                "`typedef struct { … } X;`。\n"
                "多声明符 / 指针 / 数组形式（`X, *PX;` / `X[4];`）暂不支持："
                "一次只写一个名字，指针在用的地方写 `X*`。", bad)
        if self.kind_at(j + 1) != Kind.SEMI:
            bad = self.toks[min(j + 1, len(self.toks) - 1)]
            self._typedef_skip_to_semi()
            raise ParseError(
                "E-Keyword-Unsupported",
                "`typedef struct … X` 后面只能跟 `;`：多声明符（`X, *PX;`）"
                "暂不支持。\n改法：一次只写一个名字；指针在用的地方写 `X*`。", bad)
        return self.parse_struct(typedef_name=self.toks[j].text)

    # ---- 第 3 条：`auto x = expr;` -----------------------------------------

    def _compat_at_auto_decl(self) -> bool:
        """`auto x = expr;` / `const auto x = expr;` 的形状判据（宁窄勿宽）。

        **判据**：`auto` + 标识符 +（`=` | `;`）。`auto` 后面不是标识符的
        形状（`auto f() -> u64 {`）不是局部声明，留给函数声明那条路。
        """
        return (self.at(Kind.IDENT) and (self.peek().text or "") == "auto"
                and self.kind(1) == Kind.IDENT
                and self.kind(2) in (Kind.EQ, Kind.SEMI))

    def _compat_const_auto_ahead(self) -> bool:
        """`const auto x = …;` 的形状判据（`const` 已经由调用方看到）。"""
        return (self.compat
                and self.at(Kind.CONST)
                and self.kind(1) == Kind.IDENT
                and (self.peek(1).text or "") == "auto"
                and self.kind(2) == Kind.IDENT
                and self.kind(3) in (Kind.EQ, Kind.SEMI))

    def _compat_parse_auto_decl(self, is_const: bool) -> Node:
        """`auto x = expr;` / `const auto x = expr;`（第 3 条）→ **待推断**的声明。

        产出的 AST 与 `类型 x = expr;` 同形，只把类型节点换成 `type-auto`；
        `typecheck` 在拿到初值类型后**当场替换**成具体类型（见
        `typecheck.TypeChecker._compat_resolve_auto`）。推断不出就报
        `E-Compat-AutoNoType`——**不猜**。
        """
        if is_const:
            self.expect(Kind.CONST, "'const'")
        t = self.advance()                       # IDENT `auto`
        ty = Node("type-auto", t.line, t.col)
        stmt = Node("const-decl" if is_const else "var-decl", t.line, t.col)
        stmt.value = {"mut": not is_const}
        stmt.add(ty)
        stmt.name = self.expect_ident("变量名").text
        self.value_names.add(stmt.name)
        # 第 13 条：名字登记为"**已声明但类型待推断**"（`_OPAQUE_TY`）。
        # 这不是类型，只是告诉接收者推断"别把它当成没见过的名字"。
        self._scope_declare(stmt.name, _OPAQUE_TY)
        stmt.value["has_init"] = self.at(Kind.EQ)
        if stmt.value["has_init"]:
            self.expect(Kind.EQ, "'='")
            stmt.add(self._parse_init_expr())
            self._reject_positional_init()
        self.expect(Kind.SEMI, "';'")
        if not stmt.value["has_init"]:
            raise ParseError(
                "E-Compat-AutoNoType",
                f"`auto {stmt.name};` 推不出类型：`auto` 只有**从初值**才能推断。\n"
                f"改法：① 写全类型 `u64 {stmt.name} = …;`；"
                f"② 或者给它一个初值（`auto {stmt.name} = …;`）。\n"
                f"本语言不猜类型：契约与 κ 都依赖静态类型。", t)
        return stmt

    # ---- 第 11 条：`static_cast<T>(x)` --------------------------------------

    def _compat_parse_static_cast(self, t: Token) -> Node:
        """`static_cast<T>(x)`（第 11 条）：**只认两种精确形状**。

        | 形状 | 处理 |
        |---|---|
        | `static_cast<unsigned char>(x)` | 产物 `cast-static`（目标 `u8`） |
        | `static_cast<u64>(x)` | 产物 `cast-static`（目标 `u64`） |
        | 其它任何目标（`u8` / `i64` / `size_t` / …） | `E-Cast-Unsupported` |

        **这里只做"能解析出来"，不判能不能折叠**：目标类型与实参类型哪个更宽，
        解析器不知道（`x` 可能是 `for (auto c : s)` 的元素）。折叠/报错在
        `typecheck.resolve_static_cast` 里按**静态类型**判——只有当转换后值与
        转换前**逐位相同**时才折成 `x`；窄化、有符号、类型推不出，一律
        `E-Cast-Unsupported`（**宁可多报错，不许把真转换折成 no-op**）。

        为什么不在解析器里"看着像就折"：`static_cast<unsigned char>(c)` 在
        C++ 里是**真转换**（`c` 是 `char` 时负数会变值），只有 `c` 本来就是
        `u8` 时它才是纯拼写。解析器没有类型，猜不得。
        """
        self.advance()                                  # IDENT `static_cast`
        _hint = (
            "本语言只有 `x as T` 一种转换写法，且 `static_cast` 在兼容层里"
            "**只认两种目标**：\n"
            "· `static_cast<unsigned char>(x)`——仅当 `x` 本来就是 `u8` 时"
            "才等价于 `x`（C++ 里这是常见的 `is*` 前置转换）；\n"
            "· `static_cast<u64>(x)`——仅当 `x` 是无符号整数（`u8`/`u16`/"
            "`u32`/`u64`）时才等价于 `x`。\n"
            "窄化（如 `u64` → `u8`）、有符号 ↔ 无符号、类型推不出，一律拒绝："
            "那会**改值**，不是拼写。真要做转换请写 `x as T`。")
        if not self.at(Kind.LT):
            raise ParseError("E-Cast-Unsupported",
                             f"`static_cast` 必须写成 `static_cast<T>(x)`。\n{_hint}",
                             self.peek())
        self.advance()                                  # '<'
        _parts: List[str] = []
        _depth = 0
        while not self.at(Kind.EOF):
            _k = self.kind()
            if _k == Kind.LT:
                _depth += 1
            elif _k == Kind.GT:
                if _depth == 0:
                    break
                _depth -= 1
            elif _k in (Kind.LPAREN, Kind.LBRACKET, Kind.LBRACE):
                # 尖括号里不该出现这些——直接停在这里，按"不认形状"报。
                break
            _parts.append(self.advance().text)
        _inner = " ".join(p for p in _parts if p).strip()
        if not self.at(Kind.GT):
            raise ParseError("E-Cast-Unsupported",
                             f"`static_cast<…>` 的尖括号没配平。\n{_hint}", t)
        self.advance()                                  # '>'
        if _inner == "unsigned char":
            target = "u8"
        elif _inner == "u64":
            target = "u64"
        else:
            raise ParseError(
                "E-Cast-Unsupported",
                f"`static_cast<{_inner}>(x)` 不在兼容层支持的两种形状里。\n{_hint}",
                t)
        if not self.at(Kind.LPAREN):
            raise ParseError(
                "E-Cast-Unsupported",
                f"`static_cast<{_inner}>` 后面必须紧跟 `(`："
                f"写成 `static_cast<{_inner}>(x)`。\n{_hint}", self.peek())
        self.advance()                                  # '('
        _operand = self.parse_expr()
        self.expect(Kind.RPAREN, "')'")
        return Node("cast-static", t.line, t.col, name=target).add(_operand)

    # ---- 第 4 条：聚合初始化 ------------------------------------------------

    def _compat_struct_fields_of(self, ty: Optional[Node]) -> Optional[List[str]]:
        """类型节点若指向**本文件已声明的 struct**，返回字段名（按声明顺序）。"""
        head = self._compat_head_of(ty)
        if not head:
            return None
        return self.struct_fields.get(head)

    def _compat_brace_initializer(self, ty: Node, tok: Token) -> Node:
        """声明位置的 `T name{…}`（第 4 条）→ 与 `= …` **同形**的初值节点。

        - `vector<u64> v{1,2,3}` → `list-init`（与 `v = {1,2,3}` 逐字节同形）；
        - `Point p{3,4}` → `struct-literal`（与 `p = Point { x: 3, y: 4 }` 同形）。
          位置式要变成字段名，靠的是 parser 在第 1 条里记下的 `struct_fields`。
        """
        exprs = self._compat_brace_items()
        fields = self._compat_struct_fields_of(ty)
        head = self._compat_head_of(ty) or "?"
        if fields is not None:
            return self._compat_struct_literal_from(exprs, fields, head, tok)
        # 容器：与 `= {…}` 完全同形（`_parse_init_expr` 的那条路）
        if head in ("vector", "array", "fixedvec", "Vec", "FixedVec"):
            node = Node("list-init", tok.line, tok.col)
            for e in exprs:
                node.add(e)
            return node
        raise ParseError(
            "E-Compat-AggregateArity",
            f"`{head} {{{{…}}}}` 这种聚合初始化在本语言里只对**已知形状**成立：\n"
            f"· 容器 → `vector<u64> v{{1, 2, 3}};`（= `v = {{1, 2, 3}}`）；\n"
            f"· 结构体 → 需要**先在本文件里**看到 `struct {head} {{ … }}` 的字段顺序。\n"
            f"改法：① 把 `struct {head} {{ … }}` 写在用它之前；"
            f"② 或直接写字段名 `{head} {{ x: 1, y: 2 }}`；"
            f"③ 或写成 `{head} p = {head} {{ … }}`。",
            tok)

    def _compat_brace_items(self) -> List[Node]:
        """吃掉 `{ e, e, … }`（调用方已确认当前是 `{`），返回表达式列表。"""
        self.advance()                           # '{'
        out: List[Node] = []
        if not self.at(Kind.RBRACE):
            out.append(self.parse_expr())
            while self.accept(Kind.COMMA):
                if self.at(Kind.RBRACE):        # 允许尾逗号
                    break
                out.append(self.parse_expr())
        self.expect(Kind.RBRACE, "'}'")
        return out

    def _compat_struct_literal_from(self, exprs: List[Node], fields: List[str],
                                    name: str, tok: Token) -> Node:
        """位置式实参 → `struct-literal`（字段名按 struct 声明顺序补上）。"""
        if len(exprs) != len(fields):
            raise ParseError(
                "E-Compat-AggregateArity",
                f"`{name}{{{len(exprs)} 个值}}` 与 `struct {name}` 的 "
                f"{len(fields)} 个字段对不上。\n"
                f"字段顺序是：{', '.join(fields)}。\n"
                f"改法：按声明顺序给满 {len(fields)} 个值，"
                f"或直接写字段名 `{name} {{ {fields[0]}: …, … }}`。",
                tok)
        node = Node("struct-literal", tok.line, tok.col, name=name)
        for fname, e in zip(fields, exprs):
            node.add(Node("field-init", e.line, e.col, name=fname).add(e))
        return node

    # ---- 第 5 条：`f(xs.begin(), xs.end())` → `f(xs)` ----------------------

    @staticmethod
    def _compat_same_receiver(a: Node, b: Node) -> bool:
        """两个 `iter-arg` 的接收者是不是**同一个路径**（`xs` vs `xs`）。

        只认 `path`（`xs` / `m.at(i)` 这类**不确定**形状一律视为不配平）：
        收一个表达式再去猜"它是不是同一个容器"就是在猜。
        """
        ra = a.children[0] if a.children else None
        rb = b.children[0] if b.children else None
        return (ra is not None and rb is not None
                and getattr(ra, "kind", "") == "path"
                and getattr(rb, "kind", "") == "path"
                and ra.name == rb.name)

    def _compat_fold_iter_args(self, args: Node) -> Node:
        """第 5 条：**配平的** `xs.begin(), xs.end()` 折成 `xs`。

        只在实参列表里，且必须**紧邻成对**、接收者是同一个路径；其余
        （只有 `begin`、只有 `end`、两个不同容器、不在实参里）仍旧报
        `E-Call-UnknownMethod`「本语言没有迭代器」——一个字都不改。
        """
        kids = args.children
        if not any(getattr(k, "kind", "") == "iter-arg" for k in kids):
            return args
        out: List[Node] = []
        i = 0
        while i < len(kids):
            k = kids[i]
            if getattr(k, "kind", "") != "iter-arg":
                out.append(k)
                i += 1
                continue
            nxt = kids[i + 1] if i + 1 < len(kids) else None
            if (k.name == "begin" and nxt is not None
                    and getattr(nxt, "kind", "") == "iter-arg"
                    and nxt.name == "end"
                    and self._compat_same_receiver(k, nxt)):
                out.append(k.children[0])        # 接收者本身：`f(xs)`
                i += 2
                continue
            bad = Token(Kind.IDENT, k.name or "begin", k.line, k.col, 0, 0)
            raise ParseError(
                "E-Call-UnknownMethod",
                ITERATOR_METHODS_MSG.format(name=k.name or "begin")
                + "\n（`--compat` 只多认一种写法：实参里**紧邻成对**的 "
                  "`f(xs.begin(), xs.end())`，等价于 `f(xs)`；"
                  "其余迭代器用法仍然不存在。）", bad)
        args.children = out
        return args

    # ---- 第 6 条 B 档：`std::move(e)` → `e`（**删除式**降级） ---------------

    def _compat_std_delete(self, name: str, t: Token) -> Node:
        """B 档（删除式）：`std::move(e)` **整个删掉**，直接落成 `e` 的 AST。

        **为什么可以删**：本语言**没有移动语义**——赋值 / 传参 / 返回 / 入容器
        全是真深拷贝（§3.12）。C++ 的 `std::move(x)` 本身不搬任何东西，它只把
        `x` 转成右值，真正"搬"发生在接收端的移动构造里；本语言的接收端**永远
        拷贝**，所以这里没有可以省掉的拷贝，`std::move(x)` 与 `x` **值相同**。

        于是这是**纯删除**：parser 不产生任何包装节点（不造 `call`、不造
        `copy`），`cost --signature` / `run` / AST 与手写 `x` **逐字节相同**
        （`tests/cases/test_compat.py` 的 `[35]` 钉的就是这条）。

        **方向也是安全的**（κ 只多不少）：C++ 接收端拿的是移动（O(1)），
        这里落成深拷贝（O(n)）——κ **更保守**，不会低估。

        只认 `STD_DELETE_ALIASES[name]` 给出的**恰好一个**实参：
        `std::move` / `std::move()` / `std::move(a, b)` 没有可降级的东西，
        报 `E-Compat-StdDeleteArity`（不猜、不静默丢掉多出来的实参）。

        `compat=False` 时调用点根本走不进来（判据在 `parse_postfix` 的
        `self.compat` 分支里），默认路径一个字节都不动。
        """
        arity = _compat.STD_DELETE_ALIASES[name]
        if not self.at(Kind.LPAREN):
            raise ParseError(
                "E-Compat-StdDeleteArity",
                f"`std::{name}` 在兼容层里是**删除式**降级（本语言没有移动语义），"
                f"必须写成 `std::{name}(x)`。\n"
                f"改法：把 `std::{name}(…)` 整个删掉，直接写括号里的表达式——"
                f"赋值 / 传参本来就是深拷贝，值不变。",
                t)
        self.advance()                                  # '('
        if self.at(Kind.RPAREN):
            raise ParseError(
                "E-Compat-StdDeleteArity",
                f"`std::{name}(…)` 需要**恰好 {arity} 个实参**，这里给了 0 个。\n"
                f"改法：`std::{name}` 是删除式降级——把 `std::{name}(x)` 整个删掉，"
                f"直接写 `x`（本语言没有移动语义，`x` 本来就是拷贝）。",
                self.peek())
        inner = self.parse_expr()
        if self.at(Kind.COMMA):
            raise ParseError(
                "E-Compat-StdDeleteArity",
                f"`std::{name}(…)` 需要**恰好 {arity} 个实参**，这里给了多个。\n"
                f"改法：`std::{name}` 是删除式降级，整个删掉、直接写那个表达式；"
                f"多出来的实参没有可降级的落点（**不会静默丢掉**）。",
                self.peek())
        self.expect(Kind.RPAREN, "')'")
        return inner

    # ---- 第 17 条：`std::printf("…%d…", …)` → 花括号格式串 ---------------

    @staticmethod
    def _compat_call_node(name: str, args: Node, line: int, col: int) -> Node:
        """手写 `name(args…)` 的**同形** `call` 节点（callee 是 `path` + 一个 `args`）。"""
        call = Node("call", line, col, name=name)
        call.add(Node("path", line, col, name=name))
        call.add(args)
        return call

    def _compat_printf(self, t: Token) -> Node:
        """第 17 条：`std::printf(fmt, …)` → `print("…{}…", …)` / `write(format(…))`。

        **为什么在解析期改**（不是运行期解析 `%`）：格式串在 C++ 里几乎总是
        **字面量**；在解析期就把 `%d` 换成 `{:d}`，落到的就是手写
        `print("…{}…", …)` 的**同一棵 AST**——κ / run 与手写逐字节相同，
        洞数与实参数在编译期就能对上，而且**不新增任何运行期机制、不改
        `print` 的语义**（`compat=False` 时这个函数根本走不到）。

        判据/诊断全在 `compat.printf_plan()`（**只有一份**，本函数只做接线）：

        | 形状 | 落到 |
        |---|---|
        | 以 `\\n` 结尾 | `print(<去掉末尾 \\n 的格式串>, …)`（行尾由 `print` 补） |
        | 完全不换行 | `write(format(<格式串>, …))`；没有洞时 `write(<格式串>)` |
        | 中间有 `\\n` 但不以它结尾 | `write(…)`，`\\n` 在解析期换成 `os.linesep`（与 `print` 运行期做的是同一件事） |
        | 宽度 / 精度 / 标志 / 不认识的说明符 | `E-Compat-PrintfFormat`（不猜） |

        `%c` 的实参包一层 `utf8_encode`（码点 → UTF-8 字节；ASCII 与 C 逐字节
        相同，128..255 的偏差写在 `语法清单.md` §P0 第 17 条）。
        """
        self.advance()                                  # '('
        # 诊断位置指向**格式串的开头**（比指向 `std` 更好排错）。`ParseError`
        # 收的是 `Token`，所以这里先记下 token——不能拿 AST 节点当位置。
        fmt_tok = self.peek()
        args = self.parse_args_until(Kind.RPAREN)
        self.expect(Kind.RPAREN, "')'")
        items = list(args.children)
        if not items:
            raise ParseError(
                "E-Compat-PrintfFormat",
                "`std::printf` 至少要一个**格式串**实参（`std::printf()` 没有可写的"
                "东西）。\n改法：写成 `std::printf(\"…\\n\")`，或直接删掉这一句。",
                fmt_tok)
        fmt = items[0]
        if getattr(fmt, "kind", "") != "lit-string":
            raise ParseError(
                "E-Compat-PrintfFormat",
                "`std::printf` 的**格式串必须是字符串字面量**：`--compat` 在解析期把 "
                "`%d` 改写成 `{}`，运行期拼出来的 `%` 串改不了（也就没法保证格式对）。\n"
                "改法：把格式串写成字面量；要在运行期拼就用 `format(\"…{}…\", …)` + "
                "`write(…)`（那是本语言的正路，不是 `printf`）。",
                fmt_tok)
        raw = fmt.value
        # **非 UTF-8 的字节也要原样留着**：用 `surrogateescape` 解开、再原样编回，
        # 这样格式串里恰好出现裸字节时不会被 U+FFFD 换掉（格式串本身是字节串）。
        text = (raw.decode("utf-8", "surrogateescape") if isinstance(raw, bytes)
                else str(raw))
        try:
            plan = _compat.printf_plan(text)
        except _compat.PrintfFormatError as exc:
            raise ParseError(_compat.PRINTF_FORMAT_CODE, exc.msg, fmt_tok) from None
        holes = len(items) - 1
        if holes != plan.holes:
            raise ParseError(
                _compat.PRINTF_FORMAT_CODE,
                f"`std::printf` 的格式串有 **{plan.holes}** 个转换说明，但给了 "
                f"**{holes}** 个实参。C++ 里多给的实参会被求值后**丢掉**、少给则是"
                "未定义行为（读栈上的垃圾）——兼容层不猜。\n"
                "改法：实参个数必须与格式串里的转换说明**一一对应**"
                "（`%%` 只打一个 `%`，不算转换说明）。",
                fmt_tok)
        lit = Node("lit-string", fmt.line, fmt.col,
                   value=plan.text.encode("utf-8", "surrogateescape"))
        wrapped = Node("args")
        wrapped.add(lit)
        for k, a in enumerate(items[1:]):
            if plan.wrap_byte[k]:
                # `%c`：先把整数当码点编成 UTF-8 字节，再用 `{}` 当文本打。
                one = Node("args")
                one.add(a)
                a = self._compat_call_node(_compat.PRINTF_BYTE_WRAP, one,
                                           a.line, a.col)
            wrapped.add(a)
        if plan.ends_nl:
            # 以换行结尾：行尾交给 `print`（它按平台转换行尾，与 C 的文本流一致）。
            return self._compat_call_node("print", wrapped, t.line, t.col)
        # 不换行：`write` 写原始字节。没有洞时不必经过 `format`（少一次调用）。
        if plan.holes == 0:
            return self._compat_call_node("write", wrapped, t.line, t.col)
        return self._compat_call_node(
            "write",
            Node("args").add(self._compat_call_node("format", wrapped,
                                                    t.line, t.col)),
            t.line, t.col)

    # ---- 第 7 条：返回类型 `void` → `()` -----------------------------------

    def _compat_ret_type(self) -> Node:
        """返回类型位置读一个类型；`void` 归一成 `()`（空 `type-tuple`）。

        **只改拼写**：`void f()` 与 `()`  f()` 产出**同一个** 返回类型节点，
        所以两种写法的 AST / κ / run 输出必然相同。`compat=False` 时
        `void` 保持今天的 `type-name void`——默认路径一个字节都不动。
        """
        ty = self.parse_type()
        if (self.compat and getattr(ty, "kind", "") == "type-name"
                and (ty.name or "") == "void" and not ty.children):
            return Node("type-tuple", ty.line, ty.col)
        return ty

    # ---- 第 9 条：多声明符（comma-separated declarators） ------------------

    def _compat_at_multi_decl(self) -> bool:
        """形状判据：`[const] T name (= … | , | ;) , … ;`（第 9 条）。

        只看 **token 深度**，不试解整条语句：本语言没有逗号运算符，所以语句
        层的 `,` 只有一种合法读法——"下一个声明符"。圆 / 方 / 花括号之内的
        逗号不算（`f(a, b)` / `{1, 2}`）；遇到 `;` 或深一层收尾就放弃。

        类型部分用 `_probe_type_end`（与真正的解析同一份代码），并且要求
        `const` 之下的类型**不是指针 / 引用**——`const Json* p` 走的是
        `parse_typed_decl` 那条（const 修饰"指向"，不是绑定），本轮不把两条
        路径混在一起。
        """
        if not self.compat:
            return False
        if self.at(Kind.CONST):
            save_i, save_d, save_s = self.i, len(self.diags), len(self.soft)
            try:
                ty = self.parse_type()
            except ParseError:
                return False
            finally:
                self.i = save_i
                del self.diags[save_d:]
                del self.soft[save_s:]
            if getattr(ty, "kind", "") in ("type-ptr", "type-ref"):
                return False
        end = self._probe_type_end(spaced_ptr_ok=False)
        if end is None or self.kind_at(end) != Kind.IDENT:
            return False
        if self.kind_at(end + 1) not in (
                Kind.EQ, Kind.COMMA, Kind.SEMI, Kind.LPAREN, Kind.LBRACE):
            return False
        depth = 0
        j = end + 1
        while j < len(self.toks):
            k = self.kind_at(j)
            if k in (Kind.LPAREN, Kind.LBRACKET, Kind.LBRACE):
                depth += 1
            elif k in (Kind.RPAREN, Kind.RBRACKET, Kind.RBRACE):
                if depth == 0:
                    return False
                depth -= 1
            elif depth == 0 and k == Kind.SEMI:
                return False
            elif depth == 0 and k == Kind.COMMA:
                return True
            j += 1
        return False

    def _compat_parse_multi_decl(self) -> List[Node]:
        """`T a = x, b = y, c;` → **多个与手写声明同形的兄弟节点**（第 9 条）。

        每个节点都是 `var-decl` / `const-decl` + 同一个类型 + 自己的名字 /
        初值——与手写 `T a = x;`、`T b = y;`、`T c;` 的 AST 形状完全相同
        （只有源码位置不同：源码里它们确实在同一行）。类型节点**深拷贝**，
        因为 AST 是树，下游有按 `id()` 建索引的地方。

        判据由 `_compat_at_multi_decl()` 在调用前确认；`compat=False` 时本函数
        永远不会被调用（默认路径一个字节都不变）。
        """
        is_const = self.at(Kind.CONST)
        if is_const:
            self.advance()                       # `const` 只吃一次
        ty_node = self.parse_type()
        self._reject_ptr_or_variadic()
        # **与单条声明同一条判据**（`parse_typed_decl`）：`* const` 才是
        # "绑定不可变"；`const T*` 是指向 const，绑定本身可改。
        _is_const_ptr = (ty_node.kind == "type-ptr"
                         and isinstance(ty_node.value, dict)
                         and bool(ty_node.value.get("ptr_const")))
        out: List[Node] = []
        while True:
            name_tok = self.peek()
            _is_const = _is_const_ptr or (is_const and ty_node.kind != "type-ptr")
            stmt = Node("const-decl" if _is_const else "var-decl",
                        name_tok.line, name_tok.col)
            stmt.value = {"mut": not _is_const}
            stmt.add(copy.deepcopy(ty_node))
            stmt.name = self.expect_ident("变量名").text
            self.value_names.add(stmt.name)
            # 第 13 条：登记类型（只服务"接收者是什么类型"）。
            self._scope_declare(stmt.name, stmt.children[0])
            self._parse_decl_initializer(stmt, stmt.children[0])
            out.append(stmt)
            if not self.accept(Kind.COMMA):
                break
        self.expect(Kind.SEMI, "';'")
        return out

    # ---- 第 10 条：直接构造 / 函数式初始化 `T x(args…)` -------------------

    #: 单实参直构可以退化成**拷贝初始化**（`T x = arg;`）的类型头。
    #: 只收标量：容器的单实参是"尺寸 / 填充"语义，不能这么退。
    _COMPAT_CTOR_SCALAR_HEADS = frozenset({
        "u8", "u16", "u32", "u64", "i8", "i16", "i32", "i64",
        "f32", "f64", "bool",
    })

    #: `Text`（文本）的单实参也是拷贝初始化：`Text s("abc")` → `Text s = "abc"`。
    _COMPAT_CTOR_TEXT_HEADS = frozenset({"Text"})

    def _compat_ctor_call_ahead(self, j: int) -> bool:
        """`(` 起配对，`)` 之后必须是 `;` 或 `,`（第 10 条的形状判据）。

        只认"语句在这里结束 / 后面还有下一个声明符"的形状；`)` 后面是运算符
        或 `{` 的一律不当作直接构造（交给原来的表达式路径，诊断不变）。
        """
        depth = 0
        while j < len(self.toks):
            k = self.kind_at(j)
            if k in (Kind.LPAREN, Kind.LBRACKET, Kind.LBRACE):
                depth += 1
            elif k in (Kind.RPAREN, Kind.RBRACKET, Kind.RBRACE):
                depth -= 1
                if depth == 0:
                    return self.kind_at(j + 1) in (Kind.SEMI, Kind.COMMA)
                if depth < 0:
                    return False
            j += 1
        return False

    def _compat_ctor_unsupported(self, ty: Optional[Node], name: Optional[str],
                                 nargs: int, tok: Token):
        """`T x(args…)` 没有等价物时的**唯一出口**：报错并教改法（不猜语义）。"""
        head = self._compat_head_of(ty) or "?"
        what = f"`{head} {name}({nargs} 个实参)`" if name \
            else f"`{head}({nargs} 个实参)`"
        extra = ""
        if head == "vector" and nargs == 1:
            extra = (
                "\n注意：`vector<T>(n)` 在 C++ 里是「n 个默认值」；本语言没有"
                "默认值表达式，所以不能这样退。请写 "
                f"`vector<T> {name or 'xs'} = vector<T>::filled(v, n);` 并"
                "显式给出 v。")
        # 结构体在这里有两个完全不同的原因，消息必须分清（否则读者会被
        # "本语言没有构造函数"这句误导——`--compat` 下**声明语句**里的构造
        # 是支持的，见 `_compat_parse_struct_ctor_decl`）。
        ctor_note = ""
        if head in self.struct_decls:
            if (self.struct_decls.get(head) or {}).get("ctors"):
                ctor_note = (
                    f"\n注意：`{head}` 在本文件里**有构造函数**——但 `--compat` 只"
                    f"降级**声明语句**里的构造（`{head} r(3);` → "
                    f"`{head} r; {head}_{head}(r, 3);`）。表达式位置的临时对象"
                    f"（`{head}(3)`）本语言没有等价物。改法：先写 "
                    f"`{head} {name or 'tmp'}(3);` 再用 `{name or 'tmp'}`。")
            else:
                ctor_note = (
                    f"\n注意：本文件里**没找到** `struct {head}` 的构造函数"
                    f"（`{head}(…) : … {{ }}`）。`--compat` 的构造函数降级要求"
                    f"`struct` 与它写在同一文件里。")
        raise ParseError(
            "E-Compat-CtorUnsupported",
            f"{what} 是 C++ 的**直接构造**，但本语言没有构造函数，这个形状"
            f"没有等价物。\n"
            f"`--compat` 只认两种能**落到已有语义**的直构：\n"
            f"  · 单实参拷贝初始化：`u64 x(5);` → `u64 x = 5;`，"
            f"`std::string s(\"abc\");` → `Text s = \"abc\";`\n"
            f"  · `vector<T> xs(n, v);` → "
            f"`vector<T> xs = vector<T>::filled(v, n);`"
            f"（n 个 v，与 C++ 的 `std::vector<T>(n, v)` 同义）{extra}{ctor_note}\n"
            f"改法：① **先声明再赋值**：`{head} {name or 'x'};` 之后 "
            f"`{name or 'x'} = …;`；\n"
            f"      ② 结构体写聚合初始化：`{head} {name or 'x'} = "
            f"{head} {{ x: … }};`（或位置式 `{head} {name or 'x'}{{…}}`）；\n"
            f"      ③ 如果这是 C++ 构造函数（例如 `Ring r(3);`），把它写进 "
            f"`struct` 体（`Ring(std::size_t cap) : … {{}}`）——`--compat` 会把"
            f"声明语句里的构造降级成 `Ring r; Ring_Ring(r, 3);`。\n"
            f"（不会静默放过：直构要么落到上面两种同义写法，要么报这条。）",
            tok)

    def _compat_ctor_expr(self, ty: Optional[Node], args: Node, tok: Token,
                          name: Optional[str] = None) -> Node:
        """`T x(args…)` / 表达式 `T(args…)` → **已有的等价表达式**（第 10 条）。

        | 形状 | 落到 | 依据 |
        |---|---|---|
        | `array<T, N>(v)` | 同一个 `call`（本语言已有，P4-0） | 本来就有，原样保留 |
        | `vector<T>(n, v)` | `vector<T>::filled(v, n)` | interp 注释里的同一语义 |
        | 标量 / `Text` 单实参 | 实参本身（= 拷贝初始化） | C++ 直接初始化 = 拷贝初始化 |
        | 其余（结构体 / 多实参 / `vector<T>(n)` …） | `E-Compat-CtorUnsupported` | 没有等价物，**不猜** |

        产出的节点与手写对应写法**同形**；所以 κ / run / AST 与手写一致。
        """
        head = self._compat_head_of(ty)
        n = len(args.children)
        if (n == 0 or ty is None or getattr(ty, "kind", "") != "type-name"
                or head is None):
            self._compat_ctor_unsupported(ty, name, n, tok)
        if any(getattr(c, "kind", "") == "named-arg" for c in args.children):
            self._compat_ctor_unsupported(ty, name, n, tok)
        if head == "array":
            if n == 1:                            # `array<T, N>(v)`：本语言已有
                call = Node("call", tok.line, tok.col)
                call.add(copy.deepcopy(ty))
                call.add(args)
                return call
            self._compat_ctor_unsupported(ty, name, n, tok)
        if head == "vector":
            elem = self._compat_type_arg_head(ty, 0)
            if n == 2:                            # `vector<T>(n, v)` → filled(v, n)
                inner = Node("args", tok.line, tok.col).add(
                    args.children[1], args.children[0])
                callee = Node("field", tok.line, tok.col,
                              name="filled").add(copy.deepcopy(ty))
                return Node("call", tok.line, tok.col).add(callee, inner)
            if n == 1 and elem == "u8":           # `vector<u8>(s)` = 文本初始化
                return args.children[0]
            self._compat_ctor_unsupported(ty, name, n, tok)
        if head in self._COMPAT_CTOR_SCALAR_HEADS \
                or head in self._COMPAT_CTOR_TEXT_HEADS:
            if n == 1:                            # 拷贝初始化：`T x = arg;`
                return args.children[0]
            self._compat_ctor_unsupported(ty, name, n, tok)
        # 结构体 / 其它容器 / 未知用户类型：本语言没有构造函数 → 明确拒绝。
        self._compat_ctor_unsupported(ty, name, n, tok)

    def _compat_type_arg_head(self, ty: Optional[Node], idx: int) -> Optional[str]:
        """`ty` 的第 `idx` 个类型实参的头名（`vector<T>` 的 `T`）。"""
        if ty is None:
            return None
        targs = next((c for c in ty.children if getattr(c, "kind", "") == "type-args"),
                     None)
        if targs is None or idx >= len(targs.children):
            return None
        return self._compat_head_of(targs.children[idx])

    # ================= 第 13 条：struct 的成员函数 / 构造函数 =================
    #
    # **只做 desugar，不新增语义**：成员函数与构造函数最终都落成**自由函数 +
    # 调用**，与手写同形——所以 κ 与 run 输出必然相同，也不需要给解释器
    # 加"方法表"这种环境相关的东西。
    #
    #   | C++ 写法 | 降级成（一字不多、一字不少） |
    #   |---|---|
    #   | `struct Ring { … };` | 同一个 `struct Ring`（字段照旧） |
    #   | `void push(int v) { … }` | `() Ring_push(Ring& recv, i64 v) { recv.… }` |
    #   | `Ring(std::size_t cap) : buf(cap, 0), head(0) {}` | `() Ring_Ring(Ring& recv, u64 cap) { recv.buf = vector<i64>::filled(0, cap); recv.head = 0; }` |
    #   | `r.push(1)` | `Ring_push(r, 1)` |
    #   | `Ring r(3);` | `Ring r; Ring_Ring(r, 3);` |
    #   | `Ring r;`（该 struct 有 0 参构造） | `Ring r; Ring_Ring(r);` |
    #   | 体里裸写字段 `buf` | `recv.buf` |
    #   | 体里调兄弟成员 `push(1)` | `Ring_push(recv, 1)` |
    #
    # **接收者形参名**优先 `recv`；文件里已有同名标识符时依次退到 `recv2` /
    # `recv3` …（与第 12 条的 `idx` 同一套避让规则），保证**绝不遮蔽**用户的名字。
    # 接收者是 `T&`（本语言的引用对 struct 成立，字段写回调用方——已由
    # `typecheck._ref_target_ok` 保证）。
    #
    # **不支持的形状一律报清楚**（每条给改法，见下面各条消息）：继承、
    # 虚函数、`this`、运算符重载、静态成员、嵌套 struct、模板成员函数、
    # 成员重载、只有声明没有体、`= default` / `= delete`、析构函数。

    #: 第 13 条：降级后接收者形参的**首选名**（被占用时依次 `recv2` / `recv3` …）。
    MEMBER_RECV_NAME = "recv"

    #: 第 13 条：struct 体里**明确不支持**的成员形状。词 → (这是什么, 改法)。
    #:
    #: 判据是**词 + 位置**（只在 struct 体里、成员起点的位置），不是全文白名单：
    #: `static` 在**函数声明前缀**是支持的（第 8 条），在 struct 体里是静态成员，
    #: 两者不是一件事。
    MEMBER_UNSUPPORTED_WORDS = {
        "virtual": ("虚函数（`virtual`）",
                    "本语言没有继承也没有虚函数：用带载荷的 `enum` + `match` "
                    "表达多态，例如 `enum Shape {{ Circle(u64), Square(u64), }}`，"
                    "再写一个自由函数 `u64 area(Shape s) {{ … }}`"),
        "static": ("静态成员（`static`）",
                   "本语言没有静态成员 / 静态存储：常量放 `.tu` 顶层 "
                   "（`const N: u64 = 10;`），函数写成同文件里的自由函数"
                   "（`u64 Ring_count(Ring& r)`），计数器当字段或形参传"),
        "explicit": ("`explicit` 构造函数",
                     "本语言没有隐式转换，也就没有 `explicit` 可关的东西："
                     "删掉 `explicit` 即可（构造函数降级成自由函数后本来就只能显式调用）"),
        "friend": ("友元（`friend`）",
                   "本语言没有访问控制，也就没有友元：把那个函数写成普通自由函数，"
                   "把对象作为形参传进去（`u64 area(Ring& r)`）"),
        "template": ("模板成员函数（`template<…>`）",
                     "本语言没有模板成员：泛型只支持自由函数"
                     "（`T pick<T>(T a, T b) {{ return a; }}`），"
                     "成员函数里的泛型参数改成具体类型"),
        "operator": ("运算符重载（`operator`）",
                     "本语言没有运算符重载：写成**具名**自由函数"
                     "（`Ring add(Ring& a, Ring& b)` / `bool ring_eq(Ring& a, Ring& b)`），"
                     "调用处直接写函数名"),
        "typedef": ("成员类型别名（`typedef`）",
                    "本语言没有类型别名：类型写全"),
        "using": ("成员类型别名（`using X = Y;`）",
                  "本语言没有类型别名：类型写全"),
        "constexpr": ("`constexpr` 成员",
                      "本语言没有 `constexpr`：常量写 `.tu` 顶层 `const N: u64 = …;`"),
        "inline": ("`inline` 成员",
                   "本语言一个文件就是一个模块，没有内联声明：删掉 `inline`"),
        "mutable": ("`mutable` 成员",
                    "本语言没有 `mutable`：字段本来就都能改（`_` 前缀表示内部约定）"),
        "volatile": ("`volatile` 成员",
                     "本语言没有 `volatile`：删掉它"),
        "auto": ("`auto` 成员函数",
                 "本语言没有 `auto` 类型推断：写全返回类型"
                 "（`u64 f(…) {{ … }}`，没有返回值写 `void` 或 `()`）"),
    }

    def _compat_pick_recv_name(self) -> str:
        """接收者形参名：`recv`，被文件里已有标识符占用时依次 `recv2` / `recv3` …"""
        base = self.MEMBER_RECV_NAME
        cand, n = base, 1
        while cand in self._idents_in_file:
            n += 1
            cand = f"{base}{n}"
        return cand

    def _is_member_field(self, name: Optional[str]) -> bool:
        """成员体里的这个名字是不是**裸字段引用**（要改写成 `接收者.字段`）。

        `compat=False` / 不在成员体里时恒为 False —— 默认路径一个字节都不动。
        被当前作用域链里的局部变量 / 形参遮住的名字不算字段（C++ 同）。
        """
        if not self.compat or self._member_ctx is None or not name:
            return False
        return name in self._member_ctx[1] and not self._scope_shadows(name)

    # -- 作用域（只服务"接收者是什么类型"） ---------------------------------

    def _scope_push(self) -> None:
        self._scopes.append({})

    def _scope_pop(self) -> None:
        if self._scopes:
            self._scopes.pop()

    def _scope_declare(self, name: Optional[str],
                       ty: Optional[Node]) -> None:
        """登记一个局部变量 / 形参的类型节点（不登记时不参与推断）。"""
        if name and self._scopes:
            self._scopes[-1][name] = ty

    def _scope_shadows(self, name: str) -> bool:
        """这个名字是否被**当前作用域链**里的局部变量 / 形参遮住。"""
        for sc in reversed(self._scopes):
            if name in sc:
                return True
        return False

    def _scope_type_of(self, name: str) -> Optional[Node]:
        for sc in reversed(self._scopes):
            if name in sc:
                return sc[name]
        return None

    # -- 接收者类型推断（推不出就报错，不猜） -------------------------------

    def _compat_expr_ty(self, node: Optional[Node]) -> Optional[Node]:
        """表达式在**本文件里**推得出的静态类型节点；推不出返回 None。

        只覆盖"接收者"这一种用法需要的形状：

        | 形状 | 类型 |
        |---|---|
        | `r`（局部 / 形参 / 循环变量） | 登记的类型节点 |
        | `r.f` | 字段类型（struct 字段表） |
        | `xs[i]` | `vector<T>` / `array<T, N>` 的 `T` |
        | `(e)` | `e` 的类型 |

        其它（函数返回值、聚合字面量…）一律 None——由
        `E-Compat-MethodUnresolved` 报清楚，**不猜**。

        **已声明但类型推不出**的变量（`auto`）返回 `_OPAQUE_TY`：调用方据此
        区分"根本没见过这个名字"（可能真是成员调用）与"见过但类型不知道"
        （不能妄断，交给下游既有诊断）。
        """
        k = getattr(node, "kind", "")
        if k == "paren" and node.children:
            return self._compat_expr_ty(node.children[0])
        if k == "path":
            return self._scope_type_of(node.name or "")
        if k == "field" and len(node.children) == 1:
            base = self._compat_expr_ty(node.children[0])
            if base is _OPAQUE_TY:
                return _OPAQUE_TY
            sname = self._compat_struct_name(base)
            if sname is None:
                return None
            return ((self.struct_decls.get(sname) or {}).get("types") or {}).get(
                node.name)
        if k == "index" and len(node.children) == 2:
            base = self._compat_expr_ty(node.children[0])
            if base is _OPAQUE_TY:
                return _OPAQUE_TY
            if base is None or getattr(base, "kind", "") != "type-name":
                return None
            return self._compat_type_arg_node(base, 0)
        return None

    def _compat_type_arg_node(self, ty: Optional[Node],
                              idx: int) -> Optional[Node]:
        """`ty` 的第 `idx` 个类型实参**节点**（`vector<T>` 的 `T`）。"""
        if ty is None:
            return None
        targs = next((c for c in ty.children
                      if getattr(c, "kind", "") == "type-args"), None)
        if targs is None or idx >= len(targs.children):
            return None
        return targs.children[idx]

    def _compat_struct_name(self, ty: Optional[Node]) -> Optional[str]:
        """类型节点若是**本文件里声明过的 struct**，返回它的名字。"""
        head = self._compat_head_of(ty)
        if head and head in self.struct_decls:
            return head
        return None

    def _compat_struct_ty_of(self, node: Optional[Node]) -> Tuple[str, Optional[str]]:
        """接收者类型：`("struct", 名)` / `("other", None)` / `("unknown", None)`。

        `("unknown")` 专指"这个名字在本文件里**根本没登记过**"——只有那种情况
        才可能是"漏掉了成员方法"，值得报 `E-Compat-MethodUnresolved`。
        已登记但类型不知道（`auto`）算 `("other")`：**不猜、不误报**。
        """
        ty = self._compat_expr_ty(node)
        if ty is None:
            return ("unknown", None)
        if ty is _OPAQUE_TY:
            return ("other", None)
        sname = self._compat_struct_name(ty)
        if sname is not None:
            return ("struct", sname)
        return ("other", None)

    def _compat_is_member_name(self, name: str) -> bool:
        """这个名字是不是本文件里**某个 struct 的成员方法**（且不是容器方法）。"""
        known = set()
        for d in self.struct_decls.values():
            known |= set(d.get("method_names") or ())
        if name not in known:
            return False
        # 容器 / 内建同名方法优先（`xs.push(1)` 在 vector 上仍是既有语义），
        # 只有"容器也没有这个名字"时才可能是某个 struct 的成员。
        # 表只有一份：`typecheck.METHOD_TABLE`（延迟导入，避免 parser ↔ typecheck 环）。
        try:
            from typecheck import METHOD_TABLE
            container = {m for ms in METHOD_TABLE.values() for m in ms}
        except Exception:                                   # noqa: BLE001
            container = set()
        return name not in container

    def _compat_build_call(self, mangled: str, recv: Node, args: Node,
                           line: int, col: int) -> Node:
        """手写 `Ring_push(r, 1)` 的**同形** `call` 节点（接收者作为第一个实参）。"""
        call = Node("call", line, col, name=mangled)
        call.add(Node("path", line, col, name=mangled))
        new_args = Node("args")
        new_args.add(recv)
        for a in args.children:
            new_args.add(a)
        call.add(new_args)
        return call

    def _compat_member_call(self, recv: Node, name: str, args: Node,
                            line: int, col: int,
                            tok: Token) -> Optional[Node]:
        """`recv.method(args)` → `Struct_method(recv, args)`（第 13 条）。

        返回 None 表示"这**不是**用户 struct 的成员方法"，交给原来的
        `method-call` 路径（容器方法 / 既有诊断）。
        """
        kind, sname = self._compat_struct_ty_of(recv)
        if kind == "struct":
            names = (self.struct_decls.get(sname) or {}).get("method_names") or ()
            if name in names:
                return self._compat_build_call(f"{sname}_{name}", recv, args,
                                               line, col)
            return None                     # struct 有类型但没这个方法：既有诊断
        if kind == "unknown" and self._compat_is_member_name(name):
            # 名字能定到**唯一一个** struct 时就用它的名字写改法（可照抄）；
            # 多个 struct 都有这个方法时保持通用写法。
            who = [s for s, d in self.struct_decls.items()
                   if name in (d.get("method_names") or ())]
            w = who[0] if len(who) == 1 else "Ring"
            raise ParseError(
                "E-Compat-MethodUnresolved",
                f"`{name}(…)` 是本文件里某个 struct 的**成员函数**，但接在后面那个"
                f"表达式的类型在**这个文件里**推不出来，所以没法降级。\n"
                f"`--compat` 只在**同一个文件**里认识成员（跨 `.tu` 看不见："
                f"`struct` 必须与调用写在一起）。\n"
                f"改法：① 把接收者存进**具名变量**再调（`{w} tmp = …; "
                f"tmp.{name}(…)`）——直接对函数返回值 / `auto` 变量调方法推不出类型；\n"
                f"      ② 直接写降级后的自由函数：`{w}_{name}(接收者, …)`"
                f"（第一个实参是接收者）。",
                tok)
        return None

    # -- 字段预扫：C++ 允许成员函数引用**后面**才声明的字段 ----------------

    def _compat_probe_field_decl(self, j: int) -> Optional[Tuple[Node, str, int]]:
        """试解一个 **C++ 字段声明** `类型 名字 (,|;|})` → (类型节点, 名字, 下一位置)。

        判据与 `parse_struct` 里真正的字段分支**同一份代码**（`parse_type`），
        不会漂移；副作用全部回滚（下标 / 诊断 / `_spaced_ptr_ok` / 成员上下文）。
        """
        save_i, save_d, save_s = self.i, len(self.diags), len(self.soft)
        save_ok, save_mc = self._spaced_ptr_ok, self._member_ctx
        self.i = j
        self._spaced_ptr_ok = True
        self._member_ctx = None
        try:
            ty = self.parse_type()
            if ty is not None and self.at(Kind.IDENT) and self.kind(1) in (
                    Kind.SEMI, Kind.COMMA, Kind.RBRACE):
                return (ty, self.peek().text or "", self.i + 1)
            # **第 16 条**：带默认初始值的字段（`double value = 0;` / `u64 i_ = 0;`）
            # 也要进预扫表——成员函数体里引用这些字段时，`_is_member_field`
            # 靠这张表把它们改写成 `recv.…`。这里只借用表达式之后的**终止符**
            # 位置；表达式本身由真正的字段解析路径再解析一次。
            if ty is not None and self.compat and self.at(Kind.IDENT) \
                    and self.kind(1) == Kind.EQ:
                return (ty, self.peek().text or "",
                        self._probe_member_end(self.i + 2))
            return None
        except ParseError:
            return None
        finally:
            self.i = save_i
            del self.diags[save_d:]
            del self.soft[save_s:]
            self._spaced_ptr_ok = save_ok
            self._member_ctx = save_mc

    def _probe_member_end(self, j: int) -> int:
        """从 `j` 起跳过一段**成员初始值表达式**，返回终止符的绝对下标。

        只跟踪配对括号；`;` / `,` / `}` 在深度 0 时是成员终止符（不消费）。
        """
        depth = 0
        while j < len(self.toks):
            k = self.kind_at(j)
            if k in (Kind.LPAREN, Kind.LBRACKET, Kind.LBRACE):
                depth += 1
            elif k in (Kind.RPAREN, Kind.RBRACKET, Kind.RBRACE):
                if depth == 0:
                    return j
                depth -= 1
            elif depth == 0 and k in (Kind.SEMI, Kind.COMMA):
                return j
            j += 1
        return j

    def _compat_prescan_fields(self) -> Tuple[List[Tuple[Node, str]], List[str]]:
        """在解析 struct 体**之前**扫出 `(字段类型, 字段名)` 与**成员函数名**。

        为什么要预扫：C++ 允许成员引用**后面**才声明的东西——
        `void f() { total = 1; } u64 total;` / `void a() { b(); } void b() { }`。
        单遍解析到这里时字段表与方法表还是空的，那些名字会被当成"未声明的
        变量 / 未声明的函数"——报错，或者更糟：被当成同名的全局东西。

        预扫只做一件事：把**名字**先登记好，成员体照常按顺序解析
        （类型节点照抄，仅供"初始化列表按字段类型选等价表达式"用）。

        成员函数体用花括号跳过（`{` 之后 `depth > 0`），所以体里的局部变量
        不会被误认成字段。
        """
        out: List[Tuple[Node, str]] = []
        names: List[str] = []
        j = self.i
        depth = 0
        while j < len(self.toks):
            k = self.kind_at(j)
            if k == Kind.EOF or (k == Kind.RBRACE and depth == 0):
                break
            if k in (Kind.LPAREN, Kind.LBRACKET, Kind.LBRACE):
                depth += 1
                j += 1
                continue
            if k in (Kind.RPAREN, Kind.RBRACKET, Kind.RBRACE):
                if depth == 0:
                    break
                depth -= 1
                j += 1
                continue
            if depth != 0:
                j += 1
                continue
            # **C++ 访问说明符**（`public:` / `private:` / `protected:`）：
            # 不是字段。不跳过的话，预扫会把 `public:` 后面那个字段的类型
            # 当成 `public` 的类型、再把字段名吃掉（`public: u64 x;` →
            # 假字段 `public`，真字段 `x` 丢失），成员体里引用 `x` 就不会
            # 改写成 `recv.x`。第 15 条（`class`）几乎必然带访问说明符。
            if (self.kind_at(j) == Kind.IDENT
                    and (self.toks[j].text or "") in ("public", "private",
                                                      "protected")
                    and self.kind_at(j + 1) == Kind.COLON):
                j += 2
                continue
            # 本语言自己的字段写法 `name: 类型,` 也要认——`--compat` 下
            # C++ 写法（`u64 x;`）与本语言写法（`x: u64,`）混写是合法的。
            if (self.kind_at(j) == Kind.IDENT
                    and self.kind_at(j + 1) == Kind.COLON):
                got_colon = self._compat_probe_colon_field(j)
                if got_colon is not None:
                    out.append((got_colon[0], got_colon[1]))
                    j = got_colon[2]
                    continue
            got = self._compat_probe_field_decl(j)
            if got is not None:
                out.append((got[0], got[1]))
                j = got[2]
                continue
            # 成员函数 `类型 名字 (`：只记名字（体在下面用花括号跳过）。
            end = self._probe_type_end(start=j)
            if (end is not None and self.kind_at(end) == Kind.IDENT
                    and self.kind_at(end + 1) == Kind.LPAREN):
                nm = self.toks[end].text or ""
                if nm and nm != "operator":
                    names.append(nm)
                # 停在 `(` 上，让下面的配对逻辑吃掉整个形参表与函数体
                # （跳过去的话 `)` 会在 depth 0 处提前结束扫描）。
                j = end + 1
                continue
            j += 1
        return out, names

    def _compat_probe_colon_field(self, j: int) -> Optional[Tuple[Node, str, int]]:
        """试解本语言的字段写法 `name: 类型 (,|;|})` → (类型节点, 名字, 下一位置)。"""
        save_i, save_d, save_s = self.i, len(self.diags), len(self.soft)
        save_ok, save_mc = self._spaced_ptr_ok, self._member_ctx
        self.i = j
        self._spaced_ptr_ok = True
        self._member_ctx = None
        try:
            if not self.at(Kind.IDENT) or self.kind(1) != Kind.COLON:
                return None
            nm = self.advance().text or ""
            self.advance()                            # ':'
            ty = self.parse_type()
            if ty is None:
                return None
            return (ty, nm, self.i)
        except ParseError:
            return None
        finally:
            self.i = save_i
            del self.diags[save_d:]
            del self.soft[save_s:]
            self._spaced_ptr_ok = save_ok
            self._member_ctx = save_mc

    # -- 成员形状判据 / 解析 ------------------------------------------------

    def _compat_member_ahead(self, sname: str) -> bool:
        """当前位置是不是一个**能降级**的成员（构造函数 / 成员函数）。

        判据是**形状**，不是名字白名单：

        * `Ring(` / `Ring() : …` —— 构造函数（名字 = struct 名）；
        * `类型 名字 (` —— 成员函数（类型部分用 `_probe_type_end` 试解，
          与真正的声明同一份代码）。
        """
        if not self.compat or not self.at(Kind.IDENT):
            return False
        # 明确**不支持**的成员形状（`explicit` / `virtual` / `static` / …）先交给
        # `_compat_member_reject`：否则 `explicit JsonParser(…)` 会被这里试解成
        # 返回类型 `explicit` + 函数名 `JsonParser`，报成"期望函数体 `{`"——
        # 一条与真正原因（`explicit` 不支持）无关的诊断，而且会把整个 struct
        # 体的解析**抛断**（错误恢复从构造参数后面一路跳到下一个 item）。
        if (self.peek().text or "") in self.MEMBER_UNSUPPORTED_WORDS:
            return False
        if (self.peek().text or "") == sname and self.kind(1) == Kind.LPAREN:
            return True
        end = self._probe_type_end()
        if end is None or self.kind_at(end) != Kind.IDENT:
            return False
        return self.kind_at(end + 1) == Kind.LPAREN

    def _compat_member_reject(self, sname: str) -> bool:
        """不支持的成员形状 → **报一条带改法的诊断**并跳过；返回 True 表示已处理。

        `E-Struct-BodyForm`（"struct 体里只能写字段"）那条旧诊断不再适用于
        `--compat`：本轮之后成员函数是**支持的**，读者要看的是"这一种写法
        为什么不支持"。所以这里按形状各给一条，最后统一用
        `E-Compat-MemberUnsupported` 兜底。
        """
        t = self.peek()
        if t.kind == Kind.TILDE:
            self.error("E-Compat-MemberUnsupported",
                       "析构函数（`~" + sname + "(`）没有降级目标：本语言没有"
                       "析构函数，也没有 RAII 的析构时机概念（容器自己管理内存）。\n"
                       "改法：把清理逻辑写成具名自由函数并在最后显式调用"
                       "（`() Ring_clear(Ring& recv) { … }`），或在退出前逐项清空。",
                       t)
            self._skip_member_decl()
            return True
        if t.kind == Kind.IDENT and (t.text or "") in self.MEMBER_UNSUPPORTED_WORDS:
            # **第 16 条**：`explicit S(…)` 仍然是「这个 struct 有构造函数」——
            # 它只是没被降级。记下来，免得字段默认值再合成一个 0 参构造函数，
            # 把「explicit 不支持」的诊断掩盖成「实参个数对不上」。
            if ((t.text or "") == "explicit" and self.kind(1) == Kind.IDENT
                    and (self.peek(1).text or "") == sname
                    and self.kind(2) == Kind.LPAREN):
                self.struct_decls.setdefault(sname, {})["ctor_seen"] = True
            what, how = self.MEMBER_UNSUPPORTED_WORDS[t.text or ""]
            # `template` / `using` 这些词后面跟的不是"声明"，跳过整段即可。
            self.error("E-Compat-MemberUnsupported",
                       f"struct 体里这一种成员写法不支持：{what}。\n改法：{how}。",
                       t)
            self._skip_member_decl()
            return True
        # `bool operator==(const P& o)`：类型之后的名字是 `operator`。
        end = self._probe_type_end()
        if (end is not None and self.kind_at(end) == Kind.IDENT
                and (self.toks[end].text or "") == "operator"):
            what, how = self.MEMBER_UNSUPPORTED_WORDS["operator"]
            self.error("E-Compat-MemberUnsupported",
                       f"struct 体里这一种成员写法不支持：{what}。\n改法：{how}。",
                       self.toks[end])
            self._skip_member_decl()
            return True
        # **C++ 的字段默认初始值**（`double value = 0;`）：它不是"未知成员"，
        # 而是**已知缺口**——本语言没有字段默认值。用**已有的**
        # `E-Struct-DefaultMember` 报（文案里给出 `--compat` 的落点：构造函数），
        # 而不是让它掉进"本语言没有成员函数"那句（本轮之后那句话已经不成立）。
        if (end is not None and self.kind_at(end) == Kind.IDENT
                and self.kind_at(end + 1) == Kind.EQ):
            self.error(
                "E-Struct-DefaultMember",
                f"struct 字段不能带默认初始值（`{self.toks[end].text} = …`）。\n"
                f"本语言没有「字段默认值」：默认值只有一个来源——类型的零值"
                f"（数字 `0` / 空容器）。\n"
                f"改法：① `--compat` 下写进**构造函数**"
                f"（`{sname}(u64 v) : {self.toks[end].text}(v) {{}}`，降级后是"
                f"构造体开头的 `recv.{self.toks[end].text} = v;`）；\n"
                f"      ② 在构造处给值（`{sname} {{ {self.toks[end].text}: 0 }}`）；\n"
                f"      ③ 用工厂函数造出对象。",
                self.toks[end])
            self._skip_member_decl()
            return True
        return False

    def _skip_member_decl(self) -> None:
        """跳过一个**不支持的成员声明**（只为错误恢复：一条诊断一个说法）。

        只跟踪 `(` / `[` / `{` 的配对：看到本层的 `{ … }` 就整块跳过并返回，
        看到本层的 `;` 就吃掉返回。`template<…>` / `operator<` 里的 `<` `>`
        不参与配对（它们在两种读法里都不会影响上面两条判据）。
        """
        depth = 0
        while not self.at(Kind.EOF) and not self.at(Kind.RBRACE):
            k = self.kind()
            if k == Kind.LPAREN or k == Kind.LBRACKET:
                depth += 1
            elif k == Kind.RPAREN or k == Kind.RBRACKET:
                depth = max(0, depth - 1)
            elif k == Kind.LBRACE:
                self._skip_balanced_braces()
                self.accept(Kind.SEMI)
                return
            elif k == Kind.SEMI and depth == 0:
                self.advance()
                return
            self.advance()

    def _skip_balanced_braces(self) -> None:
        """吃掉一个配平的 `{ … }`（当前位置必须是 `{`）。"""
        depth = 0
        while not self.at(Kind.EOF):
            k = self.advance().kind
            if k == Kind.LBRACE:
                depth += 1
            elif k == Kind.RBRACE:
                depth -= 1
                if depth == 0:
                    return

    def _compat_member_quals(self) -> None:
        """吃掉成员函数尾部的限定符：`const` / `noexcept` / `override` / `final` …

        **忽略**它们，不只是跳过：`const` / `noexcept` / `override` 在本语言里
        都没有对应概念（没有继承可覆盖、没有异常、引用写回是另一条规则）。
        忽略 = 接受更多写法，不会把合法程序判死；真正的写回契约仍由
        `typecheck` 按形参类型判（`T&` 对 struct 成立）。
        """
        while True:
            if self.at(Kind.CONST):
                self.advance()
                continue
            if self.at(Kind.AMP) or self.at(Kind.AMPAMP):
                self.advance()                   # `… const &` / `&&`
                continue
            if self.at(Kind.IDENT) and (self.peek().text or "") in (
                    "noexcept", "override", "final", "mutable", "volatile"):
                self.advance()
                if self.at(Kind.LPAREN):         # `noexcept(…)`
                    self.advance()
                    depth = 1
                    while not self.at(Kind.EOF) and depth > 0:
                        kk = self.advance().kind
                        if kk == Kind.LPAREN:
                            depth += 1
                        elif kk == Kind.RPAREN:
                            depth -= 1
                continue
            return

    def _compat_require_member_body(self, tok: Token, sname: str = "") -> None:
        """成员函数/构造函数必须有 `{ … }` 体。没有就报清楚（不静默跳过）。"""
        if self.at(Kind.EQ):
            raise ParseError(
                "E-Compat-MemberUnsupported",
                "`= default` / `= delete` 没有降级目标：本语言没有编译器自动生成的"
                "成员，也没有\"删除\"某个成员的概念。\n"
                "改法：把想要的初始化写进构造函数体（`Ring() : head(0) {}` 或 "
                "`{ recv.head = 0; }`），要禁止某个构造就干脆别写它。",
                self.peek())
        if self.at(Kind.SEMI):
            raise ParseError(
                "E-Compat-MemberUnsupported",
                "成员函数只有声明、没有函数体（`.tu` 里前置声明 + 类外定义 `void "
                f"{sname or 'Ring'}::push(…) {{ }}` 的写法）。本语言没有前置声明："
                "函数**定义在哪**就在哪可见。\n"
                "改法：把函数体直接写进 struct 体（`void push(int v) { … }`），"
                f"或写成同文件里的自由函数 `() {sname or 'Ring'}_push("
                f"{sname or 'Ring'}& recv, i64 v) {{ … }}`。",
                self.peek())
        if not self.at(Kind.LBRACE):
            raise ParseError(
                "E-Compat-MemberUnsupported",
                f"成员声明后面期望函数体 `{{`，实际是 {self.kind().value!r}。\n"
                "改法：成员函数必须带体（本语言没有前置声明）；写成自由函数也可以。",
                self.peek())

    @staticmethod
    def _compat_with_recv(params: Node, recv: Node) -> Node:
        """把接收者形参插到形参表最前面。"""
        out = Node("params")
        out.add(recv)
        for p in params.children:
            out.add(p)
        return out

    def _compat_member_overload(self, sname: str, name: str, tok: Token) -> None:
        raise ParseError(
            "E-Compat-MemberOverload",
            f"`{sname}::{name}` 有**重载**（同名成员出现多次）。本语言没有重载："
            f"同一个名字只能有一个定义。\n"
            f"改法：改成不同的名字（`{sname}_{name}2` 只用于区分语义不同的版本，"
            f"例如 `Ring_push_many`），或把参数类型合成一个（例如统一收 "
            f"`vector<…>`）。",
            tok)

    def _compat_parse_member_fn(self, sname: str, fields: frozenset) -> Node:
        """解析一个成员函数并降级成自由函数节点（第 13 条）。"""
        t = self.peek()
        ret = self._compat_ret_type()
        nt = self.expect_ident("成员函数名")
        if self.at(Kind.LT):
            what, how = self.MEMBER_UNSUPPORTED_WORDS["template"]
            raise ParseError("E-Compat-MemberUnsupported",
                             f"struct 体里这一种成员写法不支持：{what}。\n改法：{how}。",
                             self.peek())
        self.expect(Kind.LPAREN, "'('")
        recv_name = self._compat_pick_recv_name()
        self._scope_push()
        try:
            params = self.parse_params_until(Kind.RPAREN)
            self.expect(Kind.RPAREN, "')'")
            self._compat_member_quals()
            self._compat_require_member_body(nt, sname)
            self._member_ctx = (recv_name, fields, sname)
            try:
                body = self.parse_block()
            finally:
                self._member_ctx = None
        finally:
            self._scope_pop()
        methods = self.struct_decls.setdefault(sname, {}).setdefault("methods", {})
        if nt.text in methods:
            self._compat_member_overload(sname, nt.text, nt)
        mangled = f"{sname}_{nt.text}"
        recv_param = Node("param", t.line, t.col, name=recv_name).add(
            Node("type-ref", t.line, t.col).add(
                Node("type-name", t.line, t.col, name=sname)))
        fn = Node("fn", t.line, t.col, name=mangled)
        fn.value = {"pub": False, "fallible": False}
        fn.add(self._compat_with_recv(params, recv_param))
        fn.add(ret)
        fn.add(body)
        methods[nt.text] = fn
        self.value_names.add(mangled)
        return fn

    def _compat_parse_ctor(self, sname: str, fields: frozenset) -> Node:
        """解析构造函数并降级成自由函数（第 13 条）。

        初始化列表 `: buf(cap, 0), head(0)` → **构造体开头**的字段赋值：

            recv.buf = vector<i64>::filled(0, cap);   // 按字段类型选等价表达式
            recv.head = 0;

        与手写同形，所以 κ / run 必然一致。**没写进初始化列表的字段不动**
        （`Ring r;` 已经给了本语言的默认值；C++ 那边是 indeterminate，不能依赖）。
        `: f()`（零实参）同样不动：C++ 的 `f()` 是 value-initialization，
        正好等于本语言的默认值（`0` / 空容器）。
        """
        t = self.advance()                       # struct 名（= 构造函数名）
        self.expect(Kind.LPAREN, "'('")
        recv_name = self._compat_pick_recv_name()
        self._scope_push()
        try:
            params = self.parse_params_until(Kind.RPAREN)
            self.expect(Kind.RPAREN, "')'")
            types = (self.struct_decls.get(sname) or {}).get("types") or {}
            inits: List[Tuple[Token, Node]] = []
            # **初始化列表的实参也在成员上下文里**：C++ 允许
            # `R(u64 n) : buf(n, 0), count(buf.size())`（引用已经初始化的成员），
            # 所以里面的裸字段名照样降级成 `recv.…`（形参仍然遮蔽字段）。
            self._member_ctx = (recv_name, fields, sname)
            try:
                if self.accept(Kind.COLON):
                    while True:
                        ft = self.expect_ident("字段名")
                        if ft.text not in types:
                            raise ParseError(
                                "E-Compat-InitListField",
                                f"初始化列表里的 `{ft.text}` 不是 `struct {sname}` 的字段。\n"
                                f"`--compat` 只认字段名（按 struct 体里的声明；本文件里"
                                f"看得见的字段："
                                f"{' / '.join(types) if types else '（没有）'}）。\n"
                                f"改法：写字段名（`{next(iter(types), 'x')}(…)`）；"
                                f"要写基类 / 委托构造就把那段逻辑搬进构造体。",
                                ft)
                        self.expect(Kind.LPAREN, "'('")
                        args = self.parse_args_until(Kind.RPAREN)
                        self.expect(Kind.RPAREN, "')'")
                        inits.append((ft, args))
                        if not self.accept(Kind.COMMA):
                            break
                self._compat_member_quals()
                self._compat_require_member_body(t, sname)
                body = self.parse_block()
            finally:
                self._member_ctx = None
        finally:
            self._scope_pop()
        # 初始化列表 → 构造体**开头**的字段赋值（顺序照抄 C++ 的书写顺序）。
        new_body = Node("block", body.line, body.col)
        for ft, args in inits:
            fty = types[ft.text]
            if not args.children:                 # `f()` = value-init = 本语言默认值
                continue
            rhs = self._compat_ctor_expr(copy.deepcopy(fty), args, ft,
                                         name=ft.text)
            lhs = Node("field", ft.line, ft.col, name=ft.text).add(
                Node("path", ft.line, ft.col, name=recv_name))
            new_body.add(Node("assign", ft.line, ft.col).add(lhs, rhs))
        for c in body.children:
            new_body.add(c)
        ctors = self.struct_decls.setdefault(sname, {}).setdefault("ctors", [])
        # 降级后**每个构造函数都落到同一个名字** `S_S`，所以"第二个构造函数"
        # 本身就不可降级——不管参数个数是否相同（本语言没有重载）。
        if ctors:
            raise ParseError(
                "E-Compat-MemberOverload",
                f"`struct {sname}` 有**多个构造函数**（重载）。本语言没有重载，"
                f"而降级后它们都会落成同一个自由函数名 `{sname}_{sname}`"
                f"（已有 {len(ctors)} 个）。\n"
                f"改法：① 只留一个构造函数，其余在调用处直接给字段赋值"
                f"（`{sname} x; x.f = …;`）；\n"
                f"      ② 其余构造函数改写成**不同名字**的自由函数"
                f"（例如 `() {sname}_with_cap({sname}& recv, u64 cap) {{ … }}`），"
                f"调用处写那个名字。",
                t)
        recv_param = Node("param", t.line, t.col, name=recv_name).add(
            Node("type-ref", t.line, t.col).add(
                Node("type-name", t.line, t.col, name=sname)))
        fn = Node("fn", t.line, t.col, name=f"{sname}_{sname}")
        fn.value = {"pub": False, "fallible": False}
        fn.add(self._compat_with_recv(params, recv_param))
        fn.add(Node("type-tuple", t.line, t.col))
        fn.add(new_body)
        ctors.append(fn)
        # **第 16 条**：记下这个构造函数**显式初始化**了哪些字段——字段默认值
        # 要跳过它们（C++：mem-initializer 优先于 default member initializer，
        # 包括 `: x()` 这种 value-init）。
        self.struct_decls.setdefault(sname, {}).setdefault(
            "ctor_init_fields", []).append(frozenset(ft.text for ft, _a in inits))
        self.value_names.add(fn.name)
        return fn

    def _compat_parse_member(self, sname: str, fields: frozenset) -> Node:
        """当前位置的成员（构造函数 / 成员函数）→ 降级后的 `fn` 节点。"""
        if (self.peek().text or "") == sname and self.kind(1) == Kind.LPAREN:
            return self._compat_parse_ctor(sname, fields)
        return self._compat_parse_member_fn(sname, fields)

    # -- `Ring r(3);` → `Ring r; Ring_Ring(r, 3);` --------------------------

    def _compat_at_struct_ctor_decl(self) -> bool:
        """语句起点是不是 `Struct 名(实参…);`（本文件里有构造函数的 struct）。

        判据**宁窄勿宽**：`Struct` 必须是**本文件里**声明过、且**有构造函数**的
        struct；后面必须是 `变量名 (`，且 `)` 之后是 `;`（与第 10 条的
        `_compat_ctor_call_ahead` 同一份判据）。
        """
        if not self.compat or not self.at(Kind.IDENT):
            return False
        head = self.peek().text or ""
        d = self.struct_decls.get(head)
        if not d or not d.get("ctors"):
            return False
        end = self._probe_type_end()
        if end is None or self.kind_at(end) != Kind.IDENT:
            return False
        if self.kind_at(end + 1) != Kind.LPAREN:
            return False
        return self._compat_ctor_call_ahead(end + 1)

    def _compat_parse_struct_ctor_decl(self) -> List[Node]:
        """`Ring r(3);` → `Ring r;` + `Ring_Ring(r, 3);`（两个兄弟节点）。

        与手写两行**同形**（第 9 条的多声明符也是这么展开的），所以 κ / run
        与手写必然相同。构造函数必须在本文件里看得见（跨 `.tu` 不行）。
        """
        t = self.peek()
        ty_node = self.parse_type()
        sname = self._compat_head_of(ty_node) or ""
        name = self.expect_ident("变量名")
        lp = self.expect(Kind.LPAREN, "'('")
        args = self.parse_args_until(Kind.RPAREN)
        self.expect(Kind.RPAREN, "')'")
        self.expect(Kind.SEMI, "';'")
        decl = Node("var-decl", t.line, t.col)
        decl.value = {"mut": True, "has_init": False}
        decl.add(ty_node)
        decl.name = name.text
        self.value_names.add(decl.name)
        self._scope_declare(decl.name, ty_node)
        ctors = (self.struct_decls.get(sname) or {}).get("ctors") or []
        arity = len(args.children)
        if not any(len(c.children[0].children) == arity + 1 for c in ctors
                   if c.children):
            got = sorted(len(c.children[0].children) - 1 for c in ctors
                         if c.children)
            raise ParseError(
                "E-Compat-CtorArity",
                f"`struct {sname}` 的构造函数**没有 {arity} 个实参的版本**"
                f"（本文件里看到的是 {', '.join(str(g) for g in got)} 个）。\n"
                f"降级目标 `{sname} {name.text}; {sname}_{sname}({name.text}, "
                f"…);` 要求两边个数一致。\n"
                f"改法：按构造函数的形参个数传实参，或把构造函数改成匹配个数，"
                f"或先 `{sname} {name.text};` 再逐个字段赋值。",
                lp)
        call = self._compat_build_call(
            f"{sname}_{sname}", Node("path", t.line, t.col, name=decl.name),
            args, t.line, t.col)
        return [decl, Node("expr-stmt", t.line, t.col).add(call)]

    def _compat_fill_default_ctor(self, stmts: List[Node]) -> List[Node]:
        """`Ring r;` + 该 struct 有 **0 参构造函数** → 补一句 `Ring_Ring(r);`。

        C++ 里 `Ring r;` 就是调默认构造函数；不补这一句就是**静默不同的程序**。
        只在"本文件里看得见那个 struct 且确实有 0 参构造函数"时补——其余情况
        `Ring r;` 保持本语言的默认值语义（逐字节不变）。
        """
        out: List[Node] = []
        for st in stmts:
            out.append(st)
            if st.kind not in ("var-decl", "const-decl") or not st.children:
                continue
            if (st.value or {}).get("has_init"):
                continue
            head = self._compat_head_of(st.children[0])
            d = self.struct_decls.get(head) if head else None
            if not d:
                continue
            ctors = d.get("ctors") or []
            if not any(c.children and len(c.children[0].children) == 1
                       for c in ctors):
                continue
            out.append(Node("expr-stmt", st.line, st.col).add(
                self._compat_build_call(f"{head}_{head}",
                                        Node("path", st.line, st.col,
                                             name=st.name),
                                        Node("args"), st.line, st.col)))
        return out

    # -- 第 16 条：字段默认初始值 → 构造函数体开头的赋值 ----------------------

    def _compat_default_member_ahead(self) -> bool:
        """当前位置是不是 **C++ 的字段默认初始值** `类型 名字 = 表达式`。

        判据是**形状**（与 `_compat_probe_field_decl` 同一份试解）：试解出一个
        类型、后面跟标识符、再后面是 `=`。宁窄勿宽——
        `x: u64 = 5`（本语言写法）在这里不成立（类型试解在 `:` 处就结束），
        它走 `parse_struct` 里本语言字段那条分支。
        """
        if not self.at(Kind.IDENT):
            return False
        # 明确不支持的成员前缀（`static` / `constexpr` / `auto` / `mutable` …）
        # 仍交给 `_compat_member_reject`：`auto x = 1;` 不能因为形状像默认值就
        # 被当成"字段类型 auto"（那条诊断比"未知类型名"准得多）。
        if (self.peek().text or "") in self.MEMBER_UNSUPPORTED_WORDS:
            return False
        end = self._probe_type_end()
        return (end is not None and self.kind_at(end) == Kind.IDENT
                and self.kind_at(end + 1) == Kind.EQ)

    def _compat_parse_default_member(self) -> Tuple[Node, Node]:
        """解析 `类型 名字 = 表达式` → `(field 节点, 默认值表达式)`。

        调用前必须由 `_compat_default_member_ahead()` 确认形状；终止符
        （`;` / `,` / `}`）由调用方处理。
        """
        typ = self.parse_type()
        ft = self.expect_ident("字段名")
        self.expect(Kind.EQ, "'='")
        expr = self.parse_expr()
        return Node("field", ft.line, ft.col, name=ft.text).add(typ), expr

    def _compat_default_assign(self, name: str, line: int, col: int,
                               expr: Node, recv_name: str) -> Node:
        """`recv.<字段> = <默认值>;` —— 与手写初始化列表赋值**同形**。"""
        lhs = Node("field", line, col, name=name).add(
            Node("path", line, col, name=recv_name))
        return Node("assign", line, col).add(lhs, expr)

    def _compat_default_refs(self, expr: Optional[Node],
                             fields: frozenset) -> List[str]:
        """默认值表达式里**引用到的字段名**（审查用；顺序按出现顺序去重）。"""
        out: List[str] = []

        def walk(n: Optional[Node]) -> None:
            if n is None:
                return
            if getattr(n, "kind", "") == "path" and n.name in fields:
                if n.name not in out:
                    out.append(n.name)
            for c in (getattr(n, "children", None) or ()):
                walk(c)

        walk(expr)
        return out

    def _compat_apply_default_members(self, node: Node,
                                      decl: Optional[dict],
                                      defaults: List[Tuple[str, int, int, Node]],
                                      member_fns: List[Node],
                                      tok: Token) -> None:
        """把一个 struct 的字段默认值落成构造函数体开头的赋值（第 16 条）。

        * **有显式构造函数**：插到构造体**最前面**，按**字段声明顺序**；显式
          初始化列表里已经给出的字段**跳过**（C++ 里 mem-initializer 优先）。
        * **没有构造函数**：合成一个零参构造函数 `S_S(S& recv) { recv.a = …; }`
          ——这样 `S s;` 会由 `_compat_fill_default_ctor` 补一句 `S_S(s);`，
          与手写同形。
        * 默认值**引用其它字段** → `E-Compat-DefaultMemberRef`（报清楚，不静默）。
        """
        fields = frozenset(
            f.name for f in node.children
            if getattr(f, "kind", "") == "field" and f.name)
        for name, line, col, expr in defaults:
            refs = self._compat_default_refs(expr, fields)
            if not refs:
                continue
            self.error(
                "E-Compat-DefaultMemberRef",
                f"`struct {node.name}` 的字段默认值不能引用其它字段"
                f"（`{name} = …` 里出现了 `{'` / `'.join(refs)}`）。\n"
                f"本语言没有「成员之间互相初始化」：默认值降级成构造函数开头的"
                f"赋值，引用另一个字段就依赖声明顺序（C++ 里同样不允许用成员"
                f"初始化成员）。\n"
                f"改法：① 默认值只写常量 / 字面量 / 全局 `const`；\n"
                f"      ② 需要按字段计算就写进构造函数体"
                f"（`{node.name}() {{ {name} = …; }}`，降级后是构造体里的显式"
                f"赋值）；\n"
                f"      ③ 在构造处用聚合初始化把相关字段都写出来。",
                Token(Kind.IDENT, name, line, col, 0, 0))
        if decl is None:
            return
        # **不能写 `decl.get("ctors") or []`**：空列表是 falsy，那样会拿到一个
        # 新列表、把合成出来的构造函数丢掉（`S s;` 就不会补 `S_S(s);`）。
        ctors = decl.setdefault("ctors", [])
        inits = decl.setdefault("ctor_init_fields", [])
        while len(inits) < len(ctors):
            inits.append(frozenset())
        if ctors:
            for ctor, init_fields in zip(ctors, inits):
                params = ctor.children[0] if ctor.children else None
                recv_name = (params.children[0].name
                             if params is not None and params.children else None)
                if not recv_name:
                    continue
                prefix = [self._compat_default_assign(name, line, col, expr,
                                                      recv_name)
                          for name, line, col, expr in defaults
                          if name not in init_fields]
                if not prefix:
                    continue
                body = ctor.children[-1]
                body.children[0:0] = prefix
            return
        # 有构造函数但**没能降级**（例如 `explicit S(…)` 被成员判据拒绝）：
        # 不能再合成一个 0 参构造——那会把"没有构造函数"这个诊断变成
        # "实参个数对不上"，掩盖真正的原因。
        if decl.get("ctor_seen"):
            return
        synth = self._compat_synth_default_ctor(node.name, defaults, tok)
        member_fns.append(synth)
        ctors.append(synth)
        inits.append(frozenset())

    def _compat_synth_default_ctor(self, sname: str,
                                   defaults: List[Tuple[str, int, int, Node]],
                                   tok: Token) -> Node:
        """合成零参构造函数：`() S_S(S& recv) { recv.a = 0; … }`（第 16 条）。"""
        recv_name = self._compat_pick_recv_name()
        params = Node("params").add(
            Node("param", tok.line, tok.col, name=recv_name).add(
                Node("type-ref", tok.line, tok.col).add(
                    Node("type-name", tok.line, tok.col, name=sname))))
        body = Node("block", tok.line, tok.col)
        for name, line, col, expr in defaults:
            body.add(self._compat_default_assign(name, line, col, expr,
                                                 recv_name))
        fn = Node("fn", tok.line, tok.col, name=f"{sname}_{sname}")
        fn.value = {"pub": False, "fallible": False}
        fn.add(params)
        fn.add(Node("type-tuple", tok.line, tok.col))
        fn.add(body)
        self.value_names.add(fn.name)
        return fn

    def recover_item(self, stop_before_rbrace: bool = False):
        """跳到下一个可能的顶层构造起点。

        `stop_before_rbrace=True`：遇到**本层**的 `}` 就停下、**不消费**它——
        `namespace` 体的恢复用这个模式，否则会把命名空间的收尾 `}` 当成
        "某个 item 的收尾"吃掉，后面整段代码会被当成顶层代码误解析。
        """
        depth = 0
        while not self.at(Kind.EOF):
            k = self.kind()
            if k in (Kind.LPAREN, Kind.LBRACKET, Kind.LBRACE):
                depth += 1
            elif k in (Kind.RPAREN, Kind.RBRACKET, Kind.RBRACE):
                if depth == 0:
                    if stop_before_rbrace and k == Kind.RBRACE:
                        return
                    self.advance()
                    if k == Kind.RBRACE:
                        # 可能是 `} end X` 的收尾（`end` 现在是普通 IDENT）
                        if self.at(Kind.IDENT) and (self.peek().text or "") == "end":
                            self.advance()
                            if self.at(Kind.IDENT) or self.kind() in ITEM_START:
                                self.advance()
                        return
                    continue
                depth -= 1
            elif depth == 0 and (
                    k in ITEM_START
                    # **第 15 条**：`--compat` 下 `class X` 也是一个 item 起点
                    # （否则恢复会跳过整个 class，变成另一个盲区）。
                    or (self.compat and k == Kind.IDENT
                        and (self.peek().text or "") == "class"
                        and self.kind(1) == Kind.IDENT)):
                return
            self.advance()

    # ============================================================ 顶层

    def parse_module(self) -> Node:
        mod = Node("module", 1, 1, name=self.src_name)
        while not self.at(Kind.EOF):
            if self.at(Kind.SEMI):
                self.advance()
                continue
            mark = self.i
            try:
                item = self.parse_item()
                if item is not None:
                    # `namespace X { … }` 在这里**展平**：内部 item 直接进
                    # 模块全局作用域（重名报 `E-Name-Duplicate`，不静默覆盖）。
                    self._add_top_item(mod, item)
            except ParseError as e:
                self.error(e.code, e.message, e.tok)
                if self.i == mark:
                    self.advance()
                skip_from = self.peek()
                self.recover_item()
                self._report_skipped(skip_from)
        return mod

    #: 错误恢复一次跳过多少行以上，就必须**再报一条** `E-Parse-SkippedBody`。
    #:
    #: 动机（2026-11-10 的真实事故）：`cpp/docnums/main.cpp` 改名 `.eln` 后
    #: 只报 19 条错误，看起来"解析器基本能吃"。真相是 `namespace {` 处抛错后
    #: `recover_item` 从 `{` 配对括号**一路吞到文件末尾**——整个文件体从来没被
    #: 解析过。**"错误数少"不是"解析通过"**，恢复机制可以把错误数压成任何值。
    #: 所以只要一次跳过超过这个行数，就在诊断里留下可见的痕迹。
    SKIP_REPORT_LINES = 20

    def _report_skipped(self, start_tok: Token):
        """错误恢复结束后调用：跳过 > `SKIP_REPORT_LINES` 行就报出来。

        `start_tok` 是**恢复开始处**的 token（已经跳过的那段之前）。没跳过
        （或跳得很少）时什么也不做——恢复几行是正常错误恢复，不该变成噪声。
        """
        end = self.peek()
        n = end.line - start_tok.line
        if n <= self.SKIP_REPORT_LINES:
            return
        self.error(
            "E-Parse-SkippedBody",
            f"错误恢复跳过了 {n} 行（{start_tok.line} → {end.line}）："
            f"这一段**没有被解析**，里面的错误不会出现在本次诊断里。\n"
            f"不要根据「错误很少」判断「解析通过」——本次结果只保证"
            f"**没有被跳过的部分**。\n"
            f"改法：先修 {start_tok.line} 行附近的那条错误（它后面的代码才有机会"
            f"被真正检查），或把这一段删掉/移走。",
            start_tok)

    def parse_item(self) -> Optional[Node]:
        t = self.peek()
        k = t.kind

        if k == Kind.ERROR_DECL:
            return self.parse_error_decl()
        if k == Kind.USE:
            return self.parse_use()
        # **`using namespace std;`**（C++ 形状，R1-A）：`using` 不是关键字
        # （`use` 才是），所以按词拦——否则会先撞上「没有类型别名」那条。
        if k == Kind.IDENT and (t.text or "") == "using":
            return self.parse_using()
        # **第 2 条**（P0 兼容层）：`typedef X Y;` —— C 的老写法，落到别名表。
        # **2026-11-13**：`typedef struct … X;` 已经**不是**"只给诊断"了——
        # 它降级成 `struct X { … }`（见 `parse_typedef` / `parse_struct`），
        # 所以这条判据**不再看 `self.compat`**：三种 struct 写法在默认模式下也认。
        # 其余 typedef 形状仍由 `parse_typedef` 给**一条明确诊断**（不是裸 parse 错）。
        if k == Kind.IDENT and (t.text or "") == "typedef":
            return self.parse_typedef()
        # **第 8 条**（P0 兼容层）：**函数声明开头**的 `static`。
        #
        # C++ 里它表示「内部链接」（文件内可见）；本语言**一个文件就是一个
        # 模块**，函数本来就是文件内的，所以这条对语义**没有任何影响**——
        # 是纯拼写。这里只跳过这个 token：不产出节点、不影响 κ、不影响 AST。
        #
        # 判据用 `_compat_static_fn_ahead()`（后面必须是**带体的函数声明**）：
        # `static u64 counter = 0;` 这类**静态局部变量**仍由
        # `UNSUPPORTED_STMT_WORDS["static"]` 拒绝（那是另一件事，不放）。
        if self._compat_static_fn_ahead():
            self.advance()                       # 跳过 `static`，不产出节点
            return self.parse_item()
        # **`namespace X { … }`**（C++ 形状，2026-11-10）：接受并**展平**。
        # 本语言没有命名空间：内部 item 直接进模块全局作用域。重名不静默覆盖
        # （见 `_add_top_item`）。`using namespace std;` 走上一条分支。
        if k == Kind.IDENT and (t.text or "") == "namespace":
            return self.parse_namespace()
        if k == Kind.CONST:
            # **`const Json* dig(const Json& root, …) { … }` 是函数**，不是常量
            # 声明（2026-11-10，指针支持）。判据与 `parse_stmt` 的
            # `_const_ptr_decl_ahead` 同一套：试解出一个 `T*`，后面跟名字 + `(`。
            if self._const_ptr_fn_ahead():
                return self.parse_fn(is_pub=False)
            return self.parse_const()
        if k == Kind.ENUM:
            return self.parse_enum()
        if k == Kind.STRUCT:
            return self.parse_struct()
        # **第 15 条**（P0 兼容层）：`--compat` 下 `class` 与 `struct` **完全
        # 同义**——本语言没有访问控制，也没有"默认 private"可言，所以两者走
        # **同一条** `parse_struct` 路径（成员函数 / 构造函数的降级直接复用
        # 第 13 条）。判据宁窄勿宽：后面必须跟**类名**（`class X { … };` /
        # `class X;`）；`class` 单独出现（把 `class` 当变量名）仍按老路径。
        if (self.compat and k == Kind.IDENT and (t.text or "") == "class"
                and self.kind(1) == Kind.IDENT):
            return self.parse_struct(is_class=True)
        if k == Kind.TRAIT:
            return self.parse_trait()
        if k == Kind.IMPL:
            return self.parse_impl()
        if k == Kind.SPEC:
            return self.parse_spec()
        if k == Kind.IDENT and t.text == "kernel":
            raise ParseError(
                "E-Kernel-KeywordRemoved",
                "v0.66 起没有独立 kernel 声明：候选由 spec 里的 choose/over 声明，"
                "实现写在 fn 之后的 `} kernels L(...) { … }` 块里", t)
        if k == Kind.CHOOSE:
            raise ParseError(
                "E-Choose-NotInSpec",
                "choose 必须写在宿主 spec 块内（v0.66）", t)
        if k == Kind.PUB:
            if self.kind(1) == Kind.FN and self.kind(2) == Kind.IDENT:
                # `pub func f(…) -> T`：关键字已下线，但**继续解析**（AST 与
                # C++ 形同形），避免下游连锁出无关诊断。
                self.error("E-Func-KeywordRemoved", FUNC_REMOVED_HINT, self.peek(1))
            elif (self.kind(1) == Kind.IDENT
                  and (self.peek(1).text or "") == "fn"
                  and self.kind(2) == Kind.IDENT):
                self.error(REMOVED_KEYWORDS["fn"],
                           f"`fn` 已下线：{RETIRED_HINT['fn']}", self.peek(1))
            return self.parse_fn(is_pub=True)
        if k == Kind.FN and self.kind(1) == Kind.IDENT:
            # **`func f(…) -> T` 形已下线**（P4-0）：只留 `T f(…)`。
            # 与 `fn` 同一个做法——报一条 error，然后照常解析出 AST。
            self.error("E-Func-KeywordRemoved", FUNC_REMOVED_HINT, t)
            return self.parse_fn(is_pub=False)
        # **退役关键字（v0.68）**：词法器只出 `IDENT`（2026-11-10），
        # 位置判据在这里——顶层 `fn` / `val` / `var` / `let` **后跟标识符**
        # 才是旧声明形状；`end` 在顶层只可能是旧块终结符。判据表见
        # `RETIRED_HINT` 顶部注释。
        if k == Kind.IDENT:
            # **旧函数声明 `fn name(…)`**：继续解析（AST 与 `func` 完全同形），
            # 只报关键字已下线。**不能吞掉整个函数**——下游（choose 宿主、
            # kernels 归属、成本）还要看到它；吞掉会连锁出无关诊断。
            if (t.text or "") == "fn" and self.kind(1) == Kind.IDENT:
                self.error(REMOVED_KEYWORDS["fn"],
                           f"`fn` 已下线：{RETIRED_HINT['fn']}", t)
                return self.parse_fn(is_pub=False)
            _retired = self._retired_top_message(t)
            if _retired is not None:
                raise ParseError(_retired[0], _retired[1], t)
        # **C++ 访问说明符**（2026-11-10）：`public:` / `private:` / `protected:`
        # 在 `struct` 体里接受并**忽略**（`parse_struct` 处理）。顶层只剩
        # `public func f()` 这种"长写法"——给「应写 `pub`」；`private` /
        # `protected` 没有缩写形式，也没有访问控制。
        if k == Kind.IDENT and (t.text or "") in ("public", "private", "protected"):
            if (t.text or "") == "public":
                raise ParseError(
                    "E-Keyword-LongForm",
                    "关键字 MUST 用缩写：`public` 应写作 `pub`。\n"
                    "（`public:` 作为 C++ 的**成员访问说明符**写在 `struct` 体里"
                    "是接受的，且被忽略——本语言没有访问控制，字段本来就全部公开。）",
                    t)
            raise ParseError(
                "E-Keyword-Unsupported",
                f"本语言没有访问控制：`{t.text}` 只在 `struct` 体的 "
                f"`{t.text}:` 形状里被接受（且被忽略——字段本来就全部公开）。\n"
                f"改法：删掉 `{t.text}:`；内部约定用命名（`_x`）表达。", t)
        # **C++ 形状的函数声明**（R1-A，§8.2）：`u64 f(const u64& x) { … }` /
        # `auto f(const u64& x) -> u64 { … }`。`func` 不再必需。
        #
        # 判据是**形状**（`类型 名字 ( … ) [-> 类型] {`），不是白名单：
        # `Point make_point() { … }` / `void fill(vector<u64>& xs) { … }` 都算。
        # 放在 `_unsupported_stmt_message` 之前——否则 `auto f() -> u64 {}` 会先
        # 撞上「本语言没有 `auto`」那条（`auto` 在这里合法，只在**没有后置返回**
        # 时才是错的，由 `parse_fn` 报）。
        # **P4-0**：`func` 下线后，返回类型可以是**元组**——`(i64, u64)!
        # read_number(…) {` 的第一个 token 是 `(`，不是 IDENT；不在
        # 这里也试一次形状判据，整条声明会落到「这里不该出现 '('」。
        if k in (Kind.IDENT, Kind.LPAREN) and self._at_cxx_fn_decl():
            return self.parse_fn(is_pub=False)
        if k == Kind.REWRITE:
            return self.parse_rewrite()
        if k == Kind.EXTERNAL:
            return self.parse_external_block()
        if k == Kind.EXTERN:
            return self.parse_extern()
        if k == Kind.ASSUME:
            return self.parse_assume()
        if k == Kind.IDENT and (t.text or "") == "link":
            # **模块级链接**：`link "ucrtbase";` —— 本模块的外部符号来自这个库。
            # 配合"spec 找不到函数体 = 外部声明"，就不再需要 `external "lib"` 子句了。
            self.advance()
            lib = self.expect(Kind.STRING, "库名（字符串）").text
            if len(lib) >= 2 and lib[0] == lib[-1] and lib[0] in "\"'":
                lib = lib[1:-1]
            node = Node("link", t.line, t.col, name=lib)
            self.expect(Kind.SEMI, "';'")
            return node

        # **顶层同样适用**：`typedef` / `using` / `class` 写在 `.tu` 顶层时，
        # 以前只报一句「这里不该出现 'ident'」。形状判据与语句共用。
        if k == Kind.IDENT:
            _msg = self._unsupported_stmt_message(t)
            if _msg:
                raise ParseError("E-Keyword-Unsupported", _msg, t)
            # **顶层可变变量**（如 `u64 counter = 0;`）：以前只报
            # 「这里不该出现 'ident'」。`.tu` 顶层只放声明，没有全局可变状态。
            if self.kind(1) == Kind.IDENT and self.kind(2) in (Kind.EQ, Kind.SEMI):
                raise ParseError(
                    "E-Decl-ModuleVar",
                    "`.tu` 顶层只放声明（`const` / `struct` / `enum` / `spec` / "
                    "`func` / `extern`），没有可变全局变量。\n"
                    "改法：变量放进函数体；常量写 `const N: u64 = 10;`（顶层）；"
                    "要跨函数共享就作为形参 `T&` 传进去。", t)

        raise ParseError("E-Parse-UnexpectedTop",
                         f"这里不该出现 {k.value!r}", t)

    def _effect_name(self) -> str:
        """效果名：普通标识符，或本身是关键字的那种（目前只有 `unsafe`）。
    
        `unsafe` 在词法层是关键字（`Kind.UNSAFE`），所以 `effects { unsafe }`
        以前直接报「期望效果名」——效果写了却没法被声明，等于不可用。
        """
        if self.at(Kind.UNSAFE):
            return self.advance().text
        return self.expect_ident("效果名").text
    
    def parse_external_block(self) -> Node:
        """`external { read_file: O(1); … }` —— **给外部项登记成本**（v0.71）。

        没有登记时，成本里只有 `ext(read_file)`（"这一项不知道，其余照算"）；
        登记之后这一项被**替换成具体值**，于是整条上界变成可判定的。
        目前只接受三种写法：`O(1)`、`O(n)`、以及裸整数。
        """
        t = self.expect(Kind.EXTERNAL, "'external'")
        node = Node("external", t.line, t.col)
        self.expect(Kind.LBRACE, "'{'")
        while not self.at(Kind.RBRACE) and not self.at(Kind.EOF):
            name_tok = self.expect_ident("外部函数名")
            self.expect(Kind.COLON, "':'")
            entry = Node("ext-entry", name_tok.line, name_tok.col, name=name_tok.text)
            if self.at(Kind.IDENT) and (self.peek().text or "").upper() == "O":
                self.advance()                      # `O`
                self.expect(Kind.LPAREN, "'('")
                if self.at(Kind.INT):
                    entry.value = ("const", int(self.advance().text))
                elif self.at(Kind.IDENT):
                    entry.value = ("var", self.advance().text)
                else:
                    raise ParseError("E-External-BadBound",
                                     "external 的成本只支持 O(1) / O(n)", self.peek())
                self.expect(Kind.RPAREN, "')'")
            elif self.at(Kind.INT):
                entry.value = ("const", int(self.advance().text))
            else:
                raise ParseError("E-External-BadBound",
                                 "external 的成本只支持 O(1) / O(n) / 整数", self.peek())
            self.expect(Kind.SEMI, "';'")
            node.add(entry)
        self.expect(Kind.RBRACE, "'}'")
        return node

    def parse_use(self) -> Node:
        t = self.expect(Kind.USE, "'use'")
        return self._parse_using_tail(t)

    def parse_using(self) -> Node:
        """`using namespace std;`（C++ 形状，R1-A）。

        **注意 `using` 不是关键字**：词法表里只有 `use`（本语言的模块导入）。
        所以 `using` 是 `Kind.IDENT`，以前一路走到 `UNSUPPORTED_STMT_WORDS`
        报「本语言没有 `using`：没有类型别名」——一条与「写了个命名空间指令」
        毫不相干的诊断。这里在 `parse_item` / `parse_stmt` 里先按**词**拦。
        """
        t = self.advance()                       # IDENT `using`
        return self._parse_using_tail(t)

    def _parse_using_tail(self, t: Token) -> Node:
        # **`using namespace std;`**（§8.10）：接受。真实 C++ 文件的第一屏
        # 几乎总有这一行；`#include` 已经在词法层被忽略，这一行也必须吃得下。
        # 语义：**写了它才允许短名** `vector`（开关见 `REQUIRE_USING_FOR_SHORT_NAMES`）。
        if self.at(Kind.IDENT) and (self.peek().text or "") == "namespace":
            nt = self.advance()
            ns_tok = self.expect_ident("命名空间名（本语言只支持 `std`）")
            self.expect(Kind.SEMI, "';'")
            node = Node("using-namespace", nt.line, nt.col, name=ns_tok.text)
            if ns_tok.text == "std":
                self.using_std = True
            else:
                # 不产生级联错误：这一行已经「接受」，只是它指的东西不存在。
                self.error(
                    "E-Use-UnknownNamespace",
                    f"本语言没有命名空间：`using namespace {ns_tok.text};` 只认 `std`。\n"
                    f"改法：去掉这一行；一个文件一个全局作用域（名字加前缀），"
                    f"跨文件的契约写在 `.tu` 的 `spec` 里。", nt)
            return node
        # **第 2 条**（P0 兼容层）：`using X = Y;` → 别名表，**不进 AST**。
        # 判据放在 `parse_path` 之前：`X` 是**单个标识符**，后面紧跟 `=`。
        # （`using namespace std;` 已在上面分走；`using std::vector;` 走下面
        #  那条 `E-Use-StdSingleName`，`std` 后面是 `::` 不是 `=`。）
        if (self.compat and self.at(Kind.IDENT)
                and self.kind(1) == Kind.EQ):
            return self._compat_parse_alias_using()
        path = self.parse_path("use 路径")
        # `using std::vector;`（单个名字的 using）与 `using T = ...;`（类型别名）
        # 都不存在；给带改法的诊断，而不是「期望 ';'」。
        if path.startswith("std."):
            raise ParseError(
                "E-Use-StdSingleName",
                f"`using {path.replace('.', '::')};` 暂不支持（单名 using 只影响一个名字，"
                f"R1 没做）。\n改法：整行写 `using namespace std;`，"
                f"或类型写全 `std::vector<u64>`。", t)
        if self.at(Kind.EQ):
            raise ParseError(
                "E-Keyword-Unsupported",
                "本语言没有类型别名（`using T = …;` / `typedef`）。\n"
                "改法：类型写全；泛型直接写在函数名后：`u64 pick<T>(T a) { … }`。", t)
        self.expect(Kind.SEMI, "';'")
        return Node("use", t.line, t.col, name=path)

    def parse_error_decl(self) -> Node:
        """模块级错误类型声明。

        `error CalcError;` —— **开放**：任何变体都接受（老行为）。
        `error CalcError { BadToken, Empty };` —— **封闭**：变体可校验。

        **E3（2026-11-05）**：EBNF 里一直写着变体列表，但解析器只吃 `error X;`，
        文档与实现不一致，于是变体从不被校验。现在两种都收。
        """
        t = self.expect(Kind.ERROR_DECL, "'error'")
        name = self.expect_ident("错误类型名").text
        node = Node("error-decl", t.line, t.col, name=name)
        # 错误类型名可以出现在类型位置（`T!` 的返回侧 / `error E;` 之后）
        self.known_types.add(name)
        if self.accept(Kind.LBRACE):
            while not self.at(Kind.RBRACE) and not self.at(Kind.EOF):
                node.add(self.parse_variant())
                if not self.accept(Kind.COMMA):
                    break
            self.expect(Kind.RBRACE, "'}'")
        self.expect(Kind.SEMI, "';'")
        return node

    # ---------------------------------------------------------- namespace

    def parse_namespace(self) -> Node:
        """`namespace X { item* }` —— **接受并展平**（C++ 形状，2026-11-10）。

        ## 语义

        本语言**没有命名空间**：内部 item 直接进模块的全局作用域
        （`parse_module` 调 `_add_top_item` 展平）。所以

            namespace foo { u64 add(u64 a, u64 b) { return a + b; } }

        之后，`add` 在全局可见——**`foo::add` 这种限定名不行**（名字被展平后
        没有 `foo` 这一层；`foo::add` 会报 `E-Name-Undefined`）。这与文档
        「一个文件一个全局作用域」一致：展平 = 当作没写 `namespace`。

        ## 重名

        展平**不覆盖**：同名 item 已在模块里时由 `_add_top_item` 报
        `E-Name-Duplicate`（带两处位置与改法），并把后来者丢掉。

        ## 匿名 namespace

        `namespace { … }`（C++ 的"内部链接"写法）直接接受——反正是展平，
        名字本来就进全局。
        """
        t = self.advance()                       # IDENT `namespace`
        name = None
        if self.at(Kind.IDENT):
            name = self.advance().text
        node = Node("namespace", t.line, t.col, name=name)
        node.value = {"flatten": True}
        self.expect(Kind.LBRACE, "'{'")
        while not self.at(Kind.RBRACE) and not self.at(Kind.EOF):
            if self.at(Kind.SEMI):
                self.advance()
                continue
            mark = self.i
            try:
                node.add(self.parse_item())
            except ParseError as e:
                # **在 namespace 内部恢复**：不能让它冒泡到 `parse_module`——
                # 那样整个 namespace（以及已经解析好的兄弟 item）会被丢掉，
                # 而 `recover_item` 还会把命名空间的收尾 `}` 当成 item 收尾。
                self.error(e.code, e.message, e.tok)
                if self.i == mark:
                    self.advance()
                skip_from = self.peek()
                self.recover_item(stop_before_rbrace=True)
                self._report_skipped(skip_from)
        self.expect(Kind.RBRACE, "'}'")
        # C++ 允许 `namespace X { … };`（多余分号）；两种都吃。
        self.accept(Kind.SEMI)
        return node

    def _add_top_item(self, mod: Node, item: Node, from_ns: bool = False):
        """把一个顶层 item 放进模块；`namespace` 递归展平。

        **重名不静默覆盖，但只在"展平"这一步查**（`from_ns=True`）：

        * 普通顶层 item 照旧进模块——两个同名 `func` 是 checker 的
          `E-Duplicate-Fn` 的事，且文档里有"三种写法写同一个函数"的对照示例，
          parser 不能把它判死；
        * `namespace` 里的名字被**搬**进全局作用域，这一步会**遮掉**已有名字
          （checker 只查 `fn` / `spec`，`struct` / `enum` / `const` 会悄悄少
          一个），所以由这里报 `E-Name-Duplicate` 并丢掉后来者。
        """
        if item.kind == "namespace":
            for child in list(item.children):
                self._add_top_item(mod, child, from_ns=True)
            return
        if from_ns and item.kind in NAMED_TOP_ITEMS and item.name:
            for prev in mod.children:
                if prev.kind == item.kind and prev.name == item.name:
                    self.error(
                        "E-Name-Duplicate",
                        f"`namespace` 展平后名字冲突：{item.kind} `{item.name}` "
                        f"已经在 {prev.line}:{prev.col} 定义过。\n"
                        f"本语言没有命名空间——`namespace X {{ … }}` 里的名字"
                        f"**直接进全局作用域**，不会用命名空间区分；重名不能静默"
                        f"覆盖。\n改法：改名（如 `{item.name}_x`），或把其中一份"
                        f"移到 `.tu` 的 `spec` / 换一个名字。",
                        Token(Kind.IDENT, item.name, item.line, item.col, 0, 0))
                    return
        mod.add(item)

    #: 退役关键字在**顶层 item 起点**的判据（见 `RETIRED_HINT`）。
    def _retired_top_message(self, t: Token):
        """顶层 item 起点的退役关键字 → (码, 消息)；不是 → None。

        * `end`：顶层唯一可能的含义就是旧块终结符（模块级没有变量/表达式）；
        * `val` / `var` / `let` / `fn`：**后跟标识符**才是旧声明形状。
          `fn(x)` 是普通调用，`fn` 作类型名时后面跟的是变量名——两者都不拦。
        """
        word = t.text or ""
        if word == "end":
            return (REMOVED_KEYWORDS["end"],
                    f"`end` 已下线：{RETIRED_HINT['end']}")
        if word in OLD_DECL_WORDS and self.kind(1) == Kind.IDENT:
            return (REMOVED_KEYWORDS[word],
                    f"`{word}` 已下线：{RETIRED_HINT[word]}")
        return None

    def parse_const(self) -> Node:
        """模块级常量。

        v0.68 起推荐 `const MAX: u64 = 10;`（名字前置，与 .tu 里的类型标注一致）；
        函数体内则是 `const u64 n = xs.length();`（类型前置）。两者靠 `:` 区分。
        """
        t = self.expect(Kind.CONST, "'const'")
        if self.at(Kind.IDENT) and self.kind(1) == Kind.EQ:
            raise ParseError(
                "E-Decl-ConstNoType",
                "本语言**不做类型推断**：`const idx = 5;` 缺类型。\n"
                "改法：写 `const u64 idx = 5;`（类型在名字前）。\n"
                "理由：显式类型优于类型推断——契约与成本分析都依赖静态类型，"
                "不引入推断。", self.peek())
        if self.at(Kind.IDENT) and self.peek().text == "auto":
            _msg = self._unsupported_stmt_message(self.peek())
            if _msg:
                raise ParseError("E-Keyword-Unsupported", _msg, self.peek())
        if self.kind() == Kind.IDENT and self.kind(1) == Kind.COLON:
            name = self.advance().text
            self.advance()                       # ':'
            typ = self.parse_type()
        else:
            typ = self.parse_type()
            name = self.expect_ident("常量名").text
        self.expect(Kind.EQ, "'='")
        val = self.parse_expr()
        self.expect(Kind.SEMI, "';'")
        # 模块级常量也是"值名"：语句起点 `N * x;` 里 `N` 是常量 → 乘法。
        self.value_names.add(name)
        return Node("const", t.line, t.col, name=name).add(typ, val)

    def parse_extern(self) -> Node:
        t = self.expect(Kind.EXTERN, "'extern'")
        self.accept(Kind.FN)
        name = self.expect_ident("外部符号名").text
        node = Node("extern", t.line, t.col, name=name)
        if self.accept(Kind.LPAREN):
            node.add(self.parse_params_until(Kind.RPAREN))
            self.expect(Kind.RPAREN, "')'")
        if self.accept(Kind.ARROW):
            node.add(self.parse_type())
        # **来源库**：`extern strlen(...) -> u64 from "ucrtbase";`
        # 不写 → 用进程自身符号（POSIX 下 `CDLL(None)`）。
        if self.at(Kind.IDENT) and (self.peek().text or "") == "from":
            self.advance()
            lib = self.expect(Kind.STRING, "库名（字符串）").text
            # 去掉字面量两边的引号（token.text 保留原样）
            if len(lib) >= 2 and lib[0] == lib[-1] and lib[0] in "\"'":
                lib = lib[1:-1]
            node.value = {"lib": lib}
        # **效果**：FFI 调用必须有副作用声明（默认无 = 纯函数，但必须显式）
        if self.at(Kind.IDENT) and (self.peek().text or "") == "effects":
            self.advance()
            eff = Node("extern-effects", t.line, t.col)
            self.expect(Kind.LBRACE, "'{'")
            while not self.at(Kind.RBRACE) and not self.at(Kind.EOF):
                eff.add(Node("effect-name", t.line, t.col,
                             name=self._effect_name()))
                if not self.accept(Kind.COMMA):
                    break
            self.expect(Kind.RBRACE, "'}'")
            node.add(eff)
        self.expect(Kind.SEMI, "';'")
        return node

    def parse_assume(self) -> Node:
        """**`assume` 已移除（2026-11-04）**：这里只负责给出迁移提示。

        为什么移除：它允许作者单方面声明一个**不可验证**的上界，而 κ 的全部
        卖点是"上界"。`assume` 是整门语言里唯一没有"谁能验证它"答案的机制。
        实测：15 个算法样例、21 个函数**一个都没用过它**。

        替代写法：

        | 原来想写 | 现在写 |
        |---|---|
        | `assume n <= xs.length();` | **直接写 `xs.length()`**（本来就不需要假设） |
        | 外部给的量（`argc`、文件行数） | 在 `benchmodel { data {…} }` 里声明成**输入**并给 `scale` |
        | 真·资源预算（`while (budget > 0)`） | `external { … }` / `ext()` 登记 |

        **保留词法与这条报错是故意的**：直接删掉会让老代码变成无意义语法错误，
        而迁移提示告诉用户该改成什么。
        """
        t = self.expect(Kind.ASSUME, "'assume'")
        raise ParseError(
            "E-Assume-Removed",
            "`assume` 已移除：它允许声明一个无法验证的上界，而 κ 的卖点正是可验证。"
            "改法：① 直接写真实尺寸（如 `while (i < xs.length())`）；"
            "② 外部给的量写进 `benchmodel { data { … } }` 并给 `scale`；"
            "③ 资源预算用 `external { … }` 登记",
            t)

    def parse_rewrite(self) -> Node:
        t = self.expect(Kind.REWRITE, "'rewrite'")
        name = self.expect_ident("重写规则名").text
        node = Node("rewrite", t.line, t.col, name=name)
        if self.accept(Kind.LBRACE):
            while not self.at(Kind.RBRACE) and not self.at(Kind.EOF):
                self._add_clauses(node, self.parse_clause())
            self.expect(Kind.RBRACE, "'}'")
            self.expect_end("rewrite", required=False)
        else:
            self.expect(Kind.SEMI, "';'")
        return node

    # ---------------------------------------------------------- enum / struct / trait / impl

    def parse_enum(self) -> Node:
        t = self.expect(Kind.ENUM, "'enum'")
        # **`enum class`**（2026-11-10）：`class` 会被当成枚举名吃掉，所以
        # 必须在 `expect_ident` **之前**判。以前报「期望 '{'，实际是 'ident'」。
        if self.kind() == Kind.IDENT and self.peek().text == "class":
            raise ParseError(
                "E-Enum-NoClass",
                "本语言没有 `enum class`：`enum` 本身就有作用域（变体用 "
                "`Color::Red` 访问），也不会隐式转成整数。\n"
                "改法：写 `enum Color { Red, Green }`，用 `Color::Red` 取值；"
                "需要数值就写映射函数 `u64 code(Color c) { … }`。",
                self.peek())
        name = self.expect_ident("枚举名").text
        node = Node("enum", t.line, t.col, name=name)
        # **先登记再解析体**：`enum E { A }` 之后（以及体内的载荷类型）都能用；
        # 更要紧的是**本声明之前**用不到它——前向引用与 C++ 一样不成立。
        self.known_types.add(name)
        # **`enum E : u8`**（2026-11-10）：以前报「期望 '{'，实际是 ':'」。
        if self.at(Kind.COLON):
            raise ParseError(
                "E-Enum-NoUnderlying",
                "本语言没有 `enum E : u8`：枚举不声明底层类型，也不能隐式当整数。\n"
                "改法：直接写 `enum E { A, B }`；需要固定数值就写映射函数："
                "`u64 code(E e) { match (e) { E::A => { return 5; } "
                "E::B => { return 9; } _ => { return 0; } } }`。",
                self.peek())
        self.expect(Kind.LBRACE, "'{'")
        while not self.at(Kind.RBRACE) and not self.at(Kind.EOF):
            node.add(self.parse_variant())
            if not self.accept(Kind.COMMA):
                break
        self.expect(Kind.RBRACE, "'}'")
        self.expect_end("enum")
        return node

    def parse_variant(self) -> Node:
        t = self.expect_ident("变体名")
        v = Node("variant", t.line, t.col, name=t.text)
        # **显式枚举值 `A = 5`**（2026-11-10）：以前在 `=` 处报「期望 '}'」。
        if self.at(Kind.EQ):
            raise ParseError(
                "E-Enum-ExplicitValue",
                "本语言没有显式枚举值：`A = 5` 不存在，变体按声明顺序取 "
                "0..N-1。\n改法：删掉 `= 5`；需要固定数值就写映射函数 "
                "`u64 code(E e) { match (e) { E::A => { return 5; } "
                "… } }`。", self.peek())
        if self.accept(Kind.LPAREN):
            while not self.at(Kind.RPAREN) and not self.at(Kind.EOF):
                if self.kind() == Kind.IDENT and self.kind(1) == Kind.COLON:
                    ft = self.advance()
                    self.advance()
                    ft_node = Node("field", ft.line, ft.col, name=ft.text)
                    ft_node.add(self.parse_type())
                    v.add(ft_node)
                else:
                    v.add(self.parse_type())
                if not self.accept(Kind.COMMA):
                    break
            self.expect(Kind.RPAREN, "')'")
        return v

    def parse_struct(self, is_class: bool = False,
                     typedef_name: Optional[str] = None) -> Node:
        """`struct X { … }` /（`--compat`）`class X { … }`。

        `is_class=True` 时**只差一个关键字**：`class` 与 `struct` 完全同义
        （本语言没有访问控制），后面的字段 / 成员函数 / 构造函数解析、降级、
        诊断**全部是同一份代码**。`compat=False` 时 `is_class` 恒为 False。

        `typedef_name`（2026-11-13）：调用方是 `typedef struct … X;`，名字在
        **体后面**，所以名字由调用方从 `_parse_typedef_struct` 传来。**三种拼写
        都取 typedef 名**（`X`）作为结构体名——这正是"等价于 `struct X { … }`"：

        - `typedef struct { … } X;` → `struct X { … }`
        - `typedef struct Tag { … } X;` → `struct X { … }`（`Tag` 只是 C 的标签拼写）
        - `typedef struct X { … } X;` → `struct X { … }`

        为什么丢掉 `Tag`：本语言**没有标签命名空间**（`struct Tag` 不是一个可以
        单独使用的类型名）。C 里 `Tag` 只在 `struct Tag` 这种写法里出现，而那
        正是本语言没有的东西；保留它反而会造出 `Tag v;` 这种 C 里不合法的用法。
        所以"名字"只有一个：typedef 名。**这条规则写进文档，不靠猜。**
        """
        t = self.advance() if is_class else self.expect(Kind.STRUCT, "'struct'")
        if typedef_name is None:
            name = self.expect_ident("结构体名").text
        else:
            # `typedef struct [Tag] { … } X;`：`Tag` 只消费、不登记。
            if self.at(Kind.IDENT):
                self.advance()                   # `Tag`（标签拼写，丢弃）
            name = typedef_name                  # 结构体名 = typedef 名
        node = Node("struct", t.line, t.col, name=name)
        #: 第 16 条：本 struct 的**字段默认初始值**（按声明顺序）。
        #: `(字段名, 行, 列, 默认值表达式)`；只在 `--compat` 下非空。
        _defaults: List[Tuple[str, int, int, Node]] = []
        # **先登记再解析体**：`struct Node { next: Node*, }` 里的自引用是 C++ 的
        # 常态（不完全类型）；而**在声明之前**用 `Node * q` 仍然看不见——
        # 登记发生在这里，前向引用依旧解析不了（与 C++ 一致）。
        self.known_types.add(name)
        # **`struct D : B`（继承）**（2026-11-10）：以前报「期望 ';'，实际是 ':'」。
        if self.at(Kind.COLON):
            raise ParseError(
                "E-Struct-NoInheritance",
                "本语言没有继承：`struct D : B` 不存在。\n"
                "改法：两个 `struct` 平级声明，共同字段直接写两遍；"
                "「多种形状、按类型分派」用带载荷的 `enum` + `match`。",
                self.peek())
        # **类模板 `struct Box<T>`**（2026-11-10）：以前报「期望 ';'，实际是 '<'」。
        if self.at(Kind.LT):
            raise ParseError(
                "E-Struct-NoGeneric",
                "本语言没有类模板：`struct Box<T>` 不存在。泛型只支持函数："
                "`func pick<T>(T a, T b) -> T`。\n"
                "改法：结构体写具体类型；要装任意元素用容器字段"
                "（`vector<T>` / `map<K,V>` 自带泛型）。",
                self.peek())
        #: 第 13 条：struct 体里降级出来的**兄弟自由函数**（成员函数 / 构造函数）。
        #: 解析完 struct 体后与 `struct` 一起装进一个**展平的 `namespace` 节点**
        #: （`_add_top_item` 认得它），于是它们与手写的顶层函数**完全同形**。
        _member_fns: List[Node] = []
        if self.accept(Kind.LBRACE):
            # **第 13 条**：先扫一遍字段名——C++ 允许成员函数引用**后面**声明的
            # 字段，单遍解析到这里时字段表还是空的（见 `_compat_prescan_fields`）。
            if self.compat:
                _pre, _mnames = self._compat_prescan_fields()
                self.struct_decls[node.name] = {
                    "fields": [nm for _ty, nm in _pre],
                    "types": {nm: ty for ty, nm in _pre},
                    "method_names": set(_mnames),
                    "methods": {},
                    "ctors": [],
                }
            while not self.at(Kind.RBRACE) and not self.at(Kind.EOF):
                # **嵌套类型**（`struct A { struct B { … } }`）也不存在。
                # 用 `error` + 跳过（不是 raise）：否则错误恢复会从嵌套声明
                # 中间重新同步，把外层字段 `a: u64,` 再报成「顶层 ident」。
                # **第 15 条**：`class B { … }` 与 `struct B { … }` 同义，
                # 嵌套 class 同样按"没有嵌套类型"拒绝（同一句诊断）。
                if (self.at(Kind.STRUCT) or self.at(Kind.ENUM)
                        or (self.compat and self.at(Kind.IDENT)
                            and (self.peek().text or "") == "class"
                            and self.kind(1) == Kind.IDENT)):
                    bad = self.peek()
                    self.error(
                        "E-Struct-NoNested",
                        "本语言没有嵌套类型：`struct A { struct B { … } }` 不存在。\n"
                        "改法：两个 `struct` 平级声明，外层字段直接写 `b: B,`。",
                        bad)
                    self.advance()                  # struct / enum
                    depth = 0
                    while not self.at(Kind.EOF):
                        kk = self.peek().kind
                        if kk == Kind.LBRACE:
                            depth += 1
                        elif kk == Kind.RBRACE:
                            depth -= 1
                            if depth == 0:
                                self.advance()
                                break
                        elif kk == Kind.SEMI and depth == 0:
                            self.advance()
                            break
                        self.advance()
                    self.accept(Kind.COMMA)
                    continue
                # **C++ 访问说明符**（2026-11-10）：`public:` / `private:` /
                # `protected:` **接受并忽略**——本语言没有访问控制，struct 字段
                # 本来就全部公开。不能静默改变可见性，所以文档里明写"没有访问
                # 控制"（`文档/语法清单.md` §5）。`public` 在词法层不再是长写法
                # 错误（见 lexer.LONG_FORM_HINT 的注释）。
                if (self.at(Kind.IDENT)
                        and (self.peek().text or "") in ("public", "private", "protected")
                        and self.kind(1) == Kind.COLON):
                    self.advance()                  # 说明符
                    self.advance()                  # ':'
                    continue
                # **第 16 条**（P0 兼容层）：C++ 的**字段默认初始值**
                # `double value = 0;`。它必须在成员判据之前拦下：否则会被
                # `_compat_member_reject` 当成"不支持的成员"（那条诊断只适用于
                # `compat=False`；`--compat` 下它已经能降级成构造函数里的赋值）。
                # 只认 `类型 名字 = 表达式` 这一种形状（`=` 后面直接跟表达式）。
                if self.compat and self._compat_default_member_ahead():
                    _f, _expr = self._compat_parse_default_member()
                    _defaults.append((_f.name, _f.line, _f.col, _expr))
                    node.add(_f)
                    if not (self.accept(Kind.SEMI) or self.accept(Kind.COMMA)):
                        break
                    continue
                # **C++ 成员函数**（`const Json* get(…) const { … }`）：
                # 本语言没有成员函数，但**必须只有一条**说清这一点的诊断。
                # 以前 `const T*` 这种返回类型会先撞「期望 字段名」（`const`
                # 后面不是字段名），再在尾随 `const` 与方法体上各撞一条——
                # 三条规定同一个缺口，读者还以为自己写错了三处。
                # 判据是形状（试解类型 + 名字 + `(`），不是类型白名单。
                #
                # **第 13 条**（P0 兼容层，本轮）：`--compat` 下成员函数与
                # 构造函数**不再拒绝**，而是降级成**兄弟自由函数**（纯 desugar）。
                # 只认能落的形状；落不了的（虚函数 / 静态成员 / 运算符重载 /
                # 析构 / 纯声明 / `= default` …）各给一条带改法的诊断。
                if self.compat:
                    # **`const T& f() const`**（2026-11-12）：返回类型的 `const` 限定是
                    # 纯拼写（C++ 用它禁止经返回值修改；本语言没有这一层），去掉即可。
                    # 只在这个位置、只在 compat 下丢，别的地方的 `const` 不动。
                    if self.at(Kind.CONST) and self.kind(1) == Kind.IDENT:
                        self.advance()
                    # **`explicit` 是纯拼写**（2026-11-12）：本语言没有隐式转换，
                    # 没有"可关"的东西——那条诊断自己都写着"删掉 explicit 即可"。
                    # 所以在 compat 下直接跳过这个词，让构造函数照常降级。
                    if (self.peek().text or "") == "explicit":
                        self.advance()
                    if self._compat_member_ahead(node.name):
                        _member_fns.append(
                            self._compat_parse_member(
                                node.name,
                                frozenset(self.struct_decls.get(node.name, {})
                                          .get("fields") or ())))
                        continue
                    if self._compat_member_reject(node.name):
                        continue
                _mend = self._probe_type_end()
                if (_mend is not None and self.kind_at(_mend) == Kind.IDENT
                        and self.kind_at(_mend + 1) == Kind.LPAREN):
                    bad = self.peek()
                    self.error(
                        "E-Struct-BodyForm",
                        "struct 体里只能写字段 `name: 类型,`：本语言没有成员函数"
                        "（也没有 `this`）。\n"
                        "改法：字段写 `x: u64,`（也接受 C++ 的 `u64 x;`）；"
                        "成员函数写成自由函数 "
                        "`u64 sum1(Point p) { return p.x + p.y; }`。",
                        bad)
                    self._skip_member_function()
                    continue
                # **C++ 字段写法**（2026-11-10）：`u64 x;` / `std::string s;` →
                # 同一个 `field` 节点（`x: u64`）。判据是「试解类型 + 标识符 +
                # (`;` | `,` | `}`)」——与真正的解析器同一份代码，不会误吃
                # `x: u64,`（类型后面是 `:`，不是标识符）。
                _te = self._probe_type_end()
                if (_te is not None and self.kind_at(_te) == Kind.IDENT
                        and self.kind_at(_te + 1) in (Kind.SEMI, Kind.COMMA,
                                                      Kind.RBRACE)):
                    typ = self.parse_type()
                    ft = self.expect_ident("字段名")
                    f = Node("field", ft.line, ft.col, name=ft.text).add(typ)
                    node.add(f)
                    if not (self.accept(Kind.SEMI) or self.accept(Kind.COMMA)):
                        break
                    continue
                # **C++ 的成员函数**：字段必须是 `name: 类型`。
                if self.at(Kind.IDENT) and self.kind(1) == Kind.IDENT:
                    raise ParseError(
                        "E-Struct-BodyForm",
                        "struct 体里只能写字段 `name: 类型,`：本语言没有成员函数"
                        "（也没有 `this`）。\n"
                        "改法：字段写 `x: u64,`（也接受 C++ 的 `u64 x;`）；"
                        "成员函数写成自由函数 "
                        "`u64 sum1(Point p) { return p.x + p.y; }`。",
                        self.peek())
                ft = self.expect_ident("字段名")
                self.expect(Kind.COLON, "':'")
                f = Node("field", ft.line, ft.col, name=ft.text).add(self.parse_type())
                self._reject_ptr_or_variadic()
                # **成员默认值 `x: u64 = 5`**（2026-11-10）：以前报「期望 '}'」。
                if self.at(Kind.EQ):
                    if self.compat:
                        # **第 16 条**：本语言自己的字段写法带默认值，与 C++ 的
                        # `u64 x = 5;` 走**同一套**降级（记录到 `_defaults`）。
                        self.advance()              # '='
                        _expr = self.parse_expr()
                        _defaults.append((ft.text, ft.line, ft.col, _expr))
                        node.add(f)
                        if not (self.accept(Kind.COMMA) or self.accept(Kind.SEMI)):
                            break
                        continue
                    raise ParseError(
                        "E-Struct-DefaultMember",
                        "struct 字段没有默认初始值：`x: u64 = 5` 不存在。\n"
                        "改法：在构造处给值 `Point { x: 5, y: 0 }`，或用工厂函数 "
                        "`Point make_point() { return Point { x: 0, y: 0 }; }`。",
                        self.peek())
                node.add(f)
                if not self.accept(Kind.COMMA):
                    break
            self.expect(Kind.RBRACE, "'}'")
            self.expect_end("struct")
            # **`typedef struct … X;`（2026-11-13）**：名字在**体后面**。体已经
            # 解析完，这里吃掉声明符 `X ;`（`_parse_typedef_struct` 已经校验过
            # 形状，所以这里只有一种可能）。**不登记别名**：结构体本来就叫 `X`
            # （见 `parse_struct` 的 docstring），`X v;` 用的是真名，所以
            # AST / κ / run 与手写 `struct X { … }` 逐字节相同。
            if typedef_name is not None:
                self.expect_ident("typedef 名")
                self.expect(Kind.SEMI, "';'")
            # **第 4 条**（P0 兼容层）：记下字段顺序，`Point{3, 4}` 才能变成
            # `Point { x: 3, y: 4 }`。只在字段全部解析成功后才记——
            # 有个坏字段时不登记，后续聚合初始化会走"字段顺序推不出"那条诊断，
            # 而不是拿着半截字段表去错位。
            self.struct_fields[node.name] = [
                f.name for f in node.children
                if getattr(f, "kind", "") == "field" and f.name]
            # **第 13 条**：用**真正解析出来的**字段表覆盖预扫结果（预扫只是
            # 为了让成员体看得见"后面才声明"的字段；字段的类型以这里为准）。
            _decl = self.struct_decls.get(node.name)
            if _decl is not None:
                _decl["fields"] = list(self.struct_fields[node.name])
                for _f in node.children:
                    if (getattr(_f, "kind", "") == "field" and _f.name
                            and _f.children):
                        _decl["types"][_f.name] = _f.children[0]
            # **第 16 条**：字段默认值 → 构造函数体开头的赋值（没有构造函数就
            # 合成一个零参构造函数）。必须在字段表 / 构造函数都解析完之后做：
            # 构造函数可能写在默认值字段**前面**，默认值也可能引用后面才声明的
            # 字段名（后者要报 `E-Compat-DefaultMemberRef`）。
            if self.compat and _defaults:
                self._compat_apply_default_members(
                    node, _decl, _defaults, _member_fns, t)
            # **第 13 条**：成员函数 / 构造函数已经降级成**兄弟自由函数**。
            # 用一个 `{"flatten": True}` 的 namespace 节点把它们与 struct 一起
            # 交出去——`_add_top_item` 会铺平，于是模块里看到的就是
            # `struct Ring` + `() Ring_push(…)` + …（与手写**完全同形**）。
            if _member_fns:
                wrap = Node("namespace", t.line, t.col)
                wrap.value = {"flatten": True}
                wrap.add(node)
                for _fn in _member_fns:
                    wrap.add(_fn)
                return wrap
        else:
            self.expect(Kind.SEMI, "';'")
        return node

    def _skip_member_function(self) -> None:
        """吃掉一个**已确认形状**的 C++ 成员函数（只为错误恢复）。

        调用方已经用 `_probe_type_end` 确认了 `类型 名字 (`；这里只做括号配对，
        把参数表、尾随 `const` / `noexcept`、以及 `{ … }` 或 `;` 一起吞掉，
        避免方法体里的每一行再各报一条。
        """
        try:
            self.parse_type()
            if self.at(Kind.IDENT):
                self.advance()                      # 成员函数名
            if self.at(Kind.LPAREN):
                depth = 0
                while not self.at(Kind.EOF):
                    kk = self.advance().kind
                    if kk == Kind.LPAREN:
                        depth += 1
                    elif kk == Kind.RPAREN:
                        depth -= 1
                        if depth == 0:
                            break
        except ParseError:
            return
        while (self.at(Kind.CONST)
               or (self.at(Kind.IDENT)
                   and (self.peek().text or "") in ("noexcept", "override",
                                                    "final", "mutable"))):
            self.advance()
        if self.at(Kind.LBRACE):
            depth = 0
            while not self.at(Kind.EOF):
                kk = self.advance().kind
                if kk == Kind.LBRACE:
                    depth += 1
                elif kk == Kind.RBRACE:
                    depth -= 1
                    if depth == 0:
                        break
        elif self.at(Kind.SEMI):
            self.advance()

    def parse_trait(self) -> Node:
        """`trait` 已下线（E11，2026-11-05）。

        核对后发现它不是「约束没实现」，而是**三重不可用**：
          ① 参数里不许写 `self`；
          ② 解析器不认 `impl Show for P`（EBNF 写着 `impl path ["for" path]`，
             实现只有 `impl Path {`）；
          ③ trait 里的方法签名要求已下线的 `fn` 关键字。
        而且样例与文档里 0 处使用。**一个看起来有用、实则不存在的机制，比没有更糟。**
        """
        t = self.expect(Kind.TRAIT, "'trait'")
        raise ParseError("E-Trait-Removed",
                         '`trait` 已下线：它从来没有被校验过，而且写法本身也走不通。\n改法：① 用普通 `func` 表达同样的行为；\n      ② 需要静态分发就写具体类型；\n      ③ 需要「这组函数都满足某条件」时，在 spec 的 `effects` 里写清。', t)

    def parse_impl(self) -> Node:
        """`impl` 已下线（E11，2026-11-05）。

        核对后发现它不是「约束没实现」，而是**三重不可用**：
          ① 参数里不许写 `self`；
          ② 解析器不认 `impl Show for P`（EBNF 写着 `impl path ["for" path]`，
             实现只有 `impl Path {`）；
          ③ trait 里的方法签名要求已下线的 `fn` 关键字。
        而且样例与文档里 0 处使用。**一个看起来有用、实则不存在的机制，比没有更糟。**
        """
        t = self.expect(Kind.IMPL, "'impl'")
        raise ParseError("E-Impl-Removed",
                         '`impl` 已下线：和 `trait` 一起下线（没有约束就没有实现检查）。\n改法：直接用普通 `func` 写；要分组就用命名前缀或独立模块。', t)

    # ---------------------------------------------------------- spec

    def parse_spec(self) -> Node:
        t = self.expect(Kind.SPEC, "'spec'")
        name = self.expect_ident("函数名").text
        node = Node("spec", t.line, t.col, name=name)

        # **类型参数**（`spec pick<T>(T a, T b) -> T`）：与 `func f<T>` 同一形状。
        # 以前这里直接 expect('(')，所以 `.tu` 写不了 `<T>` —— 声明与实现配不上对。
        if self.at(Kind.LT):
            _tp = Node("type-params", t.line, t.col)
            self.advance()
            while not self.at(Kind.GT) and not self.at(Kind.EOF):
                # **非类型模板参数 `spec f<u64 N>`**（2026-11-10）：以前在 N 处
                # 报「期望 '>'，实际是 'ident'」。类型参数只能是类型名。
                if self.at(Kind.IDENT) and self.kind(1) == Kind.IDENT:
                    raise ParseError(
                        "E-Spec-NonTypeParam",
                        "类型参数只能是类型名：`spec f<u64 N>` 里的 `u64 N` 不是"
                        "类型参数（本语言没有非类型模板参数）。\n"
                        "改法：写 `spec f<N>(…)`，N 当类型参数；常量用 "
                        "`const N: u64 = …;` 在 `.tu` 顶层声明。", self.peek())
                _tp.add(Node("type-param", t.line, t.col, name=self.expect_ident("类型参数").text))
                if not self.accept(Kind.COMMA):
                    break
            self.expect(Kind.GT, "'>'")
            node.add(_tp)

        self.expect(Kind.LPAREN, "'('")
        node.add(self.parse_params_until(Kind.RPAREN))
        self.expect(Kind.RPAREN, "')'")

        if self.accept(Kind.ARROW):
            # **第 7 条**（P0 兼容层）：`-> void` 归一成 `-> ()`。
            node.add(self._compat_ret_type())
            if self.accept(Kind.BANG):          # v0.68：`T!`
                node.value = {"fallible": True}

        node.add(self.parse_spec_body())
        return node

    def parse_spec_body(self) -> Node:
        """`{ 子句* } end spec`"""
        t = self.expect(Kind.LBRACE, "'{'")
        body = Node("spec-body", t.line, t.col)
        while not self.at(Kind.RBRACE) and not self.at(Kind.EOF) \
                and not self._at_end_terminator():
            # `guarantees { … }` / `requires { … }` **展开成多条独立子句**
            # （2026-11-11）：块里每条表达式各自是一条断言，保留自己的行/列。
            self._add_clauses(body, self.parse_clause())
        # `}` 由这里统一消费（choose 不再自己吃）
        if self.at(Kind.RBRACE):
            self.advance()
        elif self.at(Kind.EOF) and not self._at_end_terminator():
            raise ParseError("E-Parse-Unclosed",
                             "spec 体缺少收尾的 `}`", self.peek())
        self.expect_end("spec")
        return body

    def _add_clauses(self, target: Node, got) -> None:
        """把 `parse_clause()` 的结果挂到父节点：普通子句是一个节点，
        `requires { … }` / `guarantees { … }` 是**一组独立子句**（列表）。"""
        if isinstance(got, list):
            for c in got:
                target.add(c)
        else:
            target.add(got)

    def parse_clause(self):
        t = self.peek()
        k = t.kind

        if k == Kind.CHOOSE:
            return self.parse_choose()

        # 旧写法 `} end spec`：spec 体到这里就该收尾了（由 parse_spec_body 处理）
        if self._at_end_terminator():
            raise ParseError("E-Spec-Closed", "spec 体结束", t)

        if k == Kind.EXTERNAL:
            # `spec f(...) { external "lib"; }` —— 这个 spec 由 C 符号实现
            self.advance()
            lib = self.expect(Kind.STRING, "库名（字符串）").text
            if len(lib) >= 2 and lib[0] == lib[-1] and lib[0] in "\"'":
                lib = lib[1:-1]
            node = Node("clause", t.line, t.col, name="external")
            node.value = {"lib": lib}
            self.expect(Kind.SEMI, "';'")
            return node

        if k == Kind.IDENT:
            word = t.text
            if word == "effects":
                self.advance()
                return self.parse_effects_clause(t)
            if word == "target":
                self.advance()
                return self.parse_target_clause(t)
            if word == "external":
                # **外部实现**：`external "ucrtbase";` —— 这个 spec 没有 .eln 实现，
                # 由 C ABI 符号提供。这样**外部函数和普通函数用同一套 spec**：
                # 有签名、有效果、有 target，`cost` / `bench` / `header` 都能用。
                self.advance()
                lib = self.expect(Kind.STRING, "库名（字符串）").text
                if len(lib) >= 2 and lib[0] == lib[-1] and lib[0] in "\"'":
                    lib = lib[1:-1]
                node = Node("clause", t.line, t.col, name="external")
                node.value = {"lib": lib}
                self.expect(Kind.SEMI, "';'")
                return node
            if word == "benchmodel":
                self.advance()
                return self.parse_benchmodel(t)
            if word == "sizes":
                self.advance()
                return self.parse_sizes_clause(t)
            if word in ("model", "usemodel"):
                raise ParseError(
                    "E-Clause-OldModel",
                    f"`{word}` 已下线（v0.69）：改用 `benchmodel {{ data {{ … }} "
                    f"scale {{ … }} }}`，一个 spec 只写一个", t)
            # 其余子句：`名字 expr ;`
            self.advance()
            # **`guarantees { … }` / `requires { … }`**（2026-11-11）：
            # 花括号只是**作用域**，块里是若干条**各自独立**的断言。
            if word in ("requires", "guarantees") and self.at(Kind.LBRACE):
                return self.parse_assertion_block(word, t)
            node = Node("clause", t.line, t.col, name=word)
            if self.at(Kind.SEMI):
                # requires / guarantees / repeats 都 MUST 有表达式
                if word in ("requires", "guarantees", "repeats"):
                    raise ParseError("E-Clause-EmptyExpr",
                                     f"子句 {word} 缺少表达式", t)
            else:
                node.add(self.parse_expr())
            self.expect(Kind.SEMI, "';'")
            return node

        raise ParseError("E-Parse-UnexpectedClause",
                         f"这里不该出现 {k.value!r}", t)

    #: `guarantees` / `requires` 块里**不能当断言**的语句起点。
    #:
    #: 花括号只是作用域（用户定的语义）：块里的元素 MUST 是**可求值的条件
    #: 表达式**。`if` / `while` / `let` 这类语句出现在这里时，给一条专属诊断，
    #: 而不是让它落成笼统的「期望表达式，实际是 'if'」。
    _ASSERT_BLOCK_BAD_START = frozenset({
        Kind.IF, Kind.WHILE, Kind.FOR, Kind.MATCH, Kind.RETURN,
        Kind.CONST, Kind.BREAK, Kind.CONTINUE, Kind.SCOPE, Kind.SPAWN,
        Kind.PARALLEL, Kind.UNSAFE, Kind.ASM, Kind.ERROR_DECL,
    })

    def parse_assertion_block(self, word: str, t: Token):
        """`guarantees { e1; e2; };` / `requires { … };` —— **块形断言**。

        ## 语义（用户定的，2026-11-11）

        | 写法 | 含义 |
        |---|---|
        | `guarantees e;` | 一条断言 |
        | `guarantees { e1; e2; };` | **两条独立断言**：逐条求值、逐条报错（能指名第几条） |
        | `guarantees { a && b };` | **一条合取断言**，短路求值 |
        | `guarantees { a || b };` | 一条析取断言 |

        **`;` 与 `&&` 不能互相转换**：`{ a; b; }` 是两条（各自独立求值），
        `{ a && b }` 是一条（短路）——`n > 0 && xs.at(0) == 1` 拆成两条会在
        `n == 0` 时越界。所以这里**只按 `;` 切分**，绝不把 `&&` 自动拆开。

        实现：块里每条表达式展开成**一个独立的 `clause` 节点**（行/列取该
        表达式自己的位置），下游的 `guarantees.classify` / `requires` 事实
        提取本来就按「一条 clause 一个表达式」遍历，无需改它们。
        """
        self.expect(Kind.LBRACE, "'{'")
        clauses: List[Node] = []
        exprs: List[Node] = []
        while not self.at(Kind.RBRACE) and not self.at(Kind.EOF):
            self._reject_assert_block_stmt(word)
            ex = self.parse_expr()
            exprs.append(ex)
            # 每条的**位置**就是它自己的位置（诊断能指到具体那一条）。
            clauses.append(Node("clause", ex.line, ex.col, name=word).add(ex))
            if self.accept(Kind.SEMI):
                continue
            if not self.at(Kind.RBRACE):
                raise ParseError(
                    "E-Clause-BlockSep",
                    f"{word} 块里的每条断言都要以 `;` 结尾（最后一条可省略）："
                    f"期望 ';' 或 '}}'，实际是 {self.kind().value!r}。\n"
                    f"`;` 表示**两条独立断言**（各自求值）；要一条合取断言写 "
                    f"`a && b`。", self.peek())
            break
        if not clauses:
            raise ParseError(
                "E-Clause-EmptyExpr",
                f"子句 {word} 的块里至少要有一条断言表达式"
                f"（如 `{word} {{ is_sorted(xs) }};`）", t)
        self.expect(Kind.RBRACE, "'}'")
        self.expect(Kind.SEMI, "';'")
        for i, ex in enumerate(exprs, 1):
            # 只给**块形**打的标记：interp 的报错用它说「第 k 条」。
            # 用普通属性而不是 Node 字段：AST 的形状/dump 一个字节都不变。
            ex.clause_no = i
            ex.clause_total = len(exprs)
        return clauses

    def _reject_assert_block_stmt(self, word: str) -> None:
        """块内出现语句起点（`if` / `while` / `let` / `u64 x`）→ 明确拒绝。"""
        k = self.kind()
        tok = self.peek()
        bad = k in self._ASSERT_BLOCK_BAD_START
        # `let dt x = …;` / `u64 x = …;` 这类**声明形状**：两个连续标识符。
        # 不拦 `f (x)`（函数调用中间有 `(`）与 `a as u64`（中间有 `as`）。
        if not bad and k == Kind.IDENT:
            if tok.text in ("let", "val", "var") and self.kind(1) == Kind.IDENT:
                bad = True
            elif self.kind(1) == Kind.IDENT:
                bad = True
        if bad:
            raise ParseError(
                "E-Clause-BlockStmt",
                f"{word} 块里**只能是断言表达式**（每条以 `;` 分隔）；"
                f"这里出现了语句起点的 `{tok.text}`。\n"
                f"改法：只写可求值的条件（`is_sorted(xs);` / `n > 0;`），"
                f"`if` / `while` / `let` / 声明语句都不属于契约。"
                f"要说明「在什么前提下」，写 `requires {{ … }}`。", tok)

    def parse_effects_clause(self, t: Token) -> Node:
        node = Node("clause", t.line, t.col, name="effects")
        if self.at(Kind.IDENT) and self.peek().text == "pure":
            # v0.62：不再允许 `effects pure;`，必须写成 `effects {};` 或 `effects {pure};`
            raise ParseError("E-Effects-BarePure",
                             "effects 必须用花括号（v0.62 起）；纯函数写 `effects {};`",
                             self.peek())
        self.expect(Kind.LBRACE, "'{'")
        names = []
        while not self.at(Kind.RBRACE) and not self.at(Kind.EOF):
            names.append(self._effect_name())
            if not self.accept(Kind.COMMA):
                break
        self.expect(Kind.RBRACE, "'}'")
        self.expect(Kind.SEMI, "';'")
        # `pure` 只能单独出现
        if "pure" in names and len(names) > 1:
            raise ParseError("E-Effects-PureMixed",
                             "pure 不能与其它效果混用", t)
        node.value = "pure" if (not names or names == ["pure"]) else names
        return node

    def parse_sizes_clause(self, t: Token) -> Node:
        """`sizes n = <形状量>, m = <形状量>;`（规格 §7.2 G1-b）。

        **每一绑都必须是 `名字 = 表达式`**。以前这里走的是"任意子句：`名字 expr ;`"，
        于是 `sizes n = xs.length();` 在 `=` 处报错，而 `sizes xs.length();`
        （没有名字，什么也没绑定）反而被接受、被全部消费者忽略——**死语法**。
        设计写的是 `bind ::= ident '=' size-expr`，这里照文法收。
        """
        node = Node("clause", t.line, t.col, name="sizes")
        if self.at(Kind.SEMI):
            raise ParseError(
                "E-Clause-EmptyExpr",
                "子句 sizes 缺少绑定（形如 `sizes n = xs.length();`）", t)
        while True:
            name_tok = self.expect_ident("尺寸名")
            self.expect(Kind.EQ,
                        "'='（sizes 的每一绑都是 `名字 = 表达式`，"
                        "如 `sizes n = xs.length();`）")
            bind = Node("size-bind", name_tok.line, name_tok.col,
                        name=name_tok.text)
            bind.add(self.parse_expr())
            node.add(bind)
            if not self.accept(Kind.COMMA):
                break
        self.expect(Kind.SEMI, "';'")
        return node

    def parse_target_clause(self, t: Token) -> Node:
        node = Node("clause", t.line, t.col, name="target")
        self.expect(Kind.LBRACE, "'{'")
        while not self.at(Kind.RBRACE) and not self.at(Kind.EOF):
            dim = self.expect_ident("成本维度").text
            self.expect(Kind.COLON, "':'")
            spec = Node("dim", t.line, t.col, name=dim)
            if self.accept(Kind.LBRACE):
                # { max: expr } / { min: …, max: … } / { exact: N }
                while not self.at(Kind.RBRACE) and not self.at(Kind.EOF):
                    slot = self.expect_ident("槽位（min/max/exact）").text
                    self.expect(Kind.COLON, "':'")
                    slot_node = Node("slot", t.line, t.col, name=slot).add(self.parse_expr())
                    spec.add(slot_node)
                    if not self.accept(Kind.COMMA):
                        break
                self.expect(Kind.RBRACE, "'}'")
            else:
                spec.add(self.parse_expr())
            node.add(spec)
            if not self.accept(Kind.COMMA):
                break
        self.expect(Kind.RBRACE, "'}'")
        self.expect(Kind.SEMI, "';'")
        return node

    def parse_benchmodel(self, t: Token) -> Node:
        """spec 体内的每一项 MUST 以 ';' 结束（含 `benchmodel` / `choose` 这类块）。

        理由：这些子句以**上下文关键字**开头（词法上是普通标识符），
        没有 `;` 就无法判断子句在哪里结束。`func` / `spec` 是真关键字、自起，故不带 `;`。
        """
        node = self._parse_benchmodel_core(t)
        # **甲（统一带 ;）**：样例与 `dsh fmt` 现在一律带 `;`。
        # 解析器这里**接受但不强制**——强制需要把 30+ 内联用例逐个改，
        # 且 spec 体内与顶层是两条解析路径，要单独设计（记入缺口 E27）。
        self.accept(Kind.SEMI)
        return node

    def _parse_benchmodel_core(self, t: Token) -> Node:
        """`benchmodel { data { … } scale { … } seed = n; }`（v0.69）

        一个 spec 只有一个 benchmodel；原先的 `model 场景名` + `usemodel` 已下线。
        """
        node = Node("benchmodel", t.line, t.col)
        self.expect(Kind.LBRACE, "'{'")

        # ---- data
        if not self._at_word("data"):
            raise ParseError("E-Bench-MissingData",
                             "benchmodel 缺少 `data` 段（每个参数的值从哪来，必写）", self.peek())
        self.advance()
        data = Node("bench-data", self.peek().line, self.peek().col)
        self.expect(Kind.LBRACE, "'{'")
        while not self.at(Kind.RBRACE) and not self.at(Kind.EOF):
            pt = self.expect_ident("参数名")
            self.expect(Kind.EQ, "'='")
            data.add(Node("binding", pt.line, pt.col, name=pt.text).add(self.parse_expr()))
            self.expect(Kind.SEMI, "';'")
        self.expect(Kind.RBRACE, "'}'")
        node.add(data)

        # ---- scale
        if not self._at_word("scale"):
            raise ParseError("E-Bench-MissingScale",
                             "benchmodel 缺少 `scale` 段（测哪些规模，必写）", self.peek())
        self.advance()
        scale = Node("bench-scale", self.peek().line, self.peek().col)
        self.expect(Kind.LBRACE, "'{'")
        while not self.at(Kind.RBRACE) and not self.at(Kind.EOF):
            st = self.peek()
            # v0.69：`参数 in range(...)`（旧写法 `n(参数) = range(...)` 已下线，MB-14）
            if not (st.kind == Kind.IDENT):
                raise ParseError("E-Bench-BadScale",
                                 "scale 段只能写 `参数名 in range(...)`", st)
            pn_tok = self.advance()
            pn = pn_tok.text
            if self.at(Kind.LPAREN):
                raise ParseError(
                    "E-Bench-OldScale",
                    f"`n({pn}) = range(...)` 已下线（v0.69）；"
                    f"改成 `{pn} in range(...)`——`data` 用 `=`（值），"
                    f"`scale` 用 `in`（遍历规模）", pn_tok)
            if not self.at(Kind.IN):
                raise ParseError("E-Bench-BadScale",
                                 f"scale 段要写 `{pn} in range(...)`（缺 `in`）", self.peek())
            self.advance()
            scale.add(Node("scale-binding", st.line, st.col, name=pn).add(self.parse_expr()))
            self.expect(Kind.SEMI, "';'")
        self.expect(Kind.RBRACE, "'}'")
        node.add(scale)

        # ---- seed（可省）
        if self._at_word("seed"):
            st = self.advance()
            self.expect(Kind.EQ, "'='")
            node.add(Node("seed", st.line, st.col).add(self.parse_expr()))
            self.expect(Kind.SEMI, "';'")

        self.expect(Kind.RBRACE, "'}'")
        return node

    # ---------------------------------------------------------- choose

    def parse_choose(self) -> Node:
        """spec 体内的每一项 MUST 以 ';' 结束（含 `benchmodel` / `choose` 这类块）。

        理由：这些子句以**上下文关键字**开头（词法上是普通标识符），
        没有 `;` 就无法判断子句在哪里结束。`func` / `spec` 是真关键字、自起，故不带 `;`。
        """
        node = self._parse_choose_core()
        # **甲（统一带 ;）**：样例与 `dsh fmt` 现在一律带 `;`。
        # 解析器这里**接受但不强制**——强制需要把 30+ 内联用例逐个改，
        # 且 spec 体内与顶层是两条解析路径，要单独设计（记入缺口 E27）。
        self.accept(Kind.SEMI)
        return node

    def _parse_choose_core(self) -> Node:
        t = self.expect(Kind.CHOOSE, "'choose'")
        name = self.expect_ident("标签名").text
        node = Node("choose", t.line, t.col, name=name)

        # 签名写在 choose 上：候选的实现共享它（v0.66）
        if not self.at(Kind.LPAREN):
            raise ParseError(
                "E-Choose-NoSignature",
                f"choose {name} 必须写参数表，例如 `choose {name}(xs: []u64) -> u64`", t)
        self.expect(Kind.LPAREN, "'('")
        node.add(self.parse_params_until(Kind.RPAREN))
        self.expect(Kind.RPAREN, "')'")
        if self.accept(Kind.ARROW):
            node.add(self.parse_type())
            if self.accept(Kind.BANG):          # v0.68：`T!`
                node.value = {"fallible": True}

        self.expect(Kind.LBRACE, "'{'")
        while not self.at(Kind.RBRACE) and not self.at(Kind.EOF):
            ct = self.peek()
            if ct.kind == Kind.IDENT:
                word = ct.text
                self.advance()
                item = Node("choose-clause", ct.line, ct.col, name=word)
                if word == "over":
                    names, pending = self.parse_candidate_set()
                    item.value = {"names": names, "pending": pending}
                elif word == "pending":
                    item.value = self.parse_ident_set()
                elif word in ("model", "usemodel"):
                    raise ParseError(
                        "E-Clause-OldModel",
                        f"choose 里不再有 `{word}`（v0.69 已删）：bench 数据统一由宿主 spec 的 "
                        f"`benchmodel` 提供，有 `?!` 就一定测", ct)
                elif word == "objective":
                    item.value = self.expect_ident("目标名").text
                else:
                    item.add(self.parse_expr())
                self.expect(Kind.SEMI, "';'")
                node.add(item)
            else:
                raise ParseError("E-Parse-UnexpectedChoose",
                                 f"这里不该出现 {ct.kind.value!r}", ct)
        if self.at(Kind.RBRACE):
            self.advance()
        self.expect_end("choose")       # 旧写法 `end choose` 在这里报错并吃掉
        return node

    def parse_ident_set(self) -> List[str]:
        names, _ = self.parse_candidate_set(allow_pending=False)
        return names

    def parse_candidate_set(self, allow_pending: bool = True):
        """`{ a, b, c pending }` —— pending 是裸词，只跟在本项名字后面。"""
        self.expect(Kind.LBRACE, "'{'")
        names: List[str] = []
        pending: List[str] = []
        while not self.at(Kind.RBRACE) and not self.at(Kind.EOF):
            nm = self.expect_ident("标识符").text
            names.append(nm)
            if allow_pending and self.at(Kind.KERNELS) is False and self._at_word("pending"):
                self.advance()
                pending.append(nm)
            if not self.accept(Kind.COMMA):
                break
        self.expect(Kind.RBRACE, "'}'")
        return names, pending

    # ---------------------------------------------------------- fn / kernel

    def parse_type_params(self) -> Node:
        """`<T>` / `<K, V>`：类型参数列表（v0.71 泛型第一批）。"""
        t = self.expect(Kind.LT, "'<'")
        node = Node("type-params", t.line, t.col)
        while not self.at(Kind.GT) and not self.at(Kind.EOF):
            node.add(Node("type-param", t.line, t.col,
                          name=self.expect_ident("类型参数名").text))
            if not self.accept(Kind.COMMA):
                break
        self.expect(Kind.GT, "'>'")
        return node

    def parse_fn(self, is_pub: bool) -> Node:
        # 第 12 条：形状③的"迭代器逃逸"只在**本函数**内追踪——同名变量在
        # 下一个函数里是另一件事，不能跨函数误报。
        if self.compat:
            self._iter_escapes.clear()
        if is_pub:
            self.expect(Kind.PUB, "'pub'")
        t = self.peek()
        ret_node = None
        is_auto = False
        ret_bang = False
        if self.at(Kind.FN) or (self.at(Kind.IDENT)
                                and (self.peek().text or "") == "fn"):
            # `func`（`Kind.FN`）继续接受；`fn`（v0.68 下线，2026-11-10 起是
            # 普通 `IDENT`）也接受——关键字诊断由 `parse_item` 的**位置判据**
            # 报，这里只负责解析（AST 与 `func` 完全同形）。
            t = self.advance()
        else:
            # **C++ 形状**：返回类型在前（`u64 f(…)`）。`auto f(…) -> T` 也走
            # 这里：`auto` 不是类型（本语言没有类型推断），只是后置返回的引导词，
            # 真正的返回类型由 `->` 给。
            if self.at(Kind.IDENT) and (self.peek().text or "") == "auto":
                is_auto = True
                self.advance()
            else:
                ret_node = self._compat_ret_type()
                # **C++ 形状里的 `T!`**（P4-0）：`u64! f(…) { … }` 与
                # `-> u64!` 同一含义（可能失败，错误类型取模块的 error 声明）。
                # 以前只有后置 `-> T!` 一条路，于是 `func f() -> u64!` 迁成
                # C++ 形后无法表达"可能失败"。
                ret_bang = bool(self.accept(Kind.BANG))
        name_tok = self.expect_ident("函数名")
        node = Node("fn", t.line, t.col, name=name_tok.text)
        node.value = {"pub": is_pub, "fallible": ret_bang}
        # 函数名是"值名"（可调用）：语句起点 `f * x;` 不该被当成 `f*` 声明。
        self.value_names.add(node.name)
        # **泛型参数**：`func id<T>(…)` / `u64 id<T>(…)` / `u64 pair<K, V>(…)`
        if self.at(Kind.LT):
            _tps = self.parse_type_params()
            node.add(_tps)
            # 类型参数也是类型名：`T f<T>(T a) { T * p; … }` 里
            # `T * p` 是声明（C++ 的 dependent type 同义）。
            for _tp in _tps.children:
                if getattr(_tp, "name", None):
                    self.type_params.add(_tp.name)

        self.expect(Kind.LPAREN, "'('")
        # 第 13 条：形参进作用域（`r.push(1)` 要能问出"`r` 是什么类型"）。
        # `compat=False` 时这里只是压一个空 dict，不影响任何判据。
        self._scope_push()
        try:
            node.add(self.parse_params_until(Kind.RPAREN))
            self.expect(Kind.RPAREN, "')'")
            if self.accept(Kind.ARROW):
                ret_node = self._compat_ret_type()
                # v0.68：`T!` 表示可能失败，错误类型取模块的 error 声明
                if self.accept(Kind.BANG):
                    node.value["fallible"] = True
            elif is_auto:
                raise ParseError(
                    "E-Fn-AutoNoReturn",
                    "`auto f(…)` 必须写后置返回类型：`auto f(const u64& x) -> u64 { … }`。\n"
                    "本语言没有 `auto` 类型推断（契约与成本都依赖静态类型）；"
                    "`auto` 只用来引导 C++ 的后置返回写法，`->` 后面才是返回类型。",
                    name_tok)
            # **子节点顺序固定**为「类型参数 → 形参 → 返回类型 → 体 → kernels」：
            # typecheck / checker / interp 都按位置找 `params` 与返回类型节点，
            # 两种写法必须产出**完全同形**的 AST（否则一半检查看不见返回类型）。
            if ret_node is not None:
                node.add(ret_node)
            node.add(self.parse_block())
        finally:
            self._scope_pop()

        # 候选块：`} kernels L(...) -> R { 名字 { … } … }`（可重复、可嵌套）
        while self.at(Kind.KERNELS):
            node.add(self.parse_kernels_clause())

        self.expect_end("fn")
        return node

    # ---------------------------------------------------------- C++ 形状探测

    def kind_at(self, j: int) -> Kind:
        """第 j 个 token 的种类（j 是**绝对下标**，不是相对偏移）。"""
        if j < 0 or j >= len(self.toks):
            return Kind.EOF
        return self.toks[j].kind

    def _probe_type_end(self, start: Optional[int] = None,
                        spaced_ptr_ok: bool = True) -> Optional[int]:
        """**试解**一个类型，返回它结束处的绝对下标；解不出返回 None。

        **为什么要试解**：`_at_type_prefixed_decl()` 与「这一行是不是 C++ 形状的
        函数声明」都要判断「当前这段是不是一个类型」。旧实现靠手写的前瞻规则
        （只看两个相邻 IDENT），`std::vector<u64> xs;` / `unsigned long long n;`
        / `char c;` 全判不出来，于是落进表达式分支、报的是一句「期望 ';'」。
        试解让**判据与真正的解析器是同一份代码**，不会再漂移。

        `spaced_ptr_ok=False`：**语句起点**用。有空格 `*` 只在左边是已知类型名
        时才算进类型（`u64 * p` 是声明，`a * b` 不是）；其余类型位置保持默认
        `True`（`char ** argv` 这类写法在形参 / 返回位置没有第二种读法）。

        副作用一律回滚：`self.i`、`self.diags`、`self.soft`、
        `self._spaced_ptr_ok` 都恢复到试解前。
        """
        save_i = self.i
        save_d = len(self.diags)
        save_s = len(self.soft)
        save_ok = self._spaced_ptr_ok
        self._spaced_ptr_ok = spaced_ptr_ok
        if start is not None:
            self.i = start
        try:
            self.parse_type()
            return self.i
        except ParseError:
            return None
        finally:
            self.i = save_i
            del self.diags[save_d:]
            del self.soft[save_s:]
            self._spaced_ptr_ok = save_ok

    def _at_cxx_fn_decl(self) -> bool:
        """当前位置是否是 **C++ 形状的函数声明**：`RET name<T>(…) [-> RET] {`。

        判据是**形状**而不是「类型名在不在表里」：`Point make_point() {` 与
        `u64 add(const u64& x) {` 一样合法。拒掉的形状：`T x = …;`（顶层变量）、
        `T x;`、`T name(…) ;`（原型——本语言没有前置声明）——它们走原来的
        诊断，不会被误当函数。
        """
        j = self._probe_type_end()
        if j is None:
            return False
        # `u64! f(…)`：`!` 表示可能失败（P4-0，C++ 形状的 fallible 标记）。
        if self.kind_at(j) == Kind.BANG:
            j += 1
        if self.kind_at(j) != Kind.IDENT:
            return False
        j += 1                                   # 函数名
        if self.kind_at(j) == Kind.LT:           # 泛型参数 `<T>`
            depth = 0
            while self.kind_at(j) != Kind.EOF:
                kk = self.kind_at(j)
                if kk == Kind.LT:
                    depth += 1
                elif kk == Kind.GT:
                    depth -= 1
                    if depth == 0:
                        j += 1
                        break
                elif kk in (Kind.SEMI, Kind.LBRACE):
                    return False
                j += 1
            else:
                return False
        if self.kind_at(j) != Kind.LPAREN:
            return False
        depth = 0
        while self.kind_at(j) != Kind.EOF:
            kk = self.kind_at(j)
            if kk in (Kind.LPAREN, Kind.LBRACKET):
                depth += 1
            elif kk in (Kind.RPAREN, Kind.RBRACKET):
                depth -= 1
                if depth == 0:
                    j += 1
                    break
            j += 1
        if self.kind_at(j) == Kind.ARROW:        # 后置返回
            j = self._probe_type_end(j + 1)
            if j is None:
                return False
        return self.kind_at(j) == Kind.LBRACE

    def _at_word(self, word: str) -> bool:
        return self.at(Kind.IDENT) and self.peek().text == word

    def parse_kernels_clause(self) -> Node:
        """`} kernels L(params) -> R { 候选* }`

        候选写成 `<名字> { 体 }`：无关键字、无签名——参数与返回类型由这个头给出。
        候选体之后可再跟一个 `kernels` 块（嵌套，深度上限见 checker）。
        """
        kt = self.expect(Kind.KERNELS, "'kernels'")
        label = self.expect_ident("choose 标签").text
        node = Node("kernels", kt.line, kt.col, name=label)
        node.value = {"depth": 1}

        self.expect(Kind.LPAREN, "'('")
        node.add(self.parse_params_until(Kind.RPAREN))
        self.expect(Kind.RPAREN, "')'")
        if self.accept(Kind.ARROW):
            node.add(self.parse_type())
            if self.accept(Kind.BANG):          # v0.68：候选头也允许 `T!`
                node.value["fallible"] = True

        self.expect(Kind.LBRACE, "'{'")
        while not self.at(Kind.RBRACE) and not self.at(Kind.EOF):
            node.add(self.parse_candidate())
        self.expect(Kind.RBRACE, "'}'")
        return node

    def parse_candidate(self) -> Node:
        # `noinline` 前缀（v0.69）：候选默认 inline，可捕获宿主局部变量
        if self._at_word("noinline"):
            self.advance()
        nt = self.expect_ident("候选名")
        cand = Node("candidate", nt.line, nt.col, name=nt.text)
        cand.add(self.parse_block())
        # v0.68：kernels 不嵌套。仍然解析（让检查器统一报 E-Kernels-Nested），
        # 但只允许一层——遇到第二层就直接报错，避免结构无意义地加深。
        if self.at(Kind.KERNELS):
            kt = self.peek()
            self.error("E-Kernels-Nested",
                       "kernels 不能嵌套在候选体里（v0.68）；"
                       "需要「选了 A 才需要 B」时，把 B 提升为独立函数", kt)
        return cand

    def _reject_positional_init(self):
        """初始化 / 返回表达式后紧跟 `{`：`Point { 1, 2 }` 是**位置式**初始化。

        结构体字面量要求字段名（`Point { x: 1, y: 2 }`，由 `parse_postfix` 的
        三重前瞻认）；走到这里说明 `{` 前的路径**不是**字段名形式，故必是
        位置式。只在声明初始化 / `return` 的收尾处判——`match token {` 的
        `{` 是 match 自己的体，不能在这里误伤。
        """
        if self.at(Kind.LBRACE):
            raise ParseError(
                "E-Type-PositionalInit",
                "本语言没有位置式初始化：`Point { 1, 2 }` 必须写字段名。\n"
                "改法：`Point { x: 1, y: 2 }`。", self.peek())

    def _reject_ptr_or_variadic(self):
        """类型后面还剩 `*` 或 `...`：分别给专属诊断。

        **`T * p` 现在按类型名判**（2026-11-10②，见 `parse_type_ref_suffix`
        与 `_at_unknown_spaced_ptr_decl`）：类型位置（形参 / 返回类型 / 字段 /
        已判定的声明）里 `*` 有没有空格都收，所以走到这里说明**这个类型解析
        路径没有 `*` 后缀的位置**（例如元组类型 `(u64, u64) * p`），或者回退
        路径把它留了下来。消息不再说"必须紧贴"——那条规则已经废弃。

        回退的正式入口在 `_at_unknown_spaced_ptr_decl`（两边都不是已知名字、
        按声明也解析不下去）。这里保留同码防御，保证「类型与 `*` 分家」不会
        落成一句「期望 ';'」。

        `T...` 仍然不存在（变参），保持原诊断。
        """
        if self.at(Kind.STAR) or self.at(Kind.STAR_STAR):
            raise ParseError(
                "E-Pointer-Spacing",
                "`*` 没有归进类型，也不在可解析的声明形状里。\n"
                "本语言判「指针声明 vs 乘法」看**左边的名字是不是已知类型名**："
                "`u64 * p;` / `MyStruct * q;` 是声明（类型名在**这一行之前**"
                "声明过），`a * b;` 是乘法（`a` 是变量）。\n"
                "若你想要指针声明，请确认类型名已声明在前面（前向引用 C++ 也"
                "解析不了）；若想要乘法，左边的操作数必须是变量。",
                self.peek())
        if self.at(Kind.RANGE):
            raise ParseError(
                "E-Param-Variadic",
                "本语言没有变参（`T... args`）：形参个数是契约的一部分"
                "（κ 依赖它）。\n改法：写固定个数的形参，或传一个容器 "
                "`vector<T>`。", self.peek())

    def _param_array_suffix(self, param: Node) -> Node:
        """形参名后的 `[]`：`char * argv[]` ≡ `char** argv`（C++ 的数组退化）。

        本语言没有 C 数组（语句里的 `T a[]` 报 `E-Type-NoCArray`），但
        **形参位置**的 `T name[]` 在 C++ 里不是数组，是指针——`int main(int
        argc, char * argv[])` 是每个 C++ 程序的第一行。拒绝它等于在 `main`
        的签名上把"改名即用"断掉。非空 `[N]` 不在这里收：那不是退化。
        """
        if not (self.at(Kind.LBRACKET) and self.kind(1) == Kind.RBRACKET):
            return param
        bt = self.advance()                      # '['
        self.advance()                           # ']'
        if param.children:
            param.children[0] = Node(
                "type-ptr", bt.line, bt.col,
                value={"pointee_const": False, "ptr_const": False},
            ).add(param.children[0])
        return param

    def parse_params_until(self, close: Kind) -> Node:
        """参数列表；遇到 close 就停。

        三种形态都接受：
          v0.68  `const T& name` / `T& name` / `T name`   （类型前置）
          v0.67  `name: T`                                （已下线，给兼容诊断）
          裸类型 `T`
        """
        params = Node("params")
        while not self.at(close) and not self.at(Kind.EOF):
            # v0.68：`const T& name` / `T& name` / `T name`（类型前置）
            if self.at(Kind.CONST) or self._at_cxx_param() or self._at_typed_param() \
                    or self._at_legacy_param():
                _is_const = self.at(Kind.CONST)
                pt = self.advance() if _is_const else self.peek()
                typ = self.parse_type()
                # **`const Json* v` 的 const 修饰的是"指向"**（不是绑定）：
                # 形参位置的前导 `const` 已经被上面吃掉了，这里补记到类型上——
                # 否则 `const Json*` 与 `Json*` 在类型层完全同形，
                # `S* p = const_ptr` 这种**去 const** 的写法会被静默放行。
                if _is_const and typ is not None and typ.kind == "type-ptr":
                    if not isinstance(typ.value, dict):
                        typ.value = {"pointee_const": False, "ptr_const": False}
                    typ.value["pointee_const"] = True
                self._reject_ptr_or_variadic()
                # **泛型按值形参**（v0.72）：`FixedVec<u64, 3> v`。
                # 以前 `_at_typed_param` 只认「两个相邻 IDENT」，`Name<…> v`
                # 被当成"没有名字的裸类型"，随后在 `v` 处报 "期望 ')'"。
                if self.at(Kind.IDENT) and self.kind(1) in (
                        Kind.COMMA, Kind.RPAREN, Kind.LBRACKET):
                    nm = self.advance().text
                else:
                    nm = self.expect_ident("参数名").text
                _p = Node("param", pt.line, pt.col, name=nm).add(typ)
                if _is_const:
                    # **`const T& xs` 的 const 以前被丢掉**（AST 里查不到），
                    # 于是"不可变借用"这条承诺在运行期无从检查。
                    _p.value = {"const": True}
                self._param_array_suffix(_p)
                self.value_names.add(nm)
                params.add(_p)
                if not self.accept(Kind.COMMA):
                    break
                continue
            # **函数类型参数**：`func(u64, u64) -> u64 g`
            if self.at(Kind.FN) and self.kind(1) == Kind.LPAREN:
                pt = self.peek()
                typ = self.parse_type()
                nm = self.expect_ident("参数名").text
                _p = self._param_array_suffix(
                    Node("param", pt.line, pt.col, name=nm).add(typ))
                self.value_names.add(nm)
                params.add(_p)
                if not self.accept(Kind.COMMA):
                    break
                continue
            if self.kind() == Kind.IDENT and self.kind(1) == Kind.COLON:
                pt = self.advance()
                self.advance()
                typ = self.parse_type()
                self._reject_ptr_or_variadic()
                p = Node("param", pt.line, pt.col, name=pt.text).add(typ)
                self._param_array_suffix(p)
                self.value_names.add(pt.text)
                params.add(p)
            else:
                _bt = self.peek()
                _bty = self.parse_type()
                self._reject_ptr_or_variadic()
                # **泛型按值形参** `FixedVec<u64, 3> v`：以前这里一律当"裸类型"，
                # 随后在 `v` 处报 "期望 ')'"。
                if self.at(Kind.IDENT) and self.kind(1) in (
                        Kind.COMMA, Kind.RPAREN, Kind.LBRACKET):
                    _nm = self.advance().text
                    _p = self._param_array_suffix(
                        Node("param", _bt.line, _bt.col, name=_nm).add(_bty))
                    self.value_names.add(_nm)
                    params.add(_p)
                else:
                    params.add(_bty)
            if not self.accept(Kind.COMMA):
                break
        # 第 13 条：形参进作用域（**只**服务"接收者是什么类型"）。
        # 成员函数体里的形参会遮住同名字段（C++ 的作用域规则相同）。
        for _p in params.children:
            if getattr(_p, "kind", "") == "param" and _p.children:
                self._scope_declare(_p.name, _p.children[0])
        return params

    def _generic_tail(self, j0: int) -> bool:
        """从下标 j0 处的 `<` 起，判断是否构成 `Name<...>::`。"""
        depth = 0
        j = j0
        while self.kind(j) != Kind.EOF:
            k = self.kind(j)
            if k == Kind.LT:
                depth += 1
            elif k == Kind.GT:
                depth -= 1
                if depth == 0:
                    return self.kind(j + 1) == Kind.PATHSEP
            elif k in (Kind.SEMI, Kind.LBRACE):
                return False
            j += 1
        return False

    def _generic_ctor_tail(self) -> bool:
        """从当前位置的 `<` 起，是否是 **`array<T, N>(v)` 形状的构造调用**。

        判据：配平的 `>` 后面**紧跟 `(`**。只在当前标识符是**已知类型名**
        （`array` / `vector` …）时才问这个问题——否则 `a < b > (c)` 这类
        比较表达式会被误读成构造调用。
        """
        depth = 0
        j = 0
        while self.kind(j) != Kind.EOF:
            k = self.kind(j)
            if k == Kind.LT:
                depth += 1
            elif k == Kind.GT:
                depth -= 1
                if depth == 0:
                    return self.kind(j + 1) == Kind.LPAREN
            elif k in (Kind.SEMI, Kind.LBRACE, Kind.RBRACE):
                return False
            j += 1
        return False

    def _generic_call_ahead(self) -> bool:
        """从当前位置起，若是 `Name<...>::` 形状，说明这是静态成员调用而不是类型。"""
        if not self.at(Kind.IDENT) or self.kind(1) != Kind.LT:
            return False
        depth = 0
        j = 1
        while self.kind(j) != Kind.EOF:
            k = self.kind(j)
            if k == Kind.LT:
                depth += 1
            elif k == Kind.GT:
                depth -= 1
                if depth == 0:
                    return self.kind(j + 1) == Kind.PATHSEP
            elif k in (Kind.SEMI, Kind.LBRACE):
                return False
            j += 1
        return False

    def _at_legacy_param(self) -> bool:
        """`T name` 之外的旧形态 `name: &T`（v0.67）：仍在 name: 分支处理。"""
        return False

    def _at_typed_param(self) -> bool:
        """判断当前是否是 `T name` 形态的参数（两个标识符相邻，且后面是 , 或 )）。"""
        if self.kind() != Kind.IDENT or self.kind(1) != Kind.IDENT:
            return False
        return self.kind(2) in (Kind.COMMA, Kind.RPAREN)

    def _at_cxx_param(self) -> bool:
        """判断当前是否是 `T& name` 形态的参数（类型后跟 & 再跟标识符）。"""
        j = 0
        depth = 0
        while self.kind(j) != Kind.EOF:
            kk = self.kind(j)
            if kk in (Kind.LT, Kind.LBRACKET, Kind.LPAREN):
                depth += 1
            elif kk in (Kind.GT, Kind.RBRACKET):
                depth -= 1
            elif kk == Kind.AMP:
                return self.kind(j + 1) == Kind.IDENT
            elif depth == 0 and kk in (Kind.COMMA, Kind.RPAREN, Kind.COLON):
                return False
            j += 1
        return False

    # ---------------------------------------------------------- 类型

    def _parse_type_args(self, line: int, col: int) -> Node:
        """解析类型实参（**唯一实现**，两处共用）。

        以前 `parse_type()` 与表达式里的 `FixedVec<...>::make()` 各写了一遍，
        改一处另一处不动——`FixedVec<u64, N + 1>` 就只在其中一边报错。
        """
        args = Node("type-args", line, col)
        while not self.at(Kind.GT) and not self.at(Kind.EOF):
            if self.at(Kind.INT):
                nt = self.advance()
                left = Node("lit-int", nt.line, nt.col, value=nt.value)
            elif self.at(Kind.IDENT) and self.kind(1) in (
                    Kind.PLUS, Kind.MINUS, Kind.STAR, Kind.SLASH):
                it = self.advance()
                left = Node("path", it.line, it.col, name=it.text)
            else:
                args.add(self.parse_type())
                if not self.accept(Kind.COMMA):
                    break
                continue
            left = self._type_const_expr_tail(left)
            args.add(left if left.kind == "binary"
                     else Node("type-const", left.line, left.col, value=left.value))
            if not self.accept(Kind.COMMA):
                break
        self.expect(Kind.GT, "'>'")
        return args

    def _type_const_expr_tail(self, left: Node) -> Node:
        """把类型实参里的 `+ - * /` 尾巴吃成 `binary` 树（只认字面量与常量名）。"""
        while self.peek().text in ("+", "-", "*", "/"):
            op = self.advance()
            if self.at(Kind.INT):
                nt = self.advance()
                right = Node("lit-int", nt.line, nt.col, value=nt.value)
            elif self.at(Kind.IDENT):
                it = self.advance()
                right = Node("path", it.line, it.col, name=it.text)
            else:
                self.error("E-Type-ConstExpr", "常量表达式右边要是数字或常量名", op)
                break
            b = Node("binary", left.line, left.col, value=op.text)
            b.add(left)
            b.add(right)
            left = b
        return left

    def parse_type(self) -> Node:
        t = self.peek()

        # **第 15 条**（P0 兼容层）：类型位置的**详细类型说明符**
        # `class JsonParser* p;` / `const class Foo& f = …;` —— `--compat` 下
        # `class X` 就是 `X`（`class` 与 `struct` 同义）。只认后面跟名字的
        # 形状（`class` 本身不是类型名）。
        if (self.compat and self.at(Kind.IDENT)
                and (t.text or "") == "class" and self.kind(1) == Kind.IDENT):
            self.advance()                               # `class`
            return self.parse_type()

        # **`const T*`（指向 const 的指针）/ `const T&`（只读借用）**：
        # `const` 是**类型谓词**，不是「不可变绑定」——`const Json* cur = &root;`
        # 之后 `cur = …`（换指向）在 C++ 里是合法的，所以这里不能走
        # `parse_const_decl` 那条「绑定不可变」的路。
        # 形参位置的 `const` 已由 `parse_params_until` 先吃掉（记在 param 上），
        # 局部 `const u64 x = 5;` 由 `parse_const_decl` 先吃掉——两条都不会到这里。
        if self.at(Kind.CONST):
            ct = self.advance()
            return self._mark_pointee_const(self.parse_type(), ct)

        # **C++ 标量拼写**（R1-A，§8.3）——`int` / `unsigned long long` /
        # `size_t` / `char` / `bool` … 一律归一到本语言的宽度类型（见
        # `CXX_SCALAR_TYPES`）。**必须在 `parse_path` 之前**：否则 `int x;`
        # 会被当成「用户类型名 `int`」，报的是 `E-Type-Unknown`。
        _scalar = self._cxx_scalar_type()
        if _scalar is not None:
            _name, _n = _scalar
            for _ in range(_n):
                self.advance()
            # **`char** argv` / `size_t* p`**：C++ 标量拼写后面同样可以跟
            # `*`（指针）——以前这里直接 return，`char**` 的 `*` 落在类型外，
            # 被 `_reject_ptr_or_variadic` 当成「没紧贴的指针」误报。
            return self.parse_type_ref_suffix(
                Node("type-name", t.line, t.col, name=_name))

        if self.at(Kind.AMP):                           # &T / &mut T（v0.67 旧写法）
            self.advance()
            node = Node("type-ref", t.line, t.col)
            if self.kind() == Kind.IDENT and self.peek().text == "mut":
                self.advance()
                node.value = "mut"
                self.error("E-Ref-OldMut",
                           "`&mut T` 已下线：v0.68 起写 `T&`（可写引用），`&` 跟在类型后",
                           t)
            else:
                self.error("E-Ref-OldMut",
                           "`&T` 已下线：v0.68 起写 `const T&` 或 `T&`（`&` 跟在类型后）",
                           t)
            node.add(self.parse_type())
            return self.parse_type_ref_suffix(node)

        if self.at(Kind.LBRACKET):                      # []T（v0.67 旧写法）
            self.advance()
            self.expect(Kind.RBRACKET, "']'")
            self.error("E-Type-OldSlice",
                       "`[]T` 已下线：v0.68 起写 `vector<T>`（可变长）"
                       "或 `array<T, N>`（定长，N 是容量）", t)
            return Node("type-slice", t.line, t.col).add(self.parse_type())

        if self.at(Kind.FN) and self.kind(1) == Kind.LPAREN:
            # **函数类型**：`func(u64, Vec<u8>) -> u64`
            # slice 1 只允许出现在**参数类型位置**（返回值/局部变量是函数先不做）。
            self.advance()                               # func
            self.advance()                               # (
            fn = Node("type-fn", t.line, t.col)
            params = Node("type-fn-params", t.line, t.col)
            while not self.at(Kind.RPAREN) and not self.at(Kind.EOF):
                params.add(self.parse_type())
                if not self.accept(Kind.COMMA):
                    break
            self.expect(Kind.RPAREN, "')'")
            self.expect(Kind.ARROW, "'->'")
            fn.add(params)
            fn.add(self.parse_type())
            return fn

        if self.at(Kind.LPAREN):                        # (T, U)
            self.advance()
            node = Node("type-tuple", t.line, t.col)
            while not self.at(Kind.RPAREN) and not self.at(Kind.EOF):
                node.add(self.parse_type())
                if not self.accept(Kind.COMMA):
                    break
            self.expect(Kind.RPAREN, "')'")
            return node

        if self.at(Kind.INT):                          # 类型实参里的常量数字：FixedVec<T, 256>
            nt = self.advance()
            return Node("type-const", nt.line, nt.col, value=nt.value)

        if self.at(Kind.IDENT) or self.at(Kind.SELF) or self.at(Kind.DYN):
            # `FixedVec<T, N>::make()` 走表达式路径，不当作类型
            if self._generic_call_ahead():
                raise ParseError("E-Parse-ExpectedType",
                                 "这里期望类型（泛型静态调用不是类型）", t)
            path = self.parse_path("类型名")
            # **`std::` 前缀与短名是同一个类型**（§8.3 / §8.11）：`std.vector` →
            # `vector`；顺带把 `size_t` / `uint32_t` 这类宽度名归一。
            path = self._normalize_type_path(path, t)
            # **第 2 条**（P0 兼容层）：类型别名在这里**当场展开**。
            # 别名不进 AST —— 展开出来的就是目标类型节点本身，所以
            # `using U64Vec = vector<u64>; U64Vec v;` 与 `vector<u64> v;`
            # 的 AST / κ / run 输出必然逐字节相同。
            if self.compat and path in self.type_aliases:
                return self.parse_type_ref_suffix(
                    copy.deepcopy(self.type_aliases[path]))
            if path == "string":
                # `string` 是 `Text` 的别名，而 `Text` 的运行时形态就是
                # `vector<u8>`（UTF-8 字节序列）——归一成它，`std::string s = "abc";`
                # 才能既**过检查**又**真能跑**（见 §8.8）。
                return self.parse_type_ref_suffix(self._string_type_node(t))
            if path == "pair":
                raise ParseError(
                    "E-Type-NoPair",
                    "本语言没有 `pair` 类型（`std::pair` 认得名字，但语义没接，R2 才做）："
                    "两元组写 `(K, V)`，取元素用 `.0` / `.1`。\n"
                    "写 `map` 用 `m[k] = v`（`m.insert({k, v})` 的花括号构造也在 R2）。", t)
            node = Node("type-name", t.line, t.col, name=path)
            if path == "BoundedVector":
                self.error("E-Type-OldBounded",
                           "`BoundedVector[N, T]` 已下线：v0.68 起写 "
                           "`array<T, N>`（定长，N 是容量）", t)

            # C++ 风格泛型：Vec<T> / FixedVec<T, N> / Opt<T>
            if self.at(Kind.LT) and self._is_generic_open():
                self.advance()
                args = self._parse_type_args(t.line, t.col)
                node = node.add(args)
                # `FixedVec<T, N>::make()` —— 静态成员调用，等价于 `.make`
                if self.at(Kind.PATHSEP):
                    self.advance()
                    node.name = f"{node.name}.{self.expect_ident('成员名').text}"
                return self.parse_type_ref_suffix(node)

            if self.at(Kind.LBRACKET):                  # T[A, B]（v0.67 旧写法）
                self.advance()
                while not self.at(Kind.RBRACKET) and not self.at(Kind.EOF):
                    node.add(self.parse_type())
                    if not self.accept(Kind.COMMA):
                        break
                self.expect(Kind.RBRACKET, "']'")
                self.error("E-Type-OldBounded",
                           "`T[...]` 已下线：可变长写 `vector<T>`，"
                           "定长写 `array<T, N>`（N 是容量，不是当前长度）", t)
            return self.parse_type_ref_suffix(node)

        raise ParseError("E-Parse-ExpectedType",
                         f"期望类型，实际是 {t.kind.value!r}", t)

    def _cxx_scalar_type(self):
        """当前是否是 C++ 标量拼写；是则返回 `(本语言类型名, 消耗 token 数)`。

        支持连写（`unsigned long long` = 3 个词），**从长到短**匹配
        （`long long` 必须先于 `long` 命中）。`char` 在词法层是 `Kind.CHAR`
        （与字符字面量共用），所以扫词时两种 token 都收。
        """
        words: List[str] = []
        j = 0
        while j < 4:
            kk = self.kind(j)
            txt = self.peek(j).text or ""
            if kk == Kind.IDENT and txt in CXX_SCALAR_WORDS:
                words.append(txt)
            elif kk == Kind.CHAR and txt == "char":
                words.append(txt)
            else:
                break
            j += 1
        for n in range(len(words), 0, -1):
            key = " ".join(words[:n])
            if key in CXX_SCALAR_TYPES:
                return CXX_SCALAR_TYPES[key], n
        return None

    def _normalize_type_path(self, path: str, tok: Token) -> str:
        """把写出来的类型名归一（R1-A）：`std::` 前缀、宽度别名、"短名要 using"。

        **只改名字，不改值域**：`std.vector` → `vector` 之后，类型比较
        （`.tu` 的 `spec` vs `.eln` 的实现）看到的是同一个字符串——这正是
        `E-Spec-SigMismatch: .tu=(vector<u64>&) vs .eln=(std.vector<u64>&)` 的修法。
        """
        had_std = False
        if path.startswith("std."):
            rest = path[4:]
            if rest not in STD_TYPE_NAMES:
                raise ParseError(
                    "E-Type-UnknownStdName",
                    f"本语言不认识 `std::{rest}`：`std::` 下只认 "
                    f"`std::vector` / `std::map` / `std::set` / `std::unordered_map` / "
                    f"`std::unordered_set` / `std::array` / `std::optional` / "
                    f"`std::expected` / `std::string` / `std::pair` 与宽度名 "
                    f"（`std::size_t` / `std::uint64_t` …）。\n"
                    f"改法：容器/结果类型写上面这些；`std::cout` / `std::sort` 这类"
                    f"是表达式与算法，不在类型位置（数组打印用 `print`）。", tok)
            path = rest
            had_std = True
        # **短名必须写在 `using namespace std;` 之后**（§8.3 定稿）。
        if (REQUIRE_USING_FOR_SHORT_NAMES and not had_std and not self.using_std
                and path in STD_GATED_NAMES):
            raise ParseError(
                "E-Type-MissingUsing",
                f"短名 `{path}` 前面必须先写模块级 `using namespace std;`"
                f"（§8.3 定稿）。\n"
                f"改法：① 类型写全 `std::{path}`；"
                f"② 在文件顶部写 `using namespace std;`（R1 里两者等价）。", tok)
        return CXX_WIDTH_ALIASES.get(path, path)

    def _string_type_node(self, tok: Token) -> Node:
        """`string` / `std::string` → `vector<u8>`（`Text` 的运行时形态）。

        `Text` 是**主名**、`string` 是别名（§8.8），但 `Text` 这个名字在
        类型检查里还没接线（R2），而 `vector<u8>` 今天就能跑（`Text` 的运行时
        就是它：`src/interp.py` 的 `_convert_to` 把 `vector<u8>` 盖成 `Text`）。
        所以这里归一成 `vector<u8>`——**能 check 也能 run**，不是"接受但没用"。
        """
        u8 = Node("type-name", tok.line, tok.col, name="u8")
        args = Node("type-args", tok.line, tok.col).add(u8)
        return Node("type-name", tok.line, tok.col, name="vector").add(args)

    def parse_type_ref_suffix(self, base: Node) -> Node:
        """v0.68：`T&` —— `&` 跟在类型后，表示引用。

        2026-11-10 起同一位置接受 **`T*` 指针**（①：`char** argv` /
        `const Json*` / `Json* const`）。

        **空格不再是判据**（2026-11-10②）：`u64 * p` 在 C++ 里就是指针声明，
        格式化工具也普遍这么排。类型位置（形参 / 返回类型 / 字段 / 已经判定
        为声明的语句）根本没有第二种读法，所以 `*` 与类型名之间有没有空格都收
        （`char ** argv` 也一样）。

        唯一需要「看左边」的是**语句起点**：那里 `a * b;` 也是合法语句。
        判据放在 `parse_stmt`（`_at_type_prefixed_decl` +
        `_at_unknown_spaced_ptr_decl`），本函数用 `_spaced_ptr_ok` 配合：
        语句起点的试解把它关掉，于是只有**已知类型名**后面的空格 `*` 才被
        收进类型，`a * b` 留作乘法。
        """
        while True:
            if self.at(Kind.AMP):
                t = self.advance()
                if self.kind() == Kind.IDENT and self.peek().text == "mut":
                    self.advance()
                    self.error("E-Ref-OldMut",
                               "`&mut` 已下线：v0.68 起写 `T&`", t)
                ref = Node("type-ref", base.line, base.col).add(base)
                base = ref
                continue
            if self.at(Kind.STAR) or self.at(Kind.STAR_STAR):
                if not self._star_attached():
                    # **有空格**：只有类型位置、或左边已经是已知类型/已知指针
                    # 才收（`u64 * p` / `char ** argv`）；语句起点的 `a * b`
                    # 因为 `a` 不是已知类型，在这里断开、留给表达式。
                    if not (self._spaced_ptr_ok or self._type_node_known(base)):
                        break
                star = self.peek()
                n = 2 if self.at(Kind.STAR_STAR) else 1
                self.advance()
                for _ in range(n):
                    base = Node("type-ptr", star.line, star.col,
                                value={"pointee_const": False,
                                       "ptr_const": False}).add(base)
                # **`Json* const p`**：const 指针（绑定不可变，指向可变）。
                # 只在 `*` 之后、且后面紧跟名字时认——`const` 出现在别的
                # 位置（类型实参 / 返回类型后面）不在这里消费。
                if (self.at(Kind.CONST) and self.kind(1) == Kind.IDENT):
                    self.advance()
                    if isinstance(base.value, dict):
                        base.value["ptr_const"] = True
                continue
            break
        return base

    def _star_attached(self) -> bool:
        """当前 `*` / `**` 是否**紧贴**前一个记号（`u64*` 是，`u64 *` 不是）。"""
        if self.i <= 0:
            return False
        prev = self.toks[self.i - 1]
        return prev.end == self.peek().start

    def _mark_pointee_const(self, node: Node, tok: Token) -> Node:
        """`const T*`：把 const 记到**指针的指向**上（不是绑定不可变）。"""
        if node is not None and node.kind == "type-ptr":
            if not isinstance(node.value, dict):
                node.value = {"pointee_const": False, "ptr_const": False}
            node.value["pointee_const"] = True
        return node

    # ---------------------------------------------- 消歧：类型名 / 值名（2026-11-10②）

    def _type_name_known(self, name: Optional[str]) -> bool:
        """`name` 是不是**这一行之前**已经看得见的类型名（指针消歧用）。

        只见名不见类型：内置类型 / 本文件前面声明的 `struct` / `enum` /
        `error` / 当前函数的泛型参数。判据与 `typecheck._type_head_known`
        同口径，但**不做跨文件解析**——`.tu` 里的类型 parser 看不见，
        `MyStruct * q` 仍按声明解析（交给 typecheck 判真伪）。
        """
        raw = (name or "").strip()
        if not raw:
            return False
        # `std::vector` / `std.vector`：parser 内部用 `.` 拼路径
        head = raw[4:] if raw.startswith("std.") else raw
        if head in BUILTIN_TYPE_NAMES or head in self.known_types \
                or head in self.type_params:
            return True
        return False

    def _type_node_known(self, node: Optional[Node]) -> bool:
        """一个**已经解析出来的类型节点**是不是"已知类型"（用于空格 `*` 的门槛）。

        `type-ptr` / `type-ref` / `type-tuple` … 都是**已经确定是类型**的节点，
        不是靠名字猜，直接算已知——这样 `char ** argv` 的第二个 `*` 不会因为
        "左边是 type-ptr 不在词表里"而被拒。
        """
        if node is None:
            return False
        if node.kind == "type-name":
            return self._type_name_known(node.name)
        return node.kind in ("type-ptr", "type-ref", "type-slice", "type-tuple",
                             "type-fn", "type-const")

    def _type_name_at_start(self) -> Optional[str]:
        """当前位置（语句起点）那个类型名的**头**；不是类型形状就 None。

        `unsigned long long * n` → `u64`（C++ 标量拼写先归一）；
        `char * s` → `u8`；`std::vector<u64> * xs` → `vector`。
        """
        _scalar = self._cxx_scalar_type()
        if _scalar is not None:
            return _scalar[0]
        if self.at(Kind.IDENT):
            name = self.peek().text or ""
            if (name == "std" and self.kind(1) == Kind.PATHSEP
                    and self.kind(2) == Kind.IDENT):
                name = self.peek(2).text or name
            return name
        return None

    def _is_generic_open(self) -> bool:
        """`<` 是泛型开括号还是小于号：向前看，若在 `>` 之前先遇到 `;`/`{` 就判为比较。"""
        depth = 0
        j = 0
        while self.kind(j) != Kind.EOF:
            k = self.kind(j)
            if k == Kind.LT:
                depth += 1
            elif k == Kind.GT:
                depth -= 1
                if depth == 0:
                    return True
            elif k in (Kind.SEMI, Kind.LBRACE, Kind.RBRACE):
                return False
            j += 1
        return False

    def parse_path(self, what: str) -> str:
        t = self.peek()
        if not (self.at(Kind.IDENT) or self.at(Kind.SELF) or self.at(Kind.DYN)):
            raise ParseError("E-Parse-ExpectedPath", f"期望 {what}", t)
        parts = [self.advance().text]
        # v0.69：类型命名空间用 `::`（枚举变体、静态成员），`.` 只用于值上的成员。
        # 路径内部一律用 `.` 拼成一个名字，由后续解析阶段区分。
        while ((self.at(Kind.DOT) or self.at(Kind.PATHSEP))
               and self.kind(1) == Kind.IDENT):
            self.advance()
            parts.append(self.advance().text)
        return ".".join(parts)

    # ---------------------------------------------------------- 块与语句

    def parse_block(self) -> Node:
        t = self.expect(Kind.LBRACE, "'{'")
        node = Node("block", t.line, t.col)
        # 第 13 条：进出一个块就是一层作用域（只在"接收者是什么类型"这件事上
        # 有意义；`compat=False` 时 `_scopes` 里只有空 dict，不产生任何影响）。
        self._scope_push()
        try:
            while not self.at(Kind.RBRACE) and not self.at(Kind.EOF):
                mark = self.i
                try:
                    # 第 9 条：`T a = 1, b = 2;` 在这里展开成两个兄弟节点。
                    for _stmt in self.parse_stmt_list():
                        node.add(_stmt)
                except ParseError as e:
                    self.error(e.code, e.message, e.tok)
                    if self.i == mark:
                        self.advance()
                    skip_from = self.peek()
                    self.skip_to_stmt_end()
                    self._report_skipped(skip_from)
            self.expect(Kind.RBRACE, "'}'")
        finally:
            self._scope_pop()
        return node

    def skip_to_stmt_end(self):
        depth = 0
        while not self.at(Kind.EOF):
            k = self.kind()
            if k in (Kind.LPAREN, Kind.LBRACKET, Kind.LBRACE):
                depth += 1
            elif k in (Kind.RPAREN, Kind.RBRACKET):
                depth = max(0, depth - 1)
            elif k == Kind.RBRACE:
                if depth == 0:
                    return
                depth -= 1
            elif k == Kind.SEMI and depth == 0:
                self.advance()
                return
            self.advance()

    # ---------------------------------------------------- 退役关键字：位置判据

    def _at_end_terminator(self) -> bool:
        """当前位置是不是**旧块终结符** `end <kind>`（v0.68 已下线）。

        判据是**位置**而不是词形（2026-11-10）：

        * 前面一个 token 是块收尾 `}`（旧写法 `} end fn`），**或**
        * 后面一个 token 是种类名（`end fn` / `end if` / `end match` …）。

        `xs.end()`（前一个 token 是 `.`）、`size_t end`（前一个 token 是 `,`）、
        `s[end]`（前一个 token 是 `[`）、`u64 end = 3;`（前一个 token 是类型名）
        都不满足，因此 `end` 当标识符用一律放行。`end` 后面跟一个普通标识符
        （不是种类名）也不算终结符——那更可能是"类型 `end` + 变量名"，
        交给正常文法判。
        """
        if not (self.at(Kind.IDENT) and (self.peek().text or "") == "end"):
            return False
        if self.i > 0 and self.toks[self.i - 1].kind == Kind.RBRACE:
            return True
        nxt = self.peek(1)
        if nxt is None:
            return False
        text = nxt.text or ""
        return text in END_KIND or text == "func"

    def _consume_end_terminator(self, t: Token) -> Node:
        """吃掉旧块终结符 `end [种类名]` 并报 `E-End-Removed`（不产生级联）。

        只吃**种类名**（`fn` / `func` / `if` / `while` / `match` …），
        不无条件吃后面的标识符——否则 `end` 后面那条语句的第一个名字会被吞掉。
        """
        self.advance()                                  # `end`
        kt = self.peek()
        if (kt.text or "") in END_KIND or (kt.text or "") == "func":
            self.advance()
        self.error("E-End-Removed", f"`end` 已下线：{RETIRED_HINT['end']}", t)
        return Node("block", t.line, t.col)

    # ---------------------------------------------------- 语句起点：未知构造

    #: 这些形状说明当前标识符只是**普通变量名**，不是不支持的 C++ 构造：
    #: `new = 1;`（赋值）、`this.x = 1;`（成员访问）。见
    #: `UNSUPPORTED_STMT_WORDS` 的判据说明。
    _STMT_WORD_PLAIN_TAIL = {
        Kind.EQ, Kind.PLUS_EQ, Kind.MINUS_EQ, Kind.STAR_EQ, Kind.SLASH_EQ,
        Kind.DOT,
    }

    def _unsupported_stmt_message(self, t: Token) -> Optional[str]:
        """语句 / 顶层 item 起点的**已知 C++ 关键字** → 专属诊断（含改法）。

        只在形状**不可能是**合法语句时返回消息（见 `UNSUPPORTED_STMT_WORDS`
        顶部注释）：`T x;` 这类未知类型名声明不在表里；`new = 1;` / `this.x = 1;`
        是合法赋值 / 成员访问，直接放行。
        """
        spec = UNSUPPORTED_STMT_WORDS.get(t.text or "")
        if spec is None:
            return None
        # **第 15 条**（P0 兼容层）：类型位置的**详细类型说明符**
        # `class S* p = …` / `const class S& r = …`。`class` 与 `struct` 同义，
        # `parse_type` 会把 `class` 跳掉；这里必须放行，否则语句起点会先撞上
        # 「本语言没有 `class`」。只在后面跟名字时放行（`class` 当变量名的形状
        # 仍按老路径：`class = 1;` 之类）。
        if ((t.text or "") == "class" and self.compat
                and self.kind(1) == Kind.IDENT):
            return None
        # **第 13 条**：成员体里的 `this->x = …` 由 `E-Compat-MemberThis` 给更准的
        # 说法（"降级后接收者是显式形参 recv"），这里放行让它走到表达式解析。
        if (t.text or "") == "this" and self.compat and self._member_ctx is not None:
            return None
        n1 = self.kind(1)
        if n1 in self._STMT_WORD_PLAIN_TAIL:
            return None
        what, how = spec
        return f"本语言没有 `{t.text}`：{what}。\n改法：{how}"

    def parse_stmt_list(self) -> List[Node]:
        """解析**一条**语句，返回它展开出的**兄弟节点列表**。

        99% 的语句只产出一个节点；`--compat` 下有两个例外，两者都是
        "语义上就是两条语句，所以 AST 里也必须是两个兄弟节点"：

        * 第 9 条 `T a = x, b = y;` → 多个 `var-decl`（与手写多行同形）；
        * 第 13 条 `Ring r(3);` → `Ring r;` + `Ring_Ring(r, 3);`。

        另外第 13 条还会**补**一句默认构造函数调用（`Ring r;` 而该 struct 有
        0 参构造函数时）。其余情况与以前逐字节相同。
        """
        if self.compat and self._compat_at_multi_decl():
            stmts = self._compat_parse_multi_decl()
        elif self.compat and self._compat_at_struct_ctor_decl():
            stmts = self._compat_parse_struct_ctor_decl()
        else:
            stmts = [self.parse_stmt()]
        if self.compat:
            # 第 13 条：`Ring r;` + 0 参构造函数 → 补 `Ring_Ring(r);`
            stmts = self._compat_fill_default_ctor(stmts)
        # 第 12 条：形状③（`it` 在外声明）降级后 `it` 不再被写，
        # 循环之后读到它是**静默错**——在语句边界上检查（见该方法）。
        if self.compat and self._iter_escapes:
            self._iter_for_report_escape(stmts)
        return stmts

    def parse_stmt(self) -> Node:
        t = self.peek()
        k = t.kind

        # **退役关键字（v0.68）**：词法器只出 `IDENT`（2026-11-10），
        # 判据是**语法位置**，不是词形：
        #   · `end` 只在块终结符位置（紧跟 `}` 或后跟种类名）→ `E-End-Removed`；
        #   · `val` / `var` / `let` 只在"语句起始 + 后跟标识符"的旧声明形状；
        #   · `fn` 同理（旧函数声明形状）。
        # 反例（**合法标识符**，必须放行）：`auto it = xs.end();`、
        # `u64 end = 3;`、`long long val = …`、`fn(x)` 调用。
        if k == Kind.IDENT:
            if self._at_end_terminator():
                return self._consume_end_terminator(t)
            if (t.text or "") in OLD_DECL_WORDS and self.kind(1) == Kind.IDENT:
                return self.parse_let()
            if (t.text or "") == "fn" and self.kind(1) == Kind.IDENT:
                raise ParseError("E-Func-KeywordRemoved",
                                 f"`fn` 已下线：{RETIRED_HINT['fn']}", t)
        if k == Kind.CONST:
            # **`const T* p` 不是「const 绑定」**：C++ 里它指「指向 const 的
            # 指针」，指针本身可改指向（`p = &other;` 合法）。所以按类型判：
            # 试解一个类型，若最外层是 `T*`（const 在**指向**上）就走类型前置
            # 声明；否则仍是 `const u64 x = 5;` 那条不可变绑定。
            if self._const_ptr_decl_ahead():
                return self.parse_typed_decl()
            # **第 3 条**：`const auto n = xs.length();`（与 `auto n = …` 同一套推断）
            if self._compat_const_auto_ahead():
                return self._compat_parse_auto_decl(is_const=True)
            return self.parse_const_decl()
        # **`using` 在函数体里**（R1-A）：`using namespace std;` 在 C++ 里**可以**
        # 写在函数内（只影响这个块），所以照收——本语言一个文件一个全局作用域，
        # 等价于写在文件顶部，不产生语句（空块占位，下游零改动）。
        # **第 2 条**：`using X = Y;` 在 `--compat` 下同样收——它**不进 AST**，
        # 这里返回一个**空块**当占位（不是语句，κ 贡献为 0）。
        if k == Kind.IDENT and (t.text or "") == "using":
            if self.kind(1) == Kind.IDENT and (self.peek(1).text or "") == "namespace":
                self.parse_using()
                return Node("block", t.line, t.col)
            if self.compat and self.kind(1) == Kind.IDENT and self.kind(2) == Kind.EQ:
                self.parse_using()
                return Node("block", t.line, t.col)
            raise ParseError(
                "E-Keyword-Unsupported",
                "本语言没有类型别名（`using T = …;` / `typedef`）。\n"
                "改法：类型写全（`std::vector<u64>`）；泛型直接写在函数名后："
                "`u64 pick<T>(T a) { … }`。\n"
                "（`using namespace std;` 是接受的，写在文件顶部或函数里都行。）", t)
        # **`typedef struct … X;` 只能写在模块顶层**（2026-11-13）：本语言的
        # struct 是**模块级类型声明**，函数体里没有"定义类型"这个语句。C 里
        # 允许局部 typedef struct，所以这里给一条说清位置与改法的诊断，而不是
        # 让 `struct` 节点混进语句表（那会把下游 walk / typecheck 弄出半截状态）。
        # **必须排在下面的 `--compat` 别名分支之前**：否则 compat 模式下它会
        # 走进 `parse_typedef`，把 struct 当语句塞进块里。
        if (k == Kind.IDENT and (t.text or "") == "typedef"
                and self.kind(1) == Kind.STRUCT):
            raise ParseError(
                "E-Keyword-Unsupported",
                "`typedef struct { … } X;` 只能写在**模块顶层**：本语言的 struct "
                "是模块级类型声明，函数体里没有「定义类型」这个语句。\n"
                "改法：把这一整段 struct 提到文件顶部（放在用它的函数之前），"
                "函数体里直接用 `X`。", t)
        # **第 2 条**：`typedef X Y;` 写在函数体里（少见，但 C 里合法）
        if k == Kind.IDENT and (t.text or "") == "typedef" and self.compat:
            self.parse_typedef()
            return Node("block", t.line, t.col)
        # **第 3 条**：`auto x = expr;`（局部类型推断，推断不出报错）
        if k == Kind.IDENT and self.compat and self._compat_at_auto_decl():
            return self._compat_parse_auto_decl(is_const=False)
        # **语句起点的未知构造**（2026-11-10）：`throw` / `static` / `using` 这些
        # C++ 关键字不是本语言的关键字，语句起点的标识符又会被当成**类型名**，
        # 于是每个都变成一句「期望 ';'，实际是 'ident'」。这里先给专属诊断，
        # 再走原有分支（`T x;` 这类未知类型名**不**受影响，见表头注释）。
        if k == Kind.IDENT and (t.text or "") == "nullptr":
            raise ParseError(
                "E-Keyword-Unsupported",
                "本语言没有 `nullptr;` 这种语句：`nullptr` 是**空指针值**，"
                "只能出现在表达式里（`u64* p = nullptr;` / "
                "`if (p != nullptr) { … }`）。\n"
                "改法：把它写进一个有类型的表达式，或删掉这一句。", t)
        if k == Kind.IDENT or k == Kind.EXTERN:
            _msg = self._unsupported_stmt_message(t)
            if _msg:
                raise ParseError("E-Keyword-Unsupported", _msg, t)
        # **`char` 类型**（R1-A 起接受）：`char` 与字符字面量共用 `Kind.CHAR`，
        # 裸 `char` 一定是在写类型。以前这里直接报 `E-Type-NoChar`（"没有 char"），
        # 现在归一成 `u8` —— `char c = s[0];` 与 C++ 同形，由 `parse_type` 处理。
        # **不再需要专门分支**：下面的 `_at_type_prefixed_decl()` 会试解出类型。
        # **`goto` / 标签不是关键字，但必须明确拒绝**（2026-11-10）。
        #
        # 以前 `goto done;` 走下面 `_at_type_prefixed_decl()` 那条路：`goto` 是
        # 标识符、`done` 也是标识符、后面是 `;`——于是被当成 **`var-decl`**：
        # 类型名 `goto`、变量名 `done`（见 AST 证据：`type-name goto @1:59`）。
        # 它不报错、也不做事，程序照样"跑对"——**比语法错误更危险**。
        #
        # 这里用 `E-Keyword-Unsupported`（不是 E-Parse-*）：用户写的是一个
        # **本语言没有的构造**，不是拼错了语法；给的是改法。
        if k == Kind.IDENT and t.text == "goto":
            raise ParseError(
                "E-Keyword-Unsupported",
                "本语言没有 `goto`：跳转用 `while` / `break` / `continue` / `return`。"
                "（`goto` 不是关键字——以前 `goto done;` 会被静默解析成变量声明"
                "「类型名 goto、变量名 done」，什么也不做。）", t)
        # **`do { } while (…)` 不存在**（2026-11-10）。
        #
        # 以前 `do` 走 `_at_type_prefixed_decl()`：`do` 是标识符、后面跟 `{` 之前
        # 还有个变量名……于是被当成「类型名 do + 变量名」，报一句
        # 「期望 ';'，实际是 '{'」——C++ 读者只会以为自己少了个分号。
        # `do` 不是本语言的关键字（与 `goto` 同口径，不进 KEYWORDS），但在**语句
        # 起点**必须明确拒绝，并说清改法与代价（`while (true)` 会让 κ 退成 ⊤）。
        if k == Kind.IDENT and t.text == "do":
            raise ParseError(
                "E-Keyword-Unsupported",
                "本语言没有 `do { } while (…)`：语句表里没有 `do`。"
                "（`do` 不是关键字——以前它被当成类型名，错报成「期望 ';'，实际是 '{'」。）\n"
                "改法：`while (true) { 体; if (!(cond)) { break; } }`，"
                "条件写在末尾并取反。\n"
                "**代价**：`while (true)` 推不出循环上界 —— `elench check` 会给 "
                "`E-While-NoBound`，`elench cost` 显示 `κ ≤ ⊤`（契约判不了，不是判失败）。"
                "要对上界有要求，就把守卫写成尺寸比较（`while (i < n)`）并在体内推进。",
                t)
        # `done:` —— 标签同样不存在。以前 `done:` 报的是一句「期望 ';'，实际是 ':'」，
        # 读者看不出"本语言没有标签"，所以这里给同一类明确诊断。
        if k == Kind.IDENT and self.kind(1) == Kind.COLON:
            raise ParseError(
                "E-Keyword-Unsupported",
                f"本语言没有标签语法：`{t.text}:` 不是跳转目标，"
                "也不会有 `goto` 跳过来。跳转用 `while` / `break` / `continue` / `return`。",
                t)
        # **C 数组 `T a[] = {…}`**（2026-11-10）：以前在 `a` 处报
        # 「期望 ';'，实际是 'ident'」（`_at_type_prefixed_decl` 不认 `[ ]`）。
        if k == Kind.IDENT and self.kind(1) == Kind.IDENT \
                and self.kind(2) == Kind.LBRACKET and self.kind(3) == Kind.RBRACKET:
            raise ParseError(
                "E-Type-NoCArray",
                "本语言没有 C 数组 `T a[]`：数组长度不写在变量上。\n"
                "改法：可变长写 `vector<u64> a = {1, 2, 3};`，"
                "定长写 `array<u64, 3> a = {1, 2, 3};`（N 是容量）。", t)
        # **`u64 * p;`（有空格）也是声明**（2026-11-10②）：判据是**左边的名字
        # 是不是已知类型名**，不是空格。已知类型的写法先由
        # `_at_type_prefixed_decl` 接走；剩下「左边不是已知类型名」的歧义形状
        # 再判：左边是已知变量就是乘法，两边都不是已知名字就按声明（见函数注释）。
        if self._at_type_prefixed_decl():
            return self.parse_typed_decl()
        if self._at_unknown_spaced_ptr_decl():
            return self.parse_typed_decl()
        if k == Kind.LBRACE:
            # **裸块**：`parallel { {a} {b} }` 里每个 `{…}` 是一个**任务**。
            # 没有它就只能靠"语句"猜任务边界，B-23 的"任务数静态可见"落不了地。
            return self.parse_block()

        if k == Kind.IF:
            return self.parse_if()
        if k == Kind.WHILE:
            return self.parse_while()
        if k in (Kind.BREAK, Kind.CONTINUE):
            # **`break;` / `continue;`**（v0.72）：词法以前根本没有这两个关键字，
            # parser 把它们当路径，运行期报"未绑定的名字 break"。
            kw = self.advance()
            self.expect(Kind.SEMI, "';'")
            return Node("break" if kw.kind == Kind.BREAK else "continue",
                        kw.line, kw.col)
        if k == Kind.PARALLEL:
            return self.parse_parallel()
        if k == Kind.FOR:
            return self.parse_for()
        if k == Kind.MATCH:
            return self.parse_match()
        if k == Kind.RETURN:
            self.advance()
            node = Node("return", t.line, t.col)
            if not self.at(Kind.SEMI):
                node.add(self.parse_expr())
                self._reject_positional_init()
            self.expect(Kind.SEMI, "';'")
            return node
        if k == Kind.SCOPE:
            raise ParseError("E-Scope-Removed",
                             "`scope` 已下线：它存在的唯一意义是给 `spawn` 提供词法边界，"
                             "而 `spawn` 也一并下线了。改用 `parallel { … }` 表达\"这几块可以并行\"。", t)
        if k == Kind.SPAWN:
            raise ParseError("E-Spawn-Removed",
                             "`spawn` 已下线：并发下的时间不是一个确定的上界（取决于核数与调度），"
                             "κ 会因此不再确定，而那正是本语言要保证的东西。\n"
                             "改法：① 需要\"可并行\"的标注就用 `parallel { … }`（顺序语义，成本保守）；\n"                             "      ② 任务级并发不在语言范围内（记为有意非目标）。", t)
        if k == Kind.ASM:
            raise ParseError("E-Asm-Removed",
                             "`asm` 已下线：内联汇编里的操作数不可数（`rep movsb` 是几条操作取决于长度），"
                             "κ 就会从\"可验证\"降级成\"可估算\"。\n"
                             "改法：① 需要底层操作就用已声明的外部函数（`external`/`extern`）；\n"                             "      ② 内联汇编记为有意非目标。", t)
        if k == Kind.UNSAFE:
            self.advance()
            blk = self.parse_block()
            # **不新开 Node kind**：仍然是个 block，只打一个标记。
            # 这样 interp / cost_interp 一行都不用改，只有检查器看得见它。
            blk.value = "unsafe"
            return blk

        # **`++i;` / `i--;`：独立语句形态的自增自减**（2026-11-10）
        #
        # `++` / `--` 现在是**词法 token**（2026-11-11），这里只按 token 分流，
        # 并 desugar 成 `i = i + 1`（或 `- 1`）——**只在 parser 做**，产出的 AST
        # 与手写 `i = i + 1` 完全同形，interp / 成本分析一行都不用改。
        if self._at_incdec() is not None:
            return self._parse_incdec_stmt()

        # 赋值或表达式语句。
        # 先按「前缀 + 后缀」解析一个位置表达式：句末若是 `++` / `--`，就是
        # 后缀独立语句（`i++;` / `xs[i]--;`）；否则把它当作 `parse_expr` 的
        # 左操作数继续——**只解析一次**，不重复走词法也不丢诊断。
        place = self.parse_postfix(self.parse_prefix())
        if self._at_incdec() is not None:
            return self._parse_incdec_suffix(place)
        # **第 19 条**（P0 兼容层）：`|=` `&=` `^=` `%=` `<<=` `>>=`。
        # 必须在 `parse_expr` **之前**分流：`|` / `&` / `^` / `%` / `<<` / `>`
        # 都是合法二元运算符，交给表达式解析器会在随后的 `=` 处报
        # `E-Parse-ExpectedExpr`——那正是这条缺口以前的样子。
        if self.compat:
            _comp = self._compat_compound_ahead()
            if _comp is not None:
                return self._compat_parse_compound_assign(place, _comp)
        expr = self.parse_expr(left=place)
        if self.at(Kind.EQ):
            self.advance()
            rhs = self.parse_expr()
            self.expect(Kind.SEMI, "';'")
            self._require_place(expr, t)
            return Node("assign", t.line, t.col).add(expr, rhs)
        if self.at(Kind.PLUS_EQ) or self.at(Kind.MINUS_EQ) \
                or self.at(Kind.STAR_EQ) or self.at(Kind.SLASH_EQ):
            op = self.advance()
            rhs = self.parse_expr()
            self.expect(Kind.SEMI, "';'")
            self._require_place(expr, t)
            n = Node("assign-op", t.line, t.col, value=op.kind.value)
            return n.add(expr, rhs)
        self.expect(Kind.SEMI, "';'")
        return Node("expr-stmt", t.line, t.col).add(expr)

    PLACE_KINDS = ("path", "field", "index", "deref")

    def _require_place(self, node: Node, tok: Token):
        """赋值左侧必须是可赋值的位置（变量 / 字段 / 下标 / 解引用）。"""
        if node.kind not in self.PLACE_KINDS:
            raise ParseError("E-Assign-NotPlace",
                             f"赋值左侧不是可赋值位置（实际是 {node.kind}）", tok)

    # ------------------------------------------ 第 19 条：复合赋值（拼写糖）

    def _compat_compound_ahead(self) -> Optional[str]:
        """当前位置是复合赋值吗？是就返回**展开后的二元运算符**（`"|"` …）。

        判据：两个 token **紧邻**（`a.end == b.start`）且命中
        `_COMPAT_COMPOUND_TOKENS`。`| =`（有空格）不是复合赋值——它本来也
        不是合法程序，但"相邻"让判据只认 C++ 的那一种拼写。
        """
        op = _COMPAT_COMPOUND_TOKENS.get((self.kind(), self.kind(1)))
        if op is None:
            return None
        if self.peek().end != self.peek(1).start:
            return None
        return op

    def _compat_parse_compound_assign(self, place: Node, op: str,
                                      semi: bool = True) -> Node:
        """`x op= y` → **与手写 `x = x op y` 同形的 AST**。

        形状：`assign(place, binary(op)(deepcopy(place), rhs))`。左操作数深拷贝
        一份（AST 必须是一棵树，不能共用 `Node` 对象）。`semi=False` 用于
        三段式 `for` 的步进段（那里没有 `;`）。
        """
        t = self.peek()
        self.advance()                 # 二元运算符那半（`|` / `<<` / `>` …）
        self.advance()                 # `=` 那半（`>>=` 是 `>=`）
        rhs = self.parse_expr()
        if semi:
            self.expect(Kind.SEMI, "';'")
        self._require_place(place, t)
        rhs_place = copy.deepcopy(place)
        binary = Node("binary", t.line, t.col, value=op).add(rhs_place, rhs)
        return Node("assign", place.line, place.col).add(place, binary)

    # ---------------------------------------------------------- 自增 / 自减

    def _at_incdec(self) -> Optional[int]:
        """当前位置是否是 `++` / `--` **token**。返回 +1 / -1 / None。

        **2026-11-11 根因修复**：以前词法没有 `++` / `--`，`i++` 是 `i` `+` `+`
        三个 token，这里靠「两个 `+` 的源码位置相邻」猜——AST 里没有"相邻"
        这种信息，转译到 C++ 时输出不确定（`i + +j` 与 `i++` 只差一个空格）。
        现在自增自减是独立 token（见 `lexer.Kind.INC/DEC`），这里**只看 token**，
        不再读源码间距；有空白的一对 `+` 是**两个 `+` token**，永远进不来。
        """
        k = self.kind()
        if k == Kind.INC:
            return 1
        if k == Kind.DEC:
            return -1
        return None

    def _desugar_incdec(self, place: Node, step: int, op_tok: Token) -> Node:
        """`++i;` / `i++;` / `--i;` / `i--;` → **与 `i = i + 1` 同形的 AST**。

        形状：`assign(path i, binary("+" | "-")(path i, lit-int 1))`。
        RHS 里的左操作数是 LHS 的**深拷贝**：AST 必须是一棵树，共用一个 `Node`
        对象会让"按 `id()` 建索引"的下游（例如哪些赋值落在循环体里）把两处当成一处。
        """
        line, col = getattr(place, "line", 0), getattr(place, "col", 0)
        rhs_place = copy.deepcopy(place)
        one = Node("lit-int", op_tok.line, op_tok.col, value=1)
        op = Node("binary", op_tok.line, op_tok.col,
                  value="+" if step > 0 else "-").add(rhs_place, one)
        return Node("assign", line, col).add(place, op)

    def _parse_incdec_stmt(self) -> Node:
        """前缀形态（语句）：`++i;` / `--i;`。"""
        t = self.peek()
        step = self._at_incdec()
        self.advance()                     # `++` / `--` 是**一个** token
        place = self.parse_postfix(self.parse_prefix())
        self._require_place(place, t)
        self.expect(Kind.SEMI, "';'")
        return self._desugar_incdec(place, step, t)

    def _parse_incdec_suffix(self, place: Node) -> Node:
        """后缀形态（语句）：`i++;` / `xs[i]--;`。

        句末必须是 `;`：`i++ + 1;` 这类"把自增当表达式用"的写法要给明确的
        「只能作独立语句」，不能静默解析成 `(i + (+1))` 之类的东西。
        """
        t = self.peek()
        step = self._at_incdec()
        self.advance()                     # `++` / `--` 是**一个** token
        # 句末必须是 `;`：`i++ + 1;` 这类"把自增当表达式用"的写法要给明确的
        # 「只能作独立语句」；但「忘了分号」（后面直接是 `}` / EOF）仍报缺分号。
        if not self.at(Kind.SEMI) and not self.at(Kind.RBRACE) and not self.at(Kind.EOF):
            raise ParseError("E-Unary-Unsupported", INC_DEC_NOT_EXPR, t)
        self.expect(Kind.SEMI, "';'")
        return self._desugar_incdec(place, step, t)

    def parse_const_decl(self) -> Node:
        """v0.68：`const Type name = expr;`（函数体内，类型前置）。"""
        t = self.expect(Kind.CONST, "'const'")
        if self.at(Kind.IDENT) and self.kind(1) == Kind.EQ:
            raise ParseError(
                "E-Decl-ConstNoType",
                "本语言**不做类型推断**：`const idx = 5;` 缺类型。\n"
                "改法：写 `const u64 idx = 5;`（类型在名字前）。\n"
                "理由：显式类型优于类型推断——契约与成本分析都依赖静态类型，"
                "不引入推断。", self.peek())
        if self.at(Kind.IDENT) and self.peek().text == "auto":
            _msg = self._unsupported_stmt_message(self.peek())
            if _msg:
                raise ParseError("E-Keyword-Unsupported", _msg, self.peek())
        stmt = Node("const-decl", t.line, t.col)
        stmt.value = {"mut": False}
        _ty_node = self.parse_type()
        stmt.add(_ty_node)
        self._reject_ptr_or_variadic()
        stmt.name = self.expect_ident("变量名").text
        self.value_names.add(stmt.name)
        # 第 13 条：登记类型（只服务"接收者是什么类型"）。
        self._scope_declare(stmt.name, _ty_node)
        # **允许只声明不初始化**（`vector<u64> xs;`）：C++ 里最普通的写法。
        # 语义是"默认构造"，由解释器按类型给初值（见 `_default_value_of`）。
        self._parse_decl_initializer(stmt, _ty_node)
        self.expect(Kind.SEMI, "';'")
        return stmt

    def _parse_init_expr(self) -> Node:
        """声明初始化位置：`= { ... }` 是**列表初始化**，否则是普通表达式。

        **为什么只在这里认 `{`**：块语句（`if/while/func` 的体、裸 `{}` 作用域）
        也以 `{` 开头。若把 `{` 提到表达式文法里，`parse_stmt` 遇到 `{` 就会先撞上
        列表初始化，把整块代码当元素列表吃掉。**声明初始化位置是唯一没有歧义的
        地方**：`Type name =` 之后不可能跟一个块（块不能当表达式）。
        """
        if not self.at(Kind.LBRACE):
            return self.parse_expr()
        t = self.advance()                      # '{'
        node = Node("list-init", t.line, t.col)
        if not self.at(Kind.RBRACE):
            node.add(self.parse_expr())
            while self.accept(Kind.COMMA):
                if self.at(Kind.RBRACE):        # 允许尾逗号 `{1, 2, 3,}`
                    break
                node.add(self.parse_expr())
        self.expect(Kind.RBRACE, "'}'")
        return node

    def parse_typed_decl(self) -> Node:
        """v0.68：`Type name = expr;`（函数体内，类型前置、可写）。

        `Json* const p`（const 指针）产出 `const-decl`（绑定不可变），
        其余产出 `var-decl`——**判断依据是类型节点上的 `ptr_const`**，
        不是名字里有没有 `const`。
        """
        t = self.peek()
        ty_node = self.parse_type()
        _is_const_ptr = (ty_node.kind == "type-ptr"
                         and isinstance(ty_node.value, dict)
                         and bool(ty_node.value.get("ptr_const")))
        stmt = Node("const-decl" if _is_const_ptr else "var-decl", t.line, t.col)
        stmt.value = {"mut": not _is_const_ptr}
        stmt.add(ty_node)
        self._reject_ptr_or_variadic()
        stmt.name = self.expect_ident("变量名").text
        # 语句起点的消歧要知道「这个名字是值」：`x * y;` 里 `x` 是变量 → 乘法
        # （不是 `x*` 指针声明）。见 `_at_unknown_spaced_ptr_decl`。
        self.value_names.add(stmt.name)
        # 第 13 条：登记类型（只服务"接收者是什么类型"）。
        self._scope_declare(stmt.name, ty_node)
        # **允许只声明不初始化**（`vector<u64> xs;`）：C++ 里最普通的写法。
        # 语义是"默认构造"，由解释器按类型给初值（见 `_default_value_of`）。
        self._parse_decl_initializer(stmt, ty_node)
        self.expect(Kind.SEMI, "';'")
        return stmt

    def _parse_decl_initializer(self, stmt: Node, ty_node: Node) -> None:
        """声明位的初值（**第 4 条**在这里落地）。

        | 写法 | 落到的节点 | 与哪条本语言写法**同形** |
        |---|---|---|
        | `T x = expr;` | 表达式节点 | 自己（原有路径） |
        | `T x = { … };` | `list-init` / **已知 struct 时** `struct-literal` | 容器是前者；struct 与 `T x{ … };` **同形** |
        | `T x{ … };`（`--compat`） | `list-init` / `struct-literal` | `T x = { … };`（struct）/ 容器列表初始化 |
        | `T x(args…);`（`--compat`，第 10 条） | 等价表达式（见 `_compat_ctor_expr`） | `T x = <等价表达式>;` |

        **第 4 条的 `= {…}` 分支（2026-11-12 补）**：C++ 的拷贝列表初始化
        `Point p = {3, 4};` 与 `Point p{3, 4};` 是同一件事。本文件里看得见
        `struct Point` 的字段顺序时，两者都降成同一个 `struct-literal`
        ——κ / run 因此与手写字段式**逐字节相同**。字段顺序看不见（struct 在
        `.tu`、或声明在使用之后）时**不在 parser 猜**：保留 `list-init`，
        由类型检查报 `E-Struct-PosInitUnsupported`（错位落值比报错危险）。

        `--compat` 关掉时**只走原有两条分支**，AST 一个字节都不变。
        """
        if self.compat and self.at(Kind.LPAREN):
            # **第 10 条**：`T x(args…)` 直接构造。等价表达式由
            # `_compat_ctor_expr` 给出；给不出就报 `E-Compat-CtorUnsupported`。
            lp = self.advance()                          # '('
            args = self.parse_args_until(Kind.RPAREN)
            self.expect(Kind.RPAREN, "')'")
            stmt.value["has_init"] = True
            stmt.add(self._compat_ctor_expr(ty_node, args, lp, name=stmt.name))
            return
        if self.compat and self.at(Kind.LBRACE):
            stmt.value["has_init"] = True
            stmt.add(self._compat_brace_initializer(ty_node, self.peek()))
            return
        stmt.value["has_init"] = self.at(Kind.EQ)
        if stmt.value["has_init"]:
            self.expect(Kind.EQ, "'='")
            # **第 4 条（2026-11-12 补）**：`S x = {1, 2};` 与 `S x{1, 2};`
            # 同形——只要**本文件里**看得见 `struct S` 的字段顺序。看不见时
            # 仍走 `_parse_init_expr()` 产出 `list-init`（不猜顺序），
            # 类型检查会明确拒绝（E-Struct-PosInitUnsupported）。
            if (self.compat and self.at(Kind.LBRACE)
                    and self._compat_struct_fields_of(ty_node) is not None):
                stmt.add(self._compat_brace_initializer(ty_node, self.peek()))
            else:
                stmt.add(self._parse_init_expr())
            self._reject_positional_init()

    def _const_ptr_fn_ahead(self) -> bool:
        """顶层 `const T* name(`（指针返回的 C++ 形状函数声明）。

        `const` 在这里修饰**指向**（`const Json*`），不是模块常量的不可变性；
        不先分流就会先撞上 `parse_const`，报一句「期望 '='，实际是 '('」。
        """
        save_i, save_d, save_s = self.i, len(self.diags), len(self.soft)
        try:
            node = self.parse_type()
            return (node is not None and node.kind == "type-ptr"
                    and self.at(Kind.IDENT) and self.kind(1) == Kind.LPAREN)
        except ParseError:
            return False
        finally:
            self.i = save_i
            del self.diags[save_d:]
            del self.soft[save_s:]

    def _const_ptr_decl_ahead(self) -> bool:
        """`const T* name` —— const 修饰的是**指向**，不是绑定。

        判据：从当前 `const` 起试解一个类型，最外层是 `type-ptr`，且后面紧接
        「名字 +（`=` 或 `;`）」。命中就交给 `parse_typed_decl`（`const` 由
        `parse_type` 消费并记到 `pointee_const`），不命中仍走 `const-decl`。
        """
        save_i, save_d, save_s = self.i, len(self.diags), len(self.soft)
        try:
            node = self.parse_type()
            return (node is not None and node.kind == "type-ptr"
                    and self.at(Kind.IDENT)
                    and self.kind(1) in (Kind.EQ, Kind.SEMI))
        except ParseError:
            return False
        finally:
            self.i = save_i
            del self.diags[save_d:]
            del self.soft[save_s:]

    def _at_unknown_spaced_ptr_decl(self) -> bool:
        """语句起点 `T * name`：**左边不是已知类型名**时怎么判（2026-11-10②）。

        语义变了：空格**不再是**判据。C++ 的规则是看**左边的名字**：

        | 左边 | 读法 | 说明 |
        |---|---|---|
        | 已知类型名（内置 / 前面声明的 struct） | 声明 | 不经过这里——`_at_type_prefixed_decl` 已经命中 |
        | 已知**值**名（形参 / 局部变量 / 常量） | 乘法 | `a * b` / `x * y` |
        | 两边都不是已知名字 | 声明 | `MyStruct * q`（`MyStruct` 可能在 .tu 里 / 在后面声明）：C++ 在这里会报"未知类型"，本语言先按声明解析，由 `typecheck` 报 `E-Type-Unknown`（消息里给"确认类型名声明在前面"的提示） |

        **为什么"两边都不是"要按声明**：`MyStruct * q;` 与 `a * b;` 在纯语法上
        完全同形，parser 没有符号表能区分"未知类型"与"外部值名"。C++ 自己
        也解析不了前向引用（`MyStruct` 未声明就报错），所以这里把决定权交给
        类型检查：真声明就过，真未知就报 `E-Type-Unknown`——**不会**再把
        `u64 * p;` 报成 `E-Pointer-Spacing`。

        回退（任务的"只有解析会失败时才用紧贴判定"）：两边都不是已知名字、
        **但按声明也解析不下去**（形状不是 `T * name (;|=)`）→ 报
        `E-Pointer-Spacing`，消息里说清"要指针声明就确认类型名已声明在前面"。
        """
        save_i, save_d, save_s = self.i, len(self.diags), len(self.soft)
        save_ok = self._spaced_ptr_ok
        save_values = set(self.value_names)
        save_tparams = set(self.type_params)
        self._spaced_ptr_ok = False
        try:
            head = self._type_name_at_start()
            try:
                self.parse_type()
            except ParseError:
                return False
            # 类型之后必须是**一串 `*`**，其中至少一个与左边有空格
            # （全紧贴的 `T* name` 已被 `_at_type_prefixed_decl` 收走）。
            spaced = False
            first_star: Optional[Token] = None
            while self.at(Kind.STAR) or self.at(Kind.STAR_STAR):
                if first_star is None:
                    first_star = self.peek()
                if self.i > 0 and self.toks[self.i - 1].end != self.peek().start:
                    spaced = True
                self.advance()
            if not spaced or first_star is None:
                return False
            # 之后必须是「名字 +（`=` 或 `;`）」——否则不是声明形状，交给表达式。
            # `T * const p` 的 `const` 是**指针本身**的 const（类型的一部分），
            # 与 `T* const p` 同义，也在这里收。
            _const_ptr = (self.at(Kind.CONST) and self.kind(1) == Kind.IDENT)
            _at = 1 if _const_ptr else 0
            if not (self.kind(_at) == Kind.IDENT
                    and self.kind(_at + 1) in (Kind.EQ, Kind.SEMI)):
                return False
            right = self.peek(_at).text or ""
            # ① 左边是已知类型名：声明（正常走不到这里，防御性保留）
            if self._type_name_known(head):
                return True
            # ② 左边是已知值名：`a * b` / `x * y` → 乘法
            if head in self.value_names:
                return False
            # ③ 右边已经是已知值名：更像"值 * 值"，也是乘法
            if right in self.value_names:
                return False
            # ④ 两边都不是已知名字：按**声明**解析；确认它真的解析得下去，
            #    解析不下去才回退到 E-Pointer-Spacing（任务里的回退规则）。
            self.i = save_i
            self._spaced_ptr_ok = True
            try:
                self.parse_typed_decl()
            except ParseError:
                raise ParseError(
                    "E-Pointer-Spacing",
                    f"`*` 与类型名之间有空格，而左边的 `{head}` 又不是已知类型名"
                    f"（也不是已知的变量）——两种读法都没能成立。\n"
                    f"如果你想要指针声明（C++ 的 `T * p` / `u64 * p` 写法），"
                    f"请确认类型名 `{head}` **声明在这一行的前面**"
                    f"（前向引用 C++ 也解析不了：`struct S` 要写在用它的函数之前）。\n"
                    f"如果你想要乘法，左边的 `{head}` 必须是一个变量。",
                    first_star)
            return True
        finally:
            self.i = save_i
            del self.diags[save_d:]
            del self.soft[save_s:]
            self._spaced_ptr_ok = save_ok
            self.value_names = save_values
            self.type_params = save_tparams

    def _at_type_prefixed_decl(self) -> bool:
        """判断当前语句是否是 `Type name = …` / `Type name;`（类型前置声明）。

        **R1-A 起改用「试解一个类型」**（`_probe_type_end`），不再手写前瞻：

        以前这里只看「两个相邻 IDENT / 后面跟 `=` 或 `;`」，于是
        `std::vector<u64> xs;`（`::`、`<`、`.` 都不认）、`unsigned long long n;`
        （三个词）、`char c;`（`char` 是 `Kind.CHAR`）全被判成表达式，
        读者看到的是「期望 ';'，实际是 'ident'」——**指不到病因**。
        试解与真正的解析是同一份代码，判据不会再漂移。

        **2026-11-10②**：`spaced_ptr_ok=False` 试解。`*` 有空格时只有
        **左边的名字是已知类型**才收进类型——所以 `u64 * p;` 在这里就是声明，
        而 `a * b;` 不会被读成 `(a*) b;`（`a` 不在类型词表里）。
        """
        end = self._probe_type_end(spaced_ptr_ok=False)
        if end is None:
            return False
        # 类型之后必须紧跟「变量名」+（`=` 或 `;`）；`T f(…)` 是函数（顶层已拦），
        # `p.x = …` / `f(…)` / `a < b;` 都在这条判据下自然落选。
        # **第 4 条**（P0 兼容层）：`T name{…}` 的第三段是 `{`——不加这一条，
        # `vector<u64> v{1,2,3};` 会被当成表达式，报「期望 ';'，实际是 '{'」。
        if (self.kind_at(end) == Kind.IDENT
                and (self.kind_at(end + 1) in (Kind.EQ, Kind.SEMI)
                     or (self.compat and self.kind_at(end + 1) == Kind.LBRACE))):
            return True
        # **第 10 条**（P0 兼容层）：`T name(args…)` 直接构造。语句位置不可能
        # 是函数声明（本语言没有嵌套函数），所以"类型 + 名字 + 括号 + `;`/`,`"
        # 只有直接构造这一种读法；`compat=False` 时这一句不生效，默认路径不变。
        return (self.compat and self.kind_at(end) == Kind.IDENT
                and self.kind_at(end + 1) == Kind.LPAREN
                and self._compat_ctor_call_ahead(end + 1))

    def parse_let(self) -> Node:
        """旧声明写法 `val x = 1;` / `var x = 1;` / `let x = 1;`（v0.68 已下线）。

        **调用方保证**：当前 token 是 `OLD_DECL_WORDS` 之一，且后跟标识符
        （声明形状）。词法器只出 `IDENT`（2026-11-10），诊断在这里报——
        同一个词在别处是合法标识符（`long long val = …`），不能按词形拦。

        **仍然解析出 AST**（`val-decl` / `var-decl`）：只换关键字不换形状，
        下游（checker / cost）看到的结构与旧版完全一致，不会因为一条
        "已下线"诊断再级联出一串「未声明」。
        """
        t = self.advance()                      # val / var / let
        word = t.text or ""
        mutable = (word == "var")
        self.error(REMOVED_KEYWORDS[word],
                   f"`{word}` 已下线：{RETIRED_HINT[word]}", t)
        stmt = Node("var-decl" if mutable else "val-decl", t.line, t.col)
        stmt.value = {"mut": mutable}

        name = self.expect_ident("变量名").text
        stmt.name = name
        self.value_names.add(name)
        if self.accept(Kind.COLON):
            stmt.add(self.parse_type())
        self.expect(Kind.EQ, "'='")
        stmt.add(self._parse_init_expr())
        self.expect(Kind.SEMI, "';'")
        return stmt

    def parse_paren_cond(self, what: str) -> Node:
        """v0.68：if / while / for 的条件 MUST 带括号。"""
        if not self.at(Kind.LPAREN):
            t = self.peek()
            self.error("E-Cond-ParenRequired",
                       f"{what} 的条件 MUST 带括号：写 `{what} ( ... ) {{ }}`", t)
            return self.parse_expr()
        self.advance()
        cond = self.parse_expr()
        self.expect(Kind.RPAREN, "')'")
        return cond

    def parse_body_block(self) -> Node:
        """`if` / `while` / `for` 的体：`{ … }` **或一条语句**（C++ 同义）。

        真实 C++ 里 `if (!v) return false;` / `for (… ) out.push_back(x);` 极常见，
        强制花括号会让这些文件在**体那一行**报「期望 '{'」——而它们本来没有
        语法错误。这里只做**同形 desugar**：单条语句包进 `block` 节点，
        下游（checker / interp / cost）看到的结构与手写 `{ … }` **完全相同**，
        不需要任何一处跟着改（与三段式 `for` 的 desugar 同一口径）。

        **只收一条语句**：`if (a) x = 1; else …` 的 `else` 由 `parse_if` 处理；
        `if (a) x = 1; y = 2;` 的 `y = 2;` 是外层语句，不是体的一部分。
        """
        if self.at(Kind.LBRACE):
            return self.parse_block()
        t = self.peek()
        blk = Node("block", t.line, t.col)
        for _stmt in self.parse_stmt_list():
            blk.add(_stmt)
        return blk

    def parse_if(self) -> Node:
        t = self.expect(Kind.IF, "'if'")
        node = Node("if", t.line, t.col)
        node.add(self.parse_paren_cond("if"))
        node.add(self.parse_body_block())
        while self.at(Kind.ELSE):
            self.advance()
            if self.at(Kind.IF):
                node.add(self.parse_if())
                return node
            node.add(self.parse_body_block())
        return node

    def parse_while(self) -> Node:
        t = self.expect(Kind.WHILE, "'while'")
        node = Node("while", t.line, t.col)
        node.add(self.parse_paren_cond("while"))
        node.add(self.parse_body_block())
        return node

    def parse_parallel(self) -> Node:
        """`parallel { … }` —— 并发构造（**语句形态**，v0.71 先做顺序模拟）。

        **为什么先做语句不做表达式**：任务返回值类型是个未决问题（计划 §5 Q4）。
        先把**成本口径**（κ = Σ，B-24）与**禁止项**（B-25/B-26）跑通，
        语义上按顺序执行——**这样它能被检查、能被定价，而不会先发明一套返回类型。**
        """
        t = self.expect(Kind.PARALLEL, "'parallel'")
        node = Node("parallel", t.line, t.col)
        node.add(self.parse_block())
        return node

    def _for_is_c_style(self) -> bool:
        """`for (` 之后、配对 `)` 之前，括号层 0 上出现 `;` 或 `=` → C 三段式。

        （2026-11-10 晚起三段式**被接受**，这个判据只用来选解析分支。）

        另外两种 `for` 的括号里**不可能**有 `;` 或 `=`，所以这个判据不会
        误伤它们（`==` 是 EQEQ，不在此列）。
        """
        depth = 0
        j = 0
        while self.kind(j) != Kind.EOF:
            k = self.kind(j)
            if k in (Kind.LPAREN, Kind.LBRACKET, Kind.LBRACE):
                depth += 1
            elif k == Kind.RPAREN:
                if depth == 0:
                    return False
                depth -= 1
            elif k in (Kind.RBRACKET, Kind.RBRACE):
                depth = max(0, depth - 1)
            elif depth == 0 and k in (Kind.SEMI, Kind.EQ):
                return True
            j += 1
        return False

    def _for_has_top_colon(self) -> bool:
        """`for (` 之后、配对 `)` 之前在括号层 0 上出现 `:` → C++ range-for。

        以前这里还要避开 `for (x in xs)` / `for (i, x in xs)` / `for (i in 0..n)`
        （它们的括号里没有 `:`）；那三种写法**已删除**，现在剩下的合法 `for`
        只有三段式与 range-for 两种，判据反而更简单了。
        """
        depth = 0
        j = 0
        while self.kind(j) != Kind.EOF:
            k = self.kind(j)
            if k in (Kind.LPAREN, Kind.LBRACKET, Kind.LBRACE):
                depth += 1
            elif k == Kind.RPAREN:
                if depth == 0:
                    return False
                depth -= 1
            elif k in (Kind.RBRACKET, Kind.RBRACE):
                depth = max(0, depth - 1)
            elif depth == 0 and k == Kind.COLON:
                return True
            j += 1
        return False

    def _for_header_iterator(self) -> Optional[Token]:
        """三段式 `for (` 头里有没有 C++ 迭代器接口调用（`xs.begin()` 等）。

        有就**在解析前**报 `E-For-NoIterator`：三段式本身已经接受，但迭代器
        仍然不存在（`*it` / `++it` 没有取值语义）。若交给正常解析，`xs.begin()`
        会先撞 `E-Call-UnknownMethod`——码不一样、也指不到「三段式里不能用」。
        """
        depth = 0
        j = 0
        while self.kind(j) != Kind.EOF:
            k = self.kind(j)
            if k in (Kind.LPAREN, Kind.LBRACKET, Kind.LBRACE):
                depth += 1
            elif k == Kind.RPAREN:
                if depth == 0:
                    break
                depth -= 1
            elif k in (Kind.RBRACKET, Kind.RBRACE):
                depth = max(0, depth - 1)
            if (k == Kind.IDENT
                    and (self.peek(j).text or "") in ITERATOR_METHODS
                    and self.kind(j + 1) == Kind.LPAREN):
                return self.peek(j)
            j += 1
        return None

    def _consume_for_c_style(self):
        """吃掉三段式 `for` 的 `( … )` 头与紧随的 `{ … }` 体（只为错误恢复）。

        迭代器形状在 `_parse_for_c_style` 开头就报 `E-For-NoIterator`；如果只抛
        不吞，`parse_block` 会从 `(` 中间重新同步，再把 `it != xs.end()` /
        `++it` 当成两条独立语句，级联出 `E-Call-UnknownMethod` +
        `E-Parse-Expected`。这里先括号配对吃掉，诊断只留一条。
        """
        depth = 1                                   # 已消费 `(`
        while not self.at(Kind.EOF) and depth > 0:
            k = self.advance().kind
            if k == Kind.LPAREN:
                depth += 1
            elif k == Kind.RPAREN:
                depth -= 1
        if self.at(Kind.LBRACE):
            depth = 0
            while not self.at(Kind.EOF):
                k = self.advance().kind
                if k == Kind.LBRACE:
                    depth += 1
                elif k == Kind.RBRACE:
                    depth -= 1
                    if depth == 0:
                        break

    # ---- 第 12 条：迭代器 `for` → **索引循环**（只在 `--compat` 下）--------
    #
    # 唯一标准形状（三种拼写落到**同一棵树**）：
    #
    #   ① for (auto it = xs.begin(); it != xs.end(); ++it) { *it; it->f; }
    #   ② for (T    it = xs.begin(); it != xs.end(); ++it) { … }        // T 被忽略
    #   ③ for (      it = xs.begin(); it != xs.end(); ++it) { … }        // it 已在外声明
    #
    # 降级目标（与手写索引循环**逐字节同形** ⇒ κ / run 必然相同）：
    #
    #   it → idx（新导入的 u64，= 0）   *it → xs[idx]   it->f → xs[idx].f
    #   xs.begin() → 0                  xs.end() → xs.size()
    #   ++it / it++ → idx = idx + 1
    #
    # 为什么**能**这样降级、而且不需要迭代器对象：整个循环的语义就是"按下标
    # 从 0 走到 size()-1"；把 `it` 换成一个下标之后，得到的 AST 与用户手写的
    # `for (u64 idx = 0; idx < xs.size(); idx = idx + 1)` **完全一样**，
    # 所以成本推导（`while` 的既有规则）与解释器执行都**不需要改一行**。
    #
    # 其余形状一律拒绝并教改法（见模块顶部 `FOR_ITER_*` 与 `compat.py`）。

    def _iter_for_header(self):
        """把 `for (` **之后**的头按括号层 0 的 `;` 切成三段。

        返回 `(parts, close)`：`parts = [init, cond, step]`（token 列表）；
        `close` 是配对 `)` 的**索引**。分不出三段时 `parts is None`。
        """
        toks = self.toks
        start = self.i
        depth = 0
        semis = []
        j = start
        while j < len(toks):
            k = toks[j].kind
            if k in (Kind.LPAREN, Kind.LBRACKET, Kind.LBRACE):
                depth += 1
            elif k == Kind.RPAREN:
                if depth == 0:
                    break
                depth -= 1
            elif k in (Kind.RBRACKET, Kind.RBRACE):
                depth = max(0, depth - 1)
            elif depth == 0 and k == Kind.SEMI:
                semis.append(j)
            j += 1
        if len(semis) != 2:
            return None, j
        return [toks[start:semis[0]], toks[semis[0] + 1:semis[1]],
                toks[semis[1] + 1:j]], j

    @staticmethod
    def _iter_for_text(tokens) -> str:
        """token 列表 → 诊断里引用的原文（只为可读，不参与判定）。

        `it < xs . end ( )` 这种空格会把读者带偏，所以按最小间隔拼：
        紧贴 `.` / `::` / `(` / `[` / `)` / `]` / `,` / `;`。
        """
        no_space_before = (Kind.DOT, Kind.PATHSEP, Kind.LPAREN, Kind.RPAREN,
                           Kind.LBRACKET, Kind.RBRACKET, Kind.COMMA, Kind.SEMI)
        no_space_after = (Kind.DOT, Kind.PATHSEP, Kind.LPAREN, Kind.LBRACKET)
        out, prev = "", None
        for tk in tokens:
            if out and prev not in no_space_after and tk.kind not in no_space_before:
                out += " "
            out += tk.text or ""
            prev = tk.kind
        return out or "（空）"

    @staticmethod
    def _iter_for_top_positions(tokens, want) -> List[int]:
        """`tokens` 里**括号 / 尖括号层 0** 上是 `want` 的位置。

        `std::map<std::string, int>::iterator it = …` 的 `,` 在 `<…>` 里面，
        不是声明符分隔；`FixedVec<u64, 3>` 同理。判据是**深度**，不是词形。
        """
        depth = 0
        out = []
        for i, tk in enumerate(tokens):
            k = tk.kind
            if k in (Kind.LPAREN, Kind.LBRACKET, Kind.LBRACE, Kind.LT):
                depth += 1
            elif k in (Kind.RPAREN, Kind.RBRACKET, Kind.RBRACE, Kind.GT):
                depth = max(0, depth - 1)
            elif depth == 0 and k == want:
                out.append(i)
        return out

    def _iter_for_plan(self, parts):
        """**只看 token** 判定这个头是否就是唯一标准形状。

        返回 `(plan, reject)`：成功时 `reject is None`；失败时
        `reject = (诊断码, 消息, token)`、`plan is None`。
        判定**不做任何猜测**：任何一处对不上就拒绝，并说清是哪一处。
        """
        if parts is None:
            return None, ("E-For-IteratorShape",
                          FOR_ITER_SHAPE_MSG % {
                              "detail": "三段式必须写全 `init; cond; step` 三段。"},
                          self.peek())
        init, cond, step = parts
        shape_detail = FOR_ITER_SHAPE_MSG

        # ---- init: `[类型…] it = recv.begin()` --------------------------
        eqs = self._iter_for_top_positions(init, Kind.EQ)
        if len(eqs) != 1:
            return None, ("E-For-IteratorShape",
                          shape_detail % {"detail":
                                          "初始化要写成 `[类型] it = xs.begin()`，"
                                          "这里是 `%s`。" % self._iter_for_text(init)},
                          init[0] if init else self.peek())
        eq = eqs[0]
        lhs, rhs = init[:eq], init[eq + 1:]
        if not lhs or lhs[-1].kind != Kind.IDENT:
            return None, ("E-For-IteratorShape",
                          shape_detail % {"detail":
                                          "`=` 左边最后一个 token 必须是迭代器名。"},
                          lhs[0] if lhs else self.peek())
        it_tok = lhs[-1]
        if len(lhs) > 1 and lhs[-2].kind == Kind.DOT:
            return None, ("E-For-IteratorShape",
                          shape_detail % {"detail":
                                          "迭代器名必须是**单个标识符**"
                                          "（`a.b = …` 这种成员赋值不降级）。"},
                          it_tok)
        if self._iter_for_top_positions(lhs, Kind.COMMA):
            return None, ("E-For-IteratorShape",
                          shape_detail % {"detail":
                                          "初始化里只能声明**一个**迭代器"
                                          "（不支持 `T a = …, b = …`）。"},
                          lhs[0])
        # rhs 的第一段必须是 `recv . METH ( )`
        if (len(rhs) < 5 or rhs[0].kind != Kind.IDENT
                or rhs[1].kind != Kind.DOT or rhs[2].kind != Kind.IDENT
                or rhs[3].kind != Kind.LPAREN
                or rhs[4].kind != Kind.RPAREN):
            return None, ("E-For-IteratorShape",
                          shape_detail % {"detail":
                                          "初始化右边要写 `xs.begin()`"
                                          "（接收者必须是**单个变量名**）。"},
                          rhs[0] if rhs else it_tok)
        meth, recv_tok = rhs[2], rhs[0]
        if (meth.text or "") in ("rbegin", "rend"):
            return None, ("E-For-IteratorReverse", FOR_ITER_REVERSE_MSG, meth)
        extra = rhs[5:]
        if extra:
            if any(tk.kind in (Kind.PLUS, Kind.MINUS, Kind.STAR, Kind.SLASH,
                               Kind.PLUS_EQ, Kind.MINUS_EQ) for tk in extra):
                return None, ("E-For-IteratorArith",
                              FOR_ITER_ARITH_MSG % {
                                  "text": self._iter_for_text(init)}, extra[0])
            return None, ("E-For-IteratorShape",
                          shape_detail % {"detail":
                                          "`begin()` 后面不能再接别的东西"
                                          "（这里是 `%s`）。"
                                          % self._iter_for_text(init)},
                          extra[0])
        if (meth.text or "") != "begin":
            return None, ("E-For-IteratorShape",
                          shape_detail % {"detail":
                                          "初始化必须从 `xs.begin()` 开始，"
                                          "这里写的是 `xs.%s()`。" % (meth.text or "")},
                          meth)

        # ---- cond: `it != recv.end()` ----------------------------------
        ops = [(i, tk) for i, tk in enumerate(cond)
               if tk.kind in (Kind.NE, Kind.LT, Kind.LE, Kind.GT, Kind.GE,
                              Kind.EQEQ)]
        if not ops:
            return None, ("E-For-IteratorTerm",
                          FOR_ITER_TERM_MSG % {
                              "text": self._iter_for_text(cond)},
                          cond[0] if cond else it_tok)
        op_i, op_tok = ops[0]
        left, right = cond[:op_i], cond[op_i + 1:]
        if not (len(left) == 1 and left[0].kind == Kind.IDENT
                and left[0].text == it_tok.text):
            if any(tk.kind in (Kind.PLUS, Kind.MINUS, Kind.STAR, Kind.SLASH,
                               Kind.PLUS_EQ, Kind.MINUS_EQ) for tk in left):
                return None, ("E-For-IteratorArith",
                              FOR_ITER_ARITH_MSG % {
                                  "text": self._iter_for_text(cond)},
                              left[0])
            return None, ("E-For-IteratorShape",
                          shape_detail % {"detail":
                                          "条件左边必须就是 `%s` 本身"
                                          "（这里是 `%s`）。"
                                          % (it_tok.text or "",
                                             self._iter_for_text(left))},
                          left[0] if left else it_tok)
        if op_tok.kind != Kind.NE:
            return None, ("E-For-IteratorTerm",
                          FOR_ITER_TERM_MSG % {
                              "text": self._iter_for_text(cond)}, op_tok)
        if (len(right) < 5 or right[0].kind != Kind.IDENT
                or right[1].kind != Kind.DOT or right[2].kind != Kind.IDENT
                or right[3].kind != Kind.LPAREN
                or right[4].kind != Kind.RPAREN):
            return None, ("E-For-IteratorTerm",
                          FOR_ITER_TERM_MSG % {
                              "text": self._iter_for_text(cond)},
                          right[0] if right else op_tok)
        if (right[2].text or "") in ("rbegin", "rend"):
            return None, ("E-For-IteratorReverse", FOR_ITER_REVERSE_MSG,
                          right[2])
        if len(right) != 5:
            return None, ("E-For-IteratorTerm",
                          FOR_ITER_TERM_MSG % {
                              "text": self._iter_for_text(cond)}, right[5])
        if (right[2].text or "") != "end":
            return None, ("E-For-IteratorTerm",
                          FOR_ITER_TERM_MSG % {
                              "text": self._iter_for_text(cond)}, right[2])
        if right[0].text != recv_tok.text:
            return None, ("E-For-IteratorShape",
                          shape_detail % {"detail":
                                          "`begin()` 与 `end()` 必须是**同一个容器**，"
                                          "这里是 `%s` 与 `%s`。"
                                          % (recv_tok.text or "",
                                             right[0].text or "")},
                          right[0])

        # ---- step: `++it` / `it++` -------------------------------------
        if (len(step) == 2 and step[0].kind == Kind.INC
                and step[1].kind == Kind.IDENT
                and step[1].text == it_tok.text):
            pass                                    # `++it`
        elif (len(step) == 2 and step[0].kind == Kind.IDENT
              and step[0].text == it_tok.text
              and step[1].kind == Kind.INC):
            pass                                    # `it++`
        else:
            kinds = [tk.kind for tk in step]
            if Kind.DEC in kinds:
                return None, ("E-For-IteratorReverse", FOR_ITER_REVERSE_MSG,
                              step[0] if step else it_tok)
            if any(k in (Kind.PLUS, Kind.MINUS, Kind.STAR, Kind.SLASH,
                         Kind.PLUS_EQ, Kind.MINUS_EQ, Kind.STAR_EQ,
                         Kind.SLASH_EQ) for k in kinds):
                return None, ("E-For-IteratorArith",
                              FOR_ITER_ARITH_MSG % {
                                  "text": self._iter_for_text(step)},
                              step[0] if step else it_tok)
            return None, ("E-For-IteratorShape",
                          shape_detail % {"detail":
                                          "推进只能是 `++%s` 或 `%s++`，"
                                          "这里是 `%s`。"
                                          % (it_tok.text or "", it_tok.text or "",
                                             self._iter_for_text(step))},
                          step[0] if step else it_tok)

        return {
            "it": it_tok.text,
            "it_tok": it_tok,
            "recv": recv_tok.text,
            "recv_tok": recv_tok,
            # 有类型前缀（①②）还是 `it` 已在外声明（③）
            "decl": len(lhs) > 1,
        }, None

    def _iter_for_index_name(self) -> str:
        """降级时新导入的下标变量名：**整份文件里的标识符都不撞**。

        先用 `idx`，撞了就 `idx2` / `idx3` …（撞的范围是整份 token 流的标识符，
        所以连"后面才声明的同名变量"也提前避开）。
        """
        base = _compat.FOR_ITER_INDEX_NAME
        cand, n = base, 1
        while cand in self._idents_in_file or cand in self._compat_idx_used:
            n += 1
            cand = f"{base}{n}"
        self._compat_idx_used.add(cand)
        return cand

    @staticmethod
    def _iter_for_tok(node: Node, text: str) -> Token:
        """AST 节点 → 诊断用的 token（只有位置有意义）。"""
        return Token(Kind.IDENT, text, getattr(node, "line", 0),
                     getattr(node, "col", 0), 0, 0)

    @staticmethod
    def _iter_for_arith_text(node: Node, it_name: str) -> str:
        """体里对迭代器做算术时的**可读原文**（`it + 1` / `it += 2` …）。

        只有字面量右操作数才拼出具体数字；其余拼成 `it + …`——**不猜**表达式
        是什么（诊断只负责指出"这里在拿迭代器算数"）。
        """
        rhs = node.children[1] if len(node.children) > 1 else None
        if getattr(rhs, "kind", "") == "lit-int" and rhs.value is not None:
            return f"{it_name} {node.value} {rhs.value}"
        return f"{it_name} {node.value} …"

    @staticmethod
    def _iter_for_index_node(src: Node, recv: str, idx: str) -> Node:
        """`*it` 的替身：`recv[idx]`（位置沿用 `*it` 的）。"""
        return Node("index", src.line, src.col).add(
            Node("path", src.line, src.col, name=recv),
            Node("path", src.line, src.col, name=idx))

    def _iter_for_rewrite(self, node: Node, it_name: str, recv: str, idx: str,
                          errs: list, pair: Optional[dict] = None) -> None:
        """体内替换：`*it` → `recv[idx]`；顺手把**不能降级**的用法收进 `errs`。

        只替换 `deref(path it)`：`it->f` / `it.f` 在 AST 里是
        `field(deref(it), f)`，替换掉里层就自动变成 `recv[idx].f`。
        裸用 `it`（比较 / 传参 / 打印）**没有等价的降级**，必须拒绝。

        `pair` 非 None 时是**第 12 条 b**（`map` / `set`）的语义：一轮元素是
        "键 / 值"而不是位置元素，替换规则换成

            it->first  →  ks[idx]
            it->second →  recv.get(ks[idx])
            *it        →  ( ks[idx], recv.get(ks[idx]) )   （`set`：`ks[idx]`）

        并把"写进迭代元素"的赋值收进 `errs`（视图写不回去）。
        """
        kids = getattr(node, "children", None) or []
        for i, c in enumerate(kids):
            k = getattr(c, "kind", "")
            if pair is not None:
                # **写进迭代元素**（`*it = …` / `it->second = …` / `it->first += …`）：
                # 降级后 `it->second` 是一次 `get()`（读），写进去只会丢写。
                if k in ("assign", "assign-op") and c.children \
                        and self._iter_for_pair_target(c.children[0], it_name):
                    errs.append(("E-For-IteratorMutate",
                                 FOR_ITER_KEYED_WRITE_MSG % {"name": it_name},
                                 self._iter_for_tok(c, it_name)))
                    continue
                if k == "field" and self._iter_for_pair_base(c, it_name):
                    nm = c.name or ""
                    if not pair["is_map"]:
                        errs.append(("E-For-IteratorShape",
                                     FOR_ITER_SET_FIELD_MSG % {
                                         "name": it_name, "field": nm},
                                     self._iter_for_tok(c, it_name)))
                        continue
                    if nm not in ("first", "second"):
                        errs.append(("E-For-IteratorShape",
                                     FOR_ITER_MAP_FIELD_MSG % {
                                         "name": it_name, "field": nm},
                                     self._iter_for_tok(c, it_name)))
                        continue
                    kids[i] = self._iter_for_pair_field(
                        c, recv, pair["snap"], idx, nm)
                    continue
            if k == "deref" and len(c.children) == 1 \
                    and c.children[0].kind == "path" \
                    and c.children[0].name == it_name:
                if pair is None:
                    kids[i] = self._iter_for_index_node(c, recv, idx)
                elif pair["is_map"]:
                    kids[i] = self._iter_for_pair_tuple(c, recv, pair["snap"], idx)
                else:                                   # `set`：`*it` 就是元素
                    kids[i] = self._iter_for_index_node(c, pair["snap"], idx)
                continue
            if k in ("var-decl", "const-decl") and c.name == it_name:
                errs.append(("E-For-IteratorShape",
                             FOR_ITER_SHADOW_MSG % {"name": it_name},
                             self._iter_for_tok(c, it_name)))
                continue
            if k == "field" and c.children \
                    and c.children[0].kind == "path" \
                    and c.children[0].name == it_name:
                # `it.f`（C++ 里迭代器要写 `it->f`，但两种拼写都收）→ `xs[idx].f`
                c.children[0] = self._iter_for_index_node(c.children[0], recv, idx)
                continue
            if k == "path" and c.name == it_name:
                errs.append(("E-For-IteratorShape",
                             FOR_ITER_BARE_MSG % {"name": it_name},
                             self._iter_for_tok(c, it_name)))
                continue
            if k in ("binary", "assign-op") and (c.value or "") in (
                    "+", "-", "*", "/", "%", "+=", "-=", "*=", "/="):
                if any(getattr(x, "kind", "") == "path"
                       and getattr(x, "name", "") == it_name
                       for x in (c.children or [])):
                    errs.append(("E-For-IteratorArith",
                                 FOR_ITER_ARITH_MSG % {
                                     "text": self._iter_for_arith_text(c, it_name)},
                                 self._iter_for_tok(c, it_name)))
                    continue
            if k == "method-call" and (c.name or "") in _compat.FOR_ITER_MUTATORS:
                base = c.children[0] if c.children else None
                if getattr(base, "kind", "") == "path" \
                        and getattr(base, "name", "") == recv:
                    errs.append(("E-For-IteratorMutate",
                                 FOR_ITER_MUTATE_MSG % {"name": recv,
                                                        "m": c.name or ""},
                                 self._iter_for_tok(c, recv)))
                    continue
            if k == "call" and (c.name or "") in _compat.FOR_ITER_MUTATING_CALLS:
                args = c.children[1] if len(c.children) > 1 else None
                if args is not None and any(
                        getattr(x, "kind", "") == "path"
                        and getattr(x, "name", "") == recv
                        for x in (args.children or [])):
                    errs.append(("E-For-IteratorMutate",
                                 FOR_ITER_MUTATE_MSG % {
                                     "name": recv, "m": f"{c.name}(…)"},
                                 self._iter_for_tok(c, recv)))
                    continue
            self._iter_for_rewrite(c, it_name, recv, idx, errs, pair)

    def _iter_for_desugar(self, t: Token, plan: dict, body: Node) -> Node:
        """把已确认形状的头 + 体拼成**手写索引循环**那棵树。

        产出（与 `for (u64 idx = 0; idx < xs.size(); idx = idx + 1)` 同形）：

            block(
                var-decl u64 idx = 0,
                while( binary < ( path idx, method-call size(recv) ),
                       block( …体…, assign(path idx, binary + (path idx, 1)) ) ))

        **`map` / `set` 不走这里**：它们没有位置索引，由
        `_iter_for_desugar_keyed` 降级成"键快照 + 下标循环"（见 `compat.py`
        第 12 条 b）。这条分派在 `_compat_iter_for` 里按接收者类型做。
        """
        it_name = plan["it"]
        recv = plan["recv"]
        it_tok = plan["it_tok"]

        idx = self._iter_for_index_name()
        errs: list = []
        self._iter_for_rewrite(body, it_name, recv, idx, errs)
        for code, msg, tok in errs[:1]:             # 同一个错误只报第一条
            self.error(code, msg, tok)

        decl = Node("var-decl", it_tok.line, it_tok.col, name=idx)
        decl.value = {"mut": True, "has_init": True}
        decl.add(Node("type-name", it_tok.line, it_tok.col, name="u64"))
        decl.add(Node("lit-int", it_tok.line, it_tok.col, value=0))

        size_call = Node("method-call", t.line, t.col, name="size")
        size_call.add(Node("path", plan["recv_tok"].line, plan["recv_tok"].col,
                           name=recv), Node("args"))
        # **条件用 `<` 而不是 `!=`**：对这个形状两者等价（`idx` 从 0 起、每轮 +1、
        # 体里不许改容器 ⇒ 正好跑 `size()` 轮），但成本域**只从 `<` 推出上界**：
        # `while (idx < xs.size())` 是 ⊤ + `E-While-NoBound`（那条警告自己就
        # 建议改成 `<`），`while (idx < xs.size())` 是 `n(xs)` 轮。所以降级取 `<`，
        # κ 才是**有限**的——否则"降级成索引循环"会让语料里所有迭代器循环变 ⊤。
        cond = Node("binary", t.line, t.col, value="<").add(
            Node("path", it_tok.line, it_tok.col, name=idx), size_call)

        step = self._desugar_incdec(
            Node("path", it_tok.line, it_tok.col, name=idx), 1, t)

        inner = Node("block", body.line, body.col)
        for c in body.children:
            inner.add(c)
        # `continue` 在 C++ 的 `for` 里仍执行第三段：与既有三段式 desugar 同一处理。
        self._inject_step_before_continue(inner, step)
        inner.add(step)

        outer = Node("block", t.line, t.col)
        outer.add(decl, Node("while", t.line, t.col).add(cond, inner))
        self.value_names.add(idx)
        if not plan["decl"]:
            # 形状③：`it` 在循环外声明。降级后循环不再写 `it`，循环之后读到的是
            # 旧值 → 静默错。登记下来，由 `parse_stmt_list` 在语句边界检查。
            self._iter_escapes[it_name] = it_tok
        return outer

    # ---- 第 12 条 b：`map` / `set` 的键快照 + 下标循环 --------------------

    def _iter_for_keyed_head(self, plan: dict):
        """接收者是 `map` / `set` 类容器吗？→ `(kind, 键类型节点, 错误)`。

        * `kind` 是 `"map"` / `"set"`（含 `unordered_*` 变体）；
        * 不是键值容器 → `(None, None, None)`，交给原来的索引降级；
        * 是键值容器但**推不出键类型** → `(None, None, 错误元组)`：降级的第一行
          就是 `vector<K> ks = …`，没有 K 写不出来（不猜）。

        **判据在体之前就算**：推不出键类型时调用方会跳过循环体，只报一条，
        不让体里的 `it->…` 级联出一串"由我们自己的降级造成的假错误"。
        """
        recv = plan["recv"]
        it_tok = plan["it_tok"]
        ty = self._compat_expr_ty(Node("path", it_tok.line, it_tok.col, name=recv))
        # **类型推不出就拒绝，别猜**（2026-11-12）：降级目标**按容器种类分岔**
        # （序列 → `recv[idx]`；map/set → 键快照 + `get`）。类型是 `auto` 时两者分不开，
        # 硬走序列那条会得到 **check 绿、run 崩**（实测 `未实现的字段访问 second`）。
        # 宁可拒绝。
        if ty is None:
            return (None, None,
                    ("E-For-IteratorShape",
                     "三段式 for 的接收者 `%s` 的**类型推不出**（很可能是 `auto`）："
                     "降级要按容器种类选不同目标（序列 → `recv[idx]`，map/set → 键快照），"
                     "推不出就**不猜**——硬猜会得到「check 过、run 崩」。\n"
                     "改法（二选一）：\n"
                     "· 把接收者写成明确类型：`map<K,V> %s = …;` 或 `vector<T> %s = …;`；\n"
                     "· 或直接用 range-for：`for (const auto& kv : %s) { … }`。"
                     % (recv, recv, recv, recv),
                     it_tok))
        # **旧名也算**（`Map` / `Set` 是 `typenames.CANON` 里的别名）：漏掉它们
        # 就会把 `Map<u64,u64>` 的迭代器降级成 `m[idx]`——check 绿、run 崩。
        head = _canon(self._compat_head_of(ty) or "")
        if head in _compat.FOR_ITER_MAP_HEADS:
            kind = "map"
        elif head in _compat.FOR_ITER_SET_HEADS:
            kind = "set"
        else:
            # 序列：走原来的索引降级；**认不出的（含 `auto`）拒绝**，理由同上。
            if head in ("vector", "array", "fixedvec", "text", "string", "vec"):
                return (None, None, None)
            return (None, None,
                    ("E-For-IteratorShape",
                     "三段式 for 的接收者 `%s` 的类型 `%s` **认不出**（很可能是 `auto`）："
                     "降级要按容器种类选不同目标，认不出就**不猜**。\n"
                     "改法（二选一）：\n"
                     "· 把接收者写成明确类型：`vector<T> %s = …;` 或 `map<K,V> %s = …;`；\n"
                     "· 或直接用 range-for：`for (const auto& kv : %s) { … }`。"
                     % (recv, head or "?", recv, recv, recv),
                     it_tok))
        inner = ty
        while (inner is not None
               and getattr(inner, "kind", "") in ("type-ref", "type-ptr")):
            inner = inner.children[0] if inner.children else None
        key_ty = self._compat_type_arg_node(inner, 0)
        if key_ty is None:
            return (None, None,
                    ("E-For-IteratorShape",
                     FOR_ITER_KEYTYPE_MSG % (recv, head, recv, recv, recv, recv),
                     it_tok))
        return (kind, key_ty, None)

    def _iter_for_snapshot_name(self) -> str:
        """降级时新导入的**快照变量**名：整份文件 + 本轮已导入的名字都不撞。"""
        base = _compat.FOR_ITER_KEYS_NAME
        cand, n = base, 1
        while cand in self._idents_in_file or cand in self._compat_idx_used:
            n += 1
            cand = f"{base}{n}"
        self._compat_idx_used.add(cand)
        return cand

    @staticmethod
    def _iter_for_vec_ty(src: Node, elem: Node) -> Node:
        """`vector<K>` 类型节点（K 深拷贝：AST 是树，节点不能共用）。"""
        args = Node("type-args", src.line, src.col).add(copy.deepcopy(elem))
        return Node("type-name", src.line, src.col, name="vector").add(args)

    def _iter_for_desugar_keyed(self, t: Token, plan: dict, body: Node,
                                kind: str, key_ty: Node) -> Node:
        """`map` / `set` 的迭代器 for → **键快照 + 下标循环**（第 12 条 b）。

        产出（与手写"先 `keys()` 再索引循环"逐字节同形——κ / run 必然相同）：

            block(
                var-decl vector<K> ks = recv.keys(),   # 快照**只取一次**（O(n)）
                var-decl u64 idx = 0,
                while( binary < ( path idx, method-call size(path ks) ),
                       block( …体…, assign(path idx, binary + (path idx, 1)) ) ))

        体里（`kind == "map"`）：

            it->first  →  ks[idx]                      # 只读快照，不查表
            it->second →  recv.get(ks[idx])            # 每次 O(log n)
            *it        →  ( ks[idx], recv.get(ks[idx]) )   # 二元组

        `set`：`*it` → `ks[idx]`（`it->first/second` 在重写时报错）。

        **快照取在循环外**是硬要求：① C++ 里迭代中改容器是 UB，看到的就是
        进入循环那一刻的键集合；② 每轮重取会把 κ 写成 O(n²) 的假象。
        """
        recv = plan["recv"]
        it_name = plan["it"]
        it_tok = plan["it_tok"]
        snap = self._iter_for_snapshot_name()
        idx = self._iter_for_index_name()
        is_map = kind == "map"

        errs: list = []
        self._iter_for_rewrite(body, it_name, recv, idx, errs,
                               pair={"snap": snap, "is_map": is_map})
        for code, msg, tok in errs[:1]:             # 同一个错误只报第一条
            self.error(code, msg, tok)

        # ---- ① 快照声明：`vector<K> ks = recv.keys();`（set 用 `.values()`）
        vec_ty = self._iter_for_vec_ty(it_tok, key_ty)
        snap_decl = Node("var-decl", t.line, t.col, name=snap)
        snap_decl.value = {"mut": True, "has_init": True}
        snap_decl.add(vec_ty)
        keys_call = Node("method-call", plan["recv_tok"].line,
                         plan["recv_tok"].col,
                         name=_compat.FOR_ITER_SNAPSHOT_METHOD[kind])
        keys_call.add(Node("path", plan["recv_tok"].line, plan["recv_tok"].col,
                           name=recv), Node("args"))
        snap_decl.add(keys_call)

        # ---- ② 下标声明：`u64 idx = 0;`（与第 12 条同一形状） ------------
        decl = Node("var-decl", it_tok.line, it_tok.col, name=idx)
        decl.value = {"mut": True, "has_init": True}
        decl.add(Node("type-name", it_tok.line, it_tok.col, name="u64"))
        decl.add(Node("lit-int", it_tok.line, it_tok.col, value=0))

        # ---- ③ `while (idx < ks.size())`：条件用 `<`，成本域才推得出上界 ---
        size_call = Node("method-call", t.line, t.col, name="size")
        size_call.add(Node("path", it_tok.line, it_tok.col, name=snap),
                      Node("args"))
        cond = Node("binary", t.line, t.col, value="<").add(
            Node("path", it_tok.line, it_tok.col, name=idx), size_call)

        step = self._desugar_incdec(
            Node("path", it_tok.line, it_tok.col, name=idx), 1, t)

        inner = Node("block", body.line, body.col)
        for c in body.children:
            inner.add(c)
        # `continue` 在 C++ 的 `for` 里仍执行第三段：与既有三段式 desugar 同一处理。
        self._inject_step_before_continue(inner, step)
        inner.add(step)

        outer = Node("block", t.line, t.col)
        outer.add(snap_decl, decl,
                  Node("while", t.line, t.col).add(cond, inner))
        self.value_names.add(snap)
        self.value_names.add(idx)
        # 快照变量的静态类型登记进作用域（只服务"接收者是什么类型"的推断）。
        self._scope_declare(snap, vec_ty)
        if not plan["decl"]:
            # 形状③：`it` 在循环外声明，降级后循环不再写它 → 循环后读到旧值。
            self._iter_escapes[it_name] = it_tok
        return outer

    @staticmethod
    def _iter_for_pair_base(field: Node, it_name: str) -> bool:
        """`it->f` / `it.f`（可带 `(*it).f` 的括号）的基址是不是**本循环的 `it`**。"""
        if not field.children:
            return False
        base = field.children[0]
        while getattr(base, "kind", "") == "paren" and base.children:
            base = base.children[0]
        k = getattr(base, "kind", "")
        if k == "path":
            return (base.name or "") == it_name
        if k == "deref" and len(base.children) == 1 \
                and base.children[0].kind == "path":
            return (base.children[0].name or "") == it_name
        return False

    def _iter_for_pair_target(self, expr: Optional[Node], it_name: str) -> bool:
        """赋值左侧的**存储位置**是不是从迭代元素长出来的。

        判据是**位置的根**，不是"子树里出现过 `it`"：
        `it->second = v` / `it->second.x = v` / `it->second[0] = v` / `*it = v`
        都拒；而 `arr[it->second] = v`（把值当**下标读**）不拒——那是对 `arr`
        的写，`it->second` 只是读。
        """
        n = expr
        while n is not None:
            k = getattr(n, "kind", "")
            if k == "field":
                if self._iter_for_pair_base(n, it_name):
                    return True
                n = n.children[0] if n.children else None
                continue
            if k in ("index", "paren"):
                n = n.children[0] if n.children else None
                continue
            if k == "deref" and len(n.children) == 1 \
                    and n.children[0].kind == "path":
                return (n.children[0].name or "") == it_name
            return False
        return False

    def _iter_for_pair_field(self, src: Node, recv: str, snap: str, idx: str,
                             which: str) -> Node:
        """`it->first` → `ks[idx]`；`it->second` → `recv.get(ks[idx])`。"""
        key = self._iter_for_index_node(src, snap, idx)
        if which == "first":
            return key
        call = Node("method-call", src.line, src.col, name="get")
        call.add(Node("path", src.line, src.col, name=recv),
                 Node("args").add(key))
        return call

    def _iter_for_pair_tuple(self, src: Node, recv: str, snap: str,
                             idx: str) -> Node:
        """`*it` → `( ks[idx], recv.get(ks[idx]) )`（与 C++ 的 `pair` 逐位同义）。"""
        return Node("tuple", src.line, src.col).add(
            self._iter_for_index_node(src, snap, idx),
            self._iter_for_pair_field(src, recv, snap, idx, "second"))

    def _iter_for_skip_body(self):
        """拒绝时**跳过**循环体（错误恢复）：`{ … }` 或一条语句。

        与 `_consume_for_c_style` 同一个理由：头形状都不支持时，体里的
        `*it` / `it->f` 只会级联出一串**由我们自己的拒绝造成**的假错误
        （`*it` 变成"解引用非指针"、`it->f` 变成"未知字段"）。所以体不解析，
        等用户把循环头改对之后再报体里的真错误。
        """
        if self.at(Kind.LBRACE):
            depth = 0
            while not self.at(Kind.EOF):
                k = self.advance().kind
                if k == Kind.LBRACE:
                    depth += 1
                elif k == Kind.RBRACE:
                    depth -= 1
                    if depth == 0:
                        return
            return
        self.skip_to_stmt_end()

    def _compat_iter_for(self, t: Token) -> Optional[Node]:
        """`--compat`：三段式 for 的迭代器形状 → 索引循环（第 12 条）。

        返回 `None` 表示**头里根本没有迭代器接口**（`xs.begin()` 等），交给
        原来的三段式解析——`compat=False` 时这个方法一次都不会被调用。
        """
        if self._for_header_iterator() is None:
            return None
        parts, close = self._iter_for_header()
        plan, reject = self._iter_for_plan(parts)
        if close < len(self.toks):
            self.i = close + 1                      # 吃掉 `( … )`（含闭括号）
        if reject is not None:
            code, msg, tok = reject
            self._iter_for_skip_body()
            self.error(code, msg, tok)
            return Node("block", t.line, t.col)
        # **`map` / `set`**（第 12 条 b）：降级目标不是 `recv[idx]`（map 的 `[]`
        # 是按**键**查），而是"键快照 + 下标循环"。判据（键类型）在**体之前**
        # 就定下来：推不出就跳过体、只报一条，不级联。
        keyed = self._iter_for_keyed_head(plan)
        if keyed[2] is not None:
            code, msg, tok = keyed[2]
            self._iter_for_skip_body()
            self.error(code, msg, tok)
            return Node("block", t.line, t.col)
        # 体里的 `it` 指的是**本循环**的迭代器（形状①②自己声明它，形状③在头里
        # 重新初始化），所以外层同名迭代器的"逃逸"状态在体里**不适用**：
        # 两个连续的 `for (it = xs.begin(); …)` 不该互相误报。
        self._iter_escapes.pop(plan["it"], None)
        body = self.parse_body_block()
        if keyed[0] is not None:
            return self._iter_for_desugar_keyed(t, plan, body, keyed[0], keyed[1])
        return self._iter_for_desugar(t, plan, body)

    def _iter_for_report_escape(self, stmts: List[Node]) -> None:
        """形状③的 `it` 在循环**之后**被读到 → `E-For-IteratorEscape`。

        降级后循环只写新导入的 `idx`，`it` 里仍是循环前的旧值（C++ 里它在这里
        也已经离开作用域）——**静默错**，所以必须拒绝。检查点放在语句边界：
        `parse_stmt_list` 每解析完一条语句，就扫一遍这条语句里有没有
        `path(it)`。因为解析是**从左到右**的，这等价于"循环之后的任何位置"。
        """
        for name, tok in list(self._iter_escapes.items()):
            for s in stmts:
                use = self._find_path_named(s, name)
                if use is not None:
                    self.error("E-For-IteratorEscape",
                               FOR_ITER_ESCAPE_MSG % {"name": name},
                               self._iter_for_tok(use, name))
                    del self._iter_escapes[name]
                    break

    @staticmethod
    def _find_path_named(node: Node, name: str) -> Optional[Node]:
        """子树里第一个 `path name`（没有就 `None`）。"""
        if getattr(node, "kind", "") == "path" \
                and getattr(node, "name", "") == name:
            return node
        for c in (getattr(node, "children", None) or []):
            got = Parser._find_path_named(c, name)
            if got is not None:
                return got
        return None

    def _parse_for_c_style(self, t: Token) -> Node:
        """C 三段式 `for (init; cond; step)` → **与 `init; while (cond) { body; step; }`
        完全同形的 AST**。

        **口径（审核文档 §2 / R2）**：三段式要打成本上界，所以**不在 parser 里
        发明新的成本规则**，只做这个 desugar——`while` 的推导、`E-While-NoBound`、
        ⊤ + 原因全部原样生效（推不出就不给数字，绝不低估）。

        * `init` 为空（`for (;;)`）→ 只有 `while (true)`；
        * `cond` 为空 → `true`（`while (true)`，成本照旧 ⊤ + 原因）；
        * `step` 为空 → 不追加语句；
        * `++i` / `i++` 仍走既有的 `_desugar_incdec`（与 `i = i + 1` 同形）。

        `--compat` 下先交给第 12 条：**唯一标准迭代器形状**降级成索引循环；
        其余迭代器形状报 `E-For-Iterator*`。不带开关时这条路一个字节都不走。
        """
        if self.compat:
            node = self._compat_iter_for(t)
            if node is not None:
                return node

        it = self._for_header_iterator()
        if it is not None:
            # 先把 `( … )` 与 `{ … }` 吃掉再报，避免从括号中间恢复出级联诊断。
            self._consume_for_c_style()
            raise ParseError(
                "E-For-NoIterator",
                FOR_C_STYLE_ITERATOR_MSG + f"\n（触发词：`{it.text}`）", it)

        outer = Node("block", t.line, t.col)
        if self.at(Kind.SEMI):
            self.advance()
        else:
            # `init` 自己消费结尾的 ';'；多声明符（第 9 条）展开成多条。
            for _stmt in self.parse_stmt_list():
                outer.add(_stmt)

        if self.at(Kind.SEMI):
            semi = self.advance()
            cond = Node("lit-bool", semi.line, semi.col, value=True)
        else:
            cond = self.parse_expr()
            self.expect(Kind.SEMI, "';'")

        step = self._parse_for_step()
        self.expect(Kind.RPAREN, "')'")
        body = self.parse_body_block()

        inner = Node("block", body.line, body.col)
        for c in body.children:
            inner.add(c)
        if step is not None:
            # `continue` 在 C++ 的 `for` 里仍执行第三段；保留这个语义。
            self._inject_step_before_continue(inner, step)
            inner.add(step)
        outer.add(Node("while", t.line, t.col).add(cond, inner))
        return outer

    def _inject_step_before_continue(self, node: Node, step: Node) -> None:
        """把**本循环**里的 `continue;` 改成 `{ step; continue; }`。

        C++ 的 `continue` 在 `for` 里会执行第三段（`for (…; …; i++)` 的 `i++`
        仍然发生）；直接 desugar 成 `while` 后，`continue` 会跳过追加在体尾的
        `step`，语义就从"跳过剩余语句"变成"卡住不推进"。

        **只处理属于本循环的 `continue`**：遇到内层 `while` / `for` / `parallel`
        就不再往下走（它们的 `continue` 属于内层）。`step` 用深拷贝——AST 是树，
        不能把同一个 `Node` 对象挂到两处（下游按 `id()` 建索引）。
        """
        for i, c in enumerate(list(node.children or [])):
            if c.kind == "continue":
                node.children[i] = Node("block", c.line, c.col).add(
                    copy.deepcopy(step), c)
            elif c.kind in ("while", "for", "parallel"):
                continue
            else:
                self._inject_step_before_continue(c, step)

    def _parse_for_step(self) -> Optional[Node]:
        """三段式的第三段：**表达式 / 赋值 / `++i` / `i++`，后面没有 `;`**。

        形状与 `parse_stmt` 的赋值尾部一致（`Node("assign")` / `Node("assign-op")` /
        `Node("expr-stmt")`），`++` / `--` 复用 `_desugar_incdec`，所以
        `for (…; …; i++)` 与 `for (…; …; i = i + 1)` 产出**同一个 AST**。
        """
        if self.at(Kind.RPAREN):
            return None
        t = self.peek()
        if self._at_incdec() is not None:
            step = self._at_incdec()
            self.advance()                 # `++` / `--` 是**一个** token
            place = self.parse_postfix(self.parse_prefix())
            self._require_place(place, t)
            return self._desugar_incdec(place, step, t)
        place = self.parse_postfix(self.parse_prefix())
        if self._at_incdec() is not None:
            step = self._at_incdec()
            self.advance()                 # `++` / `--` 是**一个** token
            return self._desugar_incdec(place, step, t)
        # 第 19 条：步进段里的复合赋值（`for (…; …; mask <<= 1)`）。
        if self.compat:
            _comp = self._compat_compound_ahead()
            if _comp is not None:
                return self._compat_parse_compound_assign(place, _comp,
                                                          semi=False)
        expr = self.parse_expr(left=place)
        if self.at(Kind.EQ):
            self.advance()
            rhs = self.parse_expr()
            self._require_place(expr, t)
            return Node("assign", t.line, t.col).add(expr, rhs)
        if self.at(Kind.PLUS_EQ) or self.at(Kind.MINUS_EQ) \
                or self.at(Kind.STAR_EQ) or self.at(Kind.SLASH_EQ):
            op = self.advance()
            rhs = self.parse_expr()
            self._require_place(expr, t)
            return Node("assign-op", t.line, t.col, value=op.kind.value).add(expr, rhs)
        return Node("expr-stmt", t.line, t.col).add(expr)

    def _parse_for_range_cpp(self, t: Token) -> Node:
        """C++ range-for `for (const auto& x : xs)` —— **引用/按值语义照 C++**。

        四种前缀都认（审核文档 §2 的 C++ 写法），并把绑定类别记到
        `node.ref`（见 `Node.ref` 的说明）：

        | 写法 | `node.ref` | 语义 | 拷贝 |
        |---|---|---|---|
        | `for (auto& x : xs)` / `for (T& x : xs)` | `"mut"` | 可变引用 | 不拷贝 |
        | `for (const auto& x : xs)` / `for (const T& x : xs)` | `"const"` | 只读引用 | 不拷贝 |
        | `for (auto x : xs)` / `for (T x : xs)` | `None` | 按值 | 每轮一份 |

        **为什么以前是静默错值**：旧实现把 range-for 当成"剥掉前缀的
        `for (x in xs)`"，而 `in` 的语义是**按值拷贝每个元素**——于是
        `for (auto& row : xs) { row.push(9); }` 改的是副本，容器纹丝不动。
        现在三种绑定分开，引用迭代不拷贝（成本域也据此不给元素拷贝记账）。
        """
        _const = False
        if self.at(Kind.CONST):
            self.advance()
            _const = True
        _ref = False
        if self.at(Kind.IDENT) and (self.peek().text or "") == "auto":
            self.advance()
            while self.at(Kind.AMP):
                self.advance()
                _ref = True
        else:
            _ty = self.parse_type()               # 已含 `T&` 后缀
            self._reject_ptr_or_variadic()
            _ref = getattr(_ty, "kind", "") == "type-ref"
        name = self.expect_ident("循环变量").text
        self.value_names.add(name)
        self.expect(Kind.COLON, "':'")
        node = Node("for", t.line, t.col, name=name,
                    ref=("const" if (_ref and _const)
                         else "mut" if _ref else None))
        _coll = self.parse_expr()
        node.add(_coll)
        self.expect(Kind.RPAREN, "')'")
        # **第 13 条**：循环变量的类型 = 容器元素类型——`for (auto& r : rings)`
        # 里的 `r.push(1)` 要能推出接收者是 `Ring`。推不出就登记 `_OPAQUE_TY`
        # （"已声明、类型待定"），**绝不误报**。
        if self.compat:
            self._scope_declare(name, self._compat_element_ty(_coll))
        node.add(self.parse_body_block())
        return node

    def _compat_element_ty(self, coll: Optional[Node]) -> Optional[Node]:
        """`for (… : coll)` 的元素类型节点（只服务第 13 条的接收者推断）。"""
        ty = self._compat_expr_ty(coll)
        if ty is None or ty is _OPAQUE_TY:
            return _OPAQUE_TY
        if getattr(ty, "kind", "") == "type-name":
            got = self._compat_type_arg_node(ty, 0)
            return got if got is not None else _OPAQUE_TY
        return _OPAQUE_TY

    def _for_header_has_in(self) -> Optional[Token]:
        """`for (` 之后、配对 `)` 之前在括号层 0 上有没有 `in`（已删除的写法）。"""
        depth = 0
        j = 0
        while self.kind(j) != Kind.EOF:
            k = self.kind(j)
            if k in (Kind.LPAREN, Kind.LBRACKET, Kind.LBRACE):
                depth += 1
            elif k == Kind.RPAREN:
                if depth == 0:
                    return None
                depth -= 1
            elif k in (Kind.RBRACKET, Kind.RBRACE):
                depth = max(0, depth - 1)
            elif depth == 0 and k == Kind.IN:
                return self.peek(j)
            j += 1
        return None

    def _parse_for_in_removed(self, t: Token) -> Node:
        """`for (x in xs)` / `for (i, x in xs)` / `for (i in 0..n)`：**已删除**。

        报 `E-For-InRemoved`，消息里给三种替代写法（遍历 / 下标 / 区间）。
        **先把整个头与体吃掉再抛**（复用 `_consume_for_c_style` 的括号配对），
        否则 `parse_block` 会从 `(` 中间重新同步，级联出一串
        `E-Parse-Expected`——同一个问题只留一条诊断。
        """
        in_tok = self._for_header_has_in()
        if in_tok is None:
            # 既不是三段式、也不是 range-for、又看不见 `in`：认不出的 `for` 头。
            self._consume_for_c_style()
            raise ParseError(
                "E-Parse-Expected",
                "for 头要写 `for (auto& x : xs)`（range-for）或 "
                "`for (u64 i = 0; i < n; i = i + 1)`（三段式）",
                t)
        self._consume_for_c_style()
        raise ParseError("E-For-InRemoved", FOR_IN_REMOVED_MSG, in_tok)

    def parse_for(self) -> Node:
        t = self.expect(Kind.FOR, "'for'")
        node = Node("for", t.line, t.col)
        if self.at(Kind.LPAREN):
            self.advance()
            # **两种 C++ 形状**（2026-11-10 晚，审核文档 §2）：
            #   · range-for `for (const auto& x : xs)` → 记 `node.ref`；
            #   · 三段式 `for (init; cond; step)` → `init; while (cond) { body; step; }`。
            # `for (x in xs)` 三种写法**已删除**（与 C++ range-for 重合）：
            # 报 `E-For-InRemoved` 并教替代写法。
            if self._for_is_c_style():
                return self._parse_for_c_style(t)
            if self._for_has_top_colon():
                return self._parse_for_range_cpp(t)
            return self._parse_for_in_removed(t)
        self.error("E-Cond-ParenRequired",
                   "for 的条件 MUST 带括号：写 `for (auto& x : xs) { }` 或 "
                   "`for (u64 i = 0; i < n; i = i + 1) { }`", t)
        # 恢复：`for x in xs { … }` 没有括号——先吃掉头，再吃掉体的收尾，
        # 否则 `x in xs` 会被当成语句重新解析，级联出一串无关诊断。
        while not self.at(Kind.EOF) and not self.at(Kind.LBRACE):
            self.advance()
        if self.at(Kind.LBRACE):
            _d = 0
            while not self.at(Kind.EOF):
                _k = self.advance().kind
                if _k == Kind.LBRACE:
                    _d += 1
                elif _k == Kind.RBRACE:
                    _d -= 1
                    if _d == 0:
                        break
        return node

    def parse_match(self) -> Node:
        t = self.expect(Kind.MATCH, "'match'")
        node = Node("match", t.line, t.col)
        node.add(self.parse_expr())
        self.expect(Kind.LBRACE, "'{'")
        while not self.at(Kind.RBRACE) and not self.at(Kind.EOF):
            node.add(self.parse_arm())
            if not self.accept(Kind.COMMA):
                pass
        self.expect(Kind.RBRACE, "'}'")
        return node

    def parse_arm(self) -> Node:
        t = self.peek()
        pat = self.parse_pattern()
        self.expect(Kind.FATARROW, "'=>'")
        arm = Node("arm", t.line, t.col).add(pat)

        if self.at(Kind.LBRACE):
            arm.add(self.parse_block())
            return arm

        # 单语句臂：终止符是 ',' 或 ';'（二者都可，',' 由外层消费）
        if self.at(Kind.RETURN):
            rt = self.advance()
            r = Node("return", rt.line, rt.col)
            if not (self.at(Kind.SEMI) or self.at(Kind.COMMA) or self.at(Kind.RBRACE)):
                r.add(self.parse_expr())
            self.accept(Kind.SEMI)
            arm.add(r)
            return arm

        e = self.parse_expr()
        self.accept(Kind.SEMI)
        arm.add(Node("expr-stmt", t.line, t.col).add(e))
        return arm

    def parse_pattern(self) -> Node:
        t = self.peek()
        # **字面量模式**：`0 => …` / `true => …`。
        # 以前这里只认路径，`match (n) { 0 => … }` 会以 E-Parse-ExpectedPath 失败，
        # 而失败之后整条 match 被丢掉、κ 却照样算出 0（"假上界"）。
        if self.kind() in (Kind.INT, Kind.TRUE, Kind.FALSE):
            lit = self.advance()
            return Node("pattern-literal", lit.line, lit.col, value=lit.text)
        if self.at(Kind.UNDERSCORE) if hasattr(Kind, "UNDERSCORE") else False:
            self.advance()
            return Node("pattern-wildcard", t.line, t.col)
        # `_` 在词法里可能是 IDENT
        if self.at(Kind.IDENT) and self.peek().text == "_":
            self.advance()
            return Node("pattern-wildcard", t.line, t.col)
        path = self.parse_path("模式")
        node = Node("pattern", t.line, t.col, name=path)
        if self.accept(Kind.LPAREN):
            while not self.at(Kind.RPAREN) and not self.at(Kind.EOF):
                if self.kind() == Kind.IDENT and self.kind(1) in (Kind.COMMA, Kind.RPAREN):
                    bt = self.advance()
                    self.value_names.add(bt.text)
                    node.add(Node("binding", bt.line, bt.col, name=bt.text))
                elif self.kind() == Kind.IDENT and self.kind(1) == Kind.COLON:
                    bt = self.advance()
                    self.value_names.add(bt.text)
                    self.advance()
                    node.add(Node("binding", bt.line, bt.col, name=bt.text).add(self.parse_type()))
                else:
                    node.add(self.parse_pattern())
                if not self.accept(Kind.COMMA):
                    break
            self.expect(Kind.RPAREN, "')'")
        return node

    # ---------------------------------------------------------- 表达式

    def parse_expr(self, min_bp: int = 0, left: Optional[Node] = None) -> Node:
        # `left` 由语句层传入（它已经解析过前缀 + 后缀，并且要检查句末的
        # `++` / `--`）；普通递归调用仍然自己解析左操作数。
        if left is None:
            left = self.parse_prefix()
            left = self.parse_postfix(left)

        while True:
            k = self.kind()
            # 后缀 `i++` 出现在表达式位置（`x = i++`）：`++` / `--` 是独立
            # token，这里明确说"只能作独立语句"。
            if k == Kind.INC or k == Kind.DEC:
                raise ParseError("E-Unary-Unsupported", INC_DEC_NOT_EXPR, self.peek())
            # **三目 `c ? a : b`**（2026-11-13）：`?` 与 `x?`（try 后缀）同一个
            # token，谁是谁由 `parse_postfix` 的 `_ternary_ahead()` 判定——判成
            # 三目时它**不消费** `?`，掉到这里由表达式循环接管。
            #
            # 形状 `ternary(cond, then, else)`：中支是**整条表达式**（C 的
            # grammar 也是 `logical-or ? expression : conditional`），else 支
            # 用同一个 `TERNARY_BP` 递归 ⇒ 右结合（见常量处的注释）。
            if k == Kind.QUESTION:
                if TERNARY_BP < min_bp:
                    break
                op = self.advance()
                then = self.parse_expr()               # 中支：整条表达式
                self.expect(Kind.COLON, "':'")
                els = self.parse_expr(TERNARY_BP)      # else：右结合
                left = Node("ternary", op.line, op.col).add(left, then, els)
                continue
            # **逻辑运算符只有符号写法**（2026-11-11）：`a && b` / `a || b`；
            # 词形 `and` / `or` 已退役（词法器报 `E-Logic-WordOp`）。
            # `>>`：两个**相邻**的 `>` 才是右移（`> >` 仍是两个比较号）。
            if (k == Kind.GT and self.kind(1) == Kind.GT
                    and self.peek().end == self.peek(1).start):
                l_bp, r_bp = SHR_BP
                if l_bp < min_bp:
                    break
                op = self.advance()
                self.advance()
                right = self.parse_expr(r_bp)
                left = Node("binary", op.line, op.col, value=">>").add(left, right)
                continue
            if k not in BINARY:
                break
            l_bp, r_bp = BINARY[k]
            if l_bp < min_bp:
                break
            op = self.advance()
            # **`**=` 没有这个运算符**（2026-11-10）：词法给的是
            # `STAR_STAR` + `EQ`（不是 `*=`，也不会被拆成两个 `*`），于是
            # 右操作数位置撞上 `=`，报 `E-Parse-ExpectedExpr 期望表达式，
            # 实际是 '='`——**明确失败，但没说"没有 `**=`"**。这里给专属
            # 诊断 + 改法。**不新造复合赋值语义**：`x **= n` 要求左操作数
            # 只求值一次，而本语言的表达式没有"左值引用"这层抽象，收下它
            # 就得先定义这条语义；`x = x ** n` 现在就能写、且语义清楚。
            if k == Kind.STAR_STAR and self.at(Kind.EQ):
                raise ParseError(
                    "E-Op-NoCompoundPow",
                    "本语言没有 `**=` 这个运算符；请写成 `x = x ** n`"
                    "（左操作数写两遍，指数写在 `**` 右边）", op)
            right = self.parse_expr(r_bp)
            left = Node("binary", op.line, op.col,
                        value=LOGIC_ALIAS.get(op.kind, op.kind.value)).add(left, right)

        return left

    def parse_prefix(self) -> Node:
        t = self.peek()
        k = t.kind

        # 前缀 `++i` 出现在表达式位置（`x = ++i`）：明确拒绝（2026-11-10）。
        # **语句位置**的 `++i;` 在 `parse_stmt` 就分流了，走不到这里。
        if self._at_incdec() is not None:
            raise ParseError("E-Unary-Unsupported", INC_DEC_NOT_EXPR, t)

        if k == Kind.BANG:
            # **`!x` 是唯一的逻辑非**（2026-11-11）：词形 `not` 已退役。
            # 只认前缀位置；`!=` 是单独的 token（NE），不受影响。
            self.advance()
            return Node("unary", t.line, t.col, value="!").add(self.parse_expr(PREFIX_BP))
        if k == Kind.TILDE:
            self.advance()
            return Node("unary", t.line, t.col, value="~").add(self.parse_expr(PREFIX_BP))
        if k == Kind.MINUS:
            self.advance()
            return Node("unary", t.line, t.col, value="-").add(self.parse_expr(PREFIX_BP))
        if k == Kind.STAR:
            # **前缀 `*` = 解引用**（2026-11-10 起指针是语言的一部分）。
            #
            # 这里只吃**前缀**位置；`a * b` 是 `parse_expr` 的二元分支
            # （`BINARY` 表），根本走不到这里——所以乘法不受影响。
            # **是不是解引用由类型检查判定**：操作数不是指针 → 仍然报
            # `E-Unary-Unsupported`（`*x` 在非指针上没有意义），见
            # `typecheck._expr` 的 `deref` 分支。这一步只产出节点。
            self.advance()
            return Node("deref", t.line, t.col).add(self.parse_expr(PREFIX_BP))
        if k == Kind.STAR_STAR:
            # `**p`（指向指针的指针，两级解引用）。`a ** b` 是幂运算，
            # 走 `parse_expr` 的二元分支，不会到这里。
            self.advance()
            inner = self.parse_expr(PREFIX_BP)
            return Node("deref", t.line, t.col).add(
                Node("deref", t.line, t.col).add(inner))
        if k == Kind.AMP:
            # 前缀 `&` 仍然**只解析成 `ref` 节点**——这里不做拒绝，原因在下面。
            #
            # 2026-11-10：`&x` 以前是"恒等"（运行期返回操作数），必须明确不再
            # 当取地址用。但**不能在这里一律拒**：样例 `calculator.eln` 用
            # `apply_top(& values, & operators)` 把实参显式标成引用（同一个函数
            # 在另一处又写成 `apply_top(values, operators)`——说明 `&` 是冗余标记）。
            # 一律拒绝会让那个样例变红，而样例内容是冻结的。
            #
            # 所以判据放在**知道形参类型**的地方（`typecheck._check_unary_usage`）：
            #   · 实参位置的 `&x`，且被调方该形参是 `T&` / `const T&` → 允许；
            #   · 其余一切位置（`print(&x)`、`u64 y = &x;`、`f(&x)` 形参不是引用）→
            #     `E-Unary-Unsupported`。
            # 也就是说：`&` 只作为"显式借用标记"在语义成立的位置被接受，
            # **不再**是一句到处都能写、运行期恒等的空话。
            self.advance()
            node = Node("ref", t.line, t.col)
            if self.kind() == Kind.IDENT and self.peek().text == "mut":
                self.advance()
                node.value = "mut"
            node.add(self.parse_expr(PREFIX_BP))
            return node

        if k == Kind.INT:
            self.advance()
            # **存解析后的数值，不存源码文本**：`0x10` / `0b1010` / `1_000`
            # 的值词法层已经算好（`Token.value`）。以前把 `t.text` 原样存进
            # `lit-int`，解释器又 `int(text)` 按**十进制**解析一次——于是
            # `u64 x = 0x10;` 过 check、run 抛宿主
            # `ValueError: invalid literal for int() with base 10: '0x10'`。
            # 词法认得、值没落地，这是"语义在两层之间蒸发"的典型。
            # 值保持任意精度 `int`（不在这里按 u64 截断）：装不进 `i64` 的
            # 正字面量要按 u64 参与运算，见 interp `_widen_oob_literals`；
            # 超出 u64 的字面量在词法层就报 `E-Lex-IntOverflow` 了。
            return Node("lit-int", t.line, t.col, value=int(t.value))
        if k == Kind.FLOAT:
            self.advance()
            return Node("lit-float", t.line, t.col, value=t.text)
        if k == Kind.CHAR:
            self.advance()
            return Node("lit-char", t.line, t.col, value=t.text)
        if k == Kind.STRING:
            self.advance()
            return Node("lit-string", t.line, t.col, value=t.value)   # 解码后的字节
        if k == Kind.TRUE or k == Kind.FALSE:
            self.advance()
            return Node("lit-bool", t.line, t.col, value=(k == Kind.TRUE))

        if k == Kind.QUESTION_BANG:
            # ?!label(args) —— 决策点；'?!' 是单个 token（见词法最长匹配）
            self.advance()
            if not self.at(Kind.IDENT):
                raise ParseError("E-Choose-NoLabel",
                                 "'?!' 后必须紧跟标签名", self.peek())
            label = self.advance().text
            node = Node("dispatch", t.line, t.col, name=label)
            self.expect(Kind.LPAREN, "'('")
            node.add(self.parse_args_until(Kind.RPAREN))
            self.expect(Kind.RPAREN, "')'")
            return node

        # **`match` 不能当表达式**（2026-11-10）：以前报一句
        # 「期望表达式，实际是 'match'」，读者看不出"`match` 只能是语句"。
        if k == Kind.MATCH:
            raise ParseError(
                "E-Match-NotExpr",
                "本语言没有 `match` 表达式：`match` 只能是语句。\n"
                "改法：先把结果赋给变量再 `match`：`u64 r = 0; match (x) { "
                "1 => { r = 10; } _ => { r = 20; } }`；或写成 `if` 链。", t)
        # **闭包 / lambda `|a, b| { … }`**（2026-11-10）：以前在 `|` 处报
        # 「期望表达式，实际是 '|'」。
        if k == Kind.PIPE:
            raise ParseError(
                "E-Lambda-Unsupported",
                "本语言没有闭包 / lambda：`|a, b| { … }` 不存在。\n"
                "改法：写成具名函数 `bool by_lt(u64 a, u64 b) { "
                "return a < b; }`，再把函数名传给 `sort_by(xs, by_lt);`。", t)
        # **C++ 的具名强转 `static_cast<T>(x)`**：写成 `x as T`。
        #
        # **第 11 条**（P0 兼容层）：`--compat` 下只认 `static_cast` 的两种
        # 精确形状（`unsigned char` / `u64` 目标），且折叠与否交给
        # `typecheck.resolve_static_cast` 按**静态类型**判。其它三个
        # `*_cast` 仍然一律报错（它们没有"值不变"的等价物）。
        if k == Kind.IDENT and t.text in (
                "static_cast", "reinterpret_cast", "const_cast", "dynamic_cast"):
            if self.compat and t.text == "static_cast":
                return self._compat_parse_static_cast(t)
            raise ParseError(
                "E-Cast-Unsupported",
                f"本语言没有 `{t.text}`：标量转换写 `x as T`。\n"
                f"例：`{t.text}<u8>(x)` → `x as u8`；`reinterpret_cast` 没有对应"
                "（按位重解释请用显式移位 / 掩码）。", t)

        if k == Kind.LPAREN:
            self.advance()
            first = self.parse_expr()
            if self.at(Kind.COMMA):
                node = Node("tuple", t.line, t.col).add(first)
                while self.accept(Kind.COMMA):
                    if self.at(Kind.RPAREN):
                        break
                    node.add(self.parse_expr())
                self.expect(Kind.RPAREN, "')'")
                return node
            self.expect(Kind.RPAREN, "')'")
            # **C 风格强转 `(u8)x`**（2026-11-10）：以前报「期望 ')'，实际是 'ident'」。
            # 括起来的**类型名**后面直接跟一个操作数，在文法里没有别的解释
            # （`(a) - b` 的下一个 token 是运算符，不在此列）。
            #
            # **2026-11-13：仍然拒绝，但消息必须能直接照抄。** 决定依据是一次
            # A/B（`demo/probe-libs` 的 `skiplist` / `ring` / `pool` 三个库共
            # 6 处）：**全部**是 `(int)<容器>.size()` / `(int)<bool>` 这种标量
            # 窄化，**没有一处**是指针强转。看上去"接受"更有价值，但真正的
            # 理由在另一边：
            #   · 本语言**只有一个**转换写法 `x as T`；再收一套 C 拼写就是
            #     "同一语义两个名字"（能力索引 §5 的反模式，`or`/`and`/`not`
            #     与 `for (x in xs)` 都是这么删掉的）；
            #   · `(a)*b` 与 `(T)*p` 的歧义要靠"名字是不是类型名"判，而
            #     `parse_primary` 看不见 `.tu` 里的类型（配对检查才看得见）——
            #     在解析期猜一次，猜错就是静默改语义。
            # 所以这条路只做到"可复制"：消息里给出**这一处**改写后的整行。
            if first.kind == "path" and self.kind() in (
                    Kind.IDENT, Kind.INT, Kind.FLOAT, Kind.CHAR, Kind.STRING,
                    Kind.SELF, Kind.TRUE, Kind.FALSE):
                _at = self.peek()
                raise ParseError(
                    "E-Cast-CStyle",
                    self._c_style_cast_message(first.name or ""), _at)
            return Node("paren", t.line, t.col).add(first)

        if k == Kind.LBRACKET:
            self.advance()
            node = Node("array", t.line, t.col)
            while not self.at(Kind.RBRACKET) and not self.at(Kind.EOF):
                node.add(self.parse_expr())
                if not self.accept(Kind.COMMA):
                    break
            self.expect(Kind.RBRACKET, "']'")
            return node

        if k in (Kind.IDENT, Kind.SELF, Kind.DYN):
            # **`nullptr` 是空指针字面量**（2026-11-10 起指针存在）。
            # 它是表达式位置的**值**（`u64* p = nullptr;` / `if (p == nullptr)`），
            # 不是变量名——所以在这里先分流，不产出 `path`（否则下游会报
            # 「未声明的名字 nullptr」）。
            if (t.text or "") == "nullptr":
                self.advance()
                return Node("lit-null", t.line, t.col)
            # 表达式里只取第一段；后续 `.name` / `.name(...)` 交给 postfix，
            # 否则 `text.at(i)` 会被整体吃成路径，方法调用就看不见了。
            t0 = self.advance()
            node = Node("path", t0.line, t0.col, name=t0.text)
            # **第 13 条**：成员函数体里的**裸字段名**改写成 `接收者.字段`。
            # 与手写 `recv.buf` **同形**——所以 κ / run 与手写降级必然相同。
            # 被局部变量 / 形参遮住的名字**不改**（C++ 的作用域规则同样如此）。
            if self._is_member_field(t0.text):
                node = Node("field", t0.line, t0.col, name=t0.text).add(
                    Node("path", t0.line, t0.col, name=self._member_ctx[0]))
            # v0.68：`FixedVec<T, N>::make()` —— 泛型类型实参先挂上来。
            # **P4-0（2026-11-11）**：`array<T, N>(v)` 的值初始化也是同一种
            # "类型名 + 实参" 形状，但后面跟的是 `(` 而不是 `::`。只在
            # **已知类型名**上认（`array` / `vector` …），所以 `a < b > (c)`
            # 这类比较不会被误读。
            if self.at(Kind.LT) and (self._generic_tail(0)
                                     or (self._type_name_known(t0.text)
                                         and self._generic_ctor_tail())):
                self.advance()
                args = self._parse_type_args(t0.line, t0.col)
                node = Node("type-name", t0.line, t0.col, name=t0.text).add(args)
            return node

        raise ParseError("E-Parse-ExpectedExpr",
                         f"期望表达式，实际是 {k.value!r}", t)

    def parse_postfix(self, left: Node) -> Node:
        while True:
            # `Type { field: expr, ... }` —— 结构体字面量（文法 primary 里的 field-init 形式）
            # 判定用三重前瞻：`路径` + `{` + `ident` + `:`。
            # 只靠 `{` 会把 `if (n > 0) {` 的条件误吃进来——那里 postfix 也会看到 `{`。
            if (self.at(Kind.LBRACE) and left.kind == "path"
                    and self.kind(1) == Kind.IDENT and self.kind(2) == Kind.COLON):
                self.advance()
                node = Node("struct-literal", left.line, left.col, name=left.name)
                while not self.at(Kind.RBRACE) and not self.at(Kind.EOF):
                    ft = self.expect_ident("字段名")
                    self.expect(Kind.COLON, "':'")
                    node.add(Node("field-init", ft.line, ft.col, name=ft.text)
                             .add(self.parse_expr()))
                    if not self.accept(Kind.COMMA):
                        break
                self.expect(Kind.RBRACE, "'}'")
                left = node
                continue

            # **第 4 条**（P0 兼容层）：**位置式**聚合初始化 `Point{3, 4}`。
            #
            # 只在 `--compat` 且 `Point` 是**本文件里已声明的 struct** 时成立：
            # 位置式要补字段名，而字段名只能来自 `struct_fields`。补不出来就
            # 交给下面的老路径（`E-Type-PositionalInit`）——**不猜**。
            if (self.compat and self.at(Kind.LBRACE) and left.kind == "path"
                    and left.name in self.struct_fields):
                _brace = self.peek()
                _fields = list(self.struct_fields[left.name])
                _name = left.name
                _exprs = self._compat_brace_items()
                left = self._compat_struct_literal_from(
                    _exprs, _fields, _name, _brace)
                continue

            t = self.peek()

            # **第 13 条**：成员体里的 `this` / `self` —— 本语言没有 `this`
            # （`self` 是它自己的关键字），而成员体降级后接收者已经有了形参名，
            # 所以这里**明确拒绝**并告诉读者该写什么，不静默改写成别的东西。
            if self.compat and self._member_ctx is not None \
                    and left.kind == "path" \
                    and (left.name or "") in ("this", "self") \
                    and self.kind() in (Kind.DOT, Kind.ARROW):
                raise ParseError(
                    "E-Compat-MemberThis",
                    f"成员函数体里不能用 `{left.name}`：本语言没有 `this` / "
                    f"隐式接收者，降级成自由函数后接收者是**显式形参**。\n"
                    f"改法：① 直接写字段名（`buf` / `head` / `count`）——它们会被"
                    f"自动改写成接收者的字段（`{self._member_ctx[0]}.buf`）；\n"
                    f"      ② 要显式指代对象就写形参名 `{self._member_ctx[0]}`"
                    f"（降级后的自由函数里就是它）；\n"
                    f"      ③ 要传给别人就写 `{self._member_ctx[0]}`。",
                    t)

            # **`p->field` 成员访问**（2026-11-10 起指针存在）：产物与
            # `(*p).field` **完全同形**（`field(deref(p), field)`）——类型检查
            # 只认 `deref` 一处「操作数必须是指针」，interp / cost 对 `deref`
            # 的分支也照旧生效，不新造第三种节点。
            if self.at(Kind.ARROW):
                at = self.advance()
                name = self.expect_ident("字段名或方法名").text
                if name in ITERATOR_METHODS:
                    raise ParseError("E-Call-UnknownMethod",
                                     ITERATOR_METHODS_MSG.format(name=name), at)
                base = Node("deref", at.line, at.col).add(left)
                if self.at(Kind.LPAREN):
                    self.advance()
                    call = Node("method-call", at.line, at.col, name=name).add(base)
                    call.add(self.parse_args_until(Kind.RPAREN))
                    self.expect(Kind.RPAREN, "')'")
                    left = call
                else:
                    left = Node("field", at.line, at.col, name=name).add(base)
                continue

            if self.at(Kind.DOT):
                self.advance()
                # 字段访问 或 方法调用
                if self.at(Kind.INT):                  # 元组下标 .0 / .1
                    it = self.advance()
                    left = Node("field", t.line, t.col, value=it.text).add(left)
                    continue
                # **成员位置的 `end` 不是退役关键字**（2026-11-10）：`end`
                # 现在是普通 IDENT（词法器不再按词形报错），`xs.end()` 直接
                # 落进下面的「没有迭代器」统一诊断。这里不需要专门分支。
                mt = self.peek()
                name = self.expect_ident("字段名或方法名").text
                # 同一个缺口一种说法：`begin` / `end` / `rbegin` / `rend` 不论
                # 带不带 `()`，都报「本语言没有迭代器」+ 三种替代写法。
                if name in ITERATOR_METHODS:
                    # **第 5 条**（P0 兼容层）：**只在实参列表里**收
                    # `xs.begin()` / `xs.end()`，先做成临时节点；是否配平由
                    # `_compat_fold_iter_args` 在拿到完整实参表后判——不配平
                    # 仍旧报**同一条**「本语言没有迭代器」。
                    if (self.compat and self._iter_arg_depth > 0
                            and name in ("begin", "end")
                            and self.at(Kind.LPAREN)
                            and self.kind(1) == Kind.RPAREN):
                        self.advance()             # '('
                        self.advance()             # ')'
                        left = Node("iter-arg", mt.line, mt.col, name=name).add(left)
                        continue
                    raise ParseError("E-Call-UnknownMethod",
                                     ITERATOR_METHODS_MSG.format(name=name), mt)
                if self.at(Kind.LPAREN):
                    self.advance()
                    args = self.parse_args_until(Kind.RPAREN)
                    self.expect(Kind.RPAREN, "')'")
                    # **第 13 条**：`r.push(1)` → `Ring_push(r, 1)`（纯 desugar）。
                    # 只在本文件里推得出接收者类型时降级；推不出且名字确实是
                    # 某个 struct 的成员时报 `E-Compat-MethodUnresolved`。
                    if self.compat:
                        _free = self._compat_member_call(left, name, args,
                                                        t.line, t.col, mt)
                        if _free is not None:
                            left = _free
                            continue
                    call = Node("method-call", t.line, t.col, name=name).add(left)
                    call.add(args)
                    left = call
                else:
                    left = Node("field", t.line, t.col, name=name).add(left)
                continue

            if self.at(Kind.PATHSEP):
                # `Type::make(...)`：静态成员调用
                self.advance()
                name = self.expect_ident("静态成员名").text
                if left.kind == "path":
                    qual = f"{left.name}.{name}"
                    if left.name == "std" and name in STD_TYPE_NAMES:
                        # **表达式里的 `std::vector<u64>::make()`**（R1-A）：
                        # `std::` 必须先归一，否则 `std.vector` 会被当成用户路径、
                        # 后面的 `<` 被当小于号——`std::vector<u64>::make()` 会
                        # 解析成一段毫无关系的比较表达式。
                        _nm = self._normalize_type_path(qual, t)
                        node = Node("type-name", t.line, t.col, name=_nm)
                        if self.at(Kind.LT):
                            self.advance()
                            node = node.add(self._parse_type_args(t.line, t.col))
                        left = node
                        continue
                    # **第 6 条**（P0 兼容层）：`std::` 之下的**表达式**名走白名单。
                    #
                    # 白名单内 → **只改名**：`std::sort(xs)` 与 `sort(xs)` 产出
                    # 同一个 `call` 节点（κ / run 因此逐字节相同）。
                    # **B 档（删除式）** → `std::move(e)` **整个删掉**，落成 `e`
                    # 的 AST（一个包装节点都不产生，见 `_compat_std_delete`）。
                    # 白名单外 → `E-Compat-UnknownStd`，并把**已支持的名字**列全。
                    if left.name == "std" and self.compat:
                        # **第 17 条**：`std::printf(fmt, …)` 不是"改个名字"就完了——
                        # 格式串的 `%d` / `%s` / `%llu` 也要一起翻成 `{}` 洞，
                        # 否则输出会原样打出 `%llu`（行为一致率最大的那块缺口）。
                        # 判据/诊断在 `compat.printf_plan()`，这里只做接线。
                        if name == "printf" and self.at(Kind.LPAREN):
                            left = self._compat_printf(t)
                            continue
                        if name in _compat.STD_DELETE_ALIASES:
                            left = self._compat_std_delete(name, t)
                            continue
                        if name in _compat.STD_EXPR_ALIASES:
                            left = Node("path", left.line, left.col,
                                        name=_compat.STD_EXPR_ALIASES[name])
                            continue
                        raise ParseError(
                            "E-Compat-UnknownStd",
                            f"`std::{name}` 不在兼容层的白名单里。\n"
                            f"已支持的名字：\n"
                            f"· 类型：`std::vector` / `std::map` / `std::set` / "
                            f"`std::unordered_map` / `std::unordered_set` / "
                            f"`std::array` / `std::optional` / `std::expected` / "
                            f"`std::string` / `std::deque` / `std::stack` / "
                            f"`std::queue` / `std::pair` 与宽度名 "
                            f"（`std::size_t` → `u64`、`std::uint64_t` → `u64` …）；\n"
                            f"· 表达式：{_compat.std_expr_names()}。\n"
                            f"{_compat.std_expr_fix()}",
                            t)
                    left = Node("path", left.line, left.col, name=qual)
                else:
                    left = Node("field", t.line, t.col, name=name).add(left)
                continue

            if self.at(Kind.LT) and left.kind in ("path", "type-name") \
                    and (left.children or self._generic_call_ahead()):
                # `FixedVec<T, N>::make(...)`：泛型静态调用（只在 `::` 前提下成立）
                self.advance()
                while not self.at(Kind.GT) and not self.at(Kind.EOF):
                    self.advance()
                self.expect(Kind.GT, "'>'")
                continue

            if self.at(Kind.LPAREN):
                self.advance()
                # **第 10 条**（P0 兼容层）：表达式位置的 `T(args…)` 也按直构
                # 处理——`std::vector<u64>(n, 0)` 在内层时只有这条能把它变成
                # 本语言已有的 `vector<u64>::filled(0, n)`。`array<T, N>(v)`
                # 本来就有等价节点，这里原样返回同一个 `call`（AST 不变）。
                if self.compat and left.kind == "type-name":
                    _ctor_args = self.parse_args_until(Kind.RPAREN)
                    self.expect(Kind.RPAREN, "')'")
                    left = self._compat_ctor_expr(left, _ctor_args, t)
                    continue
                # **第 13 条**：成员体里调**兄弟成员函数** `push(1)` →
                # `Ring_push(接收者, 1)`（C++ 里这就是"对 *this 调用"）。
                if (self.compat and left.kind == "path" and self._member_ctx
                        and not self._scope_shadows(left.name or "")
                        and (left.name or "") in
                        ((self.struct_decls.get(self._member_ctx[2]) or {})
                         .get("method_names") or ())):
                    _a = self.parse_args_until(Kind.RPAREN)
                    self.expect(Kind.RPAREN, "')'")
                    _mangled = f"{self._member_ctx[2]}_{left.name}"
                    left = self._compat_build_call(
                        _mangled,
                        Node("path", t.line, t.col, name=self._member_ctx[0]),
                        _a, t.line, t.col)
                    continue
                call = Node("call", t.line, t.col, name=left.name if left.kind == "path" else None)
                call.add(left)
                # `range(起, 止, 步长)`：步长写成 `*k` / `+k`，只在 range 里合法（v0.69）
                if left.kind == "path" and (left.name or "").split(".")[-1] == "range":
                    call.add(self.parse_range_args())
                else:
                    call.add(self.parse_args_until(Kind.RPAREN))
                self.expect(Kind.RPAREN, "')'")
                left = call
                continue

            if self.at(Kind.LBRACKET):
                self.advance()
                idx = self.parse_expr()
                self.expect(Kind.RBRACKET, "']'")
                left = Node("index", t.line, t.col).add(left, idx)
                continue

            if self.at(Kind.QUESTION):
                # **`?` 的两个身份**（2026-11-13）：
                #   · `expr?` —— try 后缀（`f()?`，Result 传播）；
                #   · `cond ? a : b` —— 三目运算符。
                # 词法上是同一个 token，只能看**后面那一截**：`? <表达式> :`
                # 是三目。判成三目时**不消费** `?`，让它掉回 `parse_expr`
                # 的二元循环（那里有 `TERNARY_BP`）；否则按 try 后缀照旧。
                if self._ternary_ahead():
                    return left
                self.advance()
                left = Node("try", t.line, t.col).add(left)
                continue

            if self.at(Kind.AS):
                self.advance()
                left = Node("cast", t.line, t.col).add(left, self.parse_type())
                continue

            return left

    def _ternary_ahead(self) -> bool:
        """当前位置的 `?` 是**三目运算符**还是 **`x?` 的 try 后缀**？

        ## 为什么需要"试解"

        `?` 在本语言里有两个身份，词法上是**同一个 token**：

        | 写法 | 身份 | 后面的形状 |
        |---|---|---|
        | `f()?` | try 后缀（Result 传播） | `;` / `,` / `)` / 任何**不能**开始表达式的东西 |
        | `c ? a : b` | 三目运算符 | `<表达式> :` |

        所以判据只可能是"后面那一截是不是 `表达式 :`"——这正是 C 系语法里
        `?` 的歧义（`f() ? a : b` 与 `f()?` 在读到一个完整表达式之前分不开）。
        实现用**试解 + 完整回退**：跳过 `?`、解一条表达式、看是不是 `:`；
        是 → 三目；解析失败或不是 `:` → try 后缀。

        ## 回退必须是"完整"的

        试解失败时会**丢弃**它产生的任何副作用，否则一个 `f()?;` 就会被
        下一次试解悄悄改写状态：

        · `self.i`（token 位置）——立刻回到 `?` 之前；
        · `self.diags` / `self.soft`——试解里可能有 `W-Compat-*` 之类的
          记录（`--compat` 的降级会登记告警），三目没成就不该留下；
        · `_compat_idx_used` / `_iter_arg_depth` / `_iter_escapes`——第 12 条
          的迭代器降级会在**实参表**里生成新变量名，试解不能占用名字。

        ## 早退

        第一个 token 根本不能开始表达式（`;` / `)` / `,` / 二元运算符…）时
        **不必试解**：`f()?;` / `f()? + 1` / `f()?, ` 都是这种形状。特权早退
        让常见写法零代价（不必真的解析一遍再回退）。
        """
        if self.kind(1) not in EXPR_START:
            return False
        save_i = self.i
        save_diags = len(self.diags)
        save_soft = len(self.soft)
        save_idx = set(self._compat_idx_used)
        save_depth = self._iter_arg_depth
        save_escapes = dict(self._iter_escapes)
        try:
            self.advance()                       # `?`
            self.parse_expr()                    # 中支（整条表达式）
            return self.at(Kind.COLON)
        except (ParseError, RecursionError):
            return False
        finally:
            self.i = save_i
            del self.diags[save_diags:]
            del self.soft[save_soft:]
            self._compat_idx_used = save_idx
            self._iter_arg_depth = save_depth
            self._iter_escapes = save_escapes

    #: C/C++ 的**标量**类型拼写 → 本语言拼写（**只用于诊断里的改法**）。
    #:
    #: 它不是"别名表"：解析器**不认**这些拼写（`int x = 5;` 仍然是未知类型名），
    #: 只在 `E-Cast-CStyle` 的消息里说清"你写的这个宽度，在本语言叫什么"。
    #: 宽度与符号**照 C++ 的定义搬**（`int` 32 位有符号、`char` 无符号 8 位——
    #: 本语言 `char` 归一成 `u8`，见 `parse_type`）。
    _C_SCALAR_SPELLING = {
        "int": "i32", "signed": "i32", "unsigned": "u32",
        "long": "i64", "unsigned_long": "u64", "short": "i16",
        "char": "u8", "size_t": "u64", "ptrdiff_t": "i64",
        "float": "f32", "double": "f64", "bool": "bool",
    }

    def _cast_operand_text(self) -> str:
        """`(T)` 后面那个**操作数**的源码原文（给"照抄这一行"用）。

        取法：在**当前位置**解一个前缀+后缀表达式，然后把 token 的
        `start` 位置之间的源码切出来。`(int)xs.size()` 的 `(int)` 已经消费完，
        所以切片正好是 `xs.size()`。

        **不改任何状态**：位置解析完就回退（消息发出去之后就抛异常了，
        但这里仍然回退——将来若有人把消息做成 warning，回退也不能少）。
        切不出来（没有源码原文 / 操作数不是合法表达式 / 太长）⇒ 空串，
        调用方退回通用写法。
        """
        if not self.src:
            return ""
        start = self.peek().start if self.peek() is not None else len(self.src)
        save = self.i
        try:
            self.parse_postfix(self.parse_prefix())
            end = self.peek().start if self.peek() is not None else len(self.src)
        except (ParseError, RecursionError):
            return ""
        finally:
            self.i = save
        if end <= start or end > len(self.src):
            return ""
        text = self.src[start:end].replace("\r", " ").replace("\n", " ").strip()
        # 太长就不是"一行可复制"了（例如整段表达式跨行）；此时宁可别给。
        return text if 0 < len(text) <= 100 else ""

    def _c_style_cast_message(self, tname: str) -> str:
        """`E-Cast-CStyle` 的消息：**这一处**该怎么改写（三条路，按常用度排）。

        ## 为什么给三条，而不是一条

        这条诊断的**全部价值**是"复制一行就能过"，而"过"要过**整条链路**：
        `check` → `run` → `dsh cpp`。三条路各自不同的地方在于**能不能发射**：

        | 改法 | check | run | `dsh cpp` |
        |---|---|---|---|
        | A 删掉 `(T)`（不需要转换时） | ✓ | ✓ | ✓ |
        | B 带类型的中间变量（`i32 n = X;`） | ✓ | ✓ | ✓ |
        | C `X as T`（通用拼写） | ✓ | ✓ | **✗**（`dsh cpp` 还不发射 `as`） |

        所以顺序是 A → B → C：先问"这个转换真的需要吗"（6 处真实用例里
        `(int)xs.size()` 全是为了对齐 printf 的 `%d`，而 `.eln` 的
        `print` / `format` 按值类型发射，根本不需要窄化），要窄化就落成
        **带类型的中间变量**，最后才是通用拼写。

        以前的消息是 `例：(u8)x → x as u8`：读者要自己把 `u8` 换成他写的类型、
        再把自己那半个表达式抄过去，而且照抄之后 `dsh cpp` 还会再报一次
        `E-Cxx-Unsupported`——多一步手工就有第二次出错机会。
        """
        target = self._C_SCALAR_SPELLING.get(tname, "")
        if not target and tname in BUILTIN_TYPE_NAMES:
            target = tname
        operand = self._cast_operand_text()
        _t = target or "T"
        out = [
            f"本语言没有 C 风格强转 `({tname})`：标量转换**只有一种写法** "
            f"`x as T`（C++ 的 `(T)x` 是第二套拼写——同一个转换两种写法，"
            f"迟早出现「同一个式子两种读法」）。",
        ]
        # A：多数情况下根本不需要转换
        if operand:
            out.append(f"改法 A（多数情况**就该这样**：不需要转换就删掉）→ `{operand}`")
            # B：确实要窄化
            out.append(f"改法 B（确实要窄化）→ 先落到带类型的中间变量再往下用："
                       f"`{_t} n = {operand};`"
                       f"（全链路可用：check / run / dsh cpp）")
            # C：通用拼写
            out.append(f"改法 C（通用拼写）→ `{operand} as {_t}`：语义与 "
                       f"`({tname})` 相同；**注意** `dsh cpp` 目前不发射 `as`"
                       f"（会报 `E-Cxx-Unsupported`），移植库代码时优先 A / B。")
        else:
            out.append("改法 A（多数情况**就该这样**：不需要转换就删掉 `(T)`）")
            out.append(f"改法 B（确实要窄化）→ 带类型的中间变量：`{_t} n = X;`"
                       f"（全链路可用：check / run / dsh cpp）")
            out.append(f"改法 C（通用拼写）→ `X as {_t}`；`dsh cpp` 目前不发射 "
                       f"`as`，移植库代码时优先 A / B。")
        if target and tname != target:
            out.append(f"· `{tname}` 是 C/C++ 的拼写，本语言写 `{target}`"
                       f"（宽度与符号不改：`int` = 32 位有符号 ⇒ `i32`）。")
        return "\n".join(out)

    def parse_range_args(self) -> Node:
        """`range(起, 止)` 或 `range(起, 止, *k / +k)`。

        步长不是普通表达式（`*2` 不是合法表达式开头），所以单独解析。
        语义约束（严格递增、点数 ≤ 64）由 checker 校验。
        """
        args = Node("args")
        while not self.at(Kind.RPAREN) and not self.at(Kind.EOF):
            if self.at(Kind.STAR) or self.at(Kind.PLUS) or self.at(Kind.MINUS):
                op = self.advance()
                node = Node("step", op.line, op.col, value=op.text)
                node.add(self.parse_expr())
                args.add(node)
            else:
                args.add(self.parse_expr())
            if not self.accept(Kind.COMMA):
                break
        return args

    def parse_args_until(self, close: Kind) -> Node:
        args = Node("args")
        # **第 5 条**（P0 兼容层）：只有**实参列表里**才允许出现
        # `xs.begin()` / `xs.end()`（做成临时 `iter-arg` 节点）；真正的
        # "配平就折成 `xs`"在 `_compat_fold_iter_args` 里判。
        # 这层计数关掉时（compat=False）下面的 `+ 1` 都不发生，行为不变。
        self._iter_arg_depth += 1
        try:
            while not self.at(close) and not self.at(Kind.EOF):
                if self.kind() == Kind.IDENT and self.kind(1) == Kind.EQ:
                    at_ = self.advance()
                    self.advance()
                    args.add(Node("named-arg", at_.line, at_.col, name=at_.text).add(self.parse_expr()))
                else:
                    args.add(self.parse_expr())
                if not self.accept(Kind.COMMA):
                    break
        finally:
            self._iter_arg_depth -= 1
        if self.compat:
            args = self._compat_fold_iter_args(args)
        return args

    # ---------------------------------------------------------- 终止符

    def expect_end(self, kind: str, required: bool = True):
        """v0.68：`end <kind>` 已下线，块只用 `}` 收尾。

        **判据是位置**（2026-11-10）：只有紧跟块收尾 `}`（或后跟种类名）的
        `end` 才是退役写法（`_at_end_terminator`）。`end` 作参数名 / 变量名 /
        成员名一律合法——词法器只出 `IDENT`，不再按词形误伤。

        遇到旧写法就报 `E-End-Removed`，并把后面的种类名一起吃掉（避免连环误报）。
        """
        if not self._at_end_terminator():
            return
        t = self.advance()
        self.error("E-End-Removed",
                   f"`end` 已下线：{RETIRED_HINT['end']}（这里是 {kind}）", t)
        kt = self.peek()
        if (kt.text or "") in END_KIND or (kt.text or "") == "func":
            self.advance()


# ---------------------------------------------------------------- 入口

def _has_type_auto(node: Optional[Node]) -> bool:
    """子树里有没有 `type-auto`（P0 兼容层第 3 条的待推断类型）。"""
    if node is None:
        return False
    if getattr(node, "kind", "") == "type-auto":
        return True
    return any(_has_type_auto(c) for c in (node.children or []))


def _has_cast_static(node: Optional[Node]) -> bool:
    """子树里有没有 `cast-static`（P0 兼容层第 11 条的待判定转换）。"""
    if node is None:
        return False
    if getattr(node, "kind", "") == "cast-static":
        return True
    return any(_has_cast_static(c) for c in (node.children or []))


def parse_source(src: str, src_name: str = "<input>") -> ParseResult:
    lex = Lexer(src).run()
    toks = list(lex.tokens)
    if not toks or toks[-1].kind != Kind.EOF:
        toks.append(Token(Kind.EOF, "", 1, 1, len(src), len(src)))

    p = Parser(toks, src_name, src)
    mod = p.parse_module()

    # **第 3 条**（P0 兼容层）：`auto x = …;` 的**尽力而为**的一遍推断。
    #
    # 为什么在 `parse_source` 里做：`auto` 的类型必须在**任何**下游之前定下来
    # ——`cost` / `build` / `stats` 拿到的模块如果还带 `type-auto`，κ 就会按
    # "类型未知"去估。这里只做**单个文件看得见**的推断（初值是本文件的
    # 函数 / 变量 / 字面量）；推断不出就**保持原样**、不留诊断，交给
    # `dsh` 里成对的 `typecheck`（那时还能看见 `.tu` 的 `spec`）报
    # `E-Compat-AutoNoType`。推断实现只有一份：`typecheck._compat_auto_ty`。
    if mod is not None and _compat.enabled() and _has_type_auto(mod):
        try:
            from typecheck import resolve_auto as _resolve_auto
            _resolve_auto(mod)
        except Exception:                                # noqa: BLE001
            pass        # 推断失败不是解析失败：诊断由成对类型检查统一报

    # **第 11 条**（P0 兼容层）：`static_cast<…>(x)` 的**尽力而为**的一遍折叠。
    #
    # 判据与 `auto` 同一条：只有**本文件看得见的静态类型**能证明"值逐位不变"
    # 时才折；证明不了就**保持 `cast-static`**，交给 `dsh` 里成对的那遍
    # （能看见 `.tu`）判——真错误由 `check` 统一报 `E-Cast-Unsupported`。
    # 折叠实现只有一份：`typecheck._static_cast_fold_ok`（与检查期同一条判据）。
    if mod is not None and _compat.enabled() and _has_cast_static(mod):
        try:
            from typecheck import resolve_static_cast as _resolve_casts
            _resolve_casts(mod)
        except Exception:                                # noqa: BLE001
            pass        # 折叠失败不是解析失败：诊断由成对类型检查统一报

    diags: List[Diagnostic] = [
        Diagnostic(code=d.code, message=d.message, line=d.line, col=d.col,
                   start=d.start, end=d.end)
        for d in lex.diagnostics
    ]
    diags.extend(p.diags)
    # **把诊断挂到 module 上**（2026-11-01）：下游（`CostInterp`）要知道
    # "这棵树是不是没解析干净"——不干净就不许给出一个数（见 cost_interp）。
    if mod is not None:
        try:
            mod._parse_diags = list(diags)
        except AttributeError:
            pass
    return ParseResult(module=mod, diagnostics=diags, tokens=toks, soft=p.soft)


def parse_file(path: str) -> ParseResult:
    with open(path, "r", encoding="utf-8") as f:
        return parse_source(f.read(), path)


def main(argv: List[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2

    as_json = "--json" in argv
    files = [a for a in argv[1:] if not a.startswith("--")]

    rc = 0
    for path in files:
        res = parse_file(path)
        if as_json:
            print(json.dumps({
                "file": path,
                "ok": res.ok,
                "diagnostics": [d.to_dict() for d in res.diagnostics],
                "ast": res.module.to_dict() if res.module else None,
            }, ensure_ascii=False, indent=2))
        else:
            print(f"=== {path} ===")
            print(res.module.dump() if res.module else "(no module)")
            if res.diagnostics:
                print(f"-- {len(res.diagnostics)} 条诊断 --")
                for d in res.diagnostics:
                    print(f"  {d.code} @{d.line}:{d.col}  {d.message}")
        if res.diagnostics:
            rc = 1
    return rc


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv))
