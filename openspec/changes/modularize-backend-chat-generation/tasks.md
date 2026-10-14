# 实施任务

T01、T02 已完成各自范围内的本地验收、文档及中文 Git 收尾，后续按批推进。公共契约、图片适配器、xAI/漫小白/HaoAI 视频协议与提交查询适配器、共享持久化、媒体暂存及 URL/DNS 规则均已在各自批准边界内复核、局部验证并推送。模型目录与服务端报价、结果回写、共享结果桥接、P1 锁定准入准备、P2-G1 纯 graph/fee 计划和媒体远程传输已完成各自限定检查。CP9 已通过独立源码复核、Root 110 项聚焦检查及 run11 的七项专属 PostgreSQL 16.15 案例；中文提交 a0b1256 已推送。P4 已通过独立源码复核及 Root 120 项测试、86 个子断言，提交 a63cbe0 已推送。credential producer2 已通过独立源码复核及 Root 115 项聚焦检查，提交 d31cac8 已推送；G2 准入暂存、费用处理和提交回执已通过独立源码复核及真实联合测试，提交 925d131 已推送。用户偏好 GET/PUT、章节编辑锁 POST/GET/DELETE、公开配置/购买套餐/系统模型三个 GET 已完成独立源码复核与完整后端验收，中文提交 3ed05b2、f34dbea、e3eaae6 均已推送。提示词管理八个接口与用户卡片、资料两个接口已完成两份独立源码复核及 Root 完整后端验收，中文提交 e247404 已推送。后台购买套餐五个接口与系统提示词管理五个接口已完成两份独立源码复核、异常清理及删除兼容修复和 Root 完整后端验收，中文提交 ecaed98 已推送。后台章节、角色、场景、道具、分镜资产及聊天消息十二个只读接口已完成三路并行开发、两份独立源码复核、集成测试与 Root 完整后端验收，查询兼容及两处测试回归问题已修复，中文提交 0679b15 已推送。本批后台系统模型、用户模型与用户提示词十五个方法已通过两份独立 GPT-6.1 Sol/xhigh 源码复核，ASM15-01 修复已完成；后台系统模型、用户模型与用户提示词三个五方法批次（共15个方法）已同层验收并完成中文代码提交推送（6193bb46a2428d7cbe1053210f0c297a42965977）。本批后台系统配置（3）、积分与日志（4）、兑换码（4）共11个方法已由两份独立源码复核联合覆盖；CQ-01/CQ-02 两处 HTTP 测试修复已源审闭合（测试修复），Root full17 完整后端验收通过；聚焦回归为121 passed, 45 warnings in 27.34s；中文代码提交 9423863f960b650a96d44195b7c3de77a303447e 已推送。本批用户管理5个、剧集管理4个、SMTP邮箱管理6个及统计1个，共16个后台方法；三路源码映射（用户15、剧集15、SMTP45，app路径有重叠，合并覆盖73项）由三份独立源码报告联合覆盖，最终源码审查无 P1/P2；中文代码提交 64a3e241a81e86ebd4e4f5ef6a3353d436ebfee8 已确认推送。历史完整后端 run19 为 1699 passed, 278 warnings, 267 subtests passed in 71.03s (0:01:11)，1040 个冻结源项及 runtime registry 前后精确一致，实际 PID 97152、wait exit 0、总耗时 72.714 秒、timeout=false，临时目录和进程组已清理；此前成功及失败运行均保留为历史。原 39 项运行验收通过数仍为 0；T03–T25 的 23 项保持未勾选，局部通过不代表整项或整体完成。实现 gpt-6-luna/xhigh，评审 GPT-6.1 Sol/xhigh；不可用时明确报告、不替换。 模型配置管理9个方法已完成接线，覆盖私有 ORM 持久层、参数补齐与排序、系统升降级、公共连接测试及真实 JWT/SQLite 集成。Root full20 完整后端实测：1737 passed, 370 warnings, 267 subtests passed in 105.03s (0:01:45)；PID 31836，wait exit 0，耗时 107.149s，Native20 运行时1056个源项前后精确；提交前 ports.py 仅移除末尾一个 LF，经独立源审及完整 AST 等价复核，沿用该运行行为结果；其余1055个源项保持一致，暂存格式检查通过；runtime registry 188 项；中文代码提交 eb131bcdc11f43f9cfcf4107d6218e20fca3eebd 已确认正常推送。 本批兑换码2项与会员文件1项以中文提交037cd45推送，Root full22为1784 passed；后台首批登录概览及系统模型列表/创建/编辑以3e07bb4推送（56 files、802 tests、--maxWorkers 1、TSC/build通过），临时SQLite同源浏览器登录、创建、编辑回填、移动导航及logout通过，跨端口保存失败原因未定、provider未验收，9 resource/23 views未完成。provider_quota 6、zhangyuge 3、aistarslab 8共17路由已并行接线，三核心与共享接线经独立Sol源码复核无P1/P2；Root full24为1822 passed、482 warnings、267 subtests in 76.62s；PID 35561 实际 wait exit 0、elapsed 78.27s、timeout=false；771项冻结源快照在该轮测试前后精确一致，runtime registry 为208，进程组与临时目录已清理。随后提交时仅 backend/src/haoai_backend/provider_quota/domain.py 去除一个 EOF LF，经 Source03 的 AST include_attributes=True 等价复核，其余770项 SHA 一致、cached check通过；不声称提交态771项字节全同。中文提交6d1161f已确认推送。最后批次：主入口4与剧集素材维护5共9项由独立 GPT-6.1 Sol/xhigh 复核；Runtime/Series9 shared Source02 与剧集素材维护 Source02 均 Accepted、活动 P1/P2=0，runtime-status4 修复另经 Source03 Accepted、P1/P2=0（其 Source02 曾记录2项P2，保留历史结论）。Root full25 本地后端验收为1883 passed、502 warnings、267 subtests passed（101.17s）；实际 PID96604 waitExit0、elapsed103.155s、timeout=false，794项冻结源项 before/after/registry 逐SHA一致，进程组及TMP清理完成。实际registry PID1797 waitExit0，共217 unique triples，旧208项全保留并新增9项。中文代码提交 ca042b782394bc7d9983181d1221e656f75d42df 已正常推送 main，HEAD=origin/main。新增系列融合素材5接口（融合资产CRUD3、剧集导入/导出2）：GPT-6.1 Sol/xhigh SourceCore02 Accepted、P1/P2=0；SharedSource01 精确覆盖28文件并Accepted、P1/P2=0。Core Native03实测10 passed/0.86s（PID85202 wait0、实际elapsed1.469s，804源项一致、进程/TMP清理）；sharedFocused01实测13 passed/4 warnings/2.03s （PID18352 wait0、实际elapsed2.708s，805源项一致、进程/TMP清理）。Root scoped26仅覆盖全部已提交后端+已接受exchange5 core/shared，并明确忽略 backend/tests/test_storyboard_save.py；815冻结源项（805已提交后端及exchange5候选范围+10未验收草稿包遍历依赖）before/after/afterRegistry逐SHA一致，PID38477 wait0、elapsed82.516s、timeout=false，1896 passed/506 warnings/267 subtests in80.68s。实际registry PID47332 wait0，旧217 triples全保留、新增5、共222，原27个create_app kwargs完全一致。中文代码提交7e37bfa3fb13e7535154bd2e88a1c86496a48978已正常推送main，HEAD=origin/main；仅28个已验收文件stage，save3草稿未stage。此前Save3阶段记录为未验收草稿：Source02 active P1=0/P2=2/P3=1；Native03独立PID39201 wait1，1 failed/50 passed in1.73s（actual elapsed2.177s），815源SHA一致且清理完成；当时不计3项进度，草稿未挂载/未Git并处于独占修复。 最新批次（北京时间2026-10-14；证据收据记录UTC日期为2026-10-13）：Full27历史红结果2 failed、1957 passed、506 warnings、267 subtests（实际PID45197 waitExit1、timeout=false；Guard遗漏）保留；随后Save3 focused 63 passed与Media core focused 65 passed，最终Root Full28完整backend/tests（含Media core测试）2024 passed、506 warnings、267 subtests in84.38s。Full28实际PID29070 waitExit0、elapsed86.256s、timeout=false；828冻结源项before/after/afterRegistry逐SHA完全一致，进程组与TMP清理完成；source snapshot SHA256 792553af1a77a352b8bad26cc27817cabc1059698b2e63e5c5437078865d2ed6。runtime registry实际PID35016 waitExit0、225 unique method/path triples（旧222全保留+3），27个create_app kwargs同序。Save3中文提交7c78993cd8f941675059078c8358bcf4a2fd05e6（29 files，+4623/-19）与Media core中文提交bfbed0ccd6da3b3721d25e217ba61b446bc530e1（12 files，+1889）均已推送，HEAD=origin/main；SourceSaveCore04、Shared02、Media02均Accepted。Media core的12文件及65聚焦测试已接受并提交，但5个路由尚未挂载，仍属未完成项。 其后 Media5、Fee 与 text codec 三批均完成独立源码复核（MediaShared02/Fee01/Codec03 均 Accepted）、Root 验证并分别以中文提交正常推送：Media5 f02576589730c1e377814699e61cef36d607c1e4，Fee 458bfe95ed42bf946ca507626e74242115797773，Codec 97e69221ee614adde85ac5bfebe559decc4e1eaf；main=origin/main=97e69221ee614adde85ac5bfebe559decc4e1eaf。Root Full31 实际全 backend/tests 为 2046 passed、506 warnings、267 subtests in 116.70s；PID73513 waitExit0、elapsed119.224s、timeout=false；834源项在测试前后、runtime registry 前后、当前与全部已提交 Git blobs 精确一致，registry PID83380 waitExit0，进程组和 owned TMP 已清理。Full29 因测试模块重名收集失败（exit2、0 tests），Full30 保留唯一 create_app 28参数严格断言失败（1 failed/2045 passed）；这些历史红结果保留。Full31全backend/tests结果不代表整体后端或迁移验收。 本批单条串行 storyboard 非流式角色保存接口已完成：修复旧路由以 characters= 调用 merged_characters= 服务的关键字错配，保留旧分类计数但不把计数当资源复用证明；失效 existing_id 不建绑定映射，第二阶段重查所有映射，绑定阶段失败仅确认资产提交。Storyboard Save1 Source01 独立源码复核 Accepted（P1/P2/P3=0）；Root focused71 passed（PID50900，wait exit0）；Root Full32 实际全 backend/tests 2054 passed、506 warnings、267 subtests in 87.19s，PID53155 wait exit0，elapsed89.164s，timeout=false；runtime registry PID57828 wait exit0，231 unique routes（固定旧230+1）、28 create_app kwargs same、836 source blobs before/after/registry/current/all committed Git HEAD exact，owned PG/TMP clean。中文提交11414d89ab7bc8dad9128bcd25f3df41959c66f0已正常 push（exec session61933，actual wait exit0）。此为该批次与 backend/tests 层证据，不代表整体后端或迁移验收。 后台用户页本批已接入单用户删除确认与失败/结果未知时保留列表行、最多1000条且继承当前筛选排序的CSV导出，以及编辑页五个固定user_id只读关联页签（credit-logs、model-configs、prompt-configs、tasks、series）。UCSV-01由8个失败、57个通过的真实红测复现后完成serializer与测试修复；Source02 Accepted，新增P1/P2/P3均为0。Root focused04为65 passed；FullNative02的TSC PID14140、Vitest PID14173、build PID17038均wait0，61个文件855 passed，90 modules build 1.29s，139项frontend输入前后精确，owned PG/TMP已清理。PersonalRoughCutEdit 295 warning与>500k chunk warning已知且不阻塞，均非本批新回归。中文提交668e38198bfedf0aee319260a832841929c50759已确认正常推送。 本批后台用户 DELETE 保护约束独立修复已完成：专属 commit_user_deletion 仅捕获 IntegrityError 并映射固定 409，generic commit、HTTP/schema、允许级联范围、正常 200 与认证保持不变。真实 JWT/fresh strict-FK SQLite 覆盖 ai_task、team_member、created_code、external_task_quote 四类晚约束冲突，允许级联 DML 全部回滚并保留原数据；commit 前 OperationalError 及成功 commit 后 ACK 丢失仍为 500。Frontend typed HTTP 4xx 明确拒绝并保留列表行，network/5xx/invalid-response 未知结果处理不变。Source01 whole8 Accepted（P1/P2/P3=0）；Root focused backend 32 passed、frontend 66 passed。FullBackend33 为 2060 passed、511 warnings、267 subtests passed in 186.76s，registry 231 routes/28 参数与 Full32 相同，836 个输入精确，PG/TMP 清理完成。FullFront05 的 TSC/Vitest/build 均 wait0，61 files 856 passed、90 modules build 1.38s、139 项输入精确；Vitest 使用命令行 --testTimeout15000，未改产品 timeout 或断言。FullFront03/04 的 5/1 failures 均为未改 WorkSpace/ChapterBrowser 默认 5 秒超时，历史记录保留；未 await 测试 warning 与大 chunk warning 仍为已知项。中文提交 8f68e1e 已正常推送。 本批后台用户积分列表/编辑与积分流水独立入口已完成：接入既有四个 admin_credit_queries 路由，按积分记录 ID 读写绝对整数余额（支持0/负数），流水支持分页、排序和 user_id 筛选。旧版两个参考资源虽有源码但未在旧 App 注册，不折算旧9资源减少2项，也不代表 U12 完成。Source02 Accepted；UCRE-01/02 经 focused RED（4 项新失败、85 项旧通过）复现后修复。Root 最终 Native 为 89 focused / 879 full，TSC 与 94 modules build 通过；Vitest 使用显式 --testTimeout 15000，默认5秒超时历史运行保留且不覆盖。中文代码提交 499f32f1a035ed495ac2912597b6141fcc19cd20 已正常推送。 本批创作端剧集新建、元数据编辑（仅提交实际修改字段）、名称确认删除及封面multipart上传/风格提示词选择已接入，共14个前端文件；代码中文提交23c657eb5e9719efe6000fffc8ff05e12900469e已正常推送。Source03 Accepted，USERIES-01至04四项P2均Closed，P1/P2/P3=0。RED06为16 failed/59 passed、RED09为2 failed/75 passed；最终focused10为77 passed。Full frontend final01为65 files/916 passed in155.53s，TSC及99 modules build 1.27s通过；Vitest使用命令行--maxWorkers=1 --testTimeout=15000，未修改测试超时配置或放宽断言；153项冻结输入前后SHA精确，owned PG/TMP已清理。 本批创作端章节管理已完成并推送（1c79948）：空白章节创建（title trim、content=[{text:""}]、order=0）、仅标题改名、章名确认删除；创建后刷新并选中新章，改名后刷新并保留当前章，删除后刷新并选择剩余章，未知写入结果不自动重放。核对弹窗展示最新目录并要求用户单独确认后才可重试，滚动目录可经键盘聚焦；Demo按服务实例维护章节增删改与制作记录。Source03 Accepted，UCHAP-01/02/03 Closed，P1/P2/P3=0。Root Focused06 88 passed；键盘回归 RED05 为 1 failed/87 passed，修复后通过。FullFrontend02 实际 65 files/934 passed，TSC通过、100 modules build 1.28s；命令行单worker/15秒timeout，154项输入前后精确，owned PG/TMP 清理。默认5秒稳定性、浏览器、Provider、R2、生产API仍未验收；本批未改后端。

关键路径：B3 的专属 PostgreSQL 验收仅覆盖其 8 个顶层案例、9 个数据库案例及 2 个删除目标子案例。CP9 run11 七项实际 PostgreSQL 案例已通过，覆盖原始迁移与历史记录、DDL 事务回滚、实际约束和跨 schema 外键、调用者提交与独立读取、不可变触发器、轮换/替换/禁用及成功解析四锁顺序；专属数据库、进程和临时目录清理完成，测试加密夹具不等于真实供应商凭据。P1 锁定准入、P2-G1 纯 graph/fee 计划、producer2 同事务快照和 G2 核心暂存/预扣/回执已各自完成限定本地检查；生产 HTTP 生成入口、五类 Worker、CAS/退款取消等整体接线仍待。P4 的本地 HTTP/TLS 与 SDK fixture 已完成限定检查，真实 provider、公网传输和 R2 验收仍待。按固定旧版本共 248 个路由方法，当前已验收登记 231 个、尚有 17 个（backend/main.py 0、series 1、storyboard 10、chat 6），其中后台管理 0 个；Media core 的 5 个路由已完成 core 与 app wiring 挂载并计入231；用户偏好两个、章节编辑锁三个和公开网站配置三个方法已分别复核、完整回归并提交推送；提示词管理八个与用户资料两个方法已独立复核、完整回归并中文提交推送；后台套餐五个与系统提示词五个方法已同层验收并中文提交推送；本批章节、角色、场景、道具、分镜资产与聊天消息十二个只读方法也已独立复核、完整回归并中文提交推送。本批后台系统模型、用户模型与用户提示词三个五方法批次已在同层验收并以中文提交推送（6193bb46a2428d7cbe1053210f0c297a42965977）。本批用户管理5个、剧集管理4个、SMTP邮箱管理6个及统计1个，共16个后台方法已完成同层验收并以中文代码提交推送（64a3e241a81e86ebd4e4f5ef6a3353d436ebfee8）。现有并行批次已收尾，当前按用户最新要求保持单条串行开发线，不再新开并行工作。12 个 React 家族及原 39 项运行验收仍待（原39项通过数0；当前 tasks 为2 checked/23 unchecked，T07/T11/T15/T24整项仍不勾），真实 provider/R2/browser/release 验收仍待；外审 GPT-5.6 Sol/xhigh 保持 OFFLINE_PENDING。共享 app 和路由集合仅由指定 Luna 写者串行接线。 本批17路由保留原191个route triples，3个可选transport仅追加在原19个create_app参数之后。系统提示词/用户管理核心视图与共享导航14文件已由 Prompts Source01、Users Source02 及 shared Source01 逐SHA核验并获 SourceAccepted，0 P1/P2；四项P2曾由6个红测实际复现后修复，Users21项聚焦测试及全量TSC通过。Root receipt haoai-next-admin-prompts-users-root-native-browser-receipt-01.json 记录真实Root执行：60 files、832 Vitest passed，TSC exit0，build 88 modules exit0（JavaScript chunk >500k warning）。真实Target API/JWT/fresh SQLite浏览器确认用户email编辑保存且空credits保持33、gold会员及原始expiry seconds保留；prompt create/edit/readback、390px prompts→users导航与logout通过。用户create仅打开表单后取消，未在浏览器提交；API/Vite PID6169/6896经SIGTERM后真实wait exit0/143，owned PID消失且TMP清理。代码提交 fdcfcbe8fd6cad7dabfd188ec820cf1044a1f606 已确认正常推送。当前用户页核心与新增操作已通过限定源码复核、focused/full Native 及 API/SQL 层检查；本批浏览器验收仍待，不代表whole U12或完整前端完成。用户关联页签、用户管理删除与CSV、独立用户积分列表/编辑及积分流水入口均已接入；旧参考积分资源未在旧App注册，不折算旧9项或宣称U12已完成。本批browser仍pending；bulk-delete、其他资源整页、whole12、wholeFrontend、原39及release仍未完成。Root fresh Target API/JWT/SQLite实测（PID67327，wait0）：五类关联资源双用户隔离、积分流水12条分页、编辑保持33积分/gold会员/原到期秒值、创建201、空用户删除200、当前管理员删除400；此为API/SQL证据，不是browser。Browser native pipe bridge unavailable/untrusted，备用CUA两次30秒超时，browser仍pending。此前Root真实复现的含AI task用户 DELETE 500/IntegrityError 与事务回滚保留数据作为历史证据保留；本批专属删除提交已对 ai_task、team_member、created_code、external_task_quote 四类保护引用限定验证返回 409 且回滚保留原数据，不能据此声称所有保护引用均可删除，也不泛化为所有删除阶段错误均已处理。历史832 passed浏览器核心用户编辑/prompts验收仅适用于旧范围，不扩展到本批。此前Runtime/series维护批次按注册表曾记录主入口剩余数更新为0、series更新为10；原39项整体验收仍未完成（通过数0），whole12个React家族仍待完成。Runtime status 的限定验证显示默认ready配置缺少真实DB/存储probe时返回503；真实生产DB全schema与R2 readiness仍待，不能据此记为生产验收。proxy-image账户认证允许free/expired账户，series维护继续执行会员规则。定时haoai-20无成功运行记录且已PAUSED（误设daily，授权仅一次）；当前按用户最新要求恢复手动单条串行开发，不开启并行或自动化；haoai-20保持PAUSED。此前Root scoped26明确忽略当时未验收的save3草稿测试 backend/tests/test_storyboard_save.py；当时1896 passed仅覆盖已提交后端与已接受exchange5 core/shared；当时save3未验收（Source02 P2=2、P3=1，Native03为1 failed/50 passed），当时不计3项进度，草稿未挂载/未Git并处于独占修复。随后Save3 SourceCore04/Shared02均Accepted，3个保存接口已挂载并以7c78993cd8f941675059078c8358bcf4a2fd05e6推送；Full28全量后端测试通过。 本批创作端章节新建、改名与删除已接入；排序、分镜内容编辑、AI生成及导入/导出仍待，故U02尚未完成。Native为限定focused/full/TSC/build证据；默认5秒运行及浏览器均未验收，既有未await断言warning与>500k chunk warning仍已知，真实云存储封面上传未执行。后端22项既有测试及8 ASGI/2 SQL使用trusted actor夹具，非默认JWT、browser、生产或R2验收。Provider、R2、生产DB、browser、外部GPT-5.6（OFFLINE_PENDING）及release仍待。

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