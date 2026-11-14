#!/usr/bin/env bash
# 回归检查：对一份已完成的译文跑全部机械检查，输出记分卡。
# 用途：每处理完一篇新文献都跑一次，作为"这篇是否达标"的客观记录；
#      也是 skill 迭代的依据（新坑 → 新检查项）。
#
# 用法: regression_check.sh <原PDF> <regions.json> <双语JSON> <工作报告目录>
set -uo pipefail

PDF="$1"; REGIONS="$2"; BIL="$3"; OUT="${4:-reports}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/env.sh"
if ! PY=$(zb_resolve ZB_PY "$(zb_home)/.dsh/runtimes/zb-translate/venv" "$ROOT/env"); then
  echo "找不到可用的 Python（需含 PyMuPDF）" >&2; exit 3
fi
mkdir -p "$OUT"

fail=0
run () {  # run <名称> <命令...>
  local name="$1"; shift
  local log="$OUT/reg_$(echo "$name" | tr ' /' '__').log"
  if "$@" > "$log" 2>&1; then
    echo "  ✅ $name"
  else
    local rc=$?
    # qa/rules 的 FAIL 会以非零退出；把摘要行提出来
    echo "  ❌ ${name}（exit=${rc}）→ $log"
    grep -E "FAIL|不连续|缺失|不一致" "$log" | head -3 | sed 's/^/       /'
    fail=1
  fi
}

echo "===== 回归检查：$(basename "$BIL") ====="
run "机械保真(qa)"        "$PY" "$ROOT/scripts/qa.py" "$BIL" --out "$OUT/reg_qa.json"
run "规则检查(rules)"     "$PY" "$ROOT/scripts/rules_check.py" "$BIL" --out "$OUT/reg_rules.json" --term-exceptions "$OUT/term_exceptions.json"
run "全篇审计(audit)"     "$PY" "$ROOT/scripts/audit_document.py" "$BIL" --out "$OUT/reg_audit.json"
run "术语审计(term)"      "$PY" "$ROOT/scripts/term_audit.py" "$BIL" --out "$OUT/reg_term.json"
run "表格交叉验证(table)" "$PY" "$ROOT/scripts/crosscheck_tables.py" "$PDF" "$REGIONS" "$BIL" --out "$OUT/reg_table.json"
run "脚注交叉核对(fn)"    "$PY" "$ROOT/scripts/build_fn_index.py" "$BIL" --out /dev/null --report "$OUT/reg_fn.json"

echo
if [ "$fail" -eq 0 ]; then echo "结论：全部机械检查通过（仍不等于可定稿，见 SKILL.md 的交付门槛）"
else echo "结论：存在未通过项 → 必须先处理或明确列入验收报告，不得声称已核验"; fi
exit "$fail"
