#!/usr/bin/env python3
"""从原始 PDF 直接抽取脚注（不依赖 MinerU）。

为什么单独抽：MinerU 把脚注放进 `discarded_blocks`，其中有 10 条根本没输出，
导致 v1–v6 的脚注内容**整段缺失**（正文有 ¹…²² 标记，却没有对应脚注文本）。

版面事实（实测本文）：
  - 脚注是页面底部的 9pt 文字，首行为单独的编号（1–2 位数字）；
  - 页脚页码也是 9pt，但位置更低（约 0.965H），且行内只有数字。
因此：取 9pt 行、限定 y ∈ [0.55H, 0.955H]、排除纯数字行，按"编号行"切块。
"""
import argparse, json, re
from pathlib import Path
import pymupdf

NUM_ONLY = re.compile(r"^\d{1,2}$")


def page_footnotes(page):
    H = page.rect.height
    lines = []
    for blk in page.get_text("dict")["blocks"]:
        for ln in blk.get("lines", []):
            y0 = ln["bbox"][1]
            if y0 < 0.55 * H or y0 > 0.955 * H:
                continue
            sz = max(round(s["size"], 1) for s in ln["spans"])
            if sz > 9.6:                     # 脚注字号
                continue
            txt = "".join(s["text"] for s in ln["spans"]).strip()
            if not txt:
                continue
            lines.append({"y": round(y0, 1), "text": txt})
    lines.sort(key=lambda l: l["y"])

    blocks, cur = [], None
    for l in lines:
        is_marker = bool(NUM_ONLY.match(l["text"])) or bool(re.match(r"^\d{1,2}\s+\S", l["text"]))
        if is_marker:
            if cur:
                blocks.append(cur)
            cur = {"y": l["y"], "marker": int(re.match(r"^(\d{1,2})", l["text"]).group(1)),
                   "lines": [re.sub(r"^\d{1,2}\s*", "", l["text"])]}
        elif cur is not None:
            if l["y"] - cur["lines_y"] > 14 if "lines_y" in cur else False:
                blocks.append(cur); cur = None
            else:
                cur["lines"].append(l["text"]); cur["lines_y"] = l["y"]
        # 非编号行且不在脚注块内 → 属于表格注释等，跳过
    if cur:
        blocks.append(cur)

    out = []
    for b in blocks:
        txt = " ".join(x for x in b["lines"] if x).strip()
        txt = re.sub(r"\s+", " ", txt)
        if len(txt) < 25:
            continue
        out.append({"marker": b["marker"], "y": b["y"], "text": txt})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf"); ap.add_argument("--out", required=True)
    a = ap.parse_args()
    doc = pymupdf.open(a.pdf)
    result = []
    for i, page in enumerate(doc, start=1):
        for f in page_footnotes(page):
            result.append({"page": i, **f})
    Path(a.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    markers = [r["marker"] for r in result]
    print(f"抽取脚注 {len(result)} 条；编号 {sorted(markers)}")
    print(f"总字符 {sum(len(r['text']) for r in result)}")
    for r in result:
        print(f"  [{r['marker']:>2}] p{r['page']:>2} {r['text'][:70]}")


if __name__ == "__main__":
    main()
