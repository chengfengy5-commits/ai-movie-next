# Verification

Date: 2026-10-05 (Asia/Shanghai)

## Scope and compatibility

本切片实现剧集内角色、场景和道具的只读浏览，仅新增三条素材列表 GET：

- GET /api/series/{series_id}/characters
- GET /api/series/{series_id}/scenes
- GET /api/series/{series_id}/props

加上前两切片，隔离 fixture 的业务白名单共八条。页面一次只读取当前分类，保留 API 返回顺序；成功响应（包括空数组）只缓存于当前用户、剧集和已挂载的素材页内。重新读取只失效当前分类缓存与破图状态。DTO 保留继承字段及必需可空字段；规范名、音频地址和声音引用不向页面展示。图片仅通过已有 loopback 同源校验，演示图片使用 CSS 占位，不发起业务或媒体请求。

冻结源码仅通过旧库 SHA 23403806898550a7668a6ee7c0c457315655c39b 的 git show 静态读取。根代理核对了 design 引用的六份源码全文哈希、schema 继承字段、访问校验与旧 GET 行为。exact-five 来源合同测试通过 3/3；来源 verifier 核对五个批准条目与 SHA，敏感模式扫描为 0 命中；新 change 的 OpenSpec strict 验证通过。旧库 HEAD 仍为冻结 SHA，唯一未跟踪项仍是原有 .spec-superflow.yaml；前两 change 的 12 个文件哈希与本轮开始快照一致。legacy-reference 没有新增文件。

## Automated checks

以下命令均从 /Users/yanghaibo/data/projects/ai/haoai-next 执行。

| Command | Result |
| --- | --- |
| npm --prefix frontend run typecheck | 最终复验通过，exit 0。 |
| npm --prefix frontend test | 11 个测试文件，163/163 通过，exit 0，16.41 秒。首次全量有一个既有 5 秒测试因加入额外导航而超时；将素材来回拆成独立用例后复跑全量通过，没有更改全局超时。 |
| npm --prefix frontend run build | 通过；Vite 46 modules，1.30 秒。输出体积（gzip 前）：CSS 27.71 kB（gzip 6.68 kB），JS 279.61 kB（gzip 85.01 kB）。之后未更改生产代码。 |
| npm --prefix frontend test -- src/features/assets/AssetLibrary.test.tsx | 1 个文件、11 项通过。覆盖纯文本、隐藏音频/规范名、空名称与空列表、错误恢复、会员与普通 403、当前 401、pending 时刷新后迟到 401、切类型迟到成功不写缓存、同 ID 图片按类型隔离及本地媒体限制。 |
| npm --prefix frontend test -- src/app/Workspace.test.tsx | 拆分后 1 个文件、17 项通过。覆盖列表筛选和分页保留、hidden/inert、返回后换剧集、退出后换用户，以及旧请求的成功/401不干扰新会话。deferred promise 均在 await act 中明确 resolve/reject 后断言。 |
| fixture agent targeted API run：npm --prefix frontend test -- src/shared/api/services.test.ts src/shared/api/chapters-http.test.ts src/shared/api/assets-http.test.ts | 3 个文件、44 项通过；动态 loopback HTTP 覆盖新增 GET、Bearer、ID 编码、取消、空/坏响应、错误状态及 timeout。 |
| node --test scripts/source-baseline-contract.test.mjs | 3/3 通过。 |
| node scripts/verify-source-baseline.mjs | exact-five 白名单与源/目标哈希通过，敏感模式命中 0。 |
| openspec validate migrate-readonly-asset-library --strict | 通过。 |
| 根代理对 37 个实现/测试文件的文本扫描 | 尾随空格、冲突标记、缺少 EOF 换行均为 0。 |

响应字段对照是冻结 Pydantic 源码的静态检查，不代表真实 FastAPI 回归。

## Browser acceptance

根代理用 Codex 应用内浏览器控制真实 UI，通过语义定位、点击、输入和键盘操作验收；这些记录独立于 Vitest/jsdom。

演示模式实际操作包括登录、全部列表加载至 24 条、进入个人及团队素材库、切换三类、重新读取、返回后保留 24 条、团队筛选后以 Tab 从章节入口移动到素材库入口并打开团队素材、既有章节流程及退出。受限剧集没有素材入口。键盘可以到达入口、分类、刷新与返回；系列列表 hidden/inert 时没有焦点留在隐藏区域。1280px 桌面与 390px 手机视口的 scroll width 分别为 1280 和 390。演示 DOM 无图片或音频元素，自动 fetch spy 也没有请求。全四类剧集筛选由自动 UI 测试覆盖。截图：

- /Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-assets-desktop.jpg
- /Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-assets-mobile.jpg

隔离 API 模式使用 loopback Vite 127.0.0.1:5174 与 fixture 127.0.0.1:4175。实际操作覆盖三类型、跨类型同 ID 的独立图片、别名和空名称、A→B→A 缓存、重复选择、显式刷新、空数组缓存和刷新恢复、普通 403、500、invalid JSON、当前会话 401，以及素材图片 404 占位后同 URL 恢复。角色 membership 403 提示另由 UI 自动测试覆盖。fixture 五轮服务端请求日志共 55 条，业务请求恰为八条白名单路径；其余仅 OPTIONS 和允许的 loopback 合成媒体。日志无锁、写入、duplicates、ignored、生成、远端媒体或凭据。临时 API 页签与进程已关闭/停止。截图：

- /Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-assets-api.jpg
- /Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-assets-broken-image.jpg

GPT-6.1 Sol / xhigh 独立源码与测试只读复审未发现未解决的实证 P1/P2；会员 403 识别和 UI 断言已闭环。该复审不替代固定模型 Grillme 审查。

## Boundaries

fixture 与浏览器验收没有导入或启动旧 FastAPI、PostgreSQL、Worker 或 R2，也没有访问生产或发起付费请求；因此不证明真实后端授权、数据库排序或生产媒体行为。没有 commit、push 或部署。Grillme 所需 GPT-5.6 Sol / xhigh 当前离线，task 6.1 是唯一保留未完成项。
