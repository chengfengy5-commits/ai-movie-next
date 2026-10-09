# 聊天数据后端模块

## 范围

haoai_backend.chat_data 承接既有聊天消息、素材关联消息与 AI 统计，共十个 HTTP 方法。它属于隔离后端的一个业务模块，不是旧站完整后端入口，也不改动认证或个人制作/粗剪模块。统一 factory 登记五十二个方法，但本批 TCP 验收只调用了十个新方法和四个既有 smoke 方法。

| 方法 | 路径 | 成功状态 |
| --- | --- | --- |
| GET | /api/chapters/{chapter_id}/chat-messages | 200 |
| POST | /api/chapters/{chapter_id}/chat-messages | 201 |
| PUT | /api/chapters/{chapter_id}/chat-messages/{message_id} | 200 |
| DELETE | /api/chapters/{chapter_id}/chat-messages | 204 |
| DELETE | /api/chapters/{chapter_id}/chat-messages/single/{message_id} | 204 |
| GET | /api/chapters/{chapter_id}/asset-chat-messages，需 asset_type 与 asset_id | 200 |
| POST | /api/chapters/{chapter_id}/asset-chat-messages | 201 |
| PUT | /api/chapters/{chapter_id}/asset-chat-messages/{message_id} | 200 |
| DELETE | /api/chapters/{chapter_id}/asset-chat-messages/single/{message_id} | 204 |
| GET | /api/chapters/{chapter_id}/ai-stats | 200 |

## 分层与访问

domain 定义不可变消息记录和创建意图；statistics 只实现分组结果合并；application 编排用例和 UoW 生命周期；ports 描述应用接口；persistence 以 SQLAlchemy Core 实现访问、写入、锁续期和统计查询；schemas/http 适配请求与路由；presentation 负责响应投影。app factory 显式装配身份解析器与业务 Session factory。缺少必要接线时路由返回 503，不创建业务 Session。

应用用同一个业务 UoW 解析章节所属剧集、调用公开 series_access 策略并执行聊天查询。认证会话和业务会话仍由认证层分开。每个端点保持其现有查询与 404/403 顺序，不增加权限或筛选规则。普通聊天列表只按章节、chat_mode 与可选 frame_index 查询，保留其原有记录范围；素材聊天列表按 asset_type、asset_id 和 chat_mode 查询。

## 兼容行为

请求继续忽略额外字段，并保留旧 schema 的默认值和 nullable 行为。普通消息与素材消息的读取、创建、内容更新和两类删除保持各自路由。创建消息时，授权依据路径章节，但数据库写入使用请求体 chapter_id；只要外键有效，请求体章节可以属于另一剧集。创建完成后，仅续期路径章节上已存在且属于当前用户的锁（包括已过期锁）；无锁或他人持有的锁保持不变。锁时长配置为 falsey 时回退为 15，负值按原配置保留。AI 统计通过任务与消息、章节的关联汇总 completed/failed 状态，不增加发起用户过滤；空或缺失模型名并入“未知模型”，失败调用计入失败数而不计入成功调用和积分。

## 数据与事务

tables.py 只声明本适配器读写所需的 Core 投影，包含章节、消息、章节锁、配置与任务统计列；它不是完整生产 schema、migration 或数据库启动配置。生产源码不创建 Engine/schema。

写请求由应用用例管理 UoW：在同一业务事务中执行必要 SQL，再提交；消息创建和内容更新提交后，通过同一个 UoW 重新读取实际消息行作为响应。提交前异常触发回滚；提交已经成功但 readback/确认失败时结果未知，模块不自动重发写入，调用方应显式读取核实。删除消息不连带清理没有消息外键约束的任务记录或账务/队列历史。

同内容更新即使没有 chat-message UPDATE，仍会提交并在同一 UoW 读回；L07 在实际提交返回后执行同 Session 主键 SELECT，L08 则由第二个物理连接写入 B 后由原 UoW 读回 B。L14 中消息 UPDATE 或本人章节锁 UPDATE 影响 0 行时返回通用 500 并回滚；已加载消息的单条 DELETE 影响 0 行仍提交并返回 204。消息删除后，非空 ai_tasks 全行（含队列相关列）、billing_units、execution_steps 和 credit_logs 保持不变。task_quotes 仅验证可空 quote_id 与无完整报价 owner 表的薄投影，不表示完整报价、账务或队列模块已迁移。交错与受控故障使用临时 SQLite，不构成 PostgreSQL 并发证明。来源与实测见 [source-review-02.json](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-chat-data-source-review-02.json) 和 [root-http-acceptance-01.json](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-chat-data-root-http-acceptance-01.json)。

## 本批验证范围

根全量后端回归为 344 项通过，另有九条既有弃用/依赖警告。根本机临时 SQLite loopback 完成十八组、194 项断言、69 条有序 TCP 请求，十个新增方法及四个既有 smoke 方法均有实际调用；432 次 SQL 尝试中 431 次完成，唯一未完成项是 L17 预期的受控 hook 故障。证据与未覆盖边界见[本批 verification](../../openspec/changes/modularize-backend-chat-data/verification.md)。
