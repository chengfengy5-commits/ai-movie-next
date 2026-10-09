# Proposal

## Why

新后端已经把认证、剧集访问、剧集/章节/分镜目录、个人制作记录和粗剪拆成边界清楚的模块。角色、场景、道具的完整目录读写及分镜素材的新增、图片更新和删除仍属于旧单体。迁移这十五个既有方法，才能让素材维护复用当前身份、访问策略与个人媒体状态协调，同时保留旧接口的字段语义、错误优先级和事务阶段。

## What Changes

- 新增 `haoai_backend.asset_data`，实现角色、场景、道具各四个方法，以及分镜素材 POST/PUT/DELETE 三个方法；现有 `GET /api/series/{series_id}/storyboard-assets` 继续由 `series_data` 提供。
- 复现既有 create/update 的 DTO 差异、字段省略与 null 语义、角色音频地址特例、响应 aliases 解析、命名 listener 和 no-op 时间戳行为。
- 普通素材删除时精确清理同剧章节中目标类别的引用，并在章节来源 DML 前调用既有个人媒体协调器；分镜图片身份变化和删除也在同一业务事务中协调跨用户记录。
- 通过显式 Core 仓储和应用 ports 接入 `app.py`，保留旧四业务方法的显式访问策略要求。若本 change 完成，统一 factory 的方法数预期由 27 增至 42；这是规划目标，不表示目前已经实现或验收。
- 增加独立的领域、命名、引用、应用、持久化、来源阶段、HTTP 和边界测试。

## Capabilities

### New Capabilities

- `backend-asset-data`：角色、场景、道具和分镜素材写入接口及其旧行为兼容要求。

### Modified Capabilities

无。基线中的主规格清单为空；本 change 新增一个能力规格，不改既有变更历史。

## Impact

- 唯一固定来源为 Git commit `23403806898550a7668a6ee7c0c457315655c39b`，限于本 change 的只读来源清单和行区间；不以共享旧 checkout 的现场源码替代固定来源。
- 旧代码仅允许修改 `backend/src/haoai_backend/app.py` 与五个 factory/边界测试。新增模块与专用测试共二十一个路径；精确白名单和职责见 `design.md`。
- 保留现有 `series_data` 分镜目录 GET；不新增上传、下载、图片抓取、媒体 CAS、生成/融合、模糊去重、孤儿检查、任务/账务、provider、Worker、队列或前端编辑能力。
- 规划阶段只新增本 change 的五个 OpenSpec 文件。实现、测试、HTTP/TypeScript、文档收尾和外部复核均是后续独立门槛；不代表生产数据库迁移、真实 PostgreSQL 并发验收或发布。
