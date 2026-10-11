# 通用任务与管理员任务后端架构（2026-10-11 验收记录）

状态截至 2026-10-11 UTC：本文件记录本族架构、源码审查与 Root 实际运行证据。I60 独立源审在其 60 code path 范围内 P1/P2=0，并接受 root integration/helper implementation；它本身不等于运行时验收。G08、A07、P03 及 harness/runner 的 source reviews 分别接受其 consumer implementation 范围，具体边界见验证记录。Root 已在 run05 对冻结矩阵完成真实本地 root acceptance：175/175（G119/A23/P33），不是全项目验收。

## 方法与 DTO

| 范围 | 方法 | 路由 | 成功响应关键字段 |
| --- | --- | --- | --- |
| 通用任务 G01 | 创建 | POST /api/tasks | id、status、credit_cost |
| 通用任务 G02 | 更新本人任务 | PUT /api/tasks/{task_id} | id、status |
| 通用任务 G03 | 更新本人进度 | PUT /api/tasks/{task_id}/progress | id、progress、progress_message |
| 通用任务 G04 | 查询外部任务状态 | GET /api/tasks/{task_id}/external-status | unknown/detail、completed/result、failed/error 或 processing |
| 管理员 A01 | 跨账户列表 | GET /api/admin/tasks | data、total；每行固定九字段 |
| 管理员 A02 | 跨账户详情 | GET /api/admin/tasks/{task_id} | 固定九字段 |

A01 行 DTO 精确包含 id、user_id、type、status、credit_cost、message_id、model_name、created_at、updated_at。falsey model_name 映射为空串。A02 DTO 精确包含 id、user_id、type、status、message_id、request_data、result、created_at、updated_at；request_data 和 result 原样保留，不解析、截断或补入列表字段。两个 DTO 分开映射，不能把详情当列表行。

## 认证与组合根接线

公开认证 facade 为 authentication.task_identity 中的 TaskIdentityResolvers 和 create_task_identity_resolvers(runtime, *, unit_of_work_factory)。构造函数只返回 closures，不调用 Session factory。active closure 用既有 authenticated 上下文要求会员有效；admin closure 不要求会员有效，但在同一真实 SQL principal 上检查 is_superuser。closure 在上下文内保存 user_id 到 TrustedActor，只有 authenticated 上下文退出且认证 UoW 的 close 成功后才把 actor 返回给路由。认证失败、提交/close 未确认时不释放 actor。HTTP 包装通过 Starlette run_in_threadpool 执行同步认证，并保留 AuthenticationError 的 status_code、detail 和 headers。

create_app 的本族三个新增 keyword 为 resolve_generic_task_active、resolve_admin_task_actor、provider_status_runtime。通用 resolver 优先级为：显式新 resolve_generic_task_active → 调用方显式提供的旧 resolve_actor → public facade active。这里特意使用显式 resolve_actor 参数；不会用 create_app 自动推导的 effective_resolver 替换它。管理员优先级为显式 resolve_admin_task_actor → public facade admin，不回退 generic active。分支仅以 is None 判定缺省，因此合法 falsey callable 不会丢失。旧 Task Observation 四个 keyword resolve_task_account、resolve_task_active、task_cancellation_connection_factory、task_cancellation_signals 及旧业务 privatepolicy/访问策略顺序继续保留。

session_factory 为空时，通用/admin UoW factory 不构造 Session；路由在调用身份端口前检查所需 UoW/身份缺项并返回 503。runtime、Session、Engine、HTTP client 与 Worker 不在模块导入或 create_app 组合阶段创建。run05 已在冻结的受控本地 cold/resource 验收范围内实际通过：175/175 cases、362/362 cleanup reports 均通过；post-exit 审计中 159 个 loopback port 均实际 rebind。该结果只描述本族受控本地运行验收；源码结构本身不能代替实际执行验收，且 PostgreSQL、真实 provider、browser 与 deployment 未验收。

## 通用任务写入与事务

generic_tasks 的 HTTP、application、ports、SQLAlchemy ORM persistence 和 tables 分层。application 只依赖 TrustedActor、provider status port 与本包窄 UoW，不持有 SQLAlchemy Session。持久化适配接收应用组合根传入的 Session factory，并假设该 Session 以 autoflush=False 配置；生产模块不创建 Engine/schema。

G01 先拒绝 batch-optimize、batch-image、image-single、video-single、ai-review 五种受控任务类型，再按 model、model_name、modelId 顺序从 request_data 取模型。非法 JSON 或 TypeError 沿源行为忽略；合法 JSON 的 null/list/scalar 在 .get 处仍可能抛出未捕获 AttributeError，不新增宽化验证。创建 processing task 时 message_id 为空串，falsey request_data 保存为 {} 字符串，先 ORM add 暂存。费用只有大于零才锁钱包；缺钱包或余额不足实际 rollback 并返回 402。成功时扣款、记录 amount 为负数的 usage 账项，key 为 generic-task-charge:{task.id}，task/wallet/usage 在同一次 commit。创建响应在 commit 后执行真实 Session.refresh 并读取刷新行。零或负费用按原数值保存，不锁钱包、不生成 usage。

G02 查本人任务时使用 id+user_id 锁定；G03 查找本人任务但不加该任务行锁。两者都先对任务是否存在返回 404，再执行三来源受控 guard：五个精确类型、任意关联 BillingUnit、同用户 Submission.task_ids 是 Python list 且精确包含 task id。任一 guard 命中返回 409。G02 只修改 status、非 None result 与 updated_at；None 保留旧 result，空串可清空。failed 且 credit_cost 为正数时，持有钱包锁并读取所有同用户/任务/type=usage/amount=-cost 的 debit；只有恰好一条且尚无 related_debit_id 指向它的 refund 才补回钱包并追加 refund，key 为 generic-task-refund:{debit.id}。wallet 缺失、debit 为零条/多条或 refund 已存在都不猜补；重复 failed 不会重复退款。

G03 将 progress 限定在 0 到 100，只有非 None 的 progress_message 才覆盖旧值；status 是任意字符串，不触发 Worker signal、重新派发、lease 恢复或新 BillingUnit 流程。更新响应在 commit 后先 expire ORM 对象再按主键读回。

提交前失败会尝试一次 rollback。commit 已被调用后发生异常、acknowledgement 不明或 readback/refresh 失败时，代码不自动重放创建、状态变更、debit 或 refund；HTTP 错误不能证明写入未 durable，应由调用者显式读取核实。Session close 在每个方法的 finally 中执行。代码级顺序与 root 实际事务阶段、提交完成、资源成功关闭是不同证据层。

## G02 deferred-FK 失败提交的 fixture 边界

run05 中 `G02-REFUND-DEFERRED-FK-ROLLBACK` 实际通过。受控 SQLite acceptance fixture 观察到真实物理 COMMIT 抛出包含外键失败的 IntegrityError，提交前后底层事务均仍 active；仅该 fixture adapter 随后在同一个 DBAPI handle 调用已观测的 rollback，并记录事务 inactive，适配器最后恢复原 delegate。该路径返回 500，保留原 COMMIT 异常。它是测试夹具对 SQLite failed-COMMIT 的物理 rollback 归一化；不证明产品 `Session.rollback()` 或 `Session.close()` 的保证，也不提供 PostgreSQL 行为证据。run05 consumption 收据只投影了此 case 的相关故障/清理 trace，不声称对 80 MB 原始 stdout 做了全文语义审阅。

## 管理员查询

管理员身份由公开 admin resolver 给出可信 TrustedActor；默认 facade 要求真实 SQL is_superuser，未要求 active membership，也不使用团队角色、premium 或客户端标志授权。A01 不加当前用户过滤，默认 skip=0、limit=10、sort_field=created_at、sort_order=desc。user_id、type、status 只在 truthy 时精确过滤；truthy model_name 使用 ilike('%输入%') 并保留 SQL 的 %/_ 通配。先单独执行 count，再执行列表读取。排序按源 getattr(AdminTask, sort_field, AdminTask.id) 解析，只有 sort_order 精确为 desc 才降序，否则升序；单字段排序、不追加第二键，分页不钳制。已存在但不可排序的列仍保留数据库/属性错误。

A02 仅按全库 task id 首条读取，不加 user_id 条件；缺失返回 404“任务不存在”。两方法返回对应的九字段 DTO。admin application finally 执行业务 UoW rollback 后 close，不提交业务写入；认证侧的既有 last_seen/session 维护若发生，应与业务只读分层记录。

## provider_status 外部状态读取

G04 先读取本人任务。缺 external_task_id 或 external_provider 时返回 unknown/detail。之后 provider status port 的 prepare 在 poll 异常转换 try 之外运行：缺少 runtime/configuration session factory 返回明确 503；配置不存在或不支持协议返回 unknown/detail。prepare 以独立配置 Session 先取同 provider 的活动 SystemModel.first，未命中才取 ModelConfig.first，不添加 user/category/model_name/order 条件，并在返回前 finally close。配置/secret/decrypt/close 失败不能变成 poll unknown。

credentials 使用显式 runtime secret_key：非空 api_key 每次 service 实例先生成或复用 Fernet，即使明文也先构造；只有 gAAAAA 前缀才 decrypt 后 UTF-8 decode；空 key 直接空串。Fernet 缓存在该 service 实例的 CredentialDecryptor 内，不是进程级缓存，不读取环境或真实凭据。prepared query 每次只执行一次 GET，按 context 退出 response/client；HTTP 200 后解析 JSON，非 200 映为 pending；30 秒为默认 timeout，manxueapi 为 60 秒，zhangyuge 使用 IPv4 connector。外部查询只观察状态，不调用旧 runner、下载、OSS、队列、Worker、账务退款或任务终态 UPDATE。

| provider | 源状态处理/专属行为 |
| --- | --- |
| zhangyuge | 精确 completed/failed；30 秒；显式 IPv4 connector |
| manxiaobai | 保留源大小写：completed/done/SUCCESS/succeeded 与 failed/FAILURE |
| xai | done 为 completed；failed/expired 走失败数据；expired 的字符串错误在原 generic error.message 路径可变成 unknown |
| manxueapi | completed/success/succeeded 与 failed/error/cancelled；60 秒 |
| geeknow | status lower 后匹配 completed/success/succeeded 与 failed/failure/error/cancelled/expired |
| snumom | 精确 completed/failed |
| biglongxia | 精确 completed/failed |
| heima | 读 data.data 内状态或顶层状态并 lower；完成时使用 /content |
| yu25 | lower 后按自身 completed 与失败集合匹配 |
| yu25_beiyong | 固定有序状态路径，trim/lower 后匹配；保留固定 URL/正文提取顺序，缺结果时走 /content |
| haoai | 精确 completed/done 与 failed；路径保留 /openai/v1/videos，结果可回退 /content |
| yiyun | 精确 completed/success/succeeded 与 failed/error/cancelled |
| suqing | 精确 completed/success/succeeded 与 failed/error/cancelled |

除每个 protocol 明确 lower/trim 的分支外，不统一规范大小写。完成结果的提取优先级仍依各 provider protocol，不合并成单一通用 parser；成功结果为空或 None 时不擅自补值。HTTP/resource owner 的实际关闭成功需要对应专项证据，不能仅凭 context manager 源码推定无泄漏。

## 保留项与验收边界

I11 author freeze SHA `f5d36d1030e9bbacc246ce40963096e69ea4e026de9bc4ce758e3e8278fa519a` 记录 97 个实际 method/path，原 91 项保留并新增六项。I60 源审 SHA `b2b7a3ed245a224a5e1d5d3bad5c01d4a6e22dfe95c7bc2940637874b9c952cb` 对 code60 gate 的 P1/P2 为零，并接受 root integration/helper implementation；G08 review08 SHA `775810db88cec61da5dfd02659c5cf9a9ac95a5b5eea92611a46e0752be89583`、A07 review06 SHA `51d9bd89253e886cdabff497b5f135553d40a6df262d99e05e041dfcb86afb87`、P03 review03 SHA `756395c8eb9f2222f3b365eeed5fb638befb964115748a164ebf39ecbbd566ba` 均是各自 source/consumer implementation 范围的接受记录；它们不替代 root 执行。

Root run05 parent receipt `haoai-next-root-generic-admin-task-local-acceptance-05.json` SHA `a7a9bc103b34f647302c1b5bb1b8374fb4300ef7ed65bbca3e35447746a9f61f` 绑定 runId `dbcead9c-e236-4664-87c2-50280761c45b`、child PID 2323、wait/exit 0 和 148.3319877909962 秒。Root consumption SHA `ca80ceb3cff1040b0535181d1bca5e0993f6fd1c582f605771a635c57c87a4cf` 记录 175/175、G119/A23/P33、matrix04 匹配、362/362 cleanup reports 通过及 target all646 SHA before/after/current 完全一致；post-exit audit SHA `194ce86e8bffd9f82c8da59a30411c54a3ae7ee175fbad5085b88febccd5111f` 记录 159 个 loopback 端口实际重绑定均通过、child PID 已退出、parent tmp 已清除。

保留历史不改写：run01 在注册阶段失败，0 case 执行；run02 在 34/175 后超时/终止；run03 为 150/175，catalog02 更正 run03 当轮两个 A missing-wiring case 的错误组件归属及分类投影，分组为 G96/119、A21/23、P33/33，合计 150/175 不变；原始运行结果、旧收据与 raw failure 均保留。run04 为 174/175，G02 deferred-FK case 当轮仍失败。run05 才是在 G08 fixture adapter 修复后同一 case 的新实际通过。此前 root 完整后端 pytest 02 为独立的 732 passed、38 warnings、35.89 秒，首次 01 的 TMPDIR 环境失败也保留。

作者对 G08 静态源码的实际检查是 `ast.parse(source_text)` 后对解析得到的 AST 调用 `compile(parsed_ast, filename, "exec")` 并丢弃 code object；没有执行源码，也不是对 source text 直接调用 `compile(text, ...)`。本文及 run05 不声称 PostgreSQL、真实外部 provider/真实凭据、React browser、部署、指定 GPT-5.6 Sol/xhigh Grillme 外审或整体项目已验收。
