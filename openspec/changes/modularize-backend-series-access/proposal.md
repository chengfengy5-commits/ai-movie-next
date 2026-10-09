# Proposal

## Why

第22批粗剪模块已实现持久化，但剧集访问策略仍由调用方提供。下一批将固定旧源码的作者、团队成员及认领访问规则迁入独立业务模块，使已有粗剪请求能够在同一 Session 内核对真实数据库记录。

## What Changes

- 新增纯领域访问规则、应用服务与读取端口，保留旧规则的错误优先级及中文 detail。
- 新增 SQLAlchemy Core 最小查询投影与仓储，从当前剧集、团队成员和认领人用户名判断访问，不创建连接或 schema，不维护数据。
- 新增显式粗剪策略桥接 callable，可直接注入第22批 app factory；原有142个代码/配置文件全部冻结，不新增 HTTP 方法。
- 对作者、未认领团队、认领者、队长、权限及失效成员建立独立测试，使用临时 SQLite 与原 GET/PUT 完成实际授权链验收。

## Capabilities

### New Capabilities

- `backend-series-access`: 隔离后端从数据库当前记录判断剧集作者、团队与认领访问，并显式适配已有粗剪访问端口。

### Modified Capabilities

无。主规格目录当前为空；第22批粗剪行为与端口不改变。

## Impact

仅在新 backend 包添加 series_access 业务目录及测试；五份规划正常创建，事后文档按白名单追加。固定旧 HEAD `23403806898550a7668a6ee7c0c457315655c39b` 是兼容来源；旧库、生产及前端不改动，不安装依赖。

本批不实现登录、JWT、会话、会员校验、团队管理或认领写入，不授予章节锁，不连接 PostgreSQL、Worker、队列、媒体/provider 或生产，不归档或发布。loopback 身份仍是显式合成实验适配，数据库成员/认领策略则使用本批实际代码；Grillme GPT-5.6 Sol/xhigh 独立外审仍单列未执行。
