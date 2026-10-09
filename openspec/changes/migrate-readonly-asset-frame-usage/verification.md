# Verification：素材卡片关联镜头

## 范围与边界

本 change 为 migrate-readonly-asset-frame-usage。只读反查复用现有章节服务与快照，不新增业务接口、DTO 或依赖。验收使用自动化、demo 和 loopback fixture；没有运行真实 FastAPI、PostgreSQL、Worker、R2、外部媒体或生产环境，也没有提交、推送或部署。

root 证据目录：[打开证据目录](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/)；下文列出的 JSON 文件均位于此目录。

Grillme 固定 GPT-5.6 Sol/xhigh 的探测退出码为 7、HTTP 000，独立审查未执行；这不由本地检查、浏览器验收或 GPT-6.1 Sol/xhigh 复审替代。任务 5.1 保持未勾。

## 来源与隔离

冻结旧源码基线为 23403806898550a7668a6ee7c0c457315655c39b。六份冻结审阅来源文件的全文 hash 与字节数均匹配；这是静态审阅证据，来源复制白名单仍为 exact-five。root 实际运行来源合同 3/3 和 npm --prefix frontend run verify:source：exit 0，确认 5 个批准条目、0 个敏感模式命中。

前九批 54 份历史产物 hash 无差异；本 change 的 24 份保护文件也无差异。旧 checkout HEAD 仍为冻结 SHA，原有唯一未跟踪项 openspec/changes/harden-task-billing-and-redeployment/.spec-superflow.yaml 保留且未读取其内容。演示素材引用守恒、API 白名单和文件 hash 记录在 root evidence directory 的 haoai-next-asset-frame-usages-conservation.json 与 haoai-next-asset-frame-usages-isolation.json。

## 自动化与 pipeline

Workspace 性能回归没有增加 Vitest timeout。首轮 root 全量为 30 个文件、449 项中 448 通过，唯一失败是“不预读章节并返回后保留列表状态”用例超过默认 5000 ms。该用例保留原业务断言，将昂贵的真实 demo 关联镜头读取拆到独立 Workspace 用例；原用例在 1828 ms 通过，timeout 未改变。Workspace 定向结果为 1 个文件、37/37 通过；退出后登录新账号时，旧 chapters Promise 的 success 与 401 都实际 settle 并通过 await act，确认旧响应不能呈现旧内容、注销新账号或清除新 token。进入素材库及展示卡片不预取 chapters；关联镜头成功路径由实际 AssetLibrary/面板集成验证。

root 对最终冻结代码的六项检查均 exit 0：

| 命令 | 结果 |
| --- | --- |
| npm --prefix frontend run typecheck | exit 0 |
| npm --prefix frontend test -- --reporter=dot | 30 个测试文件，452/452 通过，0 skipped |
| npm --prefix frontend run build | exit 0，Vite 63 modules |
| node --check tools/api-fixture/server.mjs | exit 0 |
| node --test scripts/source-baseline-contract.test.mjs | exit 0，3/3 |
| npm --prefix frontend run verify:source | exit 0，5 个批准来源，0 个敏感模式命中 |

根检查证据为 root evidence directory 下的 haoai-next-asset-frame-usages-final-pipeline.json、haoai-next-asset-frame-usages-conservation.json 和 haoai-next-asset-frame-usages-sol-review.json。GPT-6.1 Sol/xhigh 最终只读审阅实现与测试，无未解决的实证 P1/P2；复审者没有运行测试或浏览器。文档初稿落盘后，root 实跑 openspec validate migrate-readonly-asset-frame-usage --strict，exit 0，valid。文本扫描检查 145 个文本文件，UTF-8、EOF newline、尾空格、冲突标记和控制字符均为 textIssues[]；54 份历史文件及最终 8 个代码/测试文件 hash 均无差异。扫描排除 .agents、.git、dist、legacy-reference、node_modules、package-lock.json 和符号链接。扫描证据见 haoai-next-asset-frame-usages-text-check.json；最终文档冻结后 root 会再复跑 strict 与文本扫描。

## root 浏览器与 API 审计

浏览器共保存 65 条观察，包含早期失败尝试；最终验收只采用修复后标签。早期 demo-other-character-replaces-panel 与 api-repeat-current-asset 观察失败，随后由 demo-final-different-character-replaces-panel、api-fixed-reopen-and-repeat 和 api-final-repeat-and-explicit-reread 标签完成最终复测。保留这段过程，不能将全部观察描述为一次通过。

Demo 浏览器覆盖角色、场景、道具三类、章节响应顺序、跨章节命中、切换素材、重复打开不重发 GET、显式重读、目录重读关闭面板、关闭重开、键盘 Enter、返回后保留 24 部剧集及 12 部创建剧集筛选、任务页清理面板。最终桌面视口 1280×720，移动视口 390×844；两者 scrollWidth == clientWidth，关联面板媒体节点数为 0。

API 使用两个合成账号。第二账号进入目录时没有章节预读；显式反查验证角色和场景成功、默认道具零匹配、空章节、500 后保留会话并经显式重试恢复。API audit 共 73 条记录，其中 31 个业务请求、37 个 OPTIONS 和 5 个既有 loopback PNG 请求；实访 7 种业务路由模式，业务响应 30 个 200、1 个 500。章节请求 11 次（10 个 200、1 个 500），storyboard-assets 请求 0 次，unexpected[]。白名单仍支持 12 种模式，实访 7 种不等于访问全部 12 种。

浏览器的 5000ms 延迟观察证明关闭后取消且没有旧面板复现；它本身不证明自定义 Promise 迟到结算。旧 Promise success/401 实际 settle 由自动化用例覆盖。关联面板没有新增媒体元素或主动媒体 fetch；滚动可能使页面既有 lazy 图片自然加载。

root 已关闭临时 fixture 4175、API 页面 5174 和临时浏览器标签；保留 demo 页面 5173。浏览器、请求审计、隔离与 pipeline 证据文件位于 root evidence directory：haoai-next-asset-frame-usages-browser-acceptance.json、haoai-next-asset-frame-usages-api-audit.json、haoai-next-asset-frame-usages-api-stages.json、haoai-next-asset-frame-usages-root-checks.json、haoai-next-asset-frame-usages-text-check.json。

## 当前任务状态

任务 1.1–4.1 已按实现、自动化、root pipeline、浏览器、API 审计、Sol 复审、strict、文本扫描与隔离证据完成；任务 5.1 因 Grillme 离线保持未勾，当前为 11/12。真实后端、生产发布、提交、推送和部署均未执行。
