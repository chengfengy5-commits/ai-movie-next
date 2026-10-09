# Design

## Context

动机见 proposal，外部行为见本 capability spec。当前 `PersonalRoughCutPanel.tsx:302-369` 全量渲染草稿，`333-335` 分别显示 included 与 pending；`113-207` 已有私有 scope、读取世代、显式重读与永久 scope 失效。既有第十四批定位要求完整 snapshot 内的实际 row，资产正常迟到完成不关闭粗剪；第十六批筛选可参考局部读取绑定方式，不能复用其 noSavedRecord 或媒体摘要规则。

旧库仅以冻结 SHA `23403806898550a7668a6ee7c0c457315655c39b` 的 git show 静态读取：

| 来源 | 静态依据 | 全文 SHA256 |
| --- | --- | --- |
| backend/app/routes/rough_cut.py | 138-153 未保存非空初始投影；165-192 保存顺序及新增 pending/排除条目；203-230 当前用户私有 GET，无显式 commit | ce8f1668eba0776d748e537b23441a6869406a1e36fbf62e1d59abf20673d642 |
| js/rough-cut.js | 390-405 独立纳入分组与 pending 标记；423-429 分开展示纳入与排除组，保留原 index | 9504b3b22615dc640152d1f18661420ea39e82da78299036a12c02475515c5cf |

这是现有状态/顺序语义的依据；本批新增四按钮只读筛选，不移植旧页的纳入写入、排序、保存或播放。粗剪 GET 无显式 commit 不代表整个请求无认证会话副作用，不执行真实后端回归。13 份冻结来源清单见 `/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-rough-cut-status-filter-sources.json`。root 已捕获 96 历史、93 保护、203 不可变既有文件和 209 文件/97 代码基线；集合单独记录，不求和。

## Goals / Non-Goals

**Goals:**

- 对一份当前草稿提供独立条件计数和筛选，保留原行/原索引及现有定位链路。
- 读取更换时首提交默认全部，旧事件及迟到请求不能污染新选择。
- 复用原样 demo/fixture，分层记录自动化与浏览器实际能力。

**Non-Goals:**

- 不重新解释纳入/pending、媒体可用性或稳定身份，不排序/去重/重编号。
- 不改父产品、读取协议或定位 helper，不写入草稿、播放、导出、导航、搜索、持久化或新增 API/样例/依赖。

## Decisions

### 1. 独立谓词和完整快照计数

新增 `features/chapters/roughCutStatusFilter.ts/.test.ts`，类型仅引用现 `PersonalRoughCutFrame`；选项为 all/included/excluded/pending，UI 顺序“全部 / 已纳入 / 已排除 / 待安排”。谓词分别恒真、frame.included、!frame.included、frame.pending。数量以完整 snapshot.frames 计算，明确“待安排单独计数，可与纳入或排除重叠”；不能让三个条件数量之和冒充全部。

不采用互斥 pending 优先级，因为当前两个 badge 与 DTO 的布尔字段独立。冻结 GET 当前新增 pending 通常为 included=false；自动 mock 仍验证 DTO 接受的 pending=true/included=true，不将其包装为正常旧 GET 样例或浏览器实测。

以完整 frames 先建立只读 `{ frame, originalIndex }` 条目，再按条件选取，保留原 frame 对象。计数不排除空 ID、无视频或不可定位条目，不按 asset_id 合并；兼容 GET 超过500条和重复空 ID。显示与 list key 使用原完整列表索引，wire frame_index 仍仅展示。不能先 filter 后用 map 下标重新编号，也不 clone row 或向定位传递缩小的 snapshot。

### 2. 仅整理原 ready 列表

读取 ready 且 snapshot.frames 非空时显示具可访问名称、数量、aria-pressed 的按钮组；零数量选项可选择。saved=false 非空照常筛选，原未保存标题保持。读取中、错误或合法空投影无控制/旧列表；必须等待可辨识的 ready 文本再验证空态阴性，不能只等始终存在的 region。

筛选零命中显示“这份当前草稿投影中没有符合该条件的条目”，提供“查看全部条目”；仍显示原 saved/revision 摘要、removed_asset_ids.length 和媒体口径。removed 可重复，保留原条目数量，不去重。missing_reason 是原每行信息，筛后可见行保持其缺媒体/不可定位说明；隐藏行不另造全局缺媒体列表。正文保持纯文本，raw ID 与 preview 不进入新 DOM 属性/链接/媒体节点。

现行定位继续用完整 snapshot 与原 row 调用保护的 roughCutFrameNavigation；included/pending、saved、preview 或 missing_reason 不作为新增资格。过滤不自动定位，鼠标/键盘移焦引起父文章自然 blur 时沿用原反馈清理。

### 3. 选择属于读取，不属于资产快照

Panel 局部选择保存 `{ snapshot, requestGeneration, filter }`。渲染时只有它与当前 ready snapshot 的实际引用及读取世代相同、读取仍有效，才使用 filter，否则直接显示 all；不能仅在 effect 后 reset。即使测试桩将同一 snapshot 对象用于 R1/R2，也由 generation 区分，R2 首次可见默认全部。

过滤 handler 捕获事件所属 snapshot/generation；消费时对比最新 readStateRef.ready、相同 snapshot、requestGeneration 和 currentScope，并核最新永久失效值/ref或等效失效世代证据。不能只检查闭包捕获的 scopeInvalidated=false。相同选项保持幂等。关闭卸载/重开、重读和 user/services/chapter 对象变化沿用现首帧隐藏与永久失效，不使 A→B→A 复活旧选择或旧读取。

选择不绑定 assets 对象、token 或 assets generation。普通 assetDirectory loading/error 到 ready，只更新已有定位资格，保留粗剪 snapshot 和当前选择，不重新 GET 或自动 focus。筛选状态不进入 GET effect 依赖，也不调用 invalidateCurrentFrameNavigation、onClose、onUnauthorized、读取登记或改变请求世代；不改变父读取身份。显式重读仍执行原 invalidated 与 GET 流程，新的 ready 默认 all。

### 4. 可执行的局部验证

helper 覆盖四布尔组合、重叠计数、零命中、原索引/原对象与无效 ID 保留；完整 snapshot 与 row 身份由 Panel locator spy验证。Panel 覆盖 saved=false 非空、ready 空/读取错误/读取中、摘要/removed/缺媒体说明、asset late-ready 不重置、原行定位和零读取副作用。

通过观察性公开 react/jsx-runtime mock 保留正常元素创建，捕获实际 button props.onClick 后真正调用并 await act；不读取 Fiber、不增加产品测试 hook、不以 detached DOM fireEvent 当作旧闭包调用。R2/重开 R3 先选择 non-all，再回放 R1；检验当前非全部与零命中/列表、GET和invalidated次数均不变。包含同 snapshot 重用于新 generation 的反例，避免默认 all 或对象不匹配掩盖守卫缺失。

增强既有 user-only/services-only/chapter-object 首父 useLayoutEffect 与 ABA 例，观察旧控制/数量/内容已隐藏；旧 GET success/401 必须 resolve/reject 原 Promise 并 await同Promise及 act。粗剪没有媒体摘要流程，不新增摘要矩阵。Workspace 仅增强现真实粗剪定位和两用户迟到用例；对当前 live series/chapter/rough region 使用 within，关闭重开/新登录重新获取 owner DOM，登录可真实 click+paste。保留 click/Enter/focus、原Promise、token/logout、24条及tasks路径，不增 timeout/skip；若性能需拆代表路径，完整保留独立整合证据。

### 5. 样例、验收与允许文件

原样 demo 第一章：首个保存条目 included=true/pending=false，后续新增稳定条目 included=false/pending=true，同一条可在排除和待安排中找到；其他章 saved=false 初始投影可验证筛选及 pending 零命中。原章节、24剧集、media/notes/rough数据不变。现 loopback fixture 的 normal/unsaved/legacy/pending/error/delay 能力用于代表验收，unsupported组合使用自动 mock，不扩 fixture 控制或业务接口。

root 独立在1280和390视口验证数量、重叠、零命中、原序号/定位、键盘失焦及无溢出；按筛选与定位操作前后日志验证零额外业务/OPTIONS/媒体请求。开发 StrictMode 打开面板及显式重读仍按实际原GET次数记录，不能声称整段流程零GET。browser取消/晚到server200和自动 ignored-abort Promise真正settle严格分层。

仅四 existing mutable：`PersonalRoughCutPanel.tsx/.test.tsx`、`Workspace.test.tsx`、`styles.css`；仅两 new：`roughCutStatusFilter.ts/.test.ts`。ChapterBrowser产品/测试、Workspace产品、第十四定位helper、notes第十六筛选、DTO/API/services、fixture/demo、样例/依赖和前十六历史保护。十二业务模式、exact-five来源复制保持，README/compat及本change验证文档仅事后由root授权代理收口。

## Risks / Trade-offs

- [重叠计数被误当互斥分组] → 独立谓词和简短说明，全部基于完整当前草稿，不求三个条件总和。
- [未保存或无视频被误判不能看/定位] → 沿用原列表与身份规则，不搬notes的noSavedRecord或媒体核验门槛。
- [过滤下标或克隆破坏原定位] → 原索引包装、实际row及完整snapshot，原定位helper保持保护。
- [R1选择污染R2或asset迟到重置] → snapshot+读取generation绑定、latest永久失效守卫；资产快照不参与筛选身份。
- [长整合用例接近默认超时] → live区域查询与粘贴，真实行为证据保留，记录失败和成功，不推断未证实环境原因。
- [样例/审查能力被夸大] → 原样浏览器代表性、完整自动组合分层；Grillme独立离线待办，不替代。

## Migration Plan

四规划经root审读后由GPT-6 Luna/xhigh实施本局部范围，root独立全量/浏览器与事后四文档收口。回退只移除本地筛选/helper，原粗剪读取、顺序、状态和定位保持。无真实FastAPI/PG/Worker/队列/R2/付费或生产调用，无提交推送部署归档。

既有同回合规划后apply授权持续；此子任务仅规划，不实施。分析/复核沿请求并复用的GPT-6.1 Sol/xhigh路由，runtimeIndependentlyVerified=false；指定GPT-5.6 Sol/xhigh Grillme离线未执行，不启动/探测/配对/降档或由本Sol替代。
