# HaoAI-next 完整剩余工作清单与排期

更新日期：2026-10-25（北京时间）

本文依据已确认的 main 基线、历史开发分支和已接受收据校准完整剩余范围。它是唯一的迁移清单，不是新的架构设计，也不代表整站功能已验收。

## 当前基线与计数口径

本文原始范围基线为 main@9df03f0c083236b9b4bb18b722ad95fd9839a144（110 收据确认当时远端精确一致）；102 源码分析基线仍为 backend 5ca522cdc20ec2843fef48cd0d9a68f8ee128c78。102、104、110、113、116 的历史范围与证据归属保留；会员、系统配置、后台和早期全量检查等历史收据仍按各自源码时点解释。功能整合基线为 `e094224e7751ba92c25d22fe17d51e89bb15a264`；Root收据395确认该批整合后 main 与 AI 分支远端均精确指向该提交。e094整合团队管理13个方法、团队剧集协作7个方法及 frame.review 后端链。Root406确认V02移动topbar CSS与清单回写已推送至 main@`2d3ed579e739a2027db05b7681e3796142a2b0da`。其后 main 已整合 reporting 源码、hidden CSS 与 frame.review AI 分镜复核；Root469 收据所记录的 main@`d11dc51e31df3bc8ab3c5f1f3066c854bb56a6e4`及功能分支 tip `f249fd15fdd64782c1eb17d374abe94bd308c738`仍按各自历史时点解释。Root518 确认素材整理十路径已从功能分支 tip `cea87f69c02005ba806289279cc8882c666e852e` 普通合并并推送至 main@`43b10ec9b8c254bc14bc0a036869a115d5757683`。截至本条最新记录，U05.4 的开发与接线为 C/C，V=P、A=U；受影响前端测试、TSC与生产构建已通过，HTTP、GUI与E2E验收仍待补。下文较早的分支未合入状态均保留为当时记录；最新记录区分新增源码、分层验证与尚未完成范围。102 的完整分析文件为 haoai-next-current-full-remaining-inventory-102.json，116 为 haoai-next-root-development-continuation-116.json。两者及原始验收收据均在任务证据目录，本文不改动它们。

固定旧源共有 248 个显式 method/path 声明；backend385 收据中的 create_app registry 为 250 个方法、36 个参数项，旧声明已登记差集为0。这里证明路由登记，不证明请求行为、完整实现、端到端运行或浏览器接受。固定旧后台有 9 个资源、23 个资源视图；当前 React 侧 23/23 个旧资源视图均已实现并接入源码，浏览器全面验收仍未完成。登录和仪表盘独立于这 23 个资源视图。用户积分、用户积分编辑、积分流水 3 个扩展视图单独计算，不冲抵这 23 个旧资源视图。

清单采用彼此独立且有重叠的计数：12 类工作流、51 个 UI 交付行。102 初始快照统计 33 行有开发动作、28 行有接线动作、51 行有验证或接受动作；116 覆盖 U11.2 后为 32、28、51。U12.8/U12.9 完成两行开发和接线后为 30、26、51；U12.6 再闭合开发动作后为 29、26、51。U12.5 与 U12.7 又各完成一行开发和接线后为27、24、51。e094将 U03.1、U03.2、U03.3 各一行的开发与接线义务并入主线，计数为24、21、51；U03.4 闭合一行开发和接线后为23、20、51；U05.2闭合一行开发和接线后，计数为22、19、51；随后 U05.4 再闭合一行开发和接线义务，该时点为21、18、51；U01.3 闭合开发后，该时点为20、18、51；U08.3 两个导出入口的开发和接线义务完成后，当前计数为19、17、51，验证/接受仍为51。U12.6 的运行时消费者接线仍待完成。原 action 统计按 ui_workflows[].deliverables[].remaining_actions.kind 对各交付行逐行去重后计数；非 C 单元格数不是原 action 统计；局部验收不减少 51 行各自的验证/接受义务。生成原始义务 A01–A39 共 39 项、附加规范义务 6 项、原规范修复子例引用 30 项、后台 9 个资源/23 个视图另行计数。这些分母彼此重叠，不相加，也不用于推算完成百分比。特别是 A01–A39 整项完整矩阵接受数为 0；这不表示相关组件为零，已有组件、接线和局部证据仍按下表分别保留。

## 历史画布来源后端交付（2026-10-24）

新增独立canvas-source读取/命令协议，普通旧路由合同保持。分镜文字/插入/删除、三类素材创建/编辑、视频追加/精确槽位修改及服务器签发的创建撤销共8类命令使用单一事务、完整来源version与raw-content CAS；个人剧集延续无章节锁规则，团队剧集必须核当前本人有效锁。无稳定ID旧帧、聊天索引、未知字段和无关孤立故事板保全；显式删除仍触发媒体/认可协调。创建撤销不使用普通DELETE补偿，不按名称或URL猜对象；损坏/非有限来源、歧义身份和已被引用/修改的创建对象均拒绝。

Root659在15路径完整复核后发现并修复了宽松旧JSON解析导致假空快照、跨剧集直接素材聊天引用遗漏，以及必要安全矩阵证据缺口。Root660在完整1631项输入的私有冻结副本运行27个相关后端测试文件，223项通过；涵盖真实JWT resolver、ASGI、合成SQLite、个人/团队锁入口、创建撤销、媒体/便签回滚及提交回执未知。候选源码、main和受保护文件前后一致，所属进程组和临时目录已清理；9条既有警告保留。

这些证明本批后端源码与上述定向行为；不证明真实PostgreSQL并发、浏览器/E2E、外部服务或部署。Canvas来源消费者已由665发布；677已发布新增素材与媒体绑定修复，Root676冻结11文件367项及TSC/生产构建通过。U07.2仍P/P，旧布局显示兼容684与CAV03/04仍待；原重叠计数保持20开发/18接线/51验证或接受。端对端测试按用户安排暂缓。

## 历史进展（2026-10-22）

存储源码基线为 `d26910f24fffc0d22d8ab32a172a1e0dd9e3b9b4`；Root579 核对时，main、`codex/asset-image-upload-556` 及 `origin/main`、`origin/codex/asset-image-upload-556` 均精确指向该提交。该批将非 core 的 `backend.server` host/CLI 接到显式 R2 配置、原 R2 adapter 与同一 `create_app`；鉴权和业务共用同一 Session factory。原始 `create_app` 的默认 `storage=None` 保持不变，缺少配置时存储能力仍诚实不可用；这不是默认启用全部能力的启动器。

U05.5 当前为 P/P、V=P、A=U：三类素材卡片已提供 image-only 上传入口，按 fresh 目录 GET、一次 multipart 上传、明确确认前再次 GET、一次仅写 `image_url` 的 PUT、独立 GET 回读执行；保留原 run/token 身份与迟到 401 防护、unknown/ACK 区分、关窗历史及旧写资格失效，并提供受保护 proxy/Blob 预览与回收、焦点管理和 StrictMode 重放恢复。存储侧的 host/CLI 显式装配已接入主线，但原始 `create_app` 仍默认 `storage=None`，缺少配置时存储能力不可用。音视频、OSS 重试、生成历史、其他媒体引用链以及真实 R2 行为和浏览器验收仍待，本行整体义务尚未闭合。

U08.2 仍为 P/P、V=P、A=U：canonical 视频队列（含视频自带声音）、连续播放、失败重试、protected 上传的 Bearer→Blob URL 与清理、会话目录隔离已接入 main。RCP539-01～06 已经 539-03 源码复核关闭，但这不代表实际浏览器解码或可听播放验收；独立音轨、E2E 不在本批，导出另属 U08.3。开发/接线/验证动作计数保持 21/18/51。用户当前只暂缓 E2E，其他工作按需要继续开发、单测、类型检查和源码复核。

U05.5 图片链的四个前端测试文件共 89 项通过（Dialog 9、AssetLibrary 65、API 12、Preview 3）；未变的 services 测试复用同一源码版本既有结果 1 项通过、52 项跳过。TypeScript 检查与 Root561 生产构建通过。560-03 源码复核关闭 AIMG560-01～09，并保留 AIMG558-01。本批 Root577 的 12 个定向后端测试文件 155 项通过（2 warnings），覆盖合成 SQLite、fake SDK 和 in-process ASGI；同一基线的 `create_app` registry 为 250 个方法、36 个参数项。测试使用隔离的 `python-multipart` 0.0.32 overlay。P2-576-01、02、03 均已关闭。上述均为定向验证，不是全前端/全后端门禁，也不证明真实 R2、PG、HTTP listener、GUI、部署或浏览器行为。

frame.optimize 的取消 component 已在功能分支提交 `d421b8a7d938452ddef56dd32e7c8b8c5d93edb1`；runtime/host consumer 正在 C 的功能分支开发，尚未独立复核或合入 main。N6 取消/恢复、runtime、HTTP 与 UI 仍待继续。Canvas CAV01 当前 7 项修复待独立复核，不代表 U07 已全部迁完。U04.2/U04.3 仍为 P/P；A01–A39 完整矩阵接受数仍为 0，A39 GrillMe 保持 `OFFLINE_PENDING`。

本次进展依据：作者 558-03 收据 SHA-256 `71871d730aabedb1d674e11d2060d4664b8beac57f58a3df49e706d389350a84`；560-03 源码复核 SHA-256 `bdcd01338017c4ea1900ddc5b6b08827f8754d43c6793897869f8007830bb79d`；Root561 生产构建收据 SHA-256 `4bf89777bc0f5c6c8d59d81272b6fd7447e4e16e9c6887d7fcd48e0972fd0cd7`；Root562 发布收据 SHA-256 `da66c5f4b87dafb2be967043007e7014eb3b6985ddb916323dde547080c5b92a`。存储批次依据：作者 574-03 收据 SHA-256 `823a9c95ec0b4549f4f00134605a4ef0a77c952390f969bf679ab3b48104f519`；576-03 源码复核 SHA-256 `ca11053f14a18efbd7e069a7cadaf7dca41b2b114c6042b1281047a65f9c5d1e`；Root577 定向回归 SHA-256 `e5f88e139a6caabcfae21892e5751bd5c11843229a4c46fd5f562d97adfdbfbb`；Root578 发布 SHA-256 `ec82a10c3a880fb3b2f1a2b2acd6f0ecf62021233c7e989af75cd943c1e495ae`；Root579 同步核对 SHA-256 `0a31fb5010480871aaaf89cb64fcf8497dec119b38cce68a2b4996baacc23df3`。frame.optimize 取消 component 源码基线 `d421b8a7d938452ddef56dd32e7c8b8c5d93edb1`；Root542-02 旧 P6 源码复核与发布收据仅作历史记录，分别为 `7d7b01c76b165064d48f811a054085c047ba9f5b270e2f7d3b1bc8e12555391a` 和 `b6f4ad7d7089f594b66bea0a6ab737f08372936c385b0a378eac0a710e3d09e0`。

状态简写：C＝已实现或已接入；P＝部分；M＝缺失；I＝进行中；U＝当前没有足够验证；E＝依赖外部授权或外部环境。验证和接受状态分别以 V/A 标明。实现、接线状态为 C 的行只在相应列记为完成；局部测试、历史浏览器和当前完整接受不可互相替代。

## 历史进展（2026-10-23）

HaoAI-next H 批 commit `cf54bdb` 已合并并推送至 main@`cb89220`。Root607 在与合并后 main 相同的前端源码上通过 Workspace 84 项、TSC、定向 services 和生产 build；九个既有前端文件 182 项及后端 44 项结果仅复用各自匹配源码的既有结果。E2E 仍按用户安排暂缓。这些结果分别只证明对应 gate，不扩展为完整浏览器或业务接受。

随后 `codex/frame-optimize-ui-616` 在 H 中接入 frame.optimize 的类型化 HTTP service、章节批量优化对话框和 ChapterBrowser 入口，包括模型/提示词选择、原文与素材/风格上下文、运行任务接手、unknown 操作冻结、权限核验后的原文恢复，以及终态后按章节与素材真源回读。旧版 batch-optimize 任务仅在当前会话、任务类型及章节身份核对后提供状态读取和取消请求；其任务输出不获得前端写入权。作者初次受影响组合 8 个文件 295 项通过，TSC 和 Vite 生产构建通过；后续 Root618 仍发现真实协议和竞态缺口，该初次结果仅保留为对应历史源码证据。

Root618 复核发现并要求修正六项前端协议与竞态边界：空提示词按后端 `str` 契约发送空串；原文恢复读回按真实章节写入会同步的 storyboard 素材字段精确核验；恢复等待运行任务检查完成并在锁后重查；提交预检/发送与恢复共用章节 single-flight；迟到读取、401 和取消回执按 scope/operation/终态保护；JSON 风格配置只解析有效对象且不把无效容器原文注入上下文。作者定向验证中，Vitest 的原综合集 207 项通过，四项修正前失败均保留原日志并经定向修正后通过；合成 Pydantic 契约验证空串接受、null 拒绝；最终 TSC 与 Vite build 通过。六项修复经 Root 复核后，Root619 在 1,600 项输入的私有冻结副本运行 8 文件组合：306 项通过、exit 0、无超时。候选源码、main 及其受保护文件和冻结输入前后一致，所属进程组与临时副本已清理；日志保留一条 React act 警告。最终 TSC 与生产 build 使用匹配源码的作者结果，build 的大 chunk 提示保留。这些证明当前切片的源码、jsdom 行为、类型与构建，不代表浏览器、E2E、真实 Provider、部署或整行接受。

Canvas U07 当前按下面四个子行更新。H commit 和对应源码的 Root607 验证已进入主线；U07.1–U07.4 仍有明确剩余功能和接受义务，不能将这一批画布交付计为整行闭合。

frame.optimize 后端现已接入唯一的 `backend.server` host/CLI：默认关闭，显式配置 Root 凭据包后，使用同一 Session factory、数据库时钟和 ID 工厂装配注册、准入、worker 与取消路径。新增离线凭据预配及初次登记、AES-GCM 加密材料和实际 aiohttp 流式客户端；注册提交未知只确认原 attempt，不自动重放。注册批已串起逐项单次发送、原结果与收费、写回和收尾，并能在任务租约释放或到期后只接手真正未发送的分镜，接续已有本地 Work，保留未知结果观察和安全取消。认证与业务 Session factory 错配会在建立该任务能力前拒绝。

Root614 在独立冻结副本上运行 45 个相关后端测试文件，408 项通过、2 项旧测试失败。两处分别是旧 N3 用例禁止接手任何兄弟、HTTP 替身缺少新只读端口；仅校准两份测试后，Root614-02 在另一冻结副本上对这两份完整模块复验 28 项通过。Root614-02 的输入中，除两份测试外，生产源码及其他输入与首次门禁精确一致，其余通过证据按原范围复用；原失败日志保留。验证包括合成 SQLite、受控 HTTP、任务恢复与取消、旧图兼容和启动边界；当前默认路由登记新增两个 frame.optimize 入口，旧方法保留。该后端门禁版本的前端 341 项流水线输入与当时 H 批一致，复用对应历史前端证据；后续 React 批量优化变更另由 Root619 验证。

本次仍是有限交付：React 批量优化入口、上下文、运行中任务接手、旧任务只读观察/取消和终态真源核对已在功能分支实现并通过整批源码复核和 Root619 独立 306 项组合；Git 状态以提交记录为准，浏览器、E2E 和部署接受仍待。完整 Group/event N6、失败/到期金融闭环、其他生成家族和统一启动接线、PostgreSQL 竞争仍未完成。U04.2/U04.3、U06.5 仍为 P/P；当前 21/18/51 工作动作计数不减少，A01–A39、附加规范 6 项及 30 个修复子例的原义务与计数全部保留，A39 仍为 `OFFLINE_PENDING`。整体目标继续执行。

## 最新进展（截至 2026-10-26）

当前已发布功能基线为 main@`4406fb469e19e5b10cd2b46fc8f585e17720237e`。本节较早的U08.3记录当时以main@`fe4e9d57d5be8091c4ee74dcfa8d48cf51445ae0`为功能交付基线：Root678/679完成两个真实 Workspace 导出入口的源码复核及12个前端测试文件、325项冻结验证，TypeScript检查和Vite生产构建通过；Root680已将该批中文提交推送至当时main@`a00c6e07021c17949e938e1d4f367c476b9b231e`。U08.3为C/C、V=P、A=U：证据覆盖受控fetch/jsdom/fakeFS，不证明真实浏览器、磁盘落地或E2E。对应收据为 `haoai-next-root-media-export-ui-gate-679.json` 和 `haoai-next-root-media-export-ui-publish-680.json`。

Root685复核了Canvas稳定来源目标快照、旧asset image投影和普通 `frame_message` 兼容；Root689的7个后端测试文件、173项冻结验证通过，并由Root690中文提交推送至当时main@`5371fde958ef6f8e137537a64b20f00630a4747f`。该批时点仅证明对应后端源码与冻结测试范围；CAV03/04消费者、no-ID、owned结果发现/应用HTTP及浏览器接受仍待。对应收据为 `haoai-next-root-canvas-target-snapshot-gate-689.json` 和 `haoai-next-root-canvas-target-snapshot-publish-690.json`。

Root686复核了旧index布局显示与经真实前后命令及receipt证明的插帧连续性，包括重复媒体槽、无媒体章节、手工边及未知字段保留；Root687-02的12个前端文件388项、TypeScript检查和Vite生产构建通过，Root688已将修复推送至当时main@`3314b19a64fb28ebf15f0a9e2077aa90b109ddca`。首次Root687冻结的387项通过、1项失败仍按原日志保留；失败是旧布局显示行为改变后遗留的断言，不是首次全绿。修正该断言后才由独立的687-02冻结通过。对应收据为 `haoai-next-root-canvas-legacy-layout-gate-687.json`、`haoai-next-root-canvas-legacy-layout-gate-687-02.json` 和 `haoai-next-root-canvas-legacy-layout-publish-688.json`。

Root698完整复核并修正严格no-ID越界准入测试夹具，覆盖准入→producer→worker→owned receipt→P5 writer路径及对应身份、准入锁定事实与原始内容CAS边界；Root699冻结13个后端测试文件、277项通过。Root700已将该批中文提交推送至当时main@`0a978b12b6f9c136c0119d3575c0f325b29744f5`，功能提交为 `71bc646ab583deed22836431e4ff6cba47f36873`。这只覆盖合成SQLite与受控 provider/storage stub，不证明真实浏览器/E2E、生产PostgreSQL、真实provider/storage或部署；CAV03 owned-result发现/HTTP应用及团队锁边界、CAV04生成UI消费者仍待。对应收据为 `haoai-next-root-canvas-strict-noid-gate-699.json` 和 `haoai-next-root-canvas-strict-noid-publish-700.json`。

Root702复核了角色素材卡片音频维护：使用现有API上传、试听、明确保存音频URL和音色描述，支持null移除字段；没有新增TTS或voice库。Root703的219项前端测试通过后，TypeScript检查因3处新测试错误失败，原收据保留；修正后Root703-02冻结10个文件219项通过，TypeScript和Vite生产构建通过。Root704已将该批中文提交推送至当时main@`d6ccfdacc684fc6ff45df776448e78ba97a47e08`，功能提交为 `d4c2f0ec2b1ec753cbad1b5592cd942bedb1dbdf`。受控HTTP/jsdom不证明浏览器解码或可听播放；`reference_audios`、provider codecs、完整音频链和真实浏览器验收仍待。对应收据为 `haoai-next-root-character-audio-gate-703.json`、`haoai-next-root-character-audio-gate-703-02.json` 和 `haoai-next-root-character-audio-publish-704.json`。

Root712完整复核前端typed `canvasResultApplications` port及其context/token失效保护；port已接入 `createApiServices` 真实factory，但尚无Canvas UI消费者。它只查询原task/result并严格限制返回schema；non-200响应先取消body再复核身份，响应上限64KiB、body deadline 15秒，不重试、不写入。Root713冻结3个前端文件90项通过，TypeScript检查和Vite生产构建通过；Root714中文提交推送至当时main@`312158792cb21f349345e649508f814f68544ec8`，feature为 `8614c234b0be94a60802e6d18ba95e79cf1f39c6`。对应收据为 `haoai-next-root-canvas-result-lookup-client-gate-713.json` 和 `haoai-next-root-canvas-result-lookup-client-publish-714.json`。

Root716-02完整复核五路径及继承范围后，Root718在实际 `create_app`、account resolver和同厂Session上发布只读查询 `GET /api/chapters/{chapter_id}/canvas-owned-results/{result_ref}/application?task_id=...`。它验证原owner、completed canonical receipt/evidence、原target、当前access和原ApplicationRecord proof，接受合法nullable历史shape，并只返回applied/not_applied只读状态；applied可在来源被手工编辑后继续按历史证明查询，not_applied只表示查询时点未发现记录。该路径不做DML、flush或commit，并rollback/close。Root717-02冻结13个后端文件151项通过；Root717首轮149项通过、2项失败保留，原因是旧module_boundaries登记集遗漏此前已发布canvas-source GET/POST；旧基线实际登记数为254，新增lookup GET后为255。补齐旧两项并校准精确255项登记后，Root717-02才重跑通过，并非首轮全绿。Root718中文提交推送至main@`75fd4aee38f86dcb4e59d4c4fb7a60a1fb9c4ca1`，feature为 `50ecb08552f7269cf368ee459da6200b1b3d0d06`。作者706初次15项通过、2项fixture失败（误假定step含target，而step实际仅含payload binding），修正后17项通过，仅为focused历史证据；完整范围以Root717-02为准。没有真实浏览器/E2E/PG/provider/storage/deploy验证。对应收据为 `haoai-next-root-canvas-result-lookup-gate-717.json`、`haoai-next-root-canvas-result-lookup-gate-717-02.json` 和 `haoai-next-root-canvas-result-lookup-publish-718.json`。

Root728发布服务器canonical章节事实接口和前端接线：42项后端与163项前端验证通过，main@`74e95c0f32d6eb406adbc35a17a38245154b52c6`，feature `a2b2ca5f81fcaa0206a923c8c425138fc31d844a`。Root732发布owned结果候选typed client，205项前端验证及TypeScript/生产构建通过，main@`319d291001a755a225d799a426f80ce612e24bcb`，feature `6a6eb1248ff2dbfcddee4043b22e97095e4ad4a4`。Root740发布generation PUT typed client，222项前端验证及TypeScript/生产构建通过，main@`fd87b3ebd3d002226d4dba5a71cc57322896edf9`，feature `7173ab8f9bf00d0751be78a7b2d7e5fade4fcdc5`。对应发布收据为 `haoai-next-root-canvas-target-facts-publish-728.json`、`haoai-next-root-canvas-owned-results-client-publish-732.json` 和 `haoai-next-root-canvas-generation-apply-client-publish-740.json`。

Root743发布索引化owned候选HTTP、同事务receipt projection、显式有界历史回补和generation v2 migration/readiness实现；Root737-03冻结412项后端验证通过，main@`230d870e998de2d98b9c40efd8330fa9d089a5c5`，feature `594813226bb525632dc5f3f519b8054ccb084217`。该验证基于合成SQLite和受控读写场景，不表示已对真实生产数据库执行migration或历史回补。Root747发布producer与P5对character/scene/prop完整素材目标digest的一致性修复，Root746冻结516项后端验证通过，main@`a3ace4301ce934514503391a021460dddca70f85`，feature `a27ab823f6b2008bce20557bbd567cf18acac4bd`；原4字段锁定freshness guard保持。对应收据为 `haoai-next-root-canvas-result-discovery-publish-743.json` 和 `haoai-next-root-canvas-asset-digest-publish-747.json`。728、732、740、743、747门禁的文件和行为范围重叠，不相加为总测试数，也不据此估算整体完成比例。

C730 generation PUT服务端路径已由Root752-03发布，feature为`71beb11cbe9178d734211180a1eeb596710726bc`，main为`8c2a0442aa77d25886e9c00cf3617c600cca1f30`，远端分支核对精确一致；实现包括同一业务Session内的团队锁校验和条件刷新，这不等于Canvas UI显式取锁消费者或真实PostgreSQL锁竞争接受。Root751首次广回归668/670通过，2项旧route inventory guard测试失败，原收据 `haoai-next-root-canvas-generation-put-gate-751.json` 保留；仅校准测试中的既有路由登记后，Root751-02相关集合670项通过、0项跳过，收据为 `haoai-next-root-canvas-generation-put-gate-751-02.json`。Root752与752-02发布尝试分别因两文件EOF空白检查和staged-path检查停止，均未产生commit；原收据保留。Root752-03最终仅清理两文件冗余EOF空白，并绑定Root741-04字节等价复核，发布收据为 `haoai-next-root-canvas-generation-put-publish-752-03.json`。此前16项通过、11项fixture alias setup error的首次focused记录见 `haoai-next-canvas-generation-put-730/green-focused-01/terminal.json`，修复后的37项结果见 `green-focused-02/terminal.json`。H742五路径Canvas UI候选的Root749首次组合为10个文件240项，238项通过、2项新测试定位断言失败（收据 `haoai-next-root-canvas-owned-results-ui-gate-749.json`）；修正两个定位后，作者750在两个文件18项通过（收据 `haoai-next-canvas-owned-results-ui-review-750/750-canvas-generation-ui.json`）。Root749-02最终冻结复验覆盖10个文件240项，全部通过，TypeScript检查与Vite生产构建通过；Root753中文提交推送至main@`fe4e9d57d5be8091c4ee74dcfa8d48cf51445ae0`，feature为`84ccee200de57a0785de43c70aea65caad7c6de6`，远端核对精确一致。H742 UI候选现具备owned候选查询、按原key发起PUT、unknown后按原GET检查再显式重试，以及Canvas显式锁获取流程；受控测试不证明真实浏览器/E2E、PostgreSQL或部署，U07.4仍未整行验收。749首轮失败、750 focused和早期RED均保留，收据为 `haoai-next-root-canvas-owned-results-ui-gate-749-02.json`、`haoai-next-root-canvas-owned-results-ui-publish-753.json`、`haoai-next-canvas-owned-results-ui-review-750/750-canvas-generation-ui.json` 和 `haoai-next-canvas-owned-results-ui-742/red-01/run-result.json`。

### 单图准入、生成表单与可选 host 接线（2026-10-26）

Root765发布 typed `image.single`准入client，feature `f9d6f90aac82704d47a53dc6805a032629db24d3`，main当时为`d16ca5b046537aeeffdb81bffc8b4edd2701ce7b`。Root764-02状态为FAIL：292项行为检查通过，但TSC因未使用的type import失败，原失败收据保留；764-03仅删除该type import，随后TypeScript检查与Vite生产构建通过，没有重跑292项行为检查。Root768发布Canvas单图生成表单和接线，feature `e35250b4889b649e700f6206f4a065ed41c1dcad`，main当时为`36e1ccc28bb9d487abd008db5bd7fd4f10d062f6`；Root767-04的310项前端组合、TypeScript检查和Vite生产构建通过。

Root771发布显式可启用的 `image.single` server host/worker，feature `b02674c9df29393fb2130fc2e2a41fd5dc6735ba`，main为`4406fb469e19e5b10cd2b46fc8f585e17720237e`。默认保持关闭；启用时使用明确凭据文件和R2配置，本批只接通无参考图单图路径，视频、批量和参考图尚未接入。用户任务并发仍沿用`AdmissionPolicy(max_concurrent_tasks=0)`；正数策略按既有语义fail-closed，媒体传输共享2槽不是用户任务并发上限。

Root770首轮603个用例中601项通过、2项旧子进程import失败；另报告的86个subtests单独计数。770-02针对两个子进程用例复核为1项通过、1项因冷启动期间计时而超时。拆分冷启动与FIFO行为计时后，770-03两个针对性用例通过；Root将同源的601项全量通过与这2项合并记录为603个不同用例通过，并非一次603项全绿运行。相应门禁与发布收据分别为`haoai-next-root-canvas-image-admission-client-gate-764-02.json`、`haoai-next-root-canvas-image-admission-client-gate-764-03.json`、`haoai-next-root-canvas-image-admission-client-publish-765.json`、`haoai-next-root-canvas-image-generation-ui-gate-767-04.json`、`haoai-next-root-canvas-image-generation-ui-publish-768.json`、`haoai-next-root-image-single-host-gate-770.json`、`haoai-next-root-image-single-host-gate-770-02.json`、`haoai-next-root-image-single-host-gate-770-03.json`和`haoai-next-root-image-single-host-publish-771.json`。这些是受控测试、TypeScript/构建与源码发布证据，不代表真实Provider、R2、生产数据库、浏览器验收、用户暂缓的E2E或部署验收。

U07.1、U07.2、U07.3、U07.4仍均未整行关闭；U07.2保持P/P、U07.4保持P/M、V=P/A=U。no-ID生产路径、只读result-application lookup、CAV03候选发现/索引/显式有界历史回补、generation PUT服务端路径及同Session团队锁校验/条件刷新均已交付；owned候选与generation PUT typed client已发布，H742已在部分Canvas UI流程中接入owned候选查询、原key PUT/unknown显式重试和Canvas锁获取。CAV04的无参考图单图生成表单、typed准入client及默认关闭的可选`image.single` host/worker现已发布；owned preview bytes仍待，视频、批量与参考图路径未接入。真实PostgreSQL并发、真实Provider/R2、浏览器验收、用户暂缓的E2E及部署验证未完成。U05.5仍P/P；角色素材音频维护是局部交付，视频、OSS重试、生成历史、其他媒体引用及真实存储验证仍待。U06.6为P/P、V=P、A=U，但不代表新增TTS/voice库或完整旧音频工作流已迁完。U08.4的成片产品范围待明确；A01–A39完整矩阵接受数仍为0，A39仍为 `OFFLINE_PENDING`，51项验证/接受义务保持不变。当前计数仍为19/17/51，按原 `remaining_actions` 口径统计；非C单元格数不等于动作数，也不据此推算整体完成度或新增整体ETA。

## 十二类工作流与 51 项交付

### U01 登录、注册、邮箱验证码、找回与重置密码、账户安全

| ID | 交付内容 | 实现 | 接入 | 验证/验收 | 下一步 |
| --- | --- | --- | --- | --- | --- |
| U01.1 | 认证入口和会话恢复 | C | C | V=P / A=U；页面及认证 API 已有 | 用当前真实 API 补齐注册、验证码、找回、重置、撤销会话和过期会员浏览器闭环。 |
| U01.2 | 简介、登录设备、撤销会话和修改密码 | C | C | V=P / A=P；113 已证简介保存回读、session 列表读取与两账号切换局部 | 基于已证真实 JWT/owned SQL 主链，补验密码提交、完整会话撤销（尚无服务端 logout 请求证据）、脏离开取消及网络/重启/迟到分支；只复核会员合并影响的交错。 |
| U01.3 | 真实邮件发送与重置链接交付 | C | C | V=P / A=E；默认 SMTP sender、受控 TLS 投递、真实 server host 接线及发送回执未知时的 React 恢复已实现并验证 | 真实邮箱交付、浏览器和部署验收仍待；保留 SMTP CRUD 既有密码响应合同和多进程日切的接受边界。 |

### U02 剧集、章节和导入导出

| ID | 交付内容 | 实现 | 接入 | 验证/验收 | 下一步 |
| --- | --- | --- | --- | --- | --- |
| U02.1 | 剧集筛选及新建/编辑/删除 | C | C | V=P / A=U；主要 CRUD 与封面 API 已接入 | 验证当前浏览器的完整 CRUD、封面未知结果、个人/团队权限和筛选保全；缺验证时不重写已实现功能。 |
| U02.2 | 章节创建、改名、删除、选择和排序 | C | C | V=P / A=P；排序及正文保全有历史真实 API 浏览器证明 | 补创建/改名/删除、并发权限和迟到目录分支的当前浏览器证据。 |
| U02.3 | JSON/Markdown 导入与完整 JSON 导出 | C | C | V=P / A=P；历史真实 JWT/Target API/owned SQLite 主链已通过 | 补未覆盖负例和受影响接线；保留未知 frame 字段、原文、URL 和顺序。 |
| U02.4 | 跨章节、剧集、会话保全和权限联动 | P | P | V=P / A=P；scope/generation、稳定 ID、锁确认和编辑保护已存在 | 验证删除、重排、锁、多用户及生成迟到结果的交叉矩阵，并覆盖 U03/U04/U09。 |

### U03 团队、邀请、共享、权限与用量

| ID | 交付内容 | 实现 | 接入 | 验证/验收 | 下一步 |
| --- | --- | --- | --- | --- | --- |
| U03.1 | 团队创建/编辑/删除、详情、成员及角色权限 | C | C | V=P / A=U；13个团队管理/邀请方法对应UI与HTTP源码已整合到e094，并有真实HTTP与局部GUI证据；解散确认结果仍未知 | 补团队解散确认与完整跨会话/失败分支验收；覆盖 owner/admin/member/非成员，不把已接源码扩大为整行接受。 |
| U03.2 | 邀请生成、列表、撤销、加入、离开与配额 | C | C | V=P / A=P；邀请、加入/离开及配额对应UI与HTTP源码已整合，真实HTTP和浏览器正向闭环有局部证据 | 补完整邀请/配额负例、unknown/in-flight/cross-session浏览器分支；授权仍由后端决定。 |
| U03.3 | 团队剧集共享/撤回、认领/取消、创建和转移 | C | C | V=P / A=U；7个团队剧集方法及对应UI/HTTP源码已整合到e094，整行多用户GUI接受仍不足 | 补未覆盖确认/未知/跨页/跨会话、多用户权限及生成/编辑/认可交叉验收。 |
| U03.4 | 成员任务、团队/剧集/模型用量与导出 | C | C | V=P / A=P；reporting 五个 GET 已接入 React 成员任务、团队/剧集/模型用量与日期筛选，SpreadsheetML 双 sheet 导出入口已接；源码、HTTP与桌面/390px 浏览器均有局部通过证据，实际下载字节未知 | 补实际下载字节验收与多用户剩余异常矩阵；局部 HTTP/GUI/视觉证据不扩展为 Excel 下载或整团队接受。 |

### U04 分镜编辑、AI 复核优化与结果应用

| ID | 交付内容 | 实现 | 接入 | 验证/验收 | 下一步 |
| --- | --- | --- | --- | --- | --- |
| U04.1 | 分镜浏览、文字/结构编辑及素材引用导航 | C | C | V=P / A=P；编辑器、引用面板及历史正文/排序主链已证 | 补结构插入/删除、引用清理、未知写入和团队权限的当前真实后端浏览器分支。 |
| U04.2 | AI 复核/批量优化、上下文、部分成功与统计 | P | P | V=P / A=U；React 单镜头 frame.review 有有限真实 HTTP/GUI 验收；frame.optimize 后端已接入，React 批量优化切片已在 `codex/frame-optimize-ui-616` 实现；Root618 六项修正已复核，Root619 私有冻结 8 文件 306 项通过，空提示词另经真实 Pydantic schema 合成验证，匹配源码的 TSC/生产 build 通过。旧 batch-optimize 任务仅提供已核验身份后的只读状态/取消与章节素材真源回读，不获得任务输出写入权；源码复核与独立门禁已通过，浏览器与 E2E 接受未做 | 补完整角色/会话/异常及旧任务范围、其它 family 与其余 AI 入口，统一启动和完整接受矩阵。 |
| U04.3 | 生成结果应用、媒体版本与认可失效 | P | P | V=P / A=U；frame.review 已有两次真实 P5 apply/post_apply fee 与 A→B→C 源刷新；HTTP覆盖排队与处理中取消；GUI有限覆盖处理中取消及真实目标冲突时保留人工正文且不计费；其它 family、媒体版本与认可失效仍待 | 恢复开发后补齐其它结果家族、媒体版本/认可失效及剩余异常矩阵；稳定 ID、no-ID、重复、迟到与回执未知仍分别验收。 |

### U05 素材浏览管理、维护、融合、替换和上传

| ID | 交付内容 | 实现 | 接入 | 验证/验收 | 下一步 |
| --- | --- | --- | --- | --- | --- |
| U05.1 | 角色/场景/道具浏览搜索、文字 CRUD 和引用影响预览 | C | C | V=P / A=P；E17 历史真实 JWT/Target API/合成 SQLite 在角色/场景/道具三类素材上合计完成 POST 3 次、PUT 3 次、DELETE 3 次，共 9 次写入；删除影响两章且保留他人章节 | 保留 E17 的 e6d2 时点证据；只补之后变更影响的当前交叉覆盖，不把历史提交证据扩大为当前全部流程已验收。 |
| U05.2 | 孤立项、重复项、忽略/取消忽略和合并 | C | C | V=P / A=U；素材维护 UI/API 已随十路径整合至 main@43b10ec；owned HTTP 五类维护路由×角色/场景/道具三类与 SQLite 检查通过，GUI 为部分通过 | 集中补齐 fault/unknown、独立 merge POST 计数与无自动重试、403/scope 切换、完整焦点路径及权限/媒体/认可保全；用户当前只暂缓 E2E，必要开发与单元/类型/源码检查继续，最终验收待补。 |
| U05.3 | 融合素材查看/编辑/删除与生成 | P | M | V=P / A=U；准入/claim/应用和 ports 已有，create_app 默认 runtime/storage 为 None | 接入现有 runtime/storage 并做 owned 本地验证，再建 UI、任务回读和失败状态；真实 provider 另行验收。 |
| U05.4 | 跨章节素材替换 | C | C | V=P / A=U；素材卡片、逐章影响预览与选择、再次读取核对/明确确认、串行单次 POST、独立 GET 核对、逐章 ACK/unknown/未发送状态及显式只读恢复已接入；5 个受影响前端测试文件 134 项、TSC 和生产构建通过 | 补 HTTP、GUI 与 E2E 验收，并继续核对权限、媒体与认可保全；用户当前暂缓 E2E。 |
| U05.5 | 图片/音频/视频上传、OSS 重试、生成历史和媒体引用 | P | P | V=P / A=U；d26910f 存储源码基线已在 main。图片侧已将 image-only 三类素材卡片入口接入 main，完成 fresh 目录 GET→multipart 上传→确认前 GET→仅 image_url PUT→独立 GET；含原 run/token 与迟到 401 防护、unknown/ACK、关窗历史与旧资格失效、受保护 proxy/Blob 预览回收、焦点及 StrictMode 恢复。560-03 复核关闭 AIMG560-01～09；前端四文件 89 项通过、TSC与Root561生产构建通过。后端非 core host/CLI 已接显式 R2 配置→原 adapter→同一 create_app，鉴权/业务共用 Session factory；raw create_app 默认 `storage=None` 不变，缺配置时存储能力不可用。Root577 定向后端 155 项通过，registry 250 方法/36 参数 exact，multipart 0.0.32 隔离 overlay；角色素材卡片音频上传/试听/明确保存音频URL与音色描述、null移除字段已接现有API，Root702复核，Root703-02冻结10个文件219项及TSC/生产构建通过。此音频链不证明浏览器解码、完整音视频流程或真实R2/PG/HTTP验收；视频、OSS重试、生成历史、其他媒体引用链仍待。 | 完成音视频、OSS 重试、生成历史及其他媒体引用链；补真实 R2 配置下的存储验收和浏览器验收。 |

### U06 AI 助手、生成、提取、Worker 与恢复

| ID | 交付内容 | 实现 | 接入 | 验证/验收 | 下一步 |
| --- | --- | --- | --- | --- | --- |
| U06.1 | 聊天消息、文本流与 source bubble | P | P | V=P / A=U；chat CRUD/统计、detached SSE、可选 Task 绑定和 stream 财务已有 | 配置真实 text runtime，补 React 聊天操作、断流/失败/任务回读和来源 bubble；按原协议矩阵补缺口。 |
| U06.2 | 两步分镜生成、报价、SSE 预览和确认保存 | C | C | V=P / A=P；真实 JWT/Target API/owned SQLite + FakeRuntime 主链已通过 | 保留主链收据，补受影响的失败、断流、原 key 恢复和权限分支；FakeRuntime 不代表真实 provider。 |
| U06.3 | 图片单张/批量与视频单条生成入口 | P | P | V=P / A=U；媒体准入、graph、计费及 image-single 无参考图同步执行已有 scoped PG/result 证据 | 完成批图、单视频、参考图/异步和其余 provider family consumer；接 React 状态和原 key 恢复；保留 A01–A12/A21–A29/A31–A38 原矩阵。 |
| U06.4 | 提取角色/场景/道具及单镜/全镜生成 | P | P | V=P / A=U；六个提取/判断路由与React确认/逐镜保存已接入；642为三类保存增加章节内容/剧集来源、阶段权限重查及SQL CAS；Root643完整复核及修复重审通过，Root644-03按项目默认配置前端309项、类型与构建通过，精确同源后端185项与实际保存/CAS证据保留 | 继续补图片/单镜/全镜生成、跨章节清理并发及完整接受；分阶段提交不等于整请求原子性，21/18/51与整行状态保持不变。 |
| U06.5 | Worker 可靠性、金融、结果应用、取消和重启恢复 | P | P | V=P / A=U；image.single、frame.review 各有局部实现；frame.optimize 原 intent 到期控制与提交边界修复已通过 Root630 独立176项后端定向测试，并由 Root631 中文提交推送至 main；完整金融/恢复接受仍待 | image.single 的有界PG证据见E179/E181/E185，frame.review 另有e094/PG392范围证据；保留各自层级。继续完整Group/event N6、失败/到期金融闭环、PG竞争和进程crash恢复、其它family、React控制、六项附加义务和完整A01–A39接受。 |
| U06.6 | 音频相关原工作流 | P | P | V=P / A=U；角色素材真实卡片音频维护已接现有API：上传、试听、明确保存音频URL和音色描述，并以null移除字段；Root702复核，Root703-02冻结10个文件219项、TSC和Vite生产构建通过。Root703首次219项测试通过后，TSC因3处新测试错误失败，留档后修正并通过703-02复验。此处仅覆盖角色素材音频维护，没有新增TTS或voice库；`reference_audios`、provider codecs、完整音频链和真实浏览器解码/可听验收仍待 | 对照并完成 `reference_audios`、provider codecs与完整音频链；补真实浏览器解码/可听验收，区分角色素材维护和TTS，不扩写为新增voice库。 |

### U07 多节点创作画布和结果回填

| ID | 交付内容 | 实现 | 接入 | 验证/验收 | 下一步 |
| --- | --- | --- | --- | --- | --- |
| U07.1 | 图结构、缩放平移、节点/边选择和布局 | P | P | V=P / A=U；章节图、布局、缩放平移及节点/边选择已接入，完整旧行为对照和浏览器矩阵仍待 | 补齐旧图编辑行为与真实章节/素材图、多用户、键盘和窄屏浏览器验收。 |
| U07.2 | 节点编辑、手动连线、卡片/便签和撤销重做 | P | P | V=P / A=U；本地便签、卡片、手动关系和 50 步 undo 已接入；CAV02 来源后端已实现 GET/POST、8类命令、事务/CAS与受控创建撤销，Root659完整复核/修复复核及Root660冻结27文件223项通过。665已发布真实Workspace来源消费者，677已发布新增素材与媒体绑定修复；Root676冻结11文件367项及TSC/生产构建通过。688已发布旧index布局/手工边显示兼容和经证明的插帧连续性（含重复媒体槽与无媒体章节）；Root686复核、Root687-02冻结12个前端文件388项及TSC/生产构建通过。首次687的387项通过、1项旧断言失败保留为历史。CAV03/04和整行验收仍待 | 完成选中状态、未知字段、会话/锁边界、CAV03/04及整行验收；补齐真实浏览器接受。 |
| U07.3 | 画布文档/视口保存、版本冲突和失败恢复 | P | P | V=P / A=U；opt-in guarded-v1 SQL id/version CAS、2 秒防抖和配对 receipt 已接入；unknown/conflict 会停止自动保存。legacy blind-writer convergence、PostgreSQL 及完整浏览器接受仍待 | 补 legacy writer 收敛、PG 并发与完整浏览器失败恢复矩阵；维持未知写入停止自动重放。 |
| U07.4 | 画布触发生成及 owned 结果应用 | P | M | V=P / A=U；authority/result reader 与 writer 已有。Root698复核并由Root699/700发布严格no-ID准入→producer→worker→owned receipt→P5 writer路径；no-ID生产路径已交付。Root714发布typed `canvasResultApplications` port，Root718发布只读application查询GET；Root728发布服务器canonical章节事实，Root732发布owned候选typed client，Root740发布generation PUT typed client，Root743发布索引候选HTTP、receipt projection、显式有界历史回补与v2 migration/readiness实现，Root747发布producer/P5三类素材完整digest一致修复。相应局部门禁分别为42后端/163前端、205前端、222前端、412后端、516后端项；范围重叠，不相加。C730 generation PUT服务端已由Root752-03发布；Root751-02相关后端冻结集合670项通过、0项跳过，首次668/670的旧inventory guard失败保留为历史。H742五路径UI已由Root749-02冻结复验240项通过并包含候选查询、原key PUT、unknown显式重试和锁获取；765/768/771的发布与分层验证见上节，现已接通无参考图、默认关闭的`image.single`路径。视频、批量与参考图未接入；用户任务并发沿用0策略，媒体2槽不代表用户并发限制。CAV04单图路径已接线，owned preview bytes仍待。U07.4仍P/M、V=P/A=U。真实生产DB migration/backfill、PostgreSQL并发、真实Provider/R2、视频/批量/参考图、owned preview bytes、浏览器验收、用户暂缓的E2E及部署验收仍待 | 基于已接通的无参考图`image.single`补齐owned bytes预览与视频/批量/参考图能力；验证owner、完成任务、当前权限、稳定目标/CAS和重复应用不增revision；补真实PostgreSQL锁竞争、生产DB migration/backfill、浏览器验收、用户暂缓的E2E及部署接受。 |

### U08 连续预览、粗剪、媒体导出与实际输出

| ID | 交付内容 | 实现 | 接入 | 验证/验收 | 下一步 |
| --- | --- | --- | --- | --- | --- |
| U08.1 | 粗剪排序、纳入/排除和保存 | C | C | V=P / A=P；Erough 历史 fixture 覆盖 8 类、双用户/409/未知回读 | 补当前真实 Target API/SQL、多用户保存及源修改联动；保留已验编辑器事实。 |
| U08.2 | 视频/音频预览与连续试看 | P | P | V=P / A=U；canonical视频队列（含视频自带声音）、连续播放、失败重试、protected上传Bearer→Blob URL与清理、会话目录隔离已接入main@`d1b047fbc93da0626401fa0e7ce8535b9b798d1b`；Root552合并后受影响门禁与TSC/生产构建通过，539-03源码复核PASS并关闭RCP539-01～06。实际浏览器解码/可听播放未验证；独立音轨、E2E不在本批，完整导出另属U08.3。本行仅部分完成，未闭合全部义务。 | 补实际浏览器解码/可听播放验收与独立音轨；E2E按用户暂缓安排后补；导出按U08.3单独推进。 |
| U08.3 | 保存粗剪的素材批量导出与下载 | C | C | V=P / A=U；章节逐镜选择与已保存粗剪两个真实Workspace入口均已接入，共用冻结导出清单、sealed session transport、同源受保护/公开匿名字节读取、逐项状态与目录/浏览器sink；Root678/679冻结12个前端测试文件325项通过，TypeScript检查和Vite生产构建通过，Root680中文提交推送至当时main@`a00c6e07021c17949e938e1d4f367c476b9b231e`。证据限受控fetch/jsdom/fakeFS，不证明真实浏览器、磁盘落地或E2E | 补真实浏览器与文件落地验收；继续明确U08.4单成片目标与范围，素材逐项下载不计作单个成片。 |
| U08.4 | 成片/实际输出交付 | U | M | V=U / A=U；既有材料仅证明素材导出，没有证明合成为单个成片 | 明确目标产物并核对旧入口；若需成片，补媒体合成/音频/输出和恢复链，不能把素材下载称为成片。 |

### U09 制作缺项、便签、镜头认可和续作位置

| ID | 交付内容 | 实现 | 接入 | 验证/验收 | 下一步 |
| --- | --- | --- | --- | --- | --- |
| U09.1 | 私人便签/认可/续作写入与定位 | C | C | V=P / A=P；Eresume 历史 fixture 覆盖 14 例 | 补当前真实 API/SQL、多用户隔离及跨章迟到响应；保留已有局部证据。 |
| U09.2 | 缺项清单、筛选和导出 | P | P | V=P / A=U；notes/rough-cut 状态投影已有，旧 episode 有导出函数但未全读 | 对齐旧清单字段和真实数据来源，补当前未接操作/导出以及缺图、缺视频、认可重验流程。 |
| U09.3 | 媒体变化撤认和多用户一致性 | P | P | V=P / A=U；后端 source/media/notes 同事务协调器已有 | 确保 upload/generation/canvas/source writer 实际使用协调器；验双用户撤认、媒体 identity 和 notes CAS。 |

### U10 任务分页、详情、进度、取消和历史

| ID | 交付内容 | 实现 | 接入 | 验证/验收 | 下一步 |
| --- | --- | --- | --- | --- | --- |
| U10.1 | 本人任务分页、请求/结果/费用详情 | C | C | V=P / A=P；E17 历史浏览器证明 18 条结果帧、凭据脱敏；过期会员结果可读 200、请求受限 403、会话保留且钱包/账本/任务数不变 | 按未覆盖的分页、身份切换、页缩小、详情刷新和交叉权限补验；E17 是 e6d2 时点的历史局部证明，保留其证据层，不扩展成当前全流程接受。 |
| U10.2 | 运行任务进度/部分结果、取消与恢复 | P | M | V=P / A=U；旧状态/取消兼容面有，新控制链与 UI 缺失 | 接真实 worker 控制链和观察/取消 UI；完整验证 A28；cancelling 状态不能当成已停止或已退款。 |
| U10.3 | 任务历史、统计及后台/团队任务视图 | P | P | V=P / A=U；task_observation、generic/admin tasks 和团队 reporting 后端已有 | 先稳定共享 reporting 契约，再分别实现普通任务、团队及后台消费者；验证边界、来源、费用和导出一致性。 |

### U11 模型/提示词、供应商额度、积分会员与兑换

| ID | 交付内容 | 实现 | 接入 | 验证/验收 | 下一步 |
| --- | --- | --- | --- | --- | --- |
| U11.1 | 个人/系统模型与提示词 CRUD、复制、排序 | C | C | V=P / A=U；CreatorSettingsPage 和独立 port 的读写/排序已接入 | 用当前真实 API 覆盖系统/个人权限、脏稿、未知写入及排序冲突；不把连接测试等后续动作重算成 CRUD 缺失。 |
| U11.2 | 会员、套餐/联系说明、兑换及父页余额更新 | C | C | V=P / A=P；9df 已提交推送，97-02 finding 闭合；83 文件/1248 tests、TSC/build 全绿；113 两账户真实 SQL/HTTP 浏览器主路径通过 | 保留双账号拒绝重复码、简介保存回读、键盘返回/重开、390px 无横向溢出等证据；脏离开确认已弹出但取消因工具超时未证，密码提交及网络/重启浏览器变体待验，不能记为账户全完成。 |
| U11.3 | 模型连接测试、系统/个人转换、额度和账户偏好 | P | M | V=P / A=U；后端 ConnectionTester、transport、角色转换、额度/偏好接口已有 | 增加 React 操作/API；用本地 fake/owned transport 验权限、密钥处理和旧请求隔离，真实外部余额另层。 |

### U12 管理后台资源与运营配置

| ID | 交付内容 | 实现 | 接入 | 验证/验收 | 下一步 |
| --- | --- | --- | --- | --- | --- |
| U12.1 | 后台登录、会话隔离和仪表盘 | C | C | V=P / A=P；独立后台身份和 dashboard API 已接，209 已证真实管理员登录、dashboard 和 GUI 退出；服务端 session 撤销未证 | 补过期/撤销、权限及失败恢复；390px 无横向溢出已证。209 曾见共享 68px 侧栏文字拥挤，移动 CSS 已在 c8f 修复，仍待新 GUI 复验。 |
| U12.2 | 系统模型 list/create/edit/delete | C | C | V=P / A=U；旧 3 个视图及删除均已接入源码 | 用当前 SQL/API 浏览器验字段、密钥脱敏、未知写入回读和权限。 |
| U12.3 | 系统提示词 list/create/edit/delete | C | C | V=P / A=U；旧 3 个视图及删除均已接入源码 | 验分类、原文保全、系统权限和真实 API 浏览器行为。 |
| U12.4 | 用户管理、积分/流水及关联记录 | C | C | V=P / A=U；旧用户 3 视图已接入源码；积分/流水/关联记录是额外扩展读取 | 验管理员自删、外键、未知写入与用户隔离；关联记录只读不等于旧全局剧集/任务资源。 |
| U12.5 | 后台剧集 3 视图和全局任务 1 视图 | C | C | V=P / A=U；剧集功能与只读 TaskList 已接入。232/233 为剧集局部 HTTP/GUI 证据，233 不是全量浏览器验收；移动 CSS 在 c8f 修复后仍待 GUI 复验。全局任务列表真实 HTTP/浏览器验收仍待。 | 补 TaskList 的真实 HTTP/owned SQL 与浏览器验收，覆盖筛选、服务端分页/排序、当前 query 全量 CSV、身份隔离和迟到导出；剧集补移动视觉复验与剩余全流程/权限验收。 |
| U12.6 | 系统配置 list/edit | C | P | V=P / A=P；32b3 已提交页面、19 字段、真实 API 与 host 接线；194 源码复核、196 前端门禁、201 真实 HTTP/owned SQL 通过，209 局部 GUI 及独立 SQL 已证 | 页面与 API 接线已完成；运行配置消费者桥接仍待，不能由保存 metadata 推断 CORS/frontend_url/worker 已生效。补脏离开取消保稿、in-flight/unknown/cross-session GUI；移动 CSS 已源码修复但尚待新 GUI 复验，其它消费者实际生效按原边界验。 |
| U12.7 | SMTP 账户 list/create/edit/delete、启停和计数 | C | C | V=P / A=U；SMTP 账户管理前端与 host 已接入并由 78a4d9f 推送；真实 HTTP/owned SQL、浏览器 CRUD 尚未验收，未证明真实 SMTP 发送 | 补列表和 CRUD 的真实 HTTP/owned SQL 及浏览器验收，覆盖密钥保留/替换与未知退出；发送行为仍归 U01.3，secret 不回显，真实邮件另需适用授权。 |
| U12.8 | 兑换码列表/筛选/导出/批量创建/删除 | C | C | V=P / A=P；129-02/03 与 148 源码复核完成；137 owned HTTP/SQL 覆盖后台方法、鉴权及 used/expired/unused 删除，144 桌面浏览器已验列表/创建 | 补验筛选、导出和 GUI 删除；CSV 下载工具超时未证，移动端及 unknown/in-flight/cross-session 浏览器负例仍待；137 的 API 删除不等于 GUI 删除，生成至用户兑换/账本闭环另待。 |
| U12.9 | 套餐 CRUD、启停、分类与排序 | C | C | V=P / A=P；129-02/03 与 148 源码复核完成；137 owned HTTP/SQL 覆盖管理 CRUD/公开读取，144 桌面浏览器已验列表、创建、编辑及 0/null/false 保存回读 | 继续补停用显示/排序及未知、在途、跨会话等浏览器负例；不虚构真实支付。 |

## 后端、运行时和跨模块工作

路由、目录和文件存在只证明登记或实现片段。以下家族保留已开发部分，工作落在真实 consumer、接线和缺失的事务/浏览器证据上。

| 家族 | 已有实现/接线 | 仍需处理 |
| --- | --- | --- |
| B-SERIES | 剧集/章节/分镜来源、访问策略、章节锁和导入导出模块已按显式 UoW/HTTP 接入 | 补跨模块 PG、多用户和对应 UI 接受；不因旧排期过期而重写。 |
| B-ASSETS | 基础素材 SQL、维护/替换模块已存在 | 接入 media upload application 与 fused runtime/storage，完成高级素材 UI 和存储链。 |
| B-PRODUCTION | 便签、粗剪和媒体协调器已有 | 新写入者消费协调器；补真实媒体预览、字节导出/输出及多用户证据。 |
| B-ACCOUNT | 账户、偏好、模型、提示词和兑换逻辑已有；会员批已并入；651/655已接真实SMTP adapter与验证码/重置密码发送、邮箱回执恢复 | 各账户/运营工作流仍按 U01/U11/U12 补验证；真实邮箱交付、浏览器与整体验收仍待，owned SMTP sink不代表真实邮箱。 |
| B-TEAMS | 团队管理/邀请13方法与团队剧集协作7方法已随e094进入主线；reporting五个GET及成员任务、团队/剧集/模型用量和日期筛选已接入main@6ea3e6f，SpreadsheetML双sheet导出入口已接 | 补导出实际下载字节与多用户剩余异常矩阵；保留团队权限和剧集流程的完整接受缺口。 |
| B-TASKS | 旧任务 read/status/cancel、通用/后台任务和 provider status 已有；只读全局 TaskList 页面已接入 | image.single 原 intent recovery/due 消费及 frame.review 独立后端链已实现。E179/E181/E185覆盖所列 image.single 有界范围；PG392覆盖 frame.review 的真实owned取消/重放五类场景（late正价与0价）。TaskList HTTP/浏览器、完整服务重启、其它任务家族及其 UI 仍待；不将两个局部PG范围提升为完整任务工作流接受。 |
| B-ADMIN | 后台 API 家族已有，React 登录、仪表盘和 23/23 个旧资源视图均已实现并接入源码 | 完成全面浏览器、真实 HTTP/SQL 与跨会话/未知结果验收；剧集已有局部验收，TaskList 与 SMTP 真实 HTTP/浏览器仍待。3 个积分扩展视图独立。 |
| B-GENERATION | 六入口准入、合同、凭据、图/计费；image.single受限runtime与recovery/due、文本SSE、两步分镜局部浏览器证据，frame.review 后端链/React单镜头入口，以及frame.optimize显式host/React批量入口已接 | 完整Group/event N6、其它family worker、媒体生成与其余AI入口仍待；metadata/准入/claim和局部到期修复不能替代完整producer/consumer或A01–A39接受。 |
| B-INTEGRATIONS | DB/provider/storage/quota/download/SMTP ports 与部分 adapter 已存在 | 补本地组合根和真实 caller；逐项区分 owned fake/loopback 与外部账号验收。 |
| B-ARCH | 扫描 106 个 domain/application 文件；多数模块使用 port/UoW 边界 | 收口 aistarslab application 直连 persistence/provider、generation_credentials application 直连 SQLAlchemy/concrete repository 的边界，同时保留现有同事务/主体约束。 |

| 集成 | 当前证据 | 后续工作/边界 |
| --- | --- | --- |
| INT-LOCAL-COMPOSITION | create_app 显式配置，lifespan 可拥有 image-single service；frame.review各边界也采用显式依赖注入；没有全特性 launcher 证据 | 建可复现的全特性本地组合根，明确 DB/auth/providers/media ports、readiness 和 shutdown；不依赖旧 app/env 自动发现或隐式建表。 |
| INT-DB | SQLAlchemy UoW、generation migration/readiness、credential migration 和 scoped owned PG 收据存在 | image.single 到期 recovery/due 的owned PG范围见E181/E185；frame.review I07的五类owned PG范围由PG392确认通过并清理。两者不覆盖完整服务/PG重启或真实数据库，不连接或写真实数据库。 |
| INT-PROVIDERS | 部分真实 transport adapter 和显式 ports 已有 | 完成剩余 adapter、loopback/fake 协议、原 credential principal/snapshot 绑定及配置消费者；实际账号/付费调用另授权。 |
| INT-STORAGE | 非 core 的 `backend.server` host/CLI 已消费显式 R2 配置，装配原 R2 adapter 并交给同一 `create_app`；鉴权与业务共用 Session factory。原始 `create_app` 默认 `storage=None`，缺显式配置时存储能力不可用。Root577 的 155 项定向合成 SQLite/fake SDK/in-process ASGI 检查通过，registry 250 方法/36 参数 exact，使用隔离 multipart 0.0.32；P2-576-01～03 已关闭。 | 接入四类媒体能力的其余消费者，完成 fused runtime-storage 与 MediaStore 组合，并补权限、大小/MIME、DNS、重定向、内容绑定和资源清理验证；继续补齐音视频、OSS 重试、生成历史与其它媒体引用能力，并在具备真实配置后验收 R2 行为。本批不证明真实 R2、PG、HTTP listener、部署或浏览器结果。 |
| INT-SMTP | 651已将SMTP adapter接到同一host、验证码与重置密码；Root650定向288项及owned TLS SMTP sink验证通过。655邮箱回执恢复经Root654前端74项、类型和构建通过 | 补账户管理的当前完整HTTP/owned SQL与浏览器验收；真实邮箱交付需适用授权，owned sink不是外部邮箱接受。旧后台密码返回合同未在本批改为服务端脱敏，前端不回填秘密。 |
| INT-QUOTA | provider quota/balance transports 与连接测试组件已有 | 接入 Creator Settings 配额/连接/角色动作；owned transport 本地验密钥处理，真实余额查询另授权。 |
| INT-DOWNLOAD | 兼容GET禁用路径和批量URL解析接口已有identity resolver；固定旧R2兼容签名函数也原样返回URL，signed不是权限或下载成功证明 | 接实际字节读取、章内视频选择与保存粗剪导出队列、文件命名/顺序/取消/重试及落地回执；受保护同源与匿名公开媒体分开读取。素材逐文件下载不等于U08.4单个成片。 |
| INT-ARCHITECTURE | 106 个应用/领域文件已有静态 import 盘点 | 修复上述直接 persistence/provider 边界，并复核现有职责与同事务约束。 |

## Generation 原义务、附加义务与证据引用

完整 A01–A39 原始检查保留在 [chat-generation/spec.md](../openspec/changes/modularize-backend-chat-generation/specs/chat-generation/spec.md)；公共端口约定见 [public-contracts.md](../openspec/changes/modularize-backend-chat-generation/public-contracts.md)。本清单不复制协议全文、不重写 requiredCheck、不把接受过的修复子例当作原义务通过。完整矩阵接受仍为 0；逐项组件状态和剩余 consumer 已分别出现在 U04–U06、B-GENERATION、集成与下表。

A39 指定的 GPT-5.6 Sol/xhigh Grillme 外审仍为 OFFLINE_PENDING，尚未使用；普通 GPT-6.1 Sol 独立复核、作者测试、Root 测试/浏览器均不替代它。不探测、替换或伪记通过；release 授权与实际发布另层。该外审待办不阻止继续本地开发。

### 六项追加规范

| 引用 | 追加要求 | 映射原义务 | 当前状态与剩余主线 |
| --- | --- | --- | --- |
| N-APPEND-01（spec.md:730） | 生成后继续真实可 claim | A17/A18/A24/A26/A27/A29 | records/repository/P5 bridge 已有；真实 review/optimize producer、Work、新 phase claim/消费和已付交付续作仍缺。 |
| N-APPEND-02（spec.md:739） | 无 Task stream 的原金融证明 | A13/A14/A15/A27/A29 | HTTP stream 金融已有；补原始矩阵、single-use start、unknown commit、重复退款和无 graph 恢复。 |
| N-APPEND-03（spec.md:748） | poll 唯一预占与原 unknown truth | A24/A25/A26/A29/A32/A33 | repository、HaoAI codec/adapter 和 policy metadata 有；真实 planner/poll 消费、reservation、预算及原结果语义仍缺。 |
| N-APPEND-04（spec.md:757） | 原 policy 优先于取消 marker | A24/A27/A28/A29/A33 | 合同/schema 有；当前旧 cancel status-only 不满足新策略，需同锁、结算先行、全部 Work 停止和事务验收。 |
| N-APPEND-05（spec.md:766） | owned 完成结果和应用原子 CAS | A03/A17/A18/A21/A22/A24/A26/A38 | result facade 与 frame/asset writers 已有；真实 HTTP/worker/UI consumer、权限/目标校验和原子回执仍缺。 |
| N-APPEND-06（spec.md:775） | 四类资源 guard 与原 principal 绑定 | A10/A25/A26/A31/A32/A34/A35/A36/A37 | credential registry、四类 port 合同及媒体组件已有；剩余 family/launcher 接线与原矩阵验收仍需完成。 |

### 保留的 30 个修复子例引用

下表只保留原义务引用与来源 SHA-256；具体检查文本以原规范为准。编号和哈希来自 102 清单的 accepted_repair_subcases，不增加 A01–A39 通过数。

| 原义务 | 已接受修复子例引用及 source SHA-256 |
| --- | --- |
| A15 | A15:accepted-repair-subcase-1：62cec1ec0df95e2a00c9b80e3f9dd58618adbac4d1a63d6bd3c01fadc542ea51；A15:accepted-repair-subcase-2：96ec8c9c3d2e4acea13df44e3f68b046ffe355cf25dd5cf7bed14da61bbf9d3b；A15:accepted-repair-subcase-3：aeb6b13fe44c5bd1d7a4365987dc7ac24d61b377d4593c3105553f33ff043876 |
| A17 | A17:accepted-repair-subcase-1：e1296daa5d204d0a219f0f1cc40cb4ecf0944342e47ab1cbbf04ea70c9a6e176；A17:accepted-repair-subcase-2：0afd94b08ad1a1b851e478193216e5cf72afcf3e75136bc38f307ec478226d0a |
| A18 | A18:accepted-repair-subcase-1：69992e395d377cb4ba5f549ff1bd08192d8a2425cf005400ea55c63374767270；A18:accepted-repair-subcase-2：707f50d04c9599a6edb5c6bb97ba3c5bc75a1325ddafb5245c337588fad47a6c |
| A24 | A24:accepted-repair-subcase-1：cb6197d295b8917db91f3f74ba3225e9b1db352033aac3f79b07eafe4ab7ebce；A24:accepted-repair-subcase-2：20ce5e68e41d4c9e6ecc996934ff83c536159c1e6ea0f50369b4932e89c9a477；A24:accepted-repair-subcase-3：8497d180f0f0ee50040c30164839d5f569088fda34494987f9137f1b75578f9c |
| A26 | A26:accepted-repair-subcase-1：8f5b4d20ace62c6a8f29edf104a380d4a3534a103728f2d26440d811821bb10e；A26:accepted-repair-subcase-2：171b6d148334dc43041cca940de5bf78e01caa258084586f1cf1432d7b2e8e1c |
| A27 | A27:accepted-repair-subcase-1：2d6fb92edf76bc4d2ac2d8f4251fe4936d0c7ff6e3e815d9a97ca850a0508a91；A27:accepted-repair-subcase-2：2a940efd0e851bdcdad4332f46ad16ac3aa02fe98befc4e1b93d2a818b5b03ad；A27:accepted-repair-subcase-3：140f03faa1f5cf2c0e1353d772613ff66ee78b5845c33f69bef58324a9d0bbca；A27:accepted-repair-subcase-4：1814dbb5c4f148fd4ee9b57bec0f15075a4f654d2253e2e5b524b460de37edf5；A27:accepted-repair-subcase-5：0bd84c0306fc07775d5f9e6d35033c57d54ac98967fd7659ce9ad664890333bc |
| A28 | A28:accepted-repair-subcase-1：a51ddd5647d617fc8bdeac825e43daba20272f8b394427d90400ab73c7b07944；A28:accepted-repair-subcase-2：53b2954832d55ba76cac2de051764e2648069f1fa182a983cd5f58e8719d9d4a；A28:accepted-repair-subcase-3：7caff3faae4db9b5b3767db8d83226d4ed4075d1f38a3ed11d2e6c9403e5bb3f；A28:accepted-repair-subcase-4：3e333ee3b755afc4d6bada6c27c4ff0751fc3a37ddbbf29ab3c2f38ca3951b9a；A28:accepted-repair-subcase-5：b5928326b7562fdf06f97ba6a7a15b557074b5e339e7f3da630f57fd1e2d0e3d |
| A29 | A29:accepted-repair-subcase-1：a6fc525aac2b8e842c6e273988a542c9c51df569553112c89e2d8eea06690770；A29:accepted-repair-subcase-2：3cc5642f0f6fc9fbc6160b5afaf872e25471d741db15bdf8bc4ed7e06e827889；A29:accepted-repair-subcase-3：75114bb0292df54dbb5bbd05c2ffe1df4513bf085704ca02dd089751f2b6ac1a |
| A33 | A33:accepted-repair-subcase-1：e8d5e30f0566f1c2002fa81b3a52814ec87a127725da5cd357b8363c118b3df3；A33:accepted-repair-subcase-2：0939ee0203543ae70f1014c3a77764156a04795a58c1863fe1de6d679dcd6f5f；A33:accepted-repair-subcase-3：30da6ed110af4b45e1e4a4650f969955735f84e7b725fd3f56d3071e6171e136；A33:accepted-repair-subcase-4：2ab9109372ab3ecc3a663f2916a42c229eecacc078acd4a077bbfadcc8caba20 |
| A39 | A39:accepted-repair-subcase-1：7324579e87b5f7d09f2fbdc1e1a9b3f167e7645e60ba667aa43bffae72eab2b6 |

## 当前可界定工作包与排程边界

下表动态工作包状态截至本次清单更新。仅列现阶段可以界定的三个工作包。它们范围不同，可在既定窗口内选择并行，不能相加成整体剩余工时或整体完成日期。全目标其余未深审工作量与 calendar ETA 均为 unknown。

| 工作包 | 范围 | 粗略有效工时/信心 | 当前状态与下步 |
| --- | --- | --- | --- |
| GEN-W01 | image.single 原 intent 的独立 recovery/due 消费者 | 16–32 小时；低信心（原工作包估算，不是当前剩余工时或整体 ETA） | 已实现并提交推送 1e07f90bb203b8ff6e7b534eb0e8b22a12f5ddfc，E166 remoteExact=true。E160 三参数 SQL 边界 RED 为 2 failed/1 passed；加入 queued 恢复后 E163 为 3 passed/277 deselected。E165 全后端回归为 2434 passed、558 warnings、267 subtests passed（actualWaitExit=0、timeout=false、916 输入前后 exact、进程组及 TMP 清理）；create_app registry 的 250 methods/35 parameters 与 E88 一致。owned PG 合成批次 E179（foundation/A/G/H，4/4）与 E181（foundation/B/C/D/E/F-A/F-B，7/7）固定 cases-177；E185（foundation/B/C/E，4/4）固定 cases-183。三份收据均对应 1e07 源、同一 916 项 manifest，前后源码 exact 且 worker、PG 和临时目录清理通过。E181 F 是两个不同 worker PID 从同一持久 PG 图恢复；时钟由受控 SQL UTC 值推进，不是墙钟等待两小时，也不证明 PG/完整服务重启或任意 crash 恢复。仍待 P6 cancel control、poll、可验证原输出恢复、其他四类 worker consumer 和完整 A01–A39 验收。本工作包 16–32 小时仍是原始估算，不代表当前剩余工时或整体 ETA；本地回归不等于浏览器或真实 Provider 验收。 |
| 后台兑换码/套餐 | U12.8 与 U12.9，合计 5 个旧视图 | 8–16 小时原始估算；中低信心 | 已完成并由149收据确认提交推送 eca1978；源码复核见129-02/03、148。143是基于142冻结的前端门禁（87 files/1297 tests、TSC/build 均实际 exit 0）；137为独立真实 HTTP/owned SQL 验收，144为独立桌面浏览器验收。最新全前端门禁284另行覆盖当前 main。剩余证据缺口见末尾；8–16小时保留为原估算，不作为剩余工时或ETA；不包含真实支付。 |
| 团队管理/邀请 | U03.1–U03.3 | 16–32 小时；低信心（原估算） | 13个管理/邀请方法与7个团队剧集方法已在e094整合到main；GUI376历史收据仍为PARTIAL。GUI399已通过卡片V01视觉检查，GUI405已通过移动topbar V02窄视觉检查；解散确认对话框、跨页正向及完整跨会话场景未验。U03.4 已在待合入分支完成实现与接线，V=P/A=P；实际下载字节及多用户剩余异常矩阵待验。该估算是原工作包估算，不表示当前剩余工时或整体ETA。 |

其余 family、画布、媒体导出、真实 provider/SMTP/R2 和完整 A01–A39 接受未逐项深审，因此整体剩余工时和 ETA 保持未知，不复用 10 月 9 日的周数或日期，也不按 routes/files 数量线性外推。

共享 writer 协作仍按北京时间每日 12:00–14:00、20:00–次日 09:00 的窗口；窗口外在安全边界串行收口，不新建自动化。共享 reporting 契约与 U03.4 成员任务/团队报表消费者已接入当前 main 源基线 6ea3e6f；U10.3 的其它任务消费者按各自边界保留待办。GEN-W01 的 image.single 原 intent recovery/due 消费已落地；frame.review 后端链亦已接入。用户明确恢复后，再按真实消费者边界处理 frame.optimize、React AI入口、其它 family、结果应用、媒体、画布及缺项工作流。

## 特殊证据边界与最终阶段

E17 的源实现提交为 e6d2f66f88d660a7bf4550f2062495b878426ca0；它在真实 Target API、合成 SQLite/JWT、本地浏览器范围内证明了角色/场景/道具三类素材上合计 POST 3 次、PUT 3 次、DELETE 3 次，共 9 次写入、跨两章只移除目标引用并保留其他章节/帧内容、任务详情 18 条结果帧脱敏，以及过期会员可读结果而原请求返回 403。它证明该历史分支的局部通过；当前共享 client 已继续更新，需保留历史证据并只验证受后续变化影响的分支。

U11.2 的最新证据来自 9df：MEMBERSHIP97-I01 已由 97-02 finding 闭合；完整前端门禁 83 files/1248 tests、TSC、build 全绿；113 在两账户、owned SQL/真实 loopback HTTP 上通过兑换主路径、同码同/异账户拒绝、简介保存回读、键盘返回/重开、390px 无横向溢出和账户隔离。dirty-leave 确认窗已出现，但因浏览器工具超时未验证取消行为；密码提交、网络/重启 browser 变体仍未完成。故账户工作流不是全部完成。

局部状态另外保留：E17 的直接跨域连接偶发差异仍未定位根因，同源 proxy 路径已验；章节锁局部 badge/交互有真实本地浏览器验收。两项都不扩展成全站 CORS 或全部锁流程已验收。

后台会员管理批次的验收层彼此独立：136 是基于前端冻结135的门禁（87 files/1295 tests，TSC、Vitest、build 实际 exit 0）；137 是基于同一前端冻结与 backend 5ca522c 的真实路由/owned SQL 检查，15 组、32 个真实 HTTP 请求通过，后台9种操作成功响应 exact 200，鉴权覆盖401/403，套餐/兑换码字段覆盖 0、null、false 和空字符串，used/expired/unused 码删除均通过；删除后两账户积分13/4、账本0、会员free不变。143 是更新的前端冻结142门禁（87 files/1297 tests，TSC、Vitest、build 实际 exit 0）；147 仅将11个新目录文件各去掉一个EOF LF，148证明可逆，因此143未在格式收尾后的字节上重跑。144 是独立桌面浏览器正向验收：套餐 list/create/edit、兑换码 list/create，0/null/false 保存回读和确认后离开；最终 SQL 为套餐5、兑换码6，两账户积分13/4、账本0、会员free不变，并留有桌面截图。144因硬TTL以exit 1结束，但进程组、端口、临时目录和凭据已清理，不把整个run记为PASS。CSV下载工具等待超时未验，移动端、unknown/in-flight/cross-session浏览器负例、GUI删除及生成码到用户兑换闭环仍待；137的API删除不能替代GUI删除。

系统配置 list/edit 批次已提交推送至 main@32b3f721a8436a270b58b73c2a2c138ca8dc6b5a。194 为源码复核；196 基于 233 项前端输入，90 files/1324 tests、TSC/build 均 actual exit 0，输入前后 exact、进程组和 TMP 清理通过。201 为真实 loopback HTTP/owned SQLite，21 请求/11 检查通过，19 字段转换、隐藏/非目标配置及 wallet/ledger 保全分层核对；统计经既有 BalanceReader fake 隔绝默认 127.0.0.1:8765，不代表真实余额上游。209 在同一 233 前端/916 后端冻结上完成 nonce/PID 和 HTML 探针，Root 实际 GUI 已验管理员登录、dashboard、19 字段装载、5 字段保存确认及保存后 disabled；独立 SQL 仅这 5 字段改变，其它目标字段、非目标行、users/wallet/ledger 不变，0 存储与回包默认 3 差异已证。桌面及 390px JPG 已留，client/scroll/body 宽均 390、无横向溢出；209 当时所见 68px 共享侧栏拥挤后已由移动 CSS 源码修复，仍待新 GUI 复验。dirty 确认弹窗已见，dismiss 工具超时，未证取消保稿；in-flight/unknown/cross-session GUI、服务端 logout 撤销仍待。两个 owned tab 均已关闭并核 absent、viewport 已 reset。209 supervisor 实际 exit 0 且 lifecycle-stopped，233/916 source/snapshot/Git/保护尾 exact，groups/ports/TMP 及凭据清理通过；Root UI receipt 明确 partial，生命周期成功不等于全部 UI 接受，更不等于部署。历史204 phase A与 backend AIreview 279 在当时尚未并入 main；后续 frame.review 合入情况见本节后附的最新整合记录。A01–A39 完整矩阵接受数仍为 0。

后台剧集、全局任务和 SMTP 账户页已接入当前 main：剧集为后台 3 视图，另有 232/233 局部 HTTP/GUI 收据；c8f 修复移动导航与剧集表格可读性，尚待 GUI 复验。全局 TaskList 于 3e8c525b41f064b0feecc60fe74005f6931c579f、SMTP 账户管理于 78a4d9f877eaf8c964a4f7c69a656b5b9bd5f5a5 合入并推送；TaskList 与 SMTP 的真实 HTTP/owned SQL 和浏览器验收仍待，SMTP 真实发送不在此证据内。由此 23/23 旧后台资源视图源码已接入，不等于浏览器全面验收。

前端284全量门禁收据 haoai-next-root-admin-final-full-frontend-284.json（SHA-256 a7531451de9d20d3e517c6b934bd037d02346e079bc557f22ab98fb64e10d747）显示 263 项输入前后 exact；TSC、Vitest 102 files/1404 tests、build 均 actual exit 0，进程组清空且临时目录移除。历史274全量门禁为 1403 passed，唯一失败是同一 Workspace 用例在默认 5000ms 下以 5493ms 超时；282 使用临时隔离配置重跑单例与 Workspace 75 项，未复现，但不是根因定位。284 使用原配置与默认 5000ms 全量通过；根因仍未证，不能称已定位修复。

生成系统的多个 source worker type仍未全部接通；image-single与frame.review分别有已验证的局部后端链，frame.optimize、批量/视频/提取等其它 family consumer 与React AI入口仍待。准入、策略、metadata或claim不能替代真实producer和consumer，也不能替代完整A01–A39矩阵。image.single 的真实 ASGI auth→fake Provider 500→reconciling→原 deadline 后 failed/refund 路径已由本地合成 SQLite/fake 用例覆盖；非空 unknown 前后保留原 first/deadline、及时成功 retained、BYOK zero 不建 wallet、唯一退款后合法消费的 replay、commit-ACK/rollback 和 cooperative shutdown 也有合成用例。这不是浏览器或真实 Provider 证据。PG owned 合成收据 E179/E181（cases-177）与 E185（cases-183）在同一 916 项 1e07 源清单上通过各自选定范围，覆盖具体到期锁/双消费者、退款回滚与 ACK 确认及 shutdown gate。E181 F 是两个不同 worker PID 读取同一持久 PG 图，不代表 PG 或完整服务重启、任意 crash；受控 SQL UTC 时钟不是墙钟两小时等待。frame.review I07 PG392的局部结果见最新整合记录；其它任务 family 的 cancel/control、poll、可验证原输出恢复仍待按各自范围完成。

全部 backend 与 12 类 React 工作流、跨用户/权限/故障恢复及适用 owned 验收完成后，最后才做后端语言评估。真实数据库写入、资金操作、Provider/付费调用、SMTP 真实发送、R2 账号/存储及生产部署仍是各自独立授权的验收层；本清单的局部源码、测试、HTTP/SQL 或浏览器证据均不扩大这些授权。

## 团队管理与邀请开发批交接（2026-10-17）

隔离分支 `codex/team-management-294` 已实现并接入团队管理、成员角色与六项权限、邀请生成/读取/撤销、加入/离开等 13 个方法，包含 Workspace 导航、同步草稿保护和 SeriesPage 团队目录失效通知。团队剧集协作 7 方法、reporting 5 方法及其页面仍待后续。本批尚未合入 main，因此前文按主分支统计的 27 行开发、24 行接线、51 行验证/接受义务保持原口径，不提前扣减。

源码复核305发现同团队切换面板后旧邀请码读取可迟到登出并丢弃新草稿，以及共名按钮样式缺少局部作用域。修复306先取得4项真实 RED，再在修复后取得4项 GREEN；独立复核308确认两项关闭。相关团队/适配器/SeriesPage 定向套件为56项通过，Workspace真实适配器与假fetch宿主回归为2项通过、75项按名称过滤；TypeScript检查通过。上述宿主证据属于假传输测试，不代表真实后端HTTP、JWT或SQL验收。

Root完整前端检查309固定271项输入，TypeScript通过，Vitest为1267通过、162失败：154项对应本地HTTP夹具启动被拒绝（listen EPERM），另8项超时。随后310仅修正本地监听执行权限，使用相同源码、原项目配置和默认5000ms测试超时；该次Vitest达到420秒进程上限后被终止，没有完整汇总，不能记作全量通过。旧界面超时根因仍未证实。独立生产构建313实际exit 0；各次检查源码前后均一致。310即时进程组检查为未清空，312后续实测该组已消失，无需发送清理信号；自有依赖链接目录、构建目录和临时目录已移除。

本批以独立分支开发检查点保存，当前main基线仍为 `bedffc03f94adb8368dfd6bdd38c99eac9e53eff`。完整前端回归、这13个管理/邀请接口的真实后端HTTP与owned SQL、多账号权限/会员/JoinQuota，以及桌面和移动浏览器验收均待完成；尚未部署。按用户要求，保存本批提交并推送后暂停整体开发，后续恢复时从这些待验收事项接续。

### 恢复开发与前端回归（2026-10-19）

用户已恢复开发。相同源码的首次完整回归为1428通过、1项Workspace超时；该次失败收据保留。单例CPU采样显示，重复全局角色查询中的无障碍名称与样式计算占用较多时间；这只说明该采样的热点，不证明全部偶发超时的原因。随后将三处Workspace流程和续作流程的相关查询限定到已有具名区域或筛选组，并为粗剪用例既有异步断言补上await。原有交互、卡片数量、隐藏列表无障碍断言及异步标题等待均保留。

冻结271项输入后的完整前端检查 `haoai-next-team-resume-20261019-query-full.json` 实际通过：104个测试文件、1429个用例，TypeScript与生产构建均exit 0。使用原配置、单worker和默认单条5000ms超时；900秒外层进程期限未触发。源码前后精确一致，自有进程、依赖链接目录、构建输出和临时目录均已清理。独立复核318通过；仍保留既有act和构建chunk警告。本次只修改测试查询与异步断言，不改变产品行为。

截至该阶段，下一步是这13个团队管理/邀请方法的真实Target HTTP、owned SQL、多账号权限/会员/JoinQuota及桌面、移动浏览器验收；当时团队分支尚未合入main，主分支计数因此保持原口径。该状态随后由e094合入更新，见下方最新主线记录。本次前端通过不代表整个项目、浏览器或发布验收完成。

### 团队真实HTTP与浏览器验收进展（2026-10-19）

前端测试修复已以中文提交 `ee9f9cb603508324538ae81a4418df1201421c39` 推送至团队分支，远端核对一致。夹具319经独立复核320发现两处与真实权限/邀请码保留语义不符的断言，以及进程组清空核验缺项；集中修复后第二轮复核通过，产品行为未改。

Root收据 `haoai-next-root-team-http-321.json` 对应该提交、271项前端及916项后端冻结输入：实际create_app、真实bcrypt登录/JWT/session、显式InMemoryJoinQuota与owned临时SQLite上，13方法、67次请求、8组HTTP/独立SQL检查全部通过，实际exit 0。覆盖角色/六权限及清空、邀请码生成/使用/作废/过期、会员/权限/数量/限流拒绝、退出/移除的锁与保留认领、解散的原detach顺序；指定合成用户、钱包、账本和非目标团队图守恒。请求状态包含200/201/400/401/403/404/422/429/503。进程组、端口和自有临时目录已实际清理，前后源码一致。显式注入配额不证明默认运行装配已启用配额。

浏览器323已实际验证两账号登录、创建/改名/目录/详情、GUI生成码到加入闭环、复制与关闭回执、角色提升、六权限保存及空权限清空、普通成员与零权限管理员入口限制、作废/读取邀请码、退出/重新加入/移除成员。独立SQL确认相关成员/权限/邀请码状态；空权限按既有后端语义存NULL、接口返回空数组。未保存名称的离开取消与退出取消已保留页面状态。390px下权限页布局可读，无横向溢出，移动端返回及团队入口可用；桌面与移动截图已保留。

解散确认的控制结果仍未知：323尝试取消时控制调用超时，恢复后团队确已解散；保护用户/钱包/账本行哈希前后相同，但不能把这次取消或有意确认记为通过。独立诊断324未发现确定的产品源码缺陷。全新夹具325分步复验读取到confirm，随后dismiss返回对话框已失效，AX读取又超时；独立SQL确认当时团队/成员/邀请及保护行与操作前精确一致，但未取得取消完成回执，仍不能记为通过。没有为自动化修改产品确认逻辑。

323与325的前后端进程组、端口、私有凭据和临时目录已实际清理；323标签已关闭、视口已恢复。325自有标签4关闭调用超时，最终列表仍可见该失去服务的标签，标签清理单列待处理。浏览器完整在途/未知写/跨会话负例，以及解散取消和明确确认的独立复验仍待。该段记录e094合入前的隔离状态，后续主线状态见下节；未部署。

### 主线整合与 frame.review 后端链（2026-10-19）

Root 收据395确认中文整合提交 `e094224e7751ba92c25d22fe17d51e89bb15a264` 在 main 与 AI 分支远端均精确一致。团队管理/邀请13方法及团队剧集协作7方法对应的UI与HTTP源码现已接入主线；U03.1、U03.2、U03.3 的实现与接线状态均为 C/C。它们的验证与接受状态仍按各自证据边界保留在表中：HTTP与GUI有局部正向证据，解散确认、跨会话/unknown 分支及完整团队剧集多用户接受仍有缺口；U03.4 reporting在该记录时点仍待，后于2026-10-20整合main，见后附记录。

同一整合提交包含显式依赖注入的 frame.review 后端路径：真实准入→claim/intent/result→P5应用与费用→I06 recovery/C01 shutdown→I07用户取消。Root385验证的944项后端源与整合后后端逐项exact；394确认可复用该源对应的2568 tests、267 subtests及registry250/36结果，未在e094重新执行。Root392的五类真实 owned PostgreSQL I07 场景通过，late case覆盖正价与0价，且资源清理通过。这些结果只覆盖该后端子路径；frame.optimize、React AI入口、其它 worker family和完整 A01–A39 接受仍未完成。

前端371门禁为109个文件、1474项通过，TypeScript与生产构建均exit 0。GUI376历史收据仍为 PARTIAL；Root399已通过团队卡片V01视觉检查：1265px桌面六类操作自然换行，390px移动视口内六类操作可见且无横向溢出，导出长提示可折行。Root405报告 `PASS_V02_CSS_VISUAL_ONLY`：390px下六个导航控制完整横排；320px下合法20字中文剧集名面包屑自然换行且无页面横向溢出；1265px桌面header保持row布局、移动按钮隐藏、六类卡片操作保持正常。405使用已审363/334与旧916 backend fixture，不是main 944整站验收。两批仅证明限定视觉；导出下载文件字节、团队解散确认对话框、跨页正向及完整跨会话场景仍未验证，不把局部视觉结果提升为整体GUI PASS。

全量矩阵边界保持不变：A01–A39完整矩阵接受数为0，A39指定的 GPT-5.6 Sol/xhigh Grillme 外审仍为 OFFLINE_PENDING。上述源码接入、局部后端/前端检查及 owned PG 证据均不提升整站完成度，也不产生新的整体 ETA。

### 团队 reporting 主线整合与隐藏面板验收（2026-10-20）

reporting 源码与 hidden CSS 此前位于独立分支 codex/team-reporting-408；当前 main 源基线 6ea3e6f06e0f945a5ab90dc40f7470cb7591084b 已包含该批内容，此前的待合入状态已解除。U03.4 五个 GET、React 成员任务、团队/剧集/模型用量、日期筛选与 SpreadsheetML 双 sheet 导出入口已实现接线；U03.4 记为 C/C，V=P/A=P。该行闭合后 UI action 计数为 23 行开发、20 行接线、51 行验证/接受义务；51 个交付行和 A01–A39 完整矩阵接受数不变。

Root409-02 报表源码复核 PASS；Root410-02 在 CSS 修复前基于112个前端文件，1503项测试、TypeScript 与生产构建均通过，之后产品差异仅为该 CSS 修复；Root419 CSS 复核 PASS。Root414 使用当前944项后端、真实 JWT 与 owned SQLite 的 HTTP 检查 PASS，16张业务表哈希未变，认证维护单独计数。Root422 基于当前292项前端与944项后端完成限定真实浏览器检查：owner任务分页10+4、余额102、负/零积分、日期结果4/9/2、特殊模型结果3/9/1、空团队切换0/0/0；管理员可见任务、成员可见用量，普通成员权限请求被拒。桌面和390px下隐藏面板均为display:none且高度0，整页无横向溢出，表格自身滚动。

Root422 点击导出按钮未见错误，但实际浏览器下载字节未知：Root415下载事件等待超时，Root422未复用等待流程，不能据此宣称 Excel 下载验收通过。导出字节与多用户剩余异常矩阵仍待验证；不将 U03.4 局部证据提升为全团队、整站验收或部署。AI417 的当前后端、前端与真实验收状态已在后附记录更新；该功能仍未达到整行接受条件。


### AI 分镜复核分支与分层验收（2026-10-20，收尾后暂停）

AI 分镜复核的两个中文提交 794cb3750923ce3a734dd326dbc91122009f805f 与 ba45f2f153dc5897c62574fd74512a61ee7400ac 已推送至独立分支 codex/frame-review-user-flow-417；当前 HEAD 为 ba45f2f153dc5897c62574fd74512a61ee7400ac，尚未合入 main。main 当前源基线仍为 6ea3e6f06e0f945a5ab90dc40f7470cb7591084b。Root434 后端全量为2576 passed及267 subtests；原 create_app registry 250/36 保持不变，实际 frame.review factory OpenAPI 为252。Root426 使用相同298项前端源码完成114个前端文件、1519项测试、TypeScript与生产构建门禁，均通过。

Root450 实际 loopback HTTP 有限通过：同一稳定分镜 A→B→C 两次各调用 owned fake provider，真实持久结果、P5 apply、post_apply 与费用/账本均逐任务唯一，共两笔；合成余额为494。另一账号读取任务为404；关闭 gate 返回503，业务写入和 provider 调用均为0。Root451 实际 GUI 有限通过：桌面完成 A→B，390px 手机端完成 B→C；再次读取真实章节后为 C，历史 original_text 仍为 A，非目标镜头及稳定 ID 保留。独立 SQL 确认两笔扣费共6。fixture、owned 临时根、端口、fixture43511 与 Node43539 进程组及浏览器标签均已清理。

相关冻结证据位于 E：haoai-next-root-frame-review-real-http-450.json（SHA-256 7465f02bc81d6640ba85fda0bf382fa25413f83f1e31222b208c0fe83615f042）、haoai-next-root-frame-review-browser-acceptance-451.json（SHA-256 08c29d910f077e4eddd19cc845f23f5d7511a05ec5e857b2cdc87586e39e72d9）、haoai-next-root-frame-review-browser-run-451.json（SHA-256 989dd6b6dfa09b8b3228f7d4435b5ab4e1f1c33c21a04f188f2890edf2a7c4a1）及 haoai-next-root-frame-review-browser-sql-451.json（SHA-256 3c1fda39d3f51304ccd2e3005c2a73b358460aed77f5629ae986aa08ef04b4e4）。

这些结果仅覆盖限定的 frame.review 正向路径，不代表整行或整站接受。HTTP/GUI 的取消、unknown、target-conflict矩阵、普通非 superuser 正向流程与完整多角色会话仍待；frame.optimize、其它生成 family、full launcher、外部 provider 与生产部署仍未验收。U04.2/U04.3 保持 P/P，51项验证/接受义务不减少；A01–A39 完整矩阵接受数仍为0，Grillme/A39 状态不变。按用户要求，本批文档收尾后暂停；后续开发待用户明确恢复。

### AI 分镜复核主线进展与后续验收范围（2026-10-21）

Root469确认 AI 分镜复核已整合并推送至 main@`d11dc51e31df3bc8ab3c5f1f3066c854bb56a6e4`；功能分支 tip `f249fd15fdd64782c1eb17d374abe94bd308c738` 含 FR459-01 中文失败提示修复。2026-10-20 的独立分支与“收尾后暂停”段落保留为历史。当前 U04.2/U04.3 仍为 P/P；UI action 计数保持23行开发、20行接线、51行验证/接受，51项交付行不变。

Root458 的六个真实 owned HTTP 场景全部通过：普通用户 A→B→C、queued 取消、processing 取消、真实 PUT 目标冲突、完成后取消重放及异账号隔离。Root460 有限 GUI 覆盖普通用户桌面成功复核、390px处理中取消及真实 PUT 冲突，确认仅首笔扣费3、余额497，并保留人工稿、历史原文、其它镜头与稳定ID；该轮发现冲突机器码直接显示。461 经 RED→GREEN 修复提示，Dialog 定向测试10项通过、TSC exit 0；462源码复核闭合。Root466有限 GUI 复验显示中文提示、保留服务端机器原因，冲突任务有receipt/evidence且0 application/fee/ledger，余额500，人工正文完整保留。GUI provider 精确调用数未独立捕获。两轮 GUI 的服务、根目录、端口、进程组及标签均已清理，466另核 fixture44837与Node45650进程组退出。

Root468 当前前端全量门禁为114个文件、1520项测试通过，TSC与生产构建均exit 0；其中 Vitest 耗时262.969秒，TSC/生产构建分别耗时7.768/2.239秒；owned副本与源清单一致，临时进程组和目录已清理。backend 947项源码与434收据相同，2576 tests及267 subtests、原 create_app registry 250/36是复用既有全量结果，本轮未重跑后端。相关收据：haoai-next-root-frame-review-http-batch-458.json、haoai-next-root-frame-review-browser-acceptance-460.json、haoai-next-root-frame-review-browser-acceptance-466.json、haoai-next-root-frame-review-main-frontend-468.json、haoai-next-root-frame-review-publish-469.json。

开发已于2026-10-21恢复并继续；本记录不代表整行/整站接受或部署，也不新增整体 ETA。queued GUI、全部 unknown/迟到/多角色会话与更多冲突变体、frame.optimize、其它 family/AI入口、full launcher、外部 provider 与部署仍待。A01–A39完整矩阵接受数保持0，A39 Grillme 仍为 OFFLINE_PENDING。

### 素材整理主线整合与 E2E 暂缓（2026-10-22）

Root518 确认素材整理功能已从 `codex/asset-maintenance-491` 的 feature tip `cea87f69c02005ba806289279cc8882c666e852e` 普通中文合并并推送至 main@`43b10ec9b8c254bc14bc0a036869a115d5757683`。整合范围为十个前端路径；冻结输入对应 947 项 backend 与 304 项 frontend，backend 与 source467 完全一致，frontend 复用 502-03 全量门禁结果。该门禁覆盖 116 个前端测试文件、1543 个测试，TypeScript 与生产构建均实际通过；本轮没有重跑后端全量。原 507 发布收据只证明 feature checkpoint 发布门禁，当时明确记录尚未合入 main；合入与推送事实由后续 Root518 收据单独确认。功能分支保留，未部署。

HTTP505 在 owned 合成 SQLite 与真实登录上，通过五类素材维护路由覆盖角色、场景、道具三类检查；merge 回执、持久化、章节引用、融合成员、别名及媒体/认可控制均按收据通过。GUI506 为部分通过：忽略与恢复、切换保留项、取消合并且 SQL 未变化、确认合并后核对源引用/融合成员/别名、同一对话框继续检查新目录中的孤立项、孤立素材删除取消后再确认，以及空目录状态均有观测。390px 下无页面横向溢出，合并确认按钮位于视口内，长名称可检查。该轮没有完成完整键盘焦点验收；普通流程的 GUI/HTTP 通过不替代 fault 或权限负例。

仍待的集中验收包括 `bad_receipt`、`postcommit_read_failure` 两类提交后故障，独立 HTTP merge POST 请求计数与无自动重试证据，403 和 scope 切换，以及完整焦点路径，并继续核对权限、媒体和认可保护。用户当前只暂缓 E2E；开发继续，按需要进行单元测试、类型检查和源码复核，并以中文分批 Git 推进，最终端到端验收待补。该暂缓不把 506 的部分结果升级为完整 GUI 接受。清理收据确认原 owned root 已移除，API/Vite 进程组15371/15392已退出，端口53933/53934已关闭，浏览器标签6已关闭且视口已复位；资源清理通过不扩大业务接受范围。

本轮 U05.2 的开发与接线状态改为 C/C，V=P、A=U 保持；当前去重后的 UI action 计数为 22 行开发、19 行接线、51 行验证/接受。A01–A39 完整矩阵接受数仍为 0。frame.optimize 是独立工作，A 分支已推送 `e2ea`，P5 在 C 继续开发；U04.2/U04.3 仍为 P/P。

相关证据位于 E=`/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80`，按原始层级分别解释：source500-03 SHA-256 `61e948882a50f2cf370e796f0df211cec38e9234c2fea3a5e8a08434cbd92287`；501-sol-03 静态复核 SHA-256 `39a24113878c34b669d7743e4cf18aa5d6e4ec9e77e42f100bc83ed7338f767a`；502-03 全前端门禁 SHA-256 `1bf3b3fb2c095d46d7ab97302c4a5accbe75db0a51fbce8c4a18e533bc2c30af`；507 feature 发布 SHA-256 `b1ca2d179be0e8bf7f754bba43133852a8a367e14e3979193dafb342d4691292`；HTTP505 SHA-256 `72156ddcaefa11907513e1bcf0abb28a44b1e92fbf0ed478939479e28ba21090`；GUI506 partial SHA-256 `b1dbf284c84169389bd146d37279588f9ac647671cf585a68dfb67b9474499cb`；严格 stop505 SHA-256 `efce25c0417a670b75ce1e22674e1f5772e56c49cfe0f4d3cdd46137a3a47cfd`；独立清理505 SHA-256 `3c58aa129cd872a7ea5ad0fa19656f2ab57e73241f227b2460313bde8bfcecbf`；Root518 主线整合 SHA-256 `b755da232719b46993418bddedb121aeaf4da1a21217af2711e5e0b35e6a043b`。这些收据分别证明源码冻结、静态复核、前端门禁、feature 发布、HTTP、部分 GUI、清理和主线整合，不合并为一次端到端或整站接受。

### 跨章节素材替换源码实现与受影响门禁（2026-10-22）

U05.4 的开发和接线状态现为 C/C，V=P、A=U。前端已接通完整素材卡片、同剧同类新素材选择、逐章影响预览与选择、再次读取核对并明确确认、单一 inflight 真实 API POST 串行发送、独立 GET 核对，以及逐章 ACK、unknown、未发送结果和显式只读回读恢复。关闭并重开可回到原替换记录；回读不冒充原两阶段 ACK，也不会自动重发。AR530-03 复核确认预览、确认和单次尝试历史均显示各自实际素材快照名称；530-03 将 AR530-01/02/03 全部关闭，没有未闭合 Critical/Important。

Root531-03 在冻结的 5 个受影响前端测试文件上 134 项通过，TSC 与生产构建通过；验证绑定 947 backend / 312 frontend 精确源码，未声称新版全前端 1563 项结果。相关收据：`haoai-next-asset-replacement-review-530-sol-03.md` SHA-256 `ce80d3704d643d687548527c21e79198ff20b23c126f496fe3b7a5e50c6e9ba2`；`haoai-next-root-asset-replacement-frontend-531-03.json` SHA-256 `6b1ee98c8a4d2604292063b5944f8521883fc0ac76dcc9d49572b44e73ccdbe3`。HTTP、GUI 和 E2E 均未执行；用户当前只暂缓 E2E，必要的开发、单元测试、类型检查和源码复核继续，最终验收待补。

当前去重后的 UI action 计数为 21 行开发、18 行接线、51 行验证/接受；A01–A39 完整矩阵接受仍为 0，A39 仍为 `OFFLINE_PENDING`。U04.2/U04.3 仍为 P/P。frame.optimize 的独立 C 分支 P5 已发布于 `5d22227`，P6 正在审修；F 播放器 539 发现的 5 项问题正在修复。以上状态不表示它们已并入 main 或完成接受。

### frame.optimize React 结果 wire 修正（2026-10-23）

React 消费端已按现有后端状态投影支持 processing/completed 的结果数组，以及 failed/cancelled 的 `{partial, error}` 对象；严格校验结果索引与文本，并将任务结果保持为只读展示，章节仍以新鲜真源和素材身份校验为准。前后端测试共用合成 wire fixture，后端测试调用实际 `build_batch_optimize_status` 投影函数。作者定向验证为4个前端测试文件164项、后端投影测试1项、TypeScript检查与生产构建通过；Root完整差异复核通过，并在隔离源码副本上完成8个受影响前端文件314项回归，源码与受保护文件前后一致。此项仅修正结果读取合同，不代表 U04.2/U04.3 完成，二者仍为 P/P；21/18/51 义务计数及 A01–A39 完整矩阵接受数不变，其他 family、故障/权限矩阵、浏览器与部署验收仍待完成。

### frame.optimize 原 intent 到期恢复（2026-10-23）

626/627 在 C 功能分支为已注册的 `frame.optimize` 增加逐 Unit 原期限到期控制：只为已有且超过固定两小时核验期的原 submission 写入绑定原 ID/key、请求摘要和 first-intent/deadline 的 unknown control fact；不伪造 Provider 结果、费用、扣款、退款或 Work。到期处理保持图锁、CAS 与 fresh 完整确认；到期 Unit 不得晚到回写，未发送 sibling 与既有 continuation Work 可继续，已应用/收费结果和既有 failure reason 保留。合成 SQLite 作者最终受影响集合 172 项通过；一次修正旧 lost-fence 终态预期前的集合结果为 171 通过、1 失败，原日志保留。Root 的独立源码复核和冻结门禁待进行；本结果不证明真实 PostgreSQL、Provider、整项 U06.5 或 A01–A39 接受。21/18/51 义务计数、U04.2/U04.3 的 P/P 状态、A01–A39 完整矩阵接受数 0 与 A39 `OFFLINE_PENDING` 均不变。

### frame.optimize 原 intent 到期提交边界修复（2026-10-24）

629 为到期投影补充提交前 fresh clock 下的 lease、claim fence 与 due fact 时间检查；ACK 丢失后的确认会逐字段比对原 due fact 指纹，合法重算 digest 也不能掩盖同 ID 内容漂移。另补到期后 fee、delivery、finalization 续作及 rescan 回归。作者在合成 SQLite 上复跑原15个受影响后端测试文件，175项通过、1条 Pydantic warning。Root 独立源码复核与冻结门禁待进行；本轮不证明真实 PostgreSQL 竞争、Provider、浏览器/E2E、部署或 U06.5 完整接受。21/18/51义务计数、U04.2/U04.3的P/P状态、A01–A39完整矩阵接受数0与A39 `OFFLINE_PENDING` 均不变。

### frame.optimize 到期控制独立复核与验证（2026-10-24）

Root 沿用 `36ca5b9948cb0ce8d09839e6b53f0bf77b1e6b99` 完整差异基线，对 628 两项阻断问题完成聚焦复核；DUE628-01 与 DUE628-02 均已关闭。Root630 在 1,603 项输入的私有冻结源码副本上运行原15个受影响后端测试文件及实际结果投影协议测试，16个文件共176项通过，exit 0，无超时，保留1条既有 Pydantic warning。原 Root 复现脚本再次确认提交时有效 lease 会阻止到期写入、ACK 丢失后同 ID 的合法内容漂移不会被误认作原提交，以及已有 fee、delivery、finalization 完成后 rescan 可正确聚合父任务。

候选源码、main 及受保护文件、冻结输入前后一致；两个所属进程组和私有源码/TMP 均已清理。验证收据为 `haoai-next-root-frame-optimize-due-gate-630.json`，聚焦复核记录为 `haoai-next-frame-optimize-due-review-628-sol-02.md`。此结果只证明当前 frame.optimize 到期控制切片的源码和合成 SQLite 定向行为，不覆盖真实 PostgreSQL 竞争、Provider、浏览器/E2E 或部署。U04.2/U04.3、U06.5 仍为 P/P；21/18/51义务计数、A01–A39完整矩阵接受数0与A39 `OFFLINE_PENDING` 保持不变，整体改造继续。

### Storyboard Asset Runtime 与 host 接线（2026-10-24）

632 为六个角色/场景/道具提取及出场判断 POST 路由接入惰性 OpenAI-compatible stream runtime，并由 `backend.server.create_server_host` 显式注入；凭据仍由每个 owner-scoped 请求解析。保留 openai/deepseek 与 custom provider 的旧 request shape、Anthropic 流式不支持边界、首 choice 正文、内部 HTTP/公网 HTTPS URL 政策和 600 秒 asset timeout。共享 codec 的 multi-choice 默认语义不变，仅 asset runtime 请求首 choice，并读取首 choice `finish_reason` 发截断 warning。

作者测试使用真实 `create_app`、JWT、owned SQLite 与 fake HTTP transport 覆盖六路结果解析、选择帧/空提取 prompt、判断 prompt 与 `skip_merge`、权限拒绝、错误/空/HTML/only-reasoning/截断、分片 UTF-8、API key 跨内容事件脱敏及关闭响应时不伪造终态。该批没有连接真实 provider、真实网络、真实数据库、R2、浏览器或前端，也未覆盖素材图片/单镜/全镜生成，因此 U06.4 仍为 P/P，21/18/51 计数及 A01–A39 完整矩阵接受数不变。最终作者门禁与证据路径记录在本批 632 收据。

Root633 完整差异复核发现三处旧版兼容差异，634 已修正：custom provider 显式关闭 thinking、嵌套 max_tokens 保持原 default/value 与整数转换规则、内部 HTTP 保持原 DNS 判断。原三个问题的失败回归及作者首轮测试导入失败日志均保留；最终作者受影响组合141项通过。实际 Aiohttp 适配层的 fake 边界测试证明原300秒和资产600秒超时传入客户端，取消会关闭响应与会话且不伪造完成。

Root635 在1,605项输入的私有冻结源码副本上原样重跑三类独立复现，结果与固定旧源一致；随后受影响后端组合141项通过、exit 0、无超时，保留1条既有 Pydantic warning。候选源码、main及8个受保护文件、冻结输入前后一致；两个所属进程组与私有源码/TMP已清理。最终复核为 `haoai-next-storyboard-asset-review-633-sol-02.md`，冻结验证为 `haoai-next-root-storyboard-asset-gate-635.json`。这些证据仅覆盖当前六路后端运行时和host接线；前端提取编辑/确认/保存、图片与单镜/全镜生成以及真实Provider、浏览器/E2E和部署仍待，U06.4及21/18/51统计均不提升为整项完成。

### Storyboard Asset React 操作闭环（2026-10-24）

637 将六路素材提取/出场判断 SSE 与三类保存 API 接入 ChapterBrowser。用户可选择角色、场景或道具，选择模型、可选提示词、思考开关及镜头，编辑候选属性、丢弃或复用当前剧集素材、手工补候选，逐镜判断并调整绑定，查看 dry-run 计划后明确确认保存。未判定镜头保持原绑定，`[]` 与场景空字符串表示明确清空；已保存内容会重新读取章节、逐镜素材和素材库并核对后刷新页面。保存前按 owner/scope 回读，团队章节通过当前锁身份和本次租约校验；源变化或锁失效会阻止最终保存。未知保存回执冻结自动重试并要求人工读真源核对。

为保持真实保存行为，`storyboard_save` 对有效混合场景保存中的逐镜空选择落地空绑定；`None`、未知非空名称仍保留原绑定，未选镜头不变，原有整体无可采用候选拒绝条件保持。SSE/HTTP 及 React 组合验证使用实际 producer wire fixture、受控 loopback HTTP、合成 SQLite/JWT；未连接真实 Provider、真实数据库或 R2。来源变化、租约失效、用户主动关闭、旧 scope 迟到结果及未知保存回执不重发均有定向 UI 覆盖；当前会话/迟到 HTTP 401 与传输取消由 asset HTTP 测试覆盖。作者最终验证命令、进程和日志摘要记录在本批证据目录 `haoai-next-storyboard-asset-ui-637`。

作者最终受影响集合中，后端保存/producer 集成与共享 wire 测试为 101 项通过、1 条既有 Pydantic warning；前端 ChapterBrowser、Dialog、SSE/HTTP、services 与 Workspace 组合为 6 个文件、308 项通过。独立 TypeScript 检查与 Vite production build 均通过；Vite 转换 223 个模块，报告应用 chunk 超过 500 kB 提示，另有 React act 未 await 的既有测试 warning。首轮最终 TSC 发现测试文件中的未使用 helper，删除后对前端组合和 TSC 重新验证通过。实际运行命令、日志 SHA、owned PID/PGID 与退出/超时记录见 637 历史作者收据和 639 最终修复收据。

Root638 的五项前端 Important 发现按同一范围修正：judge 请求按 kind 映射到真实的 `characters`/`scenes`/`props` 字段，并由共享 wire fixture 同时验证前端请求与后端 `JudgeRequest.parse`；角色/道具的未识别名称会留下逐镜 `null` 供人工核对，显式空数组仍代表清空；迟到的锁获取先登记本次 lease 再校验 owner/scope，最终锁检查后且开始写入前再次确认 owner/signal；SSE HTTP 默认预算与 services 下限统一为 650 秒（后端运行预算 600 秒并留 50 秒余量），显式短 timeout 注入和不重试行为仍保留。后端三组保存/producer/wire 组合 101 项通过；最终前端 6 文件 308 项通过；TSC 与 Vite build 通过。首轮最终前端组合因旧 loopback 断言仍写 `candidates` 而有 1 项失败，按实际共享合同修正断言后整组通过；两次日志均保留。638/639 RED、GREEN、最终 run 元数据和日志 SHA 见 `haoai-next-storyboard-asset-review-639` 作者收据。

637/639 时点的服务器保存协议没有 expected-content CAS，保存 application 仍为多个阶段；客户端回读/租约无法证明与其他客户端之间的服务器原子竞争，因此真实并发接受仍未闭合。图片、单镜/全镜生成、真实 Provider、浏览器/E2E、部署接受仍待；U06.4 保持 P/P，V=P / A=U，21/18/51 与 A01–A39 完整矩阵接受计数均不变。

Root638 沿同一固定基线完成完整本地复核，并在修复后聚焦关闭五项 Important。Root640 在1,615项输入的私有冻结源码副本独立重跑原始请求合同、未知名称和计时预算复现：三类真实 JudgeRequest 解析通过，未知/部分未知名称保留 null，实际默认预算650秒。随后后端101项、前端6文件308项、TypeScript与Vite production build全部通过；前端使用单worker，未超时。候选源码、main及8个受保护文件、冻结输入前后一致，全部所属进程组和私有源码/TMP已清理。收据为 `haoai-next-root-storyboard-asset-ui-gate-640.json`。这些是当前素材提取确认/逐镜保存切片的源码与合成验证，仍不证明服务器CAS原子性、浏览器/E2E、真实Provider/DB/R2、部署或整行接受；21/18/51义务计数保持不变。

### 素材保存章节来源与剧集身份护栏（2026-10-24）

642为角色/场景/道具三类流式保存请求增加可选`expected_content`与`expected_series_id`。React从新鲜且经当前owner/source复核的章节快照，将这两个值同时带入dry-run与最终保存；服务端在打开流前核对，工作流每个素材阶段和最终绑定重新核对章节内容、剧集归属与当前访问权限。SQLite章节内容更新使用原始内容CAS，即使预期内容无需改动也验证提交前原来源；冲突不会覆盖后来提交的章节版本。旧请求不提供这些字段时保持兼容。

本批保留素材分阶段提交、未知回执冻结自动重试及可选角色清理语义。阶段边界发现来源或权限变化会发出真实错误并停止后续绑定，已提交的素材阶段仍保留；本批不声称全站或全请求原子性，也未关闭清理并发的剩余义务。验证使用共享合成请求fixture、真实应用router、SQLite、第二连接并发提交与owned loopback HTTP；无真实数据库、R2、Provider、浏览器/E2E或部署验收。此切片不提升整体迁移状态：21/18/51义务计数与U06.4的P/P状态保持不变。

Root643对固定基线`64d582a97ad5589dbbb27f991ee70fada0383614`的18路径作完整本地复核，发现SA643-01：来源剧集比较先于实际访问权限判断。Luna修复两个边界，三类真实HTTP的匹配/不匹配猜测在无权限时均403；有权限的陈旧来源仍409，阶段后撤权停止且保留已提交事实。作者及Root同源码后端组合185项通过；Root真实应用/JWT/合成SQLite探针确认三类实际保存、正文/未知字段保全、两连接CAS保留后来版本和媒体协调写入回滚。

Root644最初额外强制非项目默认的threads池，两轮分别出现300通过/9失败及304通过/5超时，原记录保留；隔离及插桩诊断未证明产品回归，也未确认资源或异步续体根因。核对`package.json`的`vitest run`、无pool配置的Vite和安装的Vitest默认forks后，644-03移除Root额外覆盖，使用原始测试/断言/5000ms限制，六文件309项全部通过（120.308秒），TypeScript与生产构建通过。后端185项复用精确同源644结果，未将插桩结果当原始整批通过。所有冻结输入、候选/main及8个保护文件前后一致，所属进程组和私有副本已清理。最新门禁为`haoai-next-root-storyboard-save-source-gate-644-03.json`；发布事实以645收据为准。本批没有浏览器/E2E、真实Provider/DB/R2或部署证明，整体目标继续。

## 邮件发送运行时与受控投递验证（648–650）

默认认证运行时现可从既有 SMTP 配置选择启用账号并投递验证码与重置链接；保留显式 sender callback。465 使用隐式 TLS，其他端口要求 STARTTLS，认证发生在证书与主机名校验之后。发送结果未知时不自动重发；DATA 已接受后，QUIT 或计数失败不会把投递改判为失败。共享日切仅在数据库重置成功后推进日期，计数前再次核对日期；本批不声称多进程跨午夜强一致。重置链接按既有系统配置读取有效 HTTP(S) 前端地址。

Root649 完整源码复核后发现并关闭三项问题：缺失或不可用 SMTP 配置恢复既有 503；跨午夜接受后按新日计数；DATA 尾回执仅明确 4xx/5xx 进入拒绝重试，其余未知回执不重复投递。作者最终受影响组合 91 项通过。Root650-02 在 1,622 项输入的私有冻结副本独立重跑原始缺陷探针，并通过实际 server host、合成 SQLite 与受控 TLS SMTP 完成验证码发送、邮件中实际码的服务器校验及密码重置链接投递；21 个后端测试文件共 288 项通过。未改前端的 309 项、TypeScript 和生产构建结果仅复用精确同源的 644-03 收据。

650 初次门禁因 Root 探针把 host 随机验证码误按固定值检查而提前失败，原日志保留；650-02 仅修正探针为读取实际邮件中的码再校验，未为此修改产品代码。最终候选、main 及 8 个保护文件和冻结输入前后一致，所属进程组及私有副本已清理。证据为 `haoai-next-smtp-delivery-review-649/649-review-02.json` 和 `haoai-next-root-smtp-delivery-gate-650-02.json`；Git 发布另以 651 收据为准。

U01.3 暂保留 P/P：浏览器请求可能先超时而后端继续投递，现有注册表单仍依赖本页发送成功回执才能校验，需补齐直接校验实际收到验证码的恢复入口。不得把未知发送结果显示为已送达，也不自动重发。SMTP 配置 CRUD 的既有密码响应合同本批未改，不宣称后端已脱敏；真实邮箱、真实数据库、浏览器/E2E 与部署验收均未执行。当前动作计数仍为 21 行开发、18 行接线、51 行验证或接受，彼此重叠；其他运行时消费者与完整迁移范围继续保留。

## 验证码发送回执未知时的前端恢复（652–655）

注册表单现在允许直接校验当前邮箱实际收到的验证码，不再要求本页曾收到发送成功回执。timeout/network 会提示发送结果尚未确认、先检查收件箱和垃圾邮件；用户可单独校验，也可沿既有流程直接提交注册，由服务器校验验证码。系统不会自动重发，也不把未知结果显示为邮件已送达。再次发送开始时立即清除旧的本地“已验证”，邮箱或验证码变化同样清除验证；发送失败保留输入。

Root653 关闭 652-01/652-02：撤销初版误加的独立校验前置门禁，完整保留原直接注册合同；修复再次发送结果未知时残留旧验证提示。原六个单项测试函数均逐字节保留，独立校验拒绝和注册拒绝分别覆盖；注册只在服务器成功回执后调用完成回调，错误回执保留表单。共享请求取消、服务切换、迟到响应及原默认 15 秒请求超时均保持，未通过调高超时绕过问题。

Root654 在整合 SMTP 主线后的 1,622 项冻结输入上运行三个受影响认证/API 文件，74 项通过，TypeScript 和生产构建通过；构建保留既有大于 500 kB chunk 提示。候选、main 及 8 个保护文件、冻结输入前后一致，所属进程组和私有副本已清理。SMTP 后端 288 项与真实 server host 到受控 TLS SMTP 的投递/校验/重置链接证据另按 650-02 保留；本批未重新声称真实邮箱、浏览器/E2E、真实 PostgreSQL 或部署验收。

U01.3 的实现和接线现为 C/C，V=P、A=E，覆盖上节 648–650 时点的 P/P。当前开发动作计数由 21 减为 20；接线动作仍为 18、验证或接受仍为 51，彼此重叠，不相加。102 原始 U01.3 行没有独立 wire action，因此本次不从原接线动作计数再减一。其他 AI/视频与批量恢复、画布来源与结果应用、媒体历史和成片导出、系统配置消费者及全量接受义务均继续保留；前端整体尚未完成。源码复核和门禁分别为 `haoai-next-auth-email-recovery-653/653-review-02.json`、`haoai-next-root-auth-email-recovery-gate-654.json`；Git 提交和远端状态另以 655 收据为准。
