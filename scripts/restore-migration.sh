#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "用法: $0 /path/to/migration.tar.gz [target_dir]"
  exit 1
fi

PKG="$1"
if [[ ! -f "$PKG" ]]; then
  echo "文件不存在: $PKG"
  exit 1
fi

BASE_DIR="${2:-$(pwd)}"
mkdir -p "$BASE_DIR"
tar -xzf "$PKG" -C "$BASE_DIR"
echo "已恢复到: $BASE_DIR"
echo "请检查 data/settings.json 与 data/app.db 是否完整，再运行 scripts/install.sh 或直接执行 ./run.sh 启动。"
