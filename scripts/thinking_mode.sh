#!/usr/bin/env bash
# 临时切换 DSH 的"思考模式"（只用于跑翻译子代理；跑完立刻切回）。
#
# 为什么必须两处一起改：只关 thinking 时，DSH 仍按 reasoningEffort "high" 发请求，
# 服务端回 UNSUPPORTED_REASONING_EFFORT（实测踩过）。
#
# 用法：
#   scripts/thinking_mode.sh off      # 关：thinking disabled + reasoningEffort off
#   scripts/thinking_mode.sh on       # 开：删掉这两项（恢复默认）
#   scripts/thinking_mode.sh status   # 看当前状态
#   DSH_PATCH=<路径> scripts/thinking_mode.sh off   # 换配置文件（默认 ~/.dsh/cordis.patch.yml）
#
# 安全措施：
#   - 改之前把原文件备份成 <file>.bak-thinking-<时间戳>；
#   - 用 python 做 YAML 的"块追加/删除"，不重写其它内容（含 personaPrefix 里的中文）；
#   - 同一节点重复调用幂等（先删后加，不会叠两份）；
#   - 本脚本**不会**自动还原：切回必须显式 on（避免命令中途失败留下静默状态）。
set -euo pipefail

PATCH="${DSH_PATCH:-$HOME/.dsh/cordis.patch.yml}"
PY="${ZB_PY:-$HOME/.dsh/runtimes/zb-translate/venv/bin/python}"
BLOCK_ID="llm-deepseek-account"
STATE="${1:-status}"

[ -f "$PATCH" ] || { echo "找不到 $PATCH" >&2; exit 3; }

"$PY" - "$PATCH" "$STATE" "$BLOCK_ID" <<'PY'
import re, sys, shutil, time
from pathlib import Path

path, state, bid = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
s = path.read_text(encoding="utf-8")

# 本脚本管理的节点长这样（可能不存在）：
#   - id: llm-deepseek-account
#     config:
#       thinking: disabled
#       reasoningEffort: off
NODE = re.compile(
    r"\n?- id: " + re.escape(bid) + r"\n  config:\n(?:    thinking: \w+\n)?(?:    reasoningEffort: \w+\n)?",
    re.M)

def strip(s):
    return NODE.sub("\n", s)

def current(s):
    m = NODE.search(s)
    if not m:
        return None
    body = m.group(0)
    t = re.search(r"thinking: (\w+)", body)
    r = re.search(r"reasoningEffort: (\w+)", body)
    return (t.group(1) if t else None, r.group(1) if r else None)

if state == "status":
    cur = current(s)
    if cur:
        print("thinking=%s reasoningEffort=%s  (%s)" % (cur[0], cur[1], path))
    else:
        print("thinking=(默认/未覆盖)  (%s)" % path)
    sys.exit(0)

base = strip(s)
if state == "off":
    new = base.rstrip("\n") + "\n\n- id: " + bid + "\n  config:\n    thinking: disabled\n    reasoningEffort: off\n"
elif state == "on":
    new = base
else:
    sys.exit("用法: thinking_mode.sh on|off|status")

if new == s:
    print("已经是该状态：%s" % state); sys.exit(0)

shutil.copy2(path, path.with_name(path.name + ".bak-thinking-" + time.strftime("%Y%m%d-%H%M%S")))
new = new.rstrip("\n") + "\n"
path.write_text(new, encoding="utf-8")
print("已切换为 %s（备份后写入 %s）" % (state, path))
PY
