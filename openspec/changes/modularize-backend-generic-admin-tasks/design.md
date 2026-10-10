# Generic Admin Tasks Design (Draft)

## Context

本五稿是独立 EVID 草案。来源为固定旧仓库 /Users/yanghaibo/data/projects/ai/haoai-修改版/haoai.zhuwh.com 的 Git object 23403806898550a7668a6ee7c0c457315655c39b，不在 target 查找该提交，也不读旧工作树当固定正文。本作者消费 named next-scope-analysis01 的六方法与十三协议完整分析、root dependency-closure01 的最小 crypto/OSS 补读及 Task Observation baseline01 的 55 图与旧 byte copies；本轮没有重新读取 Git object 或执行 Git。

必要旧语义范围为 tasks.py 1–299；admin.py 1–58、625–654；async_task_runner.py 1–58、119–129、242–1045；crypto_service.py 1–31、41–53；oss_service.py 1–22；object_storage.py 22–35、98–159 和已记录的 module surface；public_url.py 16–50 和 module surface；模型／auth／team/database 必要窗见冻结收据。runner 的 run/update/recover 其余正文未在此族复审。不能把来源候选 60 或哈希检查写成全部方法的递归全文语义 review。旧分析中的四新增候选／59与未闭合 crypto/OSS 是历史状态，由 closure01 更新为五新增／60和已闭合，两份旧分析保留。

当前 Task Observation 的来源 55、原代码冻结 31 保持。app 和旧十登记文件正在上一批改写，本轮不读其进行中正文作为接口；旧 app 只读 baseline byte snapshot，并以已安装 Task Observation design 的 keyword 合同作未来接线约束。现 82/166 是命名 baseline 的历史计数；S 首轮登记 91 与作者专项通过不等 root accepted，不能预扣。下一实际 B/C/H、已验收路由集合及所有旧可变字节必须在上一批验收后重建；本文算术只表达未来条件。

## Goals / Non-Goals

完整六方法和十三种真实一次查询协议；保留错误顺序、事务与字段差异；保持默认 app 冷启动无资源；为 G/A/P 并行提供冻结公开合同和独占路径；用真实本地 SQL/JWT/TCP／loopback与全 owner 守恒验收。

本批不迁生成六方法，不启动 Worker、queue、claim/lease/fencing/recovery，不运行下载或 OSS，不把 generic refund 改为 BillingUnit 结算，不实现完整 React，也不做最后语言结论。不增加 type/分页/admin/member/重放政策，不以始终 unknown 缩减第四方法。真实 PostgreSQL、实际供应商、浏览器与部署另层记录；不增为本机交付门槛。

## Decisions

### 1. Complete method and DTO contract

| ID | 方法 | 权限与输入 | 成功响应 |
| --- | --- | --- | --- |
| G01 | POST /api/tasks | active；type:str；credit_cost:int=0 可负；request_data:Optional[str]=None | 200；id/status/credit_cost |
| G02 | PUT /api/tasks/{task_id} | active；own task；status:str 任意；result 可空 | 200；id/status |
| G03 | PUT /api/tasks/{task_id}/progress | active；own task；progress:int；progress_message 可空 | 200；id/progress/progress_message |
| G04 | GET /api/tasks/{task_id}/external-status | active；own task；无新增 query/body | 200；unknown+detail、completed+result、failed+error 或 processing |
| A01 | GET /api/admin/tasks | JWT+SQL superuser；无 active gate；全账户过滤与分页 | 200；data/total；每行精确九字段 |
| A02 | GET /api/admin/tasks/{task_id} | 同 A01；全库 id first | 200；精确九详情字段 |

A01 行字段为 id/user_id/type/status/credit_cost/message_id/model_name/created_at/updated_at；A02 为 id/user_id/type/status/message_id/request_data/result/created_at/updated_at。列表 model_name falsey 变空串；详情 request_data/result 原样，不解析、截断或补列表字段。HTTP 使用普通来源类型及 defaults，不增加 response_model 收紧、status enum、正整数 clamp 或新幂等 header。

### 2. Public identity facade, auth close before business

当前 authentication/__init__.py 只公开 AuthenticationRuntime；private application 中 authenticated 的方法语义虽可复用，业务包不能把 private import 当公开合同。未来新增 authentication/task_identity.py，并仅增量修改 authentication/__init__.py：保留 AuthenticationRuntime，公开 TaskIdentityResolvers、create_task_identity_resolvers 和原 AuthenticationError。其他认证源码保持 byte 守恒。

公开工厂合同为 create_task_identity_resolvers(runtime, *, unit_of_work_factory) → TaskIdentityResolvers；factory 只构造惰性 closures，不调用 Session 工厂。TaskIdentityResolvers.active(authorization:str) → TrustedActor；admin(authorization:str) → TrustedActor。认证模块内部合法使用 AuthenticationService.authenticated；active 使用 require_membership=True，admin 使用 False 并在上下文内检查真实 SQL principal.user.is_superuser，拒绝沿固定 get_current_admin 的 status/detail。先捕获 user_id 到不可变 TrustedActor，with 成功退出且 auth Session.close 成功返回后才 return actor；close/commit/认证失败不得释放 actor 或进入 business。facade 不暴露 principal、live UoW、claims 或客户端 superuser 标志。AuthenticationError 的 status/detail/headers 由本批 HTTP 适配按原值转换。

JWT/password_version/session/last_seen/legacy-jti 维护沿原认证顺序执行；其 auth Session 与业务 Session 分开，不合并钱包或管理员查询事务。unknown auth commit 无重放，业务零 Session/SQL。admin 不使用 resolve_business_actor、Team role、premium 或 view_tasks 代替；普通会员即使有效也不是 admin，过期 SQL superuser 仍可 admin 查询。

未来 create_app 新增 keyword resolve_generic_task_active、resolve_admin_task_actor、provider_status_runtime。generic 优先显式新 active resolver，再既有显式 resolve_actor，再公开 factory 的 active；admin 优先显式可信 admin resolver，再公开 factory.admin，绝不 fallback generic active。所有缺失判断用 is None，不丢弃合法 falsey callable。新 kwargs 必须保留上一批 resolve_task_account、resolve_task_active、task_cancellation_connection_factory、task_cancellation_signals 及原 privatepolicy/effective resolver 条件。公共 facade/type exports 与 app 最终接线为单一 writer。

### 3. Narrow generic UoW with real ORM transaction

generic_tasks 纯应用只依本包 domain/ports、公开身份及 provider_status port，不依 SQLAlchemy、旧 app、旧 runner、queue、Worker 或其他包私有 persistence。冻结窄 ports：GenericTaskUnitOfWorkFactory；stage_processing_task、load_owned_task_for_update、load_owned_task_first、controlled_type/billing/submission 读取、lock_wallet、lock_matching_usage_debits、find_related_refund、stage_usage/stage_refund、commit/rollback/close、refresh_created_task 和 source-compatible postcommit response 读取。业务接收 domain value，不接收任意 SQL 或 Session。

persistence/tables 属 G，使用 supplied Session 和私有 ORM 映射，声明所需真实列／默认，不 create_all、reflect 或建 Engine。源 autoflush=False 是合同：factory 供应该设置，stage task 只 Session.add，不 flush、不 Core INSERT；钱包锁前真实 SQL trace 必须无 task INSERT。配置错误不得用 early insert 修补。保存刚创建 ORM entity，commit 后 Session.refresh(entity) 真实按 PK 读取，再逐响应字段读值；不以 commit 前 DTO 或内存复制冒充 refresh。G02/G03 保留 commit 后 ORM 属性读取／过期 PK 读回与原错误，不返回 commit 前快照。真实 Session commit/rollback 完成与 attempt 分开记录；Session.close 只有 delegate 成功返回才计 cleanup 成功。

G01 顺序：精确五类型 batch-optimize/batch-image/image-single/video-single/ai-review 先 422 → JSON 提取 model、model_name、modelId → 生成 id → 暂存 processing task，message_id 空串，falsey request_data 保存 '{}' 字符串 → credit_cost>0 才锁 UserCredit → 缺 wallet/不足 rollback 后 402 → 扣 credits 并 stage type=usage、amount=-cost、business_key=generic-task-charge:{task.id} → task/wallet/log 同一次 commit → 真实 refresh → 200。零和负费用保存原值，不锁扣钱包、不产生 usage。没有 TaskSubmission/BillingUnit/Worker 或客户端幂等键。

JSONDecodeError/TypeError 保留忽略行为；合法 JSON 的 null/list/scalar 在 .get 处未捕获 AttributeError 仍失败。不能加 object validator 或将错误默默变成功／422。model 值与 falsey 顺序按源保真。

G02 先 id+user 锁 own task；G03 先 id+user first，不加该行锁。missing 404 在 guard 之前。guard 顺序：精确五类型 → 任意 task_id BillingUnit → 同用户真实 Submission.task_ids 为 Python list 且 exact contains task.id；任意命中 409。不得用 substring LIKE、只当前 operation 或非 list／坏值命中替代。guard 在赋值前；读取所有同用户必要 submissions，保留真实 JSON 列解释。

G02 只修改 status、非 None result、updated_at；None 保留、空串清空。G03 progress clamp 0..100，仅非 None progress_message 更新，updated_at 沿原 UTC 无时区规则。任意 status 包括 cancelled/queued 仅是字符串，不触发 signal、派发、lease 重置或新退款引擎。source-compatible 属性读回错误保留。

### 4. Refund evidence and unknown outcome

只有 status='failed' 且 credit_cost>0 才尝试退款：wallet FOR UPDATE → 同 user_id/task_id/type='usage'/amount=-credit_cost 的全部 debits FOR UPDATE → 恰好一条 → 查 type='refund' 且 related_debit_id=debit.id 的已有 refund，不附加新 user predicate → 无关联退款才加余额并 stage refund。唯一 key 为 generic-task-refund:{debit.id}。缺 wallet、无或多 debit、已有 refund 均不猜补余额，仍提交 status。重复 failed 可更新 updated_at，但无重复退款；之后改其他状态不逆转退款或再扣费。

真正未提交失败 rollback；提交成功而 ack 不明、commit 完成后异常或 refresh/readback 失败可已经 durable，HTTP generic500 不自动重放 POST、debit/refund 或状态更新。注入故障只用 test/helper 私有 delegate/state，无 public fault endpoint/header。attempt、commit completion、readback 和独立 durable observation 分层；不能以最终余额或任意 SELECT 冒称事务阶段。

允许的写差集只有新 task/debit、对应 wallet credits/source updated_at、G02 的声明任务列及有证据的 refund、G03 声明列。claim_token/execution_generation/claimed_by/lease_until/recovery_status/billing_status/user_cancelled_at/cancellation_reason 及其余 task/wallet/creditlog 列、全部 BillingUnit/Submission/Step 和其他 owner 行按逐列 snapshot 守恒。auth 维护写另标，退款不改变受控 billing 状态。

### 5. Independent admin read adapter

admin_tasks 只读应用／ports 与独立私有 tables/persistence；不借 generic 或 Task Observation 的私有 UoW。管理员 DTO 两种各自映射。A01 默认 skip=0、limit=10、sort_field=created_at、sort_order=desc。user_id/type/status truthy 时 exact filter；model_name truthy 时 ilike('%input%')，保留 % 和 _ 的 SQL wildcard。先 count 全过滤集合，再 getattr(AITask, raw_sort_field, AITask.id)；只有精确 desc 降序，否则升序，单字段排序、无第二键。未知属性 fallback id；真实存在的不可排序属性保留 SQL／attribute 错误，不扩大 fallback。映射保留源 AITask 的全部可排序列与必要属性表面，不能由薄投影缺列改变 fallback。负 skip/limit 不钳制，沿真实数据库行为。

A02 全库 id first，missing 404“任务不存在”，不加 user filter。两方法 business 零 flush/commit/DML，无 provider/queue/ledger 写；认证 Session 可以沿原规则维护 last_seen，不能把它称全请求零 DML。task/business Session finally close；SQL count 与 list 两次 Result 实际消费，不能先 list 再 len 冒充 count。

### 6. Independent provider configuration, credential and protocol ports

provider_status 的公共出口为 ProviderStatusRuntime、PreparedProviderQuery、ProviderStatusPort 和 create_provider_status_service；具体名称及签名由共享作者先冻结。runtime 显式供应 configuration session_factory、secret_key、owned single-GET HTTP client factory，不读环境／全局配置。config 读取独立 Session：SystemModel(provider==raw provider,is_active==True).first；否则 ModelConfig(provider==raw provider).first，无 user/category/model_name/order 条件，finally close。provider 匹配大小写精确；无配置／不支持 provider 返回 None，route 映射 unknown。完整跨账户选择语义保持。

prepare(provider) 在 generic 的 poll try 之外查询配置／取得凭据／构造 prepared query；返回后配置 Session 已成功关闭。非空 key 先用显式 SECRET_KEY UTF-8 SHA256，再 urlsafe base64 构造或取得缓存 Fernet，即使是明文也先取 Fernet；空 key 直接空串。gAAAAA 前缀真实 decrypt/decode，其他非空 key 原样；InvalidToken、decode、secret/init/config/close 错误在 poll catch 外传播。credential cache 仅归显式 runtime 实例，无进程全局 env/key。OSS 最小 import 证据表明外查不用 storage client；新包不 import 旧 runner/OSS，不启动 semaphore、下载或 storage。

generic G04 在 own task 查找后先判断外部 id/provider 缺失 → 原 unknown+detail；完整 prepare 仍在 try 外。配置 runtime 未接线属于显式服务未接线失败，不能 catch 成 unknown；调用默认无业务配置时闭口，且不创建资源。factory 已接线但配置无匹配／provider unsupported 才是来源 unknown。

PreparedProviderQuery.poll_once(external_task_id) 只一次 GET，返回来源的中间 status/data/error 形状；extract_result 保留 URL 顺序／None／空串。generic 应用的 try 只覆盖 poll、completed 提取、failed error.message 解码。非 200 → pending → route processing；completed 返回 result 即使空／None；failed 保留错误解析，xAI expired 的 error 字符串会在 route .get('message') 路径失败并成为 200 unknown。Exception 转 unknown 的范围不得包住 prepare，也不把所有错误先统一为对象。

默认 polling URL 契约从固定 runner 每个 builder 逐一消费：配置 base 末尾 /v1 清理和 /v1/videos/{id} 按分支保留，haoai 为 /openai/v1/videos/{id}；不能对所有 provider 盲目套相同 URL 清理。headers/Bearer、JSON 解析、branch data 包装和 /content fallback 按固定 source extents 实现。十三分支都要协议证据。

| provider／固定 builder 行 | timeout | 完成／失败 status 精确集合与提取 |
| --- | --- | --- |
| zhangyuge 303–338 | 30 | completed／failed；video 字符串或对象 url → metadata.url → result_url；IPv4 TCPConnector |
| manxiaobai 339–383 | 30 | completed,done,SUCCESS,succeeded／failed,FAILURE；同默认提取；GET 不调用 download_fn |
| xai 384–441 | 30 | done／failed,expired；video.url 或 http 字符串，否则 None；expired 的 error string 保留 |
| manxueapi 442–532 | 60 | completed,success,succeeded／failed,error,cancelled；video.url → video_url → output dict/list 首项 url → metadata.url → result.url → result_url → 顶层 http url |
| geeknow 533–585 | 30 | lower 后 completed,success,succeeded／failed,failure,error,cancelled,expired；content.video_url/content.url → output dict/list 首项 url → video_url → url → detail.url → data[0].url |
| snumom 586–630 | 30 | completed／failed；video_url → url → result_url |
| biglongxia 631–678 | 30 | completed／failed；url → video_url → result_url；GET 不调用 download_fn |
| heima 679–728 | 30 | inner=data.data，inner.status 或 top status lower；completed,complete,succeeded,success,done,finished／failed,failure,error,cancelled,canceled,expired；完成直接 /content URL |
| yu25 729–773 | 30 | lower 后 completed,complete,succeeded,success,done／failed,failure,error,cancelled,canceled,expired；result.video_url → video_url → result_url → url |
| yu25_beiyong 774–871 | 30 | 固定11有序状态路径首个非空字符串 trim/lower；completed,complete,succeeded,success,done,finished,ready／failed,failure,error,cancelled,canceled,rejected,expired；固定32 URL paths／正文正则，缺结果 /content |
| haoai 872–951 | 30 | completed,done／failed；video_url → video 对象 url/http 字符串 → url → /content；/openai/v1/videos |
| yiyun 952–1003 | 30 | completed,success,succeeded／failed,error,cancelled；video_url → url → urls[0] 对象 url 或字符串 |
| suqing 1004–1045 | 30 | completed,success,succeeded／failed,error,cancelled；video_url → url |

除表中明确 lower/trim 的分支，不统一 normalize 大小写。yu25_beiyong 的 32 URL 顺序由 receipt 的 protocolContract.urlPaths 完整绑定；11 status paths 和正文正则使用固定 builder 774–871 的原顺序，不由摘要猜造。任何额外源正文消费要保留准确 read extent；作者专项同时验证多值竞争时第一命中及 falsey/非字符串。

HTTP adapter own async client/context/response；成功、non200、JSON错误、timeout、cancelled/error 均 await 资源退出完成，配置 Session 和业务 Session 各 finally close。业务读取 Session 在 G04 await 期间维持原依赖生命周期，不拿 config Session 代替。manxueapi 60 秒，其余 30；zhangyuge 显式 IPv4。没有重试 loop、poll interval、max retries、runner.run、download、OSS、任务终态 UPDATE、wallet refund 或 Worker signal。

### 7. Accurate prospective paths and writers

所有路径相对 target；这里只冻结未来候选白名单，本轮只写 EVID 六路径。来源、代码、文档分别计数。下一实际 baseline 确认这些新路径未存在并保存全部旧 bytes，不覆盖他人新增路径。

新增生产 26：

- backend/src/haoai_backend/generic_tasks/{__init__,domain,errors,ports,application,guard,persistence,tables,http}.py（9）。
- backend/src/haoai_backend/admin_tasks/{__init__,domain,errors,ports,application,persistence,tables,http}.py（8）。
- backend/src/haoai_backend/provider_status/{__init__,domain,errors,ports,configuration,credentials,protocols,http}.py（8）。
- backend/src/haoai_backend/authentication/task_identity.py（1）。

新增测试/support 21：

- backend/tests/generic_tasks_support.py；test_generic_tasks_{domain,application,guard,persistence,transactions,http,boundaries}.py（8）。
- backend/tests/admin_tasks_support.py；test_admin_tasks_{domain,application,persistence,http,boundaries}.py（6）。
- backend/tests/provider_status_support.py；test_provider_status_{configuration,credentials,protocols,http,boundaries}.py（6）。
- backend/tests/test_task_identity_facade.py（1）。

旧可变候选 13：backend/src/haoai_backend/app.py；backend/src/haoai_backend/authentication/__init__.py；backend/pyproject.toml；backend/tests/test_module_boundaries.py；test_series_access_boundaries.py；test_authentication_boundaries.py；test_rough_cut_http.py；test_production_notes_http.py；test_asset_data_boundaries.py；test_chat_data_http.py；test_canvas_data_boundaries.py；test_teams_boundaries.py；test_task_observation_boundaries.py。后十测试均在 backend/tests/。最后一个只作为上一批已验收登记数／public boundary 的必要增量候选，baseline 后核对实际必要哨兵；不预先要求改其余 Task Observation 正文。旧测试只加入新六方法/公开 facade/必要依赖与 route count，原业务、privatepolicy、冷启动、旧路由和错误哨兵不得删弱，不 skip/xfail。旧 __init__ 仅 exports 增量；pyproject 只声明单次 HTTP 的 aiohttp 和实际直接消费 Fernet 的 cryptography 依赖，兼容版本在 baseline 时按既有运行时和旧 requirements 证据冻结，不安装或任意升级无关项。

S 唯一共享作者先独占三包各 __init__/domain/errors/ports（12）、authentication/task_identity.py、test_task_identity_facade.py、旧 authentication/__init__.py 与 pyproject；冻结公开签名、DTO、clock/id、错误/事务/资源语义。G 后独占 generic 的 application/guard/persistence/tables/http 和八个 tests/support；A 独占 admin 的 application/persistence/tables/http 和六个 tests/support；P 独占 provider 的 configuration/credentials/protocols/http 和六个 tests/support。G/A/P 无同文件写入；support 各有 owner，shared 变更回唯一作者先协调。I 唯一最终整合作者拥有 app 和十旧登记测试，消费已冻结 exports；如由 S 兼任 I，也不能同时与其他作者修改公共文件。代码 Luna 6/xhigh；规划与独审 GPT-6.1 Sol/xhigh。

未来七 doc：README.md、backend/README.md、docs/architecture/backend-module-boundaries.md、docs/architecture/module-api-compatibility.md（四旧完整 baseline 前缀 EOF 追加）；docs/architecture/generic-admin-tasks-backend.md、openspec/changes/modularize-backend-generic-admin-tasks/verification.md（两新）；同 change/tasks.md（root 按实际阶段更新 checkbox）。旧 doc 必须取上一批验收后的完整前缀，不用当前历史 copy 覆盖新追加。当前 schedule、frontend、其余 auth/shared/teams/notes/access、Task Observation 实现与其余旧源冻结。

候选集合为 47 新+13 旧=60 代码键，与未来来源 60 无关。以未来实际 B/C/H 为基线，规划安装 B+5，代码完成 B+52，后置两新文档 B+54；C+47；旧代码 C−13 保护；四旧 doc 前缀保护，H 历史保留。这些算术都须以实际路径 inventory 重算，不能硬编码旧 B566/82/91/97 或猜测所有旧13均必要改写。

### 8. Valuable validation and strict evidence conjunction

| 组 | 实际验证目标 |
| --- | --- |
| V01 公共身份 | 真实 JWT/SQL session/password_version/last_seen，active/过期/superuser/非admin；auth close 成功先于业务开 Session；auth unknown 不 replay；无客户端 flag 与私有 auth import |
| V02 create atomic | autoflush=False、add 后 wallet 查询前零 INSERT、正费用锁序、缺 wallet/不足 402 全 rollback；task/wallet/usage 同 commit；真实 refresh/PK/value；零负费用／JSON model顺序／合法scalar错误 |
| V03 updates/guard | own missing404 在 guard 前；status锁/progress first；五type/任意BillingUnit/同用户 exact list 三层409；None/empty/clamp/UTC/任意status与只改声明列 |
| V04 refund/fault | wallet缺/0/1/多debit/已有refund/重复failed、唯一key/关联而非新userfilter；真实 commit/rollback、SQL失败、precommit、commit后ackunknown、refresh失败、durable readback，绝不 replay |
| V05 admin | 真实SQLsuperuser不要求membership、全账户、两九字段DTO/rawJSON；truthy/ILIKE%_/count-before-list/rawsort/非列错误/负分页/无次序追加；business零DML/commit |
| V06 provider config | 独立session/activeSystem first→ModelConfig first/跨账户无附加filter/close；unsupported/missing；空key/明文仍Fernet/密文/错误/显式secret缓存，prepare在catch外 |
| V07 thirteen protocols | 每个provider成功/失败/processing/non200/坏JSON/URL竞争/空None/xAIexpired；确切method/path/header/timeout/IPv4；注入HTTP矩阵加本地owned loopback真实HTTP，无真实provider外连 |
| V08 full owner | 复用公开 test-only fullowner24 DDL/FK ON 并创建非空auth/private/media/task/submission/billing/step/creditlog自FK等真实行；所有允许写差异逐列列出，其余全snapshot相等；生产薄map不是DDL证明 |
| V09 integration | 默认factory完整6登记无Env/Engine/Session/client/schema/Worker；缺resolver/UoW明确503零businessSession；旧acceptedroute/privatepolicy/cold和Task kwargs保持；至少六positive及关键拒绝真实driver/rawASGI/status/bodyhash/SQL阶段 |
| V10 root/full/resources | root从冻结SHA实际full backend/syntax/actualfullpathcounts/source60/保护图；root JWTSQL/TCP六接口、十三loopback与conservation；Session成功close、client/response/listener/thread/Engine/DB/socket/PID/port/ownedtmp所有终态 AND |

fullowner support test-only 可消费已公开 chapter_asset_replacement_support 的 create_owner_database/OWNER_METADATA/snapshot/physical_connection_pair；只在本批新support扩seed，不改旧helper、不在生产使用。实测 actualSQL 必须记录不同Session/DBAPI、Result消费、commit completion 和PK readback；auth seed/维护、fixture写、目标business写区分。HTTP故障先assert500，不要求JSON 500 body。所有 root helper 同样以独立source review与准确路径绑定，不因作者green直接认定root验收。

作者专项、独立新代码/旧真实diff review、root full、JWTSQL/TCP、provider loopback、资源与conservation是不同证据。Root acceptance 必须为 ordered assertions ∧实际SQL/commit/rollback/refresh∧全部ownedcleanup；不是 OR，不用作者定值硬编码 actualfullpathcounts。失败命令／launcher／截断输出／未运行项保留；cleanup精确自有资源，不动外部PID或端口。旧 live repo HEAD/status/tracked/index 在 root阶段如有用户授权完整记录，不作为固定 Git object 或 target保护的 equality gate；本作者没有进行该读取。

## Risks / Trade-offs

保留跨账户配置 first、admin wildcard/rawsort/负分页、合法非对象JSON错误、未知status、退款歧义与xAI expired→unknown等旧行为，属于兼容迁移。登记97不称97TCP；十三注入协议与本地loopback不称真实供应商成功；SQLite真实事务不称PG并发；本批没有worker/OSS/browser/deploy完成声明。指定外部5.6Sol仍offline pending，内部Solreview不得代过。

## Migration Plan

上一批实际验收 → root实际baseline与来源60/old13/old4byte图 → 五稿普通OpenSpec安装与CLI/规划独审 → 共享S合同 → G/A/P独占并行 → I单一app/registry及完整codefreeze → Sol独立完整新代码/旧diff/source review → root实际full/JWTSQL/TCP/providerloopback/conservation/ownedAND → 七doc/独立文档review/普通最终CLI与事实inventory → 中文阶段提交推送新origin → 指定外审继续pending。tasks不预勾，不新加用户approval或部署门槛。

## Open Questions

没有待用户提供的必要输入或供应商凭据。下一实际baseline、原排序字段表面、依赖具体兼容版本、各root自有helper路径和端口是实施前内部证据固定事项；不能读取上一批进行中正文代替。完整source函数体由命名分析提供，author-freeze明确本作者未重读Git正文；yu25_beiyong11status路径仍以固定builder原文为唯一顺序，不自行补摘要缺项。
