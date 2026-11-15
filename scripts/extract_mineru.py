#!/usr/bin/env python3
"""阶段1（混合版）：MinerU 出结构 + 原生文本层出文字。

为什么必须混合（2026-10-17 实测结论）：
  - MinerU 的阅读顺序、表格与公式识别都可靠，但它在双栏页上跑 OCR，
    把字改坏（Objective→Ojecive、Discussion→Dicuss、model→modl），
    这类错字会被翻译模型"顺着编"下去；
  - 原生文本层的字是逐字准确的。
所以：**块类型 / bbox / 阅读顺序** 取自 MinerU 的 middle.json，
     **文字**用 PyMuPDF 按 bbox 从原生文本层切出来，
     **公式 / 插图 / 表格**仍从原始 PDF 裁图保真。

输出与 extract.py 完全同构（blocks + regions），因此 assemble.py 无需改动。
"""
import argparse
import json
import re
from pathlib import Path

import pymupdf

from regions import normalize_math

TEXT_TYPES = {"text", "title", "list", "index", "footnote", "header", "footer", "caption"}
REGION_TYPES = {"table": "table", "image": "figure", "figure": "figure",
                "interline_equation": "equation"}
CAP_RE = re.compile(r"^(Table|Figure)\s+[A-Z]?\d+[:.\s]", re.I)


def find_caption(page, rect):
    """MinerU 常把「表注/图注」并进区域 bbox（实测 Table 1 的 bbox 含标题行）。
    这里用原生文本在该区域内定位标题行，并从裁图范围中切出去，
    标题作为英文 caption 记录，交给翻译层出中文译名（避免图里图外重复英文）。"""
    try:
        d = page.get_text("dict", clip=rect)
    except Exception:
        return "", rect
    for blk in d.get("blocks", []):
        for ln in blk.get("lines", []):
            text = "".join(sp["text"] for sp in ln["spans"]).strip()
            if not text or not CAP_RE.match(text):
                continue
            lb = pymupdf.Rect(ln["bbox"])
            height = rect.height or 1
            if lb.y1 < rect.y0 + 0.30 * height:          # 标题在上方
                return clean(text), pymupdf.Rect(rect.x0, lb.y1 + 2, rect.x1, rect.y1)
            if lb.y0 > rect.y0 + 0.70 * height:          # 标题在下方
                return clean(text), pymupdf.Rect(rect.x0, rect.y0, rect.x1, lb.y0 - 2)
            return clean(text), rect
    return "", rect


def detect_rotated_table(page, rect):
    """检测表格是否「横放」（原 PDF 把宽表旋转 90° 排在竖版页上）。

    这类表在原生文本层里，字符的 dir 是竖向 (0,±1) 而非横向 (±1,0)。
    返回旋转角度（度）：-90 表示需逆时针转 90° 回正，0 表示无需旋转。
    """
    try:
        d = page.get_text("rawdict", clip=rect)
    except Exception:
        return 0
    horiz = vert = 0
    for blk in d.get("blocks", []):
        for ln in blk.get("lines", []):
            dx, dy = ln.get("dir", (1, 0))
            if abs(dx) >= abs(dy):      # 横向文字
                horiz += 1
            else:                        # 竖向文字（横放表）
                vert += 1
    total = horiz + vert
    if total == 0 or vert <= horiz:      # 竖向不占多数 → 正常横表，不旋转
        return 0
    # 竖向文字占多数 → 横放表。dir=(0,1) 表示自上而下，需逆时针 90° 回正；
    # dir=(0,-1) 表示自下而上，需顺时针 90°。用首条竖向行定方向。
    for blk in d.get("blocks", []):
        for ln in blk.get("lines", []):
            dx, dy = ln.get("dir", (0, 1))
            if abs(dy) > abs(dx):
                return -90 if dy > 0 else 90
    return -90


SOFT = "\u00ad\u00ac"   # 软连字符等：本类 PDF 的文本层会用它们表示断词

def clean(text):
    t = text.translate({ord(c): None for c in SOFT})
    t = re.sub(r"-\s*\n\s*", "-", t)   # 保留连字符：删掉会破坏复合词，并与上标脚注号粘连成错数
    t = re.sub(r"\s+", " ", t).strip()
    return normalize_math(t)


def native_in_rect(page, rect):
    """从原生文本层取该区域的文字，并给出字号统计。

    **不能直接 get_text("text")**：本类 PDF 的内容流里，行内公式的 span 常被排到
    该行文本对象之后，直接抽取会得到
        "where is a vector of individual i's characteristics ... and is a vector Xic Fic"
    这种句子结构被破坏的文本（公式符号掉到行尾）。这会把错误的句子喂给翻译模型。
    因此这里按「基线分组 + 组内按 x 排序」重建每一行，再按行拼段。
    """
    clip = pymupdf.Rect(rect)
    # 页脚带过滤（根因）：页码位于页面底部 ~0.96H，若段落 bbox 覆盖到该区域，
    # 页码会被当成正文数字混入句子（实测 "10- to 188 year-olds"、"for 22 adolescents"）。
    # 这里直接丢弃落在页脚带的 span；页脚脚注（约 0.88–0.94H）不受影响。
    footer_y = page.rect.height * 0.95
    spans, sizes = [], []
    try:
        for blk in page.get_text("dict", clip=clip)["blocks"]:
            for ln in blk.get("lines", []):
                for sp in ln["spans"]:
                    if not sp["text"].strip():
                        continue
                    if sp["origin"][1] > footer_y:
                        continue   # 页脚带：页码等
                    # 降部残片：上一行的 g/p/q/y 等降部在本类 PDF 内容流里是独立单字
                    # span，bbox 探入本段 clip 顶部但基线在 clip 之上。不过滤会得到
                    # "g g Despite this, ..."、"p f q y p y To evaluate ..."。
                    if sp["origin"][1] < clip.y0 - 0.5 and len(sp["text"].strip()) <= 3:
                        continue
                    spans.append({
                        "base": sp["origin"][1],
                        "x0": sp["bbox"][0],
                        "x1": sp["bbox"][2],
                        "size": sp["size"],
                        "text": sp["text"],
                    })
                    sizes.append(round(sp["size"], 1))
    except Exception:
        pass
    if not spans:
        return "", 10.0

    size = max(set(sizes), key=sizes.count) if sizes else 10.0

    # 行分组：放宽到 0.6×字号，好让上标/下标（基线偏移大）落回它所属的那一行。
    # 否则脚注编号会被拆成独立"行"并插到句子中间（实测 "BMI.2" → "on an 2 adolescent's BMI"）。
    spans.sort(key=lambda s: (s["base"], s["x0"]))
    lines, cur = [], None
    for s in spans:
        if cur is not None and abs(s["base"] - cur["base_ref"]) <= 0.60 * cur["max_size"]:
            cur["spans"].append(s)
            cur["max_size"] = max(cur["max_size"], s["size"])
        else:
            if cur:
                lines.append(cur)
            cur = {"base_ref": s["base"], "max_size": s["size"], "spans": [s]}
    if cur:
        lines.append(cur)

    # 碎片行合并：全是小字号的"行"（典型是上标脚注号自成一行的 "8"）
    # 必须并回相邻行，否则它会夹在断行之间，被拼成 "18"+"8" = "188"（实测如此）。
    if lines:
        sizes = sorted(l["max_size"] for l in lines)
        body = sizes[len(sizes) // 2]
        merged = []
        for ln in lines:
            if ln["max_size"] < 0.8 * body and merged:
                # 并入基线更近的邻居（前一行或后一行）
                prev, rest = merged[-1], None
                nxt = lines[lines.index(ln) + 1] if lines.index(ln) + 1 < len(lines) else None
                d_prev = abs(ln["base_ref"] - (sum(s["base"] for s in prev["spans"]) / len(prev["spans"])))
                d_next = abs(ln["base_ref"] - nxt["base_ref"]) if nxt else 1e9
                target = prev if d_prev <= d_next else nxt
                if target is prev:
                    prev["spans"].extend(ln["spans"])
                    prev["max_size"] = max(prev["max_size"], ln["max_size"])
                else:
                    target["spans"].extend(ln["spans"])
                    target["max_size"] = max(target["max_size"], ln["max_size"])
                continue
            merged.append(ln)
        lines = merged

    out = []
    for ln in lines:
        # 以"满字号"的 span 确定该行真正的基线与字号
        full = [s for s in ln["spans"] if s["size"] >= 0.9 * ln["max_size"]]
        line_size = max(set(round(s["size"], 1) for s in full), key=lambda v: sum(1 for s in full if round(s["size"], 1) == v)) if full else ln["max_size"]
        line_base = sum(s["base"] for s in full) / len(full) if full else ln["base_ref"]

        parts, prev_x1, prev_kind = [], None, "normal"
        for s in sorted(ln["spans"], key=lambda s: s["x0"]):
            dy = s["base"] - line_base
            small = s["size"] < 0.9 * line_size
            if small and dy < -0.15 * line_size:
                kind, text = "sup", "^{" + s["text"] + "}"
            elif small and dy > 0.15 * line_size:
                kind, text = "sub", "_{" + s["text"] + "}"
            else:
                kind, text = "normal", s["text"]
            if prev_x1 is not None:
                gap = s["x0"] - prev_x1
                # 上下标紧贴前文，不插空格
                if kind in ("sup", "sub"):
                    parts.append("")
                else:
                    parts.append(" " if gap > 0.28 * line_size else "")
            parts.append(text)
            prev_x1, prev_kind = s["x1"], kind
        out.append("".join(parts))

    return clean("\n".join(out)), size


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("middle_json")
    ap.add_argument("--out-blocks", required=True)
    ap.add_argument("--out-regions", required=True)
    ap.add_argument("--assets", required=True)
    ap.add_argument("--dpi", type=int, default=300)
    args = ap.parse_args()

    doc = pymupdf.open(args.pdf)
    mid = json.loads(Path(args.middle_json).read_text(encoding="utf-8"))
    assets = Path(args.assets)
    assets.mkdir(parents=True, exist_ok=True)

    blocks_out = {"source_pdf": str(args.pdf), "pages": []}
    regions_out = {"source_pdf": str(args.pdf), "pages": []}

    for pinfo in mid["pdf_info"]:
        pno = pinfo["page_idx"] + 1
        page = doc[pno - 1] if pno <= doc.page_count else None
        if page is None:
            continue
        paras, regions = [], []
        seen_rects = []

        # seq = MinerU 原始块序号：双栏页的正确读序是「左栏自上而下 → 右栏自上而下」，
        # 与 y 坐标无关。必须一路带到 assemble.py，否则按 (page, y) 排序会左右栏交错
        # （规则 44：Jia 2017 首见，Lie 2016 Springer 双栏再次复现）。
        for seq, blk in enumerate(pinfo.get("para_blocks") or []):
            btype = (blk.get("type") or "").lower()
            bbox = blk.get("bbox") or []
            if len(bbox) != 4:
                continue
            rect = pymupdf.Rect(bbox)

            if btype in REGION_TYPES:
                kind = REGION_TYPES[btype]
                # MinerU 把表/图拆成嵌套子块：table_caption / table_body / table_footnote。
                # 必须在子块层面取，否则会把表注当成表格的一部分裁进图片（实测如此）。
                subs = blk.get("blocks") or []
                body = [pymupdf.Rect(s["bbox"]) for s in subs
                        if s.get("type") in ("table_body", "image_body", "equation") and s.get("bbox")]
                crop_rect = pymupdf.Rect(body[0]) if body else rect
                for b2 in body[1:]:
                    crop_rect |= b2
                cap_en, note_en = "", ""
                for s2 in subs:
                    st = s2.get("type")
                    if st in ("table_caption", "image_caption"):
                        cap_en = cap_en or native_in_rect(page, pymupdf.Rect(s2["bbox"]))[0]
                    elif st in ("table_footnote", "image_footnote"):
                        note_en = note_en or native_in_rect(page, pymupdf.Rect(s2["bbox"]))[0]
                if not cap_en and kind in ("table", "figure"):
                    cap_en, crop_rect = find_caption(page, crop_rect)
                if crop_rect.height < 8 or crop_rect.width < 20:
                    crop_rect = rect
                # 丢弃封面 logo 之类的小图（如 IZA 徽标 54x74pt）：占版面 <2% 一律不收录
                if kind in ("figure", "table") and crop_rect.width * crop_rect.height < 0.02 * page.rect.width * page.rect.height:
                    continue
                # 公式编号 (1)(2) 在右边界外，MinerU 的 equation bbox 不含它；
                # 向右扩到正文栏右缘，否则「式（1）」在正文里被引用却看不到编号。
                if kind == "equation":
                    crop_rect.x1 = max(crop_rect.x1, 508.0)
                name = f"p{pno}_{kind}_{len(regions) + 1}"
                path = assets / f"{name}.png"
                page.get_pixmap(clip=crop_rect, dpi=args.dpi).save(path)
                # 横放表纠正：原 PDF 把宽表旋转 90° 排在竖版页上，裁出的图文字是竖的。
                # 检测文字方向，竖向则旋转回正，转正后宽高比 >1，渲染层会自动放横向页。
                if kind == "table":
                    rot = detect_rotated_table(page, crop_rect)
                    if rot:
                        try:
                            from PIL import Image as PILImage
                            im = PILImage.open(path)
                            im = im.rotate(rot, expand=True)
                            im.save(path)
                        except Exception as e:
                            print(f"    [WARN] 横放表 {name} 旋转失败: {e}", file=sys.stderr)
                entry = {"kind": kind, "file": path.name, "seq": seq,
                         "rect": [round(v, 1) for v in crop_rect], "dpi": args.dpi}
                if cap_en:
                    entry["caption_en"] = cap_en
                if note_en:
                    entry["note_en"] = note_en
                if kind == "equation":
                    # MinerU 的 LaTeX 备用（本轮不用于渲染，仅存档供核对）
                    lines = blk.get("lines") or []
                    latex = ""
                    for ln in lines:
                        for sp in ln.get("spans", []):
                            latex += sp.get("content", "")
                    entry["latex_mineru"] = latex.strip()
                    entry["note"] = "原图保真；latex_mineru 仅供核对，未经人工逐条确认不得用于排版"
                regions.append(entry)
                seen_rects.append(rect)
                continue

            if btype not in TEXT_TYPES:
                continue

            # 文字来自原生文本层，而不是 MinerU 的 OCR
            text, size = native_in_rect(page, rect)
            if not text:
                continue
            base = rect.y0 + size * 0.8
            paras.append({
                "x": round(rect.x0, 1),
                "y": round(rect.y0, 1),
                "size": size,
                "text": text,
                "base": round(base, 1),
                "base_last": round(base, 1),
                "rect_top": round(rect.y0, 1),
                "rect_bottom": round(rect.y1, 1),
                "bold": btype == "title",
                "seq": seq,
                "n_lines": len(blk.get("lines") or []),
                "mineru_type": btype,
            })

        blocks_out["pages"].append({
            "page": pno,
            "width": round(page.rect.width, 1),
            "height": round(page.rect.height, 1),
            "images": len(page.get_images(full=True)),
            "drawings": len(page.get_drawings()),
            "paragraphs": paras,
        })
        regions_out["pages"].append({"page": pno, "regions": regions,
                                     "inline_math": []})

    Path(args.out_blocks).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out_blocks).write_text(json.dumps(blocks_out, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
    Path(args.out_regions).write_text(json.dumps(regions_out, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    np_ = sum(len(p["paragraphs"]) for p in blocks_out["pages"])
    nr = sum(len(p["regions"]) for p in regions_out["pages"])
    print(f"wrote {args.out_blocks} (paras={np_}) / {args.out_regions} (regions={nr})")


if __name__ == "__main__":
    main()
