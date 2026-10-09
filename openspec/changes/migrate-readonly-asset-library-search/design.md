# Design

## Context

动机见 proposal.md；行为契约见 specs/readonly-asset-library-search/spec.md。本批需要设计，原因是本地可见性会与完整目录身份、读取世代及既有目标导航交叉。

当前 `AssetLibrary.tsx` 的关键事实：

- 146–151、221–247：角色/道具显示原 `name`，场景显示原 `title`，空名称采用占位；别名仅展示非空字符串。
- 161–170、502–540、631–662：入口资格和 usage owner 以完整目录原始 ID 唯一性、所属剧集、原 row 引用和目录快照判断。
- 339–456、458–464：目录按 user/services/series scope、分类和 request generation 隔离，当前有效 401 沿用既有会话处理。
- 579–628：卡片原键含分类、原 ID 和完整目录索引，现有安全图片沿原 allowlist 展示。
- 671–770：新素材导航必须从完整新目录核验身份，实际 DOM 焦点成功后才消费；手动分类切换、重读与 tombstone 行为已经存在。

现有 demo 三类各两项，有“林编辑/阿岚”“旧街早晨”“无署名信封/蓝印信封”等别名，足以演示名称、别名及零命中。跨分类相同 raw ID 合法，不能按名称、别名或跨分类 ID 合并。

冻结来源 SHA 为 `23403806898550a7668a6ee7c0c457315655c39b`；沿用既有名称/别名 wire 契约，不修改或复制来源。十三份全文 SHA/bytes 收据为 [sources.json](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-library-search-sources.json)，根代理静态复核见 [frozen-source-check.json](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-library-search-frozen-source-check.json)。本批搜索是新增 UI 改善，不声称冻结旧 UI 已有相同搜索。旧 GET 的既有认证维护边界保留，隔离 fixture 不能证明真实数据库纯读取。

开工基线 [baseline.json](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-library-search-baseline.json) 记录 217 文件、102 个前十七批历史产物和 95 个受保护代码文件；根代理另记录代码基数 99。基数仅用于保全比较，不是本批验收结果。

## Goals / Non-Goals

**Goals:**

- 以当前完整成功目录实现本地缩小展示，保持原对象、原索引、原顺序与现有身份权限。
- 查询、输入回调与清空回调绑定准确读取世代；首可见提交隔离旧查询与目标导航。
- 保持现有 GET、缓存、错误、破图恢复、关联镜头统计与实际 DOM 导航语义。

**Non-Goals:**

- 不做全文、跨分类、模糊、拼音或正则搜索，不搜索隐藏字段，不改变别名/唯一身份语义。
- 不修改 Workspace 产品、ChapterBrowser、DTO/API/service、fixture、样本、依赖或已有导航 helper。
- 不新增网络、媒体链路、存储、后台写入或真实服务验收；不推送、部署或归档。

## Decisions

### 1. 纯 helper 输出带原索引的匹配信息

新增 `assetLibrarySearch.ts` 及测试。输入是分类、完整原数组与单一查询字符串；查询仅 `trim().toLowerCase()`，候选原字段仅按 `toLowerCase().includes()` 判断。非空别名按现有可见规则参与；未命名占位不参与匹配。

输出保留原 item 引用和完整数组索引，可用只读 indexed entries 或同等匹配标记；不复制 row、不排序、不更改任何字段。空查询匹配全部。使用原始字段而非整张卡片文本，避免描述、隐藏字段或占位被无意纳入。

### 2. 完整卡片继续挂载，隐藏匹配外节点

完整目录仍按原 key 渲染卡片。搜索仅为原索引对应节点设置 `hidden/inert`，并保证 CSS 不让已有 grid/card display 覆盖隐藏行为。隐藏节点不能被 Tab 或可访问树访问；清空查询恢复原节点，避免重挂载重置图片状态。

完整 `currentResult.items` 继续用于重复 raw ID 计数、belongs、原 row 引用、usage owner 和定位；绝不能把可见子集送入身份函数。分类 tab 总数保持完整目录数量，另显示“当前分类显示 M / N”；零命中说明限定当前这份快照，并保留显式清空。

### 3. 查询绑定当前读取，消费旧回调前核最新值

局部查询状态绑定 LibraryScope 实际对象、分类、当前 ScopedAssetResult、request generation 及成功目录引用。首 render 的派生检查使绑定不匹配时查询为空，避免仅用 effect 清空而首提交泄露旧查询。

分类实际切换、显式重读、scope 变化、关闭重开及新导航 epoch 重置查询；同分类重复点击沿用已有 no-op。新读取即使复用同一 items 数组也按 generation 重置。正常 token 更新不改变上述 scope/read 身份时保持查询。

输入 onChange 与清空 onClick 捕获当前成功读取 ticket；消费时先检查最新 scope/result/category/request generation、永久失效值及当前绑定，再执行任何查询、usage 或反馈状态修改。捕获时有效不代表回调执行时有效；scope A→B→A 与旧 handler 不能复活旧状态。不改变父级注册、tombstone 或请求协议，也不把查询加入目录读取 effect 依赖。

### 4. 查询与已有 usage、导航协作

原始输入值实际改变才更新查询并沿用既有关闭 usage/清反馈操作；同值不重复关闭。搜索和清空不会重读章节、重置目录缓存、清除 broken image 状态或自动打开新 usage。新 usage 的统计仍来自原章节投影。

新导航 epoch 首可见 render 必须是空查询，随后沿既有 fresh category GET 与完整目录身份判断定位。pending 同分类显式重核保留 target 并清空查询；手动其他分类放弃 target。成功后的旧 intent prop、consumed/abandoned epoch 及旧 close 回调保护原样保留。导航 DOM refs 仍对应完整原 key 节点，隐藏旧查询不能阻碍合法新目标。

### 5. 验证代表路径及真实 handler

helper 覆盖三分类、别名、空字段/占位、大小写/首尾空白、字面特殊字符、排除字段、原顺序/index/ref。

组件覆盖 ready 非空、零命中及清空、读取中/错误/合法空目录不造搜索；查询关闭 usage/反馈、隐藏卡片 focus/AX、同节点/破图状态不重建；重复 ID 被过滤成单可见项仍无入口资格。

生命周期使用父 useLayoutEffect 观察 user-only/services-only/series scope 首提交与 ABA；新读取复用 items 对象、新导航 epoch、新目标 fresh read 均从空查询开始。通过观察公开 react/jsx-runtime 的原 input onChange/清空 button onClick 保存真实闭包并实际调用，R2/R3 先输入新的非空查询后重放 R1，断言新查询、usage/反馈和读取计数不变；不使用 Fiber、产品测试 hook 或 detached DOM 的 fireEvent 冒充闭包调用。旧 GET/401 与 usage response 必须真正 settle 原 Promise 并 await act，不只检查 abort signal。

Workspace 集成采用实际 demo/service/Panel，使用当前 live region 查询和 click/paste 降低大树查询成本，保留三分类、既有导航、24 条剧集/筛选/任务与两用户旧请求隔离证据，不提高 timeout 或跳过用例。真实浏览器用现样本验证 1280 与 390 的名称/别名、零命中、清空、Tab、原导航和布局；未命名、重复身份等仅自动化构造，不冒充样本浏览器覆盖。

### 6. 请求与媒体证据单独计数

搜索输入、清空和旧 handler 拒绝的分类/章节/其他业务 GET 增量必须为零；现有安全 PNG、OPTIONS 与业务请求分别按实际日志记录。保留完整卡片减少重挂，但可见性与 lazy image 仍可能触发现有安全图片读取，不承诺 PNG 零增量，不声称全局外连审计。新目标导航本身沿既有 fresh GET，现有 SeriesPage 重校验独立记账，不归为搜索 GET。

## Risks / Trade-offs

- 可见子集误成唯一身份 → 所有授权继续用完整原数组，加入“两个相同 ID 仅一个命中”的反例。
- 旧输入 closure 改写新读取 → 最新 scope/read/generation 核验先于任何 mutation，同 items 新世代与 ABA 实际回放。
- 搜索隐藏导航目标或丢失卡片节点 → 新 epoch 首帧清空，完整 key 节点 hidden/inert 保留，并验证实际焦点与原节点。
- 大型 Workspace 测试超时 → 限定 live region、合理拆分代表路径；保持真实 Promise、键盘与原断言，默认 timeout 不变。
- 本地搜索被误称无任何媒体请求 → 无新增媒体链路与实际既有 PNG 计数分开陈述。

## Migration Plan

仅四个既有源码文件可变：AssetLibrary.tsx、其测试、Workspace.test.tsx、styles.css；仅新增 assetLibrarySearch.ts/.test.ts。其他产品/契约/样本/来源保持基线。实现后由根代理独立运行 typecheck、全量测试、build、foundation/来源五项校验、严格 OpenSpec/文本/保全检查及隔离浏览器验收，再授权补 README、compat、本 change verification 与 tasks 的实际证据。

用户已有同回合规划后 apply 持续授权；本规划子任务完成即交根代理验核，由 Luna 实施，不自行实现。GPT-6.1 Sol/xhigh 为请求/复用路线，runtime 未独立核验；GPT-5.6 Sol/xhigh Grillme 离线独立待办，禁止探测、启动、配对、降档或替代。
