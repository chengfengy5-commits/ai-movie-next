# Proposal

## Why

旧版剧本工作台使用多个大型原生 JavaScript 模块，页面状态、认证、缓存与生产调用交织，不利于逐步迁移和审查。先在独立仓库建立 React 工作台壳、登录与只读剧集列表，用冻结接口契约验证技术路线，再迁移章节、素材、个人辅助与任务。

## What Changes

- 新建 React + TypeScript + Vite 前端，提供登录、工作台壳与剧集列表四类筛选：全部、我创建的、团队剧集、我认领的。
- 默认使用隔离 mock；显式 API 模式仅允许同源相对 `/api` 或专门配置的隔离 loopback fixture，支持 JSON 登录、Bearer 会话恢复与只读列表。
- 保留 `Series.user_id` 的个人持有含义、`team_id` 的共享含义，以及认领人和 `can_enter` 字段；列表保留 API 返回顺序，每次展示 20 条。
- 区分登录失败、401 会话失效、403 会员限制、网络/服务端错误与有效空列表。新版列表失败不读取旧版缓存。
- 本切片不展示写剧集、分享、认领、剧集详情、章节或生成入口；保留相应后续契约记录。
- 建立冻结来源清单、复制白名单、模块与 API 兼容说明，以及分层验证任务。撤回 Go/Gin 占位方向，保留历史记录。

## Capabilities

### New Capabilities

- `react-workspace`: 隔离运行的 React 工作台壳、兼容认证和只读剧集列表，含错误状态、筛选、分页与调用边界。

### Modified Capabilities

无。新仓库的主规格目录尚无现有 capability；旧仓库规格作为兼容证据，不直接复制为已实现主规格。

## Impact

- 新仓库新增前端工程、mock/HTTP 适配层、接口类型及验证材料；不改旧仓库。
- 首切片 API 限于 `POST /api/auth/login`、`GET /api/auth/me`、`GET /api/series`。认证会更新会话，真实登录必须使用隔离后端；本地 HTTP fixture 验证不能称为真实 FastAPI/PostgreSQL 回归。
- 后续继续使用 Python + FastAPI、PostgreSQL、R2 和独立 Worker 的模块化单体方向；本切片不启动后端、数据库或 Worker，不接真实队列、模型、存储或账务服务，不引入 Go、Redis、微服务或新队列。
- OpenSpec 管理变更；Grillme 固定 GPT-5.6 Sol / xhigh。目前本机 Grillme 不可连接，记录离线未完成，不降档或冒充通过。本轮按既有明确授权在规划完成后进入实现；不推送、不部署。
