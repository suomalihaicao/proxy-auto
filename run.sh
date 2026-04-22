#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$BASE_DIR"

release_web_port() {
  local port="$1"
  local pids=""

  if command -v lsof >/dev/null 2>&1; then
    pids="$(lsof -tiTCP -sTCP:LISTEN -P -n -i ":$port" 2>/dev/null || true)"
  elif command -v ss >/dev/null 2>&1; then
    pids="$(ss -ltnp "sport = :$port" 2>/dev/null | awk 'NR>1 { if (match($0, /pid=([0-9]+)/, a)) print a[1] }' | sort -u || true)"
  elif command -v fuser >/dev/null 2>&1; then
    pids="$(fuser -n tcp "$port" 2>/dev/null || true)"
  fi

  if [[ -z "${pids//[[:space:]]/}" ]]; then
    return 0
  fi

  echo "检测到端口 ${port} 被占用，强制停止进程: $pids"
  for pid in $pids; do
    if [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1; then
      kill -9 "$pid" 2>/dev/null || true
    fi
  done

  sleep 1
}

if [[ -x "$BASE_DIR/.venv/bin/python" ]]; then
  PY="$BASE_DIR/.venv/bin/python"
else
  PY="python3"
fi

export PYTHONPATH="$BASE_DIR:${PYTHONPATH:-}"

web_host="0.0.0.0"
web_port="8666"

if [[ -f "$BASE_DIR/data/settings.json" ]]; then
  readarray -t settings_lines < <("$PY" - <<PY
import json
from pathlib import Path
path = Path("${BASE_DIR}") / "data" / "settings.json"
cfg = {}
try:
    cfg = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    cfg = {}
print(cfg.get("web_host", "0.0.0.0"))
print(cfg.get("web_port", 8666))
PY
  )
  if [[ -n "${settings_lines[0]:-}" ]]; then
    web_host="${settings_lines[0]}"
  fi
  if [[ -n "${settings_lines[1]:-}" ]]; then
    web_port="${settings_lines[1]}"
  fi
fi

case "$web_host" in
  "127.0.0.1"|"localhost"|"localhost.local"|"localhost.localdomain"|"::1"|"::ffff:127.0.0.1")
    web_host="0.0.0.0"
    ;;
esac

if [[ "$web_host" == "" ]]; then
  web_host="0.0.0.0"
fi

release_web_port "$web_port"
exec "$PY" -m uvicorn app.main:app --host "${web_host}" --port "${web_port}"
