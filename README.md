# Domain Proxy Manager

一个带登录的轻量代理网关管理器，用于在 Linux/Windows 上统一处理“部分域名走上游代理、其他域名直连”的场景。

项目名：proxy-auto  
默认演示管理员账号：`admin` / `admin123`  

## 特性

- Web 管理（FastAPI + 登录页 + Cookie 会话）
- 域名规则管理
  - `DOMAIN`（`exact`）
  - `DOMAIN-SUFFIX`（`suffix`）
  - `DOMAIN-KEYWORD`（`keyword`）
- 代理转发模式
  - `single_ip`（单 IP 模式，兼容旧的 `http`/`socks5`）
  - `api`（通过外部 API 获取动态上游）
  - `bigdata_api`（内置 BigData API 模式）
  - `direct`（仅默认直连）
- 一键部署脚本 + systemd 可选
- 迁移打包/恢复脚本（包含数据库与配置）

## 一键安装

```bash
cd /root/domain-proxy-manager
chmod +x scripts/install.sh
./scripts/install.sh
```
安装可选步骤里有“放行端口”，用于云服务器外网无法访问 8080/3128 时自动尝试放行。

## 访问

- Web 地址: `http://<server_ip>:<web_port>/login`
- 默认首次登录账号来自安装时填写的管理员账号

## 代理与规则

- 在规则列表里添加要走代理的域名：
  - `exact`: `api.example.com`
  - `suffix`: `example.com`
  - `keyword`: `payment`
- 本工具监听一个本地 HTTP 代理端口（默认 `3128`），你的业务应用把出站代理配置到这个端口即可。
- 规则命中时走上游代理（含 API/BIGDATA 动态上游），否则直连。
- Web 管理支持 IP 白名单（空表示允许全部，支持 `IP` 或 `CIDR`，如 `1.2.3.4,10.0.0.0/24`）。
- 修改 Web 配置（监听地址/端口或上游代理参数）后建议重启服务。

## 文件结构

- `app/main.py`：Web 后台 + 启停代理网关
- `app/proxy.py`：HTTP CONNECT/HTTP 转发实现
- `app/db.py`：规则和用户数据库
- `app/config.py`：监听、端口、上游代理配置
- `data/app.db`：规则与管理员数据（运行后自动创建）
- `data/settings.json`：运行时配置（安装生成）
- `scripts/install.sh`：一键安装脚本
- `scripts/fix-access.sh`：外网访问排查脚本（若创建）
- `scripts/package-migration.sh`：导出迁移包
- `scripts/restore-migration.sh`：恢复迁移包

## 迁移

### 导出

```bash
./scripts/package-migration.sh [output_file]
```

### 恢复

```bash
./scripts/restore-migration.sh migration-file.tar.gz [target_dir]
```

## 手动启动（未安装 systemd）

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export PYTHONPATH=/root/domain-proxy-manager
uvicorn app.main:app --host 0.0.0.0 --port <web_port>
```
