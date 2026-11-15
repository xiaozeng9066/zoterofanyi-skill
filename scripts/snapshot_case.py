#!/usr/bin/env python3
"""把一篇已完成的产物快照成"黄金测试案例"。

用途：每次改 skill 后重跑回归，确保没把已修好的地方改坏。
一个案例 = 该篇的产物（bilingual.json / blocks.json / regions.json）+ expected.json。

  # 完整双语案例（跑全部检查）
  snapshot_case.py --kind full --bilingual <bilingual.json> --regions <regions.json> \
                   --name nie2015_full --fixtures <fixtures目录> [--pdf-name xxx.pdf]

  # 仅抽取案例（验证读序/无 OCR 污染等）
  snapshot_case.py --kind extraction --blocks <blocks.json> --name pang2021_extract \
                   --must-contain "Objective:" --must-contain "machine learning" \
                   --must-not-contain "Ojecive" --must-not-contain "learnig"

  # 翻译记忆回流案例（锁住规则 83：跨词切片不得当术语注入）
  snapshot_case.py --kind memory --changes <changes.json> --name mem_inject --fixtures <fixtures目录>
"""
import argparse, json, shutil, subprocess, sys
from pathlib import Path

HERE = Path(__file__).parent


def run(script, *args):
    r = subprocess.run([sys.executable, str(HERE / script), *args],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.returncode, r.stdout + r.stderr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=["full", "extraction", "memory"], required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--fixtures", required=True)
    ap.add_argument("--bilingual"); ap.add_argument("--regions")
    ap.add_argument("--blocks"); ap.add_argument("--pdf-name", default="")
    ap.add_argument("--changes", help="memory 案例：审校修正 changes.json 源文件")
    ap.add_argument("--must-contain", action="append", default=[])
    ap.add_argument("--must-not-contain", action="append", default=[])
    a = ap.parse_args()

    case = Path(a.fixtures) / a.name
    case.mkdir(parents=True, exist_ok=True)
    tmp = case / "_tmp"
    tmp.mkdir(exist_ok=True)

    if a.kind == "full":
        shutil.copy(a.bilingual, case / "bilingual.json")
        shutil.copy(a.regions, case / "regions.json")
        doc = json.loads(Path(a.bilingual).read_text(encoding="utf-8"))
        kinds = {}
        for b in doc["blocks"]:
            kinds[b["kind"]] = kinds.get(b["kind"], 0) + 1
        exp = {"case": a.name, "kind": "full", "assertions": {
            "blocks": len(doc["blocks"]),
            "footnotes": kinds.get("footnote", 0),
            "tables": kinds.get("table", 0),
            "figures": kinds.get("figure", 0),
            "equations": kinds.get("equation", 0),
            "pdf_name": a.pdf_name,
        }}
        # 跑检查器，把"当前基线"写进 expected
        run("qa.py", str(case / "bilingual.json"), "--out", str(tmp / "qa.json"))
        run("rules_check.py", str(case / "bilingual.json"), "--out", str(tmp / "rules.json"))
        run("audit_document.py", str(case / "bilingual.json"), "--out", str(tmp / "audit.json"))
        for name, key in (("qa", "qa"), ("rules", "rules"), ("audit", "audit")):
            f = tmp / f"{name}.json"
            if not f.exists():
                continue
            d = json.loads(f.read_text(encoding="utf-8"))
            if name == "qa":
                exp["assertions"]["qa"] = {"fail": d.get("fail", 0)}
            else:
                fs = d.get("findings", [])
                exp["assertions"][key] = {"fail": sum(1 for x in fs if x.get("级别", x.get("level")) == "FAIL")}
        shutil.rmtree(tmp, ignore_errors=True)
    elif a.kind == "memory":
        # 规则 83：锁住「哪些词对可当术语注入、哪些只算跨词切片」
        if not a.changes:
            raise SystemExit("--kind memory 需要 --changes")
        if Path(a.changes).resolve() != (case / "changes.json").resolve():
            shutil.copy(a.changes, case / "changes.json")
        run("build_memory.py", "--changes", str(case / "changes.json"),
            "--tm", str(tmp / "tm"), "--report", str(tmp / "mem.json"))
        rep = json.loads((tmp / "mem.json").read_text(encoding="utf-8"))
        exp = {"case": a.name, "kind": "memory", "assertions": {
            "edits": rep["edits"],
            "term_fixes_inject": len(rep["injected"]),
            "must_keep": rep["injected"],
            "must_reject": [[w, r] for w, r, _ in rep["skipped"]],
        }}
        shutil.rmtree(tmp, ignore_errors=True)
    else:
        shutil.copy(a.blocks, case / "blocks.json")
        doc = json.loads(Path(a.blocks).read_text(encoding="utf-8"))
        paras = sum(len(p["paragraphs"]) for p in doc["pages"])
        text = "\n".join(p["text"] for pg in doc["pages"] for p in pg["paragraphs"])
        exp = {"case": a.name, "kind": "extraction", "assertions": {
            "pages": len(doc["pages"]),
            "paragraphs": paras,
            "must_contain": [s for s in a.must_contain if s in text],
            "must_not_contain": [s for s in a.must_not_contain if s not in text],
            "missing_must_contain": [s for s in a.must_contain if s not in text],
            "found_forbidden": [s for s in a.must_not_contain if s in text],
        }}

    (case / "expected.json").write_text(json.dumps(exp, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"快照完成: {case}")
    print(json.dumps(exp["assertions"], ensure_ascii=False, indent=2)[:700])


if __name__ == "__main__":
    main()
