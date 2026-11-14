#!/usr/bin/env python3
"""文本卫生：把"抽取残留"在**翻译前**从骨架上洗掉（省得译完了再回头改）。

处理 5 类（全部是实测踩过的，且都与语义无关）：
  1. 私用区字符（U+F0xx，原文上标 ∗ 之类）→ `*`，否则渲染成豆腐块；
  2. 句末标点后孤立的单字母（`… (NIHL).i`、`…clusters.f`）→ 删；
  3. URL 内部被换行切断的空格（`brc.nih r.ac.uk`、`http://www. baaudiology.org`、`GMM_ RNENT`）→ 合并；
  4. 零宽字符 U+200B/U+200C → 删（污染复制与检索）；
  5. markdown 斜体残留 `*Databases*` → 去星号（原文是斜体，PDF 抽取成了星号）。

跑在 assemble/mark_sections/apply_meta 之后、emit 分块之前；也接受 bilingual JSON（幂等）。
用法：sanitize.py work/skeleton_fn.json [--out 另存] [--report reports/sanitize.json]
"""
import argparse
import json
import re
import sys
from pathlib import Path

PUA = re.compile(r"[\uf000-\uf0ff]")
TAIL = re.compile(r"([.!?])\s*[a-z]$")
URL_HOST = re.compile(r"(https?://\S+) (?=(?:[\w-]+\.)+(?:com|org|uk|cn|net|edu|gov)\b)")
ITALIC = re.compile(r"\*([A-Za-z][^*]{0,40})\*")
FIELDS = ("en", "zh", "caption_en", "caption_zh", "note_en", "note_zh")



MATH_GLUE = None  # 延迟编译（见下）


# 括号内散落的上标碎片："tau_{ik} (_{1k2}^{nu}^{)}" -> "tau_{ik1k2}^{(nu)}"
# 说明：这段修复**不用正则**。本机 Python 3.10 的 sre 对"\^{...}}^{...})"这类
# 尾随序列会把 ')'(0x29) 误判为分组闭合，反复试错价值不大；用纯字符串扫描更可靠。
LP, RP, LB, RB, HAT = chr(40), chr(41), chr(123), chr(125), chr(94)
OPEN = LP + HAT + LB      # (^{
CLOSE = RP + RB           # )}


def paren_sup(t, stats=None):
    """清掉"空上标"碎片：^{} 一律删除（本类 arXiv PDF 的公式抽取里大量出现）。"""
    HAT, LB, RB = chr(94), chr(123), chr(125)
    VOID = HAT + LB + RB
    hit = t.count(VOID)
    if hit:
        t = t.replace(VOID, '')
    if stats is not None and hit:
        stats['空上标清理'] = stats.get('空上标清理', 0) + hit
    return t


def tidy_math(t, stats):
    """本类 arXiv PDF 的数学标记在原生文本层里被拆开：
       Θˆ^{(}^{ν}^{)}      → Θ̂^{(ν)}
       ψ_{k}_{1}          → ψ_{k1}
       Y_{i}^{⊤}_{1}      → Y_{i,1}^{⊤}
       处理原则：**只合并相邻的碎片，不改变符号本身**，便于人读与翻译；公式图仍是原图保真。
    """
    global MATH_GLUE
    if MATH_GLUE is None:
        MATH_GLUE = [
            (re.compile(r"\^\{\((\w+)\)\}"), r"^{(\1)}"),          # ^{(}_{ν}^{)} → ^{（ν）}
            (re.compile(r"\^\{\}\^\{"), "^{"),                      # 空上标
            (re.compile(r"\^\{\}"), ""),
            (re.compile(r"_(\{[^{}]+\})_(\{[^{}]+\})"), r"_{\1\2}"),  # _{k}_{1} → _{k1}
            (re.compile(r"\^\{([^{}]*)\}_\{(\w+)\}"), r"_{\2}^{\1}"),  # ^{⊤}_{1} → _{1}^{⊤}
            # 括号内散落的上标回填：τ_{ik} (_{1k2}^{ν}^{)} → τ_{ik1k2}^{(ν)}
            # 合并后的嵌套下标归一：_{{i}{1}} → _{i1}，_{k_{1}} → _{k1}
            (re.compile(r"_\{\{([^{}]*)\}\{([^{}]*)\}\}"), r"_{\1\2}"),
            (re.compile(r"\{\{([^{}]*)\}\{([^{}]*)\}\}"), r"{\1\2}"),
            (re.compile(r"_\{([A-Za-z]*(?:[A-Za-z]|\d)_\{[^{}]*\})\}"), lambda m: "_" + m.group(1).replace("{", "").replace("}", "")),
        ]
    stats.setdefault("括号回填", 0)
    t = paren_sup(t, stats)
    out = t
    for _ in range(3):   # 迭代到不再变化（碎片可能嵌套）
        before = out
        for rx, rep in MATH_GLUE:
            out = rx.sub(rep, out)
        if out == before:
            break
    if out != t:
        stats["数学标记归并"] += 1
    return out


def clean(text, stats, math=True):
    t = text
    # 顺序很关键：**先清零宽字符**再合并 URL。本类 PDF 会把 URL 每个字符之间塞 U+200B，
    # 不先清掉，"https?://\\S+" 会把整串连零宽一起吞掉，后面的 lookahead 永远匹配不上。
    if "\u200b" in t or "\u200c" in t:
        stats["零宽字符"] += t.count("\u200b") + t.count("\u200c")
        t = t.replace("\u200b", "").replace("\u200c", "")
    if PUA.search(t):
        n = len(PUA.findall(t)); t = PUA.sub("*", t); stats["私用区字符"] += n
    if TAIL.search(t):
        t = t[:TAIL.search(t).start(1) + 1]; stats["段末孤立字母"] += 1
    for _ in range(3):
        new = URL_HOST.sub(r"\1", t).replace("://www. ", "://www.")
        if new == t:
            break
        stats["URL 断行"] += 1
        t = new
    if ITALIC.search(t):
        stats["斜体星号"] += len(ITALIC.findall(t)); t = ITALIC.sub(r"\1", t)
    if math:
        t = tidy_math(t, stats)
    return t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("doc")
    ap.add_argument("--out", default="")
    ap.add_argument("--report", default="")
    a = ap.parse_args()

    doc = json.loads(Path(a.doc).read_text(encoding="utf-8"))
    stats, touched = {}, []
    for k in ("私用区字符", "段末孤立字母", "URL 断行", "零宽字符", "斜体星号", "数学标记归并"):
        stats[k] = 0
    for b in doc["blocks"]:
        hit = False
        for f in FIELDS:
            v = b.get(f)
            if isinstance(v, str) and v:
                # 参考文献不翻译，也不做数学归并（保持原文逐字）
                nv = clean(v, stats, math=not b.get("no_translate"))
                if nv != v:
                    b[f] = nv
                    hit = True
        if hit:
            touched.append(b["id"])

    out = Path(a.out or a.doc)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    total = sum(stats.values())
    print(f"文本卫生：{'、'.join(f'{k} {v}' for k, v in stats.items() if v) or '无需清洗'}"
          f"（{len(touched)} 块）-> {out}")
    if a.report:
        Path(a.report).parent.mkdir(parents=True, exist_ok=True)
        Path(a.report).write_text(json.dumps({"stats": stats, "blocks": touched},
                                             ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
