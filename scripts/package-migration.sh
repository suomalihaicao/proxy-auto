#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT="${1:-$BASE_DIR/domain-proxy-manager-migration-${STAMP}.tar.gz}"

tar -czf "$OUT" \
  -C "$BASE_DIR" \
  --exclude='.venv' \
  --exclude='*.pyc' \
  --exclude='__pycache__' \
  app data templates scripts requirements.txt README.md run.sh

echo "已导出迁移包: $OUT"
