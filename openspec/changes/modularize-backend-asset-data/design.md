# Design

## Context

本 change 将角色、场景、道具及分镜素材的十五个既有写入/目录方法迁入独立 `haoai_backend.asset_data`。固定来源为 Git commit `23403806898550a7668a6ee7c0c457315655c39b`；主要只读范围为 `backend/app/routes/series.py`、`schemas/series.py`、`models/series.py`、`asset_naming.py`、个人制作媒体 hook/state、团队权限服务和相关 tracked tests。分析记录为 `haoai-next-backend-asset-data-source-discovery-02.json` 和 `haoai-next-backend-asset-data-architecture-preparation-01.json`。现场旧 checkout 只保留 HEAD/status/tracked/index 元数据，不读其当前文件内容。

新后端已有 27 个真实方法：认证 11、既有个人方法 4、series_data 12。15 个方法是下一阶段计划范围；预计接线后为 42，不是当前已实现事实。现有分镜目录 GET `/api/series/{series_id}/storyboard-assets` 不迁移、不复制。

## Goals / Non-Goals

- **Goals:** 旧十五方法有清晰领域、schema、application、Core persistence、媒体协调及 HTTP 边界；保存真实 SQL、更新时间、访问/错误顺序及跨用户状态维护行为。
- **Non-Goals:** 不改 auth/shared identity/series access/notes/rough-cut/series_data 业务，不新增前端或 endpoint，不安装 ORM 全局 hook，不迁移上传下载、生成、provider、去重合并、orphan-check、Worker、队列、计费或语言。

## Decisions

### 1. Exact Boundary and Public Composition

新增 `haoai_backend.asset_data`，由纯 `domain`/`naming`/`references`、领域错误、ports/application、最小 Core 表与 persistence、显式 schemas/presentation、HTTP router、storyboard/media-write adapter 构成。HTTP 层只映射输入/身份/错误；application 协调用例和 UoW；persistence 负责同一业务 Session 上的查询与阶段 DML；媒体 adapter 调用公开 notes 协调能力。不得把整份旧巨大 router 搬成单体，也不抽象全能 CRUD 框架。

`app.py` 只注册 router 并维持现有构造参数和 resolver 优先级。新模块使用自身基于同一 business Session 的 series-access reader，复用公开 `require_series_access` 核心规则；不得依赖 `series_data` 私有 persistence 或 rough-cut HTTP 异常。新十五方法缺少 Session factory 或 identity resolver 时返回 503 且不建业务 Session。旧四方法仍要求显式 `series_access_policy`，不因新 reader 接入而放行。身份维护先由既有认证边界完成，再进入业务 Session。

应用管理 UoW commit、rollback、close。媒体协调器不自行结束该 UoW。配置、导入和 factory 不读取环境/秘密/文件，不创建生产 Engine/schema，不反射 schema，不启动 provider 或安装全局 listener。

### 2. DTO, Presentation and Pure Domain Rules

Schemas 对照旧 `schemas/series.py` 精确保留默认、额外字段忽略、普通 Python `str/int` coercion 和 null/omission 差异。Character `audio_url` 依据 fields-set 区分缺失与显式 null；普通更新其他 null 保留，空字符串仍赋值。Create 不接收 aliases/canonical 元数据；Update aliases 提供列表时全量替换。Storyboard create 生成 UUID，取 chapter 的 series，不自动写章引用。

响应层按原响应模型验证数据；stored aliases 的 JSON 解码错误和非列表转空列表，null 保持 null，list 内坏成员不宽松转字符串。坏 `content` 按来源要求保留原始意义，引用清理仅扫描合法 list 中的 dict 帧；不能为了扫描而规范化未改变章节的原始字节。

命名规则在纯模块复现固定 `asset_naming.py` 行为：移除空白、精确标点映射、`lower` 而非 `casefold`、场景噪词与时段拆分、未知时段和 255 Unicode 码点截断；aliases 修剪、精确大小写去重、保序后按旧 JSON 字节规则序列化。`canonical_key` 仅普通索引，没有唯一约束。Core persistence 必须同时记录赋值意图与最后字段比较：空 PUT 不做 listener 修复；同值赋值可触发既有 listener 的派生修复；最终无变化不 UPDATE、不刷新 `updated_at`，真正变化才更新时间。只在 falsey aliases 且旧 listener 会执行的路径初始化空列表，truthy 异常字符串保持。

### 3. Tables, Persistence and Error Ordering

`tables.py` 只声明本模块所需的最小 Core projection，不代表生产 schema 或 migration。查询与写入应依照旧错误优先级：目录/创建先查系列或章节；直接 ID 更新/删除先确认资产存在，缺失返回既有 404，存在再按其 `series_id` 走普通 access；Storyboard PUT/DELETE 按资产 `series_id` 授权，却按 `chapter_id` 协调媒体和续锁。保留现存错配，不额外加 series 一致性校验。认领、作者、membership 与既有错误文案由公开 access policy 决定；身份和权限拒绝不得触发素材或个人私有行写入。

同一事务内使用 Core，避免导入旧 app。Expected-row UPDATE/DELETE 检查 rowcount；完整页面响应按既有模型构造。每个业务方法保持单一事务提交；后续失败 rollback 未提交的这一 phase。数据库提交已成功而响应/确认异常时不返回可信成功、不自动重试，后续 explicit GET 决定 durable truth。

### 4. Category Reference Deletion

对角色、场景、道具 DELETE，先按旧顺序查资产与授权；在同一 UoW 删除目标资产并读取同剧所有章节，按内容原有 bytes/JSON 语义找出真正变化的 chapter 集。仅移除目标 category list 内与 raw ID 精确相等的成员，不改 frame 上其他类别、文本、非 dict 项或剩余顺序；非法 JSON/非列表跳过。未命中内容保留原始字节与时间戳。

在任何受影响章节来源 DML 之前，先按稳定 chapter ID 顺序锁定完整集合，再调用 notes 媒体协调器并提供拟写 content overlay，随后执行真正有变的 chapter DML，并以单次 commit 保存素材删除、章节引用、media state 和跨用户 private maintenance。category DELETE 的旧 ORM autoflush 不触发章节 hook，因此 Core 必须显式重现该行为。若仅 content 分类引用变化且媒体 identity 不变，仍按旧 hook 调用协调器；遗留空 media state 可 seed，但不能误撤认同一 media identity。中途任何失败都 rollback 本 phase 全部变更，不删聊天、任务或金融行。

### 5. Storyboard Lifecycle and Media Flush Equivalents

**Create.** 旧 pending `StoryboardAsset` 在续锁/首个查询前会被 flush hook 观察。Core 在新资产 INSERT 前读取当前持久 chapter 与 asset base，以 `new_assets` overlay 调协调器；随后 INSERT 并按旧流程续期当前用户已持有章锁，单次业务 commit。新 asset 不写 `chapter.storyboard`，不创建未引用 ID 的媒体状态。外人锁或无锁不阻止创建，也不因此取得新锁。

**Update.** 先查资产并按 `asset.series_id` 授权。更新不允许迁移 `chapter_id`、更改 `series_id` 或调整 `frame_index`。仅 image URL 的实际历史变化请求媒体协调；在图像 DML 前，按受影响章节稳定排序加锁，再用持久化旧资料及 overlay 调 coordinator。协调器依固定媒体身份规则决定是否升版本：临时签名差异不算身份变更。同值图像和 name/description 变化不强制媒体维护；name/description 实际变化可以更新 asset `updated_at`。更新沿旧逻辑只续当前用户既有章锁。

**Delete.** 通过 ID 查找/授权后，在删除资产前以 `deleted_asset_ids` overlay 调协调器；保持 chapter raw references 不变，使 target source 变为 invalid/tombstone，并撤销同章各用户对该 ID 的 approved 状态，保留 note、未知 entry 字段、resume 和其他 rows。删除不续章锁，不调用 ensure 替换，也不删除聊天或任务。

同章多个受影响状态先锁齐再逐章协调，不逐章提交。协调规则遵循 notes 模块的 media identity、单调 revision、跨用户撤认与 unknown-field 保留；它不拥有本模块的 commit/rollback/close。

### 6. Exact Scope and Test Plan

仅修改六旧代码路径：

- `backend/src/haoai_backend/app.py`
- `backend/tests/test_module_boundaries.py`
- `backend/tests/test_series_access_boundaries.py`
- `backend/tests/test_authentication_boundaries.py`
- `backend/tests/test_rough_cut_http.py`
- `backend/tests/test_production_notes_http.py`

五旧测试只更新 factory 方法数/新 router 的必要边界断言，保留所有原业务、冷导入、env、Engine、Session 哨兵。新增十三生产文件：`asset_data/__init__.py`, `domain.py`, `errors.py`, `naming.py`, `references.py`, `ports.py`, `application.py`, `persistence.py`, `tables.py`, `schemas.py`, `http.py`, `media_writes.py`, `presentation.py`，均在 `backend/src/haoai_backend/`。另新增八专用测试：`test_asset_data_domain.py`、`test_asset_data_naming.py`、`test_asset_data_references.py`、`test_asset_data_application.py`、`test_asset_data_persistence.py`、`test_asset_data_source_phases.py`、`test_asset_data_http.py`、`test_asset_data_boundaries.py`，均在 `backend/tests/`。固定 27 个 code/test 路径；不改 `pyproject.toml`、旧引用测试或此前模块。

测试分为七组：A1 DTO omission/null/extra/response 与 storyboard schema；A2 naming/aliases/dirty/no-op；A3 404/403 顺序、真实成员/认领策略、错配兼容和拒绝无素材/私有 DML；A4 多章/重复原 ID 引用清理、内容字节保留、整体回滚；A5 storyboard create/lock/autoflush、不造 chapter reference；A6 identity change 与跨用户批准撤销、metadata-only/no-op、墓碑与 unknown/private 字段；A7 临时 SQLite/真实外键/阶段提交与 rollback/unknown outcome、ASGI 15 methods 和冻结 TypeScript parser 实际 HTTP body。

需要保留的明确限制：固定 source tests 和新隔离测试不等于运行旧 app；PostgreSQL 只允许编译锁 SQL，不宣称真实 PG 并发；root 之后执行 loopback 和 TypeScript parser，不宣称完整 React/browser/frontend；指定 GPT-5.6 Sol/xhigh Grillme 是独立外审且保持离线待办。

## Risks / Trade-offs

- **Core 与 ORM flush 细节不同：** 以实际赋值路径和分阶段 SQLite 行快照验证 dirty repair/no-op 及协调时点，而非只比较最终 response。
- **协调及后续阶段可能失败：** 明确 UoW 与提交边界，保留旧的 phase durability；未知结果不自动补偿或重试。
- **旧模型存在重复引用、错配和不唯一 canonical key：** 兼容这些行为，不在本批静默修复。
- **SQLite 不是 PostgreSQL 并发：** 只把真实本地 SQL/事务与 PostgreSQL 方言编译分层报告。
- **媒体 hook 不再是全局自动行为：** 只显式覆盖本 change 的三类来源写入边界，不声称迁移其他 ORM writer。

## Migration Plan

1. 根冻结五份规划、精确来源和 27 路径 scope 后，才开始实现。
2. 先完成领域/DTO/命名/引用规则，再完成 Core/UoW/HTTP 与媒体协调，按任务和七组测试逐层验证。
3. 冻结代码后由 Sol 独立只读审查；根再执行后端 suite、loopback、TypeScript parser 与保全检查。
4. 只有代码和本地证据通过后才写四旧文档的 EOF 增量及两新文档。Sol 文档复核和 root 当前版本守卫各自完成后再关闭任务；Grillme 离线任务保持独立。

## Open Questions

无新增策略问题。任何执行中发现需改接口、schema、媒体资格或路径范围的事实差异，应停止对应范围并回报根，不在实现中自行扩大政策。
