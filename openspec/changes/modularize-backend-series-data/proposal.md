# Proposal

## Why

新目录已经具有认证、剧集访问策略和个人粗剪/制作记录后端，但剧集、章节与分镜来源数据的完整读写仍只存在于旧单体。迁移这十二个方法，才能让真实认证、来源编辑与个人制作状态在同一可运行的新后端形成业务闭环，并保持原有权限和分阶段持久化行为。

## What Changes

- 新增独立 `series_data` 模块，覆盖剧集列表/详情/创建/更新/删除、章节列表/排序/创建/更新/删除、删除分镜和分镜素材目录共十二个既有 HTTP 方法；统一 factory 从十五扩为二十七个方法。
- 保留旧 DTO、默认值、标准 422、错误优先级、列表与详情的认领字段差异、普通访问和删除权限差异，以及章顺序、持有者锁续期、聊天映射与资产清理规则。
- 用领域规则、应用 ports、SQLAlchemy Core 仓储与显式来源写入协调替代这一范围的单体/ORM hook 依赖。按真实来源变更阶段，在同一业务 Session 的 DML 前复用已迁移 notes 协调器，保留媒体版本、墓碑和同章所有用户认可撤销；不把多次提交压成一次事务。
- 删除章/剧时保留原级联与真实账单外键拒绝/回滚，不删除金融历史，不新增任务控制、计费、队列或供应商行为。
- 以临时 SQLite、ASGI、独立 loopback 及冻结 TypeScript parser 验证完整业务；PostgreSQL 只编译 SQL，不宣称真实生产联调或完整前端验收。

## Capabilities

### New Capabilities

- `backend-series-data`：新目录中完整十二方法的剧集/章节/分镜来源数据模块、明确配置与权限边界、分阶段来源写入和个人制作状态协调。

### Modified Capabilities

无。实际 `openspec list --specs --json` 的主规格库存为空；既有 change 与其历史规格不修改。

## Impact

- 来源固定为旧 commit `23403806898550a7668a6ee7c0c457315655c39b`。路线、DTO 与阶段分析见本批 `source-lifecycle-analysis-01.json`、`media-write-design-01.json`；旧函数名为 `delete_frame_in_chapter`。
- 旧代码只允许修改 `backend/src/haoai_backend/app.py` 和五个 factory/路由边界测试；这些测试仅更新二十七方法期望与新接线边界，不删减原业务和冷导入哨兵。新模块及专用测试的精确路径在 design 中冻结。
- 基线 368 路径、197 代码/配置/参考/工具、191 受保护旧代码、153 历史、358 不可变文件和 45 固定 Git 来源必须保全。认证、shared identity、普通 access、notes、rough-cut、前端、依赖与旧测试其余正文均冻结。旧共享工作区现场 HEAD/status/tracked/index 完整记录，不要求它们等于本批基线；固定 commit 来源字节和本任务写入范围仍须精确核验。
- 事后文档仅四旧说明完整旧前缀保留并追加本批节、两份新说明；规划阶段不写这些文件。不运行或改动旧站、生产、真实 PG、Worker、付费/provider，不读取环境秘密、不创建生产 Engine/schema、不部署或归档。指定 GPT-5.6 Sol/xhigh Grillme 独立离线待办，不能由内部复核或根验收替代。
