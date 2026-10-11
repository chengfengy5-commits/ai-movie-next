# Generic Admin Tasks Tasks

本任务表记录通用/admin 任务验收证据与后续 OpenSpec 状态。Root run05（runId `dbcead9c-e236-4664-87c2-50280761c45b`）已按 matrix04 完成真实本地 acceptance：175/175（G119/A23/P33），362/362 cleanup reports 通过，target all646 SHA before/after/current 完全一致；受控 SQLite deferred-FK fixture 结果与产品 rollback/close、PostgreSQL 边界分开记录。run01–run04 的真实失败/部分结果保持原样。I60、G08、A07、P03 与 harness/runner source review 各自接受范围及 run05 实际结果分层见 verification.md。现有 route registry 97/248，151 项未登记（148 business + 3 infrastructure）；另有 12 个 React workflow 未完成，不推算项目完成比例或 ETA。文档包含五个旧文件追加与三个任务文档共 8 路径；五旧文件完整原字节前缀已校验。独立 Sol 文档 review02 已实际接受 stage03 八份文档用于 target documentation install（P1=0/P2=0、open IDs=[]；JSON SHA `67029fe624ec15ea3b625888cc66632bf7c8aaa5ab3d5db3c506fa1a66a3828e`）；stage04 阶段状态差分已由独立 Sol review03 实际接受（P1=0/P2=0、open IDs=[]；JSON SHA `24a9752fc82bcc6a6fad811c3cdbb2545f90dd8bed9d066ab791a8b9d60a0e7f`）。Root 收据记录八文档实际安装、普通 OpenSpec strict/status/apply 均真实 exit=0，以及中文 commit/push 后远端 main 确认为 `78f2a40485435653fd0831cd2df4cb5aa7bea6e2`。按这些实际收据闭合 6.2 后，本任务表当前 16/17 项已勾；7.1 指定 GPT-5.6 Sol/xhigh Grillme 保持 OFFLINE PENDING。普通 OpenSpec 流程保持不变，backend 全部完成后推进 React 全部，最后进行语言评估。

## 1. Planning and Shared Contracts

- [x] 1.1 root 在上一批正式验收后建立本批实际 B/C/H、accepted routes、old13/old4 bytes与未来来源60图，逐项确认必要mutable增量；五稿普通OpenSpec安装/CLI/规划独审，当前55/31与166差集不预改。
- [x] 1.2 单一 Luna6/xhigh 共享作者冻结三包公共 domain/errors/ports/exports、clock/id/ORM事务/DTO/HTTP资源合同；新增public task identity facade与旧auth exports增量，真实JWTSQL/auth close专测，显式依赖声明及版本以实际baseline证据固定。

## 2. G / A / P Parallel Implementation

- [x] 2.1 G实现G01真实ORMadd/autoflushFalse无earlyINSERT，正费用wallet+usage+task同commit/402rollback/refresh，零负费用/JSONmodel顺序及原非对象错误；actualSQL/PKreadback专测。
- [x] 2.2 G实现G02/G03own404与三来源guard、status锁/progressfirst、None/空串/clamp/UTC/声明列；精确refund证据、歧义仍commitstatus、唯一key/重复callback、actualcommit/rollback/ackunknown/refresh失败无replay及fullowner守恒专测。
- [x] 2.3 G完成第四方法G04的HTTP/application：own/missing外部信息、prepare在polltry外、完整13poll/extract/error路径、xAIexpired→unknown/空None结果、无run/download/OSS/终态更新；不得前三写方法通过就关闭generic4。
- [x] 2.4 A独占两readonly方法：真实SQLsuperuser非active、全账户、两套各9字段/rawJSON、truthy/ILIKE%_/count-before-list/rawgetattr/负分页/无第二sort及零businessDML/commit，真实queryResult消费/资源专测。
- [x] 2.5 P独占配置/credentials/13协议/ownedHTTP：独立Sessionfinally、activeSystemfirst→ModelConfigfirst、精确provider、显式secret/Fernet先取evenplaintext、每branchURL/status大小写/extract顺序/timeout/IPv4/non200pending；注入完整矩阵加本地loopback与error资源专测。

## 3. Single Integration and Author Freeze

- [x] 3.1 唯一I作者消费已冻结public合同接完整六routes、kwargs与必要旧registry增量，保留上一批已验收全部route/privatepolicy/cold/Taskkwargs；actualregistry集合重算，条件91→97不硬编码业务完成；无Env/Engine/Session/client/Worker默认资源。
- [x] 3.2 冻结完整新code SHA与必要旧真实diff、作者命令/exit/全部失败史/自有资源终态；候选60code键与60source分开，旧其余code/history/source守恒，不改current schedule/frontend/TaskObservation实现。

## 4. Independent Source Review

- [x] 4.1 GPT-6.1 Sol/xhigh独立读取完整新代码、旧trueDiff与六方法/十三协议必要固定语义，核公开authfacade、ORMautoflush/commit/refresh、refund/guard/admin/rawJSON、preparetry外与资源；关闭P1/P2和验证gap，内部review不代指定外审或root实跑。

## 5. Root Actual Acceptance

- [x] 5.1 root绑定冻结SHA实际full backend/syntax/source60/完整actualfullpathcounts/旧保护图，保留真实命令exit/失败史/前后SHA，不由作者green或硬编码数量代过。
- [x] 5.2 root受审自有helper实跑真实JWTSQL/TCP完整六positive及关键拒绝、实际SQL锁/commit/rollback/PKrefresh/ackunknown无replay、真实SQLsuperuser/身份close先于业务、rawASGI/bodyhash与admin两9字段；fullowner24/FKON非空全行差分精确守恒。
- [x] 5.3 root实跑十三供应商注入协议与owned本地loopback、配置/解密/URL/状态顺序/timeout/IPv4/non200/xAIexpired；无真实provider凭据/网络执行声明，不以始终unknown或完整Worker代替。
- [x] 5.4 root完成全部owned Session成功close/client/response/DB/Engine/listener/thread/socket/PID/port/tmp终态与目标保护strictAND；逐层分开fixture/auth/业务写、SQLite/PG、protocol/providerreal/browser/deploy，失败日志与unknowndurable留存。

## 6. Seven Documents and CLI Closeout

- [x] 6.1 按真实本地结果更新五旧EOF完整prefix、两new架构/verification与本tasks标记共8路径；独立Sol文档review，解释六方法/九字段/退款歧义/十三协议/authclose/unknown/层次限制，不预报外审或finalCLI。
- [x] 6.2 root实际普通OpenSpecstrict/status/apply及finalinventory/source/code/doc/resource守卫，按实际registered集合更新声明差集；中文阶段提交推送已授权新origin，本族完整本地验收后推进生成/Worker/backend全部→React全部→最后语言评估。

## 7. External Review

- [ ] 7.1 指定GPT-5.6 Sol/xhigh Grillme offline pending；不probe/pair/substitute、不拿internalreview代过，后续由root按用户授权安排。
