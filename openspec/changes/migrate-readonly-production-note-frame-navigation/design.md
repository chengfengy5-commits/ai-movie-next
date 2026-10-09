# Design

## Context

动机见 proposal。当前 `PersonalProductionNotesPanel.tsx:213-247,303-318,440-458` 将 GET 结果和本机媒体摘要投影成 ready/readout，已有逐镜头状态/备注列表；无保存记录不显示该列表，孤立记录另区。现续作 ticket 只有 position/isCurrent，`ChapterBrowser.tsx:759-808` 按当前 owner 定位；本批不能直接把它作为新逐行身份凭据。

`personal-production/projection.ts:80-97,190-266` 已要求章节 first storyboard 身份唯一、完整 assets 同 ID 唯一再同章同剧集核验、服务端帧身份位置匹配及媒体摘要/revision 可核对。`verified` 不等同 `approved`，且不可识别便签仍可能 verified=true，故新入口另拒绝 unreadable 状态；不修改此投影或重新实现摘要。`resumePosition` 的投影和续作行为保持。

旧来源固定 `23403806898550a7668a6ee7c0c457315655c39b`，本轮实际仅 git show 静态核对：

| 全文来源 | SHA-256 | 字节 | 依据 |
| --- | --- | ---: | --- |
| js/episode.js | 79b117ce13e633b6b641faca7232328358e8e6f3be40825dcf097f87994d0e9f | 1162775 | 9637-9701 身份/媒体捕获与当前性；10121-10161 续作唯一 ID、同章资产、本地选择 |
| backend/app/routes/personal_production_notes.py | 3fd7acc882fbeea0ed7500ef2a5f901a613aae14ce7bd893879dd669a6842562 | 10303 | 108-121 当前用户读取及 commit=True 维护链 |

旧源码提供私有记录、稳定身份和续作选择依据；本批逐行入口是新的只读 UI 衔接，不声称旧页已有相同行为。不执行旧 GET：其媒体元数据/认可维护及认证 session 副作用继续按第四切片兼容说明保留，不称数据库纯读取。上表不是新增来源复制，exact-five 不扩展。root 已保存84历史/87保护/13来源基线。

## Goals / Non-Goals

**Goals:**

- 把现列表的可信行连接到已渲染的同章镜头；本人读取已核对的未标记/待修/待重新确认状态也可定位。
- 用独立完整读取凭据和父端 helper 复核，避免位置兜底与 R1/R2、scope ABA 回调错用。
- 聚焦/滚动成功才报告成功，保持记录面板；错误在当前面板说明，键盘可操作。

**Non-Goals:**

- 不创建未保存行新列表，不改原续作资格/定位/反馈行为，不修改媒体身份、projection、DTO、服务或样本。
- 不保存、认可、设置续作、编辑、生成、取锁、播放、下载、deep link 或自动定位；不增加请求、API、媒体元素、依赖或私有持久缓存。

## Decisions

### 1. 新局部身份 helper，沿用已完成的核对

新增 `features/chapters/productionNoteFrameNavigation.ts` 与测试。建议纯函数 `resolveProductionNoteFrameTarget(chapter, seriesId, assets, readout, frame)` 返回 `{ storyboardAssetId, position } | null`。入口只用于现有可见列表：要求 readout.mediaState=ready、非 noSavedRecord、frame 是同一 readout.frames 内唯一实际对象、verified=true、status 为 approved/needs_revision/needs_reconfirmation/unmarked；unverifiable/unreadable 或未知状态拒绝，派生 position 为正 safe integer 且 readout 中位置唯一。frame.hasSavedNote 不是额外导航权限，未标记行继续按现显示规则处理。

以该已核对行对应的当前章节 frame 取得原始 storyboard[0]，保留原字符串，非空且在当前章 first-ID 全量中唯一；在完整 assets 响应先按 raw ID 全量计数，再验证唯一目标的 chapter_id/series_id 与当前章/剧集一致。返回位置只由该稳定身份在当前章唯一匹配推导，不接受传入 position 与当前身份不一致，也不以 frame_index、名称或正文补救。父端重新调用同一纯 helper 复核，不复用 resume 的仅 position 接口。

### 2. 独立 ready 读取身份和定位 ticket

Panel 保留原 GET→checking→projection 链路、状态和列表。新增 `PersonalProductionNoteFrameReadIdentity`（同份 readout 引用，未 ready 时为 null，+ notes requestGeneration），以及独立定位 ticket：actual frame/readout/read identity、raw storyboardAssetId/position、chapter 引用、assets 引用和 assetSnapshotToken、contextToken/openEpoch、userId/seriesId/services、mediaSnapshotAvailable、`isCurrent()`。无需新摘要，也不把媒体 URL/摘要或 raw ID 放入 DOM/日志。

Panel 仅当前 ready ticket 可生成入口/回调；isCurrent 同时核对当前 scope、read ticket、readout 和 frame 引用及资格。父端以 expected notes owner 接收当前读取身份并保存 ref；只有 ready 身份可定位，非 ready 读取世代仍用于关闭守卫；仅同 owner 的当前读取报告可成为现凭据，同 owner 的 generation 单调登记，失效后保留读取水位；旧报告不能重新注册，同一仍当前的身份重复报告保持幂等。消费时核对此 ref 与完整 target，当前 chapter/assets 与 token/世代，再独立调用 helper；即使旧回调声称 isCurrent=true，也不能越过父端 current owner/read identity 校验。

新增逐行读取失效通知携带被失效的 readout/requestGeneration；在递增 generation 前捕获该身份，旧 effect cleanup 捕获自身身份，不能在晚执行时误取较新读取。父端只清完全匹配 owner+readout+requestGeneration 的逐行反馈和读取引用。关闭入口也绑定生成该入口的读取身份/世代，父端拒绝同 owner 的 R1 旧 close 关闭 R2；当前 loading/checking/error 的关闭仍正常，scope 关闭继续按 owner/open epoch 处理。原 onLocateResume/onResumeInvalidated 接口与行为不复用为此凭据；新增链路不得改变原续作操作。

### 3. 首帧和生命周期

保留现 Panel scope 的 contextToken、assetSnapshotToken、user/services/series/chapter 对象、assets、媒体可用状态及 open epoch 比较：render guard 首帧不显示旧内容，layout 永久失效，A→B→A 不复活/自动 GET；仅显式重开挂新实例。GET 成功与摘要完成两段均需 current，旧401不得影响新登录。

父端 notes owner、open epoch、当前章节目录/选章和 asset request generation 必须仍匹配。显式记录/章节/原图重读、关闭重开、切章、切换粗剪/关联素材、返回列表、任务页及退出作废旧定位。保持个人记录原有资产 loading/error 的显示/打开规则，不照搬第十四粗剪允许资产 late-ready 的不同生命周期，不额外读 assets/notes。重读后的 R2 定位反馈不能被 R1 invalidated 清除；旧 owner 的 locate/close/invalidated 实际回放不可影响重开后的新 owner。

### 4. DOM 操作、失败与独立反馈

使用既有 storyboard article refs，不添加目标媒体节点。父端聚焦前确认最新 target、当前 helper 身份、节点映射/连接/容器归属/hidden/inert/实际可见；`focus({preventScroll:true})` 后检查 document.activeElement、节点未替换及完整 owner/read/snapshot 仍有效，再执行 `scrollIntoView({behavior:'auto',block:'center'})`。失败返回给仍 current 的 Panel，显示“未能定位当前记录镜头”等说明，不能反馈成功或触发重读；节点恢复后显式点击重试。

新增独立逐行反馈绑定 owner/read identity/target，文案如“已定位到记录镜头 N”，静态焦点和文字可辨。article 失焦（包括进入内部按钮）只清匹配逐行反馈，不清别的流程较新反馈；不新增 timer 或导航消费状态。重复定位仍纯本地，不关闭面板、不设置续作位置。既有安全图片 lazy 读取如发生按实际记录，不能将零新增业务 GET 说成全程零媒体请求。

### 5. 文件和验证边界

允许 mutable6：`PersonalProductionNotesPanel.tsx/.test.tsx`、`ChapterBrowser.tsx/.test.tsx`、`Workspace.test.tsx`、`styles.css`；新 helper/test 两文件。其余包括 Workspace 产品、个人 projection/媒体 helper、DTO/services、fixture/demo 和历史全部保护。当前十二业务接口与 exact-five 不变，不增加 fixture 控制。

自动化先验证真实对象/身份 helper、记录可识别状态、Panel read identity 与 R1→R2、实际 DOM focus/hidden/detached/focus-fail、旧回调回放；首父 layout 采用 user-only/services-only/chapter-object ready 入口与 ABA 代表例。请求、401 与异步摘要反例必须 resolve/reject 原 Promise 后 await act，Workspace 覆盖退出新用户，不只 signal.aborted。

root 在原样 demo 1280/390 和现 fixture 做真实焦点、键盘、滚动、失焦、面板保留及点击前后请求日志；接口能表达的 error/mismatch/两账号/延迟以现能力验证。duplicate/foreign/强制 stale callback、ignored-abort Promise 等仅由自动 mock 举证，不为浏览器验收造新路由或样本。各层记录实际执行者/请求模式/原图，真实 FastAPI/DB/Worker/R2/付费和生产未运行。

## Risks / Trade-offs

- [verified 被误当作认可或可识别记录] → 复用现核对结果，另拒绝 unreadable，合法可识别状态不要求 approved；不改状态投影。
- [读取结果仅有展示位置] → 从实际 row/readout 引用绑定并独立核对原始唯一身份，不扩 DTO 或在 position 失败时回退。
- [同 owner 的旧读取清新反馈] → 专用 readout/requestGeneration 身份，注册和 invalidation 都按 expected owner 与读取匹配。
- [共享导航反馈导致旧能力变化] → 逐行 ticket/callback/feedback 独立，原 resume 不改；受影响路径做代表性回归。
- [fixture 被误称真实后端或取消被误称迟到安全] → 层级分开保存收据；真实 settle 自动化与浏览器取消分别说明。

## Migration Plan

仅在独立 haoai-next 按 tasks 实施局部 React 变更；先 helper/Panel 再父端和自动回归，之后 root 本地 pipeline/浏览器与文档收口。回退只移除新增逐行入口/helper/独立反馈，既有记录读取和续作不变。无提交、推送、部署、归档或真服务执行；本轮已获同回合规划后 apply 授权，规划交 root 复核后由 Luna 实现。固定 GPT-5.6 Sol / xhigh Grillme 离线待办，不启动/探测/配对/降档，不阻断本地工作或以 Sol 代审。
