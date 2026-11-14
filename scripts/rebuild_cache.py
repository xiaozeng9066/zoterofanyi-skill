#!/usr/bin/env python3
"""用一份"已审校定稿"的双语 JSON 重建翻译缓存。

为什么需要：缓存是在审校之前生成的，直接复用会把审校修正回退。
以最终版为准重建后，未改动的块命中缓存（保留审校成果），
仅英文文本真正变化的块才会重译。
"""
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from translate import key_of, tasks_of

ap = argparse.ArgumentParser()
ap.add_argument("bilingual"); ap.add_argument("--out", required=True)
a = ap.parse_args()
d = json.loads(Path(a.bilingual).read_text(encoding="utf-8"))
gv = d.get("meta", {}).get("glossary_version") or "v0"
n = 0
with open(a.out, "w", encoding="utf-8") as f:
    for b in d["blocks"]:
        if b.get("no_translate"):
            continue
        for task_id, dst, text in tasks_of(b):
            if text.strip() and b.get(dst):
                f.write(json.dumps({"key": key_of(text, gv), "zh": b[dst], "id": task_id},
                                   ensure_ascii=False) + "\n")
                n += 1
print(f"rebuilt cache: {n} entries (glossary_version={gv}) -> {a.out}")
