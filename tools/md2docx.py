#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""
md2docx.py —— 把我们的 markdown 转成排版可读的 docx

只支持本仓库里实际用到的那些写法（不是通用转换器）：
  # ## ### 标题 / 段落 / - 与 1. 列表 / | 表格 | / ``` 代码块 / > 引用 / --- 分隔线
  行内 **粗体** 与 `代码`

中文排版：正文用「微软雅黑」，代码用「Consolas」，并显式设置 w:eastAsia，
否则中文在 Word 里会掉回默认字体。
"""

import io
import os
import re
import sys

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

BODY_FONT = "微软雅黑"
CODE_FONT = "Consolas"

#: A4 宽 21.0 cm − 左右边距各 2.4 cm = **可用宽度**（表格列宽按它分配）
USABLE_CM = 16.2


def set_cjk(run, font=BODY_FONT):
    run.font.name = font
    rPr = run._element.get_or_add_rPr()
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.append(rFonts)
    rFonts.set(qn("w:eastAsia"), font)
    rFonts.set(qn("w:ascii"), font)
    rFonts.set(qn("w:hAnsi"), font)


def shade(paragraph, hex_fill="F2F2F2"):
    pPr = paragraph._p.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:fill"), hex_fill)
    pPr.append(shd)


INLINE = re.compile(r"(\*\*.+?\*\*|`[^`]+`|\*[^*\s][^*]*\*)")


def add_inline(paragraph, text, size=None, bold=False, code=False):
    """处理 **粗体** 与 `代码`，支持两者嵌套（如 **`pending`**）。"""
    for part in INLINE.split(text):
        if not part:
            continue
        if (part.startswith("**") and part.endswith("**")
                and len(part) > 4 and not bold):
            add_inline(paragraph, part[2:-2], size, bold=True, code=code)
            continue
        if (part.startswith("`") and part.endswith("`")
                and len(part) > 2 and not code):
            add_inline(paragraph, part[1:-1], size, bold=bold, code=True)
            continue
        # **斜体**：`*text*`（单个星号；`**` 已在上面处理掉）——
        # python-docx 的 run 支持 italic，这里就地设置，不再递归
        if (part.startswith("*") and part.endswith("*")
                and len(part) > 2 and not part.startswith("**")):
            r = paragraph.add_run(part[1:-1].replace("`", ""))
            set_cjk(r, CODE_FONT if code else BODY_FONT)
            r.italic = True
            if bold:
                r.bold = True
            if size is not None:
                r.font.size = Pt(size)
            continue
        # 单元格里的 <br> 转成真正的换行
        segments = part.split("<br>")
        for k, seg in enumerate(segments):
            if k > 0:
                br = paragraph.add_run()
                set_cjk(br, BODY_FONT)
                br.add_break()
            if not seg:
                continue
            r = paragraph.add_run(seg)
            set_cjk(r, CODE_FONT if code else BODY_FONT)
            if bold:
                r.bold = True
            base = size or 10.5
            r.font.size = Pt(base - 1 if code else base)


def _add_page_numbers(doc) -> None:
    """在页脚插入 `PAGE` / `NUMPAGES` 域（发布稿的可读性要求）。"""
    from docx.enum.text import WD_ALIGN_PARAGRAPH

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


def convert(md_path, docx_path):
    lines = io.open(md_path, encoding="utf-8").read().split("\n")
    doc = Document()

    # 页边距
    for section in doc.sections:
        section.left_margin = Cm(2.4)
        section.right_margin = Cm(2.4)
        section.top_margin = Cm(2.2)
        section.bottom_margin = Cm(2.2)

    style = doc.styles["Normal"]
    style.font.name = BODY_FONT
    style.font.size = Pt(10.5)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), BODY_FONT)

    # **页码**（发布稿需要）：页脚居中 `第 N 页 / 共 M 页` 用域实现，
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
                set_cjk(r, CODE_FONT)
                r.font.size = Pt(9)
            doc.add_paragraph().paragraph_format.space_after = Pt(2)
            continue

        # 表格
        if s.startswith("|") and i + 1 < n and re.match(r"^\|[\s:|-]+\|$", lines[i + 1].strip()):
            header = [c.strip() for c in s.strip("|").split("|")]
            i += 2
            rows = []
            while i < n and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            table = doc.add_table(rows=1, cols=len(header))
            table.style = "Table Grid"
            table.alignment = WD_TABLE_ALIGNMENT.CENTER
            # **列宽按内容分配**（否则 Word 自动均分，长文本列会被挤成一条）
            _weights = []
            for _k in range(len(header)):
                _lens = [len(header[_k])] + [len(r[_k]) for r in rows if _k < len(r)]
                # 中文按 2 个字符宽算；单列给个下限，避免太窄
                # 下限 9：避免表头很短（如 "Detected"）时被压成一两个字母换行
                _w = max(9, min(max(_lens) if _lens else 9, 64))
                _weights.append(_w)
            _total = sum(_weights) or 1
            _widths = [max(1.9, USABLE_CM * w / _total) for w in _weights]
            _scale = USABLE_CM / sum(_widths)
            _widths = [w * _scale for w in _widths]
            table.autofit = False
            _tblPr = table._tbl.tblPr
            _layout = OxmlElement("w:tblLayout")
            _layout.set(qn("w:type"), "fixed")
            _tblPr.append(_layout)
            for _k, _w in enumerate(_widths):
                table.columns[_k].width = Cm(_w)
            # 表头跨页重复
            trPr = table.rows[0]._tr.get_or_add_trPr()
            tblHeader = OxmlElement("w:tblHeader")
            tblHeader.set(qn("w:val"), "true")
            trPr.append(tblHeader)
            for k, h in enumerate(header):
                cell = table.rows[0].cells[k]
                cell.text = ""
                p = cell.paragraphs[0]
                add_inline(p, h, size=9.5)
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
                    cells[k].text = ""
                    add_inline(cells[k].paragraphs[0], row[k], size=9.5)
            doc.add_paragraph().paragraph_format.space_after = Pt(2)
            continue

        # 标题
        m = re.match(r"^(#{1,4})\s+(.*)$", s)
        if m:
            level = len(m.group(1))
            p = doc.add_heading(level=min(level, 4) - 1 if level > 1 else 0)
            p.text = ""
            add_inline(p, m.group(2))
            for r in p.runs:
                r.font.color.rgb = RGBColor(0x1F, 0x3A, 0x5F)
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
                add_inline(p, text, size=10)
            continue
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
                if (not _s) or _s.startswith(("|", "#", ">", "```", "---")) \
                        or re.match(r"^([-*]|\d+\.)\s+", _s):
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
            if (not _s) or _s.startswith(("|", "#", ">", "```", "---")) \
                    or re.match(r"^([-*]|\d+\.)\s+", _s):
                break
            buf.append(_s)
            i += 1
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(4)
        add_inline(p, " ".join(buf))

    doc.save(docx_path)
    return docx_path


def main(argv):
    if len(argv) < 2:
        print("用法：md2docx.py <输入.md> <输出.docx> [...]", file=sys.stderr)
        return 2
    for k in range(0, len(argv) - 1, 2):
        src, dst = argv[k], argv[k + 1]
        convert(src, dst)
        print(f"  {os.path.basename(dst)}  ←  {os.path.basename(src)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
