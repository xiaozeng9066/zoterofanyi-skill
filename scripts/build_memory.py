#!/usr/bin/env python3
"""build_memory.py — 翻译记忆回流（zoterofanyi 迭代闭环，规则 54）

从各篇 reports/changes*.json 的审校修正里，用字符级 diff 抽出：
  tm/term_fixes.json      词级「错译 → 正译」（翻译前注入子代理提示词）
  tm/error_patterns.json  整句级错误模式（1–4 条作为范例注入提示词）

**增量合并**：按 (wrong, right) 去重，保留历史条目；重复运行不会清空也不会膨胀。
收编自工作区 _generic/build_memory2.py（原版路径写死 + json.dump 覆盖写，重跑会丢历史）。

用法：
  build_memory.py --changes "<W>/reports/changes*.json" --tm "<K>/tm"
  build_memory.py --changes "*/reports/changes*.json" --tm "<K>/tm" --dry-run
"""
import argparse
import difflib
import glob
import json
import re
from pathlib import Path

NUM = re.compile(r"\d")
CJK = re.compile(r"[\u4e00-\u9fff]")


def jload(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return []


def collect_files(patterns):
    seen, out = set(), []
    for pat in patterns:
        hits = sorted(glob.glob(pat, recursive=True)) or ([pat] if Path(pat).is_file() else [])
        for h in hits:
            if h not in seen:
                seen.add(h)
                out.append(h)
    return out


def iter_edits(files):
    """产出统一的修正记录；兼容 patch(edits) 与 full(old/new) 两种 changes 形态。"""
    for f in files:
        doc = jload(f)
        if not isinstance(doc, dict):
            continue
        paper = Path(f).parent.parent.name or Path(f).stem
        for a in doc.get("applied", []):
            if not isinstance(a, dict):
                continue
            reason = a.get("reason", "") or ""
            item = a.get("id", "") or ""
            severity = a.get("severity", "") or ""
            edits = a.get("edits") or []
            if edits:
                for ed in edits:
                    if not isinstance(ed, dict):
                        continue
                    find = ed.get("find", "") or ""
                    repl = ed.get("replace", "") or ""
                    if not find or find == repl:
                        continue
                    yield dict(find=find, replace=repl,
                               reason=ed.get("reason", "") or reason,
                               item=item, src=paper, severity=severity)
            else:
                find, repl = a.get("old", "") or "", a.get("new", "") or ""
                if find and repl and find != repl:
                    yield dict(find=find, replace=repl, reason=reason,
                               item=item, src=paper, severity=severity)


def classify(rec):
    """返回 ('term', [(wrong,right,ctx_before)...]) 或 ('pattern', rec) 或 (None, None)。"""
    f, r = rec["find"], rec["replace"]
    sm = difflib.SequenceMatcher(None, f, r, autojunk=False)
    spans = [(i1, i2, j1, j2) for tag, i1, i2, j1, j2 in sm.get_opcodes()
             if tag in ("replace", "insert", "delete")]
    changed_in = sum(i2 - i1 for i1, i2, _, _ in spans)
    changed_out = sum(j2 - j1 for _, _, j1, j2 in spans)
    coverage = 1 - max(changed_in, changed_out) / max(len(f), len(r), 1)

    word_level = (spans and len(spans) <= 2
                  and all(i2 - i1 <= 10 and j2 - j1 <= 10 for i1, i2, j1, j2 in spans)
                  and coverage >= 0.55
                  and NUM.findall(f) == NUM.findall(r))
    if word_level:
        pairs = []
        for i1, i2, j1, j2 in spans:
            wrong, right = f[i1:i2], r[j1:j2]
            if not wrong or not right or wrong == right:
                continue
            if not (CJK.search(wrong) or CJK.search(right)):
                continue
            pairs.append((wrong, right, f[max(0, i1 - 12):i1]))
        if pairs:
            return "term", pairs
    if len(f) >= 60:
        return "pattern", rec
    return None, None


def load_existing(tm):
    terms = jload(tm / "term_fixes.json")
    if not terms:  # 兼容 v1 的 find/replace 格式
        terms = [{"wrong": x.get("find", ""), "right": x.get("replace", ""),
                  "ctx_before": "", "item": x.get("item", ""), "src": x.get("src", "")}
                 for x in jload(tm / "term_fixes.jsonl")
                 if isinstance(x, dict) and x.get("find") and x.get("replace")]
    pats = jload(tm / "error_patterns.json")
    if not pats:
        pats = [{"wrong": x.get("find", ""), "right": x.get("replace", ""),
                 "reason": x.get("reason", ""), "item": x.get("item", ""), "src": x.get("src", "")}
                for x in jload(tm / "error_patterns.jsonl")
                if isinstance(x, dict) and x.get("find") and x.get("replace")]
    return [x for x in terms if isinstance(x, dict)], [x for x in pats if isinstance(x, dict)]


def main():
    ap = argparse.ArgumentParser(description="从审校修正增量回流翻译记忆")
    ap.add_argument("--changes", action="append", required=True,
                    help="changes*.json 路径或 glob，可重复传入")
    ap.add_argument("--tm", default=str(Path(__file__).resolve().parent.parent / "tm"),
                    help="翻译记忆目录（默认 skill 根下的 tm/）")
    ap.add_argument("--max-patterns", type=int, default=40)
    ap.add_argument("--dry-run", action="store_true", help="只统计，不写文件")
    a = ap.parse_args()

    tm = Path(a.tm)
    tm.mkdir(parents=True, exist_ok=True)
    files = collect_files(a.changes)
    if not files:
        raise SystemExit("没找到任何 changes*.json，检查 --changes 路径")

    terms, pats = load_existing(tm)
    tkeys = {(x.get("wrong", ""), x.get("right", "")) for x in terms}
    pkeys = {(x.get("wrong", "")[:50], x.get("right", "")[:50]) for x in pats}
    n_term = n_pat = n_edit = 0

    for rec in iter_edits(files):
        n_edit += 1
        kind, payload = classify(rec)
        if kind == "term":
            for wrong, right, ctx in payload:
                k = (wrong, right)
                if k in tkeys:
                    continue
                tkeys.add(k)
                terms.append({"wrong": wrong, "right": right, "ctx_before": ctx,
                              "item": rec["item"], "src": rec["src"]})
                n_term += 1
        elif kind == "pattern":
            k = (rec["find"][:50], rec["replace"][:50])
            if k in pkeys:
                continue
            pkeys.add(k)
            pats.append({"wrong": rec["find"], "right": rec["replace"],
                         "reason": rec["reason"], "item": rec["item"], "src": rec["src"]})
            n_pat += 1

    kept_pats = pats[:a.max_patterns]
    print("输入文件 %d 个，审校修正 %d 条" % (len(files), n_edit))
    print("新增词级纠正 %d 条（累计 %d）；新增整句模式 %d 条（保留 %d）" %
          (n_term, len(terms), n_pat, len(kept_pats)))
    if a.dry_run:
        print("dry-run：未写入")
        return

    def dump(name, data):
        (tm / name).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    dump("term_fixes.json", terms)
    dump("error_patterns.json", kept_pats)
    print("已写入：%s / %s" % (tm / "term_fixes.json", tm / "error_patterns.json"))


if __name__ == "__main__":
    main()
