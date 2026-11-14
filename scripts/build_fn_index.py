#!/usr/bin/env python3
"""生成脚注编号对照表，并做正文 ^{n} 与脚注编号的交叉核对。

核对内容（对应规则四.6）：
  1. 正文出现但无对应脚注 → 缺失；
  2. 脚注存在但正文未引用 → 孤立；
  3. 编号是否 1..N 连续。
结果写入 reports/footnote_crossref.json。
"""
import argparse, json, re
from pathlib import Path

MARK = re.compile(r"\^\{(\d+)\}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bilingual"); ap.add_argument("--out", required=True)
    ap.add_argument("--report", default="")
    a = ap.parse_args()
    doc = json.loads(Path(a.bilingual).read_text(encoding="utf-8"))
    blocks = doc["blocks"]

    body_marks, fn = set(), {}
    for b in blocks:
        if b.get("kind") == "footnote":
            # 本篇补回的作者单位为字母标记 "A"：脚注块按字符串取键，
            # 数值交叉核对只针对纯数字标记（改动：原实现 int() 会直接崩）。
            fn[str(b["marker"])] = b
            continue
        for f in ("en", "zh"):
            for m in MARK.finditer(b.get(f) or ""):
                body_marks.add(int(m.group(1)))

    fn_nums = {int(k) for k in fn if str(k).isdigit()}
    missing = sorted(body_marks - fn_nums)      # 有标记无脚注
    orphan = sorted(fn_nums - body_marks)       # 有脚注无标记
    gaps = [i for i in range(1, max(fn_nums | body_marks) + 1)
            if i not in fn_nums] if (fn_nums or body_marks) else []

    rows = []
    for m in sorted(fn, key=lambda s: (len(str(s)), str(s))):
        b = fn[m]
        zh = (b.get("zh") or "").strip()
        en = (b.get("en") or "").strip()
        rows.append([m, "–".join(map(str, b.get("pages", []))),
                     (zh[:46] + "…") if len(zh) > 46 else (zh or "（待译）"),
                     (en[:46] + "…") if len(en) > 46 else en])

    # 对照表放在参考文献之前（若有），否则文末
    idx = next((i for i, b in enumerate(blocks)
                if b.get("kind") == "heading" and re.match(r"^(\d+\.?\s*)?references$",
                                                           (b.get("en") or "").strip(), re.I)), len(blocks))
    # 本篇无数字脚注（只有字母标记的作者单位）时，不插入空的脚注对照表：
    # 正文的 ^{n} 是参考文献编号，不是脚注标记。
    if fn_nums:
        blocks.insert(idx, {"id": "fn-index", "kind": "fn_index",
                            "pages": sorted({p for b in fn.values() for p in b.get("pages", [])}),
                            "rows": rows, "sort": None})
    Path(a.out).write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"脚注 {len(fn)} 条；正文引用编号 {sorted(body_marks)}")
    print(f"  正文有标记但无脚注: {missing or '无'}")
    print(f"  有脚注但正文无标记: {orphan or '无'}")
    print(f"  编号不连续: {gaps or '无'}")
    if a.report:
        Path(a.report).write_text(json.dumps({
            "footnotes": sorted(fn, key=lambda s: (len(str(s)), str(s))), "body_marks": sorted(body_marks),
            "missing_in_notes": missing, "orphan_notes": orphan, "gaps": gaps,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  报告 -> {a.report}")


if __name__ == "__main__":
    main()
