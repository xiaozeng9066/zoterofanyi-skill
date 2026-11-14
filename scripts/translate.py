#!/usr/bin/env python3
"""阶段2b：翻译编排的分块与合并（不含 LLM 调用本身）。

为什么把"翻译"拆成 emit / merge 两半：
  - 本环境没有独立可调的翻译 API 端点，译文由**子代理**产出（每个子代理译一块）；
  - 因此脚本的职责是：分块（控 token）、按内容哈希缓存（改术语/补段时只重译变动块）、
    合并回写、以及**覆盖率与完整性校验**（宁可报错，绝不静默丢段）。

用法：
  emit  : translate.py emit  doc_skeleton.json --outdir chunks/ --chars 6000
  merge : translate.py merge doc_skeleton.json --chunks chunks/ --out bilingual.json
"""
import argparse
import hashlib
import json
import re
from pathlib import Path

TRANSLATABLE = {"para": "en", "heading": "en", "title": "en", "footnote": "en"}
CAPTION_KEYS = ("caption_en", "stat_en", "note_en")


def tasks_of(it):
    """把一个块拆成若干独立翻译任务：(task_id, 目标字段, 英文文本)。

    图注/脚注必须与正文分开成任务，否则合并时无法可靠拆分回 caption_zh / note_zh
    （实测：表注译文进了 zh，渲染器读 caption_zh，结果表注只剩英文）。
    """
    out = []
    if it.get("no_translate"):
        return out
    if it["kind"] in TRANSLATABLE and it.get("en"):
        out.append((it["id"], "zh", it["en"]))
    for key, dst in (("caption_en", "caption_zh"), ("note_en", "note_zh")):
        if it.get(key):
            out.append((f"{it['id']}::{key.split('_')[0]}", dst, it[key]))
    return out


def item_source(it):
    return "\n".join(t[2] for t in tasks_of(it))


def key_of(text, glossary_version):
    return hashlib.sha256(f"{glossary_version}\x00{text}".encode("utf-8")).hexdigest()[:16]


def load_cache(path):
    if not path or not Path(path).exists():
        return {}
    out = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        out[rec["key"]] = rec["zh"]
    return out


def cmd_emit(args):
    doc = json.loads(Path(args.doc).read_text(encoding="utf-8"))
    gv = (doc.get("meta", {}) or {}).get("glossary_version") or doc.get("glossary_version") or "v0"
    cache = load_cache(args.cache)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    todo, cached = [], []
    for it in doc["blocks"]:
        for task_id, dst, text in tasks_of(it):
            if not text.strip():
                continue
            k = key_of(text, gv)
            if k in cache:
                cached.append({"id": task_id, "dst": dst, "zh": cache[k]})
            else:
                todo.append({"id": task_id, "dst": dst, "kind": it["kind"],
                             "en": text, "key": k})

    chunks, cur, size = [], [], 0
    for t in todo:
        if cur and size + len(t["en"]) > args.chars:
            chunks.append(cur)
            cur, size = [], 0
        cur.append(t)
        size += len(t["en"])
    if cur:
        chunks.append(cur)

    manifest = []
    for i, ch in enumerate(chunks, 1):
        p = outdir / f"chunk_{i:03d}.json"
        p.write_text(json.dumps({
            "chunk_id": i,
            "glossary": doc.get("glossary", []),
            "style": args.style,
            "items": ch,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest.append({"chunk_id": i, "file": p.name, "items": len(ch),
                         "chars": sum(len(x["en"]) for x in ch)})

    (outdir / "manifest.json").write_text(json.dumps({
        "doc": str(args.doc), "glossary_version": gv,
        "total_items": len(doc["blocks"]), "todo_items": len(todo),
        "cached_items": len(cached), "chunks": manifest,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"emit: 待译 {len(todo)} 条 / 命中缓存 {len(cached)} 条 -> {len(chunks)} 块，输出到 {outdir}")
    for m in manifest:
        print(f"  {m['file']}  items={m['items']} chars={m['chars']}")
    if args.cache and cached:
        print(f"  缓存文件: {args.cache}（已命中的条目无需重译）")


def cmd_merge(args):
    doc = json.loads(Path(args.doc).read_text(encoding="utf-8"))
    gv = (doc.get("meta", {}) or {}).get("glossary_version") or doc.get("glossary_version") or "v0"
    src = {it["id"]: it for it in doc["blocks"]}

    got = {}
    files = sorted(Path(args.chunks).glob("trans_*.json"))
    if not files:   # 兼容自定义命名：排除输入块与清单
        files = [p for p in sorted(Path(args.chunks).glob("*.json"))
                 if not p.name.startswith("chunk_") and p.name != "manifest.json"]
    for p in files:
        data = json.loads(p.read_text(encoding="utf-8"))
        for t in data.get("items", []):
            if "zh" not in t or not str(t["zh"]).strip():
                raise ValueError(f"{p.name}: 条目 {t.get('id')} 缺少译文（拒绝静默丢段）")
            if t["id"] in got:
                raise ValueError(f"条目 {t['id']} 在多个块里重复出现")
            got[t["id"]] = str(t["zh"]).strip()

    # 缓存兜底：未在新译文里出现的任务，按内容哈希从缓存补齐
    cache = load_cache(args.cache) if args.cache else {}
    from_cache = 0
    for it in doc["blocks"]:
        for task_id, dst, text in tasks_of(it):
            if task_id not in got and text.strip():
                k = key_of(text, gv)
                if k in cache:
                    got[task_id] = cache[k]
                    from_cache += 1
    if from_cache:
        print(f"merge: 其中 {from_cache} 条来自内容哈希缓存（未重译）")

    # 覆盖率校验：任何需要翻译的任务缺失都必须报错
    missing = []
    for it in doc["blocks"]:
        for task_id, dst, text in tasks_of(it):
            if text.strip() and task_id not in got:
                missing.append(task_id)
    if missing:
        raise ValueError(f"有 {len(missing)} 条未获得译文，例如 {missing[:8]}（拒绝用原文占位）")

    # 回写：按任务目标字段写（zh / caption_zh / note_zh）
    for it in doc["blocks"]:
        for task_id, dst, text in tasks_of(it):
            if task_id in got:
                it[dst] = got[task_id]

    out = Path(args.out)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.cache:
        cp = Path(args.cache)
        cp.parent.mkdir(parents=True, exist_ok=True)
        with cp.open("a", encoding="utf-8") as f:
            for it in doc["blocks"]:
                for task_id, dst, text in tasks_of(it):
                    if text.strip() and it.get(dst):
                        f.write(json.dumps({"key": key_of(text, gv), "zh": it[dst],
                                            "id": task_id}, ensure_ascii=False) + "\n")
    print(f"merge: 回写 {len(got)} 条译文 -> {out}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("emit")
    e.add_argument("doc")
    e.add_argument("--outdir", required=True)
    e.add_argument("--chars", type=int, default=6000, help="每块英文上限（字符）")
    e.add_argument("--cache", default="")
    e.add_argument("--style", default="学术中文：忠实、术语一致、保留英文引用与数字原样、不增删内容")
    e.set_defaults(func=cmd_emit)

    m = sub.add_parser("merge")
    m.add_argument("doc")
    m.add_argument("--chunks", required=True)
    m.add_argument("--out", required=True)
    m.add_argument("--cache", default="")
    m.set_defaults(func=cmd_merge)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
