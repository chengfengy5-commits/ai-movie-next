# Verification：后端剧集访问策略

## 范围与结论

本变更在隔离 backend 包中新增剧集访问策略，并将其显式接入既有个人粗剪 GET/PUT。它没有迁移旧站点入口、生产身份验证、会员/团队管理或其他业务域。实际代码审查收据记录 13 个新增代码/测试文件，Sol 以 accepted-source-and-test-review 结论完成只读源码审查；该审查不等同于指定的 Grillme 外审。

## 验证分层

- 后端自动化：root final log 为 80 passed in 1.65s。实现代理另有定向 26/26 记录；这不是 root 的独立全量结果。
- 语法与来源保全：Python 3.12 compile 检查 31 个模块，33 个代码/配置路径 hash；固定来源契约 3/3，通过并核对 5 项批准摘录，零敏感模式命中。固定旧来源基于 HEAD 23403806898550a7668a6ee7c0c457315655c39b。
- OpenSpec：代码阶段 strict、status、apply 命令均实际退出 0。apply 当时返回 ready；较早进度快照不可替代当前 tasks 复选框。文档阶段开始前 tasks 文件实际为 9/12；勾选本任务 4.1 后为 10/12。4.2 文档复核和 5.1 独立外审仍保留待办。
- 文本与保全快照：代码文档写入前，text 检查 297 个文件、13 个既有排除项、0 issue；conservation 检查 310 项 inventory、288 immutable、142 prior code、135 history、21 fixed sources，均无漂移。该快照早于本变更文档，不是文档后的最终检查。

实际命令、测试结果及 hash 清单可在[根验收收据](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-series-access-root-code-acceptance.json)、[最终 backend 测试日志](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-series-access-root-backend-final.log)、[Python 语法清单](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-series-access-root-syntax-manifest-final.json)、[来源契约测试日志](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-series-access-root-source-contract.log)、[来源 baseline 日志](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-series-access-root-source-baseline.log)、[Sol 源码审查](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-series-access-code-review.json)及[查询次序澄清](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-series-access-contract-clarification.json)。

## Loopback 访问验证

root 使用新 app factory、临时 SQLite、显式合成身份及真实 SQL series/team/claim 策略执行了 28 个本机 loopback 请求：13×200、10×403、4×404、1×401。两个服务进程均退出 0；重新启动后记录仍可读。用例覆盖作者、成员、owner、认领权限、当前认领用户名、权限撤销后的下一请求、成员移除后的下一请求以及授权保存/重读。

拒绝 GET 的 ASGI SQL 证据证明策略拒绝后没有查询源素材和本人草稿。拒绝 PUT 则由 root 比较完整既有私人行，确认未改变且没有新草稿；不能把这项完整行比较说成取得了 PUT SQL SELECT 轨迹。所有测试与 loopback 只使用自有临时数据库并已清理服务、端口和临时目录。请求日志仅记录 method、pathname 和 status。

判定顺序以当前章节加载之后开始：章节 SELECT 已包含 content；策略先于 content 解析以及后续素材目录和私人草稿读取。准确解释见[契约澄清收据](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-series-access-contract-clarification.json)，不能将结果写成零 chapter SELECT 或“所有来源 SQL 均为零”。

## 限制与未完成项

SQLite 实验和合成身份不能证明真实 JWT、password version、session revocation、active membership 接线、PostgreSQL 锁或撤销原子性，也不覆盖 Worker、队列、媒体/provider、R2 或生产部署。三张 Core 表只是当前查询所需的最小投影，不是生产 schema 或 migration。前端第21批测试属于历史证据，本批未重跑前端测试、构建或浏览器。

实现请求 GPT-6 Luna/xhigh，分析审查请求 GPT-6.1 Sol/xhigh；运行时模型元数据未独立确认。指定 GPT-5.6 Sol/xhigh Grillme 外审仍离线未执行，不能由本地测试或本轮 Sol 只读源码审查替代。本轮不归档、不部署。
