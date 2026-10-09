# 验收记录

验收日期：2026-10-05（Asia/Shanghai）。本 change 只连接本地 demo 和隔离 loopback fixture；未启动真实旧 FastAPI、PostgreSQL、Worker、R2、外部供应商或付费服务。

## 自动化与来源检查

root 在最终前端树上独立执行并通过：

| 检查 | 结果 |
| --- | --- |
| `npm --prefix frontend run typecheck` | 退出码 0 |
| `npm --prefix frontend test` | 19 个测试文件，266/266 通过，退出码 0 |
| `npm --prefix frontend run build` | 退出码 0，Vite 54 modules |
| `npm --prefix frontend run verify:source` | exact-five 白名单 5 项，敏感模式命中 0，退出码 0 |
| `node --test scripts/source-baseline-contract.test.mjs` | 3/3 通过，退出码 0 |
| `node --check tools/api-fixture/server.mjs` | 退出码 0 |
| `openspec validate migrate-readonly-my-tasks --strict` | 有效，退出码 0 |

实现侧定向结果：`myTasks.test.ts`、`MyTasksPage.test.tsx` 与 `services.test.ts` 验证 DTO、只读展示、分页、总数收缩和 demo 无 fetch；`Workspace.test.tsx` 覆盖显式入口、返回保留剧集过滤/展示数，以及离开重开和退出新用户后的迟到请求隔离。最终定向命令为：

```sh
npm --prefix frontend test -- src/shared/api/myTasks.test.ts src/features/tasks/MyTasksPage.test.tsx src/shared/api/services.test.ts src/app/Workspace.test.tsx -t 'my task response contracts|read-only task page|serves paginated demo tasks|enters tasks only|does not let a late task|ignores a late task 401'
```

结果为 4 个文件、17 passed，29 skipped；`npm --prefix frontend run typecheck` 另行通过。隔离 HTTP 契约用例共 19 项，包含两账号 Bearer 隔离、响应页/大小错误、重复 id、空页、总数缩小、错误分类、取消及响应头/响应体超时；最终全量测试包含这些用例。源合同静态核对和浏览器验收不是实际 FastAPI 或数据库回归。

GPT-6.1 Sol / xhigh 对最终源码及 deferred 测试只读复审未发现未闭环实证 P1/P2。复审闭环包括总数由 23 缩至 7 时第三页无效分页按钮与显式回第一页、demo `ai-review` 的 request_data 与投影字段相符、`frame_text` 不展示、同账号离开并重开后旧 401 不注销当前会话，以及侧栏任务入口的默认/hover/focus/active 样式。该复审不替代 Grillme。

## Demo 浏览器验收

root 使用新加载的本地 Vite demo 进行了真实桌面及手机 UI 操作，未见 console error。1280×720 桌面及 390×844 手机页面均无横向溢出。浏览器验证了显式进入任务页、12 条样例记录两页分页、上一页/下一页/重新读取、进入任务页时卸载原章节、素材和个人制作记录上下文；返回剧集列表后可重新进入章节/素材页。返回后保留已展开的 24/24 剧集和筛选，重开任务页回到第一页。键盘 Enter 可进入任务页，Tab 可抵达重新读取按钮，焦点没有落在 hidden/inert 的剧集列表。

任务页 DOM 没有链接、图像、音频或视频元素。demo 零业务 fetch 的证据来自自动 spy；浏览器 DOM 检查不是完整网络抓包。一次较早的长开 HMR tab 曾保留旧 services 对象，出现 `listMyTasks is not a function`；完整重载及新加载后未复现，最终浏览器验收使用新加载页面。

截图：

- [Demo 桌面截图](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-my-tasks-desktop.jpg)
- [Demo 手机截图](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-my-tasks-demo-mobile.jpg)

## 隔离 API 浏览器验收

root 使用本地 API 页面和 fixture 验收。初次显式进入任务页之前，fixture 日志没有 tasks GET。两账号 Bearer 隔离由 HTTP 两账号测试验证；早期浏览器也操作过身份切换及 390px 手机页，但发生在长开 HMR tab 状态问题被识别之前，不将其称作最终冻结后的重复验收。最终冻结后 root 重做了正常三页、23→7 总数收缩、500 错误恢复、pending 离开/重开/退出流程及当前有效 401。第三页在显式重新读取后收到 total=7 的合法空页；上一页和下一页禁用，“回到第一页”才发第一页 GET 并展示 7 条记录，没有自动跳页。浏览器中的 5 秒延迟请求可能被 AbortController 取消；真正忽略取消后的迟到成功/401由 deferred 自动测试覆盖。

fixture 请求日志共 78 条，其中 tasks GET 23 条：200 共 21 条、500 一条、401 一条；日志只包含 method、pathname、status，root 独立审核为批准路径且固定分页 query，没有超范围请求。记录数不是每个动作恰好一条，React StrictMode 下 GET 可能重复。API 浏览器未实测的 403/404/422/invalid JSON 错误边界由自动测试覆盖。临时 API 页签和 5174/4175 服务已关闭；demo 5173 留作本地预览。此次没有访问真实任务 API 或生产会话。

- [API 第二账号早期操作截图](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-my-tasks-api-second-user.jpg)
- [API 手机早期操作截图](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-my-tasks-api-mobile.jpg)
- [总数收缩截图](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-my-tasks-total-shrink.jpg)
- [500 错误状态截图](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-my-tasks-500.jpg)
- [隔离 fixture 请求日志](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-my-tasks-api-requests.jsonl)

## 来源与剩余边界

根代理核对本 change 的五份冻结来源全文 hash 与 design 一致；exact-five 来源目录未扩展，前四 change 的 24 份文件 hash 与本轮起始快照相同，旧库 HEAD 仍为冻结 SHA `23403806898550a7668a6ee7c0c457315655c39b`，其原有唯一 untracked YAML 状态未改。没有提交、推送或部署。

任务列表 helper 本身无显式写入、锁或外部调用，但旧认证路径可能更新登录 session；没有用真实后端验证其数据库副作用。没有验证真实 FastAPI、PostgreSQL、生产权限/排序、账务或 Worker/R2/供应商行为。Grillme 固定 GPT-5.6 Sol / xhigh 在 3939 端口离线（curl 退出 7、HTTP 000）；没有启动、重配或降档。此项继续保持未完成。

以上命令和浏览器结果由 root 实际执行；实现侧定向测试与 Sol 只读复审分层列出，不互相替代。本文落盘后，root 最终扫描 95 个手写文本文件，问题数为 0；本 change 的 OpenSpec strict 退出码为 0，前四 change 的 24 个文件 hash 全部不变。
