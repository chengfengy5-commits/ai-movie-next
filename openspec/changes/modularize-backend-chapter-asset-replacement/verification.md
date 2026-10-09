# 验证记录：章节素材替换后端

## 范围与阶段快照

本文记录本批代码、后端回归和本地 loopback 的实际证据，不代表生产部署或整体后端迁移完成。本文编写时任务为 10/13：规划、五项实现、源码审查及根三项验收已有对应收据；本文的 Sol 文档复核与文档版本后的最终 root scope/CLI 守卫仍待执行，指定外审也未完成。最终进度以 `tasks.md` 和后续实际收据为准。

实施模型请求路线为 GPT-6 Luna/xhigh，源码审查请求路线为 GPT-6.1 Sol/xhigh；实际运行模型及推理元数据未独立核验。指定的 GPT-5.6 Sol/xhigh Grillme 外部评审尚未执行，内部审查和根验收不替代该项。

## 固定来源、代码审查与后端回归

本批按冻结清单和固定 Git 源审查，不以旧工作区的实时文件替代来源。独立源码审查接受，且没有未关闭的必要问题，见[源码审查收据](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-chapter-asset-replacement-source-review-01.json)及[代码接纳记录](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-chapter-asset-replacement-root-code-accepted-01.json)。

根执行的完整后端回归为 472 passed、20 warnings；176 个后端 Python 文件的内存语法编译通过，相关进程退出码均为 0。实际记录见[回归日志](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-chapter-asset-replacement-root-full-backend-01.log)和[回归收据](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-chapter-asset-replacement-root-full-backend-01.json)。

## 本地 HTTP 与 SQLite

根受控 loopback 完成 18/18 组、53 条有序 driver 请求，和 53 条服务端请求逐项匹配；961 项 helper 检查全部通过，359 条 SQL 尝试均完成且无 SQL 错误。执行使用真实本地 HTTP、真实认证身份和临时 SQLite owner fixture。真实结果包含阶段一/阶段二提交、阶段故障及提交确认未知的失败响应；fresh canvas GET 与 Core SQL 快照分别核实持久状态，canvas GET 不被当作章节内容读取。根验收确认 22 个数据库 fixture 都有唯一终态清理记录，并在子进程退出后确认临时目录、SQLite 文件及 sidecar 已清理、端口拒绝连接且可重新绑定。详细记录见[loopback 接纳收据](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-chapter-asset-replacement-root-TCP-accepted-01.json)、[根执行收据](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-chapter-asset-replacement-root-loopback-execution-02.json)和[运行收据](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-chapter-asset-replacement-loopback-run-02.json)。18 是验收分组数，不是 HTTP 请求数；57 是工厂登记方法数，不是本批 TCP 覆盖数。

## 保留的失败历史

首次 root loopback 实际完成 17/18 组、52 条请求，唯一失败在 H18 的缺少替换 UoW 503 详情断言。真实响应为 43 字节，文字是“素材替换服务尚未接线”；产品默认错误详情本来如此，偏差只在 helper 预期文字。helper 后续只修正两处预期字符串，没有改产品代码；第二次运行才完成全部 18 组。首次失败保留于[首轮根执行收据](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-chapter-asset-replacement-root-loopback-execution-01.json)和[首轮运行收据](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-chapter-asset-replacement-loopback-run-01.json)，不以第二次通过覆盖首轮结果。

## 限制

本批使用临时 SQLite，不是 PostgreSQL 锁或并发验收；没有覆盖全部 57 个已登记方法的 TCP、完整前端、浏览器、真实 provider、生产数据库或部署。指定外部 Grillme 评审仍未执行。以上证据只证明收据绑定版本上的本地后端回归和有限 HTTP/SQLite 流程。
