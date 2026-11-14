#!/usr/bin/env python3
"""黄金测试集回归：对每个案例重跑检查器，与 expected.json 比对。

护栏规则（写进 SKILL.md）：
  任何脚本改动之后必须跑本套件；**只要有一个案例由 PASS 变 FAIL，就不许合并这次改动。**

用法：
  regression_suite.py --fixtures <fixtures目录> [--pdf-dir <原文PDF所在目录>] [--verbose]
"""
import argparse, json, os, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).parent


def make_tmpdir(prefix: str) -> Path:
    """系统 temp 可能被 DSH 沙箱设为不可写；不可写时退回当前工作区。"""
    for base in (None, Path.cwd()):
        try:
            d = Path(tempfile.mkdtemp(prefix=prefix, dir=base))
            probe = d / ".write-test"
            probe.write_text("x", encoding="utf-8")
            probe.unlink()
            return d
        except OSError:
            continue
    raise SystemExit("找不到可写的临时目录（系统 temp 与当前目录都不可写）")


def run(script, *args):
    r = subprocess.run([sys.executable, str(HERE / script), *args],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.returncode, r.stdout + r.stderr


def check_full(case, tmp, pdf_dir, verbose):
    got, errs = {}, []
    bil = case / "bilingual.json"
    run("qa.py", str(bil), "--out", str(tmp / "qa.json"))
    q = json.loads((tmp / "qa.json").read_text(encoding="utf-8"))
    got["qa"] = {"fail": q.get("fail", 0)}

    run("rules_check.py", str(bil), "--out", str(tmp / "rules.json"))
    rj = json.loads((tmp / "rules.json").read_text(encoding="utf-8"))
    got["rules"] = {"fail": sum(1 for x in rj.get("findings", []) if x.get("level") == "FAIL")}

    run("audit_document.py", str(bil), "--out", str(tmp / "audit.json"))
    aj = json.loads((tmp / "audit.json").read_text(encoding="utf-8"))
    got["audit"] = {"fail": sum(1 for x in aj.get("findings", []) if x.get("级别") == "FAIL")}

    run("build_fn_index.py", str(bil), "--out", str(tmp / "bil_idx.json"),
        "--report", str(tmp / "fn.json"))
    fn = json.loads((tmp / "fn.json").read_text(encoding="utf-8"))
    got["fn"] = {"missing": len(fn.get("missing_in_notes", [])),
                 "orphan": len(fn.get("orphan_notes", [])),
                 "gaps": len(fn.get("gaps", []))}

    doc = json.loads(bil.read_text(encoding="utf-8"))
    kinds = {}
    for b in doc["blocks"]:
        kinds[b["kind"]] = kinds.get(b["kind"], 0) + 1
    got["blocks"] = len(doc["blocks"])
    got["footnotes"] = kinds.get("footnote", 0)
    got["tables"] = kinds.get("table", 0)
    return got, errs


def check_extraction(case, verbose):
    doc = json.loads((case / "blocks.json").read_text(encoding="utf-8"))
    text = "\n".join(p["text"] for pg in doc["pages"] for p in pg["paragraphs"])
    return {"pages": len(doc["pages"]),
            "paragraphs": sum(len(p["paragraphs"]) for p in doc["pages"]),
            "must_contain": [s for s in (case_assumptions(case).get("must_contain") or []) if s in text],
            "must_not_contain": [s for s in (case_assumptions(case).get("must_not_contain") or []) if s not in text],
            "missing_must_contain": [s for s in (case_assumptions(case).get("must_contain") or []) if s not in text],
            "found_forbidden": [s for s in (case_assumptions(case).get("must_not_contain") or []) if s in text]}, []


def case_assumptions(case):
    exp = json.loads((case / "expected.json").read_text(encoding="utf-8"))
    return exp.get("assertions", {})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures", required=True)
    ap.add_argument("--pdf-dir", default="")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()

    cases = sorted(p for p in Path(a.fixtures).iterdir() if (p / "expected.json").exists())
    if not cases:
        print("没有找到任何案例（expected.json）"); return 1

    total, failed = 0, 0
    for case in cases:
        total += 1
        exp = case_assumptions(case)
        kind = json.loads((case / "expected.json").read_text(encoding="utf-8")).get("kind", "full")
        # 临时产物放可写目录，避免只读安装目录（全局 skill 目录）与受限 temp
        tmp = make_tmpdir(f"zbf-{case.name}-")
        got, errs = (check_full(case, tmp, a.pdf_dir, a.verbose) if kind == "full"
                     else check_extraction(case, a.verbose))
        # 只比 expected 里声明过的键，便于以后扩充
        diffs = []
        for k, v in exp.items():
            if k in ("pdf_name", "must_contain", "must_not_contain"):
                continue
            if k not in got:
                continue
            if isinstance(v, dict):
                for kk, vv in v.items():
                    if got[k].get(kk) != vv:
                        diffs.append(f"{k}.{kk}: 期望 {vv} 实得 {got[k].get(kk)}")
            elif got[k] != v:
                diffs.append(f"{k}: 期望 {v} 实得 {got[k]}")
        if exp.get("missing_must_contain"):
            diffs.append(f"期望存在但缺失: {exp['missing_must_contain']}")
        if exp.get("found_forbidden"):
            diffs.append(f"期望不存在但出现: {exp['found_forbidden']}")

        status = "PASS" if not diffs else "FAIL"
        if diffs:
            failed += 1
        print(f"  [{status}] {case.name:<24} " + (" | ".join(diffs)[:150] if diffs else
              f"blocks={got.get('blocks')} fn={got.get('footnotes')} "
              f"qa={got.get('qa', {}).get('fail')} rules={got.get('rules', {}).get('fail')} "
              f"audit={got.get('audit', {}).get('fail')}"))
        if a.verbose and diffs:
            for d in diffs:
                print(f"        {d}")
        import shutil; shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n===== 黄金测试集：{total - failed}/{total} PASS =====")
    if failed:
        print("存在回归：本次改动不得合并（或先在用例上确认为预期变更并更新 expected.json）")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
