#!/usr/bin/env python3
"""术语一致性审计：并行翻译最容易出的问题不是错译，而是同一术语被译成多个说法。

做法：
  1. 对术语表每一对 (en, zh)，统计它在英文原文里出现于哪些块；
  2. 检查这些块的中文里是否使用了约定的中文译名（含简称）；
  3. 报告"英文出现但中文未用约定译名"的块，并给出该块中文片段，便于人工判断是否漂移；
  4. 额外列出全文中出现频次最高的若干"疑似同义变体"（如 同伴效应/同辈效应/同伴影响），
     由人确认是否为不一致。
"""
import argparse, json, re
from collections import Counter, defaultdict
from pathlib import Path


def canon(t):
    return (t or "").translate(str.maketrans({"（": "(", "）": ")", "，": ",", "：": ":"}))


def variants(term_zh):
    out = []
    for a in re.split(r"[/｜|]", term_zh):
        a = a.strip()
        if not a:
            continue
        out.append(a)
        short = re.sub(r"[（(][^）)]*[）)]", "", a).strip()
        if short and short != a:
            out.append(short)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bilingual")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    d = json.loads(Path(a.bilingual).read_text(encoding="utf-8"))
    blocks = [b for b in d["blocks"] if not b.get("no_translate")]
    glossary = d.get("glossary") or []

    rows, drift = [], []
    for en_term, zh_term in glossary:
        alts = variants(zh_term)
        # 必须按词边界匹配：不加 \b 会把 "contROLS" 里的 "ols" 当成术语 OLS（审计器曾如此误报）
        pat = r"\b" + re.escape(en_term) + r"\b"
        hit_blocks = [b for b in blocks if re.search(pat, b.get("en") or "", re.I)]
        if not hit_blocks:
            continue
        ok = sum(1 for b in hit_blocks if any(x in (b.get("zh") or "") for x in alts))
        bad = [b for b in hit_blocks if not any(x in (b.get("zh") or "") for x in alts)]
        rows.append({"en": en_term, "zh": zh_term, "blocks": len(hit_blocks), "ok": ok, "miss": len(bad)})
        for b in bad[:6]:
            drift.append({"en": en_term, "expect": zh_term, "id": b["id"],
                          "zh": (b.get("zh") or "")[:160]})

    # 疑似同义变体：统计"同伴""同辈""肥胖""超重"等词族的高频搭配
    fam = defaultdict(Counter)
    for b in blocks:
        z = b.get("zh") or ""
        for m in re.finditer(r"[\u4e00-\u9fff]{2,3}(效应|影响|作用|水平|状况)", z):
            fam["peer/obesity 词族"][m.group(0)] += 1

    print(f"=== 术语审计（{len(rows)} 个术语在英文中出现）===")
    print(f"{'英文术语':<34}{'块数':>4}{'命中':>5}{'缺失':>5}")
    for r in sorted(rows, key=lambda x: -x["miss"]):
        if r["miss"]:
            print(f"{r['en'][:33]:<34}{r['blocks']:>4}{r['ok']:>5}{r['miss']:>5}   ← 需关注")
    print(f"\n=== 缺失样例（{len(drift)} 条）===")
    for x in drift[:12]:
        print(f"  [{x['id']}] 期望「{x['expect']}」 实际：{x['zh'][:90]}")
    print("\n=== 高频搭配（人工确认是否同义混用）===")
    for k, c in fam.items():
        print(f"  {k}: " + "、".join(f"{w}({n})" for w, n in c.most_common(8)))

    if a.out:
        Path(a.out).write_text(json.dumps({"rows": rows, "drift": drift}, ensure_ascii=False, indent=2),
                               encoding="utf-8")
        print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
