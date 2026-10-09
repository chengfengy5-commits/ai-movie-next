# 验证记录：只读章节与分镜浏览

执行日期：2026-10-04（Asia/Shanghai）

## 自动化与静态检查

| 检查 | 实际命令或方式 | 结果 |
| --- | --- | --- |
| 章节 UI 定向测试 | `npm --prefix frontend test -- src/features/chapters/ChapterBrowser.test.tsx` | 全量套件后补齐空章节列表与本人锁快照用例；最终定向复验通过，21/21。包含章节切换、重新读取期间迟到 401 和当前资产 401。 |
| 契约与分镜投影定向测试 | `npm --prefix frontend test -- src/shared/api/contracts.test.ts src/features/chapters/storyboardProjection.test.ts` | 本轮通过，2 个文件、29/29。包含严格 ISO 日期、越界日期、Pydantic 字段对照、稳定身份映射与无索引回退反例。 |
| Workspace / AuthProvider 延迟响应测试 | `npm --prefix frontend test -- src/app/Workspace.test.tsx src/features/auth/AuthProvider.test.tsx` | 第二 Luna 定向执行通过，2 个文件、15/15；覆盖切剧集、退出重登和迟到成功/401。 |
| 来源 manifest 合同 | 根代理独立执行 `node --test scripts/source-baseline-contract.test.mjs` | 3/3 通过，exit 0。 |
| 冻结来源验证 | 根代理独立执行 `node scripts/verify-source-baseline.mjs` | 精确五项 allowlist 与冻结 SHA 匹配，0 条敏感模式命中，exit 0；旧库只读 HEAD 为 `23403806898550a7668a6ee7c0c457315655c39b`。 |
| OpenSpec | 根代理独立执行 `openspec validate migrate-readonly-chapters-storyboards --strict` | strict 校验通过，exit 0。 |
| 全量前端 typecheck | 根代理执行 `npm --prefix frontend run typecheck` | exit 0；另在最后两条 UI 用例落盘后再次执行，仍 exit 0。最初一次检查因测试 mock 未使用参数报 TS6133；改为 `_seriesId` / `_chapterId` 后复验通过。 |
| 全量前端测试 | 根代理执行 `npm --prefix frontend test` | 9 个文件、111/111 通过，exit 0；随后新增两条章节 UI 用例，章节测试文件最终定向 21/21 通过，不将其表述为全量 113/113。 |
| 构建 | 根代理执行 `npm --prefix frontend run build` | exit 0；Vite 44 modules，CSS 23.98 kB / gzip 6.21 kB，JS 267.88 kB / gzip 82.54 kB，1.71 s。构建后仅新增测试用例，没有改应用源码。 |
| GPT-6.1 Sol / xhigh 只读复审 | Sol 最终复核业务和 deferred 用例 | 未发现未解决 P1/P2；补齐空章节列表和本人锁快照两个 UI 覆盖，已由章节文件 21/21 定向测试通过。 |
| 文本完整性扫描 | 根代理最终扫描 38 个实际文件 | 0 空白、冲突标记或缺少末尾换行问题，exit 0；扫描覆盖最后新增的两条章节 UI 用例。 |

全量测试结果对应新增空章节/本人锁两个用例之前；最终章节文件的 21/21 与全量 111/111 分开记录，不外推成同一轮全量通过。

## 真实浏览器验收

浏览器由 CUA 控制 Codex IAB 执行语义定位、键盘输入与点击，和 Vitest/jsdom 分开记录；本节不把 UI 单测当浏览器验收。

### Demo 模式（4.1）

- 完成 demo 登录，使用 Enter 提交；筛选剧集、加载更多后进入可读剧集。
- 在剧集卡片按钮按 Tab 聚焦并用 Enter 进入章节；受限剧集没有进入按钮，他人锁快照仍只读展示。
- 章节之间用键盘切换；从第一章 Shift+Tab 可返回“返回剧集列表”，返回后筛选与已显示数量仍保留。
- 页面切章节时列表容器仍挂载且拥有 `hidden`、`inert` 属性；真实浏览器进入后检查 `activeElement` 为 BODY、焦点不在隐藏列表内。键盘可从当前页面到达返回与章节按钮，退出后会话数据清除。
- jsdom 对“点击后父容器变为 hidden/inert”会留下旧按钮为 `document.activeElement`，与真实 IAB 观察到的 BODY 不同；自动测试保留 DOM 的 hidden/inert 检查，焦点行为以独立的真实浏览器证据为准。
- 桌面视口 1280×900；移动视口 390×844，实测 viewport/scrollWidth 为 390/390，无横向溢出。
- 操作后截图：
  - [桌面](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-chapters-desktop.jpg>)
  - [移动端](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-chapters-mobile.jpg>)

### 隔离 API 模式（4.2）

- 真实页面为 `http://127.0.0.1:5174`，fixture 为 `http://127.0.0.1:4175`；均为本地 loopback。fixture 账号为 `demo / demo123`。
- 章节默认选择响应数组首项“第二章：雨夜证词”，未按 `order` 重排。切换 A/B/null/[]/A 并再次切回 A 后，已成功资产快照命中；相同章节无重复资产 GET，空章不发资产请求。
- 章节与资产分别实测 401、403、404、500、invalid JSON、空数组；章节 500 后恢复并重试可恢复内容，资产 500 后恢复并只重发资产 GET。章节和资产重试清理当前快照。
- 合成图片正常加载（`naturalWidth=1`）；图片 404 显示本地占位。章节 content 的分镜顺序与资产 `frame_index` 顺序不一致时，仍按稳定 storyboard id 与同章资产正确配对。
- 退出、返回、空章节和资产错误均未残留旧章节文字或图片；隔离 fixture 服务端请求日志只含允许的五条业务接口、OPTIONS 和同源合成媒体，无远端媒体、锁、写入或生成请求。
- API 与 fixture 临时进程及 API 浏览器 tab 已关闭；退出状态下验证结束。保留供预览的 demo Vite 5173 不属于本 API 验收进程。
- 操作截图：
  - [API 模式](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-chapters-api.jpg>)
  - [破图占位](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-chapters-broken-image.jpg>)

## 边界

本地演示、自动测试、源文件静态核对和 fixture 浏览器验收均不等同于旧 FastAPI、PostgreSQL、Worker、R2、生产认证/权限、数据库排序或锁并发回归。本轮未连接真实服务、未生成任务、未获取编辑锁，未推送或部署。Grillme 固定模型审查当前离线，任务 6.1 保持未完成。
