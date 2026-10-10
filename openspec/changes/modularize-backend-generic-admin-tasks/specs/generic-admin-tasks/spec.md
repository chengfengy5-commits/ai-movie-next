# Generic Admin Tasks Delta Spec (Draft)

本稿仅 EVID staging，未安装、未批准实施或运行产品。来源固定旧仓库 object 23403806898550a7668a6ee7c0c457315655c39b；named analysis与closure的实际SHA/准确extent见author-freeze.json。普通OpenSpec，不切spec-superflow。

## ADDED Requirements

### Requirement: Complete six method surface

模块 SHALL 完整登记通用四方法和管理员两方法，成功均200：POST /api/tasks、PUT /api/tasks/{task_id}、PUT /api/tasks/{task_id}/progress、GET /api/tasks/{task_id}/external-status、GET /api/admin/tasks、GET /api/admin/tasks/{task_id}。第四通用GET MUST实现全部十三协议，不得只前三写方法关闭本族。

#### Scenario: Complete and inert app composition

- **WHEN** 默认app构建并冷导入全部生产模块
- **THEN** 精确登记新六方法并保留全部此前已验收路由，数量从实际集合重算；不读Env，不创建Engine/Session/schema/HTTPclient/Worker/线程
- **AND** 缺可信resolver或业务UoW闭口503且零业务Session；缺供应商runtime不可catch成unknown，已配置runtime的missing配置/unsupported才按来源unknown
- **AND** 上一批Taskkwargs、原四private接口、显式policy条件与旧resolver优先级保持，不能将条件91→97写成本轮已完成。

### Requirement: Public trusted active and administrator identities

业务包 MUST通过公开authentication facade或显式可信resolver取得TrustedActor，不import认证private application。generic需要active，superuser沿原绕过；admin只需真实SQL is_superuser，不加membership。公开factory只创建惰性closures。

#### Scenario: Auth Session finishes before business

- **WHEN** 真实JWT/SQL认证、password_version/session/last_seen维护和active/admin核查成功
- **THEN** 原auth事务与Session.close成功完成后才return actor并开business Session，admin判定在authenticated(False)上下文内读取SQLprincipal
- **AND** auth commit/close/unknown失败不释放身份、不开业务Session、不自动replay；客户端flag、premium、Teamrole和view_tasks不能授予admin。

### Requirement: Creation preserves staged atomic accounting

G01 SHALL先拒绝精确五受控type为422，JSON model→model_name→modelId，生成id后ORMadd processing task；message_id空串、falsey request_data='{}'。Session autoflush=False，wallet查询前不得earlyINSERT。仅正费用锁wallet/扣credits/stage usage，task/wallet/log同一次commit后真实refresh(task)再200 id/status/credit_cost。

#### Scenario: Insufficient and zero or negative costs

- **WHEN** 正费用wallet缺失或不足
- **THEN** 实际rollback后402，task/wallet/usage无durable新写
- **WHEN** 费用零或负
- **THEN** 保存原值，不锁扣wallet、不产生debit；没有Submission/BillingUnit/Worker/客户端幂等key
- **AND** usage key为generic-task-charge:{task.id}，amount=-cost，真实SQL证明ORMadd后wallet之前零taskINSERT及同commit/PKrefresh。

#### Scenario: Source JSON failures

- **WHEN** request_data非法JSON或TypeError
- **THEN** 沿源忽略提取错误
- **WHEN** 合法JSON为null/list/scalar
- **THEN** .get的未捕获AttributeError保留；不新增objectvalidator、422或默默成功。

### Requirement: Owned task guard precedes all updates

G02 SHALL id+actor FOR UPDATE own task，G03 SHALL id+actor first且不加任务行锁；missing先404。赋值前三来源guard顺序为精确受控type、任意BillingUnit、同用户Submission.task_ids为Pythonlist且exactcontains。命中任何一个409。

#### Scenario: Exact list and field updates

- **WHEN** type非受控但任意BillingUnit存在，或同用户list精确包含id
- **THEN** 更新409且声明列/账务无写；不得substringLIKE或仅当前operation
- **WHEN** Submission.task_ids不是list或只含相似id
- **THEN** 不因该来源误判命中；其余guard仍执行
- **AND** G02只改status/非None result/updated_at；G03 progress clamp0..100/非None message/updated_at；None保留、空串清空，原UTC规则与commit后属性PK读回保持。

### Requirement: Refund requires unambiguous original debit

G02 failed且正费用 SHALL依序锁wallet、锁同user/task/type=usage/amount=-cost的debits，仅恰一并不存在type=refund/related_debit_id关联退款时加wallet和refund；refund lookup不添加新user谓词。key为generic-task-refund:{debit.id}。

#### Scenario: Ambiguous or repeated failure

- **WHEN** wallet缺、debit0或多、已有相关refund
- **THEN** 不猜退款，仍提交status；重复failed不再退款但可更新updated_at
- **AND** 改回其它status不逆转refund或再扣费；任意queued/cancelled字符串无Worker signal/重派/lease恢复/BillingUnit结算。

### Requirement: Unknown commit and postcommit errors are not replayed

所有写方法 MUST分开真实未提交rollback、commit completion、ackunknown与提交后refresh/readback错误，不自动重放HTTP、task/debit/refund或状态写。

#### Scenario: Durable commit but failed acknowledgement

- **WHEN** 实际commit完成后异常或真实PKrefresh失败
- **THEN** generic500可对应durable写，新的独立读取观察实际状态，不重放创建或账务
- **AND** 故障只由私有test/helperdelegate/state注入，不添加publicfaultendpoint/header；attempt与completion分别留证。

### Requirement: Preserve all non-target reliability and owner rows

generic修改 MUST仅写声明列及有真实证据的wallet/log差分。claim_token/execution_generation/claimed_by/lease_until/recovery_status/billing_status/user_cancelled_at/cancellation_reason与其余非目标列，以及BillingUnit/Submission/Step/其他owner行 SHALL守恒。

#### Scenario: Full owner conservation

- **WHEN** 真实fullowner24DDL/FKON、auth/private/media/task/ledger等非空fixture运行所有六方法
- **THEN** 逐列列出允许差分，其余snapshot完全相等；seed/auth维护/business写分开
- **AND** 生产薄ORMmap不冒称完整DDL约束，不改变schema、不关闭FK或删fixture。

### Requirement: Administrator list preserves raw query behavior

A01 SHALL全账户读取，defaults skip0/limit10/created_at/desc；truthy user_id/type/status exact，model_name ILIKE '%input%'保留%/_。先count再source getattr单字段sort/offset/limit；未知attrfallbackid，只有精确desc降序，不添加第二sort，不钳制负分页。

#### Scenario: Exact list DTO and SQL errors

- **WHEN** 管理员列出任务
- **THEN** data/total，每行精确9键id/user_id/type/status/credit_cost/message_id/model_name/created_at/updated_at，model_name falsey输出空串
- **AND** 实际存在但不可sort属性的原错误保留；映射完整源sort列与属性表面，不能把薄map遗漏列误当unknownfallback；实际count/listResult分别消费，零businesscommit/flush/DML。

### Requirement: Administrator detail preserves raw data

A02 SHALL全库idfirst，无actor过滤；missing404“任务不存在”。详情精确9键id/user_id/type/status/message_id/request_data/result/created_at/updated_at。

#### Scenario: Other account and non-JSON request result

- **WHEN** 过期但真实SQLsuperuser读其它用户任务，request/result为空、坏JSON或长字符串
- **THEN** 200原值保留，无解析/截断、无credit_cost/model_name新增；普通有效会员拒绝admin
- **AND** business只读Session最终成功close；auth last_seen写另层记录。

### Requirement: Provider prepare keeps configuration and decryption order

配置 SHALL独立Session先provider精确相同且activeSystemModelfirst，fallback同providerModelConfigfirst；无user/category/name/order附加filter，finallyclose。未知provider或无配置返回None。prepare/init/config/decrypt在genericpolltry外。

#### Scenario: Plain and encrypted keys

- **WHEN** api_key非空，即使明文
- **THEN** 先从显式SECRET_KEY UTF8SHA256/urlsafebase64取得或缓存Fernet，再检查gAAAAA前缀；密文decrypt/decode，其余原样
- **WHEN** key为空
- **THEN** 直接空串
- **AND** secret/init/InvalidToken/decode/config/close错误传播，配置Session仍finallyclose，不读取Env或真实凭据、不转pollunknown。

### Requirement: All thirteen single GET protocols are implemented

provider_status SHALL完整实现zhangyuge、manxiaobai、xai、manxueapi、geeknow、snumom、biglongxia、heima、yu25、yu25_beiyong、haoai、yiyun、suqing，每次请求只一个GET。每branchmethod/URL/header/状态大小写/数据包装/结果提取顺序按固定builder与冻结protocolContract；不把所有分支统一normalize或始终unknown。

#### Scenario: Timeout IPv4 and response variants

- **WHEN** 单次poll运行
- **THEN** manxueapi60秒，其余30；zhangyugeIPv4connector；non200为pending后routeprocessing
- **AND** 完成/失败集合与URLfirsthit顺序逐provider独立，默认video/metadata/result_url、各复杂output/nestedpaths、yu25_beiyong11status/32URL/正文regex、haoai/openai/v1与/contentfallback保持原规则；空或Nonecompletedresult可直接返回。

#### Scenario: xAI expired and catch boundaries

- **WHEN** xAI expired返回error字符串，或poll/提取/error.message解码抛Exception
- **THEN** routecatch内200unknown；不提前把error字符串统一成object
- **WHEN** module配置/prepare/decrypt错误
- **THEN** 在catch外传播，不当作unknown；missing外部id/provider与无配置保留原unknown+detail。

### Requirement: Single status query has bounded resources and no execution side effects

G04 MUST只查询，不runner.run/retryloop/download/OSS/DB终态UPDATE/refund/queue/recovery。显式ownedconfigurationSession、businessSession、asyncHTTPclient/response生命周期分别管理，businessSession沿原依赖持有到await结束。

#### Scenario: Local HTTP lifecycle

- **WHEN** 成功、non200、坏JSON、timeout、取消或错误
- **THEN** ownedasynccontexts完成退出、configuration/businessSession成功close后分别计数，无client/socket/response泄漏
- **AND** 十三注入协议加本地ownedloopback验证实际HTTP，不能称真实supplier/PG/Worker/OSS运行。

### Requirement: Disjoint implementation and evidence layers

共享公开签名／exports／authfacade／依赖声明 SHALL由单一writer先冻结；G/A/P私有文件disjoint；app/registry由唯一I最后接线。代码Luna6/xhigh，设计和独审GPT6.1Sol/xhigh。rootfull/JWTSQL/TCP/providerloopback/conservation/ownedcleanup MUST实际运行留证。

#### Scenario: Bounded closeout

- **WHEN** 本族本地验收
- **THEN** 作者专项、完整新代码/旧trueDiff独审、rootactualfullpathcounts、六TCPpositive及关键拒绝、真实SQL/commit/rollback/refresh、十三loopback、全owner守恒与所有owned终态strictAND分别绑定SHA/命令/失败史
- **AND** 四旧EOFprefix+两new+tasks共七doc按事实更新后实际普通CLI，当前166不预扣，条件91→97从实际集合重算；完整backend→完整React→最后语言保持
- **AND** 指定GPT5.6Sol/xhighGrillmeofflinepending、不probe/pair/substitute；不预勾、不新增人工approval/部署门槛，不将源码哈希称全文语义阅读。
