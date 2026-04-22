#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SETTINGS_FILE="$BASE_DIR/data/settings.json"
PYTHON_BIN="$BASE_DIR/.venv/bin/python"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

web_host="0.0.0.0"
web_port="8080"
proxy_port="3128"

if [[ -f "$SETTINGS_FILE" ]]; then
  readarray -t vals < <("$PYTHON_BIN" - <<PY
import json
from pathlib import Path
cfg = {}
try:
    cfg = json.loads(Path("$SETTINGS_FILE").read_text(encoding="utf-8"))
except Exception:
    cfg = {}
print(cfg.get("web_host", "0.0.0.0"))
print(cfg.get("web_port", 8080))
print(cfg.get("listen_port", 3128))
PY
  )
  web_host="${vals[0]:-0.0.0.0}"
  web_port="${vals[1]:-8080}"
  proxy_port="${vals[2]:-3128}"
fi

echo "Web 端口: ${web_port}"
echo "代理端口: ${proxy_port}"
echo "Web 监听 host 配置: ${web_host}"
echo

echo "[1/4] 监听检查"
if ss -lntp 2>/dev/null | grep -E ":[0-9]+\\>" >/dev/null; then
  if ss -lntp | grep -q "(:${web_port} )"; then
    echo "  √ web 端口 ${web_port} 已监听"
  else
    echo "  × web 端口 ${web_port} 未监听"
  fi
  if ss -lntp | grep -q "(:${proxy_port} )"; then
    echo "  √ 代理端口 ${proxy_port} 已监听"
  else
    echo "  × 代理端口 ${proxy_port} 未监听"
  fi
else
  echo "  × ss 工具不可用，无法检查端口监听"
fi
echo

echo "[2/4] 进程检查"
ps -ef | grep -v grep | grep -q "uvicorn app.main:app" && echo "  √ uvicorn 运行中" || echo "  × uvicorn 未在运行"
echo

echo "[3/4] 防火墙放行提示"
if command -v ufw >/dev/null 2>&1; then
  echo "  使用 ufw 检测："
  sudo ufw status verbose 2>/dev/null | sed -n '1,40p' || true
elif command -v firewall-cmd >/dev/null 2>&1; then
  echo "  使用 firewalld 检测："
  sudo firewall-cmd --list-ports 2>/dev/null || true
else
  echo "  未检测到 ufw/firewalld。若通过 iptables，执行:"
  echo "    sudo iptables -C INPUT -p tcp --dport ${web_port} -j ACCEPT || sudo iptables -I INPUT -p tcp --dport ${web_port} -j ACCEPT"
  echo "    sudo iptables -C INPUT -p tcp --dport ${proxy_port} -j ACCEPT || sudo iptables -I INPUT -p tcp --dport ${proxy_port} -j ACCEPT"
fi
echo

echo "[4/4] 外网访问建议"
if [[ "${web_host}" == "127.0.0.1" || "${web_host}" == "localhost" || "${web_host}" == "::1" ]]; then
  echo "  检测到 web_host 为本地回环地址，建议设置为 0.0.0.0。"
  echo "  你可执行：sed -n ..."
else
  echo "  web_host 非回环地址，若仍超时请检查云安全组/路由和端口映射。"
fi

echo
echo "排查脚本完成。"
