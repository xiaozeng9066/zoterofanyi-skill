#!/usr/bin/env python3
"""阶段1：把 PDF 抽成"页 -> 段落块"的 JSON，供分块翻译与渲染使用。

设计要点（为省 token 与保真服务）：
  - 段落按 y 间距合并，避免把一行行断句丢给模型；
  - 记录 x/y/字号/字体，便于识别表头、脚注、公式行；
  - 表格/图片区域单独裁图落盘（PyMuPDF 无法还原表格结构，先用图像保真兜底）。
"""
import argparse
import json
import re
from pathlib import Path

import pymupdf

from regions import normalize_math

GAP_RATIO = 1.35  # 行间距超过"常规行距"该倍数视为新段落
GAP_MIN = 3.0


def _median(vals):
    vals = sorted(vals)
    if not vals:
        return 0.0
    n = len(vals)
    return vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2


def page_paragraphs(page, y_min=None, y_max=None, skip_rects=()):
    spans = []
    d = page.get_text("dict")
    for blk in d["blocks"]:
        if blk.get("type") != 0:
            continue
        for line in blk.get("lines", []):
            txt = "".join(s["text"] for s in line["spans"])
            if not txt.strip():
                continue
            y0 = line["bbox"][1]
            if y_min is not None and y0 < y_min:
                continue
            if y_max is not None and y0 > y_max:
                continue
            sizes = [round(s["size"], 1) for s in line["spans"]]
            # 基线（origin.y）比 bbox 可靠：含 CambriaMath 的行 bbox 会被撑到 67pt 高
            base = sum(s["origin"][1] for s in line["spans"]) / len(line["spans"])
            bold = any(s["flags"] & 16 for s in line["spans"])
            if any(r.y0 - 1 <= base <= r.y1 + 1
                   and line["bbox"][2] > r.x0 and line["bbox"][0] < r.x1
                   for r in skip_rects):
                continue  # 落在公式/表格/插图区域内
            spans.append({
                "x": round(line["bbox"][0], 1),
                "y": round(y0, 1),
                "base": round(base, 1),
                "size": max(sizes) if sizes else 0,
                "bold": bold,
                "text": txt,
            })

    spans.sort(key=lambda s: (s["y"], s["x"]))

    # 段落判定不能全页统一：同一页里正文 12pt/行距 20.7，脚注 9pt/行距 11，
    # 表格 10.6pt/行距 12。因此把"同一栏 + 同一字号"视为一条独立文本流，
    # 每条流用它自己的中位行距判断段落边界。
    # x 聚类：悬挂缩进的首行与续行相差约 14pt，固定桶宽会把它们切成两条流。
    # 先把 x 聚成"栏"（阈值 22pt），再与字号组合成流。
    xs = sorted({s["x"] for s in spans})
    col_of, cur_col = {}, None
    for x in xs:
        if cur_col is None or x - cur_col[-1] > 22:
            cur_col = [x]
            col_id = len(col_of.setdefault("_cols", []))
            col_of["_cols"].append(cur_col)
        else:
            cur_col.append(x)
            col_id = len(col_of["_cols"]) - 1
        col_of[x] = col_id

    streams = {}
    for s in spans:
        streams.setdefault((col_of[s["x"]], round(s["size"], 1)), []).append(s)

    stream_gaps = []
    for items in streams.values():
        ys = sorted(i["base"] for i in items)
        g = [b - a for a, b in zip(ys, ys[1:]) if b - a > 0.5]
        if g:
            stream_gaps.append(_median(g))
    fallback = _median(stream_gaps) or 12.0

    paras = []
    for items in streams.values():
        items.sort(key=lambda s: (s["base"], s["x"]))
        ys = [i["base"] for i in items]
        g = [b - a for a, b in zip(ys, ys[1:]) if b - a > 0.5]
        para_gap = max((_median(g) if g else fallback) * GAP_RATIO, GAP_MIN + fallback * 0.5)
        cur = None
        for s in items:
            if cur is None or s["base"] - cur["base_last"] > para_gap:
                if cur:
                    paras.append(cur)
                cur = {"x": s["x"], "y": s["y"], "size": s["size"],
                       "base": s["base"], "base_last": s["base"], "lines": [s["text"]],
                       "bold": bool(s.get("bold")), "n_lines": 1}
            else:
                cur["lines"].append(s["text"])
            cur["base_last"] = s["base"]
            cur["size"] = max(cur["size"], s["size"])
            cur["n_lines"] += 1
            cur["bold"] = cur.get("bold", False) and bool(s.get("bold"))
        if cur:
            paras.append(cur)

    paras.sort(key=lambda p: (p["y"], p["x"]))

    out = []
    for p in paras:
        raw = "".join(p["lines"])
        text = re.sub(r"-\s*\n", "", raw)
        text = re.sub(r"\s+", " ", text).strip()
        # 数学字母（U+1D400 区）折叠为普通字母，避免把乱码字符送去翻译
        text = re.sub(r"\s+", " ", normalize_math(text)).strip()
        if text:
            size = p["size"]
        out.append({
            "x": p["x"], "y": p["y"], "size": size, "text": text,
            "base": p["base"], "base_last": p["base_last"],
            "bold": bool(p.get("bold")), "n_lines": p.get("n_lines", 1),
            # 由基线推导的可靠边界（bbox 在本类 PDF 上不可信）
            "rect_top": round(p["base"] - 0.8 * size, 1),
            "rect_bottom": round(p["base_last"] + 0.35 * size, 1),
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--out", required=True, help="输出 JSON 路径")
    ap.add_argument("--pages", default="", help="如 1-10,19；默认全部")
    ap.add_argument("--crop", nargs="*", default=[], help="裁图规格 page:x0,y0,x1,y1:名称")
    ap.add_argument("--regions", default="", help="regions.py 产出的区域清单；其中的行不再进入正文流")
    ap.add_argument("--assets", default="assets")
    args = ap.parse_args()

    doc = pymupdf.open(args.pdf)

    # 区域门控：公式/表格/插图区域内的文本行一律不进正文流。
    # 依据基线判断（bbox 在含数学字体的页上不可信）。
    region_rects = {}
    if args.regions:
        reg = json.loads(Path(args.regions).read_text(encoding="utf-8"))
        for pg in reg["pages"]:
            region_rects[pg["page"]] = [
                pymupdf.Rect(r["rect"]) for r in pg.get("regions", [])
            ]

    wanted = None
    if args.pages:
        wanted = set()
        for part in args.pages.split(","):
            if "-" in part:
                a, b = part.split("-")
                wanted.update(range(int(a), int(b) + 1))
            else:
                wanted.add(int(part))

    result = {"source_pdf": str(args.pdf), "pages": []}
    for i, page in enumerate(doc, start=1):
        if wanted is not None and i not in wanted:
            continue
        result["pages"].append({
            "page": i,
            "width": round(page.rect.width, 1),
            "height": round(page.rect.height, 1),
            "images": len(page.get_images(full=True)),
            "drawings": len(page.get_drawings()),
            "paragraphs": page_paragraphs(page, skip_rects=region_rects.get(i, [])),
        })

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out}  pages={len(result['pages'])}  paras={sum(len(p['paragraphs']) for p in result['pages'])}")

    assets = Path(args.assets)
    assets.mkdir(parents=True, exist_ok=True)
    for spec in args.crop:
        page_no, box, name = spec.split(":", 2)
        x0, y0, x1, y1 = (float(v) for v in box.split(","))
        page = doc[int(page_no) - 1]
        pix = page.get_pixmap(clip=pymupdf.Rect(x0, y0, x1, y1), dpi=200)
        target = assets / f"{name}.png"
        pix.save(target)
        print(f"crop p{page_no} -> {target} ({pix.width}x{pix.height})")


if __name__ == "__main__":
    main()
