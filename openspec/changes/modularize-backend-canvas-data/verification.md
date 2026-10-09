# 验收记录

本文记录本批实际执行的后端回归和隔离 loopback 验收；规划状态、代码审查、自动测试和真实 HTTP 证据分别陈述，不互相替代。

## 后端回归

根执行的完整后端回归为 374/374 通过，20 条警告，耗时 13.21 秒；环境为 Python 3.12.13 与 pytest 9.1.1。根记录了子进程 wait/reap 与测试临时目录清理。详见 [root-full-backend-02.json](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-canvas-data-root-full-backend-02.json) 和 [root-code-acceptance-01.json](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-canvas-data-root-code-acceptance-01.json)。更早失败的全量记录及 helper 静态候选仍保留；本文只把上述绑定版本作为通过结果。

## 隔离 HTTP 验收

根在临时 SQLite 与本地 HTTP 环境完成 17 组、67 条 driver 请求，并与 67 条有序服务端请求相等；255/255 项检查通过。请求窗口观察到 429 条 SQL 尝试、427 条完成。两条未完成事件分别是 C11 的真实唯一键冲突和 C15 在提交后主键重读前注入的故障，不代表这些断言被跳过。111 个 Session 均成功关闭；HTTP client、监听器、fixture connection、server thread、engine 与临时目录的清理检查通过，根也 wait/reap 了自有执行进程。详见 [root-loopback-acceptance-01.json](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-canvas-data-root-loopback-acceptance-01.json)、[root-loopback-process-01.json](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-canvas-data-root-loopback-process-01.json) 和 [root-loopback-native-01.log](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-canvas-data-root-loopback-native-01.log)。

关键断言使用非空完整 owner/历史行快照，逐列检查请求写集合之外的数据。C08、C10–C12 和 C16 中由第二物理连接已提交的受控锁删除、画布更新及行插入/删除，与请求事务分别核对；请求回滚不会撤销另一连接已经提交的变化。C17 是既有粗剪/制作记录接口的正向私有读写 smoke：owner 的合法保存会写私有行，member 默认读取不创建其私有行且不改变 owner 全行。因此不能把 C17 或整次验收描述为私有表零 DML。

工厂登记 54 个方法，但本次 TCP 正向覆盖是新增的画布 GET/PUT 两个方法及既有个人制作记录/粗剪四个 smoke 方法；不能据登记数称 54 个方法均经过 TCP 验收。本次未执行 PostgreSQL、TypeScript/前端套件、浏览器或部署验收。

## SQL 追踪边界

C17 中序号 397、406、407、424 的四条已完成 SELECT 实际发生在既有个人制作记录 HTTP 请求内，但原始追踪把它们记为 `kind=fixture` 且 `sessionId=null`。这些不是 fixture-only 查询；其 HTTP 请求范围可信，但单条事件的角色/Session 归属不完整。因此 429 是请求窗口观测到的尝试数，不能据此声称每条 SQL 都有精确 Session 归属，也不能把这四条当作完整旧记录查询/锁顺序或零业务 SELECT 的证明。原始记录未改动；该限制见 [trace-boundary-review-01.json](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-canvas-data-root-loopback-trace-boundary-review-01.json)。该说明不改变新画布关键 SQL 及私有 DML 的会话归属检查。

## 阶段状态与限制

实施请求路线为 GPT-6 Luna/xhigh，独立只读审查请求路线为 GPT-6.1 Sol/xhigh；实际运行模型及推理元数据未独立核验。指定 GPT-5.6 Sol/xhigh Grillme 外审仍未执行，不能由内部审查或根验收替代。

本文编写/文档冻结时的阶段快照中，代码回归和 loopback 已有上述通过收据；本批文档复核及文档版本后的最终 scope/CLI 保全检查尚待执行。当前任务勾选见本目录 `tasks.md`；最终状态以该文件和后续根验收收据为准。本批不代表真实 PostgreSQL 并发、完整前端/浏览器验收、生产部署或整体目标完成。
