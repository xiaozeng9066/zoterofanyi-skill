#!/usr/bin/env python3
"""阶段1b：识别并裁切"非散文"区域（公式 / 插图 / 表格），产出区域清单 JSON。

设计原则（保真优先，不做无根据重建）：
  - 公式：本类 PDF 的公式字符是 U+1D400 数学字母 + 被错误映射的装饰字符
    （上划线常被映射成 Malayalam/Ethiopic 等乱码），抽取出来结构已毁。
    → 一律**裁图保真**，只翻译周边正文与变量说明，绝不重排、绝不让模型"猜着补"。
  - 插图：优先直接取嵌入的原始图像字节（质量最高），失败才回退为页面渲染裁切。
  - 表格：靠绘图线密度识别，同样裁图保真。

判定"数学行"的依据：
  1) 数学字母区 U+1D400–U+1D7FF、希腊字母、数学运算符占比高；或
  2) 出现本不该出现在英文经济学论文中的文字系统字符（Malayalam/Ethiopic/Sinhala 等，
     这是 PDF 把数学装饰符错误映射的典型症状）。
"""
import argparse
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path

import pymupdf

MATH_RANGES = (
    (0x1D400, 0x1D7FF),  # Mathematical Alphanumeric Symbols
    (0x0370, 0x03FF),    # Greek
    (0x2200, 0x22FF),    # Mathematical Operators
    (0x2A00, 0x2AFF),    # Supplemental Mathematical Operators
    (0x27C0, 0x27EF),    # Misc Mathematical Symbols-A
    (0x2980, 0x29FF),    # Misc Mathematical Symbols-B
    (0x2061, 0x2064),    # invisible operators
)
# PDF 错误映射的"装饰符"常落在这些文字系统里；英文论文正常不会出现
SUSPECT_SCRIPTS = {"MALAYALAM", "ETHIOPIC", "SINHALA", "KANNADA", "TAMIL", "TELUGU",
                   "DEVANAGARI", "BENGALI", "GUJARATI", "ORIYA", "GURMUKHI", "KHMER",
                   "MYANMAR", "LAO", "TIBETAN", "THAI"}


def is_math_char(ch):
    cp = ord(ch)
    return any(a <= cp <= b for a, b in MATH_RANGES)


def script_of(ch):
    try:
        return unicodedata.name(ch).split()[0]
    except ValueError:
        return ""


def line_profile(text):
    """返回 (数学字符占比, 可疑文字系统字符数)"""
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return 0.0, 0
    math = sum(1 for c in chars if is_math_char(c))
    suspect = sum(1 for c in chars if script_of(c) in SUSPECT_SCRIPTS)
    return math / len(chars), suspect


GREEK = {
    "ALPHA": "α", "BETA": "β", "GAMMA": "γ", "DELTA": "δ", "EPSILON": "ε",
    "ZETA": "ζ", "ETA": "η", "THETA": "θ", "IOTA": "ι", "KAPPA": "κ",
    "LAMDA": "λ", "LAMBDA": "λ", "MU": "μ", "NU": "ν", "XI": "ξ",
    "OMICRON": "ο", "PI": "π", "RHO": "ρ", "SIGMA": "σ", "TAU": "τ",
    "UPSILON": "υ", "PHI": "φ", "CHI": "χ", "PSI": "ψ", "OMEGA": "ω",
    "NABLA": "∇", "PARTIAL DIFFERENTIAL": "∂", "THETA SYMBOL": "ϑ",
    "PHI SYMBOL": "ϕ", "PI SYMBOL": "ϖ", "RHO SYMBOL": "ϱ",
    "EPSILON SYMBOL": "ϵ", "KAPPA SYMBOL": "ϰ", "FINAL SIGMA": "ς",
}
# ASCII 数学字母区起点（按样式）
ASCII_BLOCKS = {
    "MATHEMATICAL BOLD": 0x1D400, "MATHEMATICAL ITALIC": 0x1D434,
    "MATHEMATICAL BOLD ITALIC": 0x1D468, "MATHEMATICAL SCRIPT": 0x1D49C,
    "MATHEMATICAL BOLD SCRIPT": 0x1D4D0, "MATHEMATICAL FRAKTUR": 0x1D504,
    "MATHEMATICAL DOUBLE-STRUCK": 0x1D538, "MATHEMATICAL BOLD FRAKTUR": 0x1D56C,
    "MATHEMATICAL SANS-SERIF": 0x1D5A0, "MATHEMATICAL SANS-SERIF BOLD": 0x1D5D4,
    "MATHEMATICAL SANS-SERIF ITALIC": 0x1D608, "MATHEMATICAL SANS-SERIF BOLD ITALIC": 0x1D63C,
    "MATHEMATICAL MONOSPACE": 0x1D670,
}


def normalize_math(text):
    """把数学字母（U+1D400 区）折叠成普通 ASCII/希腊字母，供模型阅读。
    这是同一字符的另一种编码，属转写而非改动内容。

    注意：不能用算术偏移一概而论 —— 该区里既有拉丁字母也有希腊字母，
    粗暴映射会把 β 变成 U+0310 这类组合符（静默数据损坏）。
    """
    out = []
    for ch in text:
        cp = ord(ch)
        if not (0x1D400 <= cp <= 0x1D7FF):
            out.append(ch)
            continue
        try:
            name = unicodedata.name(ch)
        except ValueError:
            out.append(ch)
            continue

        letter = name.rsplit(" ", 1)[-1]
        # 希腊字母
        if letter in GREEK:
            g = GREEK[letter]
            out.append(g.upper() if "CAPITAL" in name and len(g) == 1 else g)
            continue
        # 数字
        if letter in {"ZERO", "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE"}:
            out.append(str(["ZERO", "ONE", "TWO", "THREE", "FOUR", "FIVE",
                            "SIX", "SEVEN", "EIGHT", "NINE"].index(letter)))
            continue
        # 拉丁字母：按样式取块起点 + 偏移
        for prefix, start in sorted(ASCII_BLOCKS.items(), key=lambda kv: -len(kv[0])):
            if name.startswith(prefix):
                if name.endswith(" CAPITAL " + letter) or " CAPITAL " in name:
                    out.append(chr(ord("A") + (cp - start) % 26))
                else:
                    out.append(chr(ord("a") + (cp - start - 26) % 26))
                break
        else:
            out.append(ch)
    return "".join(out)


def find_math_regions(page, col=(90.0, 508.0), pad=4.0):
    """把"独占一行的"公式聚成区域，返回 [(bbox, lines)]。

    两条关键判据：
      1) 垂直边界用 span["origin"] 基线，不用被坏字体度量撑大的 line["bbox"]；
      2) 行间公式独占一行 —— 若同一基线上存在散文文字，说明是**内联公式**
         （如 "where BMI_ijc designates ..." 行尾的公式），不裁图，交给正文翻译。
    """
    lines_meta, prose_baselines = [], []
    for blk in page.get_text("dict")["blocks"]:
        if blk.get("type") != 0:
            continue
        for line in blk.get("lines", []):
            text = "".join(s["text"] for s in line["spans"])
            if not text.strip():
                continue
            spans = [s for s in line["spans"] if s["text"].strip()]
            if not spans:
                continue
            size = max(s["size"] for s in spans)
            baseline = sum(s["origin"][1] for s in spans) / len(spans)
            x0 = min(s["bbox"][0] for s in spans)
            x1 = max(s["bbox"][2] for s in spans)
            ratio, suspect = line_profile(text)
            ascii_words = len(re.findall(r"[A-Za-z]{2,}", text))
            is_math = ratio >= 0.18 or (suspect >= 2 and ascii_words <= 6)
            meta = {"baseline": baseline, "size": size, "x0": x0, "x1": x1,
                    "text": text.strip(), "is_math": is_math}
            lines_meta.append(meta)
            if not is_math and ascii_words >= 3:
                prose_baselines.append(meta)

    inline = []
    hits = []
    for m in lines_meta:
        if not m["is_math"]:
            continue
        same_line_prose = any(
            abs(p["baseline"] - m["baseline"]) <= max(3.0, 0.35 * m["size"])
            and not (p["x1"] < m["x0"] - 40 or p["x0"] > m["x1"] + 40)
            for p in prose_baselines
        )
        if same_line_prose:
            inline.append(m["text"])
        else:
            hits.append(m)

    hits.sort(key=lambda h: h["baseline"])
    groups, cur = [], None
    for h in hits:
        if cur and h["baseline"] - cur["baseline_max"] < 2.2 * max(cur["size"], h["size"]):
            cur["items"].append(h)
            cur["baseline_max"] = max(cur["baseline_max"], h["baseline"])
            cur["size"] = max(cur["size"], h["size"])
        else:
            if cur:
                groups.append(cur)
            cur = {"items": [h], "baseline_min": h["baseline"],
                   "baseline_max": h["baseline"], "size": h["size"]}
    if cur:
        groups.append(cur)

    out = []
    for g in groups:
        size = g["size"]
        y0 = g["baseline_min"] - 1.6 * size
        y1 = g["baseline_max"] + 0.8 * size
        x0 = min(h["x0"] for h in g["items"]) - pad
        x1 = max(h["x1"] for h in g["items"]) + pad
        x0 = min(x0, 90 - pad)
        x1 = max(x1, 508 + pad)
        rect = pymupdf.Rect(max(x0, 0), max(y0, 0),
                            min(x1, page.rect.width), min(y1, page.rect.height))
        out.append((rect, [h["text"] for h in g["items"]]))
    return out, inline


def find_table_regions(page, min_lines=18):
    """绘图线密集的区域 = 表格（本类论文用矢量线画表格）。

    注意：表格横线的高度约 0.5pt、竖线宽度约 0.5pt，
    因此不能只按"宽度 > 20"筛，必须宽或高任一够长即可。
    """
    rects = []
    for d in page.get_drawings():
        r = d.get("rect")
        if r and (r.width > 20 or r.height > 20):
            rects.append(r)
    if len(rects) < min_lines:
        return []
    rect = pymupdf.Rect(rects[0])
    for r in rects[1:]:
        rect |= r
    return [pymupdf.Rect(rect.x0 - 4, rect.y0 - 4, rect.x1 + 4, rect.y1 + 4)]


def find_image_regions(page, doc, min_px=80):
    """插图：优先取嵌入原图字节"""
    out = []
    for info in page.get_images(full=True):
        xref, smask, w, h = info[0], info[1], info[2], info[3]
        if w < min_px or h < min_px:
            continue
        rects = page.get_image_rects(xref)
        if not rects:
            continue
        out.append({"kind": "image", "xref": xref, "px": [w, h], "rect": pymupdf.Rect(rects[0])})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--pages", required=True, help="如 16,17,34-36")
    ap.add_argument("--out", required=True)
    ap.add_argument("--assets", required=True)
    ap.add_argument("--dpi", type=int, default=300)
    args = ap.parse_args()

    wanted = set()
    for part in args.pages.split(","):
        if "-" in part:
            a, b = part.split("-")
            wanted.update(range(int(a), int(b) + 1))
        else:
            wanted.add(int(part))

    doc = pymupdf.open(args.pdf)
    assets = Path(args.assets)
    assets.mkdir(parents=True, exist_ok=True)
    manifest = {"source_pdf": str(args.pdf), "pages": []}

    for pno in sorted(wanted):
        page = doc[pno - 1]
        entry = {"page": pno, "regions": []}

        math_regions, inline_math = find_math_regions(page)
        entry["inline_math"] = [normalize_math(t) for t in inline_math]

        for rect, lines in math_regions:
            name = f"p{pno}_math_{len(entry['regions']) + 1}"
            pix = page.get_pixmap(clip=rect, dpi=args.dpi)
            path = assets / f"{name}.png"
            pix.save(path)
            entry["regions"].append({
                "kind": "equation", "file": path.name, "rect": [round(v, 1) for v in rect],
                "dpi": args.dpi, "raw_lines": [normalize_math(l) for l in lines],
                "note": "抽取字符已损坏（上划线/分数被错误映射），仅供定位，不得据此重排公式",
            })

        for img in find_image_regions(page, doc):
            name = f"p{pno}_fig_x{img['xref']}"
            path = assets / f"{name}.png"
            try:
                raw = doc.extract_image(img["xref"])
                path.write_bytes(raw["image"])
                src = "embedded"
            except Exception:
                page.get_pixmap(clip=img["rect"], dpi=args.dpi).save(path)
                src = "rendered"
            entry["regions"].append({
                "kind": "figure", "file": path.name, "source": src,
                "px": img["px"], "rect": [round(v, 1) for v in img["rect"]],
            })

        for rect in find_table_regions(page):
            name = f"p{pno}_table_{len(entry['regions']) + 1}"
            pix = page.get_pixmap(clip=rect, dpi=args.dpi)
            path = assets / f"{name}.png"
            pix.save(path)
            entry["regions"].append({
                "kind": "table", "file": path.name,
                "rect": [round(v, 1) for v in rect], "dpi": args.dpi,
            })

        manifest["pages"].append(entry)
        kinds = Counter(r["kind"] for r in entry["regions"])
        print(f"p{pno}: {dict(kinds)}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
