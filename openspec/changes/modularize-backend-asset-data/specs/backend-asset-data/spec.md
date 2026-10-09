# Spec Delta

## Purpose

定义新后端角色、场景、道具和分镜素材方法的精确接口范围、旧 DTO 与命名兼容、访问顺序，以及源引用变更对个人媒体状态的事务影响。本文是新增能力的规格差量，不声称实现已经存在。

## ADDED Requirements

### Requirement: Complete Asset Data HTTP Surface

新 `asset_data` 模块 SHALL 提供以下十五个既有方法，状态码与固定来源一致：

| 方法 | 路径 | 成功状态 |
| --- | --- | --- |
| GET | `/api/series/{series_id}/characters` | 200 |
| POST | `/api/series/{series_id}/characters` | 201 |
| PUT | `/api/characters/{character_id}` | 200 |
| DELETE | `/api/characters/{character_id}` | 204 |
| GET | `/api/series/{series_id}/scenes` | 200 |
| POST | `/api/series/{series_id}/scenes` | 201 |
| PUT | `/api/scenes/{scene_id}` | 200 |
| DELETE | `/api/scenes/{scene_id}` | 204 |
| GET | `/api/series/{series_id}/props` | 200 |
| POST | `/api/series/{series_id}/props` | 201 |
| PUT | `/api/props/{prop_id}` | 200 |
| DELETE | `/api/props/{prop_id}` | 204 |
| POST | `/api/storyboard-assets` | 201 |
| PUT | `/api/storyboard-assets/{asset_id}` | 200 |
| DELETE | `/api/storyboard-assets/{asset_id}` | 204 |

`GET /api/series/{series_id}/storyboard-assets` SHALL 继续由 `series_data` 提供；本次改动 MUST NOT 注册重复路由或添加其他端点。全部十五个方法 MUST 使用可信 actor，并遵循现有 active-membership 与 ordinary series-access 规则。缺少 Session factory 或 identity resolver 时 MUST 在创建业务 Session 前返回 503。显式配置的 resolver MUST 保持优先。既有粗剪与个人制作记录的四个方法 MUST 继续要求显式 `series_access_policy`。

#### Scenario: Factory Adds Only the New Surface

- **WHEN** 将 asset-data router 接入统一 factory
- **THEN** 暴露以上十五个方法及其规定的成功状态码
- **AND** 现有分镜素材目录 GET 仍由 `series_data` 提供
- **AND** 不新增上传、抓取、生成、provider 或 orphan-check 路由。

#### Scenario: Missing Wiring Fails Closed

- **WHEN** 缺少 Session factory 或可信身份 resolver
- **THEN** asset-data 请求返回 503，且不创建业务 Session
- **AND** 有效的显式 resolver 配置仍具有优先权。

### Requirement: Legacy DTO and Response Semantics

创建与更新 SHALL 保留固定 schema 的类型转换、额外字段、字段省略及 null 行为。创建请求 MUST 忽略未建模的 `aliases` 和 `canonical_key`；更新请求中的 `aliases` 省略或为 null 时 MUST 保留已存列表，提供列表时则整体替换。普通更新字段省略或为 null 时 MUST 保留原值；空字符串仍作为赋值处理。Character 的 `audio_url` MUST 保留其特殊的字段存在性语义：省略时保留旧值，显式 null 时清空，空字符串按原样存储。

当数据库中的 aliases 值为字符串时，响应 MUST 尝试按 JSON 解码；无效 JSON、非列表 JSON（包括字符串内容为 JSON `null`）MUST 投影为 `[]`。当数据库列本身的值为 null/None 时，响应保留 null。列表 MUST 按声明的 `List[str]` 响应校验，不得把非法成员转成字符串。Storyboard 更新 MUST NOT 将素材迁移到其他章节或修改 `chapter_id`、`series_id`、`frame_index`；这些字段不是固定更新 schema 的输入项。Storyboard 创建保留必填的 `chapter_id`、`frame_index` 和 `name`，由服务端生成素材 ID，并从章节派生 `series_id`。除固定 schema 已有校验外，MUST NOT 新增对负帧索引、重复索引、空名称或缺失图片的限制。

#### Scenario: Omission, Null and Empty Values Differ

- **WHEN** 客户端省略字段、发送 null 或发送空字符串/空列表
- **THEN** 每个字段均按上述不同语义处理，而不是应用统一的新 PATCH 规则。

#### Scenario: Create Extras and Stored Aliases

- **WHEN** 创建输入带有额外别名元数据，或已存 aliases 值格式错误
- **THEN** 创建忽略未建模字段，响应解析按 JSON/列表规则执行，不以宽松转换掩盖错误。

### Requirement: Legacy Naming and No-Op Persistence

角色、场景和道具名称 SHALL 使用既有的类型化命名规则、标点映射、空白移除、小写化、场景噪声词/时段处理，以及 255 Unicode 码点截断。Aliases 规范化 SHALL 修剪条目、按精确值去重、保留原顺序和大小写差异，并按既有 JSON 规则序列化。`canonical_key` 索引 MUST 保持非唯一；相同 key 的素材可以并存，MUST NOT 自动合并。

Persistence adapter MUST 区分赋值意图与最终值是否相等。空更新 MUST NOT 无条件修复陈旧的派生字段。若对应更新路径会使 ORM 行变为 dirty，同值赋值 MAY 触发旧 listener 对陈旧 canonical key 或 falsey aliases 的修复。最终存储值和派生值都未变化时 MUST 不执行 UPDATE，也不推进 `updated_at`；实际变化才更新该时间戳。仅在旧 listener 会执行的写入路径上，falsey aliases 才初始化为 `[]`；truthy 的格式错误字符串不应被静默修复。

#### Scenario: No-Op and Derived Repair

- **WHEN** 对含陈旧派生数据的素材发送空更新
- **THEN** 不修复该行
- **WHEN** 显式赋予同一字段且旧 listener 会运行
- **THEN** 只有最终值确实不同才修复派生字段，且 no-op 时间戳不变。

#### Scenario: Canonical Keys Are Not Unique

- **WHEN** 两个素材规范化后得到相同 canonical key
- **THEN** 两行仍并存，并可分别按各自 ID 访问。

### Requirement: Access and Error Priority

全部方法 SHALL 保留现有后端边界中的身份/Session 认证顺序，并在业务 Session 外完成认证维护。目录与创建方法 SHALL 先解析剧集，再查询或修改其素材。按 ID 更新/删除素材时 SHALL 先查询素材：不存在时返回旧 404；存在时再按该素材的 `series_id` 执行 ordinary access。Storyboard 创建 SHALL 先解析章节再授权；Storyboard PUT/DELETE SHALL 按素材的 `series_id` 授权，并按其 `chapter_id` 处理锁与媒体。实现 MUST NOT 增加 `asset.series_id == chapter.series_id` 新限制。认领、作者、membership 校验及既有错误文案/优先级 SHALL 与固定访问策略和来源保持一致。

#### Scenario: Missing and Forbidden Assets

- **WHEN** 直接 PUT 或 DELETE 指向不存在的 ID
- **THEN** 在 ordinary access 前返回旧 not-found 响应
- **WHEN** ID 存在但 actor 无权访问其所属剧集
- **THEN** 在素材或个人私有数据变更前返回既有 forbidden 响应。

#### Scenario: Storyboard Series and Chapter Differ

- **WHEN** 已存在 storyboard 素材的 `series_id` 与其章节所属剧集不一致
- **THEN** 授权使用素材的 `series_id`，锁及媒体协调使用 `chapter_id`，且不新增跨剧集拒绝。

### Requirement: Category Deletion Reconciles Chapter References

删除角色、场景或道具 SHALL 从同剧每个章节对应的引用列表中移除所有与目标 raw ID 精确相等的成员。MUST 保留其他类别、文本、非字典帧成员、列表位置及剩余条目的顺序。无效 JSON 或非列表章节内容跳过。引用未改变的章节 MUST 保持原 content 字节和时间戳。

对于变化的章节，服务 SHALL 收集完整受影响集合，按稳定 ID 顺序获取章节锁，并在章节来源 DML 前为每章调用现有个人媒体协调器；之后只写入实际变化的 content，并在同一业务事务中一次提交素材删除、章节修改和个人媒体维护。提交前失败 MUST 回滚本阶段的全部变更。即便媒体身份不变，只要类别引用导致 content 变化也调用协调；但 MUST NOT 对身份未变的媒体误撤认可。这些端点不删除聊天、任务、金融数据或无关素材。

#### Scenario: Duplicate References Across Chapters

- **WHEN** 同一 raw ID 在多个章节的目标类别中出现多次
- **THEN** 删除所有命中项，同时保留其他类别引用及未变化章节的原字节。

#### Scenario: Reconciliation Failure

- **WHEN** 后续某个受影响章节在协调时失败
- **THEN** 回滚后该未提交删除阶段中的类别行、所有章节行和个人媒体/私有行均保持原样。

### Requirement: Storyboard Asset Writes Preserve Flush and Media Semantics

Storyboard 创建 MUST 在插入新素材前，基于已持久化章节/素材以及 pending new-asset overlay 执行协调；MUST NOT 将新素材 ID 写入 `chapter.storyboard`，也不为未引用 ID 虚构媒体状态。成功创建使用一次业务提交，并保留现有的当前用户锁续期行为。

Storyboard 更新 SHALL 区分媒体身份、临时 URL 签名和描述元数据。`image_url` 发生实际历史变化时，应在素材 DML 前调用现有协调器，并使用持久化旧事实和目标 overlay；只有媒体身份实际变化才推进 revision 并撤销认可。仅 name/description 的实际变化更新素材时间戳但不强制媒体维护；同值不产生写入。更新保留旧锁续期语义。

Storyboard 删除 SHALL 在删除素材行前，以 `deleted_asset_ids` overlay 执行协调。已有章节引用保持不变；媒体状态变为 invalid/tombstone，并按 notes 协调规则撤销该 ID 上所有 approved 记录，同时保留备注、未知字段、resume 状态和非认可记录。删除不续期章节锁，也不运行 ensure 替换。

#### Scenario: Create Does Not Invent a Chapter Reference

- **WHEN** 为章节创建 storyboard 素材
- **THEN** 创建素材行，但章节的原始 storyboard 和引用保持不变。

#### Scenario: Image Identity Changes

- **WHEN** 更新改变图片的实际身份
- **THEN** 协调器在同一事务中推进媒体 revision，并使该章所有用户的相关认可失效
- **AND** 仅改变签名的 URL 不推进媒体 revision。

#### Scenario: Delete Leaves a Tombstone

- **WHEN** 删除被章节内容引用的 storyboard 素材
- **THEN** 章节引用保持不变，媒体来源变为 invalid，并撤销 approved 记录而不删除备注或任务。

### Requirement: Same-Session Transactions and Evidence Boundaries

新模块 SHALL 使用类型化 ports 与同一业务 Session 的 Core adapter。MUST 复用公开的 series-access Core policy 和现有个人媒体协调器；MUST NOT 依赖 rough-cut HTTP 内部、创建生产 Engine/schema、反射当前 schema、读取环境秘密或安装全局 ORM hook。应用代码负责 UoW 的 commit/rollback/close，协调器不接管该生命周期。每个来源阶段提交一次；后续失败不撤销更早阶段已经提交的内容。提交已完成但确认/响应环节异常时 MUST NOT 报告可信成功，也不得自动重试；客户端可显式读取当前状态。

验收 SHALL 使用启用外键的隔离临时 SQLite、具有意义的阶段级行快照、ASGI/loopback 请求以及冻结的 TypeScript 资产 parser。PostgreSQL 检查可以只编译锁 SQL，MUST NOT 描述为真实 PostgreSQL 锁或并发验收。不声称生产、provider、上传、浏览器或部署验收。

#### Scenario: Unknown Commit Result

- **WHEN** 数据库已提交来源阶段，但提交确认失败
- **THEN** 请求不声称成功，也不自动重放写入
- **AND** 后续显式读取决定实际持久化状态。
