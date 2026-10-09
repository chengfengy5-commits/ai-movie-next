# 章节画布后端

## 职责与接线

隔离后端的 `haoai_backend.canvas_data` 承接固定来源中的两个既有方法：`GET` 和 `PUT /api/chapters/{chapter_id}/canvas`。画布属于章节，是共享文档而非按用户拆分的私有数据。应用使用可信 actor、公开剧集访问策略和同一业务 UoW；此切片没有迁移旧站点的完整后端入口。

模块按 domain、schemas、application、ports、persistence/tables、presentation 和 HTTP 分层，由显式应用工厂组装。完整画布 owner 表是该适配器所需的 Core 投影，不是全站生产 schema 或迁移；生产源码不创建 Engine 或 schema。

## 读取与保存

GET 先查章节，再检查剧集访问，之后读取画布。画布缺行时返回完整默认对象：版本 1、空 nodes/edges/notes、默认 viewport，以及空 id/更新时间元数据；该请求不插入画布行、不提交业务事务。

PUT 保留兼容顺序：章节查找、剧集访问、本人已有锁续期、JSON 序列化与大小检查、画布读取、可选版本比较和写入。缺章节优先返回 404。仅本人已有锁会续期，过期本人锁也照常续期；无锁或他人锁不新建锁、不拒绝。falsey 锁时长回退 15 分钟，负值保留原配置语义。

请求正文要求为 JSON 对象；`version` 可省略或显式为 null，沿用普通 DTO 转换与额外字段忽略。序列化使用 `json.dumps(..., ensure_ascii=False)` 的默认分隔符，按 Python 字符数判断是否超过 1,000,000。新建忽略请求版本并从版本 1 开始；已有记录只将非 null 请求版本与已读快照比较，通过后写 `(current_version or 1) + 1`。即使正文相同，成功 PUT 仍递增版本。持久化更新按主键执行，不增加 SQL `WHERE version` 条件或数据库 CAS。

更新意图按首次 ORM 快照逐字段确定净变更集合：`document_json` 按原始字符串精确比较，不按解析后的 JSON 语义比较；`updated_by` 独立判断。首次读取时与请求相同的字段不会因第二连接后来写入的值而进入 `SET`；首次读取时确有变化的字段，即使第二连接后来已写成请求值，仍按首次快照保留在 `SET`。实现不重读并发值来重算写意图，也不增加版本 CAS；版本和更新时间仍按既有 PUT 规则推进。

每个成功 PUT 只提交一次，随后在同一 UoW 内真实重读写入行。响应中的记录元数据来自刷新后的数据库行，`document_json` 回显该次请求；后续 GET 返回当前持久状态。提交前异常按原事务边界回滚。若 commit 已成功而确认或刷新失败，结果未知；模块不自动重发，调用方需显式 GET 核实，不能声称回滚撤销了已成功提交。

## 验证边界

固定兼容来源为提交 `23403806898550a7668a6ee7c0c457315655c39b`。本批使用完整 owner 投影和隔离 SQLite 检查事务及历史行守恒；这不等于真实 PostgreSQL 并发或生产 schema 迁移。实际测试、HTTP 请求范围和 SQL 标签限制见[验收记录](../../openspec/changes/modularize-backend-canvas-data/verification.md)。
