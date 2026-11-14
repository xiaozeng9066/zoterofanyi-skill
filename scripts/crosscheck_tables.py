#!/usr/bin/env python3
"""表格数字 ↔ 正文数字 交叉验证（三分类 + 星号核对）。

判定口径（避免把"跨表对比"误报成错误）：
  正文里每个"结果型数字"（含小数点，或 ≥100 的整数样本量）分三类：
    A. 出现在**被引用的那张表**里            → OK
    B. 只出现在**另一张表**里                → 跨表引用（如 OLS 0.28 对 FE 0.17），正常
    C. 任何表里都没有                        → 需人工判断（作者自算/文本独有/或真的错）
另外核对**显著性星号**：同一数值若表里带 3 星而正文写 1 星，属实质性错误。
"""
import argparse, json, re
from pathlib import Path
import pymupdf

VAL = re.compile(r"(-?\d+\.\d+)(\*{0,3})")          # 小数 + 可选星号
INT = re.compile(r"(?<![\d.])(\d{3,5})(?![\d.%])")   # 样本量级整数


def is_year(v):
    return 1900 <= v <= 2100


def match_value(val, decimals, table_vals):
    """按正文的小数位数四舍五入后再比：表里写 0.169，正文写 0.17，属于同值。"""
    for tv in table_vals:
        if decimals == 0:
            if abs(tv - val) < 1e-9:
                return True
        elif round(tv, decimals) == val:
            return True
    return False
TABREF = re.compile(r"\bTable\s+([A]?\d+)", re.I)


def table_tokens(pdf, page, rect):
    t = pdf[page - 1].get_text("text", clip=pymupdf.Rect(rect))
    out = {}
    for v, st in VAL.findall(t):
        out.setdefault(float(v), set()).add(len(st))
    for i in INT.findall(t):
        out.setdefault(float(i), set()).add(0)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf"); ap.add_argument("regions"); ap.add_argument("bilingual")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    doc = pymupdf.open(a.pdf)
    reg = json.loads(Path(a.regions).read_text(encoding="utf-8"))
    bil = json.loads(Path(a.bilingual).read_text(encoding="utf-8"))
    blocks = bil["blocks"]

    stem2num, last = {}, None
    for b in blocks:
        if b.get("kind") != "table":
            continue
        m = re.search(r"Table\s+([A]?\d+)", b.get("caption_en") or "", re.I)
        num = m.group(1) if m else last
        last = num
        stem2num[Path(b["image"]).stem] = num

    tables = {}
    for pg in reg["pages"]:
        for r in pg["regions"]:
            if r["kind"] != "table":
                continue
            num = stem2num.get(Path(r["file"]).stem)
            if not num:
                continue
            d = tables.setdefault(num, {})
            for k, v in table_tokens(doc, pg["page"], r["rect"]).items():
                d.setdefault(k, set()).update(v)

    rows, nA, nB, nC = [], 0, 0, 0
    for b in blocks:
        if b.get("kind") not in ("para", "heading") or b.get("no_translate"):
            continue
        en = b.get("en") or ""
        refs = TABREF.findall(en)
        if not refs:
            continue
        en_wo_pages = re.sub(r"\bp\.\s*\d+", " ", en, flags=re.I)   # 去掉引用页码
        vals = [(float(v), len(st), len(v.split(".")[1])) for v, st in VAL.findall(en_wo_pages)]
        vals += [(float(i), 0, 0) for i in INT.findall(en_wo_pages)]
        vals = [(v, st, dec) for v, st, dec in vals if not (dec == 0 and is_year(v))]
        if not vals:
            continue
        for val, stars, dec in vals:
            in_ref = any(match_value(val, dec, tables.get(r, {})) for r in refs)
            elsewhere = [r for r in tables if r not in refs and match_value(val, dec, tables[r])]
            if in_ref:
                nA += 1
                # 星号核对
                ok_stars = True
                if stars:
                    ok_stars = False
                    for r in refs:
                        for tv, sts in tables.get(r, {}).items():
                            if (round(tv, dec) == val or (dec == 0 and abs(tv - val) < 1e-9)) and stars in sts:
                                ok_stars = True
                if not ok_stars and stars:
                    rows.append({"id": b["id"], "pages": b.get("pages"), "class": "星号不一致",
                                 "value": val, "body_stars": stars, "tables": refs})
            elif elsewhere:
                nB += 1
                rows.append({"id": b["id"], "pages": b.get("pages"), "class": "跨表引用",
                             "value": val, "tables": elsewhere, "refs": refs})
            else:
                nC += 1
                pool = [tv for r in refs for tv in tables.get(r, {})] or \
                       [tv for r in tables for tv in tables[r]]
                near = min(pool, key=lambda tv: abs(tv - val)) if pool else None
                gap = round(near - val, 6) if near is not None else None
                # 不做"疑笔误"自动定性：描述统计表里数值成百上千，任何值都能找到近邻，
                # 自动判笔误必然假警报（实测 11.34 被 11.36 误判）。只给最近值与差额，由人判断。
                kind = "文中独有（表内无同量纲条目）"
                rows.append({"id": b["id"], "pages": b.get("pages"), "class": "表中无此值",
                             "value": val, "refs": refs, "nearest": near, "gap": gap,
                             "kind": kind, "en": en[:220]})

    # 3) 显著性阈值一致性（规则：不得因分块翻译导致星号含义漂移）
    star_pat = re.compile(r"(\*+)\}?\s*p\s*[<＜]\s*(0?\.\d+)")
    star_maps = {}
    for b in blocks:
        txt = " ".join(str(b.get(k) or "") for k in ("en", "zh", "note_en", "note_zh"))
        m = star_pat.findall(txt)
        if m:
            key = tuple(sorted({(len(x), v) for x, v in m}))
            star_maps.setdefault(key, []).append(b["id"])
    print(f"=== 表格数字 ↔ 正文数字 交叉验证（{len(tables)} 张表）===")
    print(f"  显著性阈值表述：{len(star_maps)} 种")
    for k, ids in star_maps.items():
        print(f"    {k}  ← {len(ids)} 处: {ids[:4]}{'…' if len(ids) > 4 else ''}")
    if len(star_maps) > 1:
        print("  ⚠ 存在多种星号含义，需人工核对")
    print(f"  命中被引表 A: {nA} | 跨表引用 B: {nB} | 表中无此值 C: {nC}")
    for cls in ("星号不一致", "表中无此值", "跨表引用"):
        sel = [r for r in rows if r["class"] == cls]
        if not sel:
            continue
        print(f"\n--- {cls}（{len(sel)}）---")
        for r in sel[:12]:
            extra = f" body={r['body_stars']}星" if cls == "星号不一致" else ""
            if cls == "表中无此值":
                print(f"  [{r['id']}] p{r['pages']} 值={r['value']} 类型={r.get('kind')} "
                      f"最近表值={r.get('nearest')} 差={r.get('gap')} 引用表={r.get('refs')}")
            else:
                print(f"  [{r['id']}] p{r['pages']} 值={r['value']}{extra} 引用表={r.get('refs')} 实际见于={r.get('tables')}")
            if r.get("en"):
                print(f"      原文: {r['en'][:150]}")

    Path(a.out).write_text(json.dumps({
        "tables": {k: sorted(v) for k, v in tables.items()},
        "counts": {"A_in_ref_table": nA, "B_cross_table": nB, "C_not_in_any": nC},
        "star_maps": {str(k): v for k, v in star_maps.items()},
        "rows": rows,
    }, ensure_ascii=False, indent=2, default=list), encoding="utf-8")
    print(f"\n报告 -> {a.out}")


if __name__ == "__main__":
    main()
