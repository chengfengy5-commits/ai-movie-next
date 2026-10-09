# Verification

本记录区分自动化、浏览器、静态来源和未运行的后端/外部审查。证据目录：`/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/`；本 change 文件以 `haoai-next-rough-cut-status-filter-` 为前缀。最终完整测试、构建与来源校验汇总见 [pipeline.json](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-rough-cut-status-filter-pipeline.json>)，独立根核查见 [root-checks.json](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-rough-cut-status-filter-root-checks.json>)。

## 自动化与本地静态验证

| 验证 | 实际结果 | 收据 |
| --- | --- | --- |
| `npm --prefix frontend run typecheck` | exit 0 | [root-typecheck-final.log](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-rough-cut-status-filter-root-typecheck-final.log>) |
| `npm --prefix frontend test -- --no-file-parallelism` | 37 files，591/591 通过，102.81 秒；默认 timeout 未提高，没有新增 code skip | [root-full-test-final.log](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-rough-cut-status-filter-root-full-test-final.log>) |
| `npm --prefix frontend run build` | exit 0，70 modules，1.21 秒 | [root-build-final.log](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-rough-cut-status-filter-root-build-final.log>) |
| `npm --prefix frontend test -- src/app/Workspace.test.tsx` | 65/65 通过，62.85 秒 | [Workspace 全文件定向日志](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-rough-cut-status-filter-luna-workspace-final.log>) |
| `npm --prefix frontend test -- src/app/Workspace.test.tsx -t 'locates the first rough-cut row|does not let a late rough-cut'` | 3 个目标实例通过；本次 `-t` 之外的 62 项仅被 Vitest 过滤选择，不是 code skip | [Workspace 目标日志](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-rough-cut-status-filter-luna-workspace-targeted.log>) |
| 粗剪 Panel 与 helper 组件测试 | 25/25 通过，2.13 秒 | [components-after-r3-counter.log](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-rough-cut-status-filter-components-after-r3-counter.log>) |
| fixture 与基线合同 | `node --check tools/api-fixture/server.mjs` exit 0；`node --test scripts/source-baseline-contract.test.mjs` 3/3；`npm --prefix frontend run verify:source` 验证 5 个批准来源项、0 sensitive-pattern match | [foundation.json](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-rough-cut-status-filter-foundation.json>) |

Workspace 自动化使用当前 live DOM，覆盖两行草稿的反序稳定 ID、完整快照计数、待安排重叠、原草稿序号、筛选后原行定位和 focus/scroll、键盘 Enter 与鼠标过滤造成的自然失焦、零额外请求、24 条剧集列表及任务页往返。两个旧账号用例分别实际 resolve 与 reject 原 deferred Promise 并 `await act`，核对第二用户的 token、私有粗剪、筛选选择仍有效，之后从新账号草稿再次定位。

最初 Workspace 定向为 64/65：唯一失败是测试把 Shift+Tab 的目标假设为筛选按钮，实际焦点落在章节中的关联素材按钮。测试改用当前 live 筛选按钮的 DOM focus，再以真实 `user.keyboard('{Enter}')` 激活；最终全文件 65/65 通过。首轮日志仍保留：[luna-workspace-initial.log](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-rough-cut-status-filter-luna-workspace-initial.log>)。root 在补充 Panel 对原 snapshot 与 row 对象身份的断言前停止过一轮未完成的全量测试（exit 130），那次不是最终失败或通过记录；Sol 的 V1（原 snapshot 与原 row 身份断言）和 V2（R3 重放旧 handler 后 invalidated 计数不变）均已闭环；最终窄复核无未解决 P1/P2，六个文件哈希与三个浏览器产品哈希符合冻结值。该复核未运行测试或浏览器，详见 [Sol R3 counter review](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-rough-cut-status-filter-sol-review-r3-counter.json>)。更早的组件用例迭代记录见 [validation-history.json](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-rough-cut-status-filter-validation-history.json>)；不得把这些中间失败归因为产品或环境问题。

## 浏览器与 API fixture 验收

root 保存 71 条 DOM observations 和 26 条 metrics；最初 3 条 observation 与首条 metric 来自列表 accessible name 修正前，仅作历史保留。最终验收使用修正后的“当前粗剪草稿条目”名称。Demo 在 1280×900 和 390×844 验收；API 最初有一条 1280×720 的准备观测，随后设置为 1280×900 完成最终代表验收，未进行 API mobile 验收。最终 viewport、横向溢出与状态序列见 [browser observations](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-rough-cut-status-filter-browser-observations.json>)、[browser metrics](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-rough-cut-status-filter-browser-metrics.json>) 和 [final summary](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-rough-cut-status-filter-browser-final-summary.json>)。

Demo 覆盖完整快照计数、pending 与排除重叠、原序号和 raw-ID 定位、鼠标/Enter 切换及失焦清反馈、重读/重开默认全部、未保存非空投影的零命中恢复与无溢出。API fixture 按 normal、unsaved、legacy、pending、error、recovery、delay 环境分阶段运行两个合成账号，展示同章私有投影差异、显式 500 retry 和延迟退出后的新用户投影。API audit 共 52 records：22 business、26 OPTIONS、4 个既有 loopback PNG；在 12 个允许业务 patterns 中实际观测 6 个，`unexpected=[]`。五组筛选/恢复/定位行为前后 business、OPTIONS、PNG 与粗剪 GET delta 均为 0；显式打开与显式重读各产生一条既有粗剪 GET 及 OPTIONS。错误只在显式重试后恢复。完整计数和差量见 [api-audit.json](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-rough-cut-status-filter-api-audit.json>)。

![Demo desktop rough-cut filters](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-rough-cut-status-filter-demo-desktop.png)

![Demo mobile rough-cut filters](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-rough-cut-status-filter-demo-mobile.png)

![API second-user rough-cut view](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-rough-cut-status-filter-api-second-user-desktop.png)

Browser 中延迟请求取消后观察到服务端晚到 200 与第二账号视图保持正确；这不证明客户端忽略 AbortSignal 后仍安全。真实旧 Promise resolve/401 的 settle 与 `act` 证据来自上述自动化测试。API audit 仅覆盖本次隔离 fixture 的请求记录，不是全局外连审计；已有 PNG 读取不被表述为零。

## 来源、边界与未执行项

冻结来源 baseline 为 `23403806898550a7668a6ee7c0c457315655c39b`。`verify:source` 的复制白名单仍为 5 项，另有 13 份冻结来源记录供兼容审阅；这两个集合用途不同。文档前守恒记录中 203 个不可变既有项、96 个历史文件、93 个保护文件和 13 份冻结来源均无变化；旧 baseline HEAD 与状态也未变，见 [pre-doc conservation](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-rough-cut-status-filter-conservation-pre-doc.json>)。fixture syntax、来源合同、五项白名单/敏感模式检查见上表；不扩展十二条允许业务接口 patterns、fixture/sample、媒体路径或依赖。

此次只使用隔离本地 demo 与 loopback fixture。没有运行真实 FastAPI、PostgreSQL、Worker/共享队列、R2、付费任务或生产服务，没有 commit、push、deploy 或 archive。粗剪 GET 与通用认证仍可能维护会话状态；旧 GET 没有显式 commit 不等于整次请求对数据库完全无副作用。本地来源清单为静态兼容材料，不能替代真实后端验收。

OpenSpec planning strict 在实施前 exit 0，apply 当时为 ready、0/12；实施后由 tasks 勾选记录 11 项本地工作，`6.1` 指定 GPT-5.6 Sol/xhigh Grillme 仍离线未执行且保持未勾。实现/规划采用 requested/reused GPT-6 Luna/xhigh 与 GPT-6.1 Sol/xhigh 路由，runtime 未独立核验。Sol 的源码/测试只读复核无未解决 P1/P2，不代表其执行了测试或浏览器。四文档冻结后的 OpenSpec strict/status/apply、文本与保全结果由本批根收据 [openspec-final.json](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-rough-cut-status-filter-openspec-final.json>)、[text-check.json](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-rough-cut-status-filter-text-check.json>)、[conservation-final.json](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-rough-cut-status-filter-conservation-final.json>) 与 [root-checks.json](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-rough-cut-status-filter-root-checks.json>) 记录；本文不预写这些命令的通过结果。
