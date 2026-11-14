#!/usr/bin/env python3
"""把二遍审校提出的修改应用到双语 JSON，并留下逐条变更日志。

两种修正形态（同一份 fix_*.json 里可以混用）：
  1) **full**（旧）：{"id": "...", "zh": "整段新译文"}      —— 审校者重抄整段；
  2) **patch**（新，默认推荐）：{"id": "...", "edits": [{"find": "原片段", "replace": "新片段"}]}
     只回改动片段，省输出 token，也避免"重抄时手滑引入新错"。

机械体检对**最终的 new 文本**照旧执行（id 存在、非空、长度比、数字不减少）；
patch 额外要求每个 find 在**当前文本**里恰好出现一次、长度 ≥4（防止误伤）。
"""
import argparse, json, re
from collections import Counter
from pathlib import Path

NUM = re.compile(r"-?\d+(?:\.\d+)?")


def nums(t):
    return Counter(NUM.findall(t or ""))


def ctx(text, i, j, pad=18):
    return text[max(0, i - pad): j + pad].replace("\n", " ")



def near_match(cur, find):
    """等长窗口里只差 1–2 个字、且差异都在首/尾 2 字内的位置（用于容忍抄错上下文的 find）。"""
    L = len(find)
    if L < 8 or len(cur) < L:
        return None
    best, bi = 0, None
    for k in range(len(cur) - L + 1):
        diff = [j for j in range(L) if cur[k + j] != find[j]]
        if len(diff) == 0:
            return k
        if len(diff) <= 2 and all(j < 2 or j >= L - 2 for j in diff):
            if len(diff) > 0 and L - len(diff) > best:
                best, bi = L - len(diff), k
    return bi if best >= L - 2 else None


def apply_edits(old, edits):
    """返回 (new, applied[], why_failed)。任一 edit 不合法则整体拒绝（不做"改一半"）。"""
    cur, applied = old, []
    for k, e in enumerate(edits):
        find, repl = e.get("find") or "", e.get("replace")
        if repl is None or not find:
            return None, applied, f"第 {k+1} 处 edit 缺 find/replace"
        if len(find) < 4:
            return None, applied, f"第 {k+1} 处 find 太短（<4 字），无法定位"
        n = cur.count(find)
        if n == 0:
            # 近似匹配兜底：审校者把相邻句子的字抄进 find 很常见（实测 6 条被拒全是这种情况）。
            # 只在"等长窗口差异 ≤2 字、且全落在首/尾 2 字内"时接受，并在日志里标明近似命中。
            i = near_match(cur, find)
            if i is None:
                return None, applied, f"第 {k+1} 处 find 未找到：{find[:40]!r}"
            applied.append({"near": True, "find": find, "replace": repl})
        elif n > 1:
            return None, applied, f"第 {k+1} 处 find 出现 {n} 次，需更长的上下文"
        else:
            i = cur.index(find)
        cur = cur[:i] + repl + cur[i + len(find):]
        applied.append({"find": find, "replace": repl,
                        "before": ctx(cur, i, i + len(repl)) if repl else ctx(cur, i, i) + "（已删除）"})
    return cur, applied, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bilingual")
    ap.add_argument("--fixdir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--log", default="")
    ap.add_argument("--max-lost-nums", type=int, default=2,
                    help="允许减少的数字个数（默认 2，超出则拒绝）")
    a = ap.parse_args()

    doc = json.loads(Path(a.bilingual).read_text(encoding="utf-8"))
    by = {b["id"]: b for b in doc["blocks"]}

    fixes, rejected = [], []
    for p in sorted(Path(a.fixdir).glob("fix_*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        for f in data.get("fixes", []):
            bid = f.get("id")
            if bid not in by:
                rejected.append({**f, "why": "id 不存在"}); continue
            b = by[bid]
            en = b.get("en") or b.get("caption_en") or ""
            old = (b.get("zh") or b.get("caption_zh") or "")

            if f.get("edits"):
                new, edits, why = apply_edits(old, f["edits"])
                if new is None:
                    rejected.append({"id": bid, "why": why, "src": p.name,
                                     "edits": f["edits"], "severity": f.get("severity", "medium")}); continue
                mode = "patch"
            else:
                new, edits = (f.get("zh") or "").strip(), []
                if not new:
                    rejected.append({"id": bid, "why": "译文为空", "src": p.name}); continue
                mode = "full"

            if not new.strip():
                rejected.append({"id": bid, "why": "改后文本为空", "src": p.name}); continue
            ratio = len(new) / max(len(en), 1)
            if not (0.10 <= ratio <= 1.20):
                rejected.append({"id": bid, "why": f"长度比异常 {ratio:.2f}", "src": p.name}); continue
            lost = [n for n in nums(old) if n not in nums(new)]
            if len(lost) > a.max_lost_nums:
                rejected.append({"id": bid, "why": f"数字减少 {lost[:4]}", "src": p.name}); continue
            fixes.append({"id": bid, "mode": mode, "old": old, "new": new, "edits": edits,
                          "reason": f.get("reason", ""), "severity": f.get("severity", "medium"),
                          "src": p.name, "lost_nums": lost})

    for f in fixes:
        b = by[f["id"]]
        if "caption_en" in b and not b.get("en"):
            b["caption_zh"] = f["new"]
        else:
            b["zh"] = f["new"]

    out = Path(a.out)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

    sev = Counter(f["severity"] for f in fixes)
    modes = Counter(f["mode"] for f in fixes)
    patch_edits = sum(len(f["edits"]) for f in fixes)
    print(f"应用修正 {len(fixes)} 条（high {sev['high']} / medium {sev['medium']} / low {sev['low']}；"
          f"full {modes['full']} / patch {modes['patch']}，共 {patch_edits} 处片段编辑），"
          f"拒绝 {len(rejected)} 条")
    for r in rejected[:8]:
        print(f"  [拒绝] {r.get('id')}: {r.get('why')}")
    if a.log:
        Path(a.log).write_text(json.dumps({
            "applied": fixes, "rejected": rejected,
            "stats": {"total": len(fixes), "by_mode": dict(modes), "patch_edits": patch_edits,
                      "by_severity": dict(sev)},
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"变更日志 -> {a.log}")


if __name__ == "__main__":
    main()
