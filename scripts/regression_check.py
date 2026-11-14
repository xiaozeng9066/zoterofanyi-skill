#!/usr/bin/env python3
"""回归检查：对一份已完成的译文跑全部机械检查，输出记分卡。

跨平台 Python 版（Windows 无需 Git Bash）；与 regression_check.sh 行为一致。
用法: python regression_check.py <原PDF> <regions.json> <双语JSON> [工作报告目录]
"""
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    if len(sys.argv) < 4:
        print('用法: regression_check.py <原PDF> <regions.json> <双语JSON> [工作报告目录]')
        return 2
    pdf, regions, bil = (Path(x).resolve() for x in sys.argv[1:4])
    out = Path(sys.argv[4]).resolve() if len(sys.argv) > 4 else Path.cwd() / "reports"
    out.mkdir(parents=True, exist_ok=True)

    checks = [
        ("机械保真(qa)", ["qa.py", str(bil), "--out", str(out / "reg_qa.json")]),
        ("规则检查(rules)", ["rules_check.py", str(bil), "--out", str(out / "reg_rules.json"),
                         "--term-exceptions", str(out / "term_exceptions.json")]),
        ("全篇审计(audit)", ["audit_document.py", str(bil), "--out", str(out / "reg_audit.json")]),
        ("术语审计(term)", ["term_audit.py", str(bil), "--out", str(out / "reg_term.json")]),
        ("表格交叉验证(table)", ["crosscheck_tables.py", str(pdf), str(regions), str(bil),
                            "--out", str(out / "reg_table.json")]),
        ("脚注交叉核对(fn)", ["build_fn_index.py", str(bil), "--out", os.devnull,
                         "--report", str(out / "reg_fn.json")]),
    ]

    print(f"===== 回归检查：{bil.name} =====")
    failed = False
    for name, argv in checks:
        log = out / ("reg_" + name.split("(")[0] + ".log")
        with log.open("w", encoding="utf-8", errors="replace") as fh:
            r = subprocess.run([sys.executable, str(HERE / argv[0]), *argv[1:]],
                               stdout=fh, stderr=subprocess.STDOUT)
        if r.returncode == 0:
            print(f"  [OK] {name}")
        else:
            failed = True
            print(f"  [FAIL] {name}（exit={r.returncode}）-> {log}")
            try:
                lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                lines = []
            hits = [ln for ln in lines if any(k in ln for k in ("FAIL", "不连续", "缺失", "不一致"))][:3]
            for ln in hits:
                print("       " + ln)

    print()
    if not failed:
        print("结论：全部机械检查通过（仍不等于可定稿，见 SKILL.md 的交付门槛）")
        return 0
    print("结论：存在未通过项 -> 必须先处理或明确列入验收报告，不得声称已核验")
    return 1


if __name__ == "__main__":
    sys.exit(main())
