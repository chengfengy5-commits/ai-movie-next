# Verification: 关联素材只读面板

## 验收范围与边界

本 change 为 `migrate-readonly-frame-asset-references`。所有证据都针对本地 demo、自动测试或 loopback fixture；没有运行真实 FastAPI、PostgreSQL、Worker、R2、外部媒体或生产环境，也没有 commit、push 或 deploy。固定旧源码基线为 `23403806898550a7668a6ee7c0c457315655c39b`。

OpenSpec strict 已实跑通过；135 个文本文件的 UTF-8、EOF newline、尾空格、冲突标记及控制字符检查均为 issues[]，前八批 48 个历史文件 hash 为 mismatch[]。4.1 已完成。Grillme 探测 exit 7 / HTTP 000，固定 GPT-5.6 Sol/xhigh 审查未执行，5.1 保持未勾。

## 来源与隔离核对

root 的来源证据记录六份冻结审阅来源文件的 SHA-256 与字节数，六份全文均匹配冻结清单。它们是静态审阅证据；可复制来源白名单仍为 exact-five，source verifier 实际确认 5 个批准条目且 0 个敏感模式命中：

| 来源 | SHA-256 | 字节数 |
| --- | --- | ---: |
| `js/episode.js` | `79b117ce13e633b6b641faca7232328358e8e6f3be40825dcf097f87994d0e9f` | 1,162,775 |
| `backend/app/schemas/series.py` | `78b1aee6e31cc195fb9e9af7401738ed1c2b3a73a9563f196855dd9912e3bfb9` | 13,748 |
| `backend/app/routes/series.py` | `db9721e34a3c5f22f81ce7cf4bc5434d9f3661d732ab1e8a79ba2e74852243cf` | 115,619 |
| `backend/app/models/series.py` | `89bcbff8c0e3a075f62af5ab1f8ab8c961a6a2d275dc99c43c1aad7ae126a7ff` | 17,463 |
| `backend/app/services/team_service.py` | `392f0792be876adda6b4b7629af0624a1dfbaef6fbf8555d7cacfd96cc81326f` | 7,792 |
| `backend/app/routes/auth.py` | `6cae3297fb5806c8668cbd245be974ff42ccf42999265440a6d6e27a7e6d1a78` | 18,221 |

前八批 48 个历史文件的 hash 比对为 `mismatch[]`。旧 checkout HEAD 仍匹配固定 SHA；原有唯一未跟踪项 `openspec/changes/harden-task-billing-and-redeployment/.spec-superflow.yaml` 保留，内容未读。隔离结果见 `haoai-next-frame-asset-references-isolation.json`；来源和历史清单见同证据目录中的 `haoai-next-frame-asset-references-sources.json` 与 `haoai-next-frame-asset-references-history.json`。

## 代理定向测试与 fixture 守恒

实际运行：

- `npm --prefix frontend test -- --reporter=dot src/features/chapters/demoChapters.test.ts src/features/chapters/demoPersonalRoughCut.test.ts src/shared/api/chapters-http.test.ts src/shared/api/assets-http.test.ts`：exit 0，4 个测试文件、37 tests passed。
- `node --check tools/api-fixture/server.mjs`：exit 0。

守恒证据保存在 `haoai-next-frame-asset-references-fixture-conservation.json`。精确替换回放只匹配预期的引用 ID 变化；demo 每帧 character/scene/prop 数量依次保持为 `[1,0,2]`、`[1,0,1]`、`[0,1,2]`，顺序及重复数未变；fixture `createFrame` 对应数量为 `[1]`、`[1]`、`[0]`。10 个素材目录、个人制作记录、粗剪和投影保护文件 hash 不变。测试遍历全部 24 个 demo series 核对同 series、同类型引用；fixture 的共享 ID 在三个素材目录均存在，默认 prop 引用保持空数组。

## root 最终自动验证

root 在最终冻结代码上实际运行：

- `npm --prefix frontend run typecheck`：exit 0。
- `npm --prefix frontend test -- --reporter=dot`：exit 0，28 个测试文件、424/424 tests passed、0 skipped。
- `npm --prefix frontend run build`：exit 0，61 modules；CSS 40.86 kB（gzip 8.76 kB），JS 347.78 kB（gzip 102.10 kB），index 0.46 kB（gzip 0.32 kB）。
- `node --check tools/api-fixture/server.mjs`：exit 0。
- `node --test scripts/source-baseline-contract.test.mjs`：exit 0，3/3 passed。
- `npm --prefix frontend run verify:source`：exit 0，确认 5 个批准来源条目，0 个敏感模式命中。

Root 自动验证详见 `haoai-next-frame-asset-references-root-checks.json`。文档落盘后实际运行 `openspec validate migrate-readonly-frame-asset-references --strict`，exit 0，valid。文本扫描详见 `haoai-next-frame-asset-references-text-check.json`：135 个文件，检查项 issues[]，前八批 48 个历史文件 `mismatch[]`。扫描排除 .git、.agents、node_modules、dist、legacy-reference、package-lock.json 和符号链接。

## root 真实浏览器验收

本轮共保存 82 条观察（含初稿阶段）；最终验收以修复后稳定冻结版标签和最终 metrics 为准。Demo 桌面视口为 1280×720，移动端为 390×844；最终两个标签均满足 `scrollWidth == clientWidth`，面板媒体节点数为 0。移动端证据只引用最终 390px 标签；早期误标为 mobile 的 1280px 标签不计入移动端验收。

Demo 浏览器实测覆盖：初始无分类、三类文字详情及第二章节道具顺序、切换镜头/章节、与个人记录和粗剪互斥、关闭重开与章节显式重读、离开任务页、返回后 24/24 剧集及创建筛选 12/12 保留、退出后新登录重置、键盘 Enter 操作，以及桌面/移动端无横向溢出。

API audit 共 50 条记录：21 个业务请求、25 个 OPTIONS、4 个既有 loopback PNG；业务响应 20 个 200、1 个 500，`unexpected[]`。允许业务白名单为 12 种，浏览器实际访问 7 种：login、me、series、chapters、storyboard-assets、characters、scenes。请求计数证明打开时不读取目录；显式分类后 character/scene 各读取 1 次，default prop 为 0 次；返回时复用缓存，显式角色重读使 character 计数增至 2，scene 仍为 1、prop 仍为 0。还实测 500 后会话保留并正常恢复、空结果及第二合成账号独立登录/读取；关闭后超过 5 秒的延迟窗口再次观察仍无旧面板。API 窄视口为 390/390，原始 ID 不可见，面板媒体节点为 0。记录仅能证明实访的 7 种路由，不能表述为访问了完整 12 种。

默认 fixture 的道具引用为空，因此 API 浏览器证明道具未关联时不发 GET，不作为道具 API 成功案例；道具匹配成功由 demo/component/adapter 自动化证据覆盖。延迟浏览器观察证明关闭后无旧面板复现；旧 Promise 真正 settle 与回调失效由自动化测试证明，不作为浏览器网络迟到响应证明。

root 已关闭 fixture 4175、API 前端 5174 及临时 API/mobile tabs，保留 demo 5173/tab 1。浏览器证据文件为 `haoai-next-frame-asset-references-browser-observations.json`、`haoai-next-frame-asset-references-browser-metrics.json`、`haoai-next-frame-asset-references-api-audit.json` 及同目录桌面/移动截图。

## 独立复审与未完成事项

GPT-6.1 Sol/xhigh 对最终 source 和 tests 做只读复审，无未解决的实证 P1/P2；Sol 未执行本地 checks 或浏览器验收。其审阅范围不替代 root 已运行的 typecheck/test/build，也不证明真实后端或 Grillme 通过。Grillme 探测 exit 7 / HTTP 000，GPT-5.6 Sol/xhigh 未运行；5.1 保持未勾。

本 change tasks 中 1.1–3.4 与 4.1 已按实现、自动化、浏览器、Sol、strict、文本扫描和隔离证据勾选。5.1 因 Grillme 离线保持未勾。
