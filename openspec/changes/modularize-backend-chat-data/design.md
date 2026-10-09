# Design

## Context

见 proposal.md 的动机及 specs/backend-chat-data/spec.md 的外部合同。现统一工厂已有 42 个方法，聊天十方法仍位于固定旧 `routes/series.py:2062–2363`；主规格库存实际为空。本批使用固定提交 `23403806898550a7668a6ee7c0c457315655c39b`，不使用旧工作区现场正文。

新基线为 422 路径、237 份代码、230 份受保护旧代码、165 份历史、411 份不可变文件及 47 个固定 Git 来源。旧共享 checkout 可有其他会话变更，现场 HEAD/status/tracked/index 仅完整记录，不加入相等门槛。基线原七份允许修改代码的真实字节已单独保存，用于后续真实差分。

## Goals / Non-Goals

**Goals:**

- 以纯规则、应用端口、同业务 Session 的 Core 仓储及 HTTP 边界实现完整十方法，保持旧请求与副作用顺序。
- 对消息和章节锁提供真实 SQL 事务与提交后重读证据，明确未知结果；只读完整任务历史统计，不混入执行服务。
- 将已知路径错配和条件授权差异作为兼容事实记录；用真实 FK 与 SQL 数据证明，而不在模块迁移中改政策。

**Non-Goals:**

- 不写任务、账务、队列、provider、Worker 或个人制作状态；不迁移章节/剧集删除行为。
- 不改认证、全局身份、普通访问策略、现有个人记录及来源素材模块，不增加公开 CAS 或修复上述权限差异。
- 不添加聊天前端、TypeScript parser、浏览器或全站验收；不安装依赖、连接生产或真实 PostgreSQL、部署及归档。

## Decisions

### 1. 明确分层和配置

采用 `chat_data` 独立包。domain 承载消息快照和请求意图；statistics 负责原始分组合并；application 依赖 ports，不反向导入 SQLAlchemy、HTTP 或旧 app。presentation 只组装响应，schemas 保留普通 Pydantic 校验与 extra-ignore，http 保持标准局部请求错误和状态。

业务 Session 由统一工厂注入的 request UoW factory 创建；UoW 接收该 Session，负责本请求的 rollback 与 close，不自行创建 Engine。身份解析和现有认证维护仍在此前独立 auth Session 中先完成，不能借本批改成同一事务。显式 resolver 优先；未显式给 Session factory 时保留已有 auth runtime fallback。新十方法只需有效 resolver 与 Session 配置，普通访问使用同业务 Session 的 `SqlAlchemySeriesAccessReader` 和公开 series-access 应用入口，映射到公共业务错误，不依赖 rough-cut 私有错误。旧四个个人记录方法仍要求显式 `series_access_policy`，原配置规则不放宽。

缺配置在创建 UoW/业务 Session 前返回 503。默认工厂只登记路由；模块冷导入、工厂调用不读取环境/文件/密钥，不创建 Engine/表或实例化供应商。替代方案是复用来源模块内部仓储，放弃它以避免聊天依赖来源写入语义；只复用公开访问接口与可信 Actor。

### 2. HTTP 方法和权限顺序

下表前缀均为 `/api`。“查章→访问”含缺章 404；“条件访问”只在章节存在时检查，不是新许可承诺。

| 方法 | 路径 | 成功 | 查询及错误顺序 |
| --- | --- | --- | --- |
| GET | /chapters/{chapter_id}/chat-messages | 200 | 查章→访问；chapter+mode，可选 frame，不排除资产消息 |
| POST | /chapters/{chapter_id}/chat-messages | 201 | 查路径章→访问；INSERT body 章 |
| PUT | /chapters/{chapter_id}/chat-messages/{message_id} | 200 | 消息 id+路径章→消息404→查章→条件访问，无资产判别 |
| PUT | /chapters/{chapter_id}/asset-chat-messages/{message_id} | 200 | 与上一 PUT 相同，无资产判别 |
| GET | /chapters/{chapter_id}/asset-chat-messages | 200 | 查章→条件访问；必需 type/id 与 mode，无新缺章404 |
| POST | /chapters/{chapter_id}/asset-chat-messages | 201 | 查路径章→访问；INSERT body 章 |
| DELETE | /chapters/{chapter_id}/chat-messages | 204 | 查章→访问；可选 frame，不限 mode/type，零行合法 |
| DELETE | /chapters/{chapter_id}/chat-messages/single/{message_id} | 204 | 查章→访问→消息 id+章+type IS NULL→消息404 |
| DELETE | /chapters/{chapter_id}/asset-chat-messages/single/{message_id} | 204 | 查章→访问→消息 id+章+type IS NOT NULL→消息404 |
| GET | /chapters/{chapter_id}/ai-stats | 200 | 查章→访问→章统计及剧集统计 |

缺章为“章节不存在”，缺消息为“消息不存在”；旧 403 原文由公开访问模块保持。身份、会员错误沿现有 resolver。拒绝在业务 DML 前发生，但查章 SELECT 已包含内容列，不能声称授权前没有任何章节列读取。应用不解析来源 JSON、不查询媒体或私人记录。

DTO 必须区分普通创建省略 `frame_index` 的默认 None 与显式 null 校验失败；保留普通 int 转换及负值，不改为 StrictInt。资产创建没有 frame 字段，额外值忽略。字符串不 strip、不新增非空/枚举/SQL 长度型业务校验；空 role/content/mode/type 保留。普通创建 extra asset 字段和资产创建 extra frame 同样按默认 ignore，不作为本批新支持字段。响应沿原 nullable 字段与 datetime，ChatMessage 无 updated_at。

### 3. 最小生产表投影与完整测试 owner

chat_data.tables 提供 chat_messages 全消息列、chapters 的访问所需列、chapter_locks 与 system_configs 的锁续期字段，以及只读 ai_tasks 统计列。调用普通访问 reader 时使用其公开查询投影；不反射、create_all、读取 Engine URL 或获取外部配置。生产表投影不是 schema 迁移。

ChatMessage.chapter_id 保留真实章节 FK；ai_tasks.message_id 为非空 String(36)，没有消息 FK，不造级联。新统计投影只读 id/message_id/status/credit_cost/model_name，不能把现有 series-data 的薄 ai_tasks 投影当完整 owner DDL。

测试与后续 lab 由完整 owner DDL 优先建表，正常启用 SQLite FK，任务、账单、步骤、queue/reliability 字段使用实际来源定义的完整测试数据并比较全行。只有证明旧条件访问的 orphan 用例可显式使用单独 FK-off 库，标明该例不证明正常 FK 插入合法；禁止为整个套件关闭 FK。

### 4. 同值赋值、锁顺序、一次提交与实际重读

创建按旧 autoflush 可见顺序显式 INSERT 消息，再查路径章节已有锁。仅锁属于本人时读取配置、计算 now 并更新 last_active_at/expires_at；包括过期本人锁，不创建锁、不因 foreign 锁拒绝。配置取首个 SystemConfig 的 chapter_lock_idle_minutes，假值采用 15，原负值真值语义不暗改。acquired_at、锁身份及其他列保持。

两个 PUT 在第一次已加载消息上决定 content 是否有净变化。同值不发 chat UPDATE；非同值仅 SET content。不做二次读取后扩大写集合，不添加 updated_at；仍一次 commit 后通过同 UoW、同 Session 的实际 SELECT 刷新响应。返回提交前 dict 不能替代旧 db.refresh 的阶段；刷新可观察最新数据库值，不添加额外 commit、Session 或写重试。

普通读取与统计不显式 commit；写请求只提交一次。消息实际 UPDATE 与本人锁 UPDATE 期待已加载行仍存在，rowcount 零必须抛失败并回滚，HTTP 保留来源未捕获 StaleDataError 的通用 500，不新增 409 或公开 CAS；按原主键写入，不发明客户端 CAS 或串行授权锁。批量 DELETE 零行是合法 204；已加载单删并行零匹配也保留原无版本 ORM DELETE 的 commit/204 行为，不新增异常。当前安装 SQLAlchemy orm/persistence.py:1460–1485 明确无 version_id 的 DELETE 仅 warn，与 UPDATE 的 StaleDataError 不同；这是额外 installed-library 静态来源，不改47固定 Git 来源清单。

未知数据库异常不被转换成允许访问或可信成功。提交前失败回滚消息及锁；真实提交后 ack 或重读失败返回不可信的 500，关闭 Session，不自动写重试，新显式 GET 可确认 durable truth。rollback 不能承诺撤销已提交事务。

### 5. 统计是分组读取与纯合并

仓储分别读取章节及剧集两份 aggregate：自然 JOIN 任务→消息，剧集统计再 JOIN 章节；筛选 completed/failed；GROUP BY 原 model_name,status；COUNT 与 COALESCE(SUM(credit_cost),0)，仅 ORDER BY COUNT DESC。不增加最终排序、字符串归一化、状态 enum 或积分 clamp。

纯 statistics 按读取顺序维护插入有序映射：None/空名合“未知模型”，completed 加 calls 与 credits，failed 仅加 failed_calls。响应保持原 chapter/series 普通列表外形，不另加嵌套强类型限制。孤立任务自然不入 join，但原行保留；不按当前 actor 的 task.user_id 筛选，读取已授权章/剧集全部匹配任务。原实字“未知模型”与 None/空名合并为同一键；completed 的合法原负积分仍累加，不 clamp。组计数并列无 secondary sort；用无 tie 样本验证可确定顺序，不承诺数据库未规定的 tie 顺序。队列/账务生命周期留给独立模块。

### 6. 精确文件白名单和清单

新增生产源码（11）：

- backend/src/haoai_backend/chat_data/__init__.py
- backend/src/haoai_backend/chat_data/domain.py
- backend/src/haoai_backend/chat_data/errors.py
- backend/src/haoai_backend/chat_data/ports.py
- backend/src/haoai_backend/chat_data/application.py
- backend/src/haoai_backend/chat_data/persistence.py
- backend/src/haoai_backend/chat_data/tables.py
- backend/src/haoai_backend/chat_data/schemas.py
- backend/src/haoai_backend/chat_data/http.py
- backend/src/haoai_backend/chat_data/presentation.py
- backend/src/haoai_backend/chat_data/statistics.py

新增专项测试（7）：

- backend/tests/test_chat_data_domain.py
- backend/tests/test_chat_data_statistics.py
- backend/tests/test_chat_data_application.py
- backend/tests/test_chat_data_persistence.py
- backend/tests/test_chat_data_transactions.py
- backend/tests/test_chat_data_http.py
- backend/tests/test_chat_data_boundaries.py

允许修改既有代码（7）：

- backend/src/haoai_backend/app.py
- backend/tests/test_module_boundaries.py
- backend/tests/test_series_access_boundaries.py
- backend/tests/test_authentication_boundaries.py
- backend/tests/test_rough_cut_http.py
- backend/tests/test_production_notes_http.py
- backend/tests/test_asset_data_boundaries.py

app 仅新包挂载、必要工厂数量说明；六旧测试仅新增十方法集合、42→52 数量/相关冷工厂名称期望，原业务和副作用哨兵完整保留。pyproject、所有其他旧代码、旧测试、前端及前批产物均冻结。代码冻结图必须恰好以上 25 键。

本批五规划键为 .openspec.yaml、proposal.md、specs/backend-chat-data/spec.md、design.md、tasks.md，均位于本 change。事后说明仅四旧 EOF 追加：README.md、backend/README.md、docs/architecture/backend-module-boundaries.md、docs/architecture/module-api-compatibility.md；完整旧前缀不变。另新增 docs/architecture/chat-data-backend.md 及本 change 的 verification.md，不能提前写。最终七文档图是四旧、两新及 tasks；规划正文冻结后只允许 task checkbox 变化。

预计清单：422 基线 +5 规划=427；+18 新代码=445；+2 新文档=447。预计代码255，现有237仅7可变，其余230受保护；165历史与411不可变范围按根基线定义守护，不把这些预期数字当已执行结果。

### 7. 有意义的验证与证据分层

| 组 | 必需证明 |
| --- | --- |
| T1 DTO/HTTP | 十方法状态、extra-ignore、省略帧/显式null/负帧、空/任意字符串、资产 extra frame 被忽略 |
| T2 权限/目标 | 消息404与查章403顺序；conditional orphan 专用 FK-off 例；跨剧真实 FK body 插入；两个 PUT 不限资产类型；拒绝业务 DML0 |
| T3 SQL筛选/删除 | 普通列表含资产；asset_type 空串非NULL；bulk 全mode+可选负帧+零行；DELETE 前后完整 task/billing/queue 行守恒与统计 orphan 排除 |
| T4 统计 | 原 model/status 分组 count DESC 后合并插入顺序；None/空名合并；failedcredits忽略、其他模型名原值、章与同剧两查询 |
| T5 锁/更新 | 真 SQL existing own-expired/foreign/absent；仅两锁时间列变化；同content source UPDATE0仍 commit/readback；非同值仅content |
| T6 故障/竞争 | 两物理 SQLite 连接在读取后删除消息或锁，实际 UPDATE 零行失败、已加载单DELETE零行仍原commit/204；precommit全事务回滚；实际commit后ack/refresh抛错 durable GET、一次尝试无auto retry |
| T7 工厂/守恒 | 52登记、缺配置503零Session、显式resolver优先、旧四policy门槛、冷导入无外部副作用、旧七真实diff与所有保护hash |

专项 TDD 由获授权实现者执行，保存失败及最终日志；Sol 独立完整源/测试只读审查；根再独立完整 backend 回归、语法/清单/固定47来源与既有来源合同检查。实际 loopback 使用自身临时 SQLite、真实 JWT、SQL访问策略，覆盖十方法及既有 auth/access/private smoke；合成业务行不冒称生产数据、生产会员或 provider。故障为受控注入，SQLite 不证明 PG 并发；可用 PG 方言 SQL 编译时仅声明编译。

root lab 不添加生产实验 endpoint/header；私有 fault 控制留在自有 harness。记录真实有序 server trace、完整行前后及业务 Session commit/readback，资源须满足成功关闭计数、错误0、自有 listeners 移除、Engine dispose、client/thread/PID/端口/tmp 精确清理。仅自己资源，无全局无外连推论。本批不存在冻结聊天 TS consumer，HTTP 响应直接合同核验，不新增 frontend/parser 或借前批 TS/浏览器结果充数。

## Risks / Trade-offs

- [旧条件访问及 path/body 错配] → 明确作为兼容边界；安全修复需要独立规格，不利用本批悄悄增强权限。
- [同值 ORM 净变化与 Core 显式 DML 不同] → 第一次赋值意图决定 content UPDATE，真实 SQL 观测 source DML、commit 和重读；不添加并发媒体 CAS。
- [提交后 ack/refresh 失败] → 不返回可信成功、不重试；保留原失败证据和新 GET durable truth，不宣称 rollback 可撤销提交。
- [任务投影不足以证明完整历史] → 测试/lab 使用完整 owner DDL 与所有实际任务/账务/队列行 snapshot；生产仅 SELECT。
- [SQLite 与实际 PostgreSQL 不同] → 本地真实存储和顺序交错只证明本机机制，生产 PG/全站/部署另属范围，不作为本地完成强加门槛。
- [共享旧 checkout 可被他人改变] → 固定 git-show 字节守恒；现场元数据如实记录，不恢复/删除他人文件，不要求其它会话停工。

## Migration Plan

根审读五份完整规划（包括 .openspec.yaml）并实际 strict/status/apply 后才冻结和授权 Luna 实现。实现按 domain/DTO→SQL/事务→HTTP/工厂分段验证；所有新增路径与旧七差分绑定实际 SHA。未知新增路径必须先向根报告，不能顺手扩大白名单。

根完成真实源码、全套和 loopback 证据后，才允许四旧 EOF 说明、两新说明与 task checkbox 收口，再实际运行最终 OpenSpec、文本、精确清单与保全检查。没有生产部署或 schema 迁移；本地回退仅处理本批获授权新目录变更，不操作旧站。

指定 GPT-5.6 Sol/xhigh Grillme 离线外审独立未执行，内部 Sol/Luna 与根结果不替代；本地接受可保留该唯一外审待办继续后批，完整原目标仍 active。模型请求路线与 runtime 未独立核验分别记账。
