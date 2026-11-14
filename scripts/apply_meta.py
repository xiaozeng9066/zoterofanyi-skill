#!/usr/bin/env python3
"""把作者手写的 meta/glossary/legend 覆盖进骨架或双语 JSON（渲染前的最后一道人工入力）。"""
import argparse, json
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("doc"); ap.add_argument("meta"); ap.add_argument("--out", default="")
a = ap.parse_args()
doc = json.loads(Path(a.doc).read_text(encoding="utf-8"))
patch = json.loads(Path(a.meta).read_text(encoding="utf-8"))
doc["meta"] = {**doc.get("meta", {}), **patch.get("meta", {})}
doc["glossary"] = patch.get("glossary", doc.get("glossary", []))
for bid, legend in (patch.get("legends") or {}).items():
    for b in doc.get("blocks", []):
        if b.get("id") == bid:
            b["legend"] = legend
            break
    else:
        print(f"  [warn] legend 目标块不存在: {bid}")
out = Path(a.out or a.doc)
out.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"applied meta/glossary/legends -> {out}")
