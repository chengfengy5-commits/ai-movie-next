# 实施任务

T01、T02 已完成本地验收、文档及中文 Git 收尾，按批实现继续。公共契约、图片适配器、xAI/漫小白/HaoAI 视频原始协议与提交查询适配器、共享持久化、媒体本地暂存与托管地址规范化、公网 URL/DNS 校验已分别复核、局部测试并推送。模型目录与服务端报价独立复审 0 P1/P2、Root 51 项通过，中文提交 bf6416d 已推送。当前整后端 run06 实际 1162 项通过、4 项失败、249 个子断言通过；四项结果回写冷导入及异常兼容回归正在窄修，完整回归未通过。B3 真实 PostgreSQL 验证尚未执行，验证脚本复审与媒体公网获取/R2 写入子批开发并行；独占路径不重叠。原 39 项运行验收通过数仍为 0，T03–T25 共 23 项保持未勾选，组件通过不代表整项或整体完成。实现 gpt-6-luna/xhigh，评审 GPT-6.1 Sol/xhigh；不可用明确报告，不替换。

关键路径：模型目录/报价→可信 actor、原凭据绑定生产端与锁定准入→费用阶段及 Worker/路由接线；共享 schema/结果桥接→真实 PostgreSQL 同事务与权限围栏验证；媒体公网传输/R2 子批→原绑定 content 与同步 facade→实际 HTTP/R2 集成；之后完成 React、浏览器及原 39 项验收。三原独占数组与 Root 写权见 design；共享 schema/app/ORM/config/frontend 仅原三 Luna 中一个由 Root 指定，不能第四写者。未接入路由和前端仍纳入整体范围，外审 GPT-5.6 Sol/xhigh 保持 OFFLINE_PENDING。

- [x] **T01 — Root**：完成当前GAP175修复、真实适用175验收、中文文档和Git收尾；记录23G+2A处理及运行身份。

  验证产物：Root run05 实际 175/175 通过（G119、A23、P33），runId `dbcead9c-e236-4664-87c2-50280761c45b`；中文文档提交 `78f2a40`、本地任务收尾提交 `caf49bd` 已推送。旧 run03 的150/175与 run04 的174/175仍保留原结果，指定外审另列 T21。

  对应：当前GAP独立线。

- [x] **T02 — 不同GPT-6.1 Sol/xhigh + Root**：独审五稿全文SHA、39四字段、三path数组和完整已关闭修复义务，Root消费后在既有授权安装普通OpenSpec。

  验证产物：五稿独立复核通过，Root 已消费并安装普通 OpenSpec；严格校验、status、apply 实际退出0，规划提交 `f637815` 已推送。该前置任务不代表六入口产品运行验收。

  对应：A39。

- [ ] **T03 — Root唯一共享执行者**：识别真实router/registry/config/ORM/migration/frontend路径，只分配原三Luna之一共享写权；冻结P1–P6/type/phase/error/snapshot headers。

  验证产物：path清单无重叠，headers/fullSHA与普通OpenSpec绑定，剩余writers只能publicports。

  对应：A37。

- [ ] **T04 — L-A**：实现server actor/replay-first/catalog locks/fingerprints/pricing/frozen nonsecret snapshot/original credential binding公共接口与fakes。

  验证产物：A02–A10完整边界；保三media rawheader，text无新必需key，secret不入记录。

  对应：A01,A02,A03,A04,A05,A08,A09,A10。

- [ ] **T05 — Root独占schema/facade作者**：迁移UnitFeeDecision/GenerationResultReceipt/ApplicationRecord/ExecutionAttemptGroup/LocalContinuationWork/StreamFeeRecord/PollReservation/PolicyStopEvent和registry。

  验证产物：unique/foreign scope/nullable原log/phase字段/constraints readiness；旧图eligibility不扩大。

  对应：A24,A25,A26,A27,A29,A33,A37。

- [ ] **T06 — L-A**：实现phase-aware intent和result+Unit fulfillment+Work同TX、live bind/new claim_continuation/lease renew及fee/application phase恢复。

  验证产物：review已写未扣与optimize已扣未写crash可真新claim续本地phase，0Provider/假Step/无submission证据。

  对应：A17,A18,A24,A26,A27,A29。

- [ ] **T07 — L-A + Root共享adapter**：实现first taskless stream stage/confirm/lookup、可选secondTask binding、single-use reserved/exposed/revoke及原debit refund/reconciliation。

  验证产物：skip/BYOK/0/noTask/unknowncommit/secondfailure/gatereject/repeat/restart全部N3子例，0重复finance/start。

  对应：A13,A14,A15,A27,A29。

- [ ] **T08 — L-A核心 + Root cancel facade**：实现所有selected HTTP/P2/P6取消/replay共用fresh User-lock premarker event-settlement adapter及ALL Workstop/revoke/aggregate同TX。

  验证产物：event-first settler暂停/反序startedcancel/zero/positiveunverified/unknowncommit/完整A28链真实消费者，任一失败全rollback无newmarker。

  对应：A24,A27,A28,A29,A33。

- [ ] **T09 — L-A核心 + L-C消费**：实现required HaoAI group/sole-active planner、threeoptional immutableSteps/knownfailed max2 transition、原budget和PollReservation orderedpolicy。

  验证产物：unknowncreate无POST；720/7200/5s/3s不重置、max1/outstanding crash/duplicate/closed/stale/retired/auth12/late/sticky子例。

  对应：A25,A26,A27,A28,A29,A32,A33。

- [ ] **T10 — Root唯一P5 facade作者**：实现sharedSession owned completedresult reader、generation_application PUT、source/media/notes/approval/AppRecord CAS和typed errors。

  验证产物：stableID rebase、strictnoID、duplicate、asset NULL-aware、authorized exactreplay0revision、failed wholeTX rollback，video422/无workercanvasapply。

  对应：A03,A17,A18,A21,A22,A24,A26,A38。

- [ ] **T11 — L-A**：独占text/frame目录实现stream协议/旧SSE与review/optimize contexts/limits/prices/inflight/partial/fees，用已冻结public ports。

  验证产物：OpenAI/DeepSeek/custom fixtures、600s/300s、generator error无done/断连、original_text/description、3..10/30/40限制。

  对应：A01,A08,A13,A14,A16,A17,A18,A19,A20,A30。

- [ ] **T12 — L-B**：独占image/adapters/media目录实现single/batch原子准入、ordered receipts、每imagefamilypayload/ref/async/sync/evidence及真实执行。

  验证产物：每固定familyfixture和DTO/limits/idempotency/commitunknown/batchfailure0行/receipt/fence完整检查。

  对应：A01,A03,A04,A05,A06,A07,A08,A09,A11,A12,A21,A24,A25,A26,A31。

- [ ] **T13 — L-B**：实现P4 publicmedia/boundcontent/localMuse/R2 typed能力、managedURL map、SDKoffloop和owned临时生命周期。

  验证产物：200MiB/64KiB/600s/concurrency2/publicDNS/ports/MIME/noredirect、fakeSDK标准3attempt、取消/失败cleanup与callerfile不删除。

  对应：A10,A26,A34,A35,A36。

- [ ] **T14 — L-C**：独占video/adapters/execution目录实现15固定familypayload/ref/res/time/ratio/wait/poll/content/receipt与P2group消费。

  验证产物：逐固定family异构fixtures含HTTP/JSON/terminal/authcontent/localMuse，不能统一/v1/videos；HaoAI完整子例。

  对应：A01,A03,A04,A05,A06,A09,A11,A24,A25,A26,A32,A33,A36。

- [ ] **T15 — Root唯一装配作者**：显式inert config/credential/fourcaps/provider registry/executor/runtime app/router装配五workertypes及graphless stream。

  验证产物：build无资源、publicimports/no legacy/env、lazy startup/readiness，实际路由POST六个且GET不扩。

  对应：A10,A23,A29,A37。

- [ ] **T16 — 三Luna作者各自独占**：在原专属测试目录执行有意义的fake port/protocol/transaction/finance/fence/unknowncommit/crash checks，按fixedsourcefixture逐项关联39。

  验证产物：原39/修复子例0遗漏，作者检查不当Root/browser/外审；依新失败必要追加，不重复无关历史测试。

  对应：A01–A36。

- [ ] **T17 — 独立GPT-6.1 Sol/xhigh**：分别复核实际headers/source/diffs/Rootsharedfacade，核真实公共consumer和P1/P2 findings。

  验证产物：source accepted有绑定SHA/实际窗口/未读边界，不能以作者声明或绿fake测试替代；外部A39仍独立pending。

  对应：A24,A25,A26,A27,A28,A29,A33,A37,A39。

- [ ] **T18 — Root**：执行完整适用39真实集成矩阵、PostgreSQL两workerclaim/锁等待/fence/重启/late结果/ledger/CAS和sharedSession实际接线。

  验证产物：source→作者→Root各层身份/SQL/请求/资源证明分开；full A28/A29消费，SQLite不冒PG并发，未选A14不标pass。

  对应：A01–A37。

- [ ] **T19 — Root编排复用三Luna**：识别真实React目录后分配互不重叠UI featurepaths；迁移六入口/DTO/原keylostresponse/任务面板/sourcebubble/费用/忙失败partial/自动apply。

  验证产物：shared router/client/globalstate仅Root单写，canvasapplied仅committedPUT之后；未新增approval/reconnect协议。

  对应：A14,A21,A22,A38。

- [ ] **T20 — Root浏览器验收**：真实浏览器走六入口、等待/失败/部分/恢复/断连、lostresponse原key/应用/确认失效和收费展示。

  验证产物：保存actualbrowser evidence，A38只实际完成子例记录；fake backend不能替代。

  对应：A38。

- [ ] **T21 — Root证据/指定外审**：保持源码/作者/Root/browser/指定GPT-5.6 Sol/xhigh外审/发布证据分层，外审当前OFFLINE PENDING，不probe/替换/伪过。

  验证产物：A39层次清楚，release授权与完成分开，无新human approval机制。

  对应：A39。

- [ ] **T22 — Root**：实际routeinventory和模块/receipt兼容验收后更新注册清单与对应OpenSpec完成文档。

  验证产物：以真实注册/接受才计数；本draft 97/248/151不扣，六candidate不自动宣称已注册。

  对应：A37,A39。

- [ ] **T23 — Root编排后续后台迁移**：继续所有剩余后台族的普通OpenSpec/公共模块/真实验收，直到完整后台迁移目标，而非将六族完成等同全局完成。

  验证产物：实际inventory证明余项处理，独立证据和发布边界保持；不承诺日期/整体比例。

  对应：全后台原目标。

- [ ] **T24 — Root编排React完整迁移**：后台之后继续React完整用户工作流、路由/状态/API接线/权限/失败恢复/视觉流程并做真实浏览器验证。

  验证产物：六入口以外也全部迁移有实际证据；不以新页面/fake响应称全前端完成。

  对应：React全流程原目标。

- [ ] **T25 — Root最后语言评估**：后台+React完整迁移后，根据实际运维/性能/维护证据评估语言方案与必要成本。

  验证产物：形成技术评估，未授权后端语言重写不执行重写，不用现草稿代替实际结果。

  对应：最后语言assessment原目标。

## 完整验收映射与保留界限

原39每项四字段/ports/owner/修复子例在spec，不删改分类或requiredCheck。A28包含queued未intent、startedretain、failure/deadline先成立、event-first settler迟、反序cancel-first、unknown/replay、mixedUnits、ALL Work、shutdown区别及全部HTTP/P2/P6消费者；signal或子集不算完成。A14公开reconnect/cursor未选，A38当前未执行，A39指定外审OFFLINE PENDING。

截至本次任务记录更新：公共契约与静态操作注册表提交 `b9f84ed`；图片、xAI 视频原始协议解析分别提交 `c77805f`、`662638f`；OpenAI/HaoAI 图片适配器提交 `e324070`；xAI 视频提交/查询适配器提交 `b7bb53c`；漫小白原始协议解析及防御冻结提交 `0fc43bc`；生成记录持久化、增量迁移与就绪检查提交 `644f588`；媒体本地参考暂存清理与托管地址规范化提交 `88a19a1`；漫小白提交/查询与取消清理边界提交 `c92e761`。这些提交均已推送，并有各自独立源码复核与 Root 本地检查。持久化批次 Root 56 项检查通过；受控 Unix socket PostgreSQL 的兼容迁移、幂等重跑和八类错误结构拦截共9个场景实际通过，临时进程与目录已清理；这不代表记录并发、完整业务或39项集成已通过。媒体本地组件 Root 40 项、漫小白适配器及协议 Root 54 项检查通过，当前源码复核均无未关闭 P1/P2。同事务结果公共桥接 B3 的12项新本地检查通过，但既有纯层边界单项实际失败；独立源码复核提出旧 ORM 授权缓存、已提交重放目标存在、冷媒体状态基线和纯端口依赖四项问题，正在修复，尚未接受或提交。公共媒体 URL/DNS 校验、HaoAI 视频纯协议新批次已正式分配并行开发，未验收。T03 的公共 headers 已冻结；T05、T10、T12、T13、T14 等只完成部分组件，保持未勾选。当前新六入口原39项真实集成尚未执行，通过数0，路由计数不扣减；React/browser及指定外审仍未通过。