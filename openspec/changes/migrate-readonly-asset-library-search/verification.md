# 第十八切片验收记录：素材库本地搜索

日期：2026-10-07

项目：/Users/yanghaibo/data/projects/ai/haoai-next
变更：migrate-readonly-asset-library-search

## 范围与结果

本切片只在当前已读取的角色、场景或道具目录上增加本地名称/标题与可见别名搜索。过滤保留原顺序、索引、对象和卡片节点；未命中的卡片以 hidden/inert 隐藏，完整目录仍用于身份核验。查询不新增目录/章节业务请求；图片仍按既有安全显示链路按需加载，PNG 实际增量单列记录。搜索不放宽重复 ID 的导航资格。

自动化证明覆盖三类字段匹配、trim/大小写/字面子串、别名、占位与隐藏字段排除、空查询、顺序/对象身份、零命中与清空；也覆盖请求世代、user/services/series 首帧作用域、ABA、navigation epoch、重复身份和公开 JSX runtime 下真实旧 input/clear handler 重放。输入变化会关闭旧 usage panel、清本地导航反馈而不重读目录或章节。

本地搜索没有服务端搜索语义，也不是访问授权。当前分类原有 GET 与页面既有安全 loopback 图片显示继续沿用。

## 自动化与构建

以下执行者按行注明；未注明执行者的记录由 root 实际运行。

| 验证 | 执行者 | 实际结果 | 执行证据 |
| --- | --- | --- | --- |
| 最终 typecheck | root | exit 0，3.767 秒 | [root typecheck log](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-library-search-root-typecheck-final.log) |
| 全量 Vitest | root | 38 个测试文件、611/611，通过，exit 0；Vitest 115.39 秒，wall 115.852 秒；无代码 skip、未改 timeout | [root full-test log](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-library-search-root-full-test.log) |
| Vite build | root | exit 0，71 modules，Vite 1.16 秒 | [root build log](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-library-search-root-build.log) |
| 本切片组件与 helper 定向 | 实现代理（Luna 6/xhigh 指定路线；runtime 未独立核验） | 2 files、48/48，通过，exit 0，5.12 秒 | [post-review 定向日志](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-library-search-post-review.log) |
| Workspace 集成定向 | Workspace 集成代理 | 1 file、71/71，通过，exit 0，73.07 秒；这是九个测试选项修复前的独立快照，不能替代最终全量 | [Workspace 定向日志](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-library-search-luna-workspace-final.log) |
| Workspace 类型选项窄修 | Workspace 测试代理 | 6 个相关测试通过，65 个按名称过滤；不是完整 Workspace 重跑，最终 root 全量覆盖修复后的文件 | [窄修定向日志](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-library-search-typecheck-options-fix-workspace-targeted.log) |
| fixture JavaScript 语法 | root | node --check exit 0 | [fixture syntax log](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-library-search-root-fixture-syntax.log) |
| frozen source contract | root | node --test scripts/source-baseline-contract.test.mjs，3/3，通过 | [source contract log](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-library-search-root-source-contract.log) |
| exact-five 来源校验 | root | 5 个批准来源条目，敏感模式命中 0，exit 0 | [source exact-five log](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-library-search-root-source-exact-five.log) |

### 失败历史

初始 helper 定向 1 项失败、5 项通过：查询样例含内部空格，与既有字面子串规则不符；随后去掉样例内部空格，规则和断言逻辑未变。初始 Workspace 定向 5 项失败、66 项通过：部分断言在剧集标题“雾港来信”进入 ready 前就查询标题，随后调整测试等待真实 heading。二者不是产品缺陷，历史日志保留：[helper 初始日志](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-library-search-helper-initial.log)、[Workspace 初始日志](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-library-search-luna-workspace-initial.log)。

root 初始 typecheck exit 2，原因为九个新 Workspace role 查询传入 Testing Library 类型不支持的 exact 选项。窄修只删除这些查询选项，测试行为保持；Sol 只读代理通过字节级重建证明其余文件内容未变，随后 root 最终 typecheck exit 0。日志与修复收据：[初始 typecheck 失败](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-library-search-root-typecheck.log)、[窄修收据](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-library-search-typecheck-options-fix-receipt.json)。

### 只读复审

指定 GPT-6.1 Sol / xhigh 路径的源码与测试收据：[初始只读审查](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-library-search-sol-review.json)、[证据缺口关闭复核](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-library-search-sol-review-closure.json)、[typecheck 选项窄核](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-library-search-sol-typecheck-options-review.json)。[文档首稿只读审查](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-library-search-sol-documentation-review.json)记录初稿 D1/D2 文档 P2 和 D3-D5 证据补正；独立的[文档收口复核](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-library-search-sol-documentation-closure.json)确认 D1-D5 均已关闭，无未解决的实证 P1/P2 或内容/链接缺口，并核对四份文档、六份源码和三份浏览器产品哈希。源码审查中的 V1/V2 由后续源码 closure 收据关闭；这些结论与文档收口分别记录。收据说明 runtime 未独立核验，不能把指定路线写成实际运行时模型证明。Grillme 固定 GPT-5.6 Sol / xhigh 仍为独立离线待办。

## 浏览器与 API 证据

root 使用现有 demo 页面与原样 loopback fixture 验收，没有新增 fixture 模式或端点。

demo 在 1280×720 桌面及 390×844 手机视口验证角色/场景/道具名称和别名、零命中与显式清空、hidden/inert 下 Tab 焦点、关联镜头、素材与分镜导航、返回剧集列表及 24 条展示。实际滚动与焦点观察没有横向溢出。截图：[桌面搜索](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-library-search-desktop-demo-search.jpg)、[手机搜索](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-library-search-mobile-demo-search.jpg)、[手机零命中](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-library-search-mobile-demo-zero.jpg)。

API 页面在原 fixture 下验证空目录、403、显式恢复、延迟与第二合成账号。审计共 85 条记录：35 条业务请求、41 条 OPTIONS、9 条既有 loopback PNG；12 种允许业务模式中实际观察到 8 种，计数含准备阶段请求及一次登录 401，审计 unexpected 为 0。四个本地搜索/清空阶段（角色、场景、道具及 usage query）各自业务、OPTIONS、PNG 增量均为 0。素材目录打开的实际增量为 2 条业务请求、2 条 OPTIONS、1 张既有 PNG；目标素材新导航为 3 条业务请求、3 条 OPTIONS、1 张既有 PNG。图片仍按既有安全显示链路按需加载，这些计数不代表全局无媒体请求。延迟场景中浏览器客户端取消只证明取消交互；服务器之后返回 200 不证明客户端忽略 abort 后的原 Promise 被真实处理。旧 Promise 成功/401 的隔离由自动化 deferred 用例真实 settle 并 await act 覆盖，最终 611 项全量也包含这些用例。该审计不是全局外网抓包，也不证明真实后端行为。第二合成账号截图：[API 第二账号搜索](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-library-search-api-second-user-search.jpg)。

日志与浏览器观察文件：[API audit](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-library-search-api-audit.json)、[browser observations](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-library-search-browser-observations.json)、[browser acceptance](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-library-search-browser-acceptance.json)。

## 保全、清理与边界

root 文档冻结复核已实跑：[strict](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-library-search-root-strict-review-freeze.json) 与 [status](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-library-search-root-status-review-freeze.json) 均 exit 0；当时的 [apply CLI 快照](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-library-search-root-apply-review-freeze.json) 为 ready 9/12，是勾选 5.2/5.3 前的状态。文档冻结后的文本检查为 212 个文件、13 个排除项、0 问题，见[收据](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-library-search-text-review-freeze.json)。同一轮保全检查见[收据](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-asset-library-search-conservation-review-freeze.json)：225 个 inventory 文件、101 个代码文件，211 个 immutable、102 个历史文件、95 个保护文件和 13 个 frozen source 均无漂移；六份源码、三份浏览器产品及四份文档哈希匹配，旧仓库 HEAD/status 未变。依据这些实际收据，tasks 5.2/5.3 已勾选，任务清单为 11/12，唯一未完成项是 Grillme 6.1。apply 9/12 是复选框修改前的 CLI 快照；未声称修改后已重跑 CLI。

root 记录的 owned 本地服务会话均以 exit 130 停止，5173/5174/4175 无监听；已知 IAB 标签页 1/2/3 已关闭且视口复位。Chrome 新标签创建与后续清单调用超时，创建状态未知，因此不声称所有浏览器标签均已关闭。

没有运行真实 FastAPI、PostgreSQL、Worker、R2、外部媒体或付费服务；没有 commit、push 或 deploy。指定外部 Grillme 未启动、未配对、未探测，仍待 GPT-5.6 Sol / xhigh 离线复核。
