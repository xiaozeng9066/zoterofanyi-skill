#!/usr/bin/env python3
"""把审校修正整理成《规则四》要求的结构化校对记录。

规则四要求每条校对结果包含：原文位置、英文原句、当前译文、问题类型、
严重程度、修改建议、修改理由。本脚本从"审校变更日志 + 双语定稿 + 章节归属"
重建这七项，输出 JSON 与 CSV，便于逐条复核。
"""
import argparse, csv, json, re
from pathlib import Path

TYPE_RULES = [
    (r"页(码|数)|噪声|行号|脚注", "抽取噪声"),
    (r"数字|百分|数值|星号|p 值", "数字/统计量"),
    (r"引用|体例|括号|全角", "引用与体例"),
    (r"术语|译法|应为", "术语"),
    (r"漏译|缺失|未译", "漏译"),
    (r"拗口|生硬|不通|语序|可读", "中文表达"),
    (r"因果|确定性|限定", "语义强度"),
]


def classify(reason):
    for pat, name in TYPE_RULES:
        if re.search(pat, reason or ""):
            return name
    return "其他"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("changes")            # reports/review_changes.json
    ap.add_argument("bilingual")          # 定稿（含 section）
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--out-csv", required=True)
    a = ap.parse_args()

    ch = json.loads(Path(a.changes).read_text(encoding="utf-8"))
    doc = json.loads(Path(a.bilingual).read_text(encoding="utf-8"))
    by = {b["id"]: b for b in doc["blocks"]}

    rows = []
    for f in ch.get("applied", []):
        b = by.get(f["id"], {})
        rows.append({
            "原文位置": f"{f['id']} | p.{'-'.join(map(str, b.get('pages', [])))} | {b.get('section', '')}",
            "英文原句": (b.get("en") or b.get("caption_en") or "")[:600],
            "当前译文": f.get("new", "")[:600],
            "问题类型": classify(f.get("reason", "")),
            "严重程度": f.get("severity", ""),
            "修改建议": f.get("new", "")[:600],
            "修改理由": f.get("reason", ""),
            "旧译文": f.get("old", "")[:600],
        })
    for f in ch.get("manual_override", []):
        b = by.get(f["id"], {})
        rows.append({
            "原文位置": f"{f['id']} | p.{'-'.join(map(str, b.get('pages', [])))} | {b.get('section', '')}",
            "英文原句": (b.get("en") or "")[:600],
            "当前译文": f.get("new", "")[:600],
            "问题类型": classify(f.get("reason", "")) if f.get("reason") else "抽取噪声",
            "严重程度": "medium（人工裁决）",
            "修改建议": f.get("new", "")[:600],
            "修改理由": f.get("why", ""),
            "旧译文": f.get("old", "")[:600],
        })

    Path(a.out_json).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    with open(a.out_csv, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    from collections import Counter
    print(f"校对记录 {len(rows)} 条 -> {a.out_json} / {a.out_csv}")
    print("  问题类型分布:", dict(Counter(r["问题类型"] for r in rows)))
    print("  严重程度分布:", dict(Counter(r["严重程度"] for r in rows)))


if __name__ == "__main__":
    main()
