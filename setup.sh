#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$BASE_DIR"

RUN_WINDOWS=0

case "${1:-}" in
  --windows)
    RUN_WINDOWS=1
    shift || true
    ;;
  --linux)
    RUN_WINDOWS=0
    shift || true
    ;;
  "")
    ;;
  *)
    ;;
esac

if [[ "$RUN_WINDOWS" -eq 1 ]]; then
  if command -v pwsh >/dev/null 2>&1; then
    exec pwsh -NoProfile -ExecutionPolicy Bypass -File "scripts/deploy-windows.ps1" "$@"
  elif command -v powershell.exe >/dev/null 2>&1; then
    exec powershell.exe -NoProfile -ExecutionPolicy Bypass -File "scripts/deploy-windows.ps1" "$@"
  else
    echo "[setup] 未检测到 PowerShell，无法执行 Windows 部署"
    exit 1
  fi
fi

OS="$(uname -s)"
if [[ "$OS" == MINGW* || "$OS" == MSYS* || "$OS" == CYGWIN* ]]; then
  exec bash "scripts/deploy-linux.sh" "$@"
fi

if [[ -x "scripts/deploy-linux.sh" ]]; then
  exec bash "scripts/deploy-linux.sh" "$@"
fi

if command -v pwsh >/dev/null 2>&1; then
  exec pwsh -NoProfile -ExecutionPolicy Bypass -File "scripts/deploy-windows.ps1" "$@"
fi

echo "[setup] 未发现可执行部署脚本"
exit 1
