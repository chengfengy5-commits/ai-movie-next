# 验收记录

本记录对应 `migrate-readonly-team-series-filter`，验收日期为 2026-10-05。证据分为冻结源码核对、自动测试、真实浏览器操作和未运行范围；loopback fixture 不代表真实 FastAPI、数据库、Worker 或生产服务验证。

## 冻结来源与仓库边界

- 根代理核对 design 中列出的六份旧源码全文 SHA-256，均与冻结提交 `23403806898550a7668a6ee7c0c457315655c39b` 一致。此切片没有扩展五项 `legacy-reference` 白名单，也没有改动前五个 change 的 30 份文件；旧库 HEAD 和原有未跟踪文件状态未改变，未读取该未跟踪文件。
- `node --test scripts/source-baseline-contract.test.mjs`：3/3 通过，退出码 0。
- `npm --prefix frontend run verify:source`：精确五项来源与固定 SHA 校验通过，敏感模式命中 0，退出码 0。
- `node --check tools/api-fixture/server.mjs`：通过，退出码 0。

## 自动验证

最终冻结后由根代理独立执行：

| 命令 | 结果 |
| --- | --- |
| `npm --prefix frontend run typecheck` | 退出码 0 |
| `npm --prefix frontend test -- --reporter=dot` | 21 个测试文件，317/317 通过，退出码 0 |
| `npm --prefix frontend run build` | 退出码 0；Vite 56 modules，CSS 35.62 kB、JavaScript 322.35 kB |
| `openspec validate migrate-readonly-team-series-filter --strict` | valid，退出码 0 |
| `openspec instructions apply --change migrate-readonly-team-series-filter --json` | ready；11/12 完成，唯一待办为 6.1 Grillme 独立审查 |

产品冻结时首次完整测试为 21 个文件、314/314 通过，并完成构建和来源检查。随后仅补了三条 UI 生命周期证据；产品代码没有变化。根代理再次执行 typecheck 与上述完整测试，结果 317/317 通过；因此沿用产品未改前的构建结果，不重复构建。团队 DTO/service/UI 和 loopback HTTP 自动测试覆盖当前 Bearer、无 query 参数、字段及响应顺序、空/移除目录、401/403/404/422/500、无效 JSON/结构、超时、登录不预取、显式重试、目录选择与分页行为。deferred 用例会真实 settle 原 Promise 并在 `act` 中 flush，检查迟到 success/401、刷新替换、隐藏返回、退出换用户，以及只改变 userId 或只改变 services 实例时旧 state 不泄露。合成 45 条验证先筛选再展示 20→40；浏览器只对 24 条 demo 和 24 条 API 剧集验收，没有把 40 条自动测试写成浏览器实测。

Sol 使用 GPT-6.1 Sol / xhigh 完成最终只读复审，未发现未解决的实证 P1/P2；两项后续测试证据也已复读确认。此结论不替代 6.1 固定 GPT-5.6 Sol / xhigh 的 Grillme 审查。

## 默认 demo 浏览器验收

根代理在新加载的 demo tab 9 上以 1280×900 桌面和 390×844 移动视口操作。四分类实际显示全部 20→24、我创建的 12、团队 16、我认领的 4；重复点“全部剧集”保留 24。团队目录按 ID区分同名项，合法 `all` ID 可选择为空结果，`__proto__` 与空名安全显示“团队”。团队选择下的章节、个人记录、素材和任务往返保留当前目录选择；离开团队分类再返回则清除该选择。键盘焦点只落在可见控件，受限剧集不出现章节入口；桌面和移动页面均无横向溢出。

演示服务的无业务 fetch 由自动 service spy 确认；浏览器验收只确认 UI 和 DOM，不将其描述为完整网络抓包。旧预览 tab 8 在 HMR 后曾保留缺少新方法的旧 services 对象；该次诊断不作为功能失败依据，正向验收使用新 tab 9，后续没有复现产品问题。

截图：

- [桌面 1280×900](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-team-series-desktop.jpg)
- [demo 移动 390×844](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-team-series-demo-mobile.jpg)

## Loopback API 浏览器验收

根代理在 API 模式 tab 10 与隔离 fixture 上验收。登录后先读剧集；进入可见团队分类才读取目录。成功目录首次读取后，选择团队和章节、个人记录、角色/场景/道具素材、任务页往返都不额外请求团队目录。500 错误保留上次成功目录、选择和 16 条共享剧集；恢复正常后显式重读成功。removed 响应移除当前选择、回到全部团队并显示名单变化提示；empty 目录显示“暂无所属团队”，全部团队中的 16 条共享剧集仍可见。当前有效 401 退出；在 401 fixture 下重新登录新账号后，全部剧集仍可用且没有预取团队目录。

正常 API 目录有两个真实合成账号。HTTP 自动测试证明 Bearer 隔离；相似的浏览器卡片内容不作为跨用户记录不同的证据。延迟响应场景验证 pending 重读、离开到任务页后返回、退出并用另一账号登录；fixture 最终记录到旧请求响应晚于新登录。浏览器 fetch 会发送 abort，所以“忽略取消后的迟到成功/401 不改变当前 state”的证据来自自动 deferred 测试，二者分开记录。

服务端请求日志为 77 条，含 OPTIONS 与 loopback 合成 PNG；团队目录 GET 共 11 次，无 query。浏览器日志实际覆盖 10 种业务接口，没有访问 `/api/auth/me`；第 11 种由自动 HTTP 测试覆盖。根代理按批准业务路径核对，未发现白名单外路径、额外查询参数、意外日志字段、token 或密码。日志仅用于本地 fixture 验收，不包含完整浏览器 Network API 记录。API 实际浏览器覆盖正常、empty、removed、500/恢复、当前 401 和延迟交互；403/404/422/invalid JSON 等状态由自动 HTTP/UI 测试覆盖，不声称浏览器逐项手测。

截图与记录：

- [API 第二账号桌面状态](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-team-series-api-second-user.jpg)
- [API 移动 390×844](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-team-series-api-mobile.jpg)
- [移除当前团队后的状态](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-team-series-removed.jpg)
- [浏览器观察 JSON](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-team-series-browser-observations.json)
- [隔离 fixture 请求日志](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-team-series-api-requests.jsonl)

临时 API tab、fixture 和 Vite 服务已关闭；demo tab 9 保留。当前只有 demo 5173 服务仍运行，loopback 3939 没有 Grillme listener，探测结果为 HTTP 000 / curl exit 7。未启动、配对或用其他审查替代 Grillme。

## 未执行项目与最终扫描

本轮未启动真实 FastAPI、PostgreSQL、Worker、R2、计费或付费请求；未提交、推送或部署。team directory GET 本身只作为隔离 API fixture 契约验收，不由本地结果推断真实后端读写副作用。Grillme 6.1 保持未完成、离线待审。

本文落盘后的根代理最终扫描覆盖 105 份手写文本文件；UTF-8、尾随空白、冲突标记及末尾换行均为 0 问题。OpenSpec strict 校验退出码 0，apply 状态为 ready（11/12）；前五 change 的 30 份历史文件 hash 不变，六份来源 hash 与 design 一致，旧库 HEAD 与原有状态不变。
