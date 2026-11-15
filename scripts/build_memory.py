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
# 虚词/功能字（规则 83）：出现在片段边界即说明这是跨词切片，不是术语
FUNC_EDGE = set("之的着了是在和与或由对为被把而则也就都更最不很该其此所以及并且但若如从向于")


def injectable(w, r):
    """一条词级纠正能否作为「必须遵守」的替换提示注入译前指南（规则 83）。

    字符级 diff 抽出来的 pair 有两类：
      - 真术语替换（「虚拟 → 哑」「峭倾斜 → 陡降」），可安全注入；
      - 跨词切片（「由 → 以」「分类 → 项」「间 → 下二者」），一旦注入就等于
        给原文下了一条全局替换指令，会把正常句子改成病句。
    后者只留档供人回看，不注入。
    """
    w, r = w or "", r or ""
    if not w or not r:
        return False, "空条目"
    if len(w) < 2 or len(r) < 2:
        return False, "任一侧仅 1 字，属句内片段而非术语"
    if not CJK.search(r):
        return False, "右侧无中文，不是译法"
    if not CJK.search(w) and not (w.isascii() and len(w) >= 3):
        return False, "左侧过短或非词"
    if w[0] in FUNC_EDGE or w[-1] in FUNC_EDGE:
        return False, "左侧边界是虚词，属跨词片段"
    if r[0] in FUNC_EDGE or r[-1] in FUNC_EDGE:
        return False, "右侧边界是虚词，属跨词片段"
    if NUM.search(w) or NUM.search(r):
        return False, "含数字，随句变化不可当术语"
    return True, ""


def mark(terms):
    """给每条打上 inject/why（就地），并对「同一个 wrong 指向多个 right」判为冲突。"""
    confl = {}
    for x in terms:
        confl.setdefault(x.get("wrong", ""), set()).add(x.get("right", ""))
    bad = {k for k, v in confl.items() if len(v) > 1}
    for x in terms:
        ok, why = injectable(x.get("wrong"), x.get("right"))
        if ok and x.get("wrong", "") in bad:
            ok, why = False, "同一个原文片段有多种改法，互相冲突"
        x["inject"] = bool(ok)
        if ok:
            x.pop("why", None)
        else:
            x["why"] = why
    return terms


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
    ap.add_argument("--report", default="", help="把本次回流判定（注入/跳过）写一份 JSON，供回归用例比对")
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
    n_term = n_pat = n_edit = n_skip = 0
    skipped, injected = [], []

    for rec in iter_edits(files):
        n_edit += 1
        kind, payload = classify(rec)
        if kind == "term":
            for wrong, right, ctx in payload:
                k = (wrong, right)
                if k in tkeys:
                    continue
                ok, why = injectable(wrong, right)
                if not ok:
                    # 跨词切片不入库：完整句子与理由已在该篇 reports/changes.json 里留档
                    n_skip += 1
                    skipped.append([wrong, right, why])
                    if n_skip <= 8:
                        print("  [跳过] %r -> %r ｜ %s" % (wrong, right, why))
                    continue
                tkeys.add(k)
                terms.append({"wrong": wrong, "right": right, "ctx_before": ctx,
                              "item": rec["item"], "src": rec["src"], "inject": True})
                injected.append([wrong, right])
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
    terms = mark(terms)
    n_inj = sum(1 for x in terms if x.get("inject"))
    print("输入文件 %d 个，审校修正 %d 条" % (len(files), n_edit))
    print("新增词级纠正 %d 条（累计 %d，其中可注入 %d / 历史仅留档 %d）；跳过跨词切片 %d 条；新增整句模式 %d 条（保留 %d）" %
          (n_term, len(terms), n_inj, len(terms) - n_inj, n_skip, n_pat, len(kept_pats)))
    blocked = [(x.get("wrong"), x.get("right"), x.get("why")) for x in terms if not x.get("inject")]
    if blocked:
        print("仅留档（不进译前指南）示例，共 %d 条：" % len(blocked))
        for w, r, why in blocked[-8:]:
            print("  - %r -> %r ｜ %s" % (w, r, why))
    if a.dry_run:
        print("dry-run：未写入")
        return

    if a.report:
        Path(a.report).write_text(json.dumps(
            {"edits": n_edit, "injected": injected, "skipped": skipped,
             "term_fixes_total": len(terms), "term_fixes_inject": n_inj},
            ensure_ascii=False, indent=1), encoding="utf-8")
        print("判定报告：%s" % a.report)

    def dump(name, data):
        (tm / name).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    dump("term_fixes.json", terms)
    dump("error_patterns.json", kept_pats)
    print("已写入：%s / %s" % (tm / "term_fixes.json", tm / "error_patterns.json"))


if __name__ == "__main__":
    main()
