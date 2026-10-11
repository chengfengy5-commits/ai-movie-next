# chat-generation

普通OpenSpec change modularize-backend-chat-generation。39四字段精确保留；Scenario为未来验收，执行0/通过0/路线扣减0。A14公开重连子例未选择，A38真实浏览器未执行，A39指定GPT-5.6 Sol/xhigh OFFLINE PENDING。其他设计子例必须完整执行，不能用source接受或typed plan标通过。

## ADDED Requirements

### Requirement: A01 六POST path/method及DTO

原分类：baseline

原 requiredCheck（逐字）：

> 实际旧payload字段、extra forbid/strict类型、target判别联合；不连带GET proxy-download；各response结构精确比对

系统 SHALL 实现上述原检查，并同时满足public-contracts完整规范。

#### Scenario: A01 适用路径与边界

- **WHEN** 提交六类合法请求与strict/extra/target负例
- **THEN** 六POST逐个匹配固定payload/response，非法strict/extra/target拒绝，GET proxy-download不被迁移

责任：all shards + root；ports：P1, P3, P6；状态：planned_unexecuted。

### Requirement: A02 鉴权分层

原分类：baseline

原 requiredCheck（逐字）：

> 401坏token/已撤销session；会员403；superuser会员豁免；expiredmember同keyreceipt仍可回读；newgeneration拒绝

系统 SHALL 实现上述原检查，并同时满足public-contracts完整规范。

#### Scenario: A02 适用路径与边界

- **WHEN** 坏token、撤销session、expired会员重放和新生成
- **THEN** 坏/撤销401、新会员403，superuser豁免，expired但可信账号原key回原receipt而不新增生成

责任：L-A；ports：P1；状态：planned_unexecuted。

### Requirement: A03 series/chapter/asset权限

原分类：baseline-plus-design

原 requiredCheck（逐字）：

> owned/team/claimedpermission、storyboard同chapter、asset同series；review/optimize旧缺口修复按D8新增测试标注

系统 SHALL 实现上述原检查，并同时满足public-contracts完整规范。

#### Scenario: A03 适用路径与边界

- **WHEN** owned/team/claimed和跨series/chapter目标请求
- **THEN** 允许真实权限，跨关系拒绝；D8新增review/optimize规则明确归新设计

责任：L-A + L-B/L-C；ports：P1, P5；状态：planned_unexecuted。

### Requirement: A04 model/prompt归属与类别

原分类：baseline

原 requiredCheck（逐字）：

> 系统active优先/ownedBYOK；image/videocategory严格；自动text排序按UserModelOrder；新explicittext类别/ownedprompt规则标D8

系统 SHALL 实现上述原检查，并同时满足public-contracts完整规范。

#### Scenario: A04 适用路径与边界

- **WHEN** 系统active/BYOK/自动text顺序和ownedprompt输入
- **THEN** 类别/归属/排序匹配固定源，不能跨账号/类别或漏systemparams

责任：L-A + all consumers；ports：P1, P3；状态：planned_unexecuted。

### Requirement: A05 Idempotency-Key

原分类：baseline

原 requiredCheck（逐字）：

> 缺失/空白/>255拒绝；原key保留空格和长度；同user+op+key同digest一receipt；异digest409；跨用户/operation独立

系统 SHALL 实现上述原检查，并同时满足public-contracts完整规范。

#### Scenario: A05 适用路径与边界

- **WHEN** 原raw key有空格、重放、异digest及跨user/op
- **THEN** 原字符串保持，同digest一receipt、异digest409，scope隔离，缺失/blank/>255拒绝

责任：L-B/L-C via L-A core；ports：P1；状态：planned_unexecuted。

### Requirement: A06 single uncertaincommit

原分类：baseline

原 requiredCheck（逐字）：

> commit报错后同keylookup：已commit恢复原receipt；未知503指导原keyread，绝不换key自动重扣

系统 SHALL 实现上述原检查，并同时满足public-contracts完整规范。

#### Scenario: A06 适用路径与边界

- **WHEN** single commit返回异常但原提交可能成立
- **THEN** 新短TX原keylookup回原receipt或unknown503，不换key重扣

责任：L-B/L-C；ports：P1；状态：planned_unexecuted。

### Requirement: A07 batch all-or-nothing

原分类：baseline

原 requiredCheck（逐字）：

> 第N项参数/归属/model/credit失败时零TaskSubmission/Task/Unit/Step/messages/ledger；成功一次commit所有item；keyreplay不增行

系统 SHALL 实现上述原检查，并同时满足public-contracts完整规范。

#### Scenario: A07 适用路径与边界

- **WHEN** 第N个batch item非法或总余额不足
- **THEN** 零graph/messages/ledger行；合法batch一次commit，原keyreplay0新增

责任：L-B；ports：P1；状态：planned_unexecuted。

### Requirement: A08 batch limits/order

原分类：baseline

原 requiredCheck（逐字）：

> 64items/30optimize、duplicateassettypeid、allorderdigest、text255/16k/256k、refs256/8MiB/32MiB边界；并发3..10default5

系统 SHALL 实现上述原检查，并同时满足public-contracts完整规范。

#### Scenario: A08 适用路径与边界

- **WHEN** item/frame/text/ref/concurrency在边界两侧
- **THEN** 64items/30frames/3..10default5及全部固定size/order/duplicate限制严格验证

责任：L-B/L-A；ports：P1, P3, P6；状态：planned_unexecuted。

### Requirement: A09 serverpricing

原分类：baseline

原 requiredCheck（逐字）：

> fixed/by_quality(size中的1K/2K/4K前缀)/per_second×duration/resolution分档；credit非负整数（拒bool/小数），duration1..3600严格整数；fixed_by_quality匹配0回base的旧fallback；BYOK0 nocharge；不信客户端费用；不自行应用AIStars倍率

系统 SHALL 实现上述原检查，并同时满足public-contracts完整规范。

#### Scenario: A09 适用路径与边界

- **WHEN** 客户端费用、bool/小数credit或不同quality/duration
- **THEN** server非负整数price，duration1..3600，0fallback/BYOK0保旧，拒bool/小数，不加AIStars倍率

责任：L-A + L-B/L-C；ports：P1, P2；状态：planned_unexecuted。

### Requirement: A10 执行配置冻结

原分类：decision D4

原 requiredCheck（逐字）：

> accept后改modelparams/price/prompt、停用/删除、credentialsrotation：执行使用约定snapshot且不泄露credentials；systemparams不可漏

系统 SHALL 实现上述原检查，并同时满足public-contracts完整规范。

#### Scenario: A10 适用路径与边界

- **WHEN** accept后改params/price/prompt/状态或轮换凭证
- **THEN** 固定nonsecret snapshot执行，live只原可证明绑定，secret不泄露，不能漏params或换账号

责任：L-A + all consumers；ports：P1, P3, P6；状态：planned_unexecuted。

### Requirement: A11 single receipt

原分类：baseline

原 requiredCheck（逐字）：

> responsequotedamount/billingstatus取unit，包括0/empty原值；taskidownedread、message多task409、missingmessage空状态

系统 SHALL 实现上述原检查，并同时满足public-contracts完整规范。

#### Scenario: A11 适用路径与边界

- **WHEN** single unit quote为0/empty并查询ownedtask/message
- **THEN** 保原收据值和Unit账务来源，multi-task-message409，missingmessage空状态

责任：L-B/L-C + root；ports：P1, P2；状态：planned_unexecuted。

### Requirement: A12 batch receipt

原分类：baseline

原 requiredCheck（逐字）：

> tasks list顺序、assetname/messageid/taskid不变；single submissionreceipt保持exactone，扩batchreadback明确独立契约

系统 SHALL 实现上述原检查，并同时满足public-contracts完整规范。

#### Scenario: A12 适用路径与边界

- **WHEN** 多item batch接受或原receipt回读
- **THEN** 有序tasks/type/assetname/messageid/taskid保留，single exactone不扩作batchbuilder

责任：L-B + root；ports：P1；状态：planned_unexecuted。

### Requirement: A13 stream event sequence

原分类：baseline

原 requiredCheck（逐字）：

> start→content/error→done、JSONdata前缀、content拼接、enable_thinking上游fields、空AIfailed；reasoning是否透传仅D6扩展

系统 SHALL 实现上述原检查，并同时满足public-contracts完整规范。

#### Scenario: A13 适用路径与边界

- **WHEN** 正常内容流、reasoning和generator异常
- **THEN** 旧start/content/error/done合法序列及JSONdata保持，reasoning默认不透传，generator异常允许error无done，空AI失败

责任：L-A；ports：P3, P6；状态：planned_unexecuted。

### Requirement: A14 stream断连与复连

原分类：baseline-plus-design

原 requiredCheck（逐字）：

> SSE断开后台仍执行、task/message完成可查询；process重启恢复/重连cursor是D6新增能力，不拿旧源码当已支持

系统 SHALL 实现上述原检查，并同时满足public-contracts完整规范；A14公开重连部分仍未选择。

#### Scenario: A14 适用路径与边界

- **WHEN** SSE断开但可选Task/message已绑定
- **THEN** 同进程后台继续且结果可查；不称已实现进程重启恢复或公开cursor/reconnect

责任：L-A + root frontend；ports：P5, P6；状态：planned_unexecuted。

### Requirement: A15 streamledger

原分类：baseline-plus-design

原 requiredCheck（逐字）：

> skip=false真实debit先于task，skip=true不重复扣；新可靠退款需verifieddebit、无debit不能发refund；D2对旧风险的明确修复

系统 SHALL 实现上述原检查，并同时满足public-contracts完整规范。

#### Scenario: A15 适用路径与边界

- **WHEN** positive系统stream无message、skip/BYOK或commit不明
- **THEN** 原debit/proof先TX，无Task仍可验证退款；skip/zero无refund，unknown查原attempt

责任：L-A；ports：P2, P5, P6；状态：planned_unexecuted。

该原项必须追加的已接受设计修复子例（未执行，未修改requiredCheck）：

- Positive first debit/proof same commit with no message/Task; skip-positive/BYOK/zero0 debit/refund.
- Second Task commit failure/unknown and pre-exposure gate reject; original proof known-not-started refund once.
- First debit/confirm/start/refund commit unknown original lookup, no duplicate finance/Provider start.

### Requirement: A16 review上下文

原分类：baseline

原 requiredCheck（逐字）：

> 当前frame text、errorreason仅参考且禁止输出、asset/prev截断，no prompt无内置system fallback；sourcefailbubble不覆写

系统 SHALL 实现上述原检查，并同时满足public-contracts完整规范。

#### Scenario: A16 适用路径与边界

- **WHEN** review prompt/errorreason/上下文或Provider失败
- **THEN** no prompt不补内置system，参考error不输出，截断保旧，失败不覆写

责任：L-A；ports：P1, P3；状态：planned_unexecuted。

### Requirement: A17 review收费与结果

原分类：baseline

原 requiredCheck（逐字）：

> 先成功framewrite再扣；系统不足结果保留cost0；BYOK0；original_text首次保留、storyboarddescription同步；如D2改策略单独验

系统 SHALL 实现上述原检查，并同时满足public-contracts完整规范。

#### Scenario: A17 适用路径与边界

- **WHEN** review生成并写frame后余额不足或fee前crash
- **THEN** 写后才扣，正报价不足保结果cost0永久waiver；original_text首次保留、description同步，crash只续fee

责任：L-A；ports：P2, P5；状态：planned_unexecuted。

该原项必须追加的已接受设计修复子例（未执行，未修改requiredCheck）：

- Review atomic result/Work and apply commit crash before fee: fresh claim resumes fee only, no reapply/generate.
- Immutable positive insuff/confirmed-safe-failure/cancel waiver, no restart surcharge; original_text/description/media/notes true application.

### Requirement: A18 optimize费用与partial

原分类：baseline

原 requiredCheck（逐字）：

> 逐frameAI→debit→write；一成功completed/0成功failed、余额不足停止新frame；completionorder结果列表和成功cost不被后失败覆盖

系统 SHALL 实现上述原检查，并同时满足public-contracts完整规范。

#### Scenario: A18 适用路径与边界

- **WHEN** optimize多个frame含成功、余额停止和已扣未写crash
- **THEN** AI→debit→write，真应用≥1completed/0failed，partial/order/cost不被后失败覆盖，已付只续delivery

责任：L-A；ports：P2, P5, P6；状态：planned_unexecuted。

该原项必须追加的已接受设计修复子例（未执行，未修改requiredCheck）：

- Optimize debit+delivery_pending commit crash before apply: fresh claim same result/actual fee/CAS,0 Provider POST.
- Charged target conflict retained; insufficient stops newcharge/generation, allowed paid delivery drain and true partial application/cost/order.

### Requirement: A19 optimize排重

原分类：baseline-plus-design

原 requiredCheck（逐字）：

> 旧sameuserchapterrunning409、多chapter独立、terminal可新提交；如新增headerretry receipt标D1而非source事实

系统 SHALL 实现上述原检查，并同时满足public-contracts完整规范。

#### Scenario: A19 适用路径与边界

- **WHEN** 相同user/chapter同时optimize或terminal后新提交
- **THEN** inflight集合锁内409，多chapter独立，terminal可新，未新增必需header幂等

责任：L-A；ports：P1；状态：planned_unexecuted。

### Requirement: A20 frame context

原分类：baseline

原 requiredCheck（逐字）：

> chapter_texts按idx前文最多40，chapter_backgroundfallback兼容，no prompt不补内置system，30frame/3..10sem与writes串行

系统 SHALL 实现上述原检查，并同时满足public-contracts完整规范。

#### Scenario: A20 适用路径与边界

- **WHEN** 30frame、前文40及chapter background/no prompt
- **THEN** 旧context/index/fallback/sem/write顺序与3..10保持，无内置system补入

责任：L-A；ports：P1, P3, P6；状态：planned_unexecuted。

### Requirement: A21 稳定target晚结果

原分类：baseline-plus-design

原 requiredCheck（逐字）：

> imageasset已有图不覆盖；target删除/换图/章节重排/duplicateStoryBoardID隔离；review/optimize新增stableIDCAS标D5

系统 SHALL 实现上述原检查，并同时满足public-contracts完整规范。

#### Scenario: A21 适用路径与边界

- **WHEN** 目标已有图、删除、改图、重排、duplicateID或晚结果
- **THEN** 已有图不覆盖，stableID/full digest/CAS隔离，duplicate拒，无ID整章任一编辑冲突

责任：L-A + L-B/L-C；ports：P1, P5；状态：planned_unexecuted。

### Requirement: A22 media确认失效

原分类：baseline

原 requiredCheck（逐字）：

> image/previewidentity改变mediarevision+1，撤approved→unmarked/needs_reconfirmation并notesrevisionCAS，同chapter事务；签名参数不改identity

系统 SHALL 实现上述原检查，并同时满足public-contracts完整规范。

#### Scenario: A22 适用路径与边界

- **WHEN** 应用更换媒体identity或仅签名query变化
- **THEN** 真identity revision+1/撤approved/notesCAS同TX，签名query不改identity

责任：L-A core + L-B；ports：P5；状态：planned_unexecuted。

### Requirement: A23 claim concurrency

原分类：baseline

原 requiredCheck（逐字）：

> 两个PostgreSQLworker只一个claim，新generation/token；SKIPLOCKED；SQLite只测试单worker，不将其并发证明推广production

系统 SHALL 实现上述原检查，并同时满足public-contracts完整规范。

#### Scenario: A23 适用路径与边界

- **WHEN** 两个PostgreSQLworker争同Task
- **THEN** 只有一claim、fresh token/generation和SKIPLOCKED；SQLite单worker检查不冒充PG并发

责任：L-A + root DB readiness；ports：P2, P6；状态：planned_unexecuted。

### Requirement: A24 fence全面拒旧

原分类：baseline

原 requiredCheck（逐字）：

> 续租失败、leaseexpired、token/generation/ownerwrong、等chapterlock后失效：task/message/externalid/frame/image/ledger全部拒写

系统 SHALL 实现上述原检查，并同时满足public-contracts完整规范。

#### Scenario: A24 适用路径与边界

- **WHEN** 续租/lease/token/generation/owner失效或等chapter锁后lost
- **THEN** Task/message/externalid/target/ledger全拒旧写，continuation也核phase/fence

责任：L-A + all executors；ports：P2, P5, P6；状态：planned_unexecuted。

该原项必须追加的已接受设计修复子例（未执行，未修改requiredCheck）：

- Continuation token/generation/DBlease/phase rejects old wallet/target/task/message including chapter-lock wait.
- Live generation claim bind phase only, no steal live parent lease; local capability cannot prepare Provider intent.
- Lost raw poll response unknown until ordered trusted recovery accepts source links.

### Requirement: A25 durable intent

原分类：decision D1/D3

原 requiredCheck（逐字）：

> 提交前intentcommit；已有intent只query不能二次POST；POSToutcome未知pendingverification；submissionkey/stepunique和nonsecretrequestsnapshot

系统 SHALL 实现上述原检查，并同时满足public-contracts完整规范。

#### Scenario: A25 适用路径与边界

- **WHEN** create前intent未确认、已存在intent或POST未知
- **THEN** 只有confirmed capability可POST，unknown原evidence pending、无第二POST，稳定unique/key/nonsecret snapshot

责任：L-A + all provider adapters；ports：P2, P3；状态：planned_unexecuted。

### Requirement: A26 evidence apply/settle

原分类：decision D1/D3

原 requiredCheck（逐字）：

> 重复observation完全same接受，不同payloadsamekey冲突；lateevidence只unknown；可信valid+recoverableresult才apply；fallback不当success

系统 SHALL 实现上述原检查，并同时满足public-contracts完整规范。

#### Scenario: A26 适用路径与边界

- **WHEN** 重复/冲突/late观察和未验证fallback
- **THEN** same接受/different冲突，late原unknown，只有可信linkedrecoverable验证可apply，Provider truth与policy stop分开

责任：L-A + all executors；ports：P2, P4, P5；状态：planned_unexecuted。

该原项必须追加的已接受设计修复子例（未执行，未修改requiredCheck）：

- Non200 auth stays unknown; distinct policy event no fabricated known provider failed.
- Late original active real result vs sticky stop/financial disposition; retired evidence cannot select/apply/charge.

### Requirement: A27 refund ledger

原分类：baseline-plus-design

原 requiredCheck（逐字）：

> 真实debitrelatedref、退款不过debit、重复refund零新增；deadline7200第一次intent固定；timelysuccess/nocharge；source执行器接入属D1

系统 SHALL 实现上述原检查，并同时满足public-contracts完整规范。

#### Scenario: A27 适用路径与边界

- **WHEN** 真实debit退款重放、deadline和timely结果
- **THEN** related原debit/amount严格、0重复新增，首次7200固定，timely/nocharge真结算，无debit不造log

责任：L-A + all executors；ports：P2, P6；状态：planned_unexecuted。

该原项必须追加的已接受设计修复子例（未执行，未修改requiredCheck）：

- Taskless stream original usage key/id/related-debit refund≤debit, duplicate0 logs; skip-positive never quote refund.
- Timely generation local continuation after7200 no deadline reset; late text Work no newfee/apply.
- Policy event eligibility distinct provider truth; established failure refund-beforecancel vs startedcancel retain.
- PolicyStopEvent committed→standalone settler paused/crashed→cancel adapter premarker unique related-debit refund+event disposition→recover/replay0 new refund.
- Unknown full caller commit lookup original event/debit/refund/Task-Unit markers/Work/fence before financial retry;true0 no refund log.

### Requirement: A28 取消端到端

原分类：decision D2

原 requiredCheck（逐字）：

> queued未intentrefund；startedcancel停新submit且约定retain；failure/deadline先refund再cancel；shutdown不同于usercancel；legacy status-only保兼容直至新策略

系统 SHALL 实现上述原检查，并同时满足public-contracts完整规范。

#### Scenario: A28 适用路径与边界

- **WHEN** queued/started/failure-first/deadline-first/mixed取消和shutdown
- **THEN** 全consumer chain按N6先原event金融再ANYmarker，同TX Workstop/revoke/aggregate，started先cancel retain，shutdown不同cancel

责任：L-A + all executors；ports：P2, P6；状态：planned_unexecuted。

该原项必须追加的已接受设计修复子例（未执行，未修改requiredCheck）：

- Cancel even succeeded Unit local Works; review applied/unbilled waiver, optimize charged/unapplied stopped.
- Revoke/quiesce owner-control projection,0 newcharge/apply, original data/debit/refund/waiver retained.
- Entire queued/started/failure-first/deadline-first/mixed/replay/shutdown chain still required, not selected subset.
- Cancel waits User lock then fresh-discovers committed original event;refund/event financial disposition BEFORE ANY Task/Unit cancel marker;originalcancel+ALL Workstop+lease revoke+aggregate ONE TX.
- Reverse authenticated started cancel commit→late qualifying event/settler:original started marker retained,0 new refund;all public/P6 cancel/replay paths same adapter.

### Requirement: A29 恢复dispatch

原分类：baseline-plus-design

原 requiredCheck（逐字）：

> fixedstartup仅旧video恢复；newworker五typesdispatch；review/optimizegraph缺失分类；query/pending/applied/mixed/noresubmit从完整链验证，新增消费标D1

系统 SHALL 实现上述原检查，并同时满足public-contracts完整规范。

#### Scenario: A29 适用路径与边界

- **WHEN** 五types重启、Work ready无oldStep或旧无图
- **THEN** 真实generation/local/query/verify/due dispatch，new phase claim真消费，old无图隔离、unknown不resubmit

责任：L-A + root；ports：P1, P2, P3, P6；状态：planned_unexecuted。

该原项必须追加的已接受设计修复子例（未执行，未修改requiredCheck）：

- No old ready step but Work ready: new claim_continuation plus actual resume_fee/resume_delivery/finalize consumption.
- Unknown phase commits original facts lookup, release/restart local routing, terminal stops no recharge.
- Old no-graph unsupported graph quarantine; graphless stream fee reconciliation no Provider restart.

### Requirement: A30 Providertext协议

原分类：baseline

原 requiredCheck（逐字）：

> OpenAI/DeepSeek/customSSEfragment、HTTP200error、非JSON行、reasoningfallback、600s流/300soptimize fake transport；error字段与结束序列

系统 SHALL 实现上述原检查，并同时满足public-contracts完整规范。

#### Scenario: A30 适用路径与边界

- **WHEN** OpenAI/DeepSeek/custom碎片、非JSON、HTTP200error
- **THEN** fake transport覆盖旧协议/error/end，600s stream/300s optimize及reasoning fallback不统一改写

责任：L-A；ports：P3, P6；状态：planned_unexecuted。

### Requirement: A31 Providerimage协议

原分类：baseline

原 requiredCheck（逐字）：

> 每已列imagefamily fixture：syncurl/b64、multipart/jsonrefs、asyncsubmitdirectdata/ID/pollterminal、HTTP/JSONerror、超时与未返回媒体

系统 SHALL 实现上述原检查，并同时满足public-contracts完整规范。

#### Scenario: A31 适用路径与边界

- **WHEN** 逐固定imagefamily sync/async/json/multipart/URL/base64 fixture
- **THEN** 每family payload/ref/submit/poll/terminal/error/timeout/无媒体保持，不将未验证fallback当成功

责任：L-B；ports：P3, P4；状态：planned_unexecuted。

### Requirement: A32 Providervideo协议

原分类：baseline

原 requiredCheck（逐字）：

> 每family fixture payload字段refmode/time/res/ratio；JSONcode和terminalnames、multipartdoc实际JSON、wait与poll差异、URL或auth/content，不能全强制/v1videos

系统 SHALL 实现上述原检查，并同时满足public-contracts完整规范。

#### Scenario: A32 适用路径与边界

- **WHEN** 逐固定videofamily ratio/res/time/ref/wait/poll/content fixture
- **THEN** 异构路径/JSONcode/terminal/multipart实际JSON/wait与content原语保留，不全强制/v1/videos

责任：L-C；ports：P3, P4；状态：planned_unexecuted。

### Requirement: A33 haoai retry及未知create

原分类：baseline-plus-design

原 requiredCheck（逐字）：

> ref下载失败最多2resubmit、pollcounter不重置、12auth_unavailablefail、替换最新id；新attempt证据/fee范围标D3

系统 SHALL 实现上述原检查，并同时满足public-contracts完整规范。

#### Scenario: A33 适用路径与边界

- **WHEN** HaoAI active known ref failure、unknown create和auth12竞态
- **THEN** max2 retry/一费用/原720poll期限；max1reservation ordered auth政策，event-first退款和cancel-firstretain保持，不fakeProvider failure

责任：L-C + L-A core；ports：P2, P3, P6；状态：planned_unexecuted。

该原项必须追加的已接受设计修复子例（未执行，未修改requiredCheck）：

- Only active allowed optional step ready; original+max2 known ref retry, unknown create0 retry.
- Atomic reservation before GET, crash permanent budget, duplicate/order/closed/retired observation policy idempotence.
- auth12 separate policy stop; nonauth/200/newid reset onlystreak, transportexception unchanged, total720/deadline fixed.
- auth12 original unknown PolicyStopEvent remains independently committable;cancel-aware preflight prevents delayed settler schedule from changing event-first refund toretain,reverse cancel-first unchanged.

### Requirement: A34 媒体transfer

原分类：baseline

原 requiredCheck（逐字）：

> managedcurrent/legacyURLbyteforbyte、explicitaccelerationmap、200MiBchunk/contentlength上限、禁止redirect/privateIP/activeMIME、tmpcleanup取消失败场景

系统 SHALL 实现上述原检查，并同时满足public-contracts完整规范。

#### Scenario: A34 适用路径与边界

- **WHEN** managed/legacy URL、超size/redirect/private/activeMIME和cancel
- **THEN** 字节/query/显式map保持，200MiB/chunk/contentlength与tempcleanup覆盖，无全局网络豁免

责任：L-B + L-C consumers；ports：P4；状态：planned_unexecuted。

### Requirement: A35 R2adapter

原分类：baseline

原 requiredCheck（逐字）：

> date/business/UUIDkey、mime/immutablecache、nonsecretlog、SDKthread/standard3attempt；fakeSDK，无真实bucket/credentials操作

系统 SHALL 实现上述原检查，并同时满足public-contracts完整规范。

#### Scenario: A35 适用路径与边界

- **WHEN** fake R2 SDK上传与故障
- **THEN** date/business/UUID/MIME/cache、nonsecretlogs、offloop/standard3attempt保留，无真实bucket/secret操作

责任：L-B；ports：P4；状态：planned_unexecuted。

### Requirement: A36 localMuse/refinputs

原分类：baseline-plus-design

原 requiredCheck（逐字）：

> dataURI/URL/localfile、最大ref截取/audio路径、upload_dir配置读取；newguardpolicy分typed输入；不误禁授权localprovider

系统 SHALL 实现上述原检查，并同时满足public-contracts完整规范。

#### Scenario: A36 适用路径与边界

- **WHEN** Muse dataURI/URL/localfile/audio/refcap及缺capability
- **THEN** 原upload_dir/absolutepaths/ref截取保留，typed guards不误禁已授权local，清ownedstage不删caller

责任：L-B/L-C；ports：P3, P4；状态：planned_unexecuted。

### Requirement: A37 公开模块边界与路由

原分类：integration-required

原 requiredCheck（逐字）：

> 只publicportimports；无legacy app/runtime import/env discovery；rootapp惰性explicitfactory，无source60污染；root实际routeinventory再计数

系统 SHALL 实现上述原检查，并同时满足public-contracts完整规范。

#### Scenario: A37 适用路径与边界

- **WHEN** Root实际装配modules/routes/registry/config
- **THEN** 仅public imports、惰性explicitfactory、无legacy/env/source60污染；actualrouteinventory后才计数

责任：root single composition owner；ports：P1, P2, P3, P4, P5, P6；状态：planned_unexecuted。

### Requirement: A38 React用户全流程

原分类：frontend-required

原 requiredCheck（逐字）：

> 六入口触发、忙/等待/失败/部分结果/恢复、原keylostresponse、任务面板/source bubble、收费展示+确认失效；真实浏览器证据另存，不由fakebackendtests替代

系统 SHALL 实现上述原检查，并同时满足public-contracts完整规范。

#### Scenario: A38 适用路径与边界

- **WHEN** 真实React用户走六入口与失败/partial/lostresponse/application
- **THEN** 实际浏览器证明完整忙/恢复/taskpanel/sourcebubble/费用/审批失效；fake后端不能替代

责任：three shards re-used for exclusive UI features + root browser acceptance；ports：P1, P5, P6；状态：planned_unexecuted。

### Requirement: A39 证据分层

原分类：external-review-required

原 requiredCheck（逐字）：

> 源码接受、Luna作者测试、root实际测试/浏览器、指定Grillme、release授权/完成逐层独立；外部review仍OFFLINE PENDING

系统 SHALL 实现上述原检查，并同时满足public-contracts完整规范。

#### Scenario: A39 适用路径与边界

- **WHEN** 汇总源码、作者检查、Root运行、浏览器和指定外审证据
- **THEN** 各层独立；A39 GPT-5.6 Sol/xhigh仍OFFLINE PENDING，不探测/替换或伪称release

责任：root evidence/review layers；ports：P1, P2, P3, P4, P5, P6；状态：planned_unexecuted。

该原项必须追加的已接受设计修复子例（未执行，未修改requiredCheck）：

- Independent originalP2 → author03repair → independentrecheck layers distinct; no implementation/test/Root/browser/external/release acceptance inferred.

### Requirement: 生成后本地继续真实可claim

系统 SHALL result+Unit fulfillment+mandatory Work同commit；phase current fence/新claim/recovery真消费，无Provider/假Step，永久waiver不可重开。完整规范见public-contracts N2/C3及design，映射原39，不增加已通过数量。

#### Scenario: 生成后本地继续真实可claim的恢复与反例

- **WHEN** review apply后fee前或optimize debit后apply前crash且Unit已succeeded
- **THEN** 恢复新phase claim只执行剩余fee或原delivery/finalize，0新POST/重复fee/application，unknown原事实lookup。

### Requirement: stream无Task也有原金融证明

系统 SHALL first positive debit+usage+private proof同commit，无Task/Unit；confirmed proof+NEW one-shot exposed才Provider，原settlementproof refund once，skip/BYOK/0不退。完整规范见public-contracts N3/C8及design，映射原39，不增加已通过数量。

#### Scenario: stream无Task也有原金融证明的恢复与反例

- **WHEN** 无message流firstcommit未知或secondTaskTX失败/gate pre-exposure拒绝
- **THEN** 原attempt lookup证commit/已证明no-start才结算，notfound不猜rollback，repeat/unknown/restart不发新startcap。

### Requirement: poll唯一预占与原unknown truth

系统 SHALL max1原子reservation/shared720/原7200/5s/3s，crash耗预算，ordered once streak/auth12 sticky原unknown，不造Provider failed。完整规范见public-contracts N4/N5/C5/C6及design，映射原39，不增加已通过数量。

#### Scenario: poll唯一预占与原unknown truth的恢复与反例

- **WHEN** worker在GET前crash或旧closed响应晚到及12次合格auth
- **THEN** 原预算不返还/重置，duplicate/retired不改streak；stickyevent与late真原active证据/finance分离，无新POST/apply。

### Requirement: 原policy成立优先于取消marker

系统 SHALL 同User锁后fresh discovery、ALL original events/debit结算flush BEFORE ANY Task/Unit marker，再originalcancel+ALL Workstop+revoke+aggregate同callerTX；反序validstarted marker保retain。完整规范见public-contracts N6/C7及design，映射原39，不增加已通过数量。

#### Scenario: 原policy成立优先于取消marker的恢复与反例

- **WHEN** event已commit且settler暂停，cancel等待User后继续；另反序先valid startedcancel
- **THEN** event-first唯一refund和event状态先marker，later replay0变化；cancel-first引用原marker0新refund；actual0无log，positive未证实rollback无marker，unknown完整原lookup。

### Requirement: owned完成结果与应用原子CAS

系统 SHALL sameSession owner/completedTask/selectedresult/currentaccess/unique stableID或严格noID/SQL CAS/source+media+notes+ApplicationRecord同commit，canvas client自动PUT，worker不autoapply。完整规范见public-contracts N7/C9/R2及design，映射原39，不增加已通过数量。

#### Scenario: owned完成结果与应用原子CAS的恢复与反例

- **WHEN** 原结果应用commit不明后重放，或章重排/无ID整章编辑/duplicateID
- **THEN** exactauthorized appliedrecord先旧CAS且0generation/fee/revision；稳定目标未改可rebase、noID编辑和duplicate拒绝，任一失败全rollback。

### Requirement: 四资源guard与原principal绑定

系统 SHALL 所有nonsecret冻结，live仅原有效principal；四cap显式有界、SDKoffloop，配置/存储失败不新create/fallback假成功，已有idquerypending。完整规范见public-contracts N1/N7/C1/C10/R3及design，映射原39，不增加已通过数量。

#### Scenario: 四资源guard与原principal绑定的恢复与反例

- **WHEN** secret轮换未证同principal、provider-content跨origin、未授权local路径或R2故障
- **THEN** 原绑定以外拒绝/保pending，禁止globalprivate exemption/header泄漏，ownedtemp cleanup，无第二POST。

## 验收执行边界

A28所有queued/started/failure/deadline先后/replay/mixedUnit/shutdown/ALL Work/full public/P6消费必测，不能选择子例冒完成。A14只source-compatible disconnect可用，公开重连/进程重启恢复未选。A38真实浏览器另证；A39指定外审OFFLINE PENDING、release授权/实际发布另层。SQLite/fake SDK/source阅读不能冒充PostgreSQL/网络/浏览器/发布实证。
