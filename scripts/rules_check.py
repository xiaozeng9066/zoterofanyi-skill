#!/usr/bin/env python3
"""按《科研论文翻译与校对质量控制规则》做机械审查（rules_check）。

预设立场：这些检查只覆盖"能查得出"的项；它查不出译得好不好，
但能查出**规则明文禁止**的形态：
  R1.3 相关性不得提升为因果
  R1.4 限定词/否定/比较关系丢失
  R2.2 概念混用（obesity/overweight/bodyweight/waist/BMI）
  R2.3-2.4 计量术语按约定译法
  R3.1 句子不完整
  R3.2 段落缺 ID/页码/章节归属
  R4.6 脚注上标、表图编号缺失
  R5.1 覆盖率（每块都有译文）

输出：JSON（供验收报告引用）+ 控制台摘要；每条命中都给出 id、证据片段，便于人工裁决。
绝不修改译文：裁决权在人。
"""
import argparse
import json
import re
from collections import Counter
from pathlib import Path

# ---- R1.3 相关性 vs 因果 ----
ASSOC_EN = re.compile(
    r"\b(associated with|correlated with|correlation|is related to|relationship between"
    r"|linked to|predict(s|ed)?|association)\b", re.I)
CAUSAL_EN = re.compile(
    r"\b(cause[sd]?|causal|lead(s|ing)? to|result(s|ing)? in|effect of|impact of"
    r"|influence[sd]?|increase[sd]?|reduce[sd]?|determinant)\b", re.I)
# 「决定因素」「决定性因素」是 determinants 的名词短语，不是因果动词
CAUSAL_NOISE = re.compile(r"决定性?因素?|决定作用")
CAUSAL_ZH = ["导致", "引起", "造成", "致使", "使得", "决定", "必然", "一定会"]

# ---- R1.4 限定词 / 否定 / 比较 ----
HEDGE_EN = re.compile(r"\b(may|might|could|suggest(s|ed)?|appear(s|ed)?|likely|possibly"
                      r"|potentially|tend(s|ed)? to|seem(s|ed)?|plausib\w+|prima facie)\b", re.I)
HEDGE_ZH = ["不应", "不宜", "不可", "可能", "或许", "也许", "似乎", "提示", "倾向于", "有望", "未必", "表面上", "大致", "能否",
            "通常", "常常", "一般", "典型", "往往", "大体", "多半", "一定程度", "部分", "大约", "初步", "粗略", "建议", "显著性", "趋势", "容易", "易于", "普遍", "可", "能", "宜", "或许"]
# 这些不是否定：whether or not / Yes or No / yes-no
NEG_NOISE = re.compile(r"whether\s+or\s+not|\bYes\s+or\s+No\b|yes/no", re.I)
NEG_EN = re.compile(r"\b(not|no(?!\.)|never|none|neither|nor|without|fail(s|ed)? to|cannot"
                    r"|isn't|aren't|doesn't|don't|didn't|is not|are not)\b", re.I)
NEG_ZH = ["不", "无", "未", "没有", "并非", "缺乏", "无法", "不能", "未发现", "没有证据", "排除", "除外", "剔除",
          "以免", "避免", "不致", "不曾", "尚未", "免于", "无须", "不必", "拒绝", "否"]
COMP_EN = re.compile(r"\b(more|less|higher|lower|greater|smaller|stronger|weaker|larger|fewer"
                     r"|better|worse)\b\s+\w*\s*\bthan\b", re.I)
COMP_ZH = ["高于", "低于", "强于", "弱于", "大于", "小于", "多于", "少于", "较", "相比", "更"]

# ---- R2.2 概念区分 ----
CONCEPTS = [
    (re.compile(r"\bobesity\b", re.I), "肥胖", "超重"),
    (re.compile(r"\boverweight\b", re.I), "超重", "肥胖"),
    (re.compile(r"\bbody\s?weight\b", re.I), "体重", None),
    (re.compile(r"\bwaist circumference\b", re.I), "腰围", None),
    (re.compile(r"\bBMI\b"), "BMI", None),
]

# ---- R2.3 / R2.4 计量术语 ----
TERMS = [
    ("correlated effects", ["关联效应"]),
    ("contextual effects", ["情境效应", "环境效应"]),
    ("reflection problem", ["反射问题"]),
    ("fixed effects", ["固定效应"]),
    ("random effects", ["随机效应"]),
    ("marginal effects", ["边际效应"]),
    ("quantile regression", ["分位数回归"]),
    ("instrumental variable", ["工具变量"]),
    ("translog", ["超越对数"]),
    ("identification strategy", ["识别策略"]),
    ("robust standard errors", ["稳健标准误"]),
    ("ordered probit", ["有序 probit", "有序probit"]),
    ("endogeneity", ["内生性"]),
    ("dummy variable", ["虚拟变量"]),
    ("confidence interval", ["置信区间"]),
]

TERM_END = re.compile(r"[.!?:;。！？：；）\"”’]\s*$")
SUP_RE = re.compile(r"\^\{([^}]*)\}")
TAB_EN = re.compile(r"\bTable\s+([A]?\d+)", re.I)
TAB_ZH = re.compile(r"表\s*([A]?\d+)")
FIG_EN = re.compile(r"\bFigure\s+([A]?\d+)", re.I)
FIG_ZH = re.compile(r"图\s*([A]?\d+)")


def hits(patterns, text):
    return [p for p in patterns if (p.search(text) if hasattr(p, "search") else p in text)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bilingual")
    ap.add_argument("--out", required=True)
    ap.add_argument("--augmented", default="", help="写出带 section 归属的增强版 JSON")
    ap.add_argument("--term-exceptions", default="", help="已备案的术语例外表（JSON 数组）")
    a = ap.parse_args()
    doc = json.loads(Path(a.bilingual).read_text(encoding="utf-8"))
    blocks = doc["blocks"]
    # 期望值以**文档术语表**为准（单一事实来源），硬编码清单只作兜底
    doc_terms = []
    for pair in (doc.get("glossary") or []):
        if isinstance(pair, (list, tuple)) and len(pair) == 2 and str(pair[0]).strip():
            doc_terms.append((str(pair[0]).strip(), [str(pair[1]).strip()]))

    # 已备案的术语例外表：术语表与环境不符时，允许经人工批准的替代译法
    exc = {}
    if a.term_exceptions and Path(a.term_exceptions).exists():
        for e in json.loads(Path(a.term_exceptions).read_text(encoding="utf-8")):
            exc[e["term"].lower()] = e["approved_zh"]
    findings = []

    def add(rule, level, bid, msg, ev=""):
        findings.append({"rule": rule, "level": level, "id": bid, "msg": msg, "evidence": ev[:220]})

    # R3.2 章节归属 + R5.1 覆盖率
    section = None
    no_section = 0
    for i, b in enumerate(blocks):
        if b.get("kind") == "heading":
            section = (b.get("zh") or b.get("en") or "").strip()[:60]
        b["section"] = section or "（正文/摘要前）"
        if not section:
            no_section += 1
        if b.get("no_translate"):
            continue
        en = b.get("en") or b.get("caption_en") or ""
        zh = b.get("zh") or b.get("caption_zh") or ""
        if en.strip() and not zh.strip():
            add("R5.1", "FAIL", b["id"], "有原文但无译文（覆盖率缺口）", en[:120])
        if not b.get("pages"):
            add("R3.2", "FAIL", b["id"], "缺原文页码", en[:80])
        if b.get("kind") == "para" and en:
            core = re.sub(r"\^\{[^}]*\}\s*$", "", en.rstrip())
            non_sentence = (not re.search(r"[.!?]", core) and (
                "@" in core or re.search(r"\b(University|Institute|College|Department|E-mail"
                                         r"|Keywords|JEL)\b", core) or re.search(r"\d=\w+", core)))
            follows_equation = (i + 1 < len(blocks) and blocks[i + 1].get("kind") == "equation")
            if not TERM_END.search(core) and not non_sentence and not follows_equation:
                add("R3.1", "WARN", b["id"], "段落不以终止标点结尾（可能截断）", core[-90:])
        for k in ("id",):
            if not b.get(k):
                add("R3.2", "FAIL", "?", "块缺少页码 ID")

    # 逐块规则检查
    for b in blocks:
        if b.get("no_translate"):
            continue
        en = b.get("en") or b.get("caption_en") or ""
        zh = b.get("zh") or b.get("caption_zh") or ""
        if not en.strip() or not zh.strip():
            continue
        bid = b["id"]

        # R1.3
        if ASSOC_EN.search(en) and not CAUSAL_EN.search(en):
            zh_c = CAUSAL_NOISE.sub(" ", zh)
            bad = [w for w in CAUSAL_ZH if w in zh_c]
            if bad:
                add("R1.3", "FAIL", bid, f"原文为相关/预测表述，译文出现因果词 {bad}",
                    f"EN: {en[:110]} || ZH: {zh[:110]}")

        # R1.4 限定词
        # "could not / may not / might not" 是否定，不是限定；不摘出去会被当成"限定词丢失"
        en_hedge = re.sub(r"\b(could|may|might|can|would|will)\s+not\b", " ", en)
        if HEDGE_EN.search(en_hedge) and not any(h in zh for h in HEDGE_ZH):
            add("R1.4", "FAIL", bid, "原文有限定/不确定表述，译文未见对应限定词",
                f"EN: {en[:110]} || ZH: {zh[:110]}")
        # R1.4 否定
        en_neg = NEG_NOISE.sub(' ', en)
        if NEG_EN.search(en_neg) and not any(n in zh for n in NEG_ZH):
            add("R1.4", "FAIL", bid, "原文有否定，译文未见否定",
                f"EN: {en[:110]} || ZH: {zh[:110]}")
        # R1.4 比较
        if COMP_EN.search(en) and not any(c in zh for c in COMP_ZH):
            add("R1.4", "WARN", bid, "原文有比较级，译文未见比较表述",
                f"EN: {en[:110]} || ZH: {zh[:100]}")

        # R2.2 概念区分
        for rx, want, wrong in CONCEPTS:
            if rx.search(en) and want not in zh:
                add("R2.2", "WARN", bid, f"原文含「{want}」相关概念，译文未出现「{want}」",
                    f"ZH: {zh[:110]}")
            if wrong and rx.search(en) and wrong in zh and want not in zh:
                add("R2.2", "FAIL", bid, f"概念疑似混用：应为「{want}」，译文出现「{wrong}」",
                    f"ZH: {zh[:110]}")

        # R2.3/2.4 术语
        for term, wants in (doc_terms or TERMS):
            wants = exc.get(term.lower(), wants)
            # 接受简称（首次出现给全称、其余用简称是学术常规）与"原文保留英文"两种正当写法
            cand = []
            for w in wants:
                cand.append(w)
                short = re.sub(r"[（(][^）)]*[）)]", "", w).strip()
                if short and short not in cand:
                    cand.append(short)
            cand.append(term)          # 故意保留英文原文的情况
            if re.search(r"\b" + re.escape(term) + r"\b", en, re.I) and not any(w in zh for w in cand):
                add("R2.3", "WARN", bid, f"计量术语「{term}」未用约定译法 {wants}",
                    f"ZH: {zh[:110]}")

        # R4.6 脚注上标 / 表图编号
        en_sup, zh_sup = Counter(SUP_RE.findall(en)), Counter(SUP_RE.findall(zh))
        lost = [k for k in en_sup if en_sup[k] > zh_sup.get(k, 0)]
        if lost:
            add("R4.6", "WARN", bid, f"原文上标标记在译文中缺失或数量减少 {lost[:5]}",
                f"EN sup={dict(en_sup)} ZH sup={dict(zh_sup)}")
        et, zt = set(TAB_EN.findall(en)), set(TAB_ZH.findall(zh))
        if et - zt:
            add("R4.6", "WARN", bid, f"表号缺失 {sorted(et - zt)}")
        ef, zf = set(FIG_EN.findall(en)), set(FIG_ZH.findall(zh))
        if ef - zf:
            add("R4.6", "WARN", bid, f"图号缺失 {sorted(ef - zf)}")

    # 汇总输出
    by_rule = Counter((f["rule"], f["level"]) for f in findings)
    print(f"=== rules_check: {a.bilingual}（{len(blocks)} 块）===")
    for (rule, level), n in sorted(by_rule.items()):
        print(f"  {rule:<6} {level:<5} {n}")
    for f in findings:
        if f["level"] == "FAIL":
            print(f"\n[FAIL {f['rule']}] {f['id']} {f['msg']}")
            if f["evidence"]:
                print(f"   {f['evidence']}")
    print(f"\n合计 FAIL {sum(1 for f in findings if f['level']=='FAIL')} / "
          f"WARN {sum(1 for f in findings if f['level']=='WARN')} | 无章节归属块 {no_section}")

    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps({
        "source": a.bilingual, "blocks": len(blocks),
        "by_rule": {f"{k[0]}/{k[1]}": v for k, v in by_rule.items()},
        "findings": findings, "no_section": no_section,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    if a.augmented:
        Path(a.augmented).write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"增强版（带 section 归属）> {a.augmented}")
    print(f"报告 -> {a.out}")


if __name__ == "__main__":
    main()

