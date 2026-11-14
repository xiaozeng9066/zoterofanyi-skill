#!/usr/bin/env bash
# 打包 skill 以便迁移到另一台机器（含 Windows）。
# 用法: bash export_skill.sh [输出目录]   → 生成 zoterofanyi-skill-YYYYMMDD.tar.gz
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$HOME/Desktop}"
STAMP=$(date +%Y%m%d)
TAR="$OUT/zoterofanyi-skill-$STAMP.tar.gz"
mkdir -p "$OUT"
tar -czf "$TAR" -C "$ROOT/.." \
    --exclude='__pycache__' --exclude='.DS_Store' "$(basename "$ROOT")"
echo "已打包: $TAR"
echo "大小: $(du -h "$TAR" | cut -f1)"
echo "校验: sha256=$(shasum -a 256 "$TAR" | awk '{print $1}')"
echo
echo "迁移到 Windows 后：解压到 %USERPROFILE%\\.dsh\\skills\\ 下（得到 zoterofanyi\\ 目录）"
