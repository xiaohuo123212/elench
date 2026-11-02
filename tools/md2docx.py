#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""
md2docx.py —— 把我们的 markdown 转成排版可读的 docx

只支持本仓库里实际用到的那些写法（不是通用转换器）：
  # ## ### #### 标题 / 段落 / - 与 1. 列表 / | 表格 | / ``` 代码块 / > 引用 / --- 分隔线
  行内 **粗体** / *斜体* / `代码` / 独立一行的 ![alt](相对路径) 图片

版式（与预印本现版一致，无需再跑后处理）：
  * 文档标题 Arial + 黑体 20 pt 居中；H1 15 / H2 12.5 / H3 11.5 / H4 10.5 pt；
  * 正文 Times New Roman + 宋体 10.5 pt；行内代码与代码块 Consolas 9.5 pt；
  * 表格正文 9 pt（表内代码也是 9 pt），列宽按内容与最长 token 重新分配；
  * 独立一行的 `![alt](相对路径)` → 按页宽（约 6.0 英寸 = 15.2 cm）嵌入，
    下一行的 `**Figure N.**` 图注仍按普通段落渲染，不会被吃掉。

文档属性：md 顶部的 Field/Value 元信息表会写进 doc.core_properties
（Title / Author / Subject / Keywords / Category / Comments / Last-Modified-By）；
没有 Title 字段时用第一个 `# ` 标题兜底，两者都没有就留空（不会留下
python-docx 模板自带的 "python-docx"）。

`--no-images` 关闭嵌图，`![...](...)` 会作为普通文字保留。

中文排版必须显式设置 w:eastAsia，否则中文在 Word 里会掉回默认字体。
"""

import argparse
import io
import os
import re
import sys

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Inches, Pt, RGBColor

BODY_ASCII = "Times New Roman"
BODY_CJK = "宋体"
HEAD_ASCII = "Arial"
HEAD_CJK = "黑体"
CODE_FONT = "Consolas"

BODY_SIZE = 10.5
CODE_SIZE = 9.5
TABLE_SIZE = 9.0
TABLE_CODE_SIZE = 9.0
HEAD_COLOR = RGBColor(0x1A, 0x1A, 0x1A)

#: 样式名 -> 字号（pt）。md 的 `#` 走 Title，`##` 走 Heading 1，依此类推。
HEADING_SIZES = {
    "Title": 20.0,
    "Heading 1": 15.0,
    "Heading 2": 12.5,
    "Heading 3": 11.5,
    "Heading 4": 10.5,
}

#: A4 宽 21.0 cm − 左右边距各 2.4 cm = **可用宽度**（表格列宽按它分配）
USABLE_CM = 16.2

#: 嵌图宽度：约 6.0 英寸，且不超过版心宽度
IMAGE_WIDTH = min(Inches(6.0), Cm(USABLE_CM))


def set_run_fonts(run, ascii_font, cjk_font, size=None,
                  color=None, bold=None, italic=None):
    """同时设置 ascii / hAnsi / eastAsia / cs 四个字体槽。"""
    run.font.name = ascii_font
    rPr = run._element.get_or_add_rPr()
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.append(rFonts)
    rFonts.set(qn("w:eastAsia"), cjk_font)
    rFonts.set(qn("w:ascii"), ascii_font)
    rFonts.set(qn("w:hAnsi"), ascii_font)
    rFonts.set(qn("w:cs"), ascii_font)
    if size is not None:
        run.font.size = Pt(size)
    if color is not None:
        run.font.color.rgb = color
    if bold is not None:
        run.bold = bold
    if italic is not None:
        run.italic = italic


def shade(paragraph, hex_fill="F2F2F2"):
    pPr = paragraph._p.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:fill"), hex_fill)
    pPr.append(shd)


INLINE = re.compile(r"(\*\*.+?\*\*|`[^`]+`|\*[^*\s][^*]*\*)")


def add_inline(paragraph, text, *, size=None, code_size=None, bold=False,
               code=False, ascii_font=None, cjk_font=None, color=None):
    """处理 **粗体** / *斜体* / `代码`，支持嵌套（如 **`pending`**）。

    size 是这一段的基准字号；code_size 是行内代码的字号（默认 9.5 pt，
    表格里传 9.0 pt）。字体默认正文 Times New Roman + 宋体，代码 Consolas。
    """
    if size is None:
        size = BODY_SIZE
    if code_size is None:
        code_size = CODE_SIZE
    # 行内代码永远是 Consolas（后处理也是按 run 的字体名识别代码的）；
    # 其余 run 用调用方指定的字体，默认正文 Times New Roman + 宋体。
    if code:
        ascii_font = cjk_font = CODE_FONT
    else:
        ascii_font = ascii_font or BODY_ASCII
        cjk_font = cjk_font or BODY_CJK
    run_size = code_size if code else size

    for part in INLINE.split(text):
        if not part:
            continue
        if (part.startswith("**") and part.endswith("**")
                and len(part) > 4 and not bold):
            add_inline(paragraph, part[2:-2], size=size, code_size=code_size,
                       bold=True, code=code, ascii_font=ascii_font,
                       cjk_font=cjk_font, color=color)
            continue
        if (part.startswith("`") and part.endswith("`")
                and len(part) > 2 and not code):
            add_inline(paragraph, part[1:-1], size=size, code_size=code_size,
                       bold=bold, code=True, ascii_font=ascii_font,
                       cjk_font=cjk_font, color=color)
            continue
        # *斜体*：`*text*`（单个星号；`**` 已在上面处理掉）
        if (part.startswith("*") and part.endswith("*")
                and len(part) > 2 and not part.startswith("**")):
            r = paragraph.add_run(part[1:-1].replace("`", ""))
            set_run_fonts(r, ascii_font, cjk_font, run_size,
                          color=color, bold=True if bold else None, italic=True)
            continue
        # 单元格里的 <br> 转成真正的换行
        segments = part.split("<br>")
        for k, seg in enumerate(segments):
            if k > 0:
                br = paragraph.add_run()
                set_run_fonts(br, ascii_font, cjk_font, run_size)
                br.add_break()
            if not seg:
                continue
            r = paragraph.add_run(seg)
            set_run_fonts(r, ascii_font, cjk_font, run_size,
                          color=color, bold=True if bold else None)


def _add_page_numbers(doc) -> None:
    """在页脚插入 `PAGE` / `NUMPAGES` 域（发布稿的可读性要求）。"""
    for section in doc.sections:
        footer = section.footer
        para = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for _txt, _field in (("", "PAGE"), (" / ", "NUMPAGES")):
            if _txt:
                r = para.add_run(_txt)
                r.font.size = Pt(8)
            fld_begin = OxmlElement("w:fldChar")
            fld_begin.set(qn("w:fldCharType"), "begin")
            instr = OxmlElement("w:instrText")
            instr.set(qn("xml:space"), "preserve")
            instr.text = _field
            fld_end = OxmlElement("w:fldChar")
            fld_end.set(qn("w:fldCharType"), "end")
            run = para.add_run()
            run._r.append(fld_begin)
            run._r.append(instr)
            run._r.append(fld_end)
            run.font.size = Pt(8)


# ---------------------------------------------------------------------------
# core properties：md 顶部的 Field/Value 表
# ---------------------------------------------------------------------------

#: 元信息字段（小写） -> python-docx core_properties 属性
FIELD_TO_PROP = {
    "title": "title",
    "author": "author",
    "subject": "subject",
    "keywords": "keywords",
    "category": "category",
    "comments": "comments",
    "last-modified-by": "last_modified_by",
    "last modified by": "last_modified_by",
}

#: "Wang, Haolin — 某大学 — ORCID ..." 里的分隔符
AUTHOR_SEP = re.compile(r"\s+[—–]\s+|\s+-\s+")

MARKUP = re.compile(r"\*\*|`")

TABLE_SEP = re.compile(r"^\|[\s:|-]+\|$")


def _cells(row):
    return [c.strip() for c in row.strip().strip("|").split("|")]


def _plain(text):
    """去掉行内 markdown 标记，属性值不该带 `**` / 反引号。"""
    return MARKUP.sub("", text).strip()


def parse_metadata(lines):
    """解析 md 顶部的 Field/Value 两列表，返回 {属性名: 值}。

    只认第一个 `## ` 标题之前、表头恰好是 Field/Value 的第一张表；
    找不到就返回空字典（调用方保持留空）。
    """
    meta = {}
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if re.match(r"^##\s", s):          # 正文开始，元信息区结束
            break
        if (s.startswith("|") and i + 1 < len(lines)
                and TABLE_SEP.match(lines[i + 1].strip())):
            header = [c.lower() for c in _cells(s)]
            if header[:2] == ["field", "value"]:
                i += 2
                while i < len(lines) and lines[i].strip().startswith("|"):
                    cells = _cells(lines[i])
                    if len(cells) >= 2:
                        prop = FIELD_TO_PROP.get(cells[0].lower())
                        if prop and cells[1]:
                            meta[prop] = _plain(cells[1])
                    i += 1
                break
        i += 1
    return meta


def _first_heading_title(lines):
    for line in lines:
        m = re.match(r"^#\s+(.*)$", line.strip())
        if m:
            return _plain(m.group(1))
    return ""


def apply_metadata(doc, lines):
    """把元信息表写进 doc.core_properties；缺失的字段留空。"""
    meta = parse_metadata(lines)
    cp = doc.core_properties

    # python-docx 默认模板带 author="python-docx"、comments="generated by
    # python-docx"，必须先清掉，否则没有元信息的 md 也会继承这些假值。
    cp.title = ""
    cp.author = ""
    cp.subject = ""
    cp.keywords = ""
    cp.category = ""
    cp.comments = ""
    cp.last_modified_by = ""

    if not meta.get("title"):
        meta["title"] = _first_heading_title(lines)

    author = meta.get("author", "")
    if author:
        # Author 字段的约定是「姓名 — 单位 — ORCID」；core property 只放姓名
        # （与现版 docx / PDF 一致），完整信息仍保留在文档顶部的表里。
        meta["author"] = AUTHOR_SEP.split(author, 1)[0].strip()

    for prop, value in meta.items():
        if value:
            setattr(cp, prop, value)
    if cp.author:
        cp.last_modified_by = cp.author
    return meta


# ---------------------------------------------------------------------------
# 表格列宽
# ---------------------------------------------------------------------------

def visual_len(s):
    """粗略显示宽度：CJK/全角算 2，其余算 1。"""
    n = 0
    for ch in s:
        n += 2 if ord(ch) > 0x2E7F else 1
    return n


def fit_columns(doc, usable_cm=USABLE_CM):
    """按内容与最长 token 重新分配列宽，避免把单词断成两行。

    只按 len() 加权会让单字符列（`#`）和长文本列一样宽，并把代码列挤到
    `build --require-measured` 折行。这里先用「最长 token」保证不断词，
    再把剩余宽度按「偏好宽度」比例分给各列。
    """
    CHAR_CM = 0.200                      # ≈ 一个 9 pt 字符（代码是等宽）
    PAD_CM = 0.42                        # 单元格左右内边距
    for tbl in doc.tables:
        ncols = len(tbl.columns)
        if ncols == 0:
            continue
        pref, need = [], []
        for k in range(ncols):
            widest, token = 1, 1
            for row in tbl.rows:
                cells = row.cells
                if k >= len(cells):
                    continue
                txt = cells[k].text.strip()
                widest = max(widest, visual_len(txt))
                for tok in txt.replace("/", " / ").split():
                    token = max(token, min(visual_len(tok), 18))
            pref.append(PAD_CM + CHAR_CM * widest)
            need.append(PAD_CM + CHAR_CM * token)
        need = [max(1.1, n) for n in need]
        total_need = sum(need)
        if total_need >= usable_cm:      # 无法避免断行
            widths = [usable_cm * n / total_need for n in need]
        else:
            spare = usable_cm - total_need
            extra = [max(0.0, p - n) for p, n in zip(pref, need)]
            tot_extra = sum(extra) or 1.0
            widths = [n + spare * e / tot_extra for n, e in zip(need, extra)]
        for k, w in enumerate(widths):
            tbl.columns[k].width = Cm(w)
            for row in tbl.rows:
                if k < len(row.cells):
                    row.cells[k].width = Cm(w)


# ---------------------------------------------------------------------------
# 图片
# ---------------------------------------------------------------------------

IMAGE_RE = re.compile(r"^!\[(.*?)\]\((.*?)\)\s*$")


def add_picture(doc, img_path, alt=""):
    """居中嵌入一张图，宽度适配版心（约 6.0 英寸）。"""
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    pf = p.paragraph_format
    pf.space_before = Pt(8)
    pf.space_after = Pt(2)
    pf.keep_with_next = True          # 图片与下一行的图注不分开
    inline = p.add_run().add_picture(img_path, width=IMAGE_WIDTH)
    if alt:
        try:                          # alt 写进 docPr/@descr，读屏软件可用
            inline._inline.docPr.set("descr", alt)
        except Exception:
            pass
    return p


def _add_md_image(doc, md_path, src, alt):
    """相对路径按 md 所在目录解析；文件不存在返回 False。"""
    path = src
    if not os.path.isabs(path):
        path = os.path.join(os.path.dirname(os.path.abspath(md_path)), path)
    if not os.path.isfile(path):
        print(f"  ! 图片不存在，保留占位文字：{src}", file=sys.stderr)
        return False
    add_picture(doc, path, alt)
    return True


# ---------------------------------------------------------------------------
# 转换
# ---------------------------------------------------------------------------

def _is_block_start(s):
    return (not s) or s.startswith(("|", "#", ">", "```", "---", "![")) \
        or re.match(r"^([-*]|\d+\.)\s+", s) is not None


def _clear_cell(cell):
    """清空单元格第一段并返回它。

    不用 `cell.text = ""`：那个 setter 会留下一个没有字体的空 run，
    后面按 run 改字体时会读到 font.name is None。
    """
    p = cell.paragraphs[0]
    for r in list(p.runs):
        r._element.getparent().remove(r._element)
    return p


def _style_caption(p):
    """表/图题注：与后文同页，并把间距收紧。"""
    if p.text.lstrip().startswith(("Table ", "Figure ")):
        pf = p.paragraph_format
        pf.keep_with_next = True
        pf.space_before = Pt(8)
        pf.space_after = Pt(3)


def convert(md_path, docx_path, *, images=True):
    lines = io.open(md_path, encoding="utf-8").read().split("\n")
    doc = Document()

    # 页边距
    for section in doc.sections:
        section.left_margin = Cm(2.4)
        section.right_margin = Cm(2.4)
        section.top_margin = Cm(2.2)
        section.bottom_margin = Cm(2.2)

    style = doc.styles["Normal"]
    style.font.name = BODY_ASCII
    style.font.size = Pt(BODY_SIZE)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), BODY_CJK)

    apply_metadata(doc, lines)

    # **页码**（发布稿需要）：页脚居中 `PAGE / NUMPAGES` 用域实现，
    # Word / LibreOffice 都能自动更新。
    _add_page_numbers(doc)

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        s = line.strip()

        # 代码块
        if s.startswith("```"):
            i += 1
            block = []
            while i < n and not lines[i].strip().startswith("```"):
                block.append(lines[i])
                i += 1
            i += 1
            for code_line in block:
                p = doc.add_paragraph()
                p.paragraph_format.space_before = Pt(0)
                p.paragraph_format.space_after = Pt(0)
                p.paragraph_format.line_spacing = 1.0
                p.paragraph_format.left_indent = Cm(0.4)
                shade(p)
                r = p.add_run(code_line if code_line.strip() else " ")
                set_run_fonts(r, CODE_FONT, CODE_FONT, CODE_SIZE)
            doc.add_paragraph().paragraph_format.space_after = Pt(2)
            continue

        # 图片：独立一行的 ![alt](相对路径)
        m = IMAGE_RE.match(s)
        if m:
            alt, src = m.group(1), m.group(2).strip()
            if images and _add_md_image(doc, md_path, src, alt):
                i += 1
                continue
            # --no-images 或图片缺失：按普通段落保留占位文字，信息不丢
            p = doc.add_paragraph()
            p.paragraph_format.space_after = Pt(4)
            add_inline(p, s)
            _style_caption(p)
            i += 1
            continue

        # 表格
        if s.startswith("|") and i + 1 < n and TABLE_SEP.match(lines[i + 1].strip()):
            header = _cells(s)
            i += 2
            rows = []
            while i < n and lines[i].strip().startswith("|"):
                rows.append(_cells(lines[i]))
                i += 1
            table = doc.add_table(rows=1, cols=len(header))
            table.style = "Table Grid"
            table.alignment = WD_TABLE_ALIGNMENT.CENTER
            table.autofit = False
            _tblPr = table._tbl.tblPr
            _layout = OxmlElement("w:tblLayout")
            _layout.set(qn("w:type"), "fixed")
            _tblPr.append(_layout)
            # 表头跨页重复
            trPr = table.rows[0]._tr.get_or_add_trPr()
            tblHeader = OxmlElement("w:tblHeader")
            tblHeader.set(qn("w:val"), "true")
            trPr.append(tblHeader)
            for k, h in enumerate(header):
                p = _clear_cell(table.rows[0].cells[k])
                add_inline(p, h, size=TABLE_SIZE, code_size=TABLE_CODE_SIZE)
                for r in p.runs:
                    r.bold = True
            for row in rows:
                new_row = table.add_row()
                # 行内不跨页断开，避免出现只剩半个单元格文字的难看情况
                trPr = new_row._tr.get_or_add_trPr()
                cant = OxmlElement("w:cantSplit")
                cant.set(qn("w:val"), "true")
                trPr.append(cant)
                cells = new_row.cells
                for k in range(min(len(row), len(header))):
                    add_inline(_clear_cell(cells[k]), row[k],
                               size=TABLE_SIZE, code_size=TABLE_CODE_SIZE)
            doc.add_paragraph().paragraph_format.space_after = Pt(2)
            continue

        # 标题
        m = re.match(r"^(#{1,4})\s+(.*)$", s)
        if m:
            level = len(m.group(1))
            # `#` -> Title，`##` -> Heading 1，`###` -> Heading 2，`####` -> Heading 3
            # 用 add_paragraph(style=...) 而不是 add_heading()：后者会先塞一个
            # 空 run，清 text 之后它仍然留着，字体检查会读到 None。
            style_name = "Title" if level == 1 else f"Heading {min(level, 4) - 1}"
            p = doc.add_paragraph(style=style_name)
            name = p.style.name
            size = HEADING_SIZES.get(name, 11.5)
            add_inline(p, m.group(2), size=size,
                       ascii_font=HEAD_ASCII, cjk_font=HEAD_CJK)
            for r in p.runs:
                r.bold = True
                r.font.color.rgb = HEAD_COLOR
            pf = p.paragraph_format
            pf.space_before = Pt(14 if name == "Title" else 10)
            pf.space_after = Pt(10 if name == "Title" else 4)
            pf.keep_with_next = True
            if name == "Title":
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                # Title 样式自带一条下边框（在 styles.xml 里），段落级删不掉，
                # 只能写一段显式 none 覆盖它。
                pPr = p._p.get_or_add_pPr()
                for bdr in pPr.findall(qn("w:pBdr")):
                    pPr.remove(bdr)
                pBdr = OxmlElement("w:pBdr")
                for side in ("top", "left", "bottom", "right"):
                    el = OxmlElement("w:" + side)
                    el.set(qn("w:val"), "none")
                    el.set(qn("w:sz"), "0")
                    pBdr.append(el)
                pPr.append(pBdr)
            i += 1
            continue

        # 分隔线
        if s == "---":
            p = doc.add_paragraph()
            pPr = p._p.get_or_add_pPr()
            pbdr = OxmlElement("w:pBdr")
            bottom = OxmlElement("w:bottom")
            bottom.set(qn("w:val"), "single")
            bottom.set(qn("w:sz"), "6")
            bottom.set(qn("w:color"), "BFBFBF")
            pbdr.append(bottom)
            pPr.append(pbdr)
            i += 1
            continue

        # 引用：**把连续的 `>` 行合并成一段**（跨行粗体才不会被排成字面星号）
        if s.startswith(">"):
            buf = [s.lstrip(">").strip()]
            i += 1
            while i < n and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip().lstrip(">").strip())
                i += 1
            text = " ".join(x for x in buf if x)
            if text:
                p = doc.add_paragraph()
                p.paragraph_format.left_indent = Cm(0.6)
                shade(p, "F7F7F7")
                add_inline(p, text)
                _style_caption(p)
            continue

        # 列表
        m = re.match(r"^([-*]|\d+\.)\s+(.*)$", s)
        if m:
            marker, body = m.group(1), m.group(2)
            if marker[0].isdigit():
                # **有序列表：写显式编号**，不用 Word 自动编号。
                # 自动编号在整个文档里共享一个编号定义，第二张列表会接着
                # 上一张的号继续（参考文献从 31. 开始就是这么来的）。
                p = doc.add_paragraph()
                p.paragraph_format.left_indent = Cm(1.0)
                p.paragraph_format.first_line_indent = Cm(-0.6)
            else:
                try:
                    p = doc.add_paragraph(style="List Bullet")
                except KeyError:
                    p = doc.add_paragraph()
                    p.paragraph_format.left_indent = Cm(0.6)
            # **续行并入本条**：markdown 里列表项折行不是新条目，
            # 不合并的话续行会被排成一段独立正文（又散又乱）
            i += 1
            cont = [body]
            while i < n:
                _s = lines[i].strip()
                if _is_block_start(_s):
                    break
                cont.append(_s)
                i += 1
            _txt = (marker + " ") if marker[0].isdigit() else ""
            add_inline(p, _txt + " ".join(cont))
            continue

        # 空行
        if not s:
            i += 1
            continue

        # 普通段落：**把连续的正文行合并成一段**
        # （markdown 里一个换行不等于换段；不合并的话，跨行的 `**粗体**`
        #  会被渲染成字面量星号，段间距也会变得又稀又散）
        buf = [s]
        i += 1
        while i < n:
            _s = lines[i].strip()
            if _is_block_start(_s):
                break
            buf.append(_s)
            i += 1
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(4)
        text = " ".join(buf)
        add_inline(p, text)
        _style_caption(p)

    fit_columns(doc)
    doc.save(docx_path)
    return docx_path


def main(argv):
    ap = argparse.ArgumentParser(
        prog="md2docx.py",
        description="把仓库里的 markdown 转成排版可读的 docx。")
    ap.add_argument("pairs", nargs="+", metavar="FILE",
                    help="<输入.md> <输出.docx>，可以成对给多组")
    ap.add_argument("--no-images", action="store_true",
                    help="不嵌入图片，![alt](path) 保留为普通文字")
    args = ap.parse_args(argv)
    if len(args.pairs) % 2:
        ap.error("输入与输出必须成对出现")
    for k in range(0, len(args.pairs), 2):
        src, dst = args.pairs[k], args.pairs[k + 1]
        convert(src, dst, images=not args.no_images)
        print(f"  {os.path.basename(dst)}  ←  {os.path.basename(src)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
