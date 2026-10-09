# Proposal

## Why

新版已完成个人粗剪编排的前端切片，但新目录没有真实后端业务模块，现有 fixture 不能证明后端模块化。第一批从已固定来源的个人粗剪 GET/PUT 入手，将业务规则、应用事务、持久化和 HTTP 分开，为后续接入真实身份、权限及 PostgreSQL 建立可独立验证的模块。

## What Changes

- 新增独立 Python/FastAPI 后端包，以个人制作业务下的粗剪模块提供纯投影/完整集合规则、应用用例与工作单元端口、SQLAlchemy 数据访问、严格 HTTP 模型和 app factory。
- 保留已有 `GET/PUT /api/chapters/{chapter_id}/rough-cut` 的响应、错误优先级、本人章节私有状态、revision CAS 和完整镜头集合校验；不增加媒体 CAS、不改变前端接口。
- SQLAlchemy adapter 实际读取现有章节/素材投影并持久化粗剪，保留唯一键和条件更新；一次写用例统一提交或回滚，GET 不显式提交。生产代码不建表、反射、加载环境配置或启动旧应用。
- 显式注入可信身份/有效会员和同事务剧集访问策略；任一安全或数据库端口未配置时返回 503，且不创建 Session。测试替身只在测试及隔离验收中使用，不代表 JWT、会员、团队认领权限已迁移。
- 使用独立临时 SQLite 文件、不同连接、真实 FastAPI ASGI 和本机 loopback HTTP 验证读写、私有性、失败回滚与陈旧写入 CAS；明确 SQLite 和 SQL 编译检查不能证明 PostgreSQL 行锁或生产并发。

## Capabilities

### New Capabilities

- `backend-personal-rough-cut`: 新目录内具有独立分层、事务和可注入安全边界的个人粗剪后端模块；迁入固定旧契约，而非改变其业务规则。

### Modified Capabilities

无。当前主规格清单为空；前 21 个 change 及其验收历史保持原文。

## Impact

- 新增 `backend/` 的源包、依赖清单、测试及使用说明，新增后端模块边界文档；收尾仅追加根 README 与兼容清单第 22 批说明。现有前端、fixture、依赖、全部 21 个旧 change 和来源快照冻结。
- 本批交付 app factory 和可接线模块，未交付生产全站启动器、真实认证/权限适配器、数据库迁移、Worker、播放/导出或其他后端业务模块。真实 PostgreSQL、队列、R2、provider、付费调用与生产联调均不运行。
- 只在 `haoai-next` 写入；旧库、线上、真实数据和配置保持不动。不提交、推送、部署或归档；指定 GPT-5.6 Sol/xhigh Grillme 仍独立离线未执行，不由其他审查替代。
