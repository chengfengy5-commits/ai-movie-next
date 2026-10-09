# 验收记录

验收日期：2026-10-05（Asia/Shanghai）。本 change 实现当前章节个人制作记录只读面板，只向隔离 demo/fixture 验收。冻结 GET 会执行媒体协调和记录维护，可能更新同章其他用户的认可状态；没有运行真实 FastAPI、PostgreSQL、Worker、R2 或付费请求，也没有提交、推送或部署。

## 来源与自动检查

- 根代理核对五份冻结源码 SHA 与 design 一致，旧库 HEAD 仍为 `23403806898550a7668a6ee7c0c457315655c39b`；source-baseline 合同 `3/3` 通过，exact-five verifier 校验五项且敏感模式命中 `0`。
- 前三 change 的 18 份历史文件 SHA 与本轮开始快照一致；旧库只有原有未跟踪 `.spec-superflow.yaml`，本轮未读取或修改。
- fixture 代理的 notes HTTP 定向测试结果为 `4` 个文件、`62/62` 通过，`node --check tools/api-fixture/server.mjs` 通过。测试真实 loopback fetch，覆盖第二合成账号私有数据、Bearer、路径一次编码、401/403/404/422/500、响应头及响应体超时、坏 JSON/结构、取消、合法空/不可读及媒体摘要状态；不导入旧后端或连接数据库。
- 根代理最终前端类型检查与全量测试退出码均为 `0`：Vitest `16` 个文件、`230/230` 通过，耗时 `17.56s`。最后增加的会员 403 UI 用例也通过定向测试 `3/3`。
- 根代理此前构建退出码为 `0`：Vite `51` 个模块，CSS `30.47 kB`（gzip `7.18 kB`），JS `299.19 kB`（gzip `90.28 kB`），耗时 `1.14s`。构建后只增加一条测试和文档，产品代码未变。
- 根代理最终检查 `62` 个手写文本文件的 UTF-8、行尾空白、冲突标记及末尾换行，均无问题；前三 change 的 `18` 个文件 SHA-256 与本轮开始快照一致。
- OpenSpec 对当前 change 最终使用 `--strict` 校验通过；GPT-6.1 Sol / xhigh 独立只读复审未发现未解决实证 P1/P2。

## 根代理真实浏览器验收

使用 CUA 控制 Codex IAB 的真实 UI，通过语义定位点击和输入；此结果与 Vitest/jsdom 自动测试分开记录。

Demo 模式实测登录、列表展开至 `24/24`、显式打开雾港来信记录、待重新确认/待修/旧记录/续作提示、重新读取、关闭后重新打开、切章、revision 0 空记录和返回列表保留展示数；随后实测团队筛选 `16/16`、打开团队剧集记录并返回仍保留筛选。键盘可到达记录关闭按钮；桌面 `1280px` 与移动 `390px` 视口均无横向溢出。默认 demo 的零业务/媒体 fetch 由自动 spy 和代码边界核实，浏览器操作本身不冒充完整网络抓包。

Demo 浏览器截图：`/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-personal-notes-desktop.jpg`、`/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-personal-notes-demo-mobile.jpg`。

隔离 API 模式实测两个合成账号读取同一章节时各自看到私有记录、打开前无记录 GET、浏览器本地 WebCrypto 媒体核对、有效/过期认可、字面 HTML 备注与旧记录、resume 提示、revision 0 空章、unreadable 章、失效 resume、mismatch、500 保留会话和显式重试、延迟响应期间重读/关闭/切章/退出，以及当前有效 401 注销。API 浏览器没有实测 403、404、422、invalid JSON 或 double-empty；这些状态由自动 HTTP/UI 测试覆盖，不在此写为浏览器通过。手机视口 `390×844` 的 `width` 与 `scrollWidth` 均为 `390`；桌面视口为 `1280×720`。没有新增媒体请求。

隔离 fixture 服务端日志共 `93` 条记录，仅含九条批准业务路由、OPTIONS 和原有 loopback 图片路径；个人记录接口状态计数为 HTTP 200 `16` 次、401 `2` 次、500 `1` 次。React 开发 StrictMode 会重复部分请求，不能据此声称一次显式打开只产生一次 GET。日志 artifact 为 `/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-personal-notes-api-requests.jsonl`，只含 method/path/status。临时 API/fixture 进程与页签已关闭，端口 `5174` 和 `4175` 无监听；demo `5173` 保留供预览。

API 浏览器截图：`/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-personal-notes-api-second-user.jpg`、`/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-personal-notes-mobile.jpg`。

真实后端、数据库维护影响、生产权限、跨进程并发及真实媒体链路均未验收。Grillme 固定 GPT-5.6 Sol / xhigh 当前离线，根代理探测 `127.0.0.1:3939` 未连接成功；没有启动或配对该审查。它保持独立待办，不能由本地实现或 GPT-6.1 Sol 复审替代。
