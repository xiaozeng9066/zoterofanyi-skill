#!/usr/bin/env python3
"""按《科研论文翻译与校对质量控制规则》对定稿做最后一轮修正（v4）。

处理三类问题（每类都留日志）：
  A. R3.1 不得随意截断句子 —— 跨页被切断的段落合并（英文与中文分别拼接）
  B. R1.4 不得提高原文确定性 —— suggests 类表述不得译作"表明/证实"
  C. 上标标记内的空格等格式噪声
以及一处脚注标记错位（R4.6）的定位修正。
"""
import argparse, json, re
from pathlib import Path

# A. 实测确认的跨页续接对（前块尾 + 后块头 = 完整句子）
MERGES = [("p6-3", "p7-0"), ("p9-2", "p10-0"), ("p13-4", "p14-0"),
          ("p16-5", "p17-0"), ("p26-6", "p27-0"), ("p28-3", "p29-0")]

# B. 限定词：suggests → 提示（不提高确定性）
HEDGE_FIXES = [("p6-3", "调查的分析表明，超重朋友所占比例",
                       "调查的分析提示，超重朋友所占比例")]

# C. 标记内空格
MARK_RE = re.compile(r"\^\{([^}]*)\}")

def join_zh(a, b):
    a, b = (a or "").rstrip(), (b or "").lstrip()
    if not a or not b:
        return (a + b).strip()
    cjk = lambda ch: "\u4e00" <= ch <= "\u9fff"
    return a + ("" if cjk(a[-1]) and cjk(b[0]) else " ") + b


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bilingual"); ap.add_argument("--out", required=True)
    ap.add_argument("--log", default="")
    a = ap.parse_args()
    doc = json.loads(Path(a.bilingual).read_text(encoding="utf-8"))
    by = {b["id"]: b for b in doc["blocks"]}
    log = {"merged": [], "hedge": [], "markers": 0, "footnote_moved": []}

    # A
    for pid, nid in MERGES:
        if pid not in by or nid not in by:
            log["merged"].append({"pair": f"{pid}+{nid}", "result": "缺块"}); continue
        p, n = by[pid], by[nid]
        p["en"] = (p.get("en") or "").rstrip() + " " + (n.get("en") or "").lstrip()
        p["zh"] = join_zh(p.get("zh"), n.get("zh"))
        p["pages"] = sorted(set((p.get("pages") or []) + (n.get("pages") or [])))
        p["continued"] = True
        p["merged_from"] = nid
        n["_dropped"] = True
        log["merged"].append({"pair": f"{pid}+{nid}", "result": "已合并",
                              "pages": p["pages"]})

    # 注入式脚注标记错位：句首裸数字 → 移到小句末并转为上标
    p = by.get("p10-0")
    if p:
        before_en, before_zh = p["en"], p["zh"]
        p["en"] = p["en"].replace("finds no 3 correlation between adolescents’ BMI and that of their friends.",
                                  "finds no correlation between adolescents’ BMI and that of their friends.^{3}")
        p["zh"] = p["zh"].replace("之间的^{3}相关。", "之间的相关^{3}。")
        log["footnote_moved"].append({"id": "p10-0",
                                      "en_changed": p["en"] != before_en,
                                      "zh_changed": p["zh"] != before_zh})

    # B
    for bid, old, new in HEDGE_FIXES:
        b = by.get(bid)
        if b and old in (b.get("zh") or ""):
            b["zh"] = b["zh"].replace(old, new)
            log["hedge"].append({"id": bid, "old": old, "new": new})
        else:
            log["hedge"].append({"id": bid, "result": "未命中"})

    # C 标记内空格
    n_mark = 0
    for b in doc["blocks"]:
        for k in ("en", "zh", "caption_en", "caption_zh"):
            v = b.get(k)
            if not v:
                continue
            nv = MARK_RE.sub(lambda m: "^{" + m.group(1).strip() + "}", v)
            if nv != v:
                b[k] = nv; n_mark += 1
    log["markers"] = n_mark

    doc["blocks"] = [b for b in doc["blocks"] if not b.get("_dropped")]
    for b in doc["blocks"]:
        b.pop("_dropped", None)
    Path(a.out).write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"合并 {sum(1 for m in log['merged'] if m.get('result')=='已合并')} 对跨页段落 | "
          f"限定词修正 {len([h for h in log['hedge'] if 'old' in h])} 处 | 标记规范化 {n_mark} 处 | "
          f"块数 → {len(doc['blocks'])}")
    if a.log:
        Path(a.log).write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"日志 -> {a.log}")


if __name__ == "__main__":
    main()
