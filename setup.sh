#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$BASE_DIR"

RUN_WINDOWS=0
FORCE_LINUX=0

while [[ $# -gt 0 ]]; do
  case "${1:-}" in
    --windows)
      RUN_WINDOWS=1
      shift || true
      ;;
    --linux)
      FORCE_LINUX=1
      shift || true
      ;;
    -h|--help)
      echo "[setup] 用法: ./setup.sh [--windows|--linux]"
      echo "  --windows  强制使用 Windows 部署脚本"
      echo "  --linux    强制使用 Linux 部署脚本"
      exit 0
      ;;
    *)
      break
      ;;
  esac
done

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
if [[ "$FORCE_LINUX" -eq 0 ]]; then
  if [[ "$OS" == MINGW* || "$OS" == MSYS* || "$OS" == CYGWIN* ]]; then
    if command -v pwsh >/dev/null 2>&1; then
      exec pwsh -NoProfile -ExecutionPolicy Bypass -File "scripts/deploy-windows.ps1" "$@"
    elif command -v powershell.exe >/dev/null 2>&1; then
      exec powershell.exe -NoProfile -ExecutionPolicy Bypass -File "scripts/deploy-windows.ps1" "$@"
    else
      echo "[setup] 未检测到 PowerShell，将回退到 Linux 部署流程"
    fi
  fi
fi

if [[ -x "scripts/deploy-linux.sh" ]]; then
  exec bash "scripts/deploy-linux.sh" "$@"
fi

if command -v pwsh >/dev/null 2>&1; then
  exec pwsh -NoProfile -ExecutionPolicy Bypass -File "scripts/deploy-windows.ps1" "$@"
fi

echo "[setup] 未发现可执行部署脚本"
exit 1
