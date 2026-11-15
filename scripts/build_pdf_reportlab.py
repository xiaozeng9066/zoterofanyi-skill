#!/usr/bin/env python3
"""环境回退：当 Edge/Chromium 无法在沙箱内生成 PDF 时，用 reportlab 直接排版双语 JSON -> PDF。

仅依赖 reportlab（MinerU venv 已自带），使用内置 STSong-Light（Adobe-GB1）CID 字体覆盖
中文 + 拉丁 + 希腊（β/λ/ε/χ/µ）+ 重音字母，无需外部字体文件。

排版：每区块「英文段 -> 中文段」上下交错；表格/插图/公式用原图保真；参考文献保留原文；
每区块前标注「原文 p.X」。纯 ASCII 英文用 Helvetica，含中文/希腊/重音的段落用 STSong-Light。

用法: python build_pdf_reportlab.py <bilingual.json> <out.pdf> [页脚标签]
"""
import argparse, json, re, sys
from pathlib import Path

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.colors import HexColor
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Image,
                                PageBreak, KeepTogether, NextPageTemplate,
                                PageTemplate, Frame)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont

CJK = "STSong-Light"
pdfmetrics.registerFont(UnicodeCIDFont(CJK))

WIDTH, HEIGHT = A4
LM = RM = 20 * mm
TM = 18 * mm
BM = 16 * mm
AVAIL = WIDTH - LM - RM

# 上标/下标：数据里的 ^{1} / _{2} 在 reportlab 里用 <sup>/<sub> 迷你标签
SUP_RE = re.compile(r"\^\{([^}]*)\}")
SUB_RE = re.compile(r"_\{([^}]*)\}")
# 孤立上标（作者/单位编号常见 "Flamme^1" 或 "Flamme ^{1}" 已由上覆盖），
# 这里兜底处理 ^ 后跟单个非空白字符的写法（如 ^1 ^a ^†）
LONE_SUP_RE = re.compile(r"\^(?=[^\s{])")


def _html_escape(t: str) -> str:
    return (t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def rich(t: str) -> str:
    """转成 reportlab Paragraph 可用的富文本：先 HTML 转义，再把 ^{x}/_{x}/^x 转成上下标标签。

    顺序：先转义（避免把真实 < > & 当标签），再替换学术上下标写法。
    """
    if not t:
        return ""
    t = _html_escape(t)
    t = SUP_RE.sub(lambda m: "<sup>%s</sup>" % m.group(1), t)
    t = SUB_RE.sub(lambda m: "<sub>%s</sub>" % m.group(1), t)
    t = LONE_SUP_RE.sub("<sup>", t)
    return t


def norm(t: str) -> str:
    if not t:
        return ""
    t = (t.replace("ﬁ", "fi").replace("ﬂ", "fl").replace("ﬀ", "ff")
         .replace("ﬃ", "ffi").replace("ﬄ", "ffl")
         .replace("’", "'").replace("‘", "'")
         .replace("“", '"').replace("”", '"')
         .replace(" ", " "))  # NBSP -> space
    t = t.replace("｜", " | ").replace("—", "-")  # 全角竖线/破折号在 Helvetica 缺字形
    return t


def has_cjk(t: str) -> bool:
    return any('\u4e00' <= c <= '\u9fff' or '\u3400' <= c <= '\u4dbf' for c in t)


def font_for(t: str) -> str:
    if has_cjk(t):
        return CJK
    try:
        t.encode("cp1252")
    except Exception:
        return CJK
    return "Helvetica"


def make_styles():
    return {
        "title_zh": ParagraphStyle("title_zh", fontName=CJK, fontSize=17, leading=22,
                                   alignment=TA_CENTER, spaceAfter=4),
        "title_en": ParagraphStyle("title_en", fontName="Helvetica", fontSize=11,
                                   leading=15, alignment=TA_CENTER, spaceAfter=2,
                                   textColor=HexColor("#444")),
        "meta": ParagraphStyle("meta", fontName=CJK, fontSize=8.5, leading=12,
                               alignment=TA_CENTER, textColor=HexColor("#666")),
        "hr": Spacer(1, 6),
        "src": ParagraphStyle("src", fontName="Helvetica", fontSize=7.5, leading=9,
                              textColor=HexColor("#999"), spaceAfter=1),
        "h": ParagraphStyle("h", fontName="Helvetica-Bold", fontSize=12, leading=15,
                            spaceBefore=10, spaceAfter=4, textColor=HexColor("#111")),
        "h_cjk": ParagraphStyle("h_cjk", fontName=CJK, fontSize=12, leading=16,
                                spaceBefore=10, spaceAfter=4, textColor=HexColor("#111")),
        "en": ParagraphStyle("en", fontName="Helvetica", fontSize=10, leading=14.5,
                             alignment=TA_JUSTIFY, spaceAfter=2),
        "en_cjk": ParagraphStyle("en_cjk", fontName=CJK, fontSize=10, leading=15,
                                 alignment=TA_JUSTIFY, spaceAfter=2),
        "zh": ParagraphStyle("zh", fontName=CJK, fontSize=10.5, leading=16,
                             alignment=TA_JUSTIFY, spaceAfter=6,
                             textColor=HexColor("#1a1a1a")),
        "zh_indent": ParagraphStyle("zh_indent", parent=None, fontName=CJK, fontSize=10.5,
                                    leading=16, alignment=TA_JUSTIFY, spaceAfter=6,
                                    leftIndent=6, textColor=HexColor("#1a1a1a")),
        "ref": ParagraphStyle("ref", fontName=CJK, fontSize=8.5, leading=12,
                              alignment=TA_LEFT, spaceAfter=1),
        "fn": ParagraphStyle("fn", fontName=CJK, fontSize=8.5, leading=12,
                             alignment=TA_LEFT, spaceBefore=4, spaceAfter=4,
                             textColor=HexColor("#333")),
        "cap_zh": ParagraphStyle("cap_zh", fontName=CJK, fontSize=9.5, leading=13,
                                 alignment=TA_CENTER, spaceBefore=3, spaceAfter=1,
                                 textColor=HexColor("#111")),
        "cap_en": ParagraphStyle("cap_en", fontName="Helvetica", fontSize=8.5, leading=11,
                                 alignment=TA_CENTER, spaceAfter=6,
                                 textColor=HexColor("#555")),
    }


def src_label(pages):
    if not pages:
        return ""
    if len(pages) == 1:
        return f"原文 p.{pages[0]}"
    return f"原文 p.{pages[0]}–{pages[-1]}"


def build(json_in, out_pdf, label=""):
    d = json.loads(Path(json_in).read_text(encoding="utf-8"))
    meta = d.get("meta", {})
    blocks = d.get("blocks", [])
    workdir = Path(json_in).resolve().parent
    st = make_styles()
    story = []

    # ---- 标题页信息 ----
    story.append(Paragraph(rich(norm(meta.get("title_zh", ""))), st["title_zh"]))
    story.append(Paragraph(rich(norm(meta.get("title_en", ""))), st["title_en"]))
    story.append(Spacer(1, 3))
    auth = meta.get("author_year", "")
    jour = meta.get("journal", "")
    doi = meta.get("doi", "")
    meta_line = " · ".join(x for x in [auth, jour, (f"doi:{doi}" if doi else "")] if x)
    story.append(Paragraph(rich(norm(meta_line)), st["meta"]))
    item = meta.get("item_key", "")
    story.append(Paragraph(f"Zotero 条目 {item} | 中英对照全文", st["meta"]))
    story.append(Spacer(1, 8))

    # ---- 正文 ----
    # 宽表格/宽插图自动转横向：先判断图片宽高比，宽 >> 高（宽表）则整块切到横版页
    wide_ratio = 1.25  # 宽/高 超过此值视为「横向更合适」的宽表/宽图
    landscape_active = False

    for b in blocks:
        kind = b.get("kind")
        pages = b.get("pages") or []
        sl = src_label(pages)
        if sl:
            story.append(Paragraph(sl, st["src"]))

        # 检测是否宽图/宽表，决定是否切横向
        if kind in ("table", "figure"):
            img = b.get("image", "")
            want_landscape = False
            if img:
                ip = (workdir / img) if not Path(img).is_absolute() else Path(img)
                if ip.exists():
                    try:
                        from reportlab.lib.utils import ImageReader
                        iw, ih = ImageReader(str(ip)).getSize()
                        want_landscape = (iw / ih) > wide_ratio
                    except Exception:
                        want_landscape = False
            if want_landscape and not landscape_active:
                story.append(NextPageTemplate("landscape"))
                story.append(PageBreak())
                landscape_active = True
            elif not want_landscape and landscape_active:
                story.append(NextPageTemplate("portrait"))
                story.append(PageBreak())
                landscape_active = False

        if kind == "title":
            # 标题已在顶部，正文里若再次出现则作为普通标题
            en = rich(norm(b.get("en", ""))); zh = rich(norm(b.get("zh", "")))
            if zh:
                story.append(Paragraph(zh, st["h_cjk"]))
            if en and en != meta.get("title_en", ""):
                story.append(Paragraph(en, st["h"]))
        elif kind == "heading":
            en = rich(norm(b.get("en", ""))); zh = rich(norm(b.get("zh", "")))
            if zh:
                story.append(Paragraph(zh, st["h_cjk"]))
            if en:
                story.append(Paragraph(en, st["h"]))
        elif kind == "para" and b.get("no_translate"):
            en = rich(norm(b.get("en", "")))
            if en:
                for line in en.split("\n"):
                    line = line.strip()
                    if line:
                        story.append(Paragraph(line, st["ref"]))
                story.append(Spacer(1, 4))
        elif kind == "para":
            en = rich(norm(b.get("en", ""))); zh = rich(norm(b.get("zh", "")))
            if en:
                story.append(Paragraph(en, st["en_cjk"] if font_for(en) == CJK else st["en"]))
            if zh:
                story.append(Paragraph(zh, st["zh"]))
        elif kind == "footnote":
            en = rich(norm(b.get("en", ""))); zh = rich(norm(b.get("zh", "")))
            mk = b.get("marker", "")
            txt = f"注 {mk}：{zh}" if zh else f"注 {mk}：{en}"
            story.append(Paragraph(txt, st["fn"]))
        elif kind in ("equation", "figure", "table"):
            img = b.get("image", "")
            if img:
                ip = (workdir / img) if not Path(img).is_absolute() else Path(img)
                if ip.exists():
                    try:
                        from reportlab.lib.utils import ImageReader
                        iw, ih = ImageReader(str(ip)).getSize()
                        # 横版页可用宽度更大（A4 横过来），宽表放得下
                        page_w = WIDTH if not landscape_active else HEIGHT
                        page_h = HEIGHT if not landscape_active else WIDTH
                        maxw = page_w - LM - RM
                        maxh = page_h - TM - BM - 40 * mm
                        scale = min(maxw / iw, maxh / ih, 1.0)
                        w = iw * scale; h = ih * scale
                        story.append(Image(str(ip), width=w, height=h))
                    except Exception as e:
                        story.append(Paragraph(f"[图片缺失: {img} ({e})]", st["cap_en"]))
            cz = rich(norm(b.get("caption_zh", ""))); ce = rich(norm(b.get("caption_en", "")))
            ne = rich(norm(b.get("note_en", ""))); nz = rich(norm(b.get("note_zh", "")))
            if kind != "equation":
                if cz:
                    story.append(Paragraph(cz, st["cap_zh"]))
                if ce:
                    story.append(Paragraph(ce, st["cap_en"]))
            if nz:
                story.append(Paragraph(nz, st["cap_zh"]))
            elif ne:
                story.append(Paragraph(ne, st["cap_en"]))
            story.append(Spacer(1, 6))
        # fn_index / 其他：跳过

    # 文档：竖版 + 横版两个模板，宽表自动落到横版
    doc = SimpleDocTemplate(str(out_pdf), pagesize=A4,
                            leftMargin=LM, rightMargin=RM, topMargin=TM, bottomMargin=BM,
                            title=norm(meta.get("title_zh", "")),
                            author=meta.get("author_year", ""))

    frame_p = Frame(LM, BM, WIDTH - LM - RM, HEIGHT - TM - BM, id="fp")
    frame_l = Frame(LM, BM, HEIGHT - LM - RM, WIDTH - TM - BM, id="fl")
    doc.addPageTemplates([
        PageTemplate(id="portrait", frames=[frame_p], pagesize=A4),
        PageTemplate(id="landscape", frames=[frame_l], pagesize=landscape(A4)),
    ])

    if label:
        def footer(canvas, doc_):
            canvas.saveState()
            canvas.setFont(CJK, 7.5)
            canvas.setFillColor(HexColor("#999"))
            pw = doc_.pagesize[0]
            canvas.drawCentredString(pw / 2, 8 * mm,
                                     f"{norm(meta.get('title_zh',''))} | {label} | 第 {doc_.page} 页")
            canvas.restoreState()
        doc.build(story, onFirstPage=footer, onLaterPages=footer)
    else:
        doc.build(story)
    print(f"OK -> {out_pdf}  blocks={len(blocks)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("json_path"); ap.add_argument("out_pdf"); ap.add_argument("label", nargs="?", default="")
    a = ap.parse_args()
    build(a.json_path, a.out_pdf, a.label)
