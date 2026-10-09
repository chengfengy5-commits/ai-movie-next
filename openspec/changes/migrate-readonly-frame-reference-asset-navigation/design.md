# Design

## Context

详见 `proposal.md` 的动机和 `specs/readonly-frame-reference-asset-navigation/spec.md` 的可观察合同。现有 `FrameAssetReferencesPanel` 位于 `ChapterBrowser` 的章节视图，通过既有关联素材投影 `frameAssetReferences.ts` 展示已解析类别引用并只读加载对应类别目录；素材库已有角色、场景、道具分类、分类缓存和目录刷新；工作区维护用户、services、剧集和当前视图。章节页还维护既有章节级/分镜级导航。本 change 不修改既有关联素材投影 `frameAssetReferences.ts` 及其测试，也不改 DTO、服务、demo、fixture 或依赖。

### 冻结旧源与本次新增导航的界限

旧源仅作为静态兼容证据读取：固定 commit `23403806898550a7668a6ee7c0c457315655c39b`。`js/episode.js` SHA-256 为 `79b117ce13e633b6b641faca7232328358e8e6f3be40825dcf097f87994d0e9f`；类别引用与 raw ID 相关片段位于 400–407、421–439、712–731。`backend/app/routes/series.py` SHA-256 为 `db9721e34a3c5f22f81ce7cf4bc5434d9f3661d732ab1e8a79ba2e74852243cf`；既有角色、场景、道具目录 GET 位于 1278–1286、1382–1390、1467–1475，保持接口返回顺序，不为导航另行排序。两文件完整哈希登记于 `/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-frame-reference-asset-navigation-sources.json`；本 change 不复制这两份旧源。

旧 `episode.js` 的 5245–5277 涉及管理/聊天内容，2855–2907 涉及混合链路；这些流程不迁移。本 change 是在现有只读章节引用和素材目录上新增 UI 导航衔接，不声称旧产品已有独立的“引用→素材库卡片”只读入口。

## Goals / Non-Goals

**Goals:**

- 将已解析分镜引用的原始分类素材 ID 从来源 ready 快照传递到目标素材库，并在新分类目录和当前 DOM 中精确定位。
- 让来源 ticket、已接受意图、目标读取和定位反馈在用户、services、剧集、分类、owner、快照与世代变化时各自失效，阻止 A→B→A 旧回调复用。
- 目标错误时保留可解释的当前素材库状态和完整意图，允许用户明确重新读取同一分类。

**Non-Goals:**

- 不修改既有关联素材投影 `frameAssetReferences.ts` 及其测试，不增加 DTO、API、后端、fixture、sample 或依赖。
- 不增加按素材搜索、详情编辑、写入、锁、媒体展示/读取或更改章节、剧集的行为。

## Decisions

### 从原始 frame 与目录快照派生来源身份

来源新增局部 helper，使用当前分镜类别数组的原始索引读取 raw ID；不从投影 `row.key` 解析索引，也不把名称、别名、标题、位置或图片 URL 当身份。每个引用行与投影行按原顺序对应，只有投影状态为 resolved、当前 ready 类别目录中该 raw ID 总数恰为 1、该记录属于当前剧集时才提供入口。同一个分镜中重复引用同一合法 ID 是多个合法原始行，按顺序保留并可分别导航。若某引用项的原始值、投影行或身份不能对应，只显示安全说明且不发额外 GET。

替代方案：复用 `row.key` 拆出数组位置。拒绝，因为 key 是投影展示键，不是公开身份协议；这会把导航绑定到受保护的第九批实现细节。

### 让素材库接收独立的一次性素材意图

`FrameAssetReferencesPanel` 位于 `ChapterBrowser` 的章节视图，回调经 `ChapterBrowser` 桥接后交给 `Workspace`；目标页面才是 `AssetLibrary`。工作区保留既有章节和分镜导航意图，新增互不混用的素材导航意图 `{ userId, services, seriesId, category, assetId, navigationEpoch }`。来源 panel 与章节页桥接核当前 owner、类别目录/读取世代和 `isCurrent`；工作区独立核来源回调捕获的 render context 对象仍是当前对象、当前视图仍为 chapters、剧集仍一致且 `can_enter` 为 true 后接受并复制原始身份。接受后来源 panel/章节页关闭或卸载不撤销已复制意图；来源在接受前失效的回调不得导航。目标意图通过 epoch 去重，失败时保留，目标卡片实际定位并聚焦后才消费。

替代方案：让来源 panel 自己切换素材分类，或复用章节分镜 target。拒绝，因为前者绕过工作区路由与目标目录新鲜度，后者会把素材身份混入 chapter/storyboard 身份并改变既有章节导航。

### 目标分类从空状态读取新快照并做全目录身份核对

目标 `AssetLibrary` 对新意图首个可见提交隐藏旧卡片、旧选择、错误、缓存和定位反馈；初始分类直接设为意图中的角色/场景/道具类别，只对该类别发一次 fresh GET，不先读角色目录，也不复用来源或旧分类缓存。完整响应到达后，先统计所有记录的原始 `id` 次数，再检查唯一记录的 `series_id` 与目标剧集一致。目标记录不唯一、不存在、跨剧集或响应为空时不聚焦、不消费，保留意图并提示显式重新核对；该操作重新读取完整目标类别，不读其他类别。有效匹配时使用当前目录记录对应的卡片 DOM 引用进行聚焦；只有确认 `document.activeElement` 为该节点、scope/epoch/目录和节点仍有效且可见后才滚动并消费。

接受意图时即使该类别已有缓存，也必须重新读取；替代方案是直接用缓存定位。拒绝缓存方案，因为来源与目标可能跨越目录更新，缓存会展示过期身份。

### 以完整来源/目标世代约束迟到回调

Panel 的来源 ticket 绑定当前 owner、所选类别、ready 目录对象、读取世代和 `isCurrent`。类别变化、当前项变化、目录重读、关闭、离开、登出或 scope 变化会使它失效。目标素材库将请求代次和 intent epoch 绑定到 user/services/series/category，首个 layout commit 先隐藏旧视图；scope 变化永久终结旧 owner，A→B→A 不能恢复。所有旧 Promise 必须以实际 success/401 settle 后确认 generation/scope 不再当前；忽略结果和注销回调。current 401 使用现有认证流程。

手动切换到另一类别会放弃未完成意图；重复点击当前类别是 no-op，不取消加载。用户在同类别显式重读或对未定位目标执行“重新核对”时，以新请求世代替换旧读取，保留同一目标并重新读取完整目标类别目录；旧 Promise 的 success/401 不得提交。成功定位后，父级消费意图，普通刷新不重复定位；返回列表、任务视图、关闭与登出使未完成意图失效。用户切换后的关闭/迟到旧回调不得清理新 intent。

替代方案：只靠 `AbortController` 阻止旧操作。拒绝，因为取消可能迟于响应或服务忽略 abort，generation 与完整 scope 检查仍是必要的发布条件。

### 用当前卡片 DOM 进行可访问定位

每个当前分类卡片维护局部 id→DOM 引用，不从显示名称查找 DOM。定位前确认当前记录原始 ID 在完整响应中唯一且剧集匹配；节点 connected、属于当前可见分类内容且不在 hidden/inert 下。调用 `focus({ preventScroll: true })` 并确认焦点实际转移，再复核 scope、intent epoch、目录快照和节点后才滚动；失败时保留意图与显式重核入口。短暂反馈在卡片 blur 时清除，且定位动作不创建媒体元素或主动媒体请求。现有章节级、分镜级导航保持原时机和目标语义。

替代方案：先打开目标卡片再根据名称或当前位置滚动。拒绝，因为重名、排序和异步列表会错误定位。

## Risks / Trade-offs

- [来源 projection row 与原始数组发生结构性错位] → 局部 helper 按共同原序逐行核对，不解析 key；增加原序、重复引用与缺项反例。
- [目标目录中 raw ID 重复或跨剧集] → 对完整响应先计数后校验归属；失败保留目标意图，不用名称或首个匹配项回退。
- [同视图重入后旧 callback 被新状态重新解释] → 父级 captured render context 对象身份、owner 和 epoch 联合核验，覆盖同 Dashboard 视图 A→B→A。
- [卡片 DOM 尚未提交或 focus 未生效] → 验证 connected、可见、列表归属及实际 activeElement；未定位时不消费。
- [正常滚动触发既有 lazy 图片加载] → 不新增媒体节点、URL 或主动读取；验证只检查新增导航行为，不把既有安全图片加载算作新媒体功能。
