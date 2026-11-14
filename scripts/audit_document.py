#!/usr/bin/env python3
"""全篇一致性审计（audit_document）——按用户规则要求，覆盖五类审计。

设计要点（都是踩过坑换来的）：
  1. **扫描全部文本字段**：en/zh/caption_en/caption_zh/note_en/note_zh/legend。
     只扫 en+zh 会漏掉表格注释（实测"超对数"就藏在 note_zh 里）。
  2. 术语审计不只比"英文对中文"，还要找**同一英文术语的多种中文译法**（混用）。
  3. 脚注审计不只数数，还要查**挂接位置**（是否被塞进词语/句子中间）与**编号连续性**。
  4. 数值审计除数字外，还要查**显著性星号层级**与**标准误类型**是否随原文。
  5. 每条发现都要能归入五类之一：
     确认的译文错误 / 疑似原文错误 / 术语不一致 / 排版错误 / 待人工判断。
"""
import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

FIELDS = ("en", "zh", "caption_en", "caption_zh", "note_en", "note_zh")

# 同一概念的可疑变体（出现两种以上即视为混用）
VARIANTS = {
    "correlated effects": ["关联效应", "相关效应"],
    "peer group": ["同伴组", "同伴群体", "同伴群组", "同辈群体"],
    "contextual effects": ["情境效应", "语境效应", "背景效应"],
    "reflection problem": ["反射问题", "反映问题", "镜像问题"],
    "fixed effects": ["固定效应", "固定效果"],
    "marginal effects": ["边际效应", "边际效果"],
    "quantile regression": ["分位数回归", "分位回归"],
    "instrumental variable": ["工具变量", "工具变数"],
    "robust standard errors": ["稳健标准误", "稳健标准误差", "异方差稳健标准误"],
    "confidence interval": ["置信区间", "可信区间", "信赖区间"],
    "translog": ["超越对数", "超对数"],
    "obesity": ["肥胖", "肥胖症"],
    "overweight": ["超重", "过重"],
    "selection effects": ["选择效应", "选择效果"],
    "rural": ["农村", "乡村"],
}

MARK = re.compile(r"\^\{([^}]*)\}")   # 单个 ^{…}
CJK_DIGIT = re.compile(r"[\u4e00-\u9fff](\d{1,3})[\u4e00-\u9fff]")
GLUED = re.compile(r"[a-z]{2,}[A-Z][a-z]{2,}")
# 专有名词白名单：这些"小写+大写"是词本身的结构，不是丢空格
SUP_SEQ = re.compile(r"(?:\^\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\})+")


def sup_tokens(text):
    """把连续的 ^{…} 碎片按顺序拼成一个上标 token。
    本类 PDF 会把 Θ^{(ν)} 拆成 ^{}^{(}^{ν}^{)}，直接按单碎片比对会全部误报。"""
    out = []
    for m in SUP_SEQ.finditer(text):
        out.append((m.group(0) or "").replace("^{", "").replace("}", ""))
    return out


GLUED_ALLOW = {
    "GridSearch", "PostGres", "PostgreSQL", "SQLite", "MySQL", "SpringerPlus",
    "SpringerLink", "SpringerNature", "JavaScript", "TypeScript", "GitHub",
    "GitLab", "OpenAI", "DeepSeek", "ModelScope", "UKBiobank", "PyMuPDF",
    "PubMed", "CrossRef", "MacBook", "BioBank", "LaTeX", "TensorFlow", "PyTorch",
    "NumPy", "SciPy", "pdf2zh", "BabelDOC", "MinerU", "Auditbase", "camelCase", "OneStg", "TwoStg", "IndGBTM", "OneStage", "TwoStage",
}


def glued_token(text, start, end):
    i, j = start, end
    while i > 0 and text[i - 1].isalpha():
        i -= 1
    while j < len(text) and text[j].isalpha():
        j += 1
    return text[i:j]
          # AdelaideXiaobo 型黏连
HEAD_NUM = re.compile(r"^(\d+(?:\.\d+)*)\.?\s*")
TABLE_REF = re.compile(r"\bTable\s+([A]?\d+)", re.I)
FIG_REF = re.compile(r"\bFigure\s+([A]?\d+)", re.I)


def alltext(b, only=None):
    out = []
    for f in (only or FIELDS):
        v = b.get(f)
        if isinstance(v, str) and v:
            out.append(v)
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bilingual")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    doc = json.loads(Path(a.bilingual).read_text(encoding="utf-8"))
    blocks = doc["blocks"]
    F = []          # findings
    CATS = ("确认的译文错误", "疑似原文错误", "术语不一致", "排版错误", "待人工判断")

    def add(cat, level, bid, msg, ev=""):
        F.append({"类别": cat, "级别": level, "id": bid, "问题": msg, "证据": ev[:200]})

    # ---------- A1 术语一致性（全字段） ----------
    variant_hits = defaultdict(Counter)
    for b in blocks:
        zt = " ".join(x for x in (b.get("zh"), b.get("caption_zh"), b.get("note_zh")) if x)
        for term, vs in VARIANTS.items():
            for v in vs:
                n = zt.count(v)
                if n:
                    variant_hits[term][v] += n
    for term, cnt in variant_hits.items():
        used = [v for v, n in cnt.items() if n > 0]
        if len(used) > 1:
            add("术语不一致", "FAIL", "全文", f"「{term}」存在多种译法: {dict(cnt)}")

    # ---- A1b 以英文为锚的术语核查（避免"城市"在 city 语境下被误报） ----
    ANCHOR = [
        ("urban", r"\burban\b", ["城镇", "城乡", "城/乡"], r"\b(city|metropolitan)\b"),
        ("rural", r"\brural\b", ["农村", "乡村", "城乡", "城/乡"], None),
        ("translog", r"\btranslog\b", ["translog"], None),
        ("correlated effects", r"\bcorrelated effects\b", ["相关效应"], None),
        ("peer group", r"\bpeer group\b", ["同伴群体", "同伴组"], None),
        ("contextual effects", r"\bcontextual effects\b", ["情境效应"], None),
    ]
    for name, en_pat, wants, exclude in ANCHOR:
        bad = []
        for b in blocks:
            en = b.get("en") or b.get("caption_en") or b.get("note_en") or ""
            if not re.search(en_pat, en, re.I):
                continue
            if exclude and re.search(exclude, en, re.I):
                continue
            zt = " ".join(x for x in (b.get("zh"), b.get("caption_zh"), b.get("note_zh")) if x)
            if zt and not any(w in zt for w in wants):
                bad.append(b["id"])
        if bad:
            add("术语不一致", "FAIL", f"{len(bad)} 块",
                f"「{name}」在原文出现但译文未用约定译法 {wants}", str(bad[:8]))

    # ---------- A2 脚注与编号 ----------
    seq = []
    for b in blocks:
        en = b.get("en") or b.get("caption_en") or ""
        zh = " ".join(x for x in (b.get("zh"), b.get("caption_zh"), b.get("note_zh")) if x)
        # 用"拼接后的上标 token"比对：Θ^{(}^{ν}^{)} 与 Θ^{(ν)} 视为同一个上标
        en_m = Counter(sup_tokens(en))
        zh_m = Counter(sup_tokens(zh))
        for k, n in en_m.items():
            if zh_m.get(k, 0) < n:
                add("确认的译文错误", "FAIL", b["id"], f"原文上标 ^{k} 在译文中缺失或减少",
                    f"EN={dict(en_m)} ZH={dict(zh_m)}")
        # 挂接位置：标记紧跟在汉字或字母之后（未被标点隔开）→ 可疑
        for m in re.finditer(r"([\u4e00-\u9fffA-Za-z])\^\{(\d+)\}", zh):
            tail = zh[m.end():m.end() + 6]
            if m.group(1) not in "。．.，,；;：:）)":
                add("排版错误", "WARN", b["id"],
                    f"脚注标记 ^{m.group(2)} 紧贴词语「…{m.group(1)}」后，需核对是否应在标点之后",
                    zh[max(0, m.start() - 25):m.end() + 15])
        # 汉字夹数字（漏转上标）
        for m in CJK_DIGIT.finditer(zh):
            if not MARK.search(zh[max(0, m.start() - 3):m.end() + 3]):
                add("排版错误", "FAIL", b["id"], f"汉字之间夹裸数字「{m.group(1)}」",
                    zh[max(0, m.start() - 25):m.end() + 25])
        for m in MARK.finditer(en):
            if m.group(1).strip().isdigit():
                seq.append(int(m.group(1).strip()))

    if seq:
        inc = [x for a_, x in zip(seq, seq[1:]) if x < a_]
        dup = [k for k, v in Counter(seq).items() if v > 1]
        if inc:
            add("待人工判断", "WARN", "全文", f"正文脚注编号出现回退（可能跨块顺序错乱）: {inc[:6]}")
        if dup:
            add("待人工判断", "WARN", "全文", f"脚注编号重复出现: {dup[:6]}")
        missing = [i for i in range(1, max(seq) + 1) if i not in set(seq)]
        if missing:
            add("待人工判断", "WARN", "全文", f"脚注编号不连续，缺: {missing[:8]}")

    # ---------- A3 数值与统计信息 ----------
    for b in blocks:
        en = b.get("en") or ""
        zh = b.get("zh") or b.get("caption_zh") or ""
        if not en.strip() or not zh.strip():
            continue
        en_st = Counter(re.findall(r"\*{1,3}", en))
        zh_st = Counter(re.findall(r"\*{1,3}", zh))
        if en_st != zh_st and (en_st or zh_st):
            add("确认的译文错误", "WARN", b["id"], "显著性星号层级与原文不一致",
                f"EN={dict(en_st)} ZH={dict(zh_st)}")
        for kw_en, kw_zh in (("robust standard errors", "稳健标准误"),
                             ("bootstrapped standard errors", "自助法")):
            if kw_en in en and kw_zh not in zh:
                add("确认的译文错误", "WARN", b["id"], f"标准误类型「{kw_en}」未体现", zh[:120])

    # ---------- A4 结构与版式 ----------
    heads = [(b["id"], (b.get("en") or ""), (b.get("zh") or ""), b.get("section"))
             for b in blocks if b.get("kind") == "heading"]
    by = {b["id"]: b for b in blocks}
    for hid, en, zh, _ in heads:
        if by.get(hid, {}).get("no_translate"):
            continue
        if en and not zh:
            add("确认的译文错误", "FAIL", hid, "标题无译文", en[:80])
        ne, nz = HEAD_NUM.match(en), HEAD_NUM.match(zh or "")
        if ne and nz and ne.group(1) != nz.group(1):
            add("确认的译文错误", "FAIL", hid, f"章节编号不一致 EN={ne.group(1)} ZH={nz.group(1)}")
        if ne and not nz:
            add("排版错误", "WARN", hid, "中文标题缺章节编号", f"EN={en[:40]} ZH={zh[:40]}")
    for b in blocks:
        for f in ("en", "zh"):
            v = b.get(f) or ""
            for m in GLUED.finditer(v):
                tok = glued_token(v, m.start(), m.end())
                if tok in GLUED_ALLOW or any(tok.endswith(a) and len(tok) <= len(a) + 6
                                             for a in GLUED_ALLOW):
                    continue
                add("排版错误", "FAIL", b["id"], f"{f} 字段出现无空格黏连「{m.group(0)}」（token={tok}）",
                    v[max(0, m.start() - 30):m.end() + 20])

    # 表/图引用编号覆盖
    en_tab = {x for b in blocks for x in TABLE_REF.findall(b.get("en") or "")}
    zh_tab = {x for b in blocks for x in re.findall(r"表\s*([A]?\d+)", alltext(b, ("zh", "caption_zh", "note_zh"))) }
    if en_tab - zh_tab:
        add("确认的译文错误", "WARN", "全文", f"表号未在译文中出现: {sorted(en_tab - zh_tab)}")

    # ---------- 汇总 ----------
    by_cat = Counter(x["类别"] for x in F)
    print(f"=== 全篇一致性审计：{len(blocks)} 块，{len(F)} 条发现 ===")
    for c in CATS:
        if by_cat.get(c):
            print(f"  {c}: {by_cat[c]}")
    for x in F:
        print(f"\n[{x['类别']}/{x['级别']}] {x['id']} {x['问题']}")
        if x["证据"]:
            print(f"    {x['证据']}")
    Path(a.out).write_text(json.dumps({"findings": F, "by_category": dict(by_cat)},
                                      ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告 -> {a.out}")


if __name__ == "__main__":
    main()
