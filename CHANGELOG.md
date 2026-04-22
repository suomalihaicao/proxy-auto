# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added
- 
### Changed
- 
### Fixed
- 

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
