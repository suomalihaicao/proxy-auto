#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="$BASE_DIR/data"
SERVICE_NAME="domain-proxy-manager"

mkdir -p "$DATA_DIR"

if ! command -v python3 >/dev/null 2>&1; then
  echo "错误：未检测到 python3"
  exit 1
fi

read -r -p "上游代理模式 [single_ip/api/bigdata_api/direct] (回车默认 single_ip): " proxy_mode
proxy_mode="${proxy_mode:-single_ip}"
api_url=""
bigdata_api_url=""
bigdata_api_token=""
proxy_protocol="http"
proxy_host=""
proxy_port=0
proxy_user=""
proxy_pass=""

case "$proxy_mode" in
  http|socks5)
    proxy_protocol="$proxy_mode"
    proxy_mode="single_ip"
    ;;
  single_ip|api|bigdata_api|direct)
    ;;
  *)
    echo "不支持的模式: $proxy_mode"
    exit 1
    ;;
esac

if [[ "$proxy_mode" == "single_ip" ]]; then
  read -r -p "单IP代理协议 [http/socks5] (回车默认 http): " protocol_input
  protocol_input="${protocol_input:-http}"
  if [[ "$protocol_input" != "http" && "$protocol_input" != "socks5" ]]; then
    echo "不支持的协议: $protocol_input"
    exit 1
  fi
  proxy_protocol="$protocol_input"
fi

if [[ "$proxy_mode" == "bigdata_api" ]]; then
  read -r -p "BigData API 地址: " bigdata_api_url
  read -r -p "API 地址（可空，作为 fallback）: " api_url
  read -r -p "BigData Token（可空）: " bigdata_api_token
elif [[ "$proxy_mode" == "api" ]]; then
  read -r -p "API 地址（必填）: " api_url
fi

if [[ "$proxy_mode" == "api" || "$proxy_mode" == "bigdata_api" ]]; then
  if [[ -z "${api_url}" ]]; then
    api_url=""
  fi
  if [[ -z "${bigdata_api_url}" ]]; then
    bigdata_api_url=""
  fi
  if [[ -z "${bigdata_api_token}" ]]; then
    bigdata_api_token=""
  fi
fi

if [[ "$proxy_mode" == "single_ip" ]]; then
  read -r -p "代理地址 (例如: 127.0.0.1): " proxy_host
  read -r -p "代理端口 (例如: 8080): " proxy_port
  read -r -p "代理用户名(可空): " proxy_user
  read -r -s -p "代理密码(可空): " proxy_pass
  echo
fi

if [[ "$proxy_mode" == "api" ]]; then
  if [[ -z "${api_url}" ]]; then
    echo "API 模式需要填写 api_url"
    exit 1
  fi
fi
if [[ "$proxy_mode" == "bigdata_api" ]]; then
  if [[ -z "${api_url}" && -z "${bigdata_api_url}" ]]; then
    echo "BigData 模式需要至少一个 API 地址（bigdata_api_url 或 api_url）"
    exit 1
  fi
fi

if [[ "$proxy_mode" != "direct" && "$proxy_mode" != "single_ip" ]]; then
  echo "已切换到 ${proxy_mode} 模式"
fi

if [[ "$proxy_mode" == "api" ]]; then
  echo "当前使用 API 模式: ${api_url}"
fi

if [[ "$proxy_mode" == "bigdata_api" ]]; then
  if [[ -n "$bigdata_api_url" ]]; then
    echo "当前使用 BigData API 地址: ${bigdata_api_url}"
  else
    echo "当前使用 API 地址作为 BigData fallback: ${api_url}"
  fi
fi

if [[ "$proxy_mode" == "direct" ]]; then
  echo "当前使用 direct 模式（命中规则也不使用上游代理）"
fi

read -r -p "代理监听地址 [0.0.0.0]: " listen_host
listen_host="${listen_host:-0.0.0.0}"
read -r -p "代理监听端口 [3128]: " listen_port
listen_port="${listen_port:-3128}"

read -r -p "Web 监听端口 [8080]: " web_port
web_port="${web_port:-8080}"

  read -r -p "管理员用户名 [admin]: " admin_user
  admin_user="${admin_user:-admin}"
  read -r -s -p "管理员密码 [admin123]: " admin_password
  echo
  admin_password="${admin_password:-admin123}"

if command -v openssl >/dev/null 2>&1; then
  session_secret="$(openssl rand -hex 24)"
else
  session_secret="$(python3 - <<'PY'
import secrets
print(secrets.token_hex(24))
PY
)"
fi

python3 - <<PY
import json
from pathlib import Path

Path("$DATA_DIR/settings.json").write_text(
    json.dumps(
        {
            "listen_host": "$listen_host",
            "listen_port": $listen_port,
            "web_host": "0.0.0.0",
            "web_port": $web_port,
            "proxy_mode": "$proxy_mode",
            "proxy_protocol": "${proxy_protocol}",
            "proxy_host": "$proxy_host",
            "proxy_port": $proxy_port,
            "proxy_username": "$proxy_user",
            "proxy_password": "$proxy_pass",
            "api_url": "${api_url:-}",
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
            "bigdata_api_url": "${bigdata_api_url:-}",
            "bigdata_api_token": "${bigdata_api_token:-}",
            "allowed_client_ips": "",
            "session_secret": "$session_secret",
        },
        indent=2,
        ensure_ascii=False,
    ),
    encoding="utf-8",
)
PY

echo "安装虚拟环境..."
python3 -m venv "$BASE_DIR/.venv"
source "$BASE_DIR/.venv/bin/activate"
python -m pip install --upgrade pip >/dev/null
pip install -r "$BASE_DIR/requirements.txt"

echo "初始化数据库并写入管理员..."
DB_PATH="$DATA_DIR/app.db"
PYTHONPATH="$BASE_DIR" python3 - <<PY
from pathlib import Path
from app.db import init_db, get_user, create_user

db_path = "$DB_PATH"
admin_user = "$admin_user"
admin_password = "$admin_password"
db_path = Path(db_path)

init_db(db_path)
if get_user(db_path, admin_user) is None:
    create_user(db_path, admin_user, admin_password)
    print(f"已创建管理员: {admin_user}")
else:
    print(f"管理员已存在: {admin_user}")
PY

# 维持仓库内置启动脚本，避免覆盖成旧逻辑
if [[ ! -x "$BASE_DIR/run.sh" ]]; then
  cat > "$BASE_DIR/run.sh" <<'EOF'
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
EOF
  chmod +x "$BASE_DIR/run.sh"
fi

read -r -p "是否尝试放行 Web 与代理端口（8080/3128）？[y/N]: " open_ports
if [[ "$open_ports" =~ ^[Yy]$ ]]; then
  if command -v ufw >/dev/null 2>&1; then
    sudo ufw allow 8080/tcp || true
    sudo ufw allow 3128/tcp || true
    echo "已尝试通过 ufw 放行 8080/tcp 与 3128/tcp"
  elif command -v firewall-cmd >/dev/null 2>&1; then
    sudo firewall-cmd --zone=public --add-port=8080/tcp --permanent || true
    sudo firewall-cmd --zone=public --add-port=3128/tcp --permanent || true
    sudo firewall-cmd --reload || true
    echo "已尝试通过 firewalld 放行 8080/tcp 与 3128/tcp"
  elif command -v iptables >/dev/null 2>&1; then
    sudo iptables -C INPUT -p tcp --dport 8080 -j ACCEPT || sudo iptables -I INPUT -p tcp --dport 8080 -j ACCEPT
    sudo iptables -C INPUT -p tcp --dport 3128 -j ACCEPT || sudo iptables -I INPUT -p tcp --dport 3128 -j ACCEPT
    echo "已尝试通过 iptables 放行 8080/tcp 与 3128/tcp"
  else
    echo "未检测到 ufw/firewalld/iptables，跳过端口放行"
  fi
fi

read -r -p "是否创建 systemd 服务自动启动？[y/N]: " install_service
if [[ "$install_service" =~ ^[Yy]$ ]] && command -v systemctl >/dev/null 2>&1; then
  cat > /tmp/dpm.service <<EOF
[Unit]
Description=Domain Proxy Manager
After=network.target

[Service]
Type=simple
WorkingDirectory=$BASE_DIR
Environment=PYTHONPATH=$BASE_DIR
ExecStart=$BASE_DIR/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port $web_port
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
  sudo mv /tmp/dpm.service "/etc/systemd/system/${SERVICE_NAME}.service"
  sudo systemctl daemon-reload
  sudo systemctl enable --now "$SERVICE_NAME.service"
  echo "服务已安装并启动: ${SERVICE_NAME}.service"
else
  echo "已生成 run.sh，可直接执行: $BASE_DIR/run.sh"
fi

if command -v hostname >/dev/null 2>&1; then
  host_hint="$(hostname -I 2>/dev/null | awk '{print $1}')"
fi
if [[ -z "${host_hint:-}" ]]; then
  host_hint="YOUR_SERVER_IP"
fi
echo "安装完成。Web 地址: http://${host_hint}:$web_port/login"
