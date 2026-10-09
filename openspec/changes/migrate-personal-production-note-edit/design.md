# Design

## Context

见 proposal 的动机。当前 NotesPanel 只读原 GET，ready 状态只保存派生 readout，原始 snapshot 在投影后丢弃；`WorkspaceServices` 只有读取方法。父 ChapterBrowser 的读取登记采用单调世代与 tombstone，拒绝同世代替换 ready readout。保存必须在这些防护内接入，不能放宽父端或修改受保护的投影/导航 helper。

主规格清单实际为空，本 change 增加 `personal-production-note-edit` capability。前十八批历史 109 个文件、不可变基线 217 个文件继续保全；本批 baseline 的 protected 36 是窄路径子集，不代表旧批次全部保护数量。完整记录见 [baseline](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-production-note-edit-baseline.json)。既有固定 SHA 来源 13 份见 [sources](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-production-note-edit-sources.json)；legacy-reference 复制白名单仍 exact-five。

冻结 SHA 为 `23403806898550a7668a6ee7c0c457315655c39b`。下表均来自 git show 静态读取，不读旧工作区文件或未跟踪 YAML，不把维护代码等同真实数据库验证。

| 固定来源与关键行 | 契约与全文 SHA256 |
| --- | --- |
| `backend/app/routes/personal_production_notes.py:39–68,124–267` | schema 内联；严格 PUT、双 CAS、认可重写及完整响应。`3fd7acc882fbeea0ed7500ef2a5f901a613aae14ce7bd893879dd669a6842562` |
| `backend/app/models/personal_production_notes.py:29–58` | chapter_id+user_id 私有唯一行；revision>0。`0b6509fc474de25df37d7c927600ec0e11c96d24bc5cc896d183998978331645` |
| `backend/app/services/personal_production_state.py:120–196,585–653` | 媒体维护、独立提交旧认可修复、同章其他用户认可撤销。额外静态分析参考，不扩大复制或原13份保全计数；`89ece9c2338bd9a2bef837adc0b101e498e5afa6c71c0e9d649fc9de6260c9b9` |
| `backend/app/personal_production_media.py:91–163` | first storyboard raw ID 唯一、同章素材身份、位置不作身份兜底。`583db9cab32331cf37eafefbe4f71ad3ff7c7c7a56e809137f53063a2f038928` |
| `backend/app/services/team_service.py:166–185` | owner/team/claim can_enter 权限保留。`392f0792be876adda6b4b7629af0624a1dfbaef6fbf8555d7cacfd96cc81326f` |
| `js/episode.js:9704–9736,9894–9903,9943–9946,10005–10161` | 三状态、摘要失配不可认可但可改其他备注、码点长度、冲突保留及未知结果不重提。`79b117ce13e633b6b641faca7232328358e8e6f3be40825dcf097f87994d0e9f` |

不存在独立 `schemas/personal_production_notes.py`，不得虚构该 schema。当前源码 anchor 与字节哈希详见 [scope analysis](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-production-note-edit-scope-analysis.json)。旧 UI 的三状态/保存是兼容依据；新 React 编辑器与显式核对流程是本批衔接，不照搬旧 note-only 自动重新认可的陷阱。

## Goals / Non-Goals

**Goals:**

- 在 default demo 与隔离 API 完成本人当前单镜头文字/状态保存、revision 0 首次创建及显式重读核实。
- 严格当前结构身份、原始双版本与媒体认可条件，保留 unknown JSON 字段和所有未提交记录。
- 保存门禁、草稿与读取发布在 Panel owner 层管理；不因编辑器取消/重开而绕过 pending 或 must-reread。

**Non-Goals:**

- 不写 resume、不批量、不规范化 unreadable/orphan，不新增后端、锁 API、素材/章节写入、上传、播放、媒体请求、计费、队列或自动重试。
- 不修改 ChapterBrowser/Workspace 产品、已有读取投影/媒体与导航 helper、旧测试、demo 样本、依赖、source reference；不触真实服务或生产。
- 不承诺 demo 页面重载持久化、真实 FastAPI/数据库事务回归，或用本轮 Sol 审查替代指定 Grillme。

## Decisions

### 1. 明确文件白名单与可选写能力

既有可变代码仅六份：

- `frontend/src/shared/api/services.ts`
- `frontend/src/shared/api/personalProductionNotes.ts`
- `frontend/src/features/chapters/PersonalProductionNotesPanel.tsx`
- `frontend/src/features/chapters/PersonalProductionNotesPanel.test.tsx`
- `frontend/src/styles.css`
- `tools/api-fixture/server.mjs`

允许新增仅七份：

- `frontend/src/features/chapters/PersonalProductionNoteEditor.tsx` 与 `.test.tsx`
- `frontend/src/features/chapters/personal-production/noteEdit.ts` 与 `.test.ts`
- `frontend/src/shared/api/personal-production-note-edit.test.ts`
- `frontend/src/shared/api/personal-production-note-edit-http.test.ts`
- `frontend/src/app/PersonalProductionNoteEdit.integration.test.tsx`

`WorkspaceServices` 增加具名、可选 `savePersonalProductionNote(chapterId, update, signal): Promise<PersonalProductionSnapshot>`；正文类型严格表达单元素帧补丁。可选性只用于保留旧只读 provider/test fake；actual demo/API 必须提供真实实现。UI 根据能力显示编辑入口，不能缺方法时静默假保存。相比把写方法改为所有 provider 必填，该方式不扩大既有测试与产品修改范围。需要其他文件时先报告，不顺手扩白名单。

### 2. 原始身份 helper 与显式单镜头编辑器

`noteEdit.ts` 负责纯函数：从同份 chapter/assets/capture/raw snapshot/readout 构造当前候选，检查唯一 raw first storyboard ID、全资产响应先 count 后归属、服务端唯一同 ID/位置与正媒体版本；保持原 frame/raw row 引用，frame_index 只验证位置。结构要求为 readout.mediaState ready，不把 digest mismatch 等同结构失败。

解析 raw note 使用既有 `projectPersonalProductionNote`，缺省字段沿原合法默认；坏类型/unknown status 为 unreadable，不开放编辑。孤立旧项不关联任意镜头。未保存行可新增，不能因 row.hasSavedNote=false 或 noSavedRecord=true 一律拒绝首次保存。

普通可识别 unmarked/needs_revision 初始化可见状态；当前有效 approved 才可初始化 approved。needs_reconfirmation 或未核验 raw approved 初始 `null`，必须用户明确选择。approved 条件为 row.verified、对应服务端至少一个摘要非 null、完整当前 capture；unmarked/needs_revision 不因摘要失配拒绝。不根据 frame 状态推断权限，不发送六种派生 UI 状态。

Editor 使用带镜头序号的可访问名称、label/select/textarea/save/cancel，文字为纯文本；长度用 `Array.from(note).length`，不 trim 文本。既有列表中有效行可显式编辑；revision0 提供独立“新增镜头记录”选择入口，候选仅当前可编辑镜头，原 noSavedRecord 提示、只读列表和筛选数量保持。一次只编辑一个镜头；草稿不存 URL/localStorage/日志，关闭整个面板或改变 owner 清理。

### 3. PUT 的严格适配与返回校验

只新增同路径 PUT：`/api/chapters/{encodeURIComponent(chapterId)}/personal-production-notes`，无 query，当前 token Bearer，仅向既有配置校验通过的 loopback API base 写入，保留 credentials omit/cache no-store/redirect error 限制，不新增任意同源或生产写许可。业务方法白名单为原 12+1，即 11 GET、登录 POST、此 PUT；不得把同 URL 的新增方法算成仍只有12模式。

正文固定：

```json
{"expected_revision":0,"frames":[{"storyboard_asset_id":"captured-raw-id","expected_media_revision":1,"status":"needs_revision","note":"文字备注"}]}
```

Helper/adapter 校验严格 own 字段、非负安全个人版本、正安全媒体版本、非空最大36字符 raw ID、三枚举、最多2000码点、单帧、无额外字段；拒绝 user/resume/approval 元数据。真实路由支持500帧不构成本片批量承诺。

复用原 snapshot decoder，再额外核对 exact chapter、revision=expected+1、目标 ID 与 frame_index/expected media revision、目标 note/status，以及 approved 的当前认可版本/无重确认或非 approved 的认可版本清空。approved 返回的目标两摘要必须与此次本地 capture 及保存前 snapshot 都相同；相同 media_revision 但摘要不同也视为无效成功/结果未知，不能宣布认可。其他记录/未知字段/resume 采用返回快照，不从本地旧值重建。无 response_model 不代表宽松接受坏成功包。

API 保持具名路由与正文类型，不提供任意 URL/任意 JSON 写入口。区分发送前本地拒绝、已收到的401/403/404/409/422拒绝、已发出后的不确定结果；5xx、超时/响应体超时、网络断开、abort、无效2xx不能证明没写入。旧只读错误语义不改。

### 4. Panel owner 管理操作、草稿与更高世代发布

在 Panel ready read state 增加同一读取的 raw snapshot 和 captured media，不改派生 projection。编辑 admission 绑定 current scope、read ticket/readout、raw snapshot、target frame/row/capture；Editor 只呈现和发出显式动作，Panel 在每次 input/save/cancel 消费时复核最新身份与永久 invalidated ref。

Panel owner 保存同步门禁，发起合法 PUT 前先锁定操作，重复点击立即被挡，不能只依赖下一 render 的 disabled。pending 和 must-reread 门禁涵盖整个 owner 的所有镜头，Editor Cancel、重开或改选镜头不解除；pending 时编辑只显示锁定草稿，整个面板仍可关闭。保存不放到 effect 中，StrictMode 不得自动重发 PUT。pending 时不重读；失败后才提供显式核对读取。

保存 admission 同步 invalidates 旧 read identity、清原导航/反馈，推进到更高读取世代并登记 null readout，pending 时旧 resume/逐行定位不可用。操作拥有独立 immutable payload、写世代、已接受的原 snapshot/capture 和新的待发布读取世代；不能因为自身作废旧 ticket 而误判整个保存不再 current，也不能让旧回调绕过门禁。

合法成功仅当前 operation 可消费。仍同当前原始 chapter/assets/capture 时直接用返回 snapshot 重投影，发布新 ticket/readout 并登记；父端可接受高世代 null→ready，拒绝低世代/tombstone/同代旧ready规则不变。无需额外 GET 或重新下载媒体；本地筛选因新 readout/ticket 自动回全部，旧导航不复活。仅此次操作仍匹配的草稿可清除，不能覆盖更晚草稿。

用户/services/章节对象/asset snapshot/open epoch/read generation 更改、关闭整个面板、切章/重读章节/返回/退出均使旧操作和 draft 首帧隐藏、永久失效；A→B→A 不复活。旧响应401不注销新token/新账号。abort只是停止客户端等待，不等同服务器回滚。

### 5. 冲突和结果未知：保留与显式核实

409/422 保留 draft，显示原因并进入 owner must-reread；403/404及其他非401拒绝同样保留本人草稿与错误说明、进入 must-reread，不能静默重提或注销，401仅在 current operation 下沿原认证处理。已发出的不确定结果明确“不能确定是否已保存”，同样禁写，不把“重新保存”当读取重试。

用户显式 GET 后保留同一当前目标的文字草稿，另显示服务端当前记录，重新检查身份/媒体版本。新的编辑绑定新 read ticket/raw snapshot，状态置 null 并需显式选择；不自动更新 expected_revision 后发送旧 payload，不自动重新认可。如果新快照不合法，保持草稿只读；用户可取消/关闭面板。即使服务端文字与草稿一致，也只报告已读取到的记录，不再 PUT。关闭/reopen 创建新 owner 后仅显式打开 GET 可核实实际结果，旧失败/成功回调不能改变新状态。

所有旧 JSX input/save/cancel 真实回放须 guard 最新 owner/read/write record，不只检查捕获时的 invalidated=false。测试可 mock public `react/jsx-runtime` 观察原 props handler，不读私有 Fiber，不用 detached DOM 事件冒充旧闭包执行。

### 6. 内存实现与代表性验证

demo 在 services 实例内建立按认证本人/章节隔离的内存快照，首次从原种子深拷贝，后续 GET 返回克隆，save 严格 CAS 与单帧合并，保留其余未知字段/resume。原 demo 数据文件与单一 demo 账号不改，页面刷新/新services实例会重置；两账号隐私在 fixture/integration 验证。

fixture 仅增加 PUT handler、CORS PUT、隔离本人内存记录/CAS及新保存错误环境变量。原 GET 无保存时完全原样，保存后本人 notes GET 读取 overlay；其他所有路由/PNG/旧 GET 错误模式不改。环境变量+重启控制正常、个人/媒体409、422、已应用但无效回复/响应体超时等代表模式，无额外控制HTTP。每次记录仅 method/path/status 等既有安全摘要，不记录 note/body/token；无真 DB/锁/Worker/R2/provider。维护独立提交409按冻结契约做代表性内存模拟，不冒称真实SQL事务验证。

自动测试重点 exact body/current Bearer/单次编码、服务端响应校验、revision0/合并/双CAS、认可与普通备注差异、同步双击门禁、Cancel reopen不得绕过、R2非默认草稿后旧真实handler回放、scope首父layout/ABA、原PUT与摘要 success/401真正 settle+await act、higher-generation登记和既有filter/resume/row导航。新 integration 测试从真实 Workspace/ChapterBrowser 挂载，既有生产/test文件保护，不用改 timeout/skip 来掩盖失败。

真实浏览器由 root 分别记录1280桌面/390移动、demo保存与重开、隔离API本人两账号、409草稿、未知结果不重提、读取恢复及现有导航。save显式事件应仅1 PUT；notes初次open开发StrictMode可能原GET replay，必须按实际日志记账。定位/筛选不追加业务请求；原 lazy 安全 PNG 另计，不声称全局外连审计或所有媒体计数为0。browser取消与自动原Promise ignored-abort隔离证据分层。

## Risks / Trade-offs

- [旧 note-only approved 会改认可版本] → 始终提交显式三状态，待重新确认必须重新选择；保留合同反例，不照搬旧 UI 缺陷。
- [未知结果存在已提交但无回复] → owner门禁、保留草稿、显式GET核实及重新选择；无原接口幂等键，不新造幂等重放承诺。
- [保存成功沿用原世代会被父端拒绝或旧回调污染] → 更高世代/null→ready登记；原 watermark/tombstone不放宽。
- [fixture与demo只能模拟事务/维护] → 本地功能/HTTP合同/源码/浏览器分别记账，真实FastAPI/PostgreSQL未运行。
- [关闭当前owner会丢本地未保存草稿] → 清楚展示“尚未保存/结果未知”，当前范围只在同owner保存草稿，不跨账号/页面持久化私有数据。

## Migration Plan

先由 root 审查这四份规划并确认 apply ready，再由既有 Luna6/xhigh 实施白名单代码；分析/复核沿 requested/reused GPT-6.1 Sol/xhigh，runtime未独立验证。按任务完成局部/独立全量/浏览器后才追加 README 第19段、compat第19段及本change verification，旧全文保持。

回退仅移除本片编辑入口/可选保存实现，恢复隔离fixture，不触旧仓库或真实数据；内存写不承诺跨进程持久化。无commit/push/deploy/archive。前18历史109、不可变217、protected36子集、既有13固定来源与exact-five须守恒；本批新增静态 state service参考不扩source copy。

既有用户同回合 proposal→apply 授权覆盖技能额外回合边界，作者只交规划，不自行实施。指定 GPT-5.6 Sol/xhigh Grillme 当前离线，是独立待办，不探测/启动/配对/降档，也不以本轮Sol/source review替代。
