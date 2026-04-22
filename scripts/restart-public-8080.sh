#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="/root/domain-proxy-manager"
DATA_DIR="$BASE_DIR/data"
SETTINGS_FILE="$DATA_DIR/settings.json"
LOG_FILE="/tmp/domain-proxy-manager.log"
SERVICE_NAME="domain-proxy-manager"
PID_FILE="/tmp/domain-proxy-manager-8080.pid"

wait_for_health() {
  local host="$1"
  local port="$2"
  local attempts=25
  local i=1

  while (( i <= attempts )); do
    if ! curl -fsS --max-time 2 "http://${host}:${port}/health" >/dev/null 2>&1; then
      sleep 1
      ((i++))
      continue
    fi
    echo "健康检查通过: http://${host}:${port}/health"
    return 0
  done

  return 1
}

cleanup_old_instances() {
  local pattern="$1"
  local pids
  pids="$(pgrep -f "$pattern" || true)"
  if [[ -n "$pids" ]]; then
    echo "清理旧实例: $pattern"
    while IFS= read -r old_pid; do
      if [[ -z "$old_pid" ]]; then
        continue
      fi
      if kill -0 "$old_pid" >/dev/null 2>&1; then
        kill "$old_pid" || true
        sleep 1
        if kill -0 "$old_pid" >/dev/null 2>&1; then
          kill -9 "$old_pid" || true
        fi
      fi
    done <<< "$pids"
    sleep 1
  fi
}

cd "$BASE_DIR"

if [[ ! -d "$BASE_DIR/.venv" ]]; then
  echo "缺少 .venv，请先执行 scripts/install.sh"
  exit 1
fi

if [[ -f "$SETTINGS_FILE" ]]; then
  BASE_DIR="$BASE_DIR" python3 - <<'PY'
import json
import os
from pathlib import Path

settings_path = Path(os.environ["BASE_DIR"]) / "data" / "settings.json"
cfg = json.loads(settings_path.read_text(encoding="utf-8"))
cfg["web_host"] = "0.0.0.0"
cfg["web_port"] = 8080
if "proxy_mode" not in cfg:
    cfg["proxy_mode"] = "direct"
cfg.setdefault("proxy_protocol", "http")
cfg.setdefault("api_url", "")
cfg.setdefault("api_method", "GET")
cfg.setdefault("api_timeout", 8)
cfg.setdefault("api_cache_ttl", 20)
cfg.setdefault("api_headers", "")
cfg.setdefault("api_body", "")
cfg.setdefault("api_host_key", "host")
cfg.setdefault("api_port_key", "port")
cfg.setdefault("api_username_key", "username")
cfg.setdefault("api_password_key", "password")
cfg.setdefault("api_proxy_field", "proxy")
cfg.setdefault("bigdata_api_url", "")
cfg.setdefault("bigdata_api_token", "")
cfg.setdefault("allowed_client_ips", "")
settings_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
else
  cat > "$SETTINGS_FILE" <<'JSON'
{
  "listen_host": "0.0.0.0",
  "listen_port": 3128,
  "web_host": "0.0.0.0",
  "web_port": 8080,
  "proxy_mode": "direct",
  "proxy_host": "",
  "proxy_port": 0,
  "proxy_username": "",
  "proxy_password": "",
  "proxy_protocol": "http",
  "api_url": "",
  "api_method": "GET",
  "api_timeout": 8,
  "api_cache_ttl": 20,
  "api_headers": "",
  "api_body": "",
  "api_host_key": "host",
  "api_port_key": "port",
  "api_username_key": "username",
  "api_password_key": "password",
  "api_proxy_field": "proxy",
  "bigdata_api_url": "",
  "bigdata_api_token": "",
  "allowed_client_ips": "",
  "session_secret": "change-me"
}
JSON
fi

if [[ -f "$PID_FILE" ]]; then
  old_pid="$(cat "$PID_FILE")"
  if [[ -n "$old_pid" ]] && kill -0 "$old_pid" >/dev/null 2>&1; then
    echo "发现旧会话(PID=$old_pid)，尝试停止..."
    kill "$old_pid" || true
    sleep 1
    if kill -0 "$old_pid" >/dev/null 2>&1; then
      kill -9 "$old_pid" || true
      sleep 1
    fi
  fi
  rm -f "$PID_FILE"
fi

cleanup_old_instances "$BASE_DIR/.venv/bin/python -m uvicorn app.main:app --host"
cleanup_old_instances "uvicorn app.main:app --host"

if command -v systemctl >/dev/null 2>&1 && systemctl list-units --full -all --type=service 2>/dev/null | grep -q "${SERVICE_NAME}.service"; then
  sudo systemctl restart "$SERVICE_NAME" || true
  echo "已尝试重启 systemd 服务: $SERVICE_NAME"
  sleep 1
else
  nohup "$BASE_DIR/run.sh" >"$LOG_FILE" 2>&1 &
  pid="$!"
  echo "$pid" > "$PID_FILE"
  echo "已启动临时前台进程 (pid: $pid)"

  echo "监听检查:"
  if wait_for_health "127.0.0.1" 8080; then
    if command -v ss >/dev/null 2>&1; then
      ss -lntp | grep -E '(:8080|:3128)' || true
    else
      echo "  未检测到 ss 工具，已跳过监听列表输出"
    fi
  else
    echo "启动失败: 8080 健康检查超时（约 25s）"
    echo "日志尾部："
    tail -n 80 "$LOG_FILE" 2>/dev/null || true
    echo
    echo "排查建议："
    echo "1. 检查服务进程是否在运行：ps -ef | grep uvicorn"
    echo "2. 手动前台启动看直接报错：cd $BASE_DIR && ./.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8080"
    exit 1
  fi
fi
