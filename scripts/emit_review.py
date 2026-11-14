#!/usr/bin/env python3
"""生成"二遍审校"任务块：每条含英文原文 + 现有译文，供审校者逐条核对。

两种输出契约（--mode）：
  patch（默认，省 token）：审校者只回**改动片段** edits[{find, replace}]，由 apply_fixes.py 落到原文上；
                          避免重抄整段（既省输出，也避免重抄时手滑引入新错）。
  full（旧）：审校者回整段新译文 zh。

chunk 里直接带 fix_schema，派子代理时不必再手写输出格式说明。
"""
import argparse, json
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("bilingual"); ap.add_argument("--outdir", required=True)
ap.add_argument("--per-chunk", type=int, default=20)
ap.add_argument("--mode", choices=["patch", "full"], default="patch")
a = ap.parse_args()

d = json.loads(Path(a.bilingual).read_text(encoding="utf-8"))
items = []
for b in d["blocks"]:
    if b.get("no_translate"):
        continue
    en = b.get("en") or b.get("caption_en") or ""
    zh = b.get("zh") or b.get("caption_zh") or ""
    if not en.strip() or not zh.strip():
        continue
    items.append({"id": b["id"], "kind": b["kind"], "en": en, "zh": zh})

out = Path(a.outdir); out.mkdir(parents=True, exist_ok=True)
n = 0
for i in range(0, len(items), a.per_chunk):
    n += 1
    chunk = items[i:i + a.per_chunk]
    if a.mode == "patch":
        schema = {"chunk_id": n, "fixes": [
            {"id": "<原 id>",
             "edits": [{"find": "<现有译文里的原文片段，必须唯一出现且 ≥4 字>",
                        "replace": "<改成什么>", "reason": "<一句话>",
                        "severity": "high|medium|low"}]}]}
        note = ("只列**需要修改**的条目；每处改动用 edits 给出最小片段（find 必须在现有译文中恰好出现一次、"
                "长度 ≥4 字）；只有整段重写时才用 {\"id\":..., \"zh\":\"整段新译文\"}。没问题的条目不写入。")
    else:
        schema = {"chunk_id": n, "fixes": [
            {"id": "<原 id>", "zh": "<修订后的完整中文>", "reason": "<一句话>", "severity": "high|medium|low"}]}
        note = "只列需要修改的条目；zh 必须是完整替换文本。没问题的条目不写入。"
    (out / f"review_{n:03d}.json").write_text(json.dumps({
        "chunk_id": n,
        "mode": a.mode,
        "glossary": d.get("glossary", []),
        "style": "学术中文：忠实、术语一致、保留英文引用与数字原样、不增删内容",
        "fix_schema": schema,
        "note": note,
        "items": chunk,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"emit review: {len(items)} 条 -> {n} 块（每块 {a.per_chunk} 条，mode={a.mode}）到 {out}")
