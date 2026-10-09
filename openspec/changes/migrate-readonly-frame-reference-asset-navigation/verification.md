# Verification：分镜引用导航到素材卡片

## 范围与证据分层

本切片从分镜关联素材面板增加显式入口，按类别与原始素材 ID 导航至素材库的匹配卡片。目标端重新读取完整类别目录，先统计原始 ID，再核验剧集归属；只有当前唯一目标卡片实际获得焦点并通过作用域复核后才消费意图。该流程复用现有角色、场景、道具读取，不新增 API、DTO、service 方法、fixture 路由、样本或媒体接口。

自动化、demo/API 浏览器、GPT-6.1 Sol/xhigh 源码复核、legacy source 静态核验分别记录。浏览器中的取消演练证明会话切换后界面不被旧结果污染；忽略 abort 后 Promise 的迟到 success/401 实际 settle 由自动化测试验证。真实 FastAPI、PostgreSQL、Worker、R2、外部媒体和生产环境均未运行。没有提交、推送、部署或归档。

证据目录：[打开本批 evidence](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/)。主要汇总见 [root checks](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-frame-reference-asset-navigation-root-checks.json>)、[API audit](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-frame-reference-asset-navigation-api-audit.json>)、[API action deltas](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-frame-reference-asset-navigation-api-action-deltas.json>)、[browser evidence](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-frame-reference-asset-navigation-browser.json>) 与 [conservation evidence](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-frame-reference-asset-navigation-conservation.json>)。

## 自动化与本地检查

本批 Workspace 集成定向实际运行 64/64 通过，命令为 npm --prefix frontend test -- src/app/Workspace.test.tsx；[定向日志](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-frame-reference-asset-navigation-workspace-targeted.log>)。它覆盖角色、场景、道具的来源面板到目标类别读取与卡片聚焦、同类别 fresh 重读、同 Dashboard 视图 A→B→A 旧父回调拒绝、已接受意图在 source 卸载后继续，以及离开、返回和筛选恢复。退出后重新登录时，旧 source/target success 与 401 deferred Promise 均真实 settle 并经 act flush，未覆盖或注销新会话。

根侧最终独立流水线全部 exit 0：

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| npm --prefix frontend run typecheck | exit 0 | [typecheck log](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-frame-reference-asset-navigation-typecheck.log>) |
| npm --prefix frontend test | 33 files，530/530 通过，0 skipped，65.16 秒 | [full test log](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-frame-reference-asset-navigation-test.log>) |
| npm --prefix frontend run build | exit 0，Vite 66 modules；CSS 45.30 kB / gzip 9.57 kB，JS 380.06 kB / gzip 110.38 kB | [build log](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-frame-reference-asset-navigation-build.log>) |
| node --check tools/api-fixture/server.mjs | exit 0 | [fixture syntax log](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-frame-reference-asset-navigation-fixture-syntax-independent.log>) |
| node --test scripts/source-baseline-contract.test.mjs | 3/3 通过，exit 0 | [source contract log](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-frame-reference-asset-navigation-source-contract-independent.log>) |
| npm --prefix frontend run verify:source | 5 个批准来源项，敏感模式 0，exit 0 | [source copy log](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-frame-reference-asset-navigation-source-copy-independent.log>) |
| openspec validate migrate-readonly-frame-reference-asset-navigation --strict | 文档落盘后 exit 0，valid | [final strict log](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-frame-reference-asset-navigation-final-strict.log>) |

全量测试过程另有一次并行运行失败：32 个文件通过，已有 Workspace 测试 1 项超过默认 5 秒，结果为 529 passed / 1 failed。随后只运行该用例时，1 项通过、63 项 skipped，用例耗时 2.02 秒、套件耗时 3.17 秒；未提高 timeout，也未修改源码。该次超时原因未能确认。停止临时服务后，root 重新运行未改阈值的完整套件，最终 33 files / 530 passed / 0 skipped。详见 root checks 与 [timeout diagnostic](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-frame-reference-asset-navigation-timeout-diagnostic.log>)。

GPT-6.1 Sol/xhigh 只读复核无未解决实证 P1/P2。复核确认同实例新意图切换重置、view A→B→A 的旧 epoch 守卫，以及 consumed/abandoned 后继续浏览的测试闭环。hidden/detached 分支有真实 DOM 修改，但主要验证后置 focus 拒绝；map 缺项、inert 和各个前置守卫是静态核对，不能概括成所有分支均有自动化反例。

## 浏览器与 API 证据

root 保存 60 条浏览器观察和 28 项 metrics。demo 桌面 1280×720、移动 390×844 均无横向溢出，audio/video/iframe 节点数为 0；实际浏览角色、场景、道具入口、非首卡聚焦、键盘操作、过滤返回及刷新后不重放。API 浏览器也实测两个合成账号、空目录、500 后显式恢复，以及 logout/new login 后延迟取消。截图：[桌面验收](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-frame-reference-asset-navigation-desktop.png)、[移动验收](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-frame-reference-asset-navigation-mobile.png)。这些截图与 metrics 不表示 PNG 或其他媒体请求为零。

API audit 共 124 条本地记录：53 business、57 OPTIONS、14 个既有 loopback PNG 请求。12 个允许业务模式中实际观察 7 个，未观察 teams、tasks、personal-notes、rough-cut、props；记录包含页面准备、HMR 和既有 series 复核，不是全局外网流量审计，unexpected[]。稳定动作记录中，角色和场景的 source 与 target 类别读取各为一次；导航没有读取章节、storyboard 或其他目标类别，但同时观察到既有 series 复核和 PNG 请求。fixture 默认分镜 prop 引用为空，因此本次 API 道具来源不发 GET、也无 API 入口；道具正向路径由 demo 和 Workspace 自动化覆盖。最初阶段有两次角色 500 GET，原因未确认；冻结状态下重复演练记录为一次目标类别 GET。详细次数见 API audit 与 action deltas。

## 静态基线与未覆盖范围

冻结来源 SHA 为 23403806898550a7668a6ee7c0c457315655c39b。文档后最终守恒核验中，72 项历史文件、80 项保护文件、8 项来源文件和本批 11 项冻结产品/测试文件均为 changed[]；旧 checkout HEAD/status 未变，来源复制白名单保持 exact-five 五项。最终 OpenSpec apply 状态为 ready，4 个 context 文件、11 项任务中完成 10 项，唯一未完成项为 Grillme 6.1；见 [final apply](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-frame-reference-asset-navigation-final-apply.json>)。文档后文本扫描检查 172 个自维护文本文件、排除 13 个文件，UTF-8、BOM、NUL、CR、冲突标记及尾部空格/制表符检查均为 issues[]；见 [text check](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-frame-reference-asset-navigation-text-check.json>) 与 [final conservation](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-frame-reference-asset-navigation-conservation.json>)。

本批没有进行 Grillme probe 或配对审查。GPT-6.1 Sol/xhigh 的只读源码复核不替代固定 GPT-5.6 Sol/xhigh Grillme；tasks 6.1 保持未完成。真实后端、数据库权限/锁并发、生产媒体链路和生产发布未验收。
