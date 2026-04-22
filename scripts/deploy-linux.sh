#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BASE_DIR"
DATA_DIR="$BASE_DIR/data"
SETTINGS_FILE="$DATA_DIR/settings.json"
DB_PATH="$DATA_DIR/app.db"
ENV_TOOLS_DIR="$BASE_DIR/env_tools"
ENV_TOOLS_BIN="$ENV_TOOLS_DIR/bin"
LOCAL_PYTHON_BIN="$ENV_TOOLS_BIN/python3"
GET_PIP_URL="https://bootstrap.pypa.io/get-pip.py"
VENV_DIR="$BASE_DIR/.venv"
PIP_CACHE_DIR="$ENV_TOOLS_DIR/pip-cache"
LOG_TAG="[deploy]"
PYTHON_REQ_MAJOR=3
PYTHON_REQ_MINOR=10
DEPLOY_MODE="start_only"

mkdir -p "$DATA_DIR" "$ENV_TOOLS_DIR" "$ENV_TOOLS_BIN" "$PIP_CACHE_DIR"

log() {
  echo "$LOG_TAG $*" >&2
}

usage() {
  cat <<'EOF'
[deploy] 用法:
  ./scripts/deploy-linux.sh [--start-only|--interactive]

--start-only     仅启动服务（默认），不做交互式配置。
--interactive    执行原有交互式部署流程，可一次性写入 settings 与数据库。
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --start-only|--start|--no-interactive)
      DEPLOY_MODE="start_only"
      shift
      ;;
    --interactive)
      DEPLOY_MODE="interactive"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      log "参数不支持: $1"
      usage
      exit 1
      ;;
  esac
done

log "启动部署前环境检查..."
log "Linux 最低要求: Bash + Python ${PYTHON_REQ_MAJOR}.${PYTHON_REQ_MINOR}+（含 venv）+ curl 或 wget + git（可选）"
log "项目工具目录: $ENV_TOOLS_DIR（工具、下载器与 pip 缓存将放在此目录）"

require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    log "缺少命令: $cmd"
    return 1
  fi
}

python_acceptable() {
  local target="$1"
  local version major minor
  if [[ ! -x "$target" ]]; then
    return 1
  fi
  version="$("$target" -V 2>&1 | awk '{print $2}' | cut -d. -f1,2)"
  if [[ -z "${version}" ]]; then
    return 1
  fi
  major="${version%%.*}"
  minor="${version#*.}"
  if ! [[ "$major" =~ ^[0-9]+$ && "$minor" =~ ^[0-9]+$ ]]; then
    return 1
  fi
  if (( major < PYTHON_REQ_MAJOR )); then
    return 1
  fi
  if (( major == PYTHON_REQ_MAJOR && minor < PYTHON_REQ_MINOR )); then
    return 1
  fi
  return 0
}

resolve_venv_python() {
  local venv_dir="$1"
  local candidates=(
    "$venv_dir/bin/python"
    "$venv_dir/bin/python3"
    "$venv_dir/Scripts/python.exe"
    "$venv_dir/Scripts/python3.exe"
  )
  local candidate
  for candidate in "${candidates[@]}"; do
    if [[ -x "$candidate" ]]; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

resolve_venv_pip() {
  local venv_dir="$1"
  local candidates=(
    "$venv_dir/bin/pip"
    "$venv_dir/bin/pip3"
    "$venv_dir/Scripts/pip.exe"
    "$venv_dir/Scripts/pip3.exe"
  )
  local candidate
  for candidate in "${candidates[@]}"; do
    if [[ -x "$candidate" ]]; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

python_venv_ok() {
  local target="$1"
  "$target" -m venv --help >/dev/null 2>&1
}

python_has_pip() {
  local py="$1"
  "$py" -m pip --version >/dev/null 2>&1
}

record_python_to_env_tools() {
  local source_python="$1"
  if ln -sf "$source_python" "$LOCAL_PYTHON_BIN" 2>/dev/null; then
    return 0
  fi

  if [[ ! -x "$source_python" ]]; then
    return 1
  fi

  cat > "$LOCAL_PYTHON_BIN" <<EOF
#!/usr/bin/env sh
exec "$source_python" "\$@"
EOF
  chmod +x "$LOCAL_PYTHON_BIN"
  return 0
}

download_file() {
  local url="$1"
  local target="$2"

  if command -v curl >/dev/null 2>&1; then
    curl -fsSL --max-time 20 "$url" -o "$target"
    return 0
  fi

  if command -v wget >/dev/null 2>&1; then
    wget -q -O "$target" "$url"
    return 0
  fi

  return 1
}

ensure_venv_pip() {
  local venv_dir="$1"
  local python_bin="$2"
  local pip_bin=""
  local get_pip_script="$ENV_TOOLS_DIR/get-pip.py"

  if [[ ! -x "$python_bin" ]]; then
    log "虚拟环境解释器异常: $python_bin"
    return 1
  fi

  pip_bin="$(resolve_venv_pip "$venv_dir")"
  if [[ -n "$pip_bin" ]] && python_has_pip "$python_bin"; then
    echo "$pip_bin"
    return 0
  fi

  log "虚拟环境缺少 pip，尝试通过 ensurepip 修复..."
  if "$python_bin" -m ensurepip --upgrade >/dev/null 2>&1; then
    if python_has_pip "$python_bin"; then
      pip_bin="$(resolve_venv_pip "$venv_dir")"
      if [[ -n "$pip_bin" ]]; then
        echo "$pip_bin"
        return 0
      fi
    fi
  fi

  log "ensurepip 修复失败，准备从官方脚本下载补齐 pip（保存到 env_tools）..."
  mkdir -p "$ENV_TOOLS_DIR"
  download_file "$GET_PIP_URL" "$get_pip_script" || {
    log "无法下载 get-pip.py，请检查网络：$GET_PIP_URL"
    return 1
  }
  "$python_bin" "$get_pip_script" --no-warn-script-location >/dev/null
  if python_has_pip "$python_bin"; then
    pip_bin="$(resolve_venv_pip "$venv_dir")"
    if [[ -n "$pip_bin" ]]; then
      echo "$pip_bin"
      return 0
    fi
  fi

  log "pip 安装后仍不可用，请手动检查当前 Python 环境"
  return 1
}

ensure_tool_cmd() {
  local cmd="$1"
  local package_name="${2:-$1}"
  if command -v "$cmd" >/dev/null 2>&1; then
    return 0
  fi

  if command -v apt-get >/dev/null 2>&1; then
    log "未检测到 ${cmd}，尝试 apt 安装 ${package_name}"
    if command -v sudo >/dev/null 2>&1; then
      sudo apt-get update && sudo apt-get install -y "$package_name"
    else
      apt-get update && apt-get install -y "$package_name"
    fi
    return 0
  fi

  if command -v dnf >/dev/null 2>&1; then
    log "未检测到 ${cmd}，尝试 dnf 安装 ${package_name}"
    if command -v sudo >/dev/null 2>&1; then
      sudo dnf install -y "$package_name"
    else
      dnf install -y "$package_name"
    fi
    return 0
  fi

  if command -v yum >/dev/null 2>&1; then
    log "未检测到 ${cmd}，尝试 yum 安装 ${package_name}"
    if command -v sudo >/dev/null 2>&1; then
      sudo yum install -y "$package_name"
    else
      yum install -y "$package_name"
    fi
    return 0
  fi

  if command -v apk >/dev/null 2>&1; then
    log "未检测到 ${cmd}，尝试 apk 安装 ${package_name}"
    if command -v sudo >/dev/null 2>&1; then
      sudo apk add --no-cache "$package_name"
    else
      apk add --no-cache "$package_name"
    fi
    return 0
  fi

  if command -v pacman >/dev/null 2>&1; then
    log "未检测到 ${cmd}，尝试 pacman 安装 ${package_name}"
    if command -v sudo >/dev/null 2>&1; then
      sudo pacman -Sy --noconfirm "$package_name"
    else
      pacman -Sy --noconfirm "$package_name"
    fi
    return 0
  fi

  if command -v zypper >/dev/null 2>&1; then
    log "未检测到 ${cmd}，尝试 zypper 安装 ${package_name}"
    if command -v sudo >/dev/null 2>&1; then
      sudo zypper install -y "$package_name"
    else
      zypper install -y "$package_name"
    fi
    return 0
  fi

  log "未识别到可用包管理器，无法自动安装 ${cmd}"
  return 1
}

ensure_net_tools() {
  if command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1; then
    return 0
  fi

  log "未检测到 curl/wget，尝试自动安装 curl..."
  ensure_tool_cmd curl curl || return 1
}

ensure_python_runtime() {
  if python_acceptable "$LOCAL_PYTHON_BIN"; then
    PYTHON_BIN="$LOCAL_PYTHON_BIN"
    log "使用项目工具目录 Python: $PYTHON_BIN"
    return 0
  fi

  if command -v python3 >/dev/null 2>&1; then
    local sys_py
    sys_py="$(command -v python3)"
    if python_acceptable "$sys_py"; then
      if ! python_venv_ok "$sys_py"; then
        log "系统 Python 已存在，但缺少 venv 模块，尝试自动补齐"
        ensure_tool_cmd python3-venv python3-venv || true
      fi
      if python_venv_ok "$sys_py"; then
        PYTHON_BIN="$sys_py"
        record_python_to_env_tools "$sys_py"
        log "已识别系统 Python: $PYTHON_BIN（已记录到 $LOCAL_PYTHON_BIN）"
        return 0
      fi
    else
      log "系统 Python 版本低于 ${PYTHON_REQ_MAJOR}.${PYTHON_REQ_MINOR}+，尝试更新..."
      ensure_tool_cmd python3 python3 || true
    fi
  fi

  log "未检测到可用 Python ${PYTHON_REQ_MAJOR}.${PYTHON_REQ_MINOR}+，尝试自动安装"
  if command -v python3 >/dev/null 2>&1; then
    :
  else
    if ! ensure_tool_cmd python3 python3; then
      log "系统软件源安装 python3 失败"
      return 1
    fi
  fi
  if ! ensure_tool_cmd python3-venv python3-venv; then
    log "系统缺少 venv 支持，后续尝试依赖系统自带 venv"
  fi

  if ! command -v python3 >/dev/null 2>&1; then
    log "无法在系统中安装或发现 python3"
    return 1
  fi

  if python_acceptable "$(command -v python3)"; then
    local sys_py
    sys_py="$(command -v python3)"
    if python_venv_ok "$sys_py"; then
      record_python_to_env_tools "$sys_py"
      PYTHON_BIN="$sys_py"
      log "Python 安装完成，使用: $PYTHON_BIN"
      return 0
    fi
    log "python3 安装后仍缺少 venv"
    return 1
  fi

  log "系统 Python 版本仍低于 3.10，建议升级到 3.10+"
  return 1
}

detect_local_ips() {
  local ips
  if command -v ip >/dev/null 2>&1; then
    ips="$(ip -4 -o addr show scope global 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | tr '\n' ' ' | sed 's/  */ /g' | xargs)"
  fi
  if [[ -z "${ips:-}" && -f /etc/hostname ]]; then
    ips="$(hostname -I 2>/dev/null | sed 's/  */ /g' | xargs || true)"
  fi
  if [[ -z "${ips:-}" ]]; then
    ips="未检测到 IPv4"
  fi
  echo "$ips"
}

detect_public_ip() {
  local ip=""
  local endpoints=(
    "https://api.ipify.org"
    "https://ifconfig.me/ip"
    "https://icanhazip.com"
  )
  local target
  for target in "${endpoints[@]}"; do
    if command -v curl >/dev/null 2>&1; then
      ip="$(curl -fsS --max-time 4 "$target" 2>/dev/null | tr -d '[:space:]' || true)"
    elif command -v wget >/dev/null 2>&1; then
      ip="$(wget -qO- --timeout=4 "$target" 2>/dev/null | tr -d '[:space:]' || true)"
    fi
    if [[ -n "$ip" ]]; then
      echo "$ip"
      return 0
    fi
  done
  echo "未检测到公网 IP"
}

probe_mirror_latency() {
  local mirror="$1"
  local stats
  local code time_cost
  if command -v curl >/dev/null 2>&1; then
    stats="$(curl -L -m 6 --connect-timeout 4 -s -o /dev/null -w "%{http_code} %{time_total}" "${mirror}/" || true)"
    code="$(awk '{print $1}' <<< "$stats")"
    time_cost="$(awk '{print $2}' <<< "$stats")"
    if [[ "$code" == "200" || "$code" == "301" || "$code" == "302" || "$code" == "307" || "$code" == "308" ]]; then
      echo "$time_cost"
      return 0
    fi
  elif command -v wget >/dev/null 2>&1; then
    if wget --timeout=6 -q --spider "${mirror}/" >/dev/null 2>&1; then
      echo "1"
      return 0
    fi
  fi
  return 1
}

select_fastest_index() {
  local mirrors=(
    "https://mirrors.aliyun.com/pypi/simple"
    "https://pypi.org/simple"
  )
  local selected="${mirrors[1]}"
  local best=999999
  local mirror latency
  local valid=0

  for mirror in "${mirrors[@]}"; do
    if latency="$(probe_mirror_latency "$mirror")"; then
      if awk "BEGIN{exit !($latency < $best)}"; then
        best="$latency"
        selected="$mirror"
      fi
      log "镜像可用: $mirror | 延迟=${latency}s"
      valid=1
    else
      log "镜像不可达: $mirror"
    fi
  done

  if [[ "$valid" -eq 0 ]]; then
    log "镜像探测失败，回退官方源 https://pypi.org/simple"
    selected="https://pypi.org/simple"
  else
    log "已选择最快 pip 源: $selected"
  fi
  echo "$selected"
}

build_settings() {
  local proxy_mode="$1"
  local proxy_protocol="$2"
  local proxy_host="$3"
  local proxy_port="$4"
  local proxy_username="$5"
  local proxy_password="$6"
  local api_url="$7"
  local bigdata_api_url="$8"
  local bigdata_api_token="$9"
  local listen_host="$10"
  local listen_port="$11"
  local web_port="$12"
  local session_secret="$13"

  PYTHONPATH="$BASE_DIR" PY_LISTEN_HOST="$listen_host" PY_LISTEN_PORT="$listen_port" \
  PY_WEB_HOST="0.0.0.0" PY_WEB_PORT="$web_port" PY_PROXY_MODE="$proxy_mode" \
  PY_PROXY_PROTOCOL="$proxy_protocol" PY_PROXY_HOST="$proxy_host" PY_PROXY_PORT="$proxy_port" \
  PY_PROXY_USER="$proxy_username" PY_PROXY_PASS="$proxy_password" PY_API_URL="$api_url" \
  PY_BIGDATA_API_URL="$bigdata_api_url" PY_BIGDATA_API_TOKEN="$bigdata_api_token" \
  PY_SESSION_SECRET="$session_secret" "$VENV_PYTHON" - <<'PY'
from pathlib import Path
import json
import os

path = Path(os.environ["SETTINGS_FILE"])
path.parent.mkdir(parents=True, exist_ok=True)

cfg = {
    "listen_host": os.environ["PY_LISTEN_HOST"],
    "listen_port": int(os.environ["PY_LISTEN_PORT"]),
    "web_host": os.environ["PY_WEB_HOST"],
    "web_port": int(os.environ["PY_WEB_PORT"]),
    "proxy_mode": os.environ["PY_PROXY_MODE"],
    "proxy_protocol": os.environ["PY_PROXY_PROTOCOL"],
    "proxy_host": os.environ["PY_PROXY_HOST"],
    "proxy_port": int(os.environ["PY_PROXY_PORT"]),
    "proxy_username": os.environ["PY_PROXY_USER"],
    "proxy_password": os.environ["PY_PROXY_PASS"],
    "api_url": os.environ["PY_API_URL"],
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
    "bigdata_api_url": os.environ["PY_BIGDATA_API_URL"],
    "bigdata_api_token": os.environ["PY_BIGDATA_API_TOKEN"],
    "allowed_client_ips": "",
    "session_secret": os.environ["PY_SESSION_SECRET"],
}

Path(path).write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
}

mkdir -p "$PIP_CACHE_DIR"
export PIP_CACHE_DIR

log "开始部署（项目：$BASE_DIR）"
log "内网 IP: $(detect_local_ips)"
log "公网 IP: $(detect_public_ip)"
log "环境检查通过后将把依赖和工具集中到: $ENV_TOOLS_DIR"

if ! ensure_net_tools; then
  log "未检测到可用 curl/wget，且无法自动安装"
  exit 1
fi

if ! ensure_python_runtime; then
  log "未能满足 Python ${PYTHON_REQ_MAJOR}.${PYTHON_REQ_MINOR}+ 运行要求"
  exit 1
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
  log "Python 运行路径异常: $PYTHON_BIN"
  exit 1
fi
require_cmd git || log "未检测到 git（非必须，但不影响本脚本）"

if [[ -d "$VENV_DIR" ]]; then
  VENV_PYTHON="$(resolve_venv_python "$VENV_DIR" || true)"
  if [[ -n "${VENV_PYTHON:-}" ]] && ! "$VENV_PYTHON" -V >/dev/null 2>&1; then
    log "现有虚拟环境异常，准备重建 $VENV_DIR ..."
    rm -rf "$VENV_DIR"
  fi
fi

if [[ ! -d "$VENV_DIR" ]]; then
  log "未检测到现有虚拟环境，正在创建 $VENV_DIR ..."
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

VENV_PYTHON="$(resolve_venv_python "$VENV_DIR" || true)"
if [[ -z "${VENV_PYTHON:-}" ]]; then
  log "虚拟环境解释器异常: $VENV_DIR/.venv"
  exit 1
fi

if ! PYTHON_BIN_FOR_PIP="$(ensure_venv_pip "$VENV_DIR" "$VENV_PYTHON")"; then
  log "虚拟环境 pip 不可用，准备重建并重试一次"
  rm -rf "$VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
  VENV_PYTHON="$(resolve_venv_python "$VENV_DIR" || true)"
  if [[ -z "${VENV_PYTHON:-}" ]]; then
    log "重建后仍未检测到虚拟环境解释器: $VENV_DIR/.venv"
    exit 1
  fi
  if ! PYTHON_BIN_FOR_PIP="$(ensure_venv_pip "$VENV_DIR" "$VENV_PYTHON")"; then
  log "未能修复虚拟环境里的 pip，退出部署"
  exit 1
  fi
fi
VENV_PIP="$PYTHON_BIN_FOR_PIP"

PIP_INDEX="$(select_fastest_index)"
export SETTINGS_FILE

log "安装依赖到项目虚拟环境（cache: $PIP_CACHE_DIR）"
"$VENV_PIP" install --disable-pip-version-check --no-input --upgrade pip -i "$PIP_INDEX"
"$VENV_PIP" install --disable-pip-version-check --no-input -i "$PIP_INDEX" -r "$BASE_DIR/requirements.txt"

if [[ "$DEPLOY_MODE" == "interactive" ]]; then
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
    single_ip|api|bigdata_api|direct) ;;
    *)
      log "不支持的模式: $proxy_mode"
      exit 1
      ;;
  esac

  if [[ "$proxy_mode" == "single_ip" ]]; then
    read -r -p "单 IP 代理协议 [http/socks5] (回车默认 http): " protocol_input
    proxy_protocol="${protocol_input:-http}"
    if [[ "$proxy_protocol" != "http" && "$proxy_protocol" != "socks5" ]]; then
      log "不支持的协议: $proxy_protocol"
      exit 1
    fi
    read -r -p "单 IP 代理地址 (例如: 127.0.0.1): " proxy_host
    read -r -p "单 IP 代理端口 (例如: 8080): " proxy_port
    read -r -p "单 IP 账号（可空）: " proxy_user
    read -r -s -p "单 IP 密码（可空）: " proxy_pass
    echo
  fi

  if [[ "$proxy_mode" == "api" ]]; then
    read -r -p "API 地址（必填）: " api_url
    if [[ -z "$api_url" ]]; then
      log "API 模式请填写 api_url"
      exit 1
    fi
  elif [[ "$proxy_mode" == "bigdata_api" ]]; then
    read -r -p "BigData API 地址: " bigdata_api_url
    read -r -p "API 地址（可空，作为 fallback）: " api_url
    read -r -p "BigData Token（可空）: " bigdata_api_token
    if [[ -z "$api_url" && -z "$bigdata_api_url" ]]; then
      log "BigData 模式至少需要填写 bigdata_api_url 或 api_url"
      exit 1
    fi
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
    session_secret="$("$VENV_PYTHON" - <<'PY'
import secrets
print(secrets.token_hex(24))
PY
)"
  fi

  build_settings \
    "$proxy_mode" "$proxy_protocol" "$proxy_host" "$proxy_port" "$proxy_user" "$proxy_pass" \
    "$api_url" "$bigdata_api_url" "$bigdata_api_token" \
    "$listen_host" "$listen_port" "$web_port" "$session_secret"

  log "初始化数据库并创建管理员账号..."
  PROXY_ADMIN_USER="$admin_user" \
  PROXY_ADMIN_PASSWORD="$admin_password" \
DB_PATH="$DB_PATH" \
"$VENV_PYTHON" - <<'PY'
import os
from app.db import init_db, get_user, create_user

db_path = os.environ["DB_PATH"] if "DB_PATH" in os.environ else os.path.join("data", "app.db")
admin_user = os.environ["PROXY_ADMIN_USER"]
admin_password = os.environ["PROXY_ADMIN_PASSWORD"]

init_db(db_path)
if get_user(db_path, admin_user) is None:
    create_user(db_path, admin_user, admin_password)
    print(f"已创建管理员: {admin_user}")
else:
    print(f"管理员已存在: {admin_user}")
PY

  read -r -p "部署完成后立即启动服务？[Y/n]: " start_now
  if [[ "${start_now:-Y}" =~ ^[Nn]$ ]]; then
    log "已跳过启动，后续可执行: bash scripts/restart-public-8080.sh"
  else
    bash "$BASE_DIR/scripts/restart-public-8080.sh"
  fi

  log "部署完成"
  log "Web 地址: http://$(detect_public_ip):$web_port/login"
  log "默认登录: ${admin_user} / ${admin_password}"
else
  log "部署完成后将直接启动服务（非交互模式）。参数/代理配置请在 Web 面板配置。"
  bash "$BASE_DIR/scripts/restart-public-8080.sh"
fi
