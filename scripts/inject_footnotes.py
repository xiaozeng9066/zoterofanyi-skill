#!/usr/bin/env python3
"""把抽取到的脚注注入双语文档，成为独立块（kind=footnote）。

放置规则（避免与跨页合并块冲突）：
  脚注 P 插入在**第一个起始页 > P 的块之前**，即"该页所有内容之后"。
  不用"页末触发"的方式——跨页合并块的 pages 会跨越两页甚至三页，
  按 == last_page 判断会漏页或错位（前两版都踩过）。
"""
import argparse, json
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("bilingual"); ap.add_argument("footnotes"); ap.add_argument("--out", required=True)
a = ap.parse_args()
doc = json.loads(Path(a.bilingual).read_text(encoding="utf-8"))
fns = json.loads(Path(a.footnotes).read_text(encoding="utf-8"))

by_page = {}
for f in fns:
    by_page.setdefault(f["page"], []).append(f)
for v in by_page.values():
    v.sort(key=lambda x: x["marker"])

out, section = [], None


def flush_upto(page_limit):
    for pg in sorted([x for x in by_page if x <= page_limit]):
        for f in by_page.pop(pg):
            out.append({
                "id": f"p{pg}-fn{f['marker']}",
                "kind": "footnote", "pages": [pg],
                "marker": f["marker"], "en": f["text"], "zh": "",
                "section": section,
            })


for b in doc["blocks"]:
    if b.get("kind") == "heading":
        section = (b.get("zh") or b.get("en") or "").strip()[:60]
    first = (b.get("pages") or [10 ** 6])[0]
    flush_upto(first - 1)      # 该块之前，把所有"更早页"的脚注放出来
    out.append(b)
flush_upto(10 ** 6)

doc["blocks"] = out
Path(a.out).write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"注入脚注 {len(fns)} 条 -> {a.out}（块数 {len(doc['blocks'])}）")
