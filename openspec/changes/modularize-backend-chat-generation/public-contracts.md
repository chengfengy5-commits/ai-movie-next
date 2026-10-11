# 公共合同 P1–P6

## 用途与权威

本文完整规范用于未来产品实现，设计已接受不等于实现/DB/HTTP/浏览器通过。内部字段不自动新增旧HTTP/SSE wire，client不能构造authority或改变fee/network policy。调用方须同时满足以下所有SHALL义务、design中R1–R4及原39场景；不得仅按方法名称实现。

## N1：准入、参数和五费用

系统 SHALL 用server GenerationActor/OperationPolicy：trusted account下receipt replay先于new-member gate，bad/revoked401、新generation会员403/superuser豁免；锁内owner/team/claimed/series/chapter/asset/model/prompt/category/index/serverpricing。RawAdmissionKey只原三media入口，保raw非blank1..255，同user/op/key/digest返回原receipt、异digest409，不给text加必需header。

系统 SHALL 保五费用原时序：single/batch按原phase stage图/messages/真实debit一次commit，batch全部item/quote/total balance通过前0行；review先实际apply提交再独立fee，optimize逐frame output→debit→apply，不整批reserve。退款核原owner/op/task/unit或streamattempt、usage负amount/key/id/relateddebit，唯一幂等且不超过debit；quote/cost/billing投影与旧兼容worker log不成为新proof。actual0不refund；reviewpositive不足永久unbilled0/waived_insufficient，definitive安全失败仅证明无debit才waived_charge_failed，unknownpending；optimize charged未apply/冲突/删除retain、不可伪造Provider failure。

snapshot SHALL 冻结modelsource/id/provider/endpoint/全部params/prompt/context/quote/refs/target/grant/version，只credential live原row/owner/provider/origin/principal有效及可证明同principal rotation。无first-active fallback/secretpersist/凭row或decrypt猜principal；create前配置失效不POST按operation真实结算，原idquery失效pending和独立due。应用仍检查current mutation access。

## N2：原子Work、phase claim、恢复和期限

本节所有条款 SHALL 执行，以下是accepted design04完整原义务，未将其缩减为标识：

### 适用范围

Only new registered frame.review/frame.optimize graph versions require local_continuation_v1. LocalContinuationWork is a real new schema/public planner branch, not a Provider Step/Submission/evidence. Old registry graph/no-graph eligibility unchanged.

### 持久记录

Unique(task_id,unit_id,policy_version); owner/operation/selected result_ref/content digest/DB generation_fulfilled_at; immutable original first-intent/deadline refs; frozen phase order; phase/phase_version; fee_decision_ref/application_ref; terminal_reason and last execution fence. Each declared new text Unit has this mandatory completion requirement.

### 同提交产生可恢复工作

persist_fulfillment_and_continuation locks User→Task→all Units(stable item/id)→Steps→Submissions→Group/result→Work. In ONE commit persist trusted selected recoverable GenerationResultReceipt, truthful Unit generation fulfillment and initial LocalContinuationWork readiness. Task cannot finalize before required local work. Commit unknown looks up original receipt/work unique links; never Provider POST. No result-commit/work-later crash window.

### 阶段顺序

**frame.review**：- delivery_pending
- fee_pending
- finalize_pending
- done

**frame.optimize**：- fee_pending
- delivery_pending
- finalize_pending
- done

### 新claim候选与确认

P2 claim_continuation is a separate new-registry candidate branch/adapter, not unchanged task_claims.claim_next. Exact complete registered graph+selected recoverable succeeded Unit+unfinished permitted Work qualifies even with zero old ready_step_ids. Under User→Task→Unit/result→Work recheck owner/operation/nonterminal parent/phase/stop-cancel policy and no other valid Task lease. Fresh random token, execution_generation+1, DB-clock lease and Task processing commit before authority return. Expired/revoked old claim fenced off; live generation claim cannot be stolen.

### 当前live claim绑定

A live generation worker may bind_current_continuation(tx,current ClaimAuthority,work) to a phase-limited ContinuationClaimAuthority sharing current Task token/generation/lease. Thus parallel frame generation and local delivery can use one live parent lease. After crash only claim_continuation provides a new claim. Binding never renews original generation deadline or produces a CommittedIntent.

### 权限

ContinuationClaimAuthority binds exact owner/task/unit/work/result/phase_version, current Task token/worker/generation/DB lease and purpose local_continuation. P2 fee/P5 target/normal P6 task-message finalization require current processing fence and phase; after target lock recheck DB time. It is rejected by prepare_policy_intent, every Provider create and submission-key producer. Plain OwnerTaskScope never charges/applies worker continuation; source admission/owner refund/cancel retain separate declared authority.

### 阶段原子提交

Real application+ApplicationRecord+next Work phase commit once with source/media/notes. Actual original debit/UnitFeeDecision or definitive waiver+next phase commit once. Stable original fee/application identities never depend on claim generation. Unknown commit looks up original work/ledger/decision/application before any action; no blind charge/rewrite. Exact committed apply replay precedes old target snapshot CAS while current owner/fence is still validated.

### review崩溃恢复

Output/Work→delivery commit includes fee_pending; crash before fee resumes fee only via new claim, never re-apply/re-generate. Confirmed insufficient retains written result, positive Unit unbilled0 and immutable waived_insufficient. Proven absent debit with definite safe failure may waived_charge_failed; uncertain commit remains pending. Waiver never later charges.

### optimize崩溃恢复

Debit+UnitFeeDecision commit includes delivery_pending; crash before apply resumes same saved result/original actual fee via new claim. Fenced target CAS writes once. Target conflict/deletion is terminal delivery failure with charge retained; not Provider failure or refund proof. Transient TX failure remains pending; unknown commit checks ApplicationRecord first.

### 余额停止

stopped_insufficient is immutable per-Unit decision plus Task stop-new-work reason under Task lock. Stop all new charge/generation, no restart surcharge. Already paid frames may drain allowed pending delivery unless cancelled/conflicted; real applications, completion-order list and paid-cost projection determine optimize partial completion. This deterministic recovery drain is explicitly a future technical choice, not asserted old deployment behavior.

### 取消

Selected new-registry owner cancellation MUST first execute #/policyStopBeforeCancelContract discovery and unique event/debit settlement before ANY Task/Unit cancellation marker; all preflight/refund/event disposition+original lifecycle_v1 cancel+ALL Work stop+lease revoke+aggregate share ONE caller TX. After that ordering, no charge/apply after any cancel marker. Unchanged local phase rules:  Review applied-before-fee→immutable waived_cancelled/unbilled0, data retained. Optimize charged-before-apply→delivery stopped_cancelled, actual debit retained. Original refund/waiver/stop never resets; no source rollback. Worker late responses remain original unknown until controlled validation.

### 期限

Never change original first intent+7200s or truthful DB generation_fulfilled_at. Timely means recoverable validation strictly before original deadline; timely local fee/delivery/finalize can resume after7200 with new local lease and settle_due sees already_settled generation, no timeout refund/new POST. No new local business deadline invented. Late new text fulfillment remains truthful receipt with verified source settlement, Work stops_late_generation, no new fee/apply. DB/lease failure remains reconciling, never false completion.

### 完成投影

finalize_pending is real claimable local work, P6 finalize_continuation requires current Task/phase fence and does no Provider. Task terminal only after generation requirements resolved and every mandatory Work has terminal fee/delivery/finalization disposition. review requires real application+terminal fee; optimize≥1 real apply completed,0 failed, actual cost/order preserved. Unit succeeded alone insufficient; terminal parent never normally reclaimed. Cancel/policy-stop uses separately typed control settlement finalization after revoke/quiescence, allowed only cancellation/aggregate Task projection, never charge/apply/POST. No new wire cancelled status.

### 实际恢复消费者

RecoveryPlan adds resume_fee/resume_delivery/finalize with original work/phase/result refs. P6 actually acquires/binds phase claim, renews/rechecks and consumes them; owner scope alone cannot do worker writes. Ready Work routes queued on release despite empty generic ready_step_ids; unknown commit reconciling; done/waiver/stop never reopens charge/apply. Shutdown revokes/releases and preserves original work; not user cancellation/refund.

### 禁止伪造

- No repeated Provider POST
- No fake generation ready/applied/deterministic_empty step
- No fake no-submission evidence
- No old graph claim eligibility extension
- No provider failure invented from delivery failure
- No credit_cost substitute for real ledger

## N3：taskless stream原debit proof和一次性start

本节所有条款 SHALL 执行，以下是accepted design04完整原义务，未将其缩减为标识：

### 适用范围

text.stream remains Web/gate/queue, optional AITask, graph_version None. Private fee/start proof records solve D2 financial and one-shot-start evidence only, not durable worker execution/reconnect/restart replay.

### 权限

StreamFeeAuthority is server trusted actor+UUID attempt+frozen registry/model/quote/disposition. No new client key/header/SSE field; client skip/cost never actual debit proof.

### 持久记录

StreamFeeRecord unique(server_attempt_id): owner/operation=text.stream/frozen quote and model provenance/disposition debit|skipped|byok|zero, actual_debited_amount, original_debit_log_id/business_key (nullable only amount0), fee settlement/start state/token, optional message/task binding. Original owner/debit/amount immutable. StreamDebitReceipt commit_state staged|committed|unknown is trusted DTO confirmation state, not in-TX claim that commit succeeded.

### 第一事务

P1 stage_stream_debit(tx,StreamFeeAuthority,frozen_quote) keeps old model/owner/active/membership/balance checks. Debit only skip=false system positive quote; conditional wallet decrement, actual usage CreditLog key stream:debit:{server_attempt_id} and exact StreamFeeRecord linkage commit TOGETHER first TX, no Task/Unit. Skip positive still balance-only; BYOK/zero no usage/wallet mutation. All get private disposition/start bookkeeping row; staged DTO cannot call Provider.

### 确认与未知提交

After known commit, confirm_stream_commit verifies original persisted row/log, returns committed proof. Commit exception→unknown; lookup_stream_debit(authority,original_attempt) new short TX verifies exact owner/op/quote/debit links. Found returns original committed proof. Proven resolved rollback/absence aborts this request without automatic re-debit. Still uncertain means pending/old safe failure, no Provider start/refund. Mere not-found without resolved commit is not proof of rollback.

### 可选第二Task事务

No message_id means no Task/fake Unit. With message, second TX creates old processing AITask and bind_stream_task links original attempt atomically; owner/op/message exact, no mutation of original debit/amount. Originally NULL debit task/unit columns stay NULL; private record holds optional binding. Unknown second commit reads original binding/task before start; exact repeat same task only. Task credit_cost quote never refund proof.

### 一次性start

acquire_stream_start requires confirmed original receipt/disposition and optional confirmed Task binding; commit ready_to_start→start_reserved private token. Before HTTP expose_stream_start validates/consumes token once to start_exposed; only NEW confirmed committed transition returns an in-process single-use WebStartCapability. Provider after DB release. Repeated/existing/unknown transition yields no new capability; restart never relaunches. Gate rejection while still reserved atomically revokes token so callback cannot expose. Only persisted pre-exposure revocation proves known_not_started; missing Task/status or ambiguous spawn/exposed state cannot.

### 尚未暴露的失败

Known second Task TX failure, request exit before expose, or gate failure with durable pre-exposure permit revocation records controlled StreamSettlementProof known_not_started; refund original positive debit once, Task absent allowed. Unknown second commit first resolves original binding; if unresolved only row-lock-confirmed pre-exposure abort can prove no Provider exposure and refund. DB/commit ambiguity without that proof remains pending. This is explicit future D2 risk repair.

### 已开始失败

Source provider error/empty final response/controlled Web cancel yields StreamSettlementProof source_policy_failure bound to original attempt/exposed execution, safe observation and frozen rule; refund original actual debit once. Successful stream retains debit. SSE disconnect alone not cancel/failure. Unknown lost process/start without trustworthy terminal proof stays settlement_pending, no generic7200 graph deadline invented.

### 原资金退款事务

All stream writers serialize on User. Refund order User→wallet→original debit/refund logs(stable business keys)→StreamFeeRecord→optional bound Task projection, one TX. Check log.user_id/type usage/negative amount/exact stream:debit key/id/actual amount and nullable original task-unit scope plus eligible proof. Unique stream:refund:{attempt}, type refund, related_debit_id exact original, amount=actual positive debit, total<=debit. Exact repeat verifies original links, returns already_refunded0 new wallet/log changes. Unique refund/original proof prevents duplication; conflict rolls back.

### 实际零费用

skipped/BYOK/zero amount0 never debit/refund even positive quote/Task credit_cost. Never fake Task/Unit for refund_unit, never use ordinary legacy usage log as this new proof.

### 未知提交回读

Debit/refund/binding/start unknown commit first lookup original attempt/log/related refund/phase. No blind recredit/start. First real debit remains independent of Task absence/failure. P6 reconciles fee cleanup only, cannot reconstruct/replay provider calls/SSE cursors or bodies.

### 接线

Explicit P1 producer/confirm/bind + P2 lookup/refund/start + P3 single-use start + P6 bridge/reconciliation with caller-owned TX and owned same-Session optional task/message projections. Do not import/reuse old _resolve_model/_update_ai_task_safe cost-only financial side effects.

## N4：原子poll reservation与有序streak

本节所有条款 SHALL 执行，以下是accepted design04完整原义务，未将其缩减为标识：

### 固定原预算

HaoAI source max720 polls×5s, max2 known ref-failure retry delay3s, first7200s fixed for all attempts.

### 锁内预占

reserve_active_poll(tx,ClaimAuthority|ControlledQueryAuthority,group) short TX locks User→Task→Units→Steps→Submissions→Group→reservation; exact frozen graph/sole active allowed original known id/binding/no cancel-normal policy stop, poll_used<720 and DB now<original deadline. Max1 outstanding reservation. Increment shared poll_used and monotonic sequence, persist attempt/submission/id/digest/token/DB timeout/deadline/next_poll_at atomically. Only confirmed commit returns PollReservation. Existing unknown no-id intent never creates query id/next POST permission.

### 锁外网络

P3 HaoAI query requires confirmed reservation and exact original credential/id. GET outside TX after DB release. DB next_poll_at enforces old5s; accepted retry not-before also preserves3s. No secret persistence or fallback.

### 丢失预占

Reserved crash/failed query permanently consumes one poll. Controlled recovery closes expired/revoked reservation unknown after persisted query timeout, then can reserve next if permitted. Old closed response append-only original unknown; cannot consume another poll or retroactively update streak.

### 有序一次接受

accept_poll_observation first stores raw original unknown/dedupe binding; locked policy acceptance/close once per increasing reservation sequence, max1 outstanding explicit ordering. Exact repeat duplicate0 count/streak; same key different payload conflict. Lost claim raw remains unknown until controlled source/links validation; only accepted controlled ordered policy update can alter counters. Non200 never fabricated terminal failure evidence.

### 精确streak规则

Accepted matching non200 four source auth markers increments streak; accepted nonauth non200/HTTP200 resets0. Source transport exception keeps streak; conservative unknown close likewise neither increment nor reset. Matching accepted HTTP observations since last known reset is precise, not all reserved queries returned auth. Stale/duplicate/retired no streak updates. Accepted new original active id resets only streak, never poll_used/deadline.

### 唯一active attempt

Only active allowed generate.attemptN create-ready; future optional prepared members dormant. Group lock/current claim atomically advances sole intent/active pointer. Next only exact active known HTTP200 failed+three ref markers, remaining max2, before original deadline, unchanged payload/refs, no cancel/policy-stop/prior next intent. Step/submission uniqueness and original evidence retained.

## N5：sticky auth policy和late真结果

本节所有条款 SHALL 执行，以下是accepted design04完整原义务，未将其缩减为标识：

### 政策事件

PolicyStopEvent unique(group,policy_version,reason auth_unavailable_12), ordered qualifying reservation/observation refs, original active step/submission/known id and DB stopped_at. Original12 non200 observations remain unknown/nonterminal; no fabricated HTTP200 failed or Provider ResultEvidence. Establishment and later financial disposition are distinct: producer acquires shared User/Task/Units/Steps/Submissions/Group/Event locks;event financial_disposition starts pending, references only original bound debit/registry.

### 停止转换

Controlled stop_group_by_policy commits policy_stopped with sticky event, freezes dormant attempts/revokes new create and normal poll, records operation-policy terminal disposition. Unit Provider truth stays unknown until real valid output. Task old failed/auth error only after settlement/quiescence; no new wire status and no retry-create. Event producer uses #/policyStopBeforeCancelContract/linearization and observes prior committed started-cancel facts without clearing them;unknown Provider truth remains unchanged.

### 结算

Normative #/policyStopBeforeCancelContract. Independent settle_policy_stop shares locked original event/debit validator and source refund helper;event-first cancellation itself MUST settle before ANY marker even when standalone settler paused/crashed. Cancel-first started retains valid original marker. Original financial disposition+unique related debit are immutable/replayed; actual0 no logs,positive unresolved proof pending/conflict;unknown commits original event/ledger/marker lookup. No fabricated Provider failed truth.

### 晚真实结果

Genuine recoverable late response for same STILL-active original intent may controlled-validate, group succeeded_late and Unit truthful succeeded/succeeded_refunded by actual time/ledger; sticky policy event/refund/retain never undone/recharged. Policy-failed Task stays terminal under chosen old stopping behavior, no automatic target apply/restart/POST. Result owner-readable as late generation evidence. Retired attempt cannot select. Normal polling stays stopped; any explicitly controlled existing-id verification if enabled must reserve same remaining720 and original deadline, no new budget/public route.

### 预算耗尽

720 poll exhausted or7200 due with unknown Provider truth is pending verification+verified due settlement, not auth policy failure or forged Provider failure.

### 取消顺序

Mandatory operation-aware adapter and public/P6 routing: #/policyStopBeforeCancelContract; independent event/settlement TX remain allowed because selected cancel preflight closes their window.

## N6：ANY取消marker前的原event金融决定

本节所有条款 SHALL 执行，以下是accepted design04完整原义务，未将其缩减为标识：

### 适用范围

Only selected new registered graph policy; preserve independent PolicyStopEvent TX and separate settler. Fix cancellation consumer ordering, no rewrite of closed continuation/stream/poll/R2/R3 contracts. Old supported graphs without new policy event and old no-graph preserve original source behavior.

### 线性化

Both stop_group_by_policy event producer and every selected cancellation/settlement adapter serialize on the SAME owner User lock and declared Task/Units/Steps/Submissions/Group/Event order. Established means an actual committed bound event observed after acquiring User, not wall-clock timestamp/HTTP arrival/worker scheduling. Cancel must perform fresh locking/current read AFTER waiting on User, without stale cached event rows/prelock snapshot. Root adapter uses a new short transaction with current committed visibility (READ COMMITTED); if an earlier snapshot cannot prove that visibility, restart before any marker. No hidden earlier status-only writer.

### 统一锁序

User→Task→all Units(stable item/id)→Steps(stable order/id)→Submissions→Groups(stable group/id)→PolicyStopEvents→result/Work as relevant; then wallet/original debit/refund logs. All owner cancel, policy producer/settler/P6 paths share this order and Session. Network/worker waits outside locks; never open a helper-owned commit/UoW.

### fresh发现

Before touching any Task.status=cancelling/user_cancelled_at/cancellation_reason or Unit cancel marker, load and validate ALL already-committed applicable original PolicyStopEvents, registry/version/owner/task/unit/group/active-at-establishment step+submission+known id, ordered unknown provenance and original debit scope. Late original active truthful receipt does not erase sticky established policy eligibility. No invented Provider failed result_status/evidence, no retired or foreign event accepted.

### marker前结算

Mandatory P2 settle_committed_policy_before_cancel in that SAME caller TX settles every applicable event with source refund_unit(RefundReason.FAILURE), exact verified original debit:unit/owner/task/unit/usage/amount/related debit and original unique refund key. Stage/flush original Unit/ledger updates and event financial disposition BEFORE original cancel_task can stage ANY Task/Unit marker. Existing verified refund is replay only;actual zero amount is no_debit/0 logs. No positive unverified financial proof is called no_charge: unresolved/inconsistent proof returns pending/conflict and rolls back this cancellation TX, no new marker;controlled original lookup/recovery retries, no fake money.

### 单一caller提交

- Acquire fresh User lock and locked new-registry scope
- Discover/validate original committed policy events
- Stage unique refund/replay/true-zero and each event original-debit financial disposition;flush safely before refreshing source helper
- Run original lifecycle_v1 cancel_task in SAME Session only after policy preflight; source failure/due/no-intent/started/timely generation rules otherwise unchanged
- Stop ALL LocalContinuationWorks including succeeded-Unit skipped by old helper; preserve applied/charged/waiver and stop pending fee/delivery under original rules
- Revoke Task lease/token/phase authority and derive cancellation aggregate from committed generation/application/fee/control facts;no remote-wait under locks,late responses remain controlled unknown
- Caller commits refund/event disposition+original cancel markers+Work stop+fence revoke+aggregate ONCE;any failure rolls back all new effects

### event先成立

Original event committed while Unit charged/started/unknown/not-due;settler delayed/crashed. Cancel acquires User and sees event, refunds original once and marks event disposition before original Task/Unit markers. Original cancel sees refunded Unit and source-skips re-refund. Later settler/repeated cancel checks original event+refund and adds0 ledger/wallet changes. Thus financial result follows original event, not settler scheduling.

### started取消先成立

If an authenticated started Unit cancel marker was already committed before event establishment, never clear/overwrite that valid marker to force refund. New late policy event/settler records retained_by_started_cancel with original marker/debit link;source refund_unit retention stands,0 new refund. Existing independent earlier refund remains immutable replay. Before-intent cancellation and old failure/due priorities remain source rules, not generalized started retention.

### 原commit未知

Unknown event/settlement/cancellation caller commit uses a new owner-scoped original lookup of event financial disposition, original debit/refund key+related id and Task/Unit markers/Work stop/revoked fence/aggregate. Found valid refund returns replay;committed complete cancellation returns original result;rolled-back or unresolved remains retry/pending on original facts. Never infer financial commit from cancel signal/cost/not-found alone, never blindly refund or erase marker. Mixed impossible partial facts are invariant conflict, not financial repair by fabricated evidence.

### 财务终态与replay

PolicyStopFinancialDisposition belongs to original event+unit+debit. pending→refunded(original refund id/amount) OR retained_by_started_cancel(original committed marker) OR no_debit(actual0);once terminal immutable. Different events sharing original Unit debit may reference the SAME already-verified refund, not mint more refunds. A cancel marker in compliant selected path cannot newly commit while an already-observed prior applicable event remains financially pending. Foreign/mismatched bindings cannot authorize money.

### 所有公开消费者

All selected graph owner HTTP cancel facade, P2 signal_cancel/cancel_and_settle, P6 consume_cancel_and_due cancellation branch and cancellation recovery/replay MUST invoke this operation-aware adapter. Preserve old outward task_id/status:cancelling/local signal, but no selected graph direct request_cancel status-only write or unchanged cancel_task before hook. Current existing status-only endpoint is not claimed upgraded. Legacy unsupported graph/no-graph route remains source compatibility/quarantine;graphless stream fee rules untouched.

### 控制聚合

No charge/apply/POST permission is created. All Work stop/waivers/revocation and eligible aggregate projection are staged in same caller TX as premarker financial decision and original cancel. Control finalization observes that settlement invariant;incomplete control facts stay pending through existing policy rather than falsely complete. Task statuses/wire unchanged,source remote-cancel/shutdown distinction unchanged.

## N7：provider attempts、应用与资源能力

系统 SHALL 保固定Step8状态、unique(unit,step_key)/Submission unique(step)/global key，真实ResultEvidence除deterministic_empty外必须submission绑定。五claimed-worker每次create含frame非流POST只确认CommittedIntent，staged/unknown不POST，网络在DB锁外；合法迟claim只记原safe unknown，controlled可信来源/linkedrecoverable验证，同key同payload重复、异payload冲突，fallback/rawsuccess无应用权限。所有Task/message/target/externalid/wallet写current token/generation/owner/DBlease，target-lock等待后再核。

HaoAI SHALL 新registry三个optional immutable generate.attempt0/1/2+一个required group，ordinary required Steps与group共同验证。只sole active allowed ready、futureprepared dormant。已知exact active id HTTP200 failed匹配image_download / failed to download the provided image / downloading the image且剩余max2、未到原deadline、no cancel/policy、无nextintent和samepayload/refs才advance不同未提交attempt；一Unit/一debit。unknowncreate/no-id/timeout/5xx/disconnect不原或nextPOST，只proven-supported lookup否则pending/due；retired不能select或写target/finance。

P5 SHALL existingPUT显式generation_application、sameSession owned completedTask/canonical selectedresult/owner/op/category/series/chapter/target/currentmutation绑定，拒替代URL/unrelatededit。stable uniqueID/fulltargetdigest保无关edit/reorderrebase，current lockedcontent wholeSQL CAS；genuine无ID整章snapshot+index任何edit失效，duplicateID拒不fallback。asset same-series/id/fulltargetdigest/NULL-aware expectedimage。source/description/mediarevision/撤approved/notesCAS/ApplicationRecord/nextWork同commit同rollback，authorized exact appliedreplay先旧CAS，0generation/fee/revision，unknownmutation先查原applied。Canvasclient首成功自动PUT，worker不canvasautoapply，image无message/video422，默认不改layout；如以后node则同TXatomic version/node CAS，optional前检非atomic。R2完整技术约束见design R2，所有public facades不得自开第二UoW装原子。

P3/P4 SHALL 分别显式注入publicmedia/boundprovidercontent/localMuse/R2。publicDNS/HTTP(S)80,443/no redirects/private/activeMIME/200MiB/64KiB/600s/concurrency2/cleanup，managed/explicitlegacy map保querybytes。content仅原id/origin/path/credential/no userauth/no cross-origin/no redirect，private仅per-binding networkcap；Muse原endpoint/local/批准upload_dir confinement/typedinputs/refcaps/audio/absolutepaths，清ownedstage非caller文件；R2safeconfig/secrethandles/optionalSDK/offloop/standard总3attempt/date-business-UUID/MIME/immutablecache。失败resultunverified/pending，fallback不冒可信success。R3全约束见design R3，未查部署env/secret/bucket。

P6 SHALL inert explicitfactory无构造HTTP/session/crypto/storage资源、无legacy app/runtime/env discovery/cross-privateimport，真正consume新generation/Work/recovery/cancel/due/streamfee，heartbeat/lost停写，shutdownrelease/revoke保存Work不同usercancel。Root唯一装配registry/app/config/schema/facade/frontend；SQLite只单worker，PostgreSQL并发/readiness另实证。stream旧start type-only/content/error/done、reasoning默认不透传、prompt_id不apply，generator异常可error无done、disconnect不cancel；公开reconnect/重启生成未选。

## 公共类型

以下字段、不变量精确携带；server-only staged/committed/unknown不能由client宣称，新schema仍待实现。

### GenerationActor

字段：`user_id: str`；`trusted_account_identity: existing public TrustedActor`；`membership/new_admission_permission: separately resolved at new-submission stage`。

不变量：服务器产生；receipt replay不能统一提前使用active-member resolver。

### OperationPolicy

字段：`operation: str`；`task_type: str|None`；`graph_version: str|None`；`execution_mode: web_stream|claimed_worker`；`charge_phase: stream_pre_task|single_admission|batch_admission|post_apply|pre_apply`；`cancellation_policy_version: legacy_signal_only|lifecycle_v1_for_new_registered_graphs`；`local_continuation_policy None|local_continuation_v1`；`query_budget_policy None|haoai_poll_reservation_v1`；`stream_fee_policy None|stream_debit_receipt_v1`。

不变量：纯服务器 registry；参数不让客户端切换费用/worker/权限。新增 review/optimize operation 名和 graph version 是未来内部约定，不新增公开POST。 New text phase-aware intent is explicit; old generic prepare always precharges and is not directly used.

### RawAdmissionKey

字段：`raw: str, nonblank length1..255`。

不变量：保留实际原字符串，不trim替换；仅image.single/video.single/image.batch现有header。review/optimize/stream不默默新增必需key。

### AdmissionFingerprint

字段：`schema_version: str`；`ordered_nonsecret_payload_digest: str`；`actor_id: str`；`operation: str`。

不变量：有序资产/refs/target与完整有效请求参与；同user/op/rawkey同digest回原receipt，异digest409。

### FrozenExecutionSnapshot

字段：`model_ref: owner/provider/model identity`；`model_category/name/params: safe immutable values`；`prompt_ref and effective prompt/context: nonsecret fixed text`；`server_quote: nonnegative int`；`reference_snapshot_ref: encrypted or typed frozen refs`；`target_snapshot: TargetSnapshot`；`credential_binding_ref: nonsecret owner/provider/account reference`；`FrozenGrantSnapshot admission actor/series/team/access provenance`；`safe adapter/config versions + original endpoint policy`。

不变量：未来D4默认冻结非秘密执行参数；credentials不写快照/log，运行时按绑定解析；不能将params遗漏或任意当前行fallback当等价。 Frozen worker execution grant does not replace current user mutation access or original credential validity.

### TargetSnapshot

字段：`kind: none|stream_message|frame_message|asset_message|canvas_frame_preview|canvas_asset_image|frame_text`；`chapter_id/series_id and stable_storyboard_asset_id or asset_type/id`；`compat_frame_index: int|None`；`expected_text_digest/media_identity/content_digest/notes_revision as applicable`；`unique stable-ID full frame digest, or absent-ID full chapter canonical digest+index`；`expected asset image NULL-aware value + same-series target facts`。

不变量：R2 server-derived locked target snapshot; stable-ID rebase preserves unrelated current content, no-ID any chapter edit conflicts, duplicate ID never fallback.

### SubmissionGraphDraft

字段：`submission_id/internal_key: server ids`；`operation/graph_version`；`tasks: ordered TaskDraft[]`；`units: BillingUnitDraft[]`；`steps: ExecutionStepDraft[]`；`messages: MessageDraft[]`；`snapshots and optional precharge plans`。

不变量：review一frame unit；optimize逐frame unit且无全额预扣/总余额reserve；single exact-one；batch image逐item图一次commit。

### SingleAdmissionReceipt

字段：`submission_id: str`；`operation: image.single|video.single`；`task_id: str`；`message_id: str|None`；`status: LegacyTaskStatus`；`quoted_amount: int`；`billing_status: BillingStatus`。

不变量：字段/0/empty/null与旧wire保持；canvas message_id=None；quotedamount取unit，不取投影cost。

### BatchImageReceipt

字段：`tasks: ordered {type,asset_id,asset_name,message_id,task_id}[]`。

不变量：与single回执不同；不扩exact-one submission receipt builder到batch。

### FrameQueuedReceipt

字段：`review: task_id/model_name/is_system/credit_cost quote`；`optimize: task_id/chapter_id/frame_count`。

不变量：新图为内部，旧receipt保持；actual初始AITask cost0，不能把quote视为已扣。

### ClaimAuthority

字段：`owner scope(user_id/task_id/operation)`；`worker_instance/token`；`execution_generation: int`；`lease_until: DB-clock UTC datetime`；`local_lost_signal`；`purpose generation; local phase requires bound ContinuationClaimAuthority`。

不变量：仅5 claimed worker types；所有worker task/message/target/wallet/externalID变更都重核current processing fence与DB lease，等chapter锁后再次核。

### WebStreamAuthority

字段：`trusted actor+server attempt/StreamFeeAuthority`；`confirmed StreamDebitReceipt/disposition mandatory; debit refNone only actual0`；`optional confirmed Task/message binding`；`in-process single-use WebStartCapability + cancel token`。

不变量：Graphless Web process authority, no fake worker lease; first actual debit proof confirmed before one-shot start. Private fee/start markers add no public SSE/client key/restart execution.

### OwnerTaskScope

字段：`trusted actor or controlled recovery authority`；`user_id/task_id/operation`。

不变量：cancel/deadline/recovery控制不需要有效worker lease；依然验证owner及完整task/unit scope。

### CommittedIntent

字段：`step_id/attempt_id/external_submission_id`；`provider/model binding`；`nonsecret request digest/snapshot`；`committed_at/first_intent_at/deadline_at`；`optional proven provider idempotency key`。

不变量：只能由事务commit成功后构造；P3所有claimed-worker create（含非流frame POST）要求此能力；D6旧Web stream默认无graph、属于明确durability限界，staged Intent不能调用Provider。7200秒deadline首次intent固定，重试不重置。

### ExternalOperationRef

字段：`intent/attempt/provider binding`；`external_id: str|None`；`create_outcome: submitted|sync_result|unknown|definitive_failure`；`last safe provider state`。

不变量：AITask外部ID仅兼容投影，不能是唯一durable evidence；未知create无ID不得自动重POST。

### ProviderObservation

字段：`owner/intent/attempt/external ref`；`dedupe_key/payload_digest/received_at`；`typed safe status/media/result/error fields`；`transport provenance/recovery source`。

不变量：合法旧worker迟响应可记unknown，完全重复接受；同key不同payload冲突；provider status字段本身不赋予可信应用权。

### ValidatedGenerationResult

字段：`persisted observation/result_ref`；`validation_version and recoverable content/media locator`；`timely-success confirmation`；`selected attempt and safe output`。

不变量：只由受控validator/P2生成；原URL fallback/不完整download不能直接当可信success；输出应用和结果可恢复性分别记录。

### CanvasApplyAuthority

字段：`trusted actor owning allowed target`；`owned completed task and selected GenerationResultReceipt (mandatory)`；`frozen expected target/content/media state`；`application id/result selection`。

不变量：Completed user mutation authority is distinct from worker claim. R2 selected same-TX owned-result/target/CAS mode; implementation still pending.

### ApplicationOutcome

字段：`kind: applied|already_applied|stale|target_deleted|target_conflict|write_failed`；`application/result ref`；`target revision evidence`；`no_charge/debit evidence handled separately by billing policy`。

不变量：结果仍可任务查看；canvas首个成功选择与应用完成状态分离；apply失败可重试同结果，不新增生成或退款。

### BillingOutcome

字段：`kind: charged|already_charged|no_charge|insufficient|refunded|already_refunded|retained_by_cancellation|no_verified_debit`；`unit/attempt/actor/operation`；`related_debit_ref and exact amount`。

不变量：钱包/ledger真证据，退款不超过原debit；credit_cost/billing_status仅展示投影；0-price不能伪造已退款。 报价>0的review不足免费结果为unbilled+内部waived_insufficient终态，不能伪作quote0 no_charge，也不得恢复后补扣。

### RecoveryPlan

字段：`kind submit_prepared|query_existing|verify_unknown|settle_due|resume_fee|resume_delivery|finalize|reconcile_stream_fee|keep_pending|incomplete_graph|noop`；`typed original step/submission/owner scope OR local Work/phase/result refs OR separate optional-Task stream fee authority`；`frozen graph/continuation/fee policy version`。

不变量：RecoveryPlan adds resume_fee/resume_delivery/finalize with original work/phase/result refs. P6 actually acquires/binds phase claim, renews/rechecks and consumes them; owner scope alone cannot do worker writes. Ready Work routes queued on release despite empty generic ready_step_ids; unknown commit reconciling; done/waiver/stop never reopens charge/apply. Shutdown revokes/releases and preserves original work; not user cancellation/refund. Graphless stream fee reconciliation never restarts Provider; old generation no-graph remains quarantined.

### ReferenceInput

字段：`kind: managed_url|public_http|data_uri|bytes|local_muse_path|provider_content`；`safe locator/bytes and category metadata`；`trusted provider operation reference for authenticated content`。

不变量：public guard与授权provider/content/local策略分开；不能以用户任意URL配置带凭证内容请求。

### GenerationRuntimeConfig

字段：`SessionFactory/DB clock/ID factory`；`explicit HTTP factory and credential resolver`；`typed provider registry/executors`；`MediaStore and local Muse staging config`；`timeouts/concurrency/shutdown settings`；`separate PublicMediaFetcher/BoundProviderContentFetcher/LocalMuseCapability/R2StorageCapability`。

不变量：惰性显式注入、不import旧app/config/env discovery；SQLite只单worker，生产PostgreSQL/readiness/依赖由root接线。 Original-bound resolver is separate from current provider_status.prepare(provider).

### UnitFeeDecision

字段：`unit_id unique`；`charge_phase`；`resolution pending|charged|no_charge|waived_insufficient|waived_charge_failed|waived_cancelled|waived_delivery_failed|stopped_insufficient|stopped_late_generation`；`ledger_ref/result_ref/application_ref/resolved_at`。

不变量：Positive waiver stays unbilled and never recharges; zero no_charge only; decision and actual fee atomically durable.

### GenerationResultReceipt

字段：`result_ref unique`；`owner/task/unit/operation`；`original step/submission/evidence/selected attempt links`；`recoverable safe locator+content digest`；`validation version/validated_at`；`generation_fulfilled_at trusted DB validation time`；`late/policy-stop disposition and automatic_application_allowed frozen policy`。

不变量：Usable generated output proves Unit fulfillment; separate application proves target delivery; no arbitrary client URL or provider raw success.

### ExecutionAttemptGroup

字段：`unique(unit_id,group_key)`；`frozen three attempt_step_ids/registry version/original payload digest`；`max_retries2/active_attempt/selected_evidence_id/status`；`poll_used/auth_unavailable_streak/terminal_reason`；`poll_sequence/next_poll_at/outstanding_reservation_ref`；`sticky policy_stop_event_ref/active eligibility version`。

不变量：Required server group; original evidence remains bound to its actual step/submission; cannot fabricate internal no-submission result or mutate frozen required flags. Only sole active allowed optional member create-ready; policy_stopped/succeeded_late distinguish generation truth from selected stop policy.

### GenerationApplicationCommand

字段：`task_id/result_ref`；`expected_target frozen binding/digest`；`optional explicit application key fixed to result/target`。

不变量：Existing PUT explicit mode; owned completed task+selected result; no unrelated edits/client-supplied replacement URL; exact replay zero generation.

### ApplicationRecord

字段：`unique(result_ref,target_scope)`；`owner/task/unit/target/result digest`；`delivery_status pending|applied|target_conflict|target_deleted|failed`；`before/after target/media/notes facts and committed_at`。

不变量：Committed applied record returned before old expected-target comparison on exact authorized replay; transaction includes source/media/notes mutations.

### CredentialBinding

字段：`source,row_id,owner_or_system,provider,original principal binding`；`allowed endpoint origin/content paths`；`safe binding version/rotation provenance`。

不变量：Only original valid secret resolved live; no first-active provider fallback, secret/effective credentials never persisted in safe snapshot.

### LocalContinuationWork

字段：`unique(task,unit,policy)`；`owner/operation/selected result+digest+DB fulfillment/first deadline refs`；`phase/order/version/fee/application refs/terminal reason/current Task fence`。

不变量：Unique(task_id,unit_id,policy_version); owner/operation/selected result_ref/content digest/DB generation_fulfilled_at; immutable original first-intent/deadline refs; frozen phase order; phase/phase_version; fee_decision_ref/application_ref; terminal_reason and last execution fence. Each declared new text Unit has this mandatory completion requirement.

### ContinuationClaimAuthority

字段：`owner/task/unit/work/result/phase_version`；`worker/token/generation/DB lease; purpose local_continuation`；`permitted resume_fee|resume_delivery|finalize only`。

不变量：ContinuationClaimAuthority binds exact owner/task/unit/work/result/phase_version, current Task token/worker/generation/DB lease and purpose local_continuation. P2 fee/P5 target/normal P6 task-message finalization require current processing fence and phase; after target lock recheck DB time. It is rejected by prepare_policy_intent, every Provider create and submission-key producer. Plain OwnerTaskScope never charges/applies worker continuation; source admission/owner refund/cancel retain separate declared authority.

### StreamFeeAuthority

字段：`trusted server actor/UUID attempt/text.stream`；`frozen model/quote/skip-BYOK disposition`；`controlled fee/start/settlement provenance`。

不变量：StreamFeeAuthority is server trusted actor+UUID attempt+frozen registry/model/quote/disposition. No new client key/header/SSE field; client skip/cost never actual debit proof.

### StreamDebitReceipt

字段：`actor/attempt/operation/quote/disposition`；`actual amount/original usage id/key nullable only0`；`commit_state staged|committed|unknown; optional confirmed Task/message binding`。

不变量：StreamFeeRecord unique(server_attempt_id): owner/operation=text.stream/frozen quote and model provenance/disposition debit|skipped|byok|zero, actual_debited_amount, original_debit_log_id/business_key (nullable only amount0), fee settlement/start state/token, optional message/task binding. Original owner/debit/amount immutable. StreamDebitReceipt commit_state staged|committed|unknown is trusted DTO confirmation state, not in-TX claim that commit succeeded.

### StreamStartPermit

字段：`original confirmed receipt/attempt/start version/token`；`reserved state; confirmed NEW exposed transition yields in-process single-use capability`。

不变量：acquire_stream_start requires confirmed original receipt/disposition and optional confirmed Task binding; commit ready_to_start→start_reserved private token. Before HTTP expose_stream_start validates/consumes token once to start_exposed; only NEW confirmed committed transition returns an in-process single-use WebStartCapability. Provider after DB release. Repeated/existing/unknown transition yields no new capability; restart never relaunches. Gate rejection while still reserved atomically revokes token so callback cannot expose. Only persisted pre-exposure revocation proves known_not_started; missing Task/status or ambiguous spawn/exposed state cannot.

### StreamSettlementProof

字段：`original attempt/exposed or revoked start token`；`kind known_not_started|source_policy_failure`；`safe trusted original Web observation/monotonic policy fact`。

不变量：Known second Task TX failure, request exit before expose, or gate failure with durable pre-exposure permit revocation records controlled StreamSettlementProof known_not_started; refund original positive debit once, Task absent allowed. Unknown second commit first resolves original binding; if unresolved only row-lock-confirmed pre-exposure abort can prove no Provider exposure and refund. DB/commit ambiguity without that proof remains pending. This is explicit future D2 risk repair. Source provider error/empty final response/controlled Web cancel yields StreamSettlementProof source_policy_failure bound to original attempt/exposed execution, safe observation and frozen rule; refund original actual debit once. Successful stream retains debit. SSE disconnect alone not cancel/failure. Unknown lost process/start without trustworthy terminal proof stays settlement_pending, no generic7200 graph deadline invented.

### PollReservation

字段：`unique(group,sequence)/id/token`；`active attempt/step/submission/known id/digest`；`committed shared poll count/DB time/timeout/next_poll_at`；`state reserved|accepted|closed_unknown/immutable observation ref`。

不变量：reserve_active_poll(tx,ClaimAuthority|ControlledQueryAuthority,group) short TX locks User→Task→Units→Steps→Submissions→Group→reservation; exact frozen graph/sole active allowed original known id/binding/no cancel-normal policy stop, poll_used<720 and DB now<original deadline. Max1 outstanding reservation. Increment shared poll_used and monotonic sequence, persist attempt/submission/id/digest/token/DB timeout/deadline/next_poll_at atomically. Only confirmed commit returns PollReservation. Existing unknown no-id intent never creates query id/next POST permission.

### PolicyStopEvent

字段：`unique(group,policy,reason auth_unavailable_12)`；`ordered original unknown query refs/active original intent/id/DB stopped_at`；`verified financial disposition`；`financial_disposition pending|refunded|retained_by_started_cancel|no_debit`；`original_debit/refund/amount or original committed started-cancel marker refs;owner/task/unit/group binding`。

不变量：PolicyStopEvent unique(group,policy_version,reason auth_unavailable_12), ordered qualifying reservation/observation refs, original active step/submission/known id and DB stopped_at. Original12 non200 observations remain unknown/nonterminal; no fabricated HTTP200 failed or Provider ResultEvidence. Event-before-cancel financial decision MUST occur before ANY new Task/Unit marker via #/policyStopBeforeCancelContract. No event/settler scheduling inversion.

### ControlSettlementAuthority

字段：`trusted owner or controlled recovery action`；`exact task/operation/stop event/current revocation-quiescence facts`；`allowed cancellation/due/policy/terminal aggregate only`。

不变量：Explicit source-compatible owner control exception; cannot charge/apply local work/Provider; control finalization after revoke/quiescence only. Selected cancellation always calls operation-aware premarker policy-event settlement;owner permission never bypasses original debit proof or existing started marker.

### ControlledQueryAuthority

字段：`trusted source-bounded original owner/task/group recovery`；`original active step/submission/id and frozen registry/binding`；`reserve original remaining budget only`。

不变量：No worker target/wallet permission, no Provider create. Reservation/observation accounting only, cancel/due policy enforced.

### PolicyStopFinancialDisposition

字段：`original PolicyStopEvent/owner/task/unit/group/registry refs`；`state pending|refunded|retained_by_started_cancel|no_debit`；`exact original debit id/key/amount;optional related refund id/amount or prior committed started-cancel marker`；`recorded in caller TX;terminal immutable and money never exceeds original debit`。

不变量：PolicyStopFinancialDisposition belongs to original event+unit+debit. pending→refunded(original refund id/amount) OR retained_by_started_cancel(original committed marker) OR no_debit(actual0);once terminal immutable. Different events sharing original Unit debit may reference the SAME already-verified refund, not mint more refunds. A cancel marker in compliant selected path cannot newly commit while an already-observed prior applicable event remains financially pending. Foreign/mismatched bindings cannot authorize money.

## 公共方法

签名逐字保留；tx/shared_tx属caller，只stage/flush。claim adapter短TX是明确例外。各方法同时受上述完整规范及design R1–R4约束。这里保留方法特有首句，避免重复粘贴共同长条款；不能省略完整规范。

### P1 GenerationAdmissionUnitOfWork + GenerationCatalog

所有者：L-A contracts/admission core；共同规范：N1/N3。

**P1.1** `begin_admission(actor) -> AdmissionUnitOfWork`

短DB事务；caller-owned commit/rollback/close。

**P1.2** `find_original_receipt(operation, raw_key, fingerprint) -> replay|absent|conflict`

replay在新membership check前；不做网络。

**P1.3** `authorize_new_and_lock_catalog_targets(command) -> FrozenExecutionSnapshot`

series/team/chapter/asset/prompt/model/category/index与价格锁内检查。

**P1.4** `stage_graph(snapshot, policy, ordered_items) -> SubmissionGraphDraft`

仅stage；review/optimize不自动预扣，不新增客户端key。

**P1.5** `stage_precharges(graph, quote_policy) -> BillingOutcome[]`

single/batch image按旧时序；batch所有有效+总余额先验。

**P1.6** `commit_receipt(graph) -> committed legacy receipt`

所有graph/messages/ledger原子commit；single失败用新的短TX按原key查。

**P1.7** `stage_stream_debit(tx, StreamFeeAuthority, frozen_quote) -> StagedStreamDebitReceipt`

P1 stage_stream_debit(tx,StreamFeeAuthority,frozen_quote) keeps old model/owner/active/membership/balance checks.

**P1.8** `confirm_stream_commit(authority, original_attempt, commit_result) -> CommittedStreamDebitReceipt|unknown`

After known commit, confirm_stream_commit verifies original persisted row/log, returns committed proof.

**P1.9** `bind_stream_task(tx, StreamFeeAuthority, original_receipt, owned_task, message) -> binding|same|conflict`

No message_id means no Task/fake Unit.

### P2 GenerationExecutionPort / LifecycleUnitOfWork

所有者：L-A lifecycle/core；共同规范：N1–N6/N7。

**P2.1** `claim_next(type, instance) -> ClaimAuthority|None`

adapter自持短claim TX、DB clock/SKIP LOCKED；型registry控制。

**P2.2** `renew_or_revoke(claim) -> renewed|ownership_lost`

exact token/generation/owner；不能仅凭task_id。

**P2.3** `prepare_policy_intent(tx, claim, step, frozen_operation_policy, safe_request) -> staged intent`

Same lifecycle_v1 owner/graph/fence/dependency/secret/first7200s checks; phase-aware deferred review/optimize charging.

**P2.4** `commit_intent(tx, staged) -> CommittedIntent`

确认commit后才暴露Provider capability；不跨未决commit自动再POST。

**P2.5** `record_observation(tx, scope, observation) -> recorded|duplicate|conflict`

迟claim合法响应只unknown；同key不同data拒绝。

**P2.6** `validate_observation(scope, observation_ref) -> ValidatedGenerationResult|pending|invalid`

受控来源、terminal/result可恢复性/时限；不信rawsuccess或fallback。

**P2.7** `charge_or_refund(tx, scope, unit, evidence, policy) -> BillingOutcome`

按五费用时序；verified debit、0/no-charge、独立成功投影。

**P2.8** `signal_cancel(scope) -> accepted_signal`

All selected graph owner HTTP cancel facade, P2 signal_cancel/cancel_and_settle, P6 consume_cancel_and_due cancellation branch and cancellation recovery/replay MUST invoke this operation-aware adapter.

**P2.9** `cancel_and_settle(tx, OwnerTaskScope, lifecycle_v1_policy) -> LifecycleOutcome`

User→Task→all Units(stable item/id)→Steps(stable order/id)→Submissions→Groups(stable group/id)→PolicyStopEvents→result/Work as relevant; then wallet/original debit/refund logs.

**P2.10** `settle_due(tx, OwnerTaskScope, unit) -> LifecycleOutcome`

Selected real P6 consumer; first intent7200s unchanged, timely verified recoverable generation fulfillment preserved, verified debit only.

**P2.11** `classify_recovery(scope) -> RecoveryPlan[]`

RecoveryPlan adds resume_fee/resume_delivery/finalize with original work/phase/result refs.

**P2.12** `resolve_fee_once(tx, ContinuationClaimAuthority, unit, result_ref, application_ref, phase) -> UnitFeeDecision`

ContinuationClaimAuthority binds exact owner/task/unit/work/result/phase_version, current Task token/worker/generation/DB lease and purpose local_continuation.

**P2.13** `advance_attempt_group(tx, ClaimAuthority, group, validated_active_failure) -> next_prepared|group_failed|blocked`

Only active allowed generate.attemptN create-ready; future optional prepared members dormant.

**P2.14** `complete_attempt_group(tx, ControlledRecoveryScope, group, linked_validated_result) -> generation_fulfilled|failed|pending`

New group-aware validator requires ordinary required generation steps and required groups.

**P2.15** `persist_fulfillment_and_continuation(tx, controlled_validator, linked_validated_result) -> GenerationResultReceipt + LocalContinuationWork`

persist_fulfillment_and_continuation locks User→Task→all Units(stable item/id)→Steps→Submissions→Group/result→Work.

**P2.16** `claim_continuation(task_type, instance, lease_seconds) -> ContinuationClaimAuthority|None`

P2 claim_continuation is a separate new-registry candidate branch/adapter, not unchanged task_claims.claim_next.

**P2.17** `bind_current_continuation(tx, current ClaimAuthority, work_ref) -> ContinuationClaimAuthority|ownership_lost|phase_conflict`

A live generation worker may bind_current_continuation(tx,current ClaimAuthority,work) to a phase-limited ContinuationClaimAuthority sharing current Task token/generation/lease.

**P2.18** `lookup_continuation(scope, original unit/result) -> Work + original fee/application|pending`

Real application+ApplicationRecord+next Work phase commit once with source/media/notes.

**P2.19** `lookup_stream_debit(StreamFeeAuthority, original_attempt) -> committed|proven_absent|unknown`

After known commit, confirm_stream_commit verifies original persisted row/log, returns committed proof.

**P2.20** `refund_stream_once(tx, StreamFeeAuthority, original_receipt, StreamSettlementProof) -> refunded|already_refunded|no_debit|pending`

All stream writers serialize on User.

**P2.21** `acquire_stream_start(tx, StreamFeeAuthority, committed_receipt, optional_binding) -> staged StreamStartPermit`

acquire_stream_start requires confirmed original receipt/disposition and optional confirmed Task binding; commit ready_to_start→start_reserved private token.

**P2.22** `expose_stream_start(tx, committed StreamStartPermit) -> staged one-shot WebStartCapability|revoked|already_exposed|unknown`

acquire_stream_start requires confirmed original receipt/disposition and optional confirmed Task binding; commit ready_to_start→start_reserved private token.

**P2.23** `reserve_active_poll(tx, ClaimAuthority|ControlledQueryAuthority, group) -> staged PollReservation|not_due|blocked|budget_exhausted`

reserve_active_poll(tx,ClaimAuthority|ControlledQueryAuthority,group) short TX locks User→Task→Units→Steps→Submissions→Group→reservation; exact frozen graph/sole active allowed original known id/binding/no cancel-normal policy stop, poll_used<720 and DB now<original deadline.

**P2.24** `accept_poll_observation(tx, reservation, raw) -> recorded|duplicate|conflict|policy_stop`

accept_poll_observation first stores raw original unknown/dedupe binding; locked policy acceptance/close once per increasing reservation sequence, max1 outstanding explicit ordering.

**P2.25** `close_expired_poll_reservation(tx, controlled_recovery, reservation) -> closed_unknown|same|not_due`

Reserved crash/failed query permanently consumes one poll.

**P2.26** `stop_group_by_policy(tx, controlled_validator, PolicyStopEvent) -> policy_stopped|same`

Controlled stop_group_by_policy commits policy_stopped with sticky event, freezes dormant attempts/revokes new create and normal poll, records operation-policy terminal disposition.

**P2.27** `settle_policy_stop(tx, OwnerTaskScope, original PolicyStopEvent) -> refunded|retained|no_debit|pending`

Normative #/policyStopBeforeCancelContract.

**P2.28** `settle_committed_policy_before_cancel(tx, OwnerTaskScope, locked_new_registry_scope) -> PolicyStopFinancialDisposition[]|pending|conflict`

Both stop_group_by_policy event producer and every selected cancellation/settlement adapter serialize on the SAME owner User lock and declared Task/Units/Steps/Submissions/Group/Event order.

### P3 GenerationProviderPort / ProviderTransport

所有者：L-A contract; per-family L-A/L-B/L-C adapters；共同规范：N1/N3/N4/N7。

**P3.1** `stream_text(snapshot, WebStreamAuthority) -> AsyncIterator[TextEvent]`

旧OpenAI/DeepSeek/custom协议；reasoning上游但旧下游默认不转发。

**P3.2** `optimize_frame(snapshot, context, CommittedIntent) -> TextResult|ProviderFailure`

300s非流与600sstream不同；no prompt无systemfallback。

**P3.3** `generate_sync_image(snapshot, refs, CommittedIntent) -> MediaResult|UnknownCreate|Failure`

保留JSON/multipart/URL/base64；同步timeout也可unknown。

**P3.4** `submit_async_media(snapshot, refs, CommittedIntent) -> ExternalOperationRef`

纯create；所有HTTP在DB事务外；无自动再次create。

**P3.5** `query_existing(snapshot, ExternalOperationRef, optional confirmed PollReservation) -> ProviderObservation`

仅真实GET/wait/query；supplier异构，不统一/v1/videos。

**P3.6** `fetch_provider_content(snapshot, external_ref) -> ReferenceInput`

授权content下载显式来源/endpoint/header；由P4存储。

**P3.7** `resolve_original_credential(binding, purpose=create|query|content) -> BoundCredential|Unavailable`

Only original identity/owner/provider/origin/principal and proven same-principal rotation; safe failure, no cross-account fallback; secret ephemeral.

### P4 GenerationMediaStore

所有者：L-A contract; L-B media store；共同规范：N7及design R3。

**P4.1** `normalize_managed_url(url, explicit_origin_map) -> URL|unmanaged`

current/allowlistedlegacyURL保query；没有发现新host行为。

**P4.2** `prepare_reference(input, capability, config) -> PreparedReference`

ref截取、typed data/local/url、Muse upload_dir；路径生命周期显式。

**P4.3** `transfer_public_url(input, guard_config) -> StoredMedia|TransferFailure`

200MiB/并发2/64KiB/80,443/no redirect-private-active MIME/600s/清理。

**P4.4** `store_bytes_or_provider_content(input, object_policy) -> StoredMedia`

R2 date/business/UUID、mime/immutable cache/SDK thread standard3 attempts。

**P4.5** `release_prepared_reference(handle) -> cleanup outcome`

成功/失败/取消所有暂存清理；provider不会私自绕P4。

**P4.6** `fetch_bound_content(ExternalOperationRef, ProviderContentCapability) -> PreparedReference`

Original origin/path and credential only, no redirects/header leakage; public guard or per-binding authorized private policy, never global exception.

### P5 GenerationResultApplicationPort

所有者：L-A contract; root existing public facade extensions/shared same-TX binding；共同规范：N2/N7及design R2。

**P5.1** `apply_worker_result(tx, ClaimAuthority|ContinuationClaimAuthority, validated_result, target) -> ApplicationOutcome`

claim+目标锁/CAS+媒体revision+approval失效+notes CAS同TX；不自行commit/开第二UoW。

**P5.2** `apply_web_text(tx, WebStreamAuthority, text_result) -> ApplicationOutcome`

只适用旧Web stream，检查trusted actor/task/message scope；不假称worker lease。

**P5.3** `apply_canvas_media(shared_tx, CanvasApplyAuthority, GenerationApplicationCommand) -> ApplicationOutcome`

Existing PUT explicit mode; owner+completed task/selected result+target snapshot checked under same TX; public same-Session source/media/notes facades, one commit by caller, exact replay before old pre-apply CAS.

**P5.4** `lookup_applied_result(scope, result_ref) -> ApplicationRecord|None`

persisted apply证据防重复写；不能以UIapplied标志代替。

**P5.5** `load_owned_completed_result(shared_tx, actor, task_id, result_ref) -> GenerationResultReceipt|not_found|unready`

Mandatory trusted owner/task/result binding in same mutation TX; never calls separate read-only UoW as atomic proof.

### P6 GenerationWorkerRuntime / SubmissionRecoveryPort

所有者：L-A runtime/core; root inert composition and each family executor；共同规范：N2–N7。

**P6.1** `build_runtime(explicit_config, executor_factories) -> inert runtime`

构造不分配HTTP/session/crypto/storage资源；root汇总registry。

**P6.2** `run_claimed_once(task_type) -> execution/recovery outcome`

五旧worker types，Graph满足才执行；heartbeat贯穿、lost停止新writes。

**P6.3** `consume_recovery(plan) -> query|verify|resume_fee|resume_delivery|finalize|settled|pending`

RecoveryPlan adds resume_fee/resume_delivery/finalize with original work/phase/result refs.

**P6.4** `run_web_stream(command, WebStreamAuthority) -> legacy SSE bridge`

默认保持Web进程queue/gate及optional AITask；不新增公开task/reconnect/cursor/事件字段。

**P6.5** `shutdown(reason) -> bounded shutdown receipt`

shutdown不同usercancel；释放/revoke正确claim、不业务退款finalize；留恢复证据。

**P6.6** `consume_cancel_and_due(scope) -> settled|retained|pending|terminal_projection`

Cancellation branch mandatory #/policyStopBeforeCancelContract adapter for ALL selected new-registry types/replay;same caller TX ordering.

**P6.7** `consume_local_continuation(plan) -> fee_resolved|delivered|finalized|pending|ownership_lost`

RecoveryPlan adds resume_fee/resume_delivery/finalize with original work/phase/result refs.

**P6.8** `finalize_continuation(tx, ContinuationClaimAuthority, work) -> task_projection|pending`

finalize_pending is real claimable local work, P6 finalize_continuation requires current Task/phase fence and does no Provider.

**P6.9** `finalize_control_stop(tx, ControlSettlementAuthority, task) -> control_terminal_projection|pending`

finalize_pending is real claimable local work, P6 finalize_continuation requires current Task/phase fence and does no Provider.

**P6.10** `consume_stream_fee_reconciliation(StreamFeeAuthority, original_attempt) -> settled|pending`

Debit/refund/binding/start unknown commit first lookup original attempt/log/related refund/phase.

## 状态、outcome和错误

Task wire：`queued`, `processing`, `cancelling`, `reconciling`, `completed`, `failed`；billing wire：`unbilled`, `charged`, `refunded`, `no_charge`，internal cancelled不是新增receipt状态。

**stepStateVocabularyDraft**：`prepared`, `submitting`, `submitted`, `result_observed`, `applied`, `failed`, `reconciling`, `deterministic_empty`.

**unitResultVocabularyDraft**：`pending`, `partial`, `succeeded`, `failed`, `unknown`.

**providerOutcomesDraft**：`text_content`, `text_reasoning`, `text_error`, `text_end`, `sync_result`, `submitted`, `pending`, `unknown_create`, `definitive_failure`, `content_available`, `unsupported`.

**applicationOutcomeDraft**：`applied`, `already_applied`, `stale`, `target_deleted`, `target_conflict`, `write_failed`.

**legacyLifecycleOutcomesFreshRead**：`partial`, `succeeded`, `succeeded_refunded`, `failed_refunded`, `failed_retained`, `failed_charged`, `failed_without_verified_debit`, `expired_refunded`, `expired_retained`, `expired_without_verified_debit`, `not_due`, `already_settled`, `stale_observation`, `cancelled`.

**attemptGroupStatesDraft**：`prepared`, `active`, `pending_verification`, `succeeded`, `failed`, `cancelled`, `policy_stopped`, `succeeded_late`.

**localContinuationWorkDraft**：`delivery_pending`, `fee_pending`, `finalize_pending`, `done`, `stopped_cancelled`, `stopped_insufficient`, `stopped_delivery_failed`, `stopped_late_generation`.

**streamFeeStatesDraft**：`staged DTO`, `committed debit|skipped|byok|zero`, `ready_to_start`, `start_reserved`, `start_exposed`, `success_retained`, `settlement_pending`, `refunded`, `no_debit_terminal`.

Step固定8值，unknown属于Unit/provider，不新造既有Step enum；group/fee/delivery是新schema。Fee：`resolution pending|charged|no_charge|waived_insufficient|waived_charge_failed|waived_cancelled|waived_delivery_failed|stopped_insufficient|stopped_late_generation`。

code是合同分类，不自动加wire字段；Root沿旧safe shape映射：

- `unauthenticated`：401 existing。坏token/撤销session；可信身份port产生，未在本任务执行JWT。
- `membership_or_resource_forbidden`：403 existing/new D8 gate as explicit future fix。new generation会员与锁内owner/team/claimed权限；receipt replay不先拒绝expiredmember。
- `invalid_generation_request`：422/legacy validation shape。DTOextra/strict/modelcategory/index/target；video canvas全部422。
- `key_digest_conflict`：409 existing。raw key相同但digest不同；不用newkey修复。
- `inflight_conflict`：409 existing behavior strengthened by D8 lock。optimize同user/chapter仍在途；不是请求幂等。
- `admission_commit_unknown`：503 single existing; original-key lookup/recovery。single已commit查回原receipt，否则未知；不把batch500安全error_id默默统一成single503。
- `batch_internal_error`：500 existing safe error_id。batch原generic异常shape保持；没有来源证明single uncertaincommit fallback通用于batch。
- `ownership_lost`：internal execution outcome, not new public route。停止当前worker writes/submit；合法迟证据仅unknown。
- `provider_create_unknown`：internal pending verification/task projection。timeout/掉线/commit uncertain不等于definitive failure；不得自动再次create。
- `evidence_conflict_or_invalid`：internal contract outcome; map through existing task/legacy response。same dedupe key different payload冲突，invalid/unrecoverable/fallback不成功。
- `target_conflict_deleted_stale`：409 expected target/result-unready/conflicting application;404 missing/non-owned task or deleted target; legacy403 target access gate。Future existing-PUT explicit mode; safe fixed error detail, no overwrite/new generation/synthetic refund. Current target schemas/mapping not implemented.
- `configuration_unavailable`：pre-intent explicit refusal or pending existingquery。Admission keeps legacy unavailable/permission error shapes; accepted worker before-create failure has no POST and operation-specific verified settlement. Original binding/query unavailable remains reconciling/pending and independent due settlement; no cross-account fallback.
- `continuation_phase_or_fence_conflict`：internal ownership_lost/phase_conflict through old task projection。Stop writes, original Work/fee/apply lookup; never regenerate/recharge terminal waiver
- `stream_commit_or_start_unknown`：old safe failure/error shape, no new public field。Original attempt proof pending; no blind debit/refund/provider-start
- `auth_unavailable_12_policy_stop`：old failed/auth error projection。Separate policy fact; original non200 unknown, verified original debit refund/retain; zero retry-create
- `poll_reservation_conflict_or_budget_exhausted`：internal pending/blocked through old shapes。No new query without original remaining confirmed reservation; no reset/fake provider failure

## literal和来源

四source auth markers/各family fixture以既往固定F07/window和source manifest为真值，不从当前model/response动态扩增；full source SHA/原read windows按accepted design04携带在receipt，本轮未重新读取或执行。P1–P6/type/body canonical绑定和输出全文SHA在author receipt，原39及修复子例见spec。
