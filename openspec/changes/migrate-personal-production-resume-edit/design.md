# Design

## Context

参见 proposal.md 的动机。本切片只有本人续作标记设置/清除；设置不是认可、播放或定位。CLI 主 specs 实际为空，schema 为 spec-driven。请求复用 GPT-6.1 Sol/xhigh 设计路线，runtime 未独立核验；指定 GPT-5.6 Sol/xhigh Grillme 未执行。

固定来源为 23403806898550a7668a6ee7c0c457315655c39b，均仅 git show 静态读取：
- backend/app/routes/personal_production_notes.py:54–68 的 inline Pydantic model 允许显式 resume-only；并不存在 schemas/personal_production_notes.py。:71–79/125–160 校验会员、章节访问、本人 revision，并使用数据库事务锁；:189–199 的位置分支只要求目标存在/source_valid，没有媒体 CAS；:201–267 可维护认可并写入私人行，返回完整服务器快照。
- backend/app/models/personal_production_notes.py:33–58 唯一键 chapter_id+user_id、私有 revision 和可空 resume_frame_id。
- backend/app/personal_production_media.py:91–163 以唯一 storyboard[0] 绑定本章原图；frame_index 只是展示位置。services/personal_production_media.py 仅 re-export，不是另一份规则。
- backend/app/services/personal_production_state.py:120–196 无私人行返回 revision 0，不建行；GET 可初始化媒体元数据/维护认可并 commit，:585–653 可维护同章其他用户认可。
- js/episode.js:9733–9734 的 editable 判定不要求摘要相等；:10047–10058 已用两键正文保存位置；:10061–10118 有当前 operation 隔离与未知结果不自动重发。清除是在既有 nullable wire 上增加明确 UI；保留现 React 的 verified 续作定位规则。

本轮实际计算的全文 SHA256：

| 固定来源文件 | SHA256 |
| --- | --- |
| backend/app/routes/personal_production_notes.py | 3fd7acc882fbeea0ed7500ef2a5f901a613aae14ce7bd893879dd669a6842562 |
| backend/app/models/personal_production_notes.py | 0b6509fc474de25df37d7c927600ec0e11c96d24bc5cc896d183998978331645 |
| backend/app/services/personal_production_state.py | 89ece9c2338bd9a2bef837adc0b101e498e5afa6c71c0e9d649fc9de6260c9b9 |
| backend/app/services/personal_production_media.py | b1173526e3615b1b4627c9cf30ebfb5a23de684e849518544ca482c71cbeed72 |
| backend/app/personal_production_media.py | 583db9cab32331cf37eafefbe4f71ad3ff7c7c7a56e809137f53063a2f038928 |
| backend/app/services/team_service.py | 392f0792be876adda6b4b7629af0624a1dfbaef6fbf8555d7cacfd96cc81326f |
| js/episode.js | 79b117ce13e633b6b641faca7232328358e8e6f3be40825dcf097f87994d0e9f |

当前 personalProductionNotes.ts:218–287 只接受 note-only 精确正文；services.ts:66–71 的备注 write capability 可选；Panel:731–877 已实现同步写门禁及 higher null→ready 登记。projection.ts:270–301 的续作定位要求 verified。新能力不能通过放宽这些已有规则实现。

## Goals / Non-Goals

**Goals:** 新增独立 resume-only 语义和私有持久于实例内/fixture 内存的设置与清除；复用个人 revision CAS、当前 token 检查及父级单调读取协议；清楚区分保存标记和可定位。

**Non-Goals:** 不变更原备注 helper/editor/认可逻辑、DTO 读取解码、投影、media normalization、父 ChapterBrowser/Workspace 产品和旧测试；不改源后端/数据库 schema、源样本/fixture GET、依赖、媒体/播放、锁 API 或其他业务写入。不存在生产许可或新同源 API 许可。

## Decisions

### 1. 独立类型和纯 helper

新增 PersonalProductionResumeUpdate = { expected_revision: number; resume_frame_id: string | null }，单独 parser 要求 own 两字段、无 extra、非负安全 revision 且加一安全，非 null ID 保留原字符串、非空白且至多 36 Unicode 码点。不复用或修改原 note parser 的允许键集。

新增 personal-production/resumeEdit.ts，负责：
- 从同份 chapter/content、完整 assets、capture、canonical snapshot 中产生设置候选，携 raw ID、原 frame_index/显示位置及原始对象引用。完整 raw ID 计数后才验证唯一资产的 chapter_id/series_id，服务器 frames 同 ID 唯一且 frame_index/source_valid 一致。不从 row.status、row.verified 或 note 可识别性判断保存资格。
- 设置只要求结构和身份可核对，不强加摘要相等、positive media revision CAS、非双空或媒体认可。位置/索引仅从此同份章映射；不能跨快照按索引兜底。clear 单独验证当前 owner 的 ready canonical chapter snapshot，完全不依赖媒体/目标候选；canonical 媒体 empty/unreadable/mismatch 仍可清除。
- 成功解码完整 snapshot，验证同章、revision=expected+1、精确 resume 值；设置再 count 完整返回 frames 后验证目标 raw ID、原 frame_index/source_valid。摘要、media_revision 和其他便签可因服务器维护变化。clear 不要求 frames 有目标或 media_state ready。
采用独立 helper 而不扩展 noteEdit.ts，避免把认可门槛或 note-only PATCH 带入位置保存。

### 2. 独立可选能力，共享真值存储

WorkspaceServices 增加可选 savePersonalProductionResume(chapterId, update, signal?) → Promise<PersonalProductionSnapshot>。真实 demo/API 都提供；旧只读 provider 无方法时只读，不能 silent fallback 到备注写方法。

API 复用当前受控 PUT path、当前 Bearer、单次 encodeURIComponent 和既有配置校验通过的 loopback resolveApiBaseUrl；不加 query/endpoint。post-await 保留当前 token、signal 和响应解码检查。十三种业务方法保持：十二个既有方法和同路径 PUT。

demo 新分支必须使用现有 note 保存同一私有 Map（user+chapter/services 实例），共用 revision CAS；设置与备注交替保存相互看到 revision，不能用第二个 Map。clear 在媒体资格判断之前走合法 null 分支；revision 0 可设置。只存返回真值的 clone，不改 demo 数据源，不把实例内保存宣称刷新页面持久。

fixture 仅扩展同一 PUT 的 resume-only 分支/CAS/现有环境变量错误设施，保留 note-only 分支、CORS PUT、无敏感正文日志、全部旧 GET 首次样本及其他路由行为。成功后同章本人 GET 读到内存真值是写入结果，不是改初始样本。两用户同章私有；设置422/个人CAS409/维护变化/clear通过独立合成压力测试验证，复杂媒体空态可由服务/UI桩覆盖，不为凑浏览器新增 GET modes 或控制 HTTP 路由。

### 3. 续作区域与未保存备注互斥

独立位置区展示当前保存位置/无法映射说明；用可访问镜头序号和文本选择有效候选并显式“保存续作位置”，另有“清除续作位置”。内部持有候选对象，公开选择项可用本次选项索引映射，不能把该值当稳定身份。ready 时即使 noSavedRecord、无可识别便签或只有 resume write capability，仍可展示候选；不扩展全部未保存便签记录列表。

相同已存 raw ID 是 no-op；位置为 null 不提供有效清除。选择不写、不定位、不取锁。设置成功后如媒体未 verified，应说明位置已保存但当前不能定位，不放宽原 hasVerifiedResume/navigation。

备注编辑器打开时禁止两类位置动作。关闭后将草稿与当前 canonical 的原 noteEdit 初始值比较，只有真正修改过的 note 或明确状态选择才阻止；默认未修改草稿不阻止。Cancel 仍保留草稿；提示先保存备注或用既有“重新读取记录”显式放弃修改，不新增批量丢弃操作。

### 4. 一个 owner 写门禁和当前事件证据

Panel 内以具名 note/resume 操作区分 payload、展示和成功验真，共用 save gate/同步 ref；不能拆成两个可并发写 gate，也不改父协议。位置选择绑定 read ticket+snapshot+capture+当前 scope/选择世代，消费时读取最新 ref。旧选择/Save/Clear/Cancel/Close 的闭包不仅校验捕获时状态，必须校验最新永久 invalidation、ticket、request generation、owner 和候选引用；同 snapshot 被复用到 R2 也不能复活 R1 选择。

提交同步取得共享门禁后：冻结完整操作和设置/清除意图；推进请求世代，失效旧逐行/续作导航与反馈，发布高于旧登记的 null 身份。pending 展示锁定意图，保留上次读取但无旧导航/后续写资格。写操作用独立 prestate/current operation 检查，不把旧 ready ticket 再登记成 current。

成功产生更高 generation 的新 canonical/readout/capture，重新登记 ready；采用完整服务器 frame_notes 和 resume，不覆盖维护后的状态。保持旧投影实现；重投影只算字符串摘要，不加载媒体。重置位置选择、筛选和旧导航；无自动 GET，备注 helper/editor 本身不改。原 parent watermark/tombstone 判定不放宽，失败时当前 null 身份仍可关闭面板。

### 5. 失败门禁与显式重读

非401拒绝（409/422/403/404等）或 unknown（5xx/timeout/network/invalid2xx）保留“设置第N镜头”或“清除位置”的具体意图只读，并进入共享 must-reread。关闭/重开 Editor 不解除门禁，重复旧事件不发送任何请求。当前401走既有注销；旧401没有权限影响新 token/session。

用户显式重读，必须真 GET 成功且同章 full snapshot 被当前读取接纳，才解除 resume 失败门禁；失败 GET 不解除。旧意图不自动重定位到新镜头，不自动复用 revision，也不自动重发。新 ready 默认为未选择，需重新选择有效目标或再次明确点击清除；若 server 已保存相同位置则 no-op。备注失败恢复仍维持第19草稿保留/重新显式状态确认规则。

关闭面板/换 scope 作废旧私有操作与草稿，不移植到新 owner；用户显式重开产生 fresh GET，再以真值决策。首 render/layout 防护含 user、services、series、chapter 对象、open/context epoch、资产/capture及读世代，A→B→A 永久作废旧 owner，普通同 token 值更新不凭空改变身份。

### 6. 文件白名单与验收口径

仅旧五文件可变：
- frontend/src/shared/api/services.ts
- frontend/src/shared/api/personalProductionNotes.ts（只追加独立类型/parser，读取及原 note parser 保护）
- frontend/src/features/chapters/PersonalProductionNotesPanel.tsx
- tools/api-fixture/server.mjs（仅原 PUT 的 resume 分支）
- frontend/src/styles.css（仅必要追加；不用则全文 hash 保持）

仅新六文件：
- frontend/src/features/chapters/personal-production/resumeEdit.ts
- frontend/src/features/chapters/personal-production/resumeEdit.test.ts
- frontend/src/shared/api/personal-production-resume-edit.test.ts
- frontend/src/shared/api/personal-production-resume-edit-http.test.ts
- frontend/src/features/chapters/PersonalProductionResumeEdit.test.tsx
- frontend/src/app/PersonalProductionResumeEdit.integration.test.tsx

旧全部 tests、noteEdit/Editor、DTO contracts/投影/媒体和定位 helpers、ChapterBrowser.tsx、Workspace.tsx、demo 源样本、依赖及 exact-five 复制不变。验收后只追加 README/compat 本批段并创建本 change verification/更新 tasks。

本批仅保存本人续作标记，不代表粗剪等其他核心业务已迁移。保护 baseline 为 238 inventory/108 code、117 old history/49 narrow protected/231 immutable，narrow protected 是当前保全子集，不能误称旧全仓保护数。

## Risks / Trade-offs

- [源接口没有位置媒体 CAS] → 只承诺个人 revision CAS 及响应中的当前目标身份；不承诺媒体冻结或设置等于认可。响应维护真值保留。
- [UI 写成功但浏览器取消/响应丢失] → unknown gate + explicit GET，禁止自动重发；自动测试真正 settle 原 Promise 并 await act，browser 取消另记。
- [notes 与 resume 协议整合破坏父水位线] → 复用第19 higher null→higher ready 登记，专门测 pending/失败关闭重开、旧 register/invalidated/locate/close 回放与同步跨类型双保存。
- [原安全图片 lazy GET 仍可发生] → 不新增媒体链路；分别记录业务、OPTIONS、安全 PNG 实际计数，不能把代表动作 delta0 泛化成全局无外连审计。
- [源码、fixture 不等同真实数据库] → 只静态记录源维护/权限/事务行为，本地与隔离 HTTP/浏览器分层；真实 FastAPI/PG/Worker/R2/付费及指定 Grillme 均未执行。

## Migration Plan

根代理审读本轮四 planning 并核 strict/apply ready 后，Luna 实施白名单；Sol 独立只读 source/test review，根代理独立最终 typecheck/full test/build、来源/保全、桌面移动 demo/API 真实验收。无 timeout 提高或 code skip。事后追加四文档并冻结/复核。仅本地隔离交付，无生产部署/归档/commit/push；回滚可撤销本批允许文件与新文件，保留此前版本和私有 CAS 语义，不触旧库。外部 GPT-5.6 Sol/xhigh Grillme 独立离线待办，不探测或替代。
