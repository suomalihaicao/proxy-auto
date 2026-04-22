# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added
- Windows 一键部署脚本支持 `--start-only` 与 `--interactive` 参数，默认行为调整为非交互启动，配置由 Web 面板完成。
- `setup.sh` 文档与参数透传补齐，默认启动不再阻塞在交互式代理配置。
### Changed
- `deploy-windows.ps1` 新增默认非交互模式与公共设置兜底，减少首次部署在 8080/3128 拉起失败时人工误操作。
- Windows 默认启动流程改为新建终端窗口运行服务，并尝试自动打开 Web 登录页。
### Fixed
- Windows 非交互部署时若未提供代理参数不再要求输入 `Upstream proxy mode`，可直接拉起 Web 服务。

## [1.0.3] - 2026-04-22

### Added
- Windows 安装器支持 `--start-only/--interactive` 两种部署模式。

### Changed
- Web 启动入口与 `setup.sh` 提示统一为“默认非交互”。

### Fixed
- 修复 Windows Git Bash 部署场景下交互式部署参数会拦截自动化启动的问题。

## [1.0.2] - 2026-04-22

### Added
- `setup.sh` 支持在 `Git Bash/MINGW/MSYS/CYGWIN` 下自动分流到 `deploy-windows.ps1`，避免误走 Linux 部署逻辑。
- Windows 安装器新增 Python 安装源测速与选择，优先下载可达且响应更快的源到 `env_tools\python`。

### Changed
- Linux 部署脚本增强虚拟环境解释器/脚本路径识别（兼容 `bin` 与 `Scripts` 下常见命令名），降低不同运行环境误判概率。
- Linux 部署在首次检测到虚拟环境 pip 缺失时，自动重建虚拟环境后重试修复逻辑。

### Fixed
- 修复 `./setup.sh` 在 Windows Git Bash 场景下误调用 Linux 流程导致 `.venv/bin/python` 缺失并直接退出的问题。
- 修复 `deploy-windows.ps1` 在部分 PowerShell 环境因 UTF-8 中文内容导致解析报错的问题（字符串改为兼容 ASCII，避免部署中断）。

## [1.0.1] - 2026-04-22

### Added
- 部署入口 `deploy-linux.sh` 支持默认非交互启动模式（`--start-only`），不再要求在部署时填写代理参数。
- 支持 `--interactive` 模式用于一次性重建 `settings.json` 与管理员信息。
- `restart-public-8080.sh` 新增启动前默认管理员兜底逻辑，未登录环境也可自动补齐 `admin/admin123`。
- 文档更新，明确启动与参数化部署行为。

### Changed
- `restart-public-8080.sh` 移除硬编码项目路径，改为脚本路径动态定位。
- `restart-public-8080.sh` 不再强制覆盖已配置的 `web_host/web_port`，保留 Web 面板配置。
- `deploy-linux.sh` 支持无参数直接启动服务，减少重复配置步骤。

### Fixed
- 部署/启动流程中的健康检查超时提示更合理，避免误判进程退出状态。
- 依赖环境和 pip 修复逻辑与现有自动化流程链路对齐（在前面版本已补齐后续继续沿用）。

## [1.0.0] - 2026-04-22

- 首次公开发布版本（基础代理规则、分组管理、登录管理与部署脚本）。
