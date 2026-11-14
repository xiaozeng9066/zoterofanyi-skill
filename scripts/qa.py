#!/usr/bin/env python3
"""阶段2c：译文机械保真度校验（二遍校对里"可自动化"的那一半）。

先划清能力边界：**本脚本不能判断译文好坏**（术语是否地道、句式是否学术）。
它能查的是"可证伪的机械错误"：
  1. 覆盖完整性：该译的都译了，没有静默丢段；
  2. 数字/统计符号是否原样保留（0.596***、(0.033)、6.1%、10–17.99 等）；
  3. 引用标记是否原样保留（(Cheng, 2012)、(Mang et al., 2011)、上标编号 15）；
  4. 是否漏译（中文段落里残留长英文串）；
  5. 长度比是否异常（中文/英文字符数应落在合理区间）；
  6. 术语表一致性：原文出现的术语，译文中是否用了约定的中文译名。

输出：reports/qa_<name>.json + 控制台摘要。任何 FAIL 项都需人工复核后才能交付。
"""
import argparse
import json
import re
from collections import Counter
from pathlib import Path

NUM_RE = re.compile(r"-?\d+(?:\.\d+)?(?:\s*[%‰])?|\*{1,3}")
CITE_RE = re.compile(r"\([A-Z][A-Za-z&.\- ]+(?:et al\.?|and|&)?[,\s]*\d{4}[a-z]?\)"
                     r"|[A-Z][a-z]+ (?:and|&) [A-Z][a-z]+ \(\d{4}\)"
                     r"|\b[A-Z][a-z]+ et al\.? \(\d{4}\)")
ASCII_RUN_RE = re.compile(r"[A-Za-z][A-Za-z ,;:'\-()]{40,}")


PUNCT_MAP = str.maketrans({
    "（": "(", "）": ")", "，": ",", "、": ",", "：": ":", "；": ";",
    "．": ".", "％": "%", "－": "-",
    "　": " ", "“": '"', "”": '"', "’": "'",
})


def canon(text):
    """统一全角标点与区间连接符。

    必要性：中文译文里引用写成 （Cheng, 2012）、区间写成 6~17 岁，
    不统一就会把"正确的译文"误报成"引用丢失/数字丢失"（校验器第一版即如此）。
    """
    t = text.translate(PUNCT_MAP)
    # 先只把**数字之间**的连接符统一成 "-"（6~17 岁 / 0.39–0.80 / 2010—2011），
    # 其余位置的波浪号与破折号一律当分隔符，绝不当负号（"——16 所" 曾是 -16 的来源）。
    t = re.sub(r"(?<=\d)\s*[~～–—－]\s*(?=\d)", "-", t)
    t = t.translate(str.maketrans({"—": " ", "–": " ", "~": " ", "～": " "}))
    # 区间归一。关键：不能把散文里的 "from 6.1% in 1993 to 13.1% in 2009"
    # 误当成区间 "1993-13.1"（校验器第二版即犯此错），故要求 to 两侧都是纯整数。
    t = re.sub(r"(?<![\d.])(\d+)\s*-\s*to\s+(\d+)(?![\d.%])", r"\1-\2", t)   # 2- to 18
    t = re.sub(r"(?<![\d.%])(\d+)\s+to\s+(\d+)(?![\d.%])", r"\1-\2", t)      # 3 to 18
    t = re.sub(r"(?<![\d.])(\d+(?:\.\d+)?)\s*年?\s*(?:–|—|~|～|至|-)\s*(\d+(?:\.\d+)?)\s*年?(?![\d.%])",
               r"\1-\2", t)
    # 只在**数字与数字之间**去掉连字符两侧的空格。
    # 原来无条件 \s*-\s*→- 会把"—— 16 所"变成"-16"、"~ 30%"变成"-30%"，
    # 于是把正确的译文误报成"数字丢失"（2026-10 Williams/Jia 两篇实测）。
    t = re.sub(r"(?<=\d)\s*-\s*(?=\d)", "-", t)
    return t


RANGE_RE = re.compile(r"(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)")


def norm_nums(text):
    """数值比对：把区间展开成端点再比。

    原因：同一含义在英文与中文里常写成不同形态 ——
    英文 "10 and 18" / "0.16 to 0.30"，中文 "10 至 18 岁" / "0.16—0.30"。
    若把区间当成整体 token，就会把正确译文误判为"数字缺失"（校验器曾如此）。
    代价：不再校验区间结构本身，只校验数字是否都还在。
    """
    t = canon(text)
    counter = Counter()
    for m in RANGE_RE.finditer(t):
        counter[m.group(1)] += 1
        counter[m.group(2)] += 1
    t = RANGE_RE.sub(" ", t)
    for m in NUM_RE.finditer(t):
        tok = m.group(0).replace(" ", "")
        counter[tok] += 1
        # 同时登记去掉百分号的形式：英文常写 "0.39 percent"，中文写 "0.39%"，数值相同
        bare = tok.rstrip("%‰")
        if bare != tok:
            counter[bare] += 1
    return counter



# ---- 本地方案：中文计量/年代写法的等价判定（对应 SKILL 规则 23–24 的"尺子错"）----
MAG_ZH = {"亿": 1e8, "万": 1e4}
MAG_EN = {"billion": 1e9, "bn": 1e9, "million": 1e6, "mn": 1e6, "thousand": 1e3, "k": 1e3}
ZH_DIGIT = {"两": "2", "二": "2", "三": "3", "四": "4", "五": "5",
            "六": "6", "七": "7", "八": "8", "九": "9", "十": "10"}


def mag_values(text):
    """把"15亿 / 4.5亿 / 450 million / 1.5 billion"折算成同一量纲的数值集合。"""
    vals = set()
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(亿|万)", text):
        vals.add(round(float(m.group(1)) * MAG_ZH[m.group(2)], 3))
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(billion|bn|million|mn|thousand|k)\b", text, re.I):
        vals.add(round(float(m.group(1)) * MAG_EN[m.group(2).lower()], 3))
    return vals


def covered_by_equiv(num, en, zh):
    """判定一个"丢失"的数字是否其实用另一种中文写法出现了。"""
    tok = str(num)
    # 1) 数量级写法：1.5 billion ↔ 15亿
    try:
        val = float(tok)
    except ValueError:
        return False
    if val.is_integer() and 1900 <= val <= 2099 and re.search(re.escape(tok) + r"['\u2019]?s\b", en):
        # 年代：1970s / 1930's / 1930’s ↔ 20 世纪 70 年代
        yy = tok[2:]
        if re.search(r"(?:20|19)\s*世纪\s*" + yy + r"\s*年代", zh):
            return True
    # 数量级写法：先看这个数字后面是否紧跟 billion/million（英文侧），再折算比对
    m_mag = re.search(re.escape(tok) + r"\s*(billion|bn|million|mn|thousand|k)\b", en, re.I)
    if m_mag:
        scaled = val * MAG_EN[m_mag.group(1).lower()]
        for v in mag_values(zh):
            if abs(v - scaled) <= max(abs(scaled), 1) * 1e-6:
                return True
    # 2) 中文数词：the 2 models ↔ 两个模型
    if val.is_integer() and 2 <= val <= 10:
        for zh_ch, d in ZH_DIGIT.items():
            if d == str(int(val)) and zh_ch in zh:
                return True
    # 3) 章节/阶段编号：Phase 1 ↔ 第一阶段（中文正文里"一"字无处不在，不能靠 rule 2 放行）
    if val.is_integer() and 1 <= val <= 9:
        if re.search(r"\b(Phase|Step|Stage|Chapter|Part|Section|Appendix)\s+" + re.escape(tok) + r"\b",
                     en, re.I):
            cn = next((zh_ch for zh_ch, d in ZH_DIGIT.items() if d == str(int(val))), None)
            if int(val) == 1:
                cn = "一"
            if cn and re.search(r"第\s*" + cn + r"\s*(阶段|步|章|节|部分|期)", zh):
                return True
    return False


def check_block(b, glossary):
    issues = []
    if b.get("no_translate"):
        return issues
    en = canon(b.get("en") or b.get("caption_en") or "")
    zh = canon(b.get("zh") or b.get("caption_zh") or "")
    if not en.strip():
        return issues
    if not zh.strip():
        issues.append(("FAIL", "无译文"))
        return issues

    # 数字/统计量
    en_nums, zh_nums = norm_nums(en), norm_nums(zh)
    lost = [n for n in en_nums if n not in zh_nums and not covered_by_equiv(n, en, zh)]
    newly = [n for n in zh_nums if n not in en_nums]  # 新增数字仍只报 WARN
    if lost:
        issues.append(("FAIL", f"原文数字未在译文中原样出现: {lost[:8]}"))
    if newly:
        # 中文单位/编号可能合理新增，标为待查而非失败
        issues.append(("WARN", f"译文出现原文没有的数字（需人工确认是否合理新增）: {newly[:8]}"))

    # 引用：不比对完整字面（中译文用「和 / 等 /（）」是正当的），
    # 只要求「首作者姓 + 年份」在译文中相互邻近地出现。
    lost_cites = []
    for m in CITE_RE.finditer(en):
        cite = m.group(0)
        surname = re.match(r"[A-Za-z\-']+", cite)
        year = re.search(r"(\d{4})", cite)
        if not surname or not year:
            continue
        sn, yr = surname.group(0), year.group(1)
        ok = False
        start = 0
        while True:
            pos = zh.find(sn, start)
            if pos < 0:
                break
            window = zh[max(0, pos - 30): pos + len(sn) + 30]
            if yr in window:
                ok = True
                break
            start = pos + len(sn)
        if not ok:
            lost_cites.append(cite)
    if lost_cites:
        issues.append(("FAIL", f"引用标记疑似丢失/改写: {lost_cites[:5]}"))

    # 漏译残留
    for run in ASCII_RUN_RE.findall(zh)[:2]:
        issues.append(("WARN", f"译文里残留长英文串（可能漏译）: {run[:60]}…"))

    # 长度比
    ratio = len(zh) / max(len(en), 1)
    if ratio < 0.15 or ratio > 1.10:
        issues.append(("WARN", f"中文/英文字符长度比异常: {ratio:.2f}"))

    # 术语表
    for pair in glossary:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            continue
        term_en, term_zh = pair
        alts = []
        for a in re.split(r"[/｜|]", canon(term_zh)):
            a = a.strip()
            if not a:
                continue
            alts.append(a)
            # 「BMI（体质指数）」→ 也接受简称「BMI」：学术写作只在首次出现处给全称
            short = re.sub(r"[（(][^）)]*[）)]", "", a).strip()
            if short and short not in alts:
                alts.append(short)
        # 词边界：否则 "OLS" 会命中 "controls" 里的 ols（审计器与校验器都犯过这个错）
        if re.search(r"\b" + re.escape(canon(term_en)) + r"\b", en, re.I) \
                and not any(a in zh for a in alts):
            issues.append(("WARN", f"术语未按约定翻译: 「{term_en}」应为「{term_zh}」"))
    return issues


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bilingual")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    doc = json.loads(Path(args.bilingual).read_text(encoding="utf-8"))
    blocks = doc.get("blocks") or doc.get("items") or []
    glossary = doc.get("glossary") or []

    results, n_fail, n_warn = [], 0, 0
    for b in blocks:
        issues = check_block(b, glossary)
        n_fail += sum(1 for lvl, _ in issues if lvl == "FAIL")
        n_warn += sum(1 for lvl, _ in issues if lvl == "WARN")
        results.append({"id": b.get("id"), "kind": b.get("kind"), "issues": issues})

    total = len(blocks)
    print(f"=== QA: {args.bilingual} ===")
    print(f"块数 {total}｜FAIL {n_fail}｜WARN {n_warn}")
    for r in results:
        if r["issues"]:
            print(f"\n[{r['id']}] ({r['kind']})")
            for lvl, msg in r["issues"]:
                print(f"   {lvl:<4} {msg}")
    if not any(r["issues"] for r in results):
        print("机械校验全部通过（注意：这不等于译文质量合格，风格仍需人工/二遍审校）")

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps({
            "source": args.bilingual, "blocks": total,
            "fail": n_fail, "warn": n_warn, "results": results,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nwrote {args.out}")
    # 有 FAIL 时以非零退出，便于流水线卡口
    raise SystemExit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
