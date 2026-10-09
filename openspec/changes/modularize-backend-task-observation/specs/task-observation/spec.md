# Task Observation Delta Spec (Draft)

本稿仅在EVID staging，尚未安装、批准实施或运行验收。固定来源为Git23403806898550a7668a6ee7c0c457315655c39b，读取窗口与SHA见design/receipt。

## ADDED Requirements

### Requirement: Complete observation and control surface

模块SHALL登记精确九方法，成功均200；静态路径优先匹配，不迁移生成/Worker/账务写入。

| 方法 | 路径 | 身份 |
| --- | --- | --- |
| GET | /api/chat/tasks | 登录即可 |
| GET | /api/chat/submissions/{operation} | 登录即可 |
| GET | /api/chat/tasks/{task_id}/request | 活动会员 |
| GET | /api/chat/tasks/list | 活动会员 |
| GET | /api/chat/ai-review/counts | 活动会员 |
| GET | /api/chat/ai-review/{task_id} | 活动会员 |
| GET | /api/chat/batch-optimize/running | 活动会员 |
| GET | /api/chat/batch-optimize/{task_id}/status | 活动会员 |
| POST | /api/chat/batch-optimize/{task_id}/cancel | 活动会员 |

#### Scenario: Inert factory and missing configuration

- **WHEN** 默认factory构建并冷导入全部生产模块
- **THEN** 登记原82+新9=91，不创建Engine/Session/schema/env/provider/线程/Worker；查询身份或Session未配置返回503且零业务Session，取消连接factory未配置也闭口
- **AND** 旧四notes/rough私人接口、章节替换的显式policy条件与旧resolver优先级保持。

### Requirement: Separate trusted account and active principals

两个回执GET MUST登录即可，其余七个MUST活动会员。真实auth维护先在独立auth Session提交/关闭，之后才业务Session。输入header/body/前端flag不得授予superuser。

#### Scenario: Expired membership and SQL privilege

- **WHEN** 真实JWT会话有效但会员过期
- **THEN** 两本人回执仍可读，其他七个认证拒绝且业务/取消零DML
- **AND** 详情任务存在后，只从真实SQL users.is_superuser取得特权；account接口不得使用require-membership resolver替代。

### Requirement: Task receipt uses first billing unit

GET /api/chat/tasks SHALL保留actor-owned task/message：task_id与message_id均非None（包括空串）时422“task_id 与 message_id 只能提供一个”；仅空白task_id422“task_id 不能为空”；两者缺失或message_id空白422“必须提供 task_id 或 message_id”；缺本人task404“任务不存在”；message多任务409“message_id 对应多个任务，请改用 task_id 查询”；无匹配返回status/result均null；过滤原字符串。单元按created_at ASC取first，平局无新次排序；无单元才回退task.billing_status/credit_cost。

#### Scenario: Missing and multiple units

- **WHEN** 本人任务有零个或多个单元
- **THEN** 零个回退任务，多个取最早且其falsey字段不回退
- **AND** 原十三响应键/result不截断/message_id或null/progress或0/负值/时间isoformat或null保持。

### Requirement: Submission receipt requires one task and one unit

提交GET的idempotency_key SHALL为必填普通query字符串（不是Idempotency-Key header）；缺query沿FastAPI标准422。已提供参数进入helper时SHALL先验证operation仅image.single/video.single，否则422“不支持的任务操作”；再校验Idempotency-Key非空白且Python字符长度<=255，否则422“Idempotency-Key 必须为 1 到 255 个非空白字符”。SQL使用原未trim key+actor+operation，不筛submission.status/task.type。

#### Scenario: Corrupt submission and recovery observation

- **WHEN** 未找到、task_ids非列表或非恰一、task缺失/错owner、unit缺失/多行
- **THEN** 分别404“未找到该幂等键对应的任务提交”、500“提交记录与任务集合不一致”、500“提交任务记录不存在”、500“任务计费单元记录不存在”、原多行异常generic500
- **AND** 正常七键submission_id/operation/task_id/message_id/status/quoted_amount/billing_status使用当前值；不自动重提/生成/扣费/provider调用，公开GET不增加request digest校验。

### Requirement: Request access keeps task-first policy

详情SHALL先全局id查任务，missing404“任务不存在”；本人/SQLsuperuser可读。其他用户仅truthy team_id经本人membership、公开view_tasks规则与目标用户membership获准；不增加team存在404、SeriesAccess/type/章权限。

#### Scenario: Team denial and folded JSON

- **WHEN** 他人缺membership、缺权限或目标非成员
- **THEN** 来源顺序/detail保持“你不是该团队成员”“没有执行该操作的权限”“无权查看该任务的请求体”
- **AND** 两次本人membership实际查询、重复PK首次观察字段语义及owner/permissions规则保持。
- **AND** 空request_data为{}，坏JSON为_raw；data:或长度>500纯base64字符串折叠为<base64 len=N>，depth>6停止；不把合法JSONscalar/list或不可hash输入改成新422。

### Requirement: List and context projection keep wide source values

列表SHALL查询当前actor/count/created_at DESC/原offset-limit，page默认1/size默认50，参数仅普通int，不加正数/上限/只准10。批量读消息、章标题、四素材名，不加章访问过滤。

#### Scenario: Context, negative values and truncation

- **WHEN** 普通/素材消息、孤立task或batch-optimize/ai-review request JSON出现
- **THEN** 原十九基础键和动态prompt_id保持；普通消息frame_index+1而chapter_id仍null；公开pure helper的异常及已赋值部分保持
- **AND** failed truthy result最多5000，其它>500或data:置空，request_data最多200；negative credit/progress、未知type/status保持。
- **AND** 实际page_size10 body可供现TS parser验证，不将前端选择改成后端只准10合同。

### Requirement: Review and optimization preserve JSON quirks

counts SHALL筛owned ai-review queued/processing/completed，保留带空格SQL LIKE及JSONsource_message_id复核/truthyprompt分组；空message_id返回counts={}。两按id状态GET只筛本人，不加type门槛。

#### Scenario: Scalar JSON and running results

- **WHEN** counts命中合法非dict或不可hash prompt_id
- **THEN** 保留来源generic500，紧凑JSON不匹配原LIKE差异保持，不加预验证。
- **WHEN** running筛owned batch-optimize queued/processing/cancelling
- **THEN** created_at DESC逐JSON匹配chapter；parse失败跳过，无匹配task=null，合法非dict原失败保留，不增加访问或次排序。
- **WHEN** 读取状态
- **THEN** 审核result原样；优化truthyresult json.loads仅JSONDecodeError回raw，scalar/list保留；progress或0/message或空及404保持。

### Requirement: Cancellation preserves independent status-only transaction

取消SHALL按id+actor查首次task，missing404且无type门槛。仅queued/processing才取registry truthy信号set→writer；其它状态不信号/不独立事务，仍返回task_id/status:cancelling。200不保证Worker停止或退款。

#### Scenario: Zero match and preserved claims

- **WHEN** 独立连接执行取消
- **THEN** 仅UPDATE ai_tasks SET status='cancelling' WHERE id=:id AND status IN ('queued','processing')，无owner/type/claim/CAS，避免onupdate扩大字段
- **AND** row0仍commit200且bool忽略；updated_at/claimed_by/lease/execution_generation/claim_token/recovery/user_cancelled_at/cancellation_reason及账本/消息/章等行保持。

#### Scenario: Signal and uncertain commit are not replayed

- **WHEN** 已set后SQL/precommit/实际commit后确认失败
- **THEN** 信号不撤，未提交写rollback，ack未知可已durable；generic500不自动重试SQL/信号/受理/provider
- **AND** 新GET观察durable；初读Result消费后第二物理连接提交终态或删行可产生row0，B已提交差异单列，不称PG并发/Worker完成。

### Requirement: Evidence and remaining migration stay distinct

实现MUST以完整ownerDDL/FK ON和非空任务/提交/执行/账本/credit自FK/来源/media/private/auth行验证守恒，生产仅必要查询投影无schema初始化。源码review、作者tests、根fullbackend、真实SQL/JWT/TCP、实际body→冻结TS和资源分别留证。

#### Scenario: Bounded local acceptance

- **WHEN** 根完成本批本地检查
- **THEN** 91登记不称91TCP，九方法按实际trace计数，失败历史不覆盖；Session.close成功计数/ownedclient/listeners/Engine/thread/socket/PID/port/temp全部AND，不声称全机无外连
- **AND** Grillmeoffline、完整React/生成/Worker/generic/admin/proxy/166差集/最后语言评估继续待办；不将PG/provider/SMTP/部署加入这九方法必做门槛。
