# 第 25 批验证记录：后端认证模块化

日期：2026-10-08。范围是隔离的后端 authentication 实现与统一工厂接线。下列测试、HTTP 实验、TypeScript 消费者检查和源码复核分别记录，不代表生产发布或全站迁移完成。

## 结果

| 层次 | 执行者与结果 | 证据 |
| --- | --- | --- |
| 固定源码边界 | root 对固定来源检查为 3/3；exact-five 来源检查命令退出 0。来源为批准的旧 commit，不导入旧应用。 | [source contract receipt](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-authentication-root-source-contracts.json)、[source check receipt](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-authentication-root-source-five.json) |
| 后端自动化 | root 在 Python 3.12 临时环境运行完整后端套件：191 passed，pytest 报告 5.67 秒；runner 单独记录 6.216 秒，含 1 个依赖弃用 warning。 | [root suite log](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-authentication-root-backend-full-final-02.log)、[runner receipt](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-authentication-root-backend-full-final-02.json) |
| 独立 loopback | root 用真实 bcrypt/JWT、文件 SQLite、三个合成用户（alpha、beta、expired；其中 alpha/beta 用于验证私有隔离）和受控邮件 sender 运行 50 个 HTTP 请求；状态为 200×35、201×3、401×4、403×4、404×4，11 个认证方法和 4 个既有业务方法均覆盖。未知 jti 先持久化并提交两次，随后会员拒绝时没有创建业务 Session；受控邮件 3 条验证码、1 条重置消息、SMTP 调用 0。 | [root wire and cleanup receipt](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-authentication-root-wire-and-cleanup-final-01.json) |
| 真实 TypeScript 消费者 | root 将实际 HTTP 响应体传入冻结的 contracts.ts 导出函数；parseUser 解析 5 条、parseLoginResponse 解析 6 条，全部通过。源文件 SHA 与冻结值一致；没有重跑整套前端测试、build 或浏览器。 | [TypeScript parser receipt](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-authentication-root-typescript-consumer-final-01.json)、[runner log](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-authentication-root-typescript-consumer-run-01.log) |
| 独立源码审查 | Sol 只读复核闭合了已记录源码与测试问题；该审查未代跑测试、HTTP、SQL 或 build。 | [final source review](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-authentication-source-review-final.json) |

## 兼容与验收边界

已核验的兼容点包括旧密码校验差异、验证码公开校验与注册消费顺序、会话维护提交顺序、真实 JWT/password_version、改密与重置密码的不同会话影响、三条精确限额，以及四个既有业务方法的真实身份与会员接线。拒绝的业务 GET 在素材与私人行读取前返回；会员拒绝时认证 Session 已完成旧规定的身份维护。root 的 SQL 计数按 captured SELECT 中的表名出现次数解释，正数不是精确查询数量。

所有 SQL 实验使用临时 SQLite，不是生产 schema/migration 或 PostgreSQL 运行并发。没有真实 SMTP、生产密钥/环境、会员支付、Worker、外部 provider、生产数据库、部署或发布。TypeScript parser 试验只覆盖 parseUser/parseLoginResponse 的实际响应，不代表完整前端 tests/build/browser。本轮请求路线为实施 GPT-6 Luna/xhigh、分析审查 GPT-6.1 Sol/xhigh；实际运行模型/推理元数据未独立核验。指定 GPT-5.6 Sol/xhigh Grillme 尚未执行，仍为独立待办；内部源码审查或 root 验收不能替代该外部审查。

## 实施中的失败记录

实现先于第一轮完整套件落盘，本轮不是全程 test-first。最初契约测试记录 2 failed、1 passed，随后相同目标修正后 3 passed；历史日志保留在 [initial RED](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-authentication-initial-contract-red.log) 和 [contract green](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-authentication-contract-green.log)。

一次测试文件收集因缺少 pytest 导入而出现 NameError，后续已修复。ASGI 集成阶段的 500 由测试夹具把多个会话分配为同一个 generated-id 引起；SQLite 正确拒绝唯一键冲突，夹具随后改用确定性的递增 ID。该问题不是产品自动重试缺陷，历史失败见 [stage 12](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-authentication-asgi-stage-12.log) 与 [fixture diagnosis](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-authentication-asgi-debug-15.log)。另有一个解释器路径启动错误，保留在自动化日志索引中；未把它算作通过。

作者活动、实际日志摘要、工具路径证据和 22 个自有 pytest 临时目录的清理映射见 [authoring correction receipt](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-authentication-implementation-authoring-correction-01.json)。root 后验确认自有服务已退出、loopback 临时目录和作者/根测试临时目录已清理。此记录不证明其他路径或环境不存在缓存。

本地实现、外部审查和全站迁移是不同状态。本变更没有部署；其他旧业务、生产 SMTP/数据库、生产 schema 与真实 PostgreSQL 验收仍在本批范围之外。


## 4.3 本地范围保全收尾

根运行限定范围保全检查，退出码 0，耗时 3.946 秒；25 项检查全部通过且 `errors` 为空，结果见 [scope precheck 03](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-authentication-scope-conservation-final-check-precheck-03.json)。该检查将主窗口中的 905 条原生操作结果（root 复核分区 251 条、Sol/Luna 复核分区 654 条）与独立后续窗口的 275 条结果分别核对；后续窗口到 2026-10-08 08:00:36 UTC 为止，52 条写操作均在该窗口内逐条审查。只读结果、写入目标、调用记录和资源清理均按收据中的有限窗口及绑定关系验证。

这项结果不改写较早的严格保全收据：[strict precheck 03](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-authentication-root-conservation-scoped-precheck-03.json) 仍保留 `legacyStatusExact` 与 `legacyTrackedAndIndexDiffEmpty` 两项 false；[fresh precheck 04](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-authentication-root-conservation-scoped-precheck-04.json) 记录 HEAD 与 status-baseline 分类为 false。固定来源仍取自 commit `23403806898550a7668a6ee7c0c457315655c39b`；现场记录的 HEAD 为 `8c4920729ecf73e46ce22ce43c1ea11acdfecd8c`，status 原文及 tracked/index 结果见 scope precheck 03。该方法核验的是已审查的窗口和目标，不代表旧工作区全局未变化，也不判断未审查变更由谁产生。更早的 [scope precheck 01](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-authentication-scope-conservation-final-check-precheck-01.json) 与 [scope precheck 02](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-authentication-scope-conservation-final-check-precheck-02.json) 作为失败历史保留。窗口结束后的 EVID 作者记录、检查器和文档哈希映射不属于上述已审查操作窗口。

本次只关闭本地任务 4.3。更新后任务快照为 11/13；5.2 仍待根对最终文档执行 strict/status/apply、文本与保全核验，6.1 指定的独立 Grillme 审查仍未执行。该复选框不表示整个迁移目标或生产认证已经完成。


## 5.2 本地文档与保全收尾（2026-10-08）

上面的 4.3 末段记录的是当时 11/13 的进度快照。此后，本地 5.2 收尾已勾选；当前任务快照为 12/13，指定的独立 Grillme 审查 6.1 仍未执行。下面分别记录限定范围审计、完整保全和文本检查的实际结果，不能将它们合并解释为旧工作区完全未变化。

根在本次 verification、tasks 与文档哈希映射更新之前执行的限定范围保全检查退出码为 0，耗时 3.915 秒，25 项检查全部为 true，`errors` 为空，见 [scope closeout stage 02](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-authentication-scope-conservation-final-check-closeout-stage02.json)。该检查按已审查的有限窗口、调用与写入目标分区、固定来源和冻结文件映射核对；它保留并引用原严格检查中的失败项，不表示完整保全检查通过。

同样在本次文档与复选框更新之前取得的完整保全快照 [root conservation stage 02](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-authentication-root-conservation-closeout-stage02.json) 的 `passed` 仍为 false：`legacyHeadExact`、`legacyStatusExact`、`legacyTrackedAndIndexDiffEmpty` 三项为 false，其余 12 项为 true。该快照记录现场 HEAD `8c4920729ecf73e46ce22ce43c1ea11acdfecd8c`，固定源码仍来自 `23403806898550a7668a6ee7c0c457315655c39b`；tracked 状态包含 `admin-panel/package-lock.json` 的变更。这里仅保留检查结果，不恢复或归因工作区差异，也不读取旧工作区未跟踪文件内容。较早的严格收据 [precheck 03](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-authentication-root-conservation-scoped-precheck-03.json) 与 fresh metadata 收据 [precheck 04](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-authentication-root-conservation-scoped-precheck-04.json) 继续保留各自快照中的失败分类。

文档写入前，根运行的文本检查退出码为 0，355 个文件已检查、13 个历史文件按规则排除、问题数为 0；结果见 [text receipt](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-authentication-root-text-closeout-stage02.json) 和 [runner receipt](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-authentication-root-text-closeout-stage02-run.json)。该文本检查早于本次 verification、tasks 和文档哈希映射更新，因此不作为这些最终字节的检查结果。

作为阶段历史，[stage 01 scope check](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-authentication-scope-conservation-final-check-closeout-stage01.json) 曾因当时要求当前 legacy 元数据与先前快照分类相等而退出 1；对应 [runner record](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-authentication-root-scope-closeout-stage01-run.json) 与 [log](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-authentication-root-scope-closeout-stage01-run.log) 保留该失败。阶段 01 的输出名冲突及 `FileExistsError` 记录也仍是失败历史，没有计作通过。阶段 01 的 OpenSpec strict、status 和 apply 命令各自退出 0；当时 apply readiness 为 11/13，早于本次复选框更新，见 [strict](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-authentication-root-strict-closeout-stage01.json)、[status](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-authentication-root-status-closeout-stage01.json) 和 [apply](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-authentication-root-apply-closeout-stage01.json)。根将在本次文档和复选框版本冻结后另行执行最终 guards；这些后续检查在此处不预报结果。
