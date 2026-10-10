# 任务观察与取消控制后端

`haoai_backend.task_observation` 承接已有任务和提交回执的查询、本人任务列表、受权限控制的请求查看、AI 审核/批量优化观察及取消控制。它迁移八个 GET 和一个 POST，使用固定兼容来源提交 `23403806898550a7668a6ee7c0c457315655c39b`，不迁入生成、账务受理或 Worker 生命周期。当前源码独审、根全量后端测试、真实 JWT/SQL/TCP 和实际响应消费现有 TypeScript parser 均已通过；七路径文档已按授权安装并经独立文档审查接受；普通 OpenSpec strict/status/apply 已在 11/13 状态通过，并在勾选 5.2 后的 12/13 状态再次全部通过；当前 tasks 为 12/13，本地 CLI 收口完成。实际记录见[本批 verification](../../openspec/changes/modularize-backend-task-observation/verification.md)。

## 接口与身份

| 方法 | 路径 | 身份 |
| --- | --- | --- |
| GET | `/api/chat/tasks` | 有效登录 |
| GET | `/api/chat/submissions/{operation}` | 有效登录 |
| GET | `/api/chat/tasks/list` | 活动会员 |
| GET | `/api/chat/tasks/{task_id}/request` | 活动会员 |
| GET | `/api/chat/ai-review/counts` | 活动会员 |
| GET | `/api/chat/ai-review/{task_id}` | 活动会员 |
| GET | `/api/chat/batch-optimize/running` | 活动会员 |
| GET | `/api/chat/batch-optimize/{task_id}/status` | 活动会员 |
| POST | `/api/chat/batch-optimize/{task_id}/cancel` | 活动会员 |

两个 account 接口允许会话仍有效但会员过期的用户查询本人回执；其余七个接口要求活动会员。account resolver 优先取显式 `resolve_task_account`，否则调用公开 `AuthenticationService.authenticated(..., require_membership=False)`，不使用活动会员 resolver 替代。active resolver 依次取显式 `resolve_task_active`、既有有效 resolver，否则调用同一公开上下文的 `require_membership=True`。显式业务 `session_factory` 优先于认证 runtime 的 factory；认证与业务仍分别创建自己的 Session。

认证适配在公开上下文内取得 `principal.user.id`，退出上下文后才返回 `TrustedActor`；认证原有维护提交/关闭在业务用例前结束。认证错误保留 status/detail/headers，不引用私有认证 helper。这个顺序已有源码独审和根实际 JWT/auth SQL 的共享 request_id 时间线证据：认证 commit/close 成功先于业务读取。fake resolver 的早期专项测试单独保留，不代这次实际证据。

## 模块职责与依赖

| 文件 | 职责 |
| --- | --- |
| `domain.py`、`errors.py`、`ports.py`、`__init__.py` | 显式记录、稳定错误、窄 reader/UoW/cancellation 合同及公开导出 |
| `receipts.py`、`payload.py` | 两种回执、请求折叠、审核/优化投影及列表公开 helper 的调用 |
| `application.py` | 九个用例的查询、访问判断、清理与取消顺序 |
| `authentication.py` | 公开 account/active 认证上下文适配 |
| `persistence.py`、`tables.py` | 供应业务 Session 的 Core 查询与最小表投影 |
| `cancellation.py` | 显式独立连接的 status-only SQL 写入 |
| `http.py` | 普通参数、两个身份依赖、静态路由优先及已知错误转换 |

列表只复用公开纯函数 `teams.reporting.task_payload.collect_task_context` 和 `project_task_items`；团队权限只复用公开 `teams.policy.has_team_permission`。不借团队私有 persistence/UoW，不修改团队源码，也没有另建一套团队或账务政策。应用层依赖窄端口，不取得任意 SQL、Engine、旧应用、队列或 provider。

Core 表只表达查询所需列，不是完整生产 schema 或 migration；生产模块不反射数据库、创建 schema 或创建 Engine。验收 fixture 使用完整 owner DDL、外键开启及非空任务、提交、计费单元/步骤、credit 自引用、执行、外部结果、媒体、private 和认证行，其守恒证据不能用薄查询表代替。

## 查询事务与请求权限

八个 GET 都使用一份供应的 request-scoped 业务 Session。UoW 只暴露读方法和 rollback/close，不提供 commit；用例在 finally 中先 rollback，再 close，失败时仍尝试两者。业务路径没有 commit、flush 或 DML。认证 Session 仍可能查询、更新会话/last_seen 并提交，因此“业务只读”不能扩展成整个请求没有 SQL 或提交。

请求详情先按任务 ID 全局读取，missing 先返回 404“任务不存在”。本人直接获准；他人的 superuser 特权只在该请求内查询真实 `users.is_superuser`，不取 header/body/前端 flag，也不让其它八个端点跨用户。否则只有 truthy `team_id` 才进入本人 membership、公开 `view_tasks` 权限及目标用户 membership 检查，保留“你不是该团队成员”“没有执行该操作的权限”“无权查看该任务的请求体”的顺序。不新增 team 存在性 404、剧集访问或任务类型门禁。

本人 membership 两次 SELECT 仍实际执行；同一主键在当前 UoW 内映射为首次观察的字段，保留来源 ORM identity 行为，不用后一次数据重置权限快照。团队 owner 和 permissions 解码继续由公开纯政策负责。

## 两种回执

任务回执 `GET /tasks` 的 `task_id` 与 `message_id` 互斥，以原字符串过滤，空白只用于校验而不改写 SQL 值。任务 ID 未找到本人任务返回 404；message 匹配超过一个返回 409，无匹配仅返回 `status/result` 两个 null。

正常任务回执恰有十三键：`id`、`type`、`message_id`、`status`、`result`、`billing_status`、`quoted_amount`、`progress`、`progress_message`、`external_task_id`、`external_provider`、`created_at`、`updated_at`。它按计费单元 `created_at ASC` 取 first，不新增同时间次排序；只有没有单元时才回退任务的 `billing_status/credit_cost`。已有单元的零值、空值不回退，result 原样保留，progress 沿用 `or 0`，负值和未知业务值不被统一修正。

提交回执 `GET /submissions/{operation}` 的 `idempotency_key` 是必填 query，header 不能代替。缺 query 保留标准 422；进入用例后先校验 operation 为 `image.single`/`video.single`，再按 Python 字符长度检查 key 为 1–255 个非空白字符，查询仍用原未 trim key、用户和 operation，不筛提交状态或任务 type。task_ids 必须是恰好一个元素的 list，对应任务属于提交者，计费单元必须恰好一个；多单元保留原多行异常，不改取 first。

正常提交响应七键：`submission_id`、`operation`、`task_id`、`message_id`、`status`、`quoted_amount`、`billing_status`。缺提交为 404；集合不一致、任务缺失/错 owner、缺单元为各自原 500。该 GET 不新增 request digest 校验、受理、重提、扣费、provider 调用或退款。

## 列表、请求与 JSON 兼容

列表 count/查询均限本人，按 `created_at DESC` 和原 offset/limit 查询。page 默认 1，page_size 默认 50，都是普通 int；不 clamp 零值、负值，不设置新上限或只准 10。批量加载消息、章标题及 character/scene/prop/storyboard 名称，投影十九个基础键和动态 `prompt_id`。普通消息 frame_index 加一但 chapter_id 保持 null；孤立任务、部分 JSON 赋值和公开 helper 的原异常/截断保持。failed 的 truthy result 最多 5000 字符，其它过长或 data: result 置空，request_data 最多 200 字符。

详情 request_data 为空时是 `{}`，解码失败为 `_raw`；合法 scalar/list 原样递归处理。data: 或长度大于 500 的纯 base64 字符串折叠为 `<base64 len=N>`，深度大于 6 停止折叠。各端点的 JSON 行为并不相同：审核 counts 保留带空格 SQL LIKE 和 source_message_id 复核；解码失败跳过，合法非 dict 或不可 hash prompt_id 仍可能 generic 500。running 保留候选顺序、解码失败跳过和无匹配 task=null；合法非 dict 不被预校验成 422。两个按 ID 状态读取只筛本人、不加 type；审核 result 原样，优化 truthy result 仅在 JSONDecodeError 时回退 raw，合法 scalar/list 保留。

## 取消信号与独立写事务

取消入口必须接好业务和独立 writer，否则返回相应 503 且不打开业务 Session。已接线后先读取本人任务，missing 为 404；初读只有 `queued`/`processing` 才查询 registry，对 truthy 信号先调用 `set()`，再调用 writer。其它状态不取信号、不调用 writer、不打开独立事务，仍响应任务 ID 和 `status: cancelling`，不加 type 门槛。

writer 的 connection factory 必须由外部显式提供，例如已配置 Engine 的 `begin`，不能从业务 Session 私有 Engine/URL 发现，也不会加载旧全局 Engine。独立 context manager 负责 commit/rollback，执行的 SQL 保持：

```sql
UPDATE ai_tasks SET status='cancelling'
WHERE id=:id AND status IN ('queued','processing')
```

仅 status 改动，无 owner/type/claim/version/CAS 条件，不使用带 onupdate 的 Task 表扩大写集合。writer 的 rowcount bool 被应用忽略，零行仍正常 commit/200；`updated_at`、claim/lease/generation/token/recovery/cancel metadata、账务与消息/章节行保持。第二物理连接 B 在初读结果消费后先提交终态或删除，可让 A 的 UPDATE 为零行；B 已提交差异必须单列，A 的 rollback 不会撤销它。这是本地 SQLite 交错测试，不是 PostgreSQL 并发结论。

SQL 或 precommit 失败会回滚独立未提交写入，已经 set 的信号不撤销。真正提交后 acknowledgement 丢失可返回 500，而状态已经 durable；后续显式 GET 可观察，不自动重放 SQL、信号或受理。返回 200/cancelling 只描述取消申请，既不证明 Worker 已停，也不证明退款。registry 为 None 表示没有外部供应的信号引用，不创建线程，不证明线上 Worker 联通。

## 装配与当前验收范围

组合根仅在调用用例时由显式 factory 创建业务 Session，独立 writer 也只保存供应的 connection factory。`is None` 判断保留 falsey resolver/factory/registry；默认创建应用仍能精确登记 91 个方法且不创建 Engine、Session、schema、provider 或 Worker。缺必要配置以 503 失败关闭，零业务 Session。原 82 个方法及旧四 notes/rough 私人接口、章节素材替换的显式 `series_access_policy` 接线规则保留，没有通过新模块放宽。

根实际全量后端为 617 passed、20 warnings，242 个 Python 文件 AST/内存 compile 通过，31 代码与全部 592 个冻结文件保护保持。作者专项 SQLite 测试、独立源码审查、根实际全量运行、联合 JWT/SQL/TCP 和真实 body→`parseMyTaskPage` 分层记录。TCP04 的 29 个完成请求覆盖九个新增方法及关键拒绝/故障，176 SQL 事件、51 Session、九个独立 writer/竞争者事务和七次信号分别计数；24 个 owner 表的预期差异匹配，四任务 status 变化及一任务竞态删除之外的字段/行守恒，清理 strict AND 通过。Node 20.20.2 使用原两份 TS 源消费真实 body，得到 total13/page1/page_size10/10 个任务；它仅证明局部 parser 消费，不代表完整 React。本批文档安装与独立审查已完成；普通 OpenSpec strict/status/apply 已在 11/13 状态通过，并在勾选 5.2 后的 12/13 状态再次全部通过；当前 tasks 为 12/13，本地 CLI 收口完成。PostgreSQL、provider、Worker、SMTP、真实生产计费、部署和指定 GPT-5.6 Sol/xhigh Grillme 外审各自保留未验证边界，不额外成为这九接口的新业务政策。后续完整后端、完整 React 和最后语言评估继续进行。
