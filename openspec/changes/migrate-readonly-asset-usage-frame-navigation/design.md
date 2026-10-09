# Design

## Context

参见 [proposal.md](./proposal.md) 与 [spec delta](./specs/readonly-asset-usage-frame-navigation/spec.md) 的外部行为约束。当前素材反查面板打开时读取一次章节响应，并以它生成文字投影；投影只提供章节展示位置、分镜展示位置和文字。章节级按钮由该 ready 快照的原始章节 ID 建立。目标 `ChapterBrowser` 会重新读取章节，再通过既有章节素材流程构建可展示的分镜及其 DOM 引用。工作区在素材页切到章节页时卸载来源面板，因此目标身份必须在接受时复制，不能把来源回调或投影位置延长为目标身份。

本地静态实现依据为第十批既有 `assetFrameUsage.ts` / `assetFrameUsage.test.ts`、`AssetFrameUsagePanel`、`AssetLibrary`、`Workspace` 与 `ChapterBrowser`；本设计不改第十批投影契约。冻结来源定位见 `/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-usage-frame-navigation-sources.json`，旧仓库基线为 `23403806898550a7668a6ee7c0c457315655c39b`。`episode.js` 10144、10714 附近可用于核对 `storyboard[0]` 的原始身份与章节素材关系；10200–10204、10643–10649、10669–10693、10714–10732 仅作安全章节内定位参考。`personal_production_media.py` 91 的实现没有用位置代替身份。旧 `selectFrame` 2630–2656 混有聊天历史状态，不能复用为跨页面定位规则。

## Goals / Non-Goals

**Goals:**

- 把素材反查中已核验的类别引用和 frame `storyboard[0]` 身份作为目标意图，并由新章节、分镜素材快照再次核验。
- 只在目标 frame 的当前 DOM 节点确实可定位时聚焦和滚动，并在成功后消费意图。
- 保持第十一批章节级入口与普通章节浏览行为兼容，隔离用户、服务、剧集、来源打开世代及迟到响应。

**Non-Goals:**

- 不改 `assetFrameUsage.ts` 或 `assetFrameUsage.test.ts` 的第十批投影，不改 DTO、services/API、demo 样本或 fixture，不增加依赖。
- 不定位到素材 URL、图片地址、`frame_index`、标题或邻近位置；不新增媒体元素、主动媒体请求、写入、编辑、锁、播放或导出。
- 不改个人制作记录的 resume 验证和反馈；其定位反馈不得被此功能伪装或覆盖。

## Decisions

### 从当前反查 owner 和 ready 快照派生独立的 frame target

在 `AssetFrameUsagePanel` 中新增局部、显式的分镜身份 target，而不是扩展第十批纯文字投影。由投影的章节/分镜位置只在同一份 `readState.status === ready` 响应中取回原始 `Chapter` 与原始 frame；这些位置只作快照内索引，不进入导航身份。来源创建 target 前依次确认：素材 panel owner 仍有效；对应章节的原始 ID 在**全章节响应**中恰好出现一次且 `series_id` 等于当前剧集；该命中 frame 的 `storyboard[0]` 是非空原始字符串且在该章节中唯一；当前 owner 的类别素材仍由精确类别字段引用，且类别引用素材 `assetId` 与已验证素材目录项一致。同一类别字段重复引用同一素材 ID 合法，不使身份无效、不增加该 frame 的入口数，并保留既有投影引用次数表现。跨类别同 ID、别名、trim 后相等都不构成匹配。

有效 frame 显示单独的“定位对应镜头：章节 · 镜头<n>”动作。章节身份有效而 frame 身份无效时，仅隐藏该 frame 动作并显示不含 raw ID 的说明，保留原章节动作。章节动作继续只建立 chapter-only target。两类入口同在一个面板时，source read count 不增加：使用已 ready 的章节快照即可。

来源回调携带完整 owner/目录/ready-state/request-generation ticket。回调发出时 panel 与 `AssetLibrary` 当前 owner 逐层验证；`Workspace` 再核对 `dashboardScope`、用户、services 实例、视图为当前素材页、剧集对象和 `can_enter === true`。接受前任一来源 owner/scope/read-generation 失效均拒绝 callback。Workspace 接受时复制 `{ userId, services, seriesId, chapterId, navigationEpoch, frameTarget: { storyboardAssetId, category, assetId } }`；`storyboardAssetId` 是该命中 frame 的 `storyboard[0]` 原始 ID，`assetId` 是其当前类别引用的原始素材 ID，两者不可混用。接受后不再调用来源 `isCurrent`：自然卸载、关闭或重放旧关闭回调均不能撤销已接受意图；之后目标仅受目标/Workspace user、services、series、navigationEpoch scope 和用户显式放弃行为约束。

**备选方案：**把 `framePosition`、`frame_index` 或显示标题放入投影后直接传到目标页。拒绝，因为新章节排序或素材快照变化可能令同一位置对应另一个 frame。

### 分镜目标分阶段核验，区分早期失配与素材响应失配

工作区使用现有章节导航状态承载内部的可选 `frameTarget`；章节级入口不含该字段。Frame target 分别绑定目标原始章节 ID、类别、类别引用原始素材 `assetId`、来源 `storyboard[0]` 原始分镜 `storyboardAssetId`，并使用目标 navigation epoch。`ChapterBrowser` 在接收后先隐藏旧内容与旧定位反馈，读取当前剧集章节目录一次。

先在完整 fresh directory 中统计目标 raw chapter ID；只有恰好一项且同剧集才继续。再在该章内容中按已复制的 `storyboardAssetId` 找到唯一 frame，并确认其指定类别至少一次仍含已复制的 `assetId`；重复类别引用合法。不能先选默认首章，不能根据旧 frame position、名称、`frame_index` 或近邻项猜测。此阶段任何章节、frame 或类别引用失配（包括合法空章节）都不选中目标、显示显式重新核对提示，并使 storyboard asset GET 数为 0。

前置身份有效后，复用当前 `listStoryboardAssets(seriesId, chapterId, signal)` 一次读取该章的新素材响应。目标响应 asset raw ID 必须等于已复制的 `storyboardAssetId`（来源命中 frame 的 `storyboard[0]`），且在整份响应中唯一、`series_id` 和 `chapter_id` 均精确匹配。类别引用 `assetId` 仅确认 fresh frame 仍含同一类别素材引用，不与响应资产 ID 比较。此处资产 ID 缺失、重复或归属错误时本次 GET 已发生（正好一次），但不得定位或消费；保留已核验目标章节只读内容和未消费意图，并显示未定位提示。显式重新核对从 fresh chapters GET 开始，再按前置身份结果决定是否发 assets GET，不得只重试旧素材请求。确认 frame 与 fresh asset identity 后，按章节当前内容顺序求当前 DOM position；该位置只用于引用已确认对象对应的节点。不能要求 `frame_index` 顺序与章节分镜相等。image URL 为空、不允许显示或图片失败只影响既有图片显示，不能否定已经核对的文字身份。

请求计数约束：有效来源打开不增加现有 source chapter GET；点击有效导航后目标章节 GET 为 1；章节 ID、frame `storyboard[0]` 或类别引用在前置检查失配时目标 assets GET 为 0；前置检查通过后才有且仅有 1 次 target assets GET；asset snapshot 中身份错误属于后置失配，asset GET 数为 1。无效意图不得因为重复点击、strict-mode probe 或当前回调变化增加读取。

**备选方案：**在目标章节页立即选中章节，并在资产读取完成后按素材顺序或 `frame_index` 猜一个 frame。拒绝，因为数据顺序可能不同，且该做法会在目标身份无法核对时展示并聚焦错误内容。

### 让两类导航的接受与消费时点保持不同

Workspace 接受 frame 意图时复制 `{ userId, services, seriesId, chapterId, navigationEpoch, frameTarget }`。接受成功后 source panel 卸载不撤销它。若 user、services 或 series scope 改变，父级和目标页在首个 layout commit 即隐藏旧目标并永久结束旧 epoch；A→B→A 不得重新应用旧意图。目标页当前有效请求的 401 沿用现有登出；已失效请求的成功或 401 必须在 Promise 实际 settle 后仍被 generation/scope guard 丢弃。

Chapter-only 意图保留第十一批行为：fresh chapter 唯一匹配并选中时消费；不自动聚焦 frame，也不等待 chapter assets 完成。Frame intent 则在新 chapter 与 asset 快照都核验后，先调用目标节点 focus 并确认 `document.activeElement === node`，再复核 owner、epoch、当前 chapter/assets snapshot、节点连接和可见归属；只有复核通过才滚动、显示成功反馈并消费。焦点未实际转移不得滚动或消费。遇到错误或不确定时保留意图，必须用户显式重新核对；章节/frame 前置失配不选中目标章节，素材响应缺失/重复/归属错误或素材请求非 401 失败时保留已核验章节只读内容。每次显式重核都从 fresh chapters GET 开始；成功聚焦后手动切章、普通章节重读、返回列表不会重放它。用户在尚未完成时主动选另一有效章节可放弃 frame intent。返回仍走现有剧集列表，不恢复素材面板。

显式重新核对推进导航和请求世代，清旧资产缓存、选择和定位反馈，再从 fresh chapter GET 开始；只有新的前置身份仍有效时才发 target assets GET。非 401 错误显示对应错误和可重复的显式动作；无自动轮询或后台重试。StrictMode effect setup/cleanup 探测只取消首轮尚未发出的请求，不永久关闭当前有效 epoch。

**备选方案：**chapter GET 成功后就清空导航意图，后续资产失败时再让用户手动猜目标。拒绝，因为用户无法重试原始身份目标，且无法区分“章节打开成功”和“镜头定位成功”。

### 使用当前 DOM 引用做一次可访问定位，不触碰旧 resume 状态

ChapterBrowser 为分镜卡片维护当前 frame position 到 DOM 节点的局部引用。定位前确认 identity guard 指向当前选中章节和 asset result，节点 `isConnected`、属于可见分镜列表且不在 `[hidden]`/`[inert]` 下。随后调用 `focus({ preventScroll: true })` 并确认 `document.activeElement === node`；再复核 owner、navigation epoch、当前章节/素材快照、节点连接及可见归属仍有效，只有全部通过才调用 `scrollIntoView({ behavior: "auto", block: "center" })`、显示反馈并消费意图。焦点没有实际转移时不得滚动或消费。卡片使用 `tabIndex={-1}` 与“第 n 章 · 镜头 m”可访问名称；目标成功时给短暂、可见的 frame-navigation feedback，并在目标卡片失焦时清除。chapter/assets 重读、离开、用户或 services scope 变化也会清除目标高亮；该状态独立于个人制作记录的 resume target/feedback。

定位不创建额外 image/audio/video 元素，不新增媒体 API 或主动媒体读取逻辑，不暴露 asset ID 或 URL。分镜卡片仍沿用现有安全 loopback 图片渲染；滚动可能让原本延迟加载的图片按原页面规则加载，因此验收不把既有图片网络行为误记为本功能新增请求。

**备选方案：**用 `window.location.hash`、新增 route 参数或 URL path 表示 frame。拒绝，因为 URL 会暴露内部身份、形成越权重放或刷新恢复语义，并扩大既有路由范围。

## Risks / Trade-offs

- [来源位置与实际 Chapter/frame 对象错配] → 仅用位置在同一个 ready 原始响应中回取对象，随后全程传 raw identities；目标页用新响应重新核对。
- [同 raw ID 在外剧集、另一章节或素材分类重复] → 先统计全目录 raw IDs，再检查 series/category；不可先过滤范围后误判唯一。
- [素材顺序与 frame 顺序不同] → identity 使用 `storyboard[0]`、category reference 和 asset raw ID；position 只在校验完毕后映射当前 DOM。
- [界面成功但目标节点未挂载或隐藏] → focus 前验证 connected、list ownership 和 hidden/inert；未实际定位时不消费意图。
- [拒绝 abort 的迟到 response 或 401] → 用 epoch、scope、request generation 二次核验；deferred 自动测试须实际 settle 原 Promise 并 `await act`。
- [旧章节级流程被新延迟影响] → 无 `frameTarget` 时沿用旧选择/消费时机，加入 chapter-only 兼容回归。

## Migration Plan

仅变更本地 React 来源面板、工作区导航与章节页，不改变服务端数据、接口或部署。不需要数据迁移；若回归失败，可回滚本 change 的前端和测试文件。保持第十批投影文件及前序历史文件不变。

## Open Questions

无。目标身份与 source/target 生命周期、章节级兼容和消费时点已由本 change 的 spec 确定。
