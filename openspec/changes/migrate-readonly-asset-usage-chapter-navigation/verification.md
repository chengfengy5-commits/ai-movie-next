# Verification：素材关联章节导航

## 范围与分层

本 change `migrate-readonly-asset-usage-chapter-navigation` 为素材关联结果增加按章节原始身份导航。实现复用现有章节列表和分镜素材读取；未新增业务 API、DTO、服务方法、fixture 路由、章节/素材样本、帧级定位或写入；不新增媒体接口或播放链路，沿用既有章节安全原图展示。自动化、demo、loopback fixture 与 GPT-6.1 Sol/xhigh 源码复审是不同证据层次，不代表真实后端或生产环境验收。

root 证据目录：[打开 evidence 目录](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/)。

Grillme 固定 GPT-5.6 Sol/xhigh 的最终探测退出码为 7、HTTP 000，审查未执行；任务 6.1 保持未勾。没有运行真实 FastAPI、PostgreSQL、Worker、R2、外部媒体或生产环境；没有提交、推送、部署或归档。

## 来源与隔离

冻结来源基线为 `23403806898550a7668a6ee7c0c457315655c39b`。root 复核 6 份冻结来源文件 hash、60 份历史产物和 25 份保护文件均无差异；来源复制白名单仍为 exact-five，`verify:source` 核验 5 个批准项、0 个敏感模式命中。来源合同测试 3/3 通过。旧 checkout HEAD 与原有状态保持不变；唯一原有未跟踪 `.spec-superflow.yaml` 保留，未读取其内容。证据见 [root checks](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-usage-chapter-navigation-root-checks.json) 与 [conservation](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-usage-chapter-navigation-conservation.json)。

## 自动化与本地 pipeline

Workspace 定向测试 `npm --prefix frontend test -- --reporter=dot src/app/Workspace.test.tsx` 通过 43/43；Luna 最终 Workspace 与 ChapterBrowser 定向结果合计 99/99，typecheck 通过。Workspace 回归覆盖角色、场景、道具入口不预读章节、有效来源读取、同名非首项目标经新目录按 raw ID 定位、目标分镜素材只读一次、返回后保留列表数量与筛选；退出后重新登录时，旧 Promise 的 success 与 401 均实际 settle 并 `await act`，不覆盖新状态或注销新用户。另捕获真实 AssetLibrary→Workspace 回调，在换剧集后重放旧 target；包含旧 target 继续报告 `isCurrent() === true` 的反例，确认 Workspace 当前上下文守卫不接受它。

Workspace 集成回归还检查 `navigationIntent=null → 手动选首章 → 重读`：章节 GET 共三次、分镜素材依次读取目标章、首章、首章，且目标意图只消费一次。

ChapterBrowser 独立回归覆盖目标目录缺失、重复和跨剧集、旧 handler 重放，以及同章新请求仍 pending 时旧 Promise 真实 settle 后不覆盖新世代状态。无效手动选择和忽略 abort 的迟到响应也由自动化用例验证；这些 Promise settle 用例不以浏览器取消证据替代。

root 对最终冻结产品代码执行的检查均 exit 0：

| 命令 | 实际结果 |
| --- | --- |
| `npm --prefix frontend run typecheck` | exit 0 |
| `npm --prefix frontend test -- --reporter=dot` | 30 个测试文件，472/472 通过，0 skipped；exit 0，45.29 秒 |
| `npm --prefix frontend run build` | exit 0，Vite 63 modules |
| `node --check tools/api-fixture/server.mjs` | exit 0 |
| `node --test scripts/source-baseline-contract.test.mjs` | exit 0，3/3 |
| `npm --prefix frontend run verify:source` | exit 0，5 个批准来源、0 个敏感模式命中 |
| `openspec validate migrate-readonly-asset-usage-chapter-navigation --strict` | 文档落盘后 root 实跑 exit 0 |

root pipeline、fixture、来源合同及复核摘要见 [root checks](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-usage-chapter-navigation-root-checks.json)。文档落盘后的 OpenSpec strict 已由 root 实跑 exit 0；文本扫描计数以 root-checks 最终证据为准，此处不预填数字。

GPT-6.1 Sol/xhigh 最终只读复核修复后的源码与测试，确认两个 P2 和 3.4 用例缺口均已关闭，无未解决实证 P1/P2；该复审未运行测试或浏览器。

## root 浏览器与 API 审计

本轮共保存 76 条浏览器观察和 15 项 metrics，早期尝试包含错误标为 mobile 的 1280px 视口及修复前结果；最终验收只采用修复后的稳定标签。最终桌面视口为 1280×720，手机视口为 390×844，两者均无横向溢出且关联面板媒体节点为 0。实际覆盖角色、场景、道具入口，非首章精确导航、键盘 Enter、目标章节打开与重复选择不重复请求，以及返回后保留列表筛选和展示数量（包括 24 部全部列表与 12 部创建列表）。最终截图：[桌面面板](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-usage-chapter-navigation-desktop-panel.png)、[手机面板](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-usage-chapter-navigation-mobile-panel.png)、[手机目标章节](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-usage-chapter-navigation-mobile-target.png)。

隔离 API 使用两个合成账号。最终正常路径重新验证 source chapters GET 1 次、目标目录 fresh GET 1 次、目标 storyboard-assets GET 1 次；同章重复没有新增请求。还覆盖 source ready 后切空目录、500 后显式重试恢复及 logout/new login 后的客户端取消。API audit 共 102 条本地记录：40 个业务请求、48 个 OPTIONS、14 个既有 loopback PNG 请求；12 个允许业务模式中实访 8 个，另 4 个本轮未观察。章节响应有 13 个 200、1 个 500，storyboard-assets 有 5 个 200，`unexpected[]`。记录含准备和 HMR 阶段；它是本地 fixture 请求审计，不是外部网络完整审计。

旧目标回调、重复/跨剧集目标和忽略 abort 后真实 settle 的迟到 success/401 由自动化回归验证。浏览器延迟场景只证明客户端取消，不能替代这些真实 Promise settle 用例。临时 4175 fixture 与 5174 API 前端已停止，临时 tabs 8/9 已关闭，保留 demo 5173。API tab 在请求 viewport reset 后仍报告 390px，因此通过关闭临时 tab 清除覆盖，不把 reset 请求记为成功。详细记录见 [observations](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-usage-chapter-navigation-browser-observations.json)、[metrics](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-usage-chapter-navigation-browser-metrics.json)、[API summary](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-usage-chapter-navigation-api-summary.json) 与 [API audit](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-usage-chapter-navigation-api-audit.jsonl)。

## 当前任务状态

任务 1.1–5.1 根据实现、自动化、root pipeline、浏览器、API 审计、Sol 复审、来源与隔离证据完成；任务 6.1 因 Grillme 离线保持未勾，当前为 11/12。真实后端和生产未验收，也没有提交、推送、部署或归档。
