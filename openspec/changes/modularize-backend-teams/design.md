# Teams Backend Design (Draft)

本稿为本地规划候选设计；实施依赖 root 本批实际基线、普通 OpenSpec CLI 与明确冻结批准。固定兼容来源为 `23403806898550a7668a6ee7c0c457315655c39b`；源窗口/25业务顺序与前端依赖见 source-analysis-01 和 supplement-01；安装库证据另列，不伪称固定Git语料。root 完成 batch31 本地收口后必须重新建立本批实际基线，再核定本批范围与冻结批准。

## Context

完整25路由同时包含管理写入、团队剧集metadata和任务用量只读；拆分按业务族保持每族完整。默认旧Session autoflush=False/expire_on_commit=True，因此赋值、待提交INSERT、即时bulk、commit后隐式读回都有可观察差别。当前React仅已有GET my服务/六字段parser，其它旧UI操作仍不是目标React迁移完成。

## Goals / Non-Goals

目标是25方法保真、同业务Session授权、完整历史守恒、并行独立文件所有权及可测试故障边界。非目标为安全政策暗改、原锁/claim并发缺陷修复、任务/金融/队列写入、外部服务、PG执行、前端执行、上线或语言评估。本批之后仍有完整项目待办；指定外审独立pending。

## Decisions

### 1. One shared contract owner before three family owners

共享作者先建立下列9生产/4测试路径；经root核对公共DTO/ports/policy/SQL reader/clock/quota签名及源码后冻结，再允许三族并行。分析与评审仍Sol6.1/xhigh；代码作者 Luna6/xhigh 的文件所有权和写入权限以 root 本批实施授权为准；规划候选不授予写入权限。三组不得在各自实现中加共享字段或修改别人文件，契约变动先交root统一窄审。

| 唯一所有者 | 精确生产文件 | 精确测试/支持文件 |
| --- | --- | --- |
| 共享契约作者 | `backend/src/haoai_backend/teams/__init__.py`<br>`backend/src/haoai_backend/teams/domain.py`<br>`backend/src/haoai_backend/teams/errors.py`<br>`backend/src/haoai_backend/teams/ports.py`<br>`backend/src/haoai_backend/teams/policy.py`<br>`backend/src/haoai_backend/teams/schemas.py`<br>`backend/src/haoai_backend/teams/persistence.py`<br>`backend/src/haoai_backend/teams/tables.py`<br>`backend/src/haoai_backend/teams/quota.py` | `backend/tests/teams_support.py`<br>`backend/tests/test_teams_shared.py`<br>`backend/tests/test_teams_quota.py`<br>`backend/tests/test_teams_boundaries.py` |
| management 作者 | `backend/src/haoai_backend/teams/management/__init__.py`<br>`backend/src/haoai_backend/teams/management/domain.py`<br>`backend/src/haoai_backend/teams/management/ports.py`<br>`backend/src/haoai_backend/teams/management/application.py`<br>`backend/src/haoai_backend/teams/management/persistence.py`<br>`backend/src/haoai_backend/teams/management/http.py` | `backend/tests/test_teams_management_domain.py`<br>`backend/tests/test_teams_management_application.py`<br>`backend/tests/test_teams_management_persistence.py`<br>`backend/tests/test_teams_management_transactions.py`<br>`backend/tests/test_teams_management_http.py` |
| series 作者 | `backend/src/haoai_backend/teams/series/__init__.py`<br>`backend/src/haoai_backend/teams/series/domain.py`<br>`backend/src/haoai_backend/teams/series/ports.py`<br>`backend/src/haoai_backend/teams/series/application.py`<br>`backend/src/haoai_backend/teams/series/persistence.py`<br>`backend/src/haoai_backend/teams/series/http.py` | `backend/tests/test_teams_series_application.py`<br>`backend/tests/test_teams_series_persistence.py`<br>`backend/tests/test_teams_series_transactions.py`<br>`backend/tests/test_teams_series_http.py` |
| reporting 作者 | `backend/src/haoai_backend/teams/reporting/__init__.py`<br>`backend/src/haoai_backend/teams/reporting/ports.py`<br>`backend/src/haoai_backend/teams/reporting/application.py`<br>`backend/src/haoai_backend/teams/reporting/persistence.py`<br>`backend/src/haoai_backend/teams/reporting/http.py`<br>`backend/src/haoai_backend/teams/reporting/usage.py`<br>`backend/src/haoai_backend/teams/reporting/task_payload.py` | `backend/tests/test_teams_reporting_usage.py`<br>`backend/tests/test_teams_reporting_task_payload.py`<br>`backend/tests/test_teams_reporting_persistence.py`<br>`backend/tests/test_teams_reporting_http.py` |


共28生产、17测试/支持、45新代码。共享tests/teams_support.py由唯一作者维护，三族只消费，分别使用独立owned basetemp。HTTP/persistence/domain测试归所属族；共享boundary最终在唯一集成阶段验证全部82。共享生产模块与共享测试收集不得顶层导入尚未存在的management/series/reporting；build_teams_routers只在最终集成调用时延迟导入三族。共享阶段只实际运行可独立成立的types/ports/policy/DTO/quota/owner DDL与shared冷导入/资源断言。跨族只导入 `teams` 公共domain/ports/policy/schemas/tables/persistence，不导入另一族私有模块，也不依赖旧app、auth内部writer或普通access内部错误。

共享测试可按明确的planned node selector分期：test_shared_import_is_inert、test_shared_public_dependency_boundaries、test_shared_owner_fixture_cleanup属于早期；test_integrated_registry_has_82_methods、test_integrated_all_25_team_methods_are_registered、test_integrated_default_factory_is_inert属于最终唯一集成。上述名字是规划选择器，未来作者须登记实际node IDs；预写集成断言可在函数内延迟导入，不能在collect时要求family已存在。延后项状态明确not_run/not_yet_wired，不能skip/xfail、虚称整个boundary文件已通过或把未运行项计入早期绿测。最终实际运行新boundary全部断言、原8登记测试全部业务/cold/explicit-policy哨兵及root完整backend。

### 2. Public types and explicit runtime dependencies

`domain.py`放Team/Membership/Invite/Series读取快照、TeamConfig五值默认、Clock与assignment结果记录；`ports.py`定义TeamPolicyReader、ConfigReader、Clock、IdSource、InviteCodeSource、JoinQuota和接收业务Session的UoW factory契约；纯应用不持有SQLAlchemy/FastAPI类型。各族ports扩展其真实查询及写意图，不让reporting获得writer能力。

`policy.py`保留six keys、默认admin两权限、list与布尔判断的不同输出，执行source顺序和重复membership SELECT。`persistence.py`提供同Session reader及首次已加载PK快照、显式readback和mutation-plan辅助；普通SELECT不会静默populate_existing，但仍执行来源必要查询以观察行是否存在，commit后清理旧快照再实际读取。配置只读取system_configs first row，不缓存成globalenv。五字段明确为team_created_limit=3、team_joined_limit=3、team_member_limit=20、invite_code_ttl_hours=24、chapter_lock_idle_minutes=15，均按value or default。

`schemas.py`只声明九DTO及team局部422适配；不新增response schema造成序列化收紧。`tables.py`只声明业务DML和report读取所需列/MetaData对象，禁止启动Engine/schema/create_all/reflection。测试/lab完整owner DDL则按固定模型全字段、真实FK/nullable/成员唯一约束建库；invite code不得凭索引补UNIQUE。

`quota.py`实现显式进程内10/hour窗口端口，key是(remote-IP,pathname)，clock是显式epoch读取；默认create_app不实例化quota或Session。auth已有三限额不改、不共享其全局可变bucket。join缺quota返回503且零业务Session，其余配置完整路由无需quota。每个(remote-IP,pathname)的首个合法hit以当前epoch建立expiry=epoch+3600，不按整点小时划桶。后续成功、合法业务失败与超额hit均不延长expiry；now>=expiry才开启新窗。429仅error体和原detail，不用现auth的window_label去拼错次数，也不增加来源未启用的headers。显式内存adapter无需复制旧库Timer/线程。

### 3. Caller-owned Session, queued ORM intentions and actual bulk DML

统一factory/request UoW factory创建一个业务Session；UoW接收并负责当前rollback/close，不自己new Engine，auth维护Session先commit后独立业务Session。读权限、配置、实体和DML必须全部用该Session。

以首次ORM快照计算每个赋值字段的net change，保存planned SET；adapter不得第二次读B后扩大同值写集合或删除本来真实changed的SET。源码autoflush=False时，ORM pending assignments/invite/member/series INSERT、无version DELETE保持到来源flush/commit边界，不能让生成邀请时前一个pending code因Core立即INSERT而参与后一个查重。create team显式flush由立即team INSERT保留，owner member随后待commit。bulk UPDATE/DELETE在原调用点立即执行，之后查询读取其实际效果；series实际UPDATE仅按来源onupdate更新时间，Team/TeamMember没有新造updated_at。

实际dirty PK UPDATE0对应StaleDataError语义为generic500/rollback，不暴露409；同值/早返无UPDATE，不人为制造冲突。bulk0是合法结果；无version ORM DELETE0警告但仍commit。SQL UNIQUE/FK失败、commit前故障、commit后ack/refresh失败均不自动重试。UoW commit应先实际执行待提交Core DML再委托Session.commit；追踪区分attempt、完成和故障阶段。

### 4. Family transaction and readback contracts

| 操作 | commit与读回 |
| --- | --- |
| create team | team INSERT/flush→owner-member INSERT→一次commit→真实PK读team id/name/created_at |
| rename | null/空请求仍一次commit→真实team PK读；同值sourceUPDATE0次 |
| dissolve/leave/remove | 原bulk/ORM顺序→一次commit，仅message；无多余readback |
| role | 同角色返回零commit；真变role+permissions→一次commit→真实member role/permissions |
| permissions | 一次commit，valid permissions预先算好意图echo；不另SELECT |
| create/revoke invites | 一次commit；创建预先构造id/code/expires_at，作废message；无无来源refresh |
| invites GET | only overdue active row时一次commit→全状态降序SELECT，否则零commit |
| join | expired维护commit后400；成功member+invite used单commit→真实team name/id读取 |
| create team-series | 一次commit→实际series refresh，原五字段响应 |
| share/claim | 单commit→实际team或series过期字段读回；同值share仍清series锁 |
| unshare/unclaim/transfer | source单commit/message或body echo；合法无claim unclaim早返零commit |
| reporting5 | 零businessDML/commit，auth维护可能另有写 |

解散先bulk清series.team_id再按team当前series查锁是来源顺序：通常旧锁保留，不预取修复。share/unshare、leave/remove只释放来源范围锁，不清claim，普通access之后如何拒绝必须作为实际集成结果验证。无需调用media协调器或撤销认可，因为本批不改变chapter content/素材媒体身份。

### 5. Reporting pure transforms and SQL read models

reporting.usage负责日期解析、completed/failed聚合、未知模型合并及四种不同输出/排序；reporting.task_payload复制必要纯转换，reporting.persistence提供批量真实读取。成员任务只按已授权target user，包含外团队/个人/孤立task，UserCredit是全账户，不改成team quota。task message_id无FK，usage innerjoins自然排除孤立task；不让chat薄表代替完整owner fixture。

成员任务正常消息分支保留chapter_id字段原值（来源初始化None，仅特定无message任务从request_data填充），failed result截5000，非failed长串/data:置空，request_data截200。batch-optimize/ai-review解析异常按原忽略；不强类型收紧任意request_data。四用量只completed/failed，failed积分忽略，合法负积分保留；无效日期忽略而非400。export是JSON。先把用量中的falsey模型名合并成字面‘未知模型’形成all_models，再以原AITask.model_name IN(all_models)精确SQL谓词查询全库DISTINCT(model_name,model_type)，按实际返回顺序setdefault保留首条type；不加team/time/status限制，不把数据库NULL/空串先归一化后加入type map。model_order使用来源type priority（video/image/chat/optimize-frame，未知type默认9）与(name or '').lower()组成的键稳定排序；相等键保留输入迭代次序，不增加大小写次级键或跨进程固定tie顺序。每个member.models独立使用普通sorted(model-name集合)，不复用model_order键；rows四文本字段与members(-calls,username)排序保持。只读模型type，不读provider配置、不写Excel文件。

现冻结parseMyTeamList的六字段/ISO日历/member_count非负safeInteger/重复id契约仅作为静态consumer事实与实际GET my响应断言；不改旧响应迎合parser，不引入本批frontend执行。用户卡、task request detail、series import属于旧UI额外依赖，不因25接口完工宣称页面完工。

### 6. Final single integration owner and source guards

唯一集成Luna作者最后修改以下9旧路径，只加25方法组合、必要参数及57→82的登记集合/count/名字；不得删旧业务断言、skip/xfail或放松cold env/dotenv/Engine/Session/旧四explicit-policy哨兵。

- `backend/src/haoai_backend/app.py`
- `backend/tests/test_module_boundaries.py`
- `backend/tests/test_series_access_boundaries.py`
- `backend/tests/test_authentication_boundaries.py`
- `backend/tests/test_rough_cut_http.py`
- `backend/tests/test_production_notes_http.py`
- `backend/tests/test_asset_data_boundaries.py`
- `backend/tests/test_chat_data_http.py`
- `backend/tests/test_canvas_data_boundaries.py`


默认新router零启动副作用，GET my在GET team动态路由之前。已有business methods/config规则、authentication、shared identity、SeriesAccess、notes、chapter replacement、所有前端/pyproject不改。future root baseline必须保存上述9原件与四旧EOF文档，不借历史重建缺原bytes的“差分”。52候选来源完整hash是硬绑定；旧共享live Git metadata只完整记录，不设ambient equality gate，不做旧文件恢复。

## Meaningful Validation

共享：owner/member/admin/异常权限、list顺序重复、两次membership查询与first loaded snapshot、config0/None/negative、九DTO/局部firstmsg/extra/null/whitespace、join10/hour的DTO/auth-before-quota、合法失败耗额、IP/path独立；首hit epoch100→expiry3700，整点3600仍限额、3699.999仍限额、3700新窗，成功/合法失败/超额不续期；常态其它24不限额。

management：真实完整owner/nonempty表、GET过期维护、join过期commit再400、invite code重复/碰撞/真实member UNIQUE两连接、create team INSERT/flush顺序、同值assignment0sourceDML仍commit读回、role/unclaim早返、zero-row差异、解散实际锁保留/leave/remove精准锁范围、beforecommit rollback/aftercommit未知与refresh消失。

series：七方法实际SQL、page/clamp/count/头像与权限错误优先，share samevalue仍清锁不清claim、普通访问后效、同snapshot SET与两真实连接interleave、dirty UPDATE0、真正commit后series/team读回与bodyecho，无新CAS/claim锁。

reporting：全非空任务/消息/章节/四类素材/user/credits/queue/ledger与source/private/media行快照，target全部task和总积分/分页/截断/孤立，四种日期/状态/未知/负积分/排序/empty-body/model-type全库精确IN/DISTINCT首条映射；用非空A/a同type、NULL/空/字面未知及全库同名不同type反例分别证明稳定lower-key tie、member.models普通sorted和type过滤，五接口无businessDML/commit。

root：作者专项结果与失败历史分开，Sol完整源审54实际SHA与旧9true diff；完整后端/语法、52fixedshow/source保护；真实25新方法TCP成功加JWT身份/会员/团队角色/ordinary access与旧四个人私有隔离，quota与维护写拒绝口径准确。任何lab故障在私有driver状态或真实delegating adapter控制，不加公开实验endpoint/header。准确driver/server有序trace，fixtureSQL和业务SQL区分，500先核status不强行JSONparse，actual readback定位在成功commit之后。全部owned资源成功清理全AND与root wait/exit，失败receipt独占保留。

## Risks / Trade-offs

保留人数检查非原子、code非unique/消费非CAS、claim不串行和解散锁顺序等旧差异是迁移保真，不是新安全政策承诺。后续如需修复须另change。SQLite两连接控制证明指定可观察顺序，不等于PG实并发或部署；默认内存quota非跨进程服务。返回字典避免不必要strict response DTO改变历史值，同时用实际响应与完整历史行证明。

## Migration Plan

本地规划候选→root新实际基线/固定52/五规划审读与ordinary CLI→root批准→唯一共享作者及公共接口冻结→三个独立族并行→唯一集成→Sol源审→root完整后端/真实TCP/清理→四旧EOF+两新说明/tasks→root最终guard/CLI。指定外审仍单独pending，root本地12/13可推进下一batch；整体项目未完成。

## Open Questions

无需要用户改变政策的未决项。规划候选不表示 CLI、目标安装或测试已经执行；各阶段完成状态以 root 的实际执行收据为准。root必须先核定本批实际基线计数/旧原件、作者公共API稳定性及运行授权，不能把草案或来源52建议当implementation ready。
