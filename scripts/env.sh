#!/usr/bin/env bash
# 统一的解释器解析（macOS / Linux / Windows-Git-Bash / WSL 通用）。
# Windows 的 venv 可执行文件在 Scripts/python.exe，Unix 在 bin/python —— 这是迁移最常见的坑。

zb_venv_py() {   # zb_venv_py <venv目录> → 打印可用的 python 路径
  local v="$1"
  for c in "$v/bin/python" "$v/Scripts/python.exe" "$v/bin/python3"; do
    [ -x "$c" ] && { printf '%s' "$c"; return 0; }
  done
  return 1
}

zb_resolve() {   # zb_resolve <环境变量名> <首选venv> [备选venv...] → 打印 python
  local var="$1"; shift
  local override="${!var:-}"
  if [ -n "$override" ] && [ -x "$override" ]; then printf '%s' "$override"; return 0; fi
  local v
  for v in "$@"; do
    if p=$(zb_venv_py "$v"); then printf '%s' "$p"; return 0; fi
  done
  return 1
}

zb_home() { printf '%s' "${HOME:-$USERPROFILE}"; }
