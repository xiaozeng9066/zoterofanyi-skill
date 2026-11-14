#!/usr/bin/env bash
# 阶段3+4：双语 JSON -> HTML -> (Edge 打印) -> PDF -> 盖页脚 + 文本层自检
# 用法: scripts/build_pdf.sh <bilingual.json> <out.pdf> [页脚标签]
set -euo pipefail

JSON_IN="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
OUT="$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"
LABEL="${3:-}"
WORKDIR="$(dirname "$JSON_IN")"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/env.sh"
if ! PY=$(zb_resolve ZB_PY "$(zb_home)/.dsh/runtimes/zb-translate/venv" "$ROOT/env" "${ROOT}/../env"); then
  echo "找不到可用的 Python（需含 PyMuPDF）。请见 SETUP.md 或设置 ZB_PY" >&2; exit 3
fi
# 浏览器解析：EDGE_BIN 环境变量优先，其次自动探测 macOS / Windows(Git Bash) / Linux
if [ -n "${EDGE_BIN:-}" ] && [ -x "$EDGE_BIN" ]; then EDGE="$EDGE_BIN"
elif [ -x "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge" ]; then
  EDGE="/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"
elif [ -x "/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" ]; then
  EDGE="/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"     # Windows + Git Bash
elif [ -x "/c/Program Files/Microsoft/Edge/Application/msedge.exe" ]; then
  EDGE="/c/Program Files/Microsoft/Edge/Application/msedge.exe"
elif [ -x "/mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" ]; then
  EDGE="/mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" # WSL
elif command -v google-chrome >/dev/null 2>&1; then EDGE="$(command -v google-chrome)"
elif command -v chromium >/dev/null 2>&1; then EDGE="$(command -v chromium)"
else echo "找不到 Edge/Chrome，请设置 EDGE_BIN" >&2; exit 3; fi

HTML="$WORKDIR/sample.html"
RAW="$WORKDIR/sample_raw.pdf"

"$PY" "$ROOT/scripts/render.py" "$JSON_IN" --out "$HTML"

# Edge/Chromium 在本机受限环境下必须 --no-sandbox，否则 GPU/沙箱初始化直接失败
"$EDGE" --headless=new --no-sandbox --disable-gpu --disable-software-rasterizer \
        --disable-dev-shm-usage --no-first-run --no-default-browser-check \
        --user-data-dir="$WORKDIR/.edge-profile" --virtual-time-budget=20000 \
        --no-pdf-header-footer --print-to-pdf="$RAW" "file://$HTML" 2>&1 | tail -1

"$PY" "$ROOT/scripts/stamp.py" "$RAW" --label "$LABEL" --check
mv -f "$RAW" "$OUT"
echo "OK -> $OUT"
