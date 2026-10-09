# 验收记录：个人粗剪草稿只读面板

验收日期：2026-10-05（Asia/Shanghai）

项目：`/Users/yanghaibo/data/projects/ai/haoai-next`

接口：`GET /api/chapters/{chapter_id}/rough-cut`，使用当前 Bearer、不带 query；仅在章节页面显式打开或点击重读时请求。

## 自动验证与代码复审

下表未特别注明者由 root 实际执行；fixture 代理定向结果按行标注：

| 命令 | 结果 |
| --- | --- |
| `npm --prefix frontend run typecheck` | 退出码 0 |
| `npm --prefix frontend test` | 25 个测试文件，375/375 通过 |
| `npm --prefix frontend run build` | 退出码 0；Vite 59 modules，CSS 37.93 kB（gzip 8.26 kB），JS 333.17 kB（gzip 98.32 kB） |
| `npm --prefix frontend test -- src/shared/api/personal-rough-cut-http.test.ts` | fixture 代理定向 22/22 通过；该文件也包含在 root 的 375/375 全量测试中 |
| `node --check tools/api-fixture/server.mjs` | root 冻结产品验证及 fixture 代理定向检查均退出码 0 |
| `node --test scripts/source-baseline-contract.test.mjs` | root 执行，3/3 通过 |
| `npm --prefix frontend run verify:source` | root 执行，exact-five 五项匹配，敏感模式 0 命中 |

产品冻结构建后只新增一条 ChapterBrowser 资产延迟成功用例，没有改产品代码；该用例定向 1 个文件、34/34 通过，随后 typecheck 再次退出码 0，因此没有重复 build。它真实 resolve 原资产 Promise 并 `await act`，断言粗剪面板仍显示、原图成功显示、粗剪与资产请求各仍为一次，制作记录请求为零。前端其余 deferred 用例也实际 settle 原 Promise 并刷新 React 状态，覆盖重读后旧 success/401、关闭重开、user/services/chapter 分别变化、切章/章节重读/原图重读、返回换剧集和退出后新用户；不以仅触发 abort 作为迟到响应证明。

GPT-6.1 Sol / xhigh 对冻结源码、DTO、demo、组件和新增测试完成只读复审，未发现未解决实证 P1/P2。旧来源全文 hash 与 change design 固定值匹配。root 按恢复点保存的 36 文件历史 hash 清单比对，changed=[]；这只说明恢复点后的文件未变，不声称中断前另有一轮完整比对。旧库 HEAD 仍为 `23403806898550a7668a6ee7c0c457315655c39b`，状态仅包含原有未跟踪 OpenSpec yaml；未读取该文件内容。root 最终扫描 118 份文本文件为 0 问题，36 份历史 hash 比对 changed=[]，OpenSpec strict 退出码 0、状态 valid；`openspec instructions apply --change migrate-readonly-personal-rough-cut --json` 退出码 0，apply 状态 ready，12 项中完成 11 项，唯一未完成项为离线待办 6.1。

## 默认 demo 浏览器

root 用真实浏览器完成 1280×900 桌面、390×844 手机与键盘验收。只在章节中显式打开粗剪；验证 wire 顺序与源镜头位置、纳入/待安排、失效旧引用数量、重读与关闭。粗剪与个人制作记录互斥；章节重读、切章、返回和退出后面板卸载。全部分类下返回列表时保留 24 条已展示数；另在 team-studio 筛选下验证 16 条剧集，进入章节返回后筛选和选择仍保留。还回归了登录、素材分类、任务分页与团队筛选。Tab 焦点不留在 hidden/inert 内容中，两个 viewport 均无横向溢出。默认样本保持两个非空章节；demo 无业务网络请求由自动 service/UI 测试证实，浏览器 DOM 中没有粗剪媒体节点。

![Demo 桌面截图](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-personal-rough-cut-demo-desktop.jpg)

![Demo 移动截图](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-personal-rough-cut-demo-mobile.jpg)

## 隔离 API 浏览器与请求审计

root 在 loopback Vite/fixture 页面验收两合成账号读取同章私有草稿：第一用户版本 6、有 2 条引用且顺序与源镜头不同；第二用户版本 11、有 1 条引用及待安排行。API 页面覆盖正常、未保存、空列表、失效引用、无效结构、500、会员 403 及重读恢复，也验证有效 401 退出。延迟请求下可以在粗剪 pending 时重读、关闭、重读原图、切章和退出。浏览器请求会由 abort 取消；迟到 success/401 的实际结算与新会话隔离依据自动 deferred 测试，而不是浏览器声称收到已取消的响应。

初次显式进入粗剪面板之前，fixture 日志中 rough-cut GET 为 0，个人记录 GET 为 0，章节资产 GET 为 1；打开后累计 rough-cut GET 为 1、个人记录 GET 为 0、资产 GET 仍为 1。全程无视频 URL 访问；仅观察到既有本地 PNG 与 OPTIONS。API 审计记录 119 条 JSON 请求事件，12 个批准业务路由族均有浏览器请求，粗剪 GET 共 17 次，另有 59 条 OPTIONS 事件；批准白名单为 12 条业务路由（11 GET 加登录 POST）。请求日志只记录 method、path、status；路由与敏感模式审计无违规，不记录响应正文或草稿原始 ID。

![API 第一账号截图](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-personal-rough-cut-api-first-user.jpg)

![API 第二账号截图](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-personal-rough-cut-api-second-user.jpg)

![API 移动截图](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-personal-rough-cut-api-mobile.jpg)

![API 空列表截图](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-personal-rough-cut-api-empty.jpg)

![API 已保存空投影截图](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-personal-rough-cut-api-saved-empty.jpg)

![API 会员错误截图](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-personal-rough-cut-api-membership-error.jpg)

请求日志、浏览器观察记录、路由审计、root 检查和恢复点 hash 清单：

- [API 请求日志](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-personal-rough-cut-api-requests.jsonl)
- [API 日志审计](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-personal-rough-cut-api-log-audit.json)
- [浏览器操作观察记录](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-personal-rough-cut-browser-observations.json)
- [root 检查记录](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-personal-rough-cut-root-checks.json)
- [恢复点历史 hash 清单](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-personal-rough-cut-resumption-history.json)

临时 API tab 已关闭，fixture 4175 与 Vite 5174 已停止；lsof 只保留 demo 5173，Grillme 3939 无监听。本次没有启动旧 FastAPI、数据库、Worker、队列、R2、付费或生产媒体链路，没有提交、推送或部署。Grillme 固定 GPT-5.6 Sol / xhigh 探测返回 curl exit 7 / HTTP 000，仍保持离线未完成；Sol 复审不替代该项。
