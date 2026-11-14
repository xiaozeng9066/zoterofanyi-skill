#!/usr/bin/env python3
"""把"按约定不翻译"的区块标上 no_translate（目前是参考文献）。

学术惯例：参考文献条目保留原文，只要求引用编号与原文一致。
标上之后 translate.py 不再为它们分块，省下整段翻译预算（实测本文占 28% 字符）。
"""
import argparse, json, re
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("doc"); ap.add_argument("--out", default="")
a = ap.parse_args()
d = json.loads(Path(a.doc).read_text(encoding="utf-8"))

# 参考文献条目的形态：首字母大写的姓 + 逗号/名 + (年份)
REF_ENTRY = re.compile(r"^[A-Z][^.]{0,60}\(\d{4}\)")
# 参考条目的其他形态：含"作者 + (年份)"、且带引号标题，或一段里出现多个 (年份)
REF_LOOSE = re.compile(r"\(\d{4}[a-z]?\)")


def looks_like_ref(txt):
    if REF_ENTRY.match(txt):
        return True
    years = REF_LOOSE.findall(txt)
    if len(years) >= 2:
        return True
    if years and ("\u201c" in txt[:220] or ", " in txt[:80]):
        return True
    return False
in_refs = False
n = 0
for b in d.get("blocks", []):
    b.pop("no_translate", None)          # 重跑时先清除旧标记，避免累积误标
for b in d.get("blocks", []):
    txt = (b.get("en") or "").strip()
    if b.get("kind") == "heading" and re.match(r"^(\d+\.?\s*)?references$", txt, re.I):
        in_refs = True
        # 参考文献「标题」本身要译为中文（"参考文献"），不能 no_translate；
        # 仅其下的条目保留原文。否则交付门禁 A-覆盖会 BLOCK（标题缺中文）。
        n += 1
        continue
    if not in_refs or b.get("kind") not in ("para", "heading"):
        continue
    # **关键修复**：参考文献区在遇到"不再是参考条目"的块时结束
    # （本文附录表 A1–A11 排在参考文献之后，原逻辑会把附录表注一并误标为不翻译）
    if looks_like_ref(txt) or (b.get("kind") == "heading" and not txt):
        b["no_translate"] = True
        n += 1
    else:
        in_refs = False

out = Path(a.out or a.doc)
out.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"marked no_translate: {n} blocks -> {out}")
