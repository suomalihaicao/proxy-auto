#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$BASE_DIR"

if [[ -x "$BASE_DIR/.venv/bin/python" ]]; then
  PY="$BASE_DIR/.venv/bin/python"
else
  PY="python3"
fi

export PYTHONPATH="$BASE_DIR:${PYTHONPATH:-}"

web_host="0.0.0.0"
web_port="8080"

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
print(cfg.get("web_port", 8080))
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

exec "$PY" -m uvicorn app.main:app --host "${web_host}" --port "${web_port}"
