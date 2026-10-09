# Verification：从素材关联结果定位分镜

## 范围与证据分层

本 change 在素材关联面板中为经过核验的分镜增加显式定位。它复用既有章节目录和分镜素材读取，不新增业务 API、DTO、service 方法、fixture 路由、素材样本或媒体接口。身份分别保存 frame `storyboard[0]` 的 `storyboardAssetId` 和当前类别引用的素材 `assetId`；目标端先核对完整章节目录、章节 frame 与类别引用，再核对完整素材响应，之后才尝试 DOM 定位。章节级导航维持原行为。

本地自动化、root 的 demo/API 浏览器记录、GPT-6.1 Sol/xhigh 源码审查是不同证据层次。浏览器延迟演练证明用户切换后的界面仍安全，不代替自动化中忽略 abort 的 Promise 实际 settle。所有本地验收均未连接真实 FastAPI、PostgreSQL、Worker、R2、外部媒体或生产环境；未提交、推送、部署或归档。

证据目录：[打开本轮 evidence 目录](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/)。本 change 的主要记录为 [root checks](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-usage-frame-navigation-root-checks.json>)、[API audit](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-usage-frame-navigation-api-audit.json>)、[browser observations](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-usage-frame-navigation-browser.json>) 和 [final conservation](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-usage-frame-navigation-final-conservation.json>)。

## 自动化与本地 pipeline

在同 Dashboard view A→B→A 窄修新增回归之前，`Workspace.test.tsx` 单文件定向为 52/52；这项历史运行不包含后续新增的旧父级 callback 反例。该反例加入后，主实施 Luna 执行的两个测试文件窄修定向为 4 项通过、114 项 skipped；root 随后独立运行的最终完整测试为 32 个文件、499/499 通过，包含修复后的回归。Workspace 集成覆盖三类素材从来源卡片打开到 fresh 章节/素材快照的意图传递，包括目录与镜头顺序改变、非首章节/镜头、真实 focus 与 scroll、成功后重读不重放、手动切章放弃待处理目标、来源关闭/卸载后意图存活，以及离开目标页进入“我的任务”后旧章节 Promise 的处理。退出后换用户时，旧章节/素材 Promise 的 success 与 401 均实际 settle 并经 `act` flush，不覆盖新用户状态。A→B→A 后，即使重放的旧父级回调仍返回 `isCurrent() === true`，也不能改变当前素材视图或创建新读取。目标失焦清除反馈与意图只消费一次由自动化覆盖。

ChapterBrowser 自动化验证新章节和素材身份的分阶段核对、GET 次数、章节级兼容行为、pending 同章世代隔离，以及隐藏/脱离 DOM 的真实目标节点在 Promise settle 后不聚焦、不滚动、不消费。hidden 与断开节点由真实 DOM 测试；`inert` 与节点映射缺项分支另有静态守卫核对，不将其表述为独立参数化自动反例。单测中迟到响应由 deferred Promise 实际 resolve/reject 后等待 `act`，不把浏览器 abort 当作该证据。

root 最终独立执行的流水线均 exit 0：

| 检查 | 结果 |
| --- | --- |
| `npm --prefix frontend run typecheck` | exit 0 |
| `npm --prefix frontend test -- --reporter=dot` | 32 个测试文件，499/499 通过，0 skipped；exit 0，62.67 秒 |
| `npm --prefix frontend run build` | exit 0，Vite 65 modules |
| `node --check tools/api-fixture/server.mjs` | exit 0 |
| `node --test scripts/source-baseline-contract.test.mjs` | exit 0，3/3 |
| `npm --prefix frontend run verify:source` | exit 0，5 个批准来源项、0 个敏感模式命中 |
| `openspec validate migrate-readonly-asset-usage-frame-navigation --strict` | exit 0，valid；[final strict log](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-usage-frame-navigation-final-strict.log) |
| 文档文本扫描 | 164 个自维护非忽略文本文件，`issues[]`；排除环境、依赖、构建输出、`.agents`、`legacy-reference` 和旧 `.spec-superflow.yaml`；[text check](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-usage-frame-navigation-text-check.json) |
| 最终历史/保护/来源守恒 | exit 0；66 份历史、76 份保护、8 份冻结来源均 `changed[]` 为空，旧 HEAD/status 未变；[final conservation](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-usage-frame-navigation-final-conservation.json) |

最终 build 输出为 CSS 44.65 kB（gzip 9.46 kB）、JS 372.52 kB（gzip 108.47 kB）。早期 495/495 是 Sol 复审前的预审 pipeline，不替代上表 499/499 最终结果。

冻结基线为 `23403806898550a7668a6ee7c0c457315655c39b`。root 在文档落盘前核验了 66 份历史文件、76 份保护文件及 8 份冻结来源文件，`changed[]` 均为空；旧 checkout HEAD 和既有状态不变。来源复制白名单仍为 exact-five 五项。文档落盘后的 strict、文本扫描和最终守恒复核均已完成且通过；最终证据分别见上表链接与 root checks。旧 change 与保护文件的前序事实不在本记录中改写。

## root 浏览器与 API 验收

root 保存 66 条浏览器观察和 22 项 metrics。最终桌面视口为 1280×720，移动视口为 390×844；两者均无横向溢出，browser metrics 中的 audio/video/iframe 节点数为 0。demo 与隔离 API 页面实际覆盖角色、场景、道具、非首章/非首镜头定位、键盘 Enter、返回筛选/展示数量（含我创建的 12 部），以及 root 最后复核的失焦清除反馈和道具 frame 入口定位。截图：[桌面 frame](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-usage-frame-navigation-desktop-frame.png)、[移动 frame](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-usage-frame-navigation-mobile-frame.png)。这些观察不表示旧章节原图请求为零；页面仍沿用原有安全 loopback 图片行为。

API audit 共 76 条本地记录：30 个业务请求、35 个 OPTIONS、11 个既有 loopback PNG 请求。12 个允许业务模式中实际观察 8 个；个人制作记录、粗剪、任务、团队四种模式本轮未访问。章节接口记录 11 次 200；storyboard-assets 记录 5 次 200 和 1 次 500，`unexpected[]`。这不是全局外网流量审计。正常目标路径按 source chapter、fresh target chapters、target storyboard-assets 各 GET 一次；还实际演练两账号、空素材、500 后从完整章节→素材链路显式重核恢复、logout/new login 的延迟取消。浏览器取消只证明 UI 不被旧响应覆盖；忽略 abort 的迟到 success/401 实际 settle 由上述自动化 mock 验证。

临时 API 页面和 fixture 已停止，仅保留 demo 5173。更详细统计见 [API audit JSON](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-usage-frame-navigation-api-audit.json>) 与 [browser JSON](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-usage-frame-navigation-browser.json>)。

## 独立源码复核与剩余状态

GPT-6.1 Sol/xhigh 只读复核最终源码和测试，确认同 Dashboard 内 view A→B→A 的旧父级 callback 守卫、blur 反馈清理及真实 DOM pending Promise settle 测试已闭环，无未解决的实证 P1/P2。复核没有运行测试或浏览器，不替代 Grillme。固定 GPT-5.6 Sol/xhigh 的 Grillme probe 退出码为 7、HTTP 000，审查未执行；任务 6.1 保持未勾。tasks 1.1–5.1 已据实现、自动化、root pipeline、浏览器、API、Sol、strict、文本扫描和守恒证据勾选（11/12）；任务 6.1 因固定 GPT-5.6 Sol/xhigh 的 Grillme 离线保持未勾。真实后端、生产链路和发布仍未验收。
