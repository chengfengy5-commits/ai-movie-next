# Design

## Context

动机见 proposal.md；HTTP 与可观察行为见 specs/backend-series-data/spec.md。新后端已有十一认证方法和四个人业务方法、真实 SQL 剧集访问策略与显式 notes 媒体协调器。本批固定旧来源为 commit `23403806898550a7668a6ee7c0c457315655c39b`，不读取共享旧 checkout 的当前源码。

主要来源锚为 `backend/app/routes/series.py` 的十二方法、`ensure_storyboard_assets_for_chapter`:1552–1644、`delete_frame_in_chapter`:534、`schemas/series.py`、`models/series.py`、`team_service.py`、`media_hooks.py`、`personal_production_state.py`、`personal_production_media.py` 和相关 tracked tests。来源 SHA 与完整阶段分析分别记录在本批 source-lifecycle-analysis-01.json、media-write-design-01.json 和 45 项 baseline。原分析中请求的 delete_frame 名称错误已由实际函数名补正，不覆盖历史分析。

基线为 368 路径、197 旧代码/配置/参考/工具、191 受保护旧代码、153 历史与 358 不可变文件。旧共享 checkout 的现场 HEAD/status/tracked/index 只读取完整记录；固定来源字节、target 保护路径和本批明确写入范围是保全门槛。

## Goals / Non-Goals

- **Goals:** 十二方法全部可经统一 factory 运行；Core adapter 复现实际 SQL 写集合、时间戳、分阶段事务和旧 before_flush 的媒体变化时点；真实认证、访问策略和个人状态在本地临时数据库形成闭环。
- **Non-Goals:** 不改认证/shared identity、普通访问规则、notes/rough 代码、前端、依赖、生产 schema；不迁移其他 ORM writer/global hooks、生成/队列/provider/上传/计费策略，不删除金融历史，不部署旧站或生产。SQLite 与 PostgreSQL 编译不是真实 PG 并发或上线证据；指定外审保持独立。

## Decisions

### 1. Cohesive Module and Explicit Ports

采用 `haoai_backend.series_data`，应用层只依赖自己的 typed records/UoW/clock/ID ports，纯规则负责字段变化、排序、帧引用和聊天映射；HTTP 负责 DTO 与错误映射，SQL adapter 负责同 Session Core 查询、变更与阶段事务。避免直接搬运旧巨型 route、在 application 引入 SQLAlchemy/FastAPI，或为十二方法各造重复 service。

`presentation.py` 保持列表/详情认领字段差异、锁与 style prompt/头像纯投影；不拉取头像。最小查询表投影只含实际旧列与约束，不反射、不读取数据库 URL、不在生产 create_all。测试/lab 自建临时 SQLite schema，显式启用 FK。新投影不得虚构 AITask.message_id 外键、users 新列或章 order 唯一约束。

### 2. Configuration and Access Wiring

app 保持原参数与显式 resolver 优先级。新十二方法自己的 UoW factory 仅在有效 Session factory 与 resolver 同时存在时接线；它创建一个业务 Session，构造自己的 `SqlAlchemySeriesAccessReader`，复用公开 core `require_series_access`，将 SeriesAccessError 在本模块映射原 HTTP 错误。缺接线预检查 503，在身份拒绝/配置失败前不开业务 Session。认证自动 resolver 使用既有独立认证事务，不能合并认证维护与业务提交。

原四方法仍要求显式 `series_access_policy`，接入新 reader 不填充或改变其默认条件。新模块不依赖 rough-specific bridge，也不修改 ordinary access。查章本身的 SELECT 可以含 content；授权在其解析、素材与私有状态查询之前执行，不宣称授权前没有读取该列。

普通访问保持 claim gate→作者短路→最终 membership 再读。删除剧集使用独立 creator/superuser/member delete 权限，creator 短路后才对非作者在当前业务 Session 读取真实 users.is_superuser；不能把删除资格扩展为普通访问或从请求传入 flag。权限并发读取保持原非原子限制，不新造串行权限政策。

### 3. Compare Before DML and Preserve DTO Differences

Core 不具备 ORM dirty detection/onupdate，仓储 SHALL 比较当前字段：只有实际变化才发 UPDATE 并设置对应旧 onupdate 时间。空 PUT、同值非 null 字段、同序列化字节 content、完全相同的已匹配素材 index/name/description 不刷新来源 updated_at。仅本人锁续期更新锁行，不刷新章节。只有来源 content 同字节且无实际 new/delete/媒体相关资产变化时不调用协调器、不强制初始化；真实 content 变化即使只有 text，仍沿旧 hook 调用协调器，无 media states 时可以 seed R1，但媒体身份相同不升版或撤认。指定 content 仍执行旧 ensure/聊天提交阶段。

Schema 保留旧 coercion/default/extra ignore：创建 SceneFrame 丢额外引用、更新 List[dict] 保留未知内容；缺失/null 不修改、空字符串/[] 可修改。content 与 raw_content 优先级、order=0 的 max+1、负 order 和 raw reorder dict 的遗漏/重复保持。锁只在 update_chapter 续期，分钟配置 truthy/15 回退和 expires_at>now 保持。应用以注入 clock/ID 提供可实证时间和 ID，不改变 wire 政策。

### 4. Reproduce Flush Phases with Explicit Media Overlays

只按 commit 次数调用协调器不足以兼容：旧新资产先 flush，再替换本地帧引用，最终章 content/deletes 又 flush。采用“读取当前持久化事实→协调 pending overlay→执行该阶段 Core DML”；不把尚未写回的本地 frames 当数据库旧事实。

媒体 adapter 组合冻结 `personal_production.notes.reconciliation.reconcile_chapter_media_state` 与同 Session 的 `SqlAlchemyNotesUnitOfWork` 能力。调用方持章锁，随后按稳定 ID 顺序读取/锁资产、media state、用户 private 行；notes adapter/coordinator 不拥有本批 Session 的 commit/rollback/close。application 管理每阶段 UoW 生命周期，纯 Core 避免旧 pending ORM notes 被覆盖。

| 操作 | 来源持久化与协调阶段 | 提交边界 |
| --- | --- | --- |
| create chapter | 插入新章阶段沿旧 hook 排除；之后 ensure 对已持久章逐项资产及最终 content 协调 | 章插入 commit1，ensure commit2 |
| update chapter | 实际变更的 content 在首次来源 UPDATE 前协调；title/order/lock 非媒体变更不协调；ensure 与聊天映射随后执行 | commit1；指定非 null content 时 commit2、commit3 |
| ensure | 初始 chapter_id 素材目录 map 固定；每个 new asset overlay 使用当时数据库 content，insert 后才替换本地 refs；最后 content override 与删除初始未引用资产合并协调 | 逐 flush-equivalent phase，最终一次本阶段 commit |
| delete frame | 内容删除及 indexed chat 删除/shift 前协调；随后 ensure | commit1、commit2 |
| delete chapter | 整章排除媒体协调，notes/media 通过真实章 FK cascade；再按旧引用集合清 orphan | 删除 commit1；任一引用集合非空才有清理 commit2 |
| delete series | 释放锁、任务/聊天/资产/章/剧范围清理，整章删除排除协调 | 一次 commit |

媒体状态为空时先 seed 旧 base R1，再算 pending target，真实变化形成 R2。每真实 phase 每受影响用户只推进一次 private revision，未知字段/note/resume 不丢。text/category/order/位置/描述/临时签名参数不产生媒体版本；真实图片/视频变化、删除墓碑、恢复维持单调版本与跨用户撤认。新资产并不因无 preview/video 被拒绝。

旧 ensure 仅 chapter_id 查初始目录，无额外 series 过滤；重复匹配首 ID 复用同资产且后位置覆盖，保留其原多引用数组。新 ID 不补回初始 map；不可 hash 引用继续在原操作位置失败。用明确阶段 plan 表达待写内容和素材，不能一次计算新全章最终态后代替各 flush 事实。

### 5. Transactions, Chat and Deletion Constraints

每阶段 commit 一次；失败只 rollback 当前阶段并 finally close。已提交阶段不会因后阶段失败撤销。Core UPDATE/DELETE 需要期待行存在时检查 rowcount，未知 DB/提交后 acknowledgement failure 不返回可信成功、不自动重试。GET 不添加来源维护提交，目录只读原 raw URL；生产不创建 Engine 或 schema。

旧 update content 的 old/new 首 storyboard 映射保持重复最后覆盖；映射空不删除旧聊天，null frame_index 不参与 indexed remap。delete-frame 删除当前 index 聊天并 shift 后续。两者不删除 AITask：旧 models/series.py:262 的 message_id 是 String(36)、nullable=False、index=True，**没有 ForeignKey**。整章/整剧才按关联 message_id 批删任务。

删除章按 chapter_id 清 storyboard，包含错 series_id 资产；角色/场景/道具 orphan 清理必须限所属 series 且不在剩余章引用中。删除剧集复现锁、rough draft、canvas、chat、章与资产族清理。测试 schema 真实保留 billing_units.task_id→ai_tasks 的非 CASCADE FK，以及账务 steps/log 引用；有账单任务删除失败回滚本阶段，账本完整不动。复杂队列/计费修复另批，不借迁移猜删法。

### 6. Exact File Allowlist

仅允许六旧代码路径变动：

- `backend/src/haoai_backend/app.py`
- `backend/tests/test_module_boundaries.py`
- `backend/tests/test_series_access_boundaries.py`
- `backend/tests/test_authentication_boundaries.py`
- `backend/tests/test_rough_cut_http.py`
- `backend/tests/test_production_notes_http.py`

后五个只更新 factory 二十七方法/对应接线期望，保留所有原业务、冷导入、环境/Engine/Session/file 哨兵；app 只挂新模块并保持原配置协议。pyproject 不变，不加依赖。

固定十九新代码/测试路径：

- `backend/src/haoai_backend/series_data/__init__.py`
- `backend/src/haoai_backend/series_data/domain.py`
- `backend/src/haoai_backend/series_data/errors.py`
- `backend/src/haoai_backend/series_data/ports.py`
- `backend/src/haoai_backend/series_data/application.py`
- `backend/src/haoai_backend/series_data/persistence.py`
- `backend/src/haoai_backend/series_data/tables.py`
- `backend/src/haoai_backend/series_data/schemas.py`
- `backend/src/haoai_backend/series_data/http.py`
- `backend/src/haoai_backend/series_data/storyboard.py`
- `backend/src/haoai_backend/series_data/media_writes.py`
- `backend/src/haoai_backend/series_data/presentation.py`
- `backend/tests/test_series_data_domain.py`
- `backend/tests/test_series_data_application.py`
- `backend/tests/test_series_data_persistence.py`
- `backend/tests/test_series_data_source_phases.py`
- `backend/tests/test_series_data_http.py`
- `backend/tests/test_series_data_boundaries.py`
- `backend/tests/test_series_data_deletion.py`

事后四旧文档 `README.md`、`backend/README.md`、`docs/architecture/backend-module-boundaries.md`、`docs/architecture/module-api-compatibility.md` 全部保留完整旧前缀，仅 EOF 追加本批段。新文档仅 `docs/architecture/series-data-backend.md` 和当前 change 的 `verification.md`。规划只五文件；不创建这些产品、测试或事后文档。其他路径需先报告，不顺手扩白名单。

### 7. Meaningful Acceptance Cases and Evidence Layers

阶段验收以已有分析 T1–T7 为准，真实 SQL 记录完整 rows、阶段 commit/rollback 和再读结果，避免只测 helper：

| Case | 必需反例及观测 |
| --- | --- |
| T1 | 用既有 PUT chapter 改变 content preview 身份（可预置已有 asset image）覆盖 legacy 无 media 的已认可帧，base R1→R2、同章多用户撤认及未知字段/note/resume/无关行守恒；不新增 image-update endpoint/生产 writer |
| T2 | 新章首次提交无 hook 初始化；多个缺失/悬空首 ID 新 asset 插入早于 refs 写回，重复、多引用、unhashable、错 series 行为原样 |
| T3 | delete-frame 第一阶段墓碑与聊天移动，第二阶段无重复推进；第一阶段故障全回滚，第二阶段故障保留第一阶段；任务无 chat FK 可孤留 |
| T4 | 实际 text/category/order 变化不推进媒体版本；同字节 content/同 metadata/no-op/仅锁续期不刷新来源 timestamps；真实 preview/delete/restore 单调墓碑无认可恢复 |
| T5 | 当前 SQL 而非缓存、同 phase new+delete、多用户 CAS 零匹配全阶段回滚；真实提交前故障与提交后 ack raise 显式再读；PG 只编译锁序 |
| T6 | creator/superuser/member delete 权限与普通访问差异、伪造 flags、错误 priority、claim 分阶段 membership 再读、实际拒绝零 asset/private 查询 |
| T7 | 无账单时错 series storyboard 按章清理；有账单真实 FK 阻止章/剧删除且完整 ledger/steps/source rows 保持 |

根完整新后端 pytest、syntax/精确 manifest、45 固定 Git bytes 与原 source contract3/exact copy5 分层执行；不重跑或冒称既有前端测试。根真实 loopback 在自己临时 SQLite 上覆盖十二方法与既有 auth/access/个人状态闭环，包括正常编辑、无视频、空章、权限拒绝、阶段错误/恢复与账单拒绝。用冻结 contracts.ts 的 parseSeries/parseSeriesList/parseChapterList/parseStoryboardAssetList 消费实际 HTTP body；单 ChapterResponse 用 [actualBody] 交 parseChapterList(expectedSeriesId)，不改 parser、不声称完整 React/browser 验收。

解释器只读复用已核旧 venv，不导入旧 app；PYTHONDONTWRITEBYTECODE=1、python -B、PYTEST_DISABLE_PLUGIN_AUTOLOAD=1、PYTHONPATH 仅 backend/src、-c backend/pyproject.toml、-p no:cacheprovider 和独立 /tmp basetemp。不安装、不加 timeout/skip。保留实际失败及修复日志，精确清理本批自有 PID/端口/temp；不称 global 无外连或清理别人资源。

## Risks / Trade-offs

- [多阶段事务不能全请求原子] → 复现旧阶段耐久性，并用故障后新连接再读证明；不自动补偿或重试。
- [Core 与 ORM dirty/flush 时点不同] → actual field 比较、显式媒体 overlays 与完整 no-op timestamps/阶段 rows 测试，不以最终投影相同代替版本相同。
- [遗留 duplicate/unhashable/错 series 和 orphan task 不整洁] → 本批兼容记录，不静默修政策；有账单删除由真实 FK 拒绝。
- [权限多次 SELECT 非原子，SQLite 无 PG 行锁] → 明确限制并编译 PG lock statement，不新增串行化保障或宣称 PG 验收。
- [共享旧 checkout 可由其他会话变化] → 只完整记录现场元数据，固定 git-show bytes 与本任务 target 保全精确；不引入重复九百事件审计或 ambient equality 门槛。

## Migration Plan

根独立审读四规划、真实 strict/status/apply ready 后冻结并授权 Luna 实施。按纯规则/DTO→SQL/媒体阶段→HTTP/factory 顺序做 meaningful tests；Sol 只读绑定稳定 SHA 复核后，根实际全量与 loopback/TS/保全。事后文档按白名单追加、冻结复核、最终 checkbox-only 收口。不会运行生产迁移、自动建库或部署；本地代码可继续后续批次，外审单列，不把全目标标完成。
