# Task Observation Design (Draft)

## Context

本五稿仅EVID staging。团队本地收口后，根建立实际B/C/H、旧可变原文/固定55字节、安装普通规划/CLI/独立review并批准才实施。当前target564/teams54/teams52冻结，B不是硬编码564。

固定兼容提交23403806898550a7668a6ee7c0c457315655c39b：chat.py739–1007、2362–2419、2547–2621；task_admission.py392–470；job_queue.py89–99；task_operations.py全文。chat697–736仅恢复查询背景，不迁生成。models/task_reliability、series/user/team、team_service/database必要窗提供真实列/政策；SHA/读extent/source-analysis02见receipt。55相比团队52仅新增task_admission/job_queue/task_operations，不改当前集合。

当前公开AuthenticationService.authenticated(authorization,require_membership=False/True)可分别适配两身份。resolve_business_actor始终active、只给TrustedActor.user_id，不能替代account-only；SQLsuperuser限详情本请求读取，不改共享identity。

## Goals / Non-Goals

完整九方法与独立取消事务，接通当前列表真实body；纯应用依赖本包窄ports和公开purepolicy/payload，不依SQLAlchemy/旧app/queue/billing/provider。生成6/generic4/admin2/proxy、Worker终态/退款/claimfencing、全React、PG/生产provider/SMTP/部署/最后语言不在本批；不增加type/分页/access/CAS政策。

## Decisions

### 1. One supplied request Session and narrow read ports

ports发布TaskObservationReader/TaskReceiptReader/TaskRequestAccessReader、TaskObservationUnitOfWork/Factory、CancellationSignalRegistry/Signal、TaskCancellationWriter。domain显式任务/提交/单元读取字段，真实空值不coerce，动态JSON适度Any；不向应用泄露Session/arbitrarySQL。

reader公开窄方法：load_owned_task(actor_id,task_id)、load_task(task_id)、find_owned_by_message(actor_id,message_id,limit=2)、count_owned_tasks/list_owned_tasks(actor_id,offset,limit)、load_messages/load_chapter_titles/load_asset_names、审核LIKE候选/running候选；receipt first_billing_unit(task_id)、find_submission(actor_id,operation,raw_key)、unique_billing_unit(task_id)；access load_sql_superuser(actor_id)、load_membership(team_id,user_id)。内部集合不转外部新validator。

统一request factory供应Session，UoW接收并负责rollback/close；八GET零业务commit/flush/DML，SQL Result实际消费。membership两次SQL仍执行，同PK按首次观察字段映射ORMidentity语义，不用第二值rebasing；不另查team存在。两回执独立函数，first和scalar_one_or_none差异不可复用一个选unit helper。

payload直接调用公开teams.reporting.task_payload.collect_task_context(tasks,messages)及project_task_items(tasks,messages,chapter_titles,asset_names)，只新增本模块fold/review/batch JSON；公开teams.policy.has_team_permission沿owner/permissions规则。当前无neutral提取必要，不改teams文件、不借reporting私有persistence或UoW；若未来提取须新范围保持teams语义。

### 2. Two explicit identity seams and inert configuration

app候选keyword resolve_task_account、resolve_task_active、task_cancellation_connection_factory、task_cancellation_signals均None，无默认资源；query Session沿现effective session_factory。

account优先显式resolve_task_account，否则已有AuthenticationRuntime下publicauthenticated(False)，绝不fallback active resolve_actor。active优先resolve_task_active、既有显式resolve_actor、再publicauthenticated(True)。is None判断缺失，falsey对象保留。旧82effective_resolver/旧四private和章节替换显式series_access_policy条件不变。

authentication.py适配publicservice/context与AuthenticationError的status/detail/headers，不引用私有helper、不修改auth代码。真实JWT/session/password_version/last_seen/membership先在authSession原提交完成并close，再开业务Session；未知authcommit失败闭口无重试。superuser仅详情任务存在后SQL users flag；其他端点无跨用户特权。

http沿原普通Query/Path类型与默认，不新response_model收紧；submission的idempotency_key是必填query，不是Header，Idempotency-Key仅helper错误detail文字，缺query先保留标准422；staticlist/counts/running避免dynamic遮挡。缺resolver/Session：503“任务观察服务尚未接线”且零业务Session；取消另缺独立factory：503“任务取消服务尚未接线”。默认闭包登记91，不实例化Engine/SQL/Auth adapter/env/Worker。

### 3. Signal first and independent cancellation transaction

纯application先owned task首次状态，queued/processing才registry.get，保持truthyset→writer顺序；其他状态不writer不独立事务仍cancelling。None registry是空本进程registry，不创建线程、不证明线上Worker联通。

SQL cancellation adapter构造接受Callable[[],ContextManager[Connection]]，由外部显式已配置Engine.begin提供；连接类型只在SQL构造边界，不进入应用。不得从Session私有engine/url推断或加载旧全局engine，不自己newEngine。

真实raw status-only UPDATE、正常context退出commit含row0，bool返回但app忽略。异常传播，仅独立txnrollback；无owner/type/claim/version WHERE，不用带onupdate的Task表扩大写集合，不写取消元数据。已set不撤，ack未知可durable；generic500不重放。业务读取与取消独立连接分别记录；实验不是PG/Worker/退款证明。

### 4. Exact files and single-owner contract

未来新增生产12路径（backend/src/haoai_backend/task_observation/）：__init__.py公共exports；domain.py记录；errors.py来源knownHTTP/missingconfig；ports.py共享窄合同；application.py九编排；receipts.py两种回执；payload.py纯JSON/公开helper；authentication.py两auth适配；persistence.pysuppliedSession只读；cancellation.py独立status写；tables.py真实查询投影/MetaData声明无create_all/reflection；http.py参数/顺序/配置。

新增测试9路径：backend/tests/task_observation_support.py；test_task_observation_domain.py；test_task_observation_application.py；test_task_observation_receipts.py；test_task_observation_persistence.py；test_task_observation_cancellation.py；test_task_observation_authentication.py；test_task_observation_http.py；test_task_observation_boundaries.py。

未来旧mutable10：backend/src/haoai_backend/app.py；backend/tests/test_module_boundaries.py、test_series_access_boundaries.py、test_authentication_boundaries.py、test_rough_cut_http.py、test_production_notes_http.py、test_asset_data_boundaries.py、test_chat_data_http.py、test_canvas_data_boundaries.py、test_teams_boundaries.py。九旧测试只登记新9/91/factory名称，原业务、env/Engine/Session、policy和old82哨兵保留；根下一baseline保存旧10并审trueDiff。本轮30路径授权不含九旧测试全正文，不虚称已全审。精确freeze31=21新+10旧。

未来7doc：README.md、backend/README.md、docs/architecture/backend-module-boundaries.md、docs/architecture/module-api-compatibility.md（四旧完整prefix EOFappend）；docs/architecture/task-observation-backend.md、本changeverification.md（新2）；本changetasks.md（仅根授权checkbox）。schedule、frontend、pyproject、auth/sharedidentity、teams实现、notes/access/其他history冻结。

单一共享作者S先冻结__init__/domain/errors/ports/receipts/payload/authentication公共合同。后续如root准并行，Q独占persistence/tables及receipt/persistence专测，C独占cancellation及cancel专测；S独占application/http和app/旧9最后整合；测试作者独占support及剩余专测。共享接口修改回S，不同时改文件或借私有port。当前不启动作者/agent或授权实现。

### 5. Valuable validation cases

| 场景 | 实际证明 |
| --- | --- |
| T01 identity/config | realJWT过期会员两回执可读/七active拒绝；SQLsuperuser/team/双用户；404/403顺序；缺配置零业务Session，old82/cold/privatepolicy |
| T02 task receipt | selector/歧义/原字符串、无/多/最早unit/falsey值、13键/rawresult/负值 |
| T03 submission | operation/key255/256/原key、非list/0/2task/错owner、缺/多unit、7键与无重提/账务DML |
| T04 list/context | default50/显式10/零负int沿SQL、不合法int422；四素材/章/消息/孤立task/partialJSON/截断/动态prompt |
| T05 request | globalmissing404先行/SQLspecial仅详情、两membership实际SELECT+首次PK值；fold长度/深度/坏scalarJSON |
| T06 review/running | LIKE空格/compact、statusfilter/noIDtypegate、非dict/unhashable500、runningnull/parseerror、优化JSONscalar/raw |
| T07 cancellation | queued/processing真实独立txn/statusonly；terminal不writer仍200；truthyeventset在writer前；claim/financial全行 |
| T08 row0 race | 初读Result消费后distinctDBAPI B提交终态/删行，再A UPDATE0+commit200；B目标差异单列其余全等 |
| T09 fault/unknown | actualSQL/precommitrollback但event保留；actualcommit后ack500/newGETdurable，无autoreplay；attempt/completion分别真实 |
| T10 fullowner | FKON非空24owner/Submission/task/quote可null真实constraint/Billing/Step/Credit自FK/external/result/media/private/auth；message_id无假FK |
| T11 rootTCP/TS | 九positive/关键错误/realJWTSQL/oldauth-team-private smoke；ordereddriver/rawASGI；actualpage10body给parseMyTaskPage(body,requestedPage)，非synthetic/frontend验收 |
| T12 scope/resources | rootfullbackend/syntax/55/31/oldprefix/history；ownedclose成功后计、client/listener/Engine/thread/socket/PID/port/tmp全AND/失败独占保留 |

新support可test-only使用已读公开chapter_asset_replacement_support.create_owner_database/完整OWNER_METADATA/snapshot/physical_connection_pair，扩展seed只在新support，不改旧helper或用于生产。旧TrackedSession.close计数早于delegate，roothelper须自记成功返回，不拿该计数单独当cleanup。生产薄查询表不算fullownerDDL。

HTTP故障由私有Pythonstate/透明delegate，不增publicendpoint/header。500先断status不强行JSON。SQLtrace明Session/connection、实际Result消费、完成commit时点；fixtureseed/B已提交写/读取独立标注，不用anySELECT或最终值冒称阶段。作者tests、rootfull、review、SQL/TCP/TS与资源分层，全部本轮not_run。

## Risks / Trade-offs

保留旧receipt差异、详情宽权限/LIKE/坏合法JSON500、宽int/负值、event先于独立写/row0正常/无claimCAS等反直觉行为，这是兼容非安全或串行政策修复。auth与业务与取消事务分别证明；B已提交变化不能被Arollback恢复。Nodefixture/registered/作者green不等rootTCP或fullReact。

当前166差集与生成6/generic4/admin2/proxy集合按analysis02原样保留直到真实交付。52/55仅固定来源，不迁provider/billing/Worker。Grillmeoffline，requestedSol6.1xhigh与runtime未独核分开。

## Migration Plan

团队收口后根actualB/C/H+旧10/旧4copy→五规划普通CLI/独立review/freeze/approval→sharedfreeze→query/cancel→HTTP/单一整合→独立31sourcegate→rootfull/SQLJWT/TCP/TS/resources→授权doc7→finalCLI/local12of13。外审6.1独立pending。

条件算术B+5→B+26→B+28、C+21、旧codeC−10保护、旧sourceB−14不变、priorhistoryH保持；不是当前实际基线创建。当前teams54全冻结；未来old10获准时global54其余44保留，teams45新包仅boundary为登记候选、44包键保留。旧liveHEAD/status/tracked/index只完整记录不baseline等式gate；fixed55字节/target保护必须exact，部署和语言决策不在本批。

## Open Questions

本9方法必要来源无新增未批body阻碍。实际下一基线、旧10窄diff、作者选择、独立连接factory实例/本机端口由root后续批准固定；本轮不创建资源、不自批ready。
