#!/usr/bin/env python3
"""对"已翻译"的双语 JSON 合并跨页段落：英文与中文都直接拼接。

说明：更彻底的做法是**在翻译前**合并（assemble.py 已实现，今后的文献走那条路）。
本脚本只用于修已完成的译稿：两半各自是按片段忠实翻译的，拼接后语义连贯。
CJK 与 CJK 之间不留空格。
"""
import argparse, json, re
from pathlib import Path

TERM_RE = re.compile(r"[.!?:;。！？：；\"”’)]\s*$")


def join_en(a, b):
    return a.rstrip() + " " + b.lstrip()


def join_zh(a, b):
    a, b = a.rstrip(), b.lstrip()
    if not a or not b:
        return (a + b).strip()
    cjk = lambda ch: "\u4e00" <= ch <= "\u9fff"
    return a + ("" if cjk(a[-1]) and cjk(b[0]) else " ") + b


ap = argparse.ArgumentParser()
ap.add_argument("doc"); ap.add_argument("--out", required=True)
a = ap.parse_args()
d = json.loads(Path(a.doc).read_text(encoding="utf-8"))
out, n = [], 0
for it in d["blocks"]:
    if (out and it["kind"] == "para" and out[-1]["kind"] == "para"
            and not out[-1].get("no_translate") and not it.get("no_translate")
            and it.get("pages") and out[-1].get("pages")
            and it["pages"][0] - out[-1]["pages"][-1] <= 1):
        prev = out[-1]
        pe, ce = (prev.get("en") or "").rstrip(), (it.get("en") or "").lstrip()
        if pe and ce and not TERM_RE.search(pe) and (ce[0].islower() or "\u4e00" <= ce[0] <= "\u9fff"):
            prev["en"] = join_en(pe, ce)
            prev["zh"] = join_zh(prev.get("zh") or "", it.get("zh") or "")
            prev["pages"] = sorted(set(prev["pages"] + it["pages"]))
            prev["continued"] = True
            n += 1
            continue
    out.append(it)
d["blocks"] = out
Path(a.out).write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"merged {n} cross-page paragraphs -> {a.out}  ({len(out)} blocks)")
