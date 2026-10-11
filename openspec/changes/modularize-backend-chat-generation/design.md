# 设计：六生成入口

## 基础和接受层

独审03与Root consumption已接受design04的设计闭合。以下新图、Work、stream金融proof、poll reservation、policy event、generation_application和实际消费者仍待实现。旧源码提供五费用时序、Step/Submission约束和部分lifecycle_v1原语；不能把它们描述为完整已迁移/已运行。

普通task_claims排除succeeded Unit，文本fee/delivery恢复必须新registry的真实Work claim分支，不造readyStep或无submission evidence。全部固定源/header/原窗口按design04精确carry，本轮没有重扫目标、legacy或Git历史。

## D1–D9具体默认

### D1

选择：Only new registered frame.review/frame.optimize graph versions require local_continuation_v1. LocalContinuationWork is a real new schema/public planner branch, not a Provider Step/Submission/evidence. Old registry graph/no-graph eligibility unchanged. persist_fulfillment_and_continuation locks User→Task→all Units(stable item/id)→Steps→Submissions→Group/result→Work. In ONE commit persist trusted selected recoverable GenerationResultReceipt, truthful Unit generation fulfillment and initial LocalContinuationWork readiness. Task cannot finalize before required local work. Commit unknown looks up original receipt/work unique links; never Provider POST. No result-commit/work-later crash window.

固定旧边界：固定route review/optimize无图；media有图但执行器未接intent/evidence；固定runner无补图时reconciling。

### D2

选择：Five fee phases preserved; mandatory local Work current phase claims and separate taskless StreamDebitReceipt; new registered full lifecycle_v1 cancel/deadline consumers.

固定旧边界：各operation费用和commit时序不同；兼容执行器未全调用取消/期限原语。

### D3

选择：Confirmed committed intent for each allowed create; existing/unknown intent never re-POST. HaoAI initial+max2 allowed retries use required group over three immutable optional attempt steps, one charge and fixed budgets.

固定旧边界：视频多仅AITask extid，async图常无id；外部幂等与未知create查询能力未验证。

### D4

选择：Freeze all nonsecret execution/config/grant facts; credentials live only from exact original valid principal binding, allowed same-principal rotation. Query unavailable stays pending, no provider-wide fallback.

固定旧边界：源worker读当前model/prompt、systemFake漏params；快照存在不等于执行已冻结。

### D5

选择：Stable unique storyboardID/full target digest; absent ID uses strict whole-chapter snapshot+index fallback. Mutate locked current chapter through exact content CAS; duplicate ID rejected.

固定旧边界：源review/optimize直接当前index写，原图像稳定绑定不同；server targetCAS不是当前JS local JSON guard。

### D6

选择：迁移旧Web/gate/queue SSE；start仅type、content/error/done、reasoning默认不转发、prompt_id不自动应用；无message_id无AITask，断连不cancel。

固定旧边界：generator exception可error无done；不能错误统一为必有done或将process内任务称durable。

### D7

选择：Root composition separates public media, bound provider content, explicit local Muse and R2 storage; retain family payload/refcaps/poll budgets/source R2 semantics.

固定旧边界：内部reference/content下载若干未过guard；OSS名字兼容层实际R2；不能声称全部取资源已有统一防护。

### D8

选择：未来account identity先receipt replay、new-generation membership；复用锁内series/team/claimed rules，补review/optimize/stream资源/model/prompt/category/index授权，optimize真实user/chapter inflight锁。

固定旧边界：原review/optimize缺series/promptowner/index约束；原排重查询无锁，不是request重试回执。

### D9

选择：Keep client first-success automatic application, image canvas no message and video canvas422. Existing PUT explicit generation_application mode verifies owned completed task/result and server target CAS in one transaction.

固定旧边界：新current JS实读证明上述client层；未历史Gitblob绑定，source layer不同于固定后端；local snapshot/context守卫不证明serverCAS。

## 五收费时序

### FEE-stream

旧顺序：skip=false系统cost>0：wallet/usage commit → optional processing AITask第二commit；skip=true只余额检查，不扣；BYOK0

本次约束：保持两阶段可观察行为与无message_id无AITask；失败refund只接受实际debit linkage，skipbool/cost字段不作proof

### FEE-single

旧顺序：准入单事务TaskSubmission+queuedTask+oneUnit+preparedStep+precharge→receipt

本次约束：precharge实际ledger关系+Unit quote；commit uncertain查原key；fail退款真实debit；BYOK0 no_charge

### FEE-batch-image

旧顺序：validate全部items/quote/total余额后，所有逐itemTask/Unit/Step/messages/precharges一次commit

本次约束：保留有序tasks receipt/全原子准入、执行和退款逐item；不变整个batch一个chargeunit

### FEE-review

旧顺序：AI成功→frame/description成功write commit→worker ledger debit→completed；系统不足保留结果cost0；BYOK0

本次约束：图Unit初始unbilled，不在admission预扣；apply成功证据持久后另短TX幂等charge，不足保已写结果actualcost0、Unit unbilled并持久waived_insufficient终态原因；no_charge仅quote0；不把两阶段默默合并原子预扣

### FEE-optimize

旧顺序：每frame AI成功→ledger debit commit→frame/description write→结果；一次success completed/0failed，余额耗尽止新frame

本次约束：Per-frame AI→durable verified generated output→actual debit commit→fenced frame application commit. Generated Unit fulfillment and target delivery separately persisted; charged-unapplied retains charge, zero auto-refund based on cost/CAS alone.

## 事务架构

所有public mutation facade共享caller Session，只stage/flush，caller commit一次；network/worker等待/SDK/transfer在锁外。claim adapter可拥有明确的短claim TX。worker所有task/message/externalid/target/wallet写核owner/token/generation/DBlease，等待目标锁后再次核。

新review/optimize一次提交selected recoverable result+Unit真实generation fulfillment+mandatory Work readiness。review delivery→fee→finalize，optimize fee→delivery→finalize。真正的新claim_continuation或current-live bind给phase authority；它不授权Provider。unknown phase查原result/Work/fee/ApplicationRecord，不重扣/重写/重生成；永久waiver/stop不重开。Task需generation+ALL Work终态才finalize。

firstintent+7200s固定，strictly-timely DB validated generation之后可在期限后续local work，不新deadline/timeout退款/POST；晚text留真result且无新fee/apply。HaoAI optional attempts由required group管，唯一active ready；known HTTP200 ref failure最多2retry，一Unit费用、720poll/5s/3s/原7200共享，unknowncreate不重POST。

## 原子取消线性化

所有selected public/P2/P6取消/replay先在同User-first锁序等锁后fresh/current读取原committed policy event，READ COMMITTED新短TX，任何Task/Unit marker前验证原event/owner/task/unit/registry/group/submission/id/unknown/debit，原refund+event disposition stage/flush，再原cancel+ALL Workstop+lease revoke+aggregate一次caller commit。

event先commit/settler延迟时cancel负责先退款；started-cancel先commit则原retain不可清marker强退。actual0无refundlog，positive未证实全rollback无新marker，unknown查原event/ledger/markers/Work/fence/aggregate。auth12原non200仍unknown，policy sticky不同Provider failed，late原active真成功不能撤金融决定/重新收费/自动apply或重启终态Task。

## R2/R3/R4落地适配

下面保留已接受技术选择的完整义务。文字中的proposed/future表示待产品实现；不得将当前旧facade、旧claim或独立readonly UoW冒称新合同已实现。P1–P6公开边界和具体phase/锁/authority在public-contracts中共同生效。

### R1

**policyAwareIntent**

P2 prepare_policy_intent(tx, claim, step, frozen policy). Preserve lifecycle_v1 graph/owner/fence/secret/dependency/deadline checks, but charge only at frozen phase. Media uses old before-intent/admission precharge; review post_apply and optimize pre_apply defer charge. Existing prepare_step_intent is not called unchanged for text.

**generationVersusDelivery**

persist_fulfillment_and_continuation locks User→Task→all Units(stable item/id)→Steps→Submissions→Group/result→Work. In ONE commit persist trusted selected recoverable GenerationResultReceipt, truthful Unit generation fulfillment and initial LocalContinuationWork readiness. Task cannot finalize before required local work. Commit unknown looks up original receipt/work unique links; never Provider POST. No result-commit/work-later crash window.

**chargeDecision**

New UnitFeeDecision unique(unit_id): phase, resolution(pending|charged|no_charge|waived_insufficient|waived_charge_failed|waived_cancelled|stopped_insufficient), ledger_ref, resolved_at, generation_result_ref, application_ref. Positive waiver leaves Unit unbilled/debited=0/refunded=0 and actualcost0; immutable terminal waiver blocks all later charge attempts. no_charge only quote0. Decision+actual debit or definitive waiver committed atomically under unit/owner locks.

**review**

Output/Work→delivery commit includes fee_pending; crash before fee resumes fee only via new claim, never re-apply/re-generate. Confirmed insufficient retains written result, positive Unit unbilled0 and immutable waived_insufficient. Proven absent debit with definite safe failure may waived_charge_failed; uncertain commit remains pending. Waiver never later charges.

**optimize**

Debit+UnitFeeDecision commit includes delivery_pending; crash before apply resumes same saved result/original actual fee via new claim. Fenced target CAS writes once. Target conflict/deletion is terminal delivery failure with charge retained; not Provider failure or refund proof. Transient TX failure remains pending; unknown commit checks ApplicationRecord first. stopped_insufficient is immutable per-Unit decision plus Task stop-new-work reason under Task lock. Stop all new charge/generation, no restart surcharge. Already paid frames may drain allowed pending delivery unless cancelled/conflicted; real applications, completion-order list and paid-cost projection determine optimize partial completion. This deterministic recovery drain is explicitly a future technical choice, not asserted old deployment behavior.

**cancelEndpoint**

Future selected new-registry public owner adapter preserves old wire/local signal but invokes #/policyStopBeforeCancelContract;never writes status-only marker before original committed policy preflight/refund. Legacy no-graph status-only behavior unchanged;current endpoint not claimed upgraded.

**cancelConsumer**

Selected new-registry owner cancellation MUST first execute #/policyStopBeforeCancelContract discovery and unique event/debit settlement before ANY Task/Unit cancellation marker; all preflight/refund/event disposition+original lifecycle_v1 cancel+ALL Work stop+lease revoke+aggregate share ONE caller TX. After that ordering, no charge/apply after any cancel marker. Unchanged local phase rules:  Review applied-before-fee→immutable waived_cancelled/unbilled0, data retained. Optimize charged-before-apply→delivery stopped_cancelled, actual debit retained. Original refund/waiver/stop never resets; no source rollback. Worker late responses remain original unknown until controlled validation.

**deadlineConsumer**

Never change original first intent+7200s or truthful DB generation_fulfilled_at. Timely means recoverable validation strictly before original deadline; timely local fee/delivery/finalize can resume after7200 with new local lease and settle_due sees already_settled generation, no timeout refund/new POST. No new local business deadline invented. Late new text fulfillment remains truthful receipt with verified source settlement, Work stops_late_generation, no new fee/apply. DB/lease failure remains reconciling, never false completion.

**localContinuationContract**

Normative #/postGenerationContinuationContract including fresh recovery claim/live phase binding and finalize

**streamFeeContract**

Normative #/streamDebitContract independent first-TX proof and public producer/confirm/lookup/refund/start

### R2

**wire**

Extend existing chapter/character/scene/prop PUT with an explicit optional generation_application object. Legacy body without it retains old behavior. In generation mode, require task_id/result_ref/expected_target, reject unrelated edits, load canonical stored result server-side; ignore/reject any client URL that differs. No extra provider POST and no new canvas video acceptance (still422). Current client automatic first-success choice remains; set applied only after committed PUT receipt, retry same result on transport uncertainty.

**authority**

CanvasApplyAuthority is an authenticated actor plus owned completed task and selected validated result receipt; exact task.user_id==actor.user_id, operation/category/series/chapter/target match frozen admission, task.status completed, result_ref exists and matches stored digest/selected evidence. Ordinary task metadata, arbitrary URL, team read permission or cost field alone cannot authorize. Current series/target mutation access is checked in the same transaction.

**transaction**

One GenerationWriteTransaction holds User→Task→Unit/result→Chapter→stable source assets→media states→notes ordering. Root public facade adapters share exactly this Session/connection, never open/commit another UoW. After waiting on target lock, worker authority is re-fenced; completed user authority has no processing claim requirement. Source patch, storyboard description/media reconciliation, approval revocation, notes CAS and unique ApplicationRecord commit once; any failure rolls back all.

**stableID**

Admission resolves exactly one stable storyboard ID and stores full canonical frame snapshot digest, expected preview/media revision and referenced asset set. Under chapter lock find exactly one same ID in current content and same-chapter asset; equal target frame snapshot permits rebase across unrelated chapter changes/reorder. Patch current content, SQL CAS against the entire current locked content bytes; descriptions update only validated same-chapter refs. Duplicate ID is rejected, never routed to no-ID fallback.

**noID**

Only genuine absent stable ID uses (chapter_id,index,exact server canonical chapter snapshot digest,exact target frame digest). At apply compare all content before interpreting original index, plus SQL CAS expected_content; any edit/reorder/insert/delete invalidates it. Do not mint stable IDs, copy media approvals or search by similar text. If no stable media identity exists, preserve invalid/source_valid=false handling; never create false media revision/approval identity.

**asset**

Character/scene/prop application requires locked same-series exact asset id and frozen full target digest including previous image_url. New conditional adapter uses id+series_id+expected image_url (NULL-aware) and relevant target digest equality under lock; general asset PUT lacks this currently. Worker batch existing-image nonoverwrite remains; explicitly requested canvas replacement is allowed only for the unchanged expected old target, as the old client intended.

**applicationReplay**

ApplicationRecord unique(result_ref,target_scope). Exact committed replay returns already_applied without rechecking old pre-apply snapshot or bumping media/notes revisions; changed target/receipt payload conflict409. Lookup requires current authorized owner. A failed/unknown client PUT may query/retry that application, never submit another generation.

**canvasDocument**

Generation application writes frame preview/asset source, not an unbounded canvas document replacement. If this mode needs persisted canvas node state, require separate frozen canvas version+node scope and atomic id/version UPDATE in the same TX; do not infer that current optional version precheck provides it. Default does not mutate layout or add that dependency.

**failureAudit**

Any failed target mutation rolls back proposed source/media/notes/ApplicationRecord writes together. Delivery-conflict/failed observations may be persisted in a separate safe audit/phase transaction; only a committed applied ApplicationRecord proves mutation. Unknown mutation commit first looks up exact applied record before retry.

Root public facade落位提议：

- series_data/generation.py: stage_frame_result(tx, authority, result, target) and stage_frame_preview(tx, authority, result, target)
- asset_data/generation.py: stage_asset_image(tx, authority, result, target)
- personal_production/notes/facade.py: stage_media_reconciliation(shared_tx, chapter, proposed source changes)
- task_observation/ports.py extension or public generation result reader: load_owned_completed_result(shared_tx, actor, task_id, result_ref)

### R3

**snapshot**

FrozenExecutionSnapshot contains model source/id/provider/endpoint origin+path policy, model name/category/all params, effective prompt/context, quote, refs/target, grant provenance(actor/series/team/access decision at admission), credential_binding_ref and safe configuration/adapter versions. Worker uses the accepted grant and fixed nonsecret parameters, never reloads current pricing/prompt/params or invents a new account permission. User target application separately uses current mutation access.

**credential**

CredentialBinding=(source,row_id,owner_or_system,provider,original principal binding,allowed endpoint origin). Resolver is live only for this original binding and may return a currently valid secret under an explicitly authorized same-principal rotation; checks row ownership/provider/binding/allowed origin and enabled state. No secret in snapshot/evidence/log/receipt and no provider-wide first-active/model-config fallback. If same-principal rotation cannot be established from existing metadata, do not infer it from row id or decrypt success; mark binding_unverified until explicit bound config supplies identity.

**newAdmissionAndCreateFailure**

New admission rejects absent/disabled model or current permission failure before graph/debit by old route error shape. Registered accepted task can create only with unchanged frozen provider/endpoint request and original valid credential binding; unavailable before intent enters bounded configuration failure with no POST, settles actual existing precharge according to operation policy. Unknown credential/intent commit never authorizes replacement account/create.

**queryFailure**

Known existing external id uses only original binding and exact frozen endpoint/protocol. Missing/deleted/disabled/rotated-to-unproven-principal/403/decrypt/config/query failure preserves pending_verification/reconciling, no alternate credential/provider/model or new POST; persist safe failure reason and continue independent due settlement. Non200 generally pending; HaoAI explicitly bounded auth_unavailable policy is preserved per R4.

**composition**

Root creates inert runtime with explicit SessionFactory/DBclock/id factory/catalog/grant reader/bound CredentialResolver/P2/P5 plus four transport/storage capabilities below. No env discovery in generation providers; root translates allowed object-storage configuration once, injects secret handles, optional SDK factory and readiness. Source config names describe requirements only; no current env/secret/bucket/availability was inspected.

**publicMedia**

PublicMediaFetcher always public DNS guard, HTTP(S)80/443, no redirect, forbidden active MIME,200MiB/64KiB chunks/600s/concurrency2 and cleanup. Untrusted references/results never inherit a provider/local exception. Preserve managed URL/explicit legacy acceleration map and query bytes.

**providerContent**

ProviderContentFetcher requires bound ExternalOperationRef and server-constructed exact original provider origin/path/content endpoint; Authorization only from that original binding, no user-supplied auth headers, no redirect/header forwarding to other origin. Public original origin remains public guarded; an originally authorized private provider needs an explicit per-binding network capability restricting scheme/host/port/resolved network, not a global exception. Bounded stream/MIME/size/temp cleanup still applies.

**localMuse**

LocalMuseTransport capability explicitly restricts bound endpoint and local network; ReferenceStager has approved upload_dir plus path confinement/typed dataURI/public URL/local path handles. Keep source ref caps/audio payload/absolute image_path semantics; cleanup only owned generated staging files after provider no longer needs them or deadline/cancel cleanup, never caller-owned local files. Missing local path capability fails that provider before create; do not ban all authorized local providers or globally enable internal URL fetching.

**R2Storage**

GenerationMediaStore gets explicit R2 endpoint/bucket/public base/transfer temp/managed origins/legacy map and credential handles. SDK optional dependency injected, calls off event loop, standard total3 attempts (not3 application POST retries), date/business/UUID media key/MIME/immutable cache. Storage unavailable leaves result unverified/pending or fails before a new dependent create; original public URL fallback is not trusted success without recoverability validation.

### R4

**frozenGraph**

New registry video-haoai-attempt-group-v1 freezes three ExecutionSteps generate.attempt0/1/2, all optional, stable order0/1/2 and immutable per-attempt rule digests. Do not make dependencies point to a failed step; retry eligibility is a trusted attempt-group transition rule, not success dependency. New required ExecutionAttemptGroup unique(unit_id,group_key) lists these three frozen ids, max_retries2, original payload digest, status, active_attempt, selected_evidence_id, terminal_reason, poll_used, auth_unavailable_streak. Group existence/membership is verified against its new registry version in every transition.

**requiredCompletion**

New group-aware validator requires ordinary required generation steps and required groups. Active true valid linked recoverable receipt proves generation; definitive nonretry/exhausted allowed attempts prove real failure. Auth12 separately policy_stopped via immutable event, original unknown not Provider failed. Late active result rules in #/authPolicyStopContract; existing helper/planner unchanged for old registry.

**intent**

Each attempt owns one original ExternalSubmission with global stable server submission_key derived from unit/step/version, not worker generation. Under User→Task→Unit→Step→Group locks validate exact current claim/DBlease, cancellation/deadline, group eligibility, fixed request digest, existing-intent rule, phase-aware billing. Commit confirmed CommittedIntent once before POST. Retry uses a distinct never-submitted attempt step; original intents/evidence remain immutable. One Unit/precharge/verified debit across all attempts; subsequent precharge result may be already_charged, never a new task charge. New planner exposes only sole active allowed optional member as ready, remaining prepared members dormant.

**retryEligibility**

Only original bound query returns terminal failed for the exact active known external id and a trusted proof matches the three source marker strings, group retry budget remains, no cancellation, not yet deadline, no prior intent in next attempt and original frozen payload/ref digest unchanged. Invalid attempt proof records failure but optional step does not immediately refund entire Unit; group advances atomically. No retry for timeout, malformed/no-id create reply, disconnect,5xx or unknown create.

**unknownCreate**

Persist original intent, missing/unknown external id and unknown evidence; retry-create disabled, even if prior attempt had an eligible reference failure. Use only a proven supported lookup/query capability (none inferred for HaoAI no-id create); otherwise remain pending verification until deadline settlement. Never re-POST original/next attempt just because a worker restarted or an attempt returned no id.

**queryEvidenceFence**

Known id queries use original credential/provider/endpoint; all ids remain in original submissions, task external id is only latest compatibility projection under current fence. Stale worker can append owner-scoped exact-linked unknown observations; controlled recovery validator decides validity. Nonactive retired attempts cannot win group selection or mutate target/task/debit; selected result/group transition is immutable and idempotent. Same evidence key accepts exact immutable repeat only.

**budgets**

Normative #/pollReservationContract: commit atomic shared reservation before lock-free GET, conservative crash720 count, increasing once policy acceptance, source streak rules/5s/3s/original7200.

**settlement**

Group successful generated output follows original media charge/result/target policy; exhausted definitive group failure refunds exact verified debit at most once unless started-cancel retain applies; failure/deadline already established before cancel refunds first. Timely group success is not undone; late valid completion can succeed with refunded charge. Zero debit has no refund ledger. For prior committed auth policy event, every selected cancel invokes mandatory #/policyStopBeforeCancelContract before any Task/Unit marker;not source cancel first.

**oldRecords**

Persisted no-graph or unsupported-graph tasks remain incomplete_graph/reconciling and owned-readable. No implicit graph insertion, new intent, target write or debit/refund proof manufactured from credit_cost/legacy worker log. Any separate migration needs its own bounded source/ledger binding; none selected or executed by this draft.

**policyStop**

Normative #/authPolicyStopContract plus #/policyStopBeforeCancelContract premarker financial settlement, reverse started-cancel retention and original commit lookup. Other retry/poll/late result contracts unchanged.

## 三作者独占

实现/修改用gpt-6-luna/xhigh，分析/设计/评审用GPT-6.1 Sol/xhigh；不可用报告，不替换。P1–P6/type/state/error/operation policy先冻结。Root共享schema/facade/app/config/registry/ORM/frontend只指定原三Luna之一唯一写者，不能第四位并行写共享区。独占分片使用public ports/fakes，不互相private imports。

### L-A

入口：/api/chat/stream, /api/chat/ai-review, /api/chat/batch-optimize。

原独占path数组：

- `/Users/yanghaibo/data/projects/ai/haoai-next/backend/src/haoai_backend/generation_contracts/*`
- `/Users/yanghaibo/data/projects/ai/haoai-next/backend/src/haoai_backend/generation_core/*`
- `/Users/yanghaibo/data/projects/ai/haoai-next/backend/src/haoai_backend/generation_transport/*`
- `/Users/yanghaibo/data/projects/ai/haoai-next/backend/src/haoai_backend/text_generation/*`
- `/Users/yanghaibo/data/projects/ai/haoai-next/backend/src/haoai_backend/frame_generation/*`
- `/Users/yanghaibo/data/projects/ai/haoai-next/backend/src/haoai_backend/generation_providers/text/*`
- `/Users/yanghaibo/data/projects/ai/haoai-next/backend/tests/generation_core/*`
- `/Users/yanghaibo/data/projects/ai/haoai-next/backend/tests/text_generation/*`
- `/Users/yanghaibo/data/projects/ai/haoai-next/backend/tests/frame_generation/*`

依赖：R1 policy-aware intent/fee/output-delivery distinction and R4 group schema plus P1–P6 frozen contracts; root P5 same-TX public facades/config/registry/migrations。

原验收归属：A01, A02, A03, A04, A05, A08, A09, A10, A13, A14, A15, A16, A17, A18, A19, A20, A21, A22, A23, A24, A25, A26, A27, A28, A29, A30, A33。

### L-B

入口：/api/chat/image, /api/chat/batch-generate-images。

原独占path数组：

- `/Users/yanghaibo/data/projects/ai/haoai-next/backend/src/haoai_backend/image_generation/*`
- `/Users/yanghaibo/data/projects/ai/haoai-next/backend/src/haoai_backend/generation_providers/image/*`
- `/Users/yanghaibo/data/projects/ai/haoai-next/backend/src/haoai_backend/image_execution/*`
- `/Users/yanghaibo/data/projects/ai/haoai-next/backend/src/haoai_backend/media_storage/*`
- `/Users/yanghaibo/data/projects/ai/haoai-next/backend/tests/image_generation/*`
- `/Users/yanghaibo/data/projects/ai/haoai-next/backend/tests/media_storage/*`

依赖：Frozen P1–P6/R2 application mode/R3 four capabilities; implement image/media store only in exclusive paths; shared root facade adapters supplied via fake public ports。

原验收归属：A01, A03, A04, A05, A06, A07, A08, A09, A10, A11, A12, A21, A22, A24, A25, A26, A27, A28, A31, A34, A35, A36。

### L-C

入口：/api/chat/video。

原独占path数组：

- `/Users/yanghaibo/data/projects/ai/haoai-next/backend/src/haoai_backend/video_generation/*`
- `/Users/yanghaibo/data/projects/ai/haoai-next/backend/src/haoai_backend/generation_providers/video/*`
- `/Users/yanghaibo/data/projects/ai/haoai-next/backend/src/haoai_backend/video_execution/*`
- `/Users/yanghaibo/data/projects/ai/haoai-next/backend/tests/video_generation/*`

依赖：Frozen P1–P6/R4 required-group transition and R3 binding/content/Muse policy; implement15 provider families; use public P4 fake/store, never private B imports。

原验收归属：A01, A03, A04, A05, A06, A09, A10, A11, A21, A24, A25, A26, A27, A28, A32, A33, A34, A36。

### Root独占原path提议

未识别router/registry/frontend实际路径未来由Root落实，不伪称已读：

- `/Users/yanghaibo/data/projects/ai/haoai-next/backend/src/haoai_backend/app.py`
- `/Users/yanghaibo/data/projects/ai/haoai-next/backend/pyproject.toml`
- `actual existing app/startup/router/registry paths to be identified by root, not read here`
- `schema migration versions/readiness/config composition`
- `existing authentication/series_access/task_observation/chat_data/asset_data/notes public facade extensions if needed`
- `ordinary OpenSpec files and shared cross-module tests`
- `actual existing frontend root/router/API client/global state shared files`
- `/Users/yanghaibo/data/projects/ai/haoai-next/backend/src/haoai_backend/series_data/generation.py`
- `/Users/yanghaibo/data/projects/ai/haoai-next/backend/src/haoai_backend/asset_data/generation.py`
- `/Users/yanghaibo/data/projects/ai/haoai-next/backend/src/haoai_backend/personal_production/notes/facade.py`
- `Existing task_observation public same-TX result/cancellation facade extension; source HTTP/schema dispatch for generation_application`
- `New UnitFeeDecision/GenerationResultReceipt/ApplicationRecord/ExecutionAttemptGroup schema migrations and registry wiring (root assigned one existing Luna, not fourth parallel author)`

Root修复schema/adapter独占范围：

- LocalContinuationWork mandatory new text registry/planner; existing Task fence token/generation/lease reused
- StreamFeeRecord first-TX log linkage/private start state and optional Task binding; nullable original task/unit log retained
- PollReservation/PolicyStopEvent/immutable group fields and active-only planner; Step/Submission/Evidence constraints unchanged
- Same-Session task/message/source/notes facade, cancel/continuation/fee settlement adapters/app/composition/migration remain Root-exclusive; assign one existing Luna, no fourth writer

## 顺序与验收

当前GAP175修复/实际验收/文档/中文Git收尾→不同Sol独审五稿→Root在已有授权下安装普通OpenSpec和冻结公共合同→L-A共享headers/fakes与Root唯一schema/facade/config→三片独占实现→Root真正装配五worker types和Web stream→39全量适用验收。

A28全queued/started/failure-first/deadline-first/replay/mixed/shutdown链必测，signal-only/子集不算完成。A14重连未选，A38浏览器未执行，A39指定GPT-5.6 Sol/xhigh OFFLINE PENDING。源码、作者测试、Root实际PostgreSQL锁/并发/HTTP/重启、浏览器、外审、发布逐层，不以typed plans或fake tests证明实际consumer。六接口之后仍全后台迁移、React全流程、最后语言评估，不保证整体耗时/百分比。
