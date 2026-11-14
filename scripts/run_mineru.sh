#!/usr/bin/env bash
# 用 MinerU 做结构化抽取（pipeline 后端，CPU）。
#
# 用法: scripts/run_mineru.sh <input.pdf> <outdir> [--pages 7-9] [--lang en]
#
# 说明：
#   - MinerU 2.x 的 pipeline 后端 = DocLayout-YOLO 版面分析 + OCR + 表格/公式模型，
#     对"无框线表格"和"双栏阅读顺序"比 PyMuPDF 启发式可靠得多；
#   - 大 PDF 先用 PyMuPDF 切出页区间再跑，避免几十分钟的等待；
#   - 模型来源用 ModelScope（国内稳定），首次运行会自动下载约 1GB+。
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/env.sh"
if ! PY=$(zb_resolve MINERU_PY "$(zb_home)/.dsh/runtimes/mineru/venv310"); then
  echo "找不到 MinerU 环境，请设置 MINERU_PY（见 SETUP.md）" >&2; exit 3
fi
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SLICER=$(zb_resolve ZB_PY "$(zb_home)/.dsh/runtimes/zb-translate/venv" "$ROOT/env") || SLICER=""

IN="$1"; OUT="$2"; shift 2
PAGES=""; LANG_CODE="en"
while [ $# -gt 0 ]; do
  case "$1" in
    --pages) PAGES="$2"; shift 2 ;;
    --lang)  LANG_CODE="$2"; shift 2 ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done

mkdir -p "$OUT"
TARGET="$IN"
if [ -n "$PAGES" ]; then
  TARGET="$OUT/_input_p${PAGES//,/_}.pdf"
  echo "[slice] 抽取页 $PAGES -> $TARGET"
  $SLICER - "$IN" "$TARGET" "$PAGES" <<'EOF'
import sys, pymupdf
src, dst, spec = sys.argv[1], sys.argv[2], sys.argv[3]
pages = []
for part in spec.split(","):
    if "-" in part:
        a, b = part.split("-"); pages += list(range(int(a), int(b) + 1))
    else:
        pages.append(int(part))
d = pymupdf.open(src); out = pymupdf.open()
for p in pages:
    out.insert_pdf(d, from_page=p - 1, to_page=p - 1)
out.save(dst); print(f"  sliced {len(pages)} pages")
EOF
fi

export MINERU_MODEL_SOURCE="${MINERU_MODEL_SOURCE:-modelscope}"
export MINERU_DEVICE_MODE="${MINERU_DEVICE_MODE:-cpu}"
echo "[mineru] 后端=pipeline 语言=$LANG_CODE 设备=$MINERU_DEVICE_MODE 模型源=$MINERU_MODEL_SOURCE"
"$PY" -m mineru.cli.client -p "$TARGET" -o "$OUT" -b pipeline -l "$LANG_CODE" 2>&1 | tail -25
echo "[mineru] 输出目录："
find "$OUT" -maxdepth 3 -name "*.md" -o -maxdepth 3 -name "*middle.json" | sed 's/^/  /'
