#!/usr/bin/env python3
"""阶段2a：把「散文块」与「非散文区域」装配成按阅读顺序排列的文档骨架。

输出 doc_skeleton.json：每个条目都有稳定的 id 与原文锚点，
散文条目只有 en（待翻译填 zh）；公式/插图/表格条目带图片与英文标题，
术语与译注由翻译阶段填写。

处理的关键细节：
  - 落进公式/插图/表格区域内的散文段落要被**抑制**（否则公式会被重复呈现，
    且那些字符是损坏的，翻译出来是错的）；
  - 图注/表注/来源说明这类段落要从散文流里摘出来，挂到对应区域条目上。
"""
import argparse
import json
import re
from pathlib import Path

import pymupdf

CAPTION_RE = re.compile(r"^(Figure|Table)\s+[A-Z]?\d+\b", re.I)
NOTE_RE = re.compile(r"^(Source|Note|Notes)\s*[:：]", re.I)


def para_rect(para):
    """段落几何盒：上下边界由基线推导（rect_top/rect_bottom），
    不使用 bbox —— 含数学字体的行 bbox 会被坏字体度量撑高近 4 倍。"""
    return pymupdf.Rect(para["x"], para["rect_top"], para["x"] + 460, para["rect_bottom"])


PAGE_NUM_RE = re.compile(r"^\d{1,3}$")


def overlaps(a, b, ratio=0.5):
    inter = a & b
    if inter.is_empty:
        return False
    return (inter.width * inter.height) / max(a.width * a.height, 1e-6) >= ratio


TERM_RE = re.compile(r"[.!?:;。！？：；\"”’)]\s*$")


def merge_cross_page(items):
    """合并被分页切断的段落。

    判据：前一段不以终止标点结尾，且后一段以小写字母/汉字起首，且两段位于相邻页。
    不合并的话，英文会停在 "…which is"，中文停在半句上（实测确实如此）。

    规则 81c：允许中间隔着独立公式/图/表。跨页断句常被页首的公式图片打断
    （实测本篇 p3-7 尾 "…quanti" → 公式图 p3_equation_5 → p4-0 首 "andcov(…"），
    旧逻辑要求 out[-1] 就是上一段，于是这类断句永远合不上（Gate 1 H-续句 BLOCK）。
    中间最多跳过 3 个非正文块；上一段若以 ":;" 等结尾仍视为完整，不合并（"It follows that:" 那类）。
    """
    out = []
    for it in items:
        if it["kind"] == "para" and it.get("pages"):
            k, skipped = len(out) - 1, 0
            while k >= 0 and out[k]["kind"] != "para" and out[k]["kind"] in ("equation", "figure", "table"):
                skipped += 1
                if skipped > 3:
                    break
                k -= 1
            if k >= 0 and skipped <= 3 and out[k]["kind"] == "para":
                prev = out[k]
                if prev.get("pages") and it["pages"][0] - prev["pages"][-1] <= 1:
                    prev_en = (prev.get("en") or "").rstrip()
                    cur_en = (it.get("en") or "").lstrip()
                    if prev_en and cur_en and not TERM_RE.search(prev_en) and (
                            cur_en[0].islower() or "\u4e00" <= cur_en[0] <= "\u9fff"):
                        prev["en"] = prev_en + " " + cur_en
                        prev["pages"] = sorted(set(prev["pages"] + it["pages"]))
                        prev["continued"] = True
                        if it.get("continued_from"):
                            prev["continued_from"] = it["continued_from"]
                        continue
        out.append(it)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("blocks")
    ap.add_argument("regions")
    ap.add_argument("--out", required=True)
    ap.add_argument("--page-map", default="", help="切片页码->原文页码，如 1=7,2=8,3=9")
    args = ap.parse_args()

    blocks = json.loads(Path(args.blocks).read_text(encoding="utf-8"))
    pmap = {}
    for pair in (args.page_map or "").split(","):
        if "=" in pair:
            k, v = pair.split("=")
            pmap[int(k)] = int(v)
    regions = json.loads(Path(args.regions).read_text(encoding="utf-8"))
    regions_by_page = {p["page"]: p for p in regions["pages"]}

    items, table_leaks = [], []
    for page in blocks["pages"]:
        pno = page["page"]
        reg = regions_by_page.get(pno, {})
        regs = reg.get("regions", [])
        consumed = set()

        paragraphs = []
        for i, para in enumerate(page["paragraphs"]):
            rect = para_rect(para)
            hit = next((r for r in regs if overlaps(rect, pymupdf.Rect(r["rect"]))), None)
            if hit:
                consumed.add(i)
                paragraphs.append({"idx": i, "kind": "suppressed", "by": hit["file"], "rect": rect})
            else:
                paragraphs.append({"idx": i, "kind": "prose", "para": para, "rect": rect})

        for r in regs:
            rect = pymupdf.Rect(r["rect"])
            item = {
                "id": f"p{pno}-{r['kind']}-{Path(r['file']).stem}",
                "kind": {"equation": "equation", "figure": "figure", "table": "table"}[r["kind"]],
                "pages": [pno],
                "image": f"assets/{r['file']}",
                "y": rect.y0,
                # MinerU 结构里已给出表注/图注与脚注，优先采用
                "caption_en": r.get("caption_en", ""), "note_en": r.get("note_en", ""),
            }
            if r["kind"] == "equation":
                item["raw_lines"] = r.get("raw_lines", [])
                item["caption_zh"] = ""   # 公式不设译名，靠周边正文说明

            # 图注/表注：区域下方最近的、以 Figure/Table 开头的段落
            for p in sorted(paragraphs, key=lambda x: x["rect"].y0 if x["kind"] != "suppressed" else -1):
                if p["kind"] != "prose" or p["idx"] in consumed:
                    continue
                gap_below = p["rect"].y0 - rect.y1      # 图注在区域下方
                gap_above = rect.y0 - p["rect"].y1      # 表注在区域上方
                if ((0 <= gap_below <= 70) or (0 <= gap_above <= 40)) \
                        and CAPTION_RE.match(p["para"]["text"]):
                    item["caption_en"] = p["para"]["text"]
                    consumed.add(p["idx"])
                    break
            # 来源/注释：图注上下的 Source:/Note: 段落（论文里位置不固定）
            cap_rect = None
            for p in paragraphs:
                if p["kind"] == "prose" and p["para"]["text"] == item["caption_en"] and item["caption_en"]:
                    cap_rect = p["rect"]
                    break
            for p in paragraphs:
                if (p["kind"] == "prose" and p["idx"] not in consumed
                        and NOTE_RE.match(p["para"]["text"])
                        and (abs(p["rect"].y0 - (cap_rect.y0 if cap_rect else rect.y0)) < 80
                             or abs(p["rect"].y1 - rect.y0) < 60)):
                    item["note_en"] = p["para"]["text"]
                    consumed.add(p["idx"])
                    break
            # 排序跟着 MinerU 原始块序号走（规则 44），不再按 y。
            # 旧 blocks/regions 没有 seq 时退回 10000+y，等价于原来的行为，保证向后兼容。
            rseq = r.get("seq")
            items.append({**item,
                          "sort": (pno, rseq if rseq is not None else 10000 + rect.y0)})

        for p in paragraphs:
            if p["kind"] != "prose" or p["idx"] in consumed:
                continue
            para = p["para"]
            if PAGE_NUM_RE.match(para["text"]) and para["size"] <= 10:
                continue  # 页码不是正文
            txt = para["text"]
            # 小标题识别：MinerU 有时把 "5.Results" / "Descriptive statistics" 归为 text
            is_num_head = bool(re.match(r"^\d+(\.\d+)*\.?\s*[A-Z]", txt)) and len(txt) < 80
            is_short_head = (len(txt) <= 45 and txt[:1].isupper()
                             and not txt.rstrip().endswith((".", ":", ";", ",")))
            # 文档大标题（规则 81c）：旧逻辑只认「字号≥14 且 <90 字」，长标题（本例 106 字）
            # 会被判成正文，渲染后整篇没有大标题（preflight J-标题 WARN）。MinerU 已标
            # mineru_type=title，且首页首个正文块，据此认定为 title。
            is_doc_title = (para.get("mineru_type") == "title" and pno == 1
                            and not any(x["kind"] == "title" for x in items))
            if is_doc_title:
                kind = "title"
            else:
                kind = "heading" if (para["size"] >= 14 and len(txt) < 90) or is_num_head or is_short_head else "para"
            item = {
                "id": f"p{pno}-{p['idx']}",
                "kind": kind, "pages": [pno],
                "en": txt, "zh": "",
                "size": para["size"],
                "sort": (pno, para["seq"] if para.get("seq") is not None
                         else 10000 + para["y"]),
            }
            # 极短且落在表格/图片纵向范围内 → 多半是跨页表格边界漏出的单元格文字。
            # 标为 table_leak 并从正文流剔除（否则会冒出一个孤立的 "school" 段），
            # 但记录在 doc meta 的 table_leaks 里，可追溯、不静默。
            if kind == "para" and len(txt) < 20:
                cy = (p["rect"].y0 + p["rect"].y1) / 2
                near = [r for r in regs
                        if r["kind"] in ("table", "figure")
                        and r["rect"][1] - 40 <= cy <= r["rect"][3] + 40
                        and p["rect"].x1 > r["rect"][0]
                        and p["rect"].x0 < r["rect"][2]]
                if near:
                    table_leaks.append({"page": pno, "text": txt,
                                        "nearest": near[0]["file"]})
                    continue
                item["flag"] = "疑似边界残余文字，需人工核对"
            items.append(item)

    merged_before = len(items)
    items = merge_cross_page(items)
    if len(items) != merged_before:
        print(f"  跨页段落合并: {merged_before} -> {len(items)} 块")

    if pmap:   # 切片页码 → 原文页码（否则页码锚点会显示成 p.1、p.2…）
        for it in items:
            it["pages"] = [pmap.get(p, p) for p in it.get("pages", [])]

    items.sort(key=lambda it: it["sort"])
    for it in items:
        it.pop("sort", None)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "meta": {
            "source_pdf": blocks.get("source_pdf", ""),
            "pages_in_sample": sorted({p["page"] for p in blocks["pages"]}),
            "glossary_version": "v0",
            "layout": "段落交错 + 公式/插图/表格原图保真",
        },
        "table_leaks": table_leaks,
        "glossary": [],
        "blocks": items,
    }
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

    kinds = {}
    for it in items:
        kinds[it["kind"]] = kinds.get(it["kind"], 0) + 1
    print(f"wrote {out}  items={len(items)}  {kinds}")
    for it in items:
        label = it.get("en") or it.get("caption_en") or it.get("image", "")
        print(f"  {it['kind']:<9} {it['id']:<28} {label[:64]}")


if __name__ == "__main__":
    main()
