# 验证记录

验收日期：2026-10-04（Asia/Shanghai）。构建、测试、来源校验和浏览器验收均在新库 `/Users/yanghaibo/data/projects/ai/haoai-next` 进行；旧库只按冻结 SHA 读取，不修改。当前实现使用 Node.js 20.20.2、npm 10.8.2；Vite、Vitest、jsdom 的 `engines` 条件已写入 `frontend/package.json`，未更改全局 Node。

## 自动检查

| 工作目录 | 命令 | 结果 |
| --- | --- | --- |
| 仓库根目录 `/Users/yanghaibo/data/projects/ai/haoai-next` | `npm --prefix frontend run typecheck` | 通过，退出码 0。 |
| 仓库根目录 `/Users/yanghaibo/data/projects/ai/haoai-next` | `npm --prefix frontend run test` | 通过，6 个文件、37 个测试；覆盖 UI、API adapter、冻结 Pydantic 字段、回环配置、会话竞态及原始 JSON/请求超时/响应体超时。退出码 0。 |
| 仓库根目录 `/Users/yanghaibo/data/projects/ai/haoai-next` | `npm --prefix frontend run build` | 通过，退出码 0；Vite 转换 41 个模块，生成 CSS 文件体积（gzip 前）16.96 kB、JS 文件体积（gzip 前）249.14 kB。 |
| 仓库根目录 | `node scripts/verify-source-baseline.mjs` | 通过；固定 5 个来源条目均匹配冻结提交 `23403806898550a7668a6ee7c0c457315655c39b`，敏感模式命中数为 0。 |
| 仓库根目录 | `node --test scripts/source-baseline-contract.test.mjs` | 通过，3/3；包含空清单和超范围摘录的拒绝用例。 |
| 仓库根目录 | `openspec validate migrate-react-shell-series-list --strict` | 通过，Change valid。 |

根代理在 2026-10-04 00:17（Asia/Shanghai）对最终树再次独立执行 `npm --prefix frontend run build`（退出码 0，41 个模块，1.11s）和 `npm --prefix frontend test`（退出码 0，6 个文件、37 个测试，3.55s）。根代理也独立复核来源 manifest、3 个来源校验用例和 OpenSpec strict 验证，均通过；检查 42 个新增文本文件的行尾空格、冲突标记和文件末尾换行，0 项问题。

Vitest 的 HTTP 集成用例启动临时 Node HTTP fixture 并使用真实 `fetch`，断言登录 JSON、Bearer 会话、`POST /api/auth/login → GET /api/auth/me → GET /api/series` 的准确顺序和仅三条请求；也验证响应头后挂起的响应体超时、等待响应超时和无效 JSON。该 fixture 来源于冻结 schema 的独立测试实现，不会导入旧 FastAPI、连 PostgreSQL 或启动 Worker。

## 真实浏览器验收

由根任务代理在 2026-10-04 使用 CUA 控制 Codex in-app browser 完成真实 UI 验收，通过语义定位进行点击和输入；这与 Vitest/jsdom 自动测试分开记录。首选 Node 浏览器 runtime 因 native ABI 不可用，改用 CUA 后实际验收成功。临时 API 页面运行于 `127.0.0.1:5174`，独立 fixture 运行于 `127.0.0.1:4175`。

- 默认演示完成错误登录、成功登录、四筛选（mine 12、team 16、claimed 4）、全部 20 条后加载至 24 条、刷新恢复、退出和键盘 Tab/Enter 操作。390×844 和 1280×900 视口均未产生横向溢出；默认演示没有业务服务或远端媒体请求。
- API 模式没有继承演示会话。fixture 登录、刷新后的 `/me` 再到 `/series`、会员 403 保留会话、500 保留会话并重试恢复列表、列表 401 清理会话并回到登录、空列表和原始无效 JSON 的消息与重试均已实测。
- API 临时 Vite/fixture 进程已停止；演示预览进程 `127.0.0.1:5173` 由根代理保留。fixture 只供本地浏览器验收，不能作为旧后端或生产接口通过的证据。

## 未执行项

本次没有运行真实旧 FastAPI、PostgreSQL、Worker、远端媒体或生产接口；没有提交、推送或部署。Grillme 固定 GPT-5.6 Sol / xhigh 补审因本机连接离线未执行，任务 6.1 保持未勾选。上述静态、自动与浏览器证据均不替代该补审或真实后端回归。
