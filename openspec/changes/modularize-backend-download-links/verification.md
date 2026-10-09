# 验证记录：后端下载链接模块

## 范围与阶段快照

本文记录本次模块实现和根验收的实际结果，不代表部署或整体后端迁移完成。本文编写时任务为 9/13：代码只读审查、固定来源/完整后端回归及隔离 loopback 对应任务已勾选；文件保全收尾、Sol 文档复核、最终 strict/status/apply 与文本守卫、指定外部评审仍按 tasks.md 状态跟踪。最终进度以任务文件和后续实际验收收据为准。

请求路由为实施 GPT-6 Luna/xhigh、源码审查 GPT-6.1 Sol/xhigh；实际运行模型与推理元数据未独立核验。指定 GPT-5.6 Sol/xhigh Grillme 离线外部评审尚未执行，内部源码审查和根验收不替代该项。

## 实际验证

- 源码只读审查接受：[source-review-01.json](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-download-links-source-review-01.json)。根固定来源守卫重新核对 49 项固定 Git 源：[root-fixed-source-and-scope-guard-01.json](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-download-links-root-fixed-source-and-scope-guard-01.json)。
- 根完整后端回归实际退出码为 0，401 项通过、20 条警告；日志与汇总见 [root-full-backend-01.log](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-download-links-root-full-backend-01.log) 和 [root-full-backend-01.json](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-download-links-root-full-backend-01.json)。
- 根受控 loopback 实际完成 34 个 TCP 请求、34 个 ASGI 请求和 34 个验收 case，driver 与 server 顺序一致；完整响应字节核验包含两份各 500 项的 HTTP 200 成功响应体，以及一次通用 HTTP 500 错误响应体。root receipt 为 [root-loopback-02.json](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-download-links-root-loopback-02.json)，运行日志为 [root-loopback-02.log](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-download-links-root-loopback-02.log)。默认 resolver 调用 504 次；自定义 resolver 启动 506 次、完成 505 次，最大并发为 1。32 个认证 Session 全部关闭，30 次连接 checkout/checkin 相等；业务 Session 和 series policy 调用均为 0。根子进程实际 wait/reap 退出码为 0，三个端口均在回收后拒绝连接且可重新绑定，自有临时目录已不存在。
- 根验收证明的是隔离 SQLite、真实认证接线与 loopback 行为，不是 PostgreSQL 并发、浏览器或生产运行。完整回归和 loopback 不验证真实 HMAC、R2/OSS provider、下载流、部署或完整前端验收。

## 失败历史

helper03 首轮实际完成 31 个请求和 31 个 case；D05-32 在请求到达 ASGI 前因 HTTPX ReadError 中断，根根据本地 Uvicorn h11 行为确认响应完成后重抛会关闭 transport。helper04 随后仅给请求增加 Connection: close，使每个请求使用独立连接；产品代码未因此变更，后续根 run02 才完成全部 34 项。

首轮恢复收据还记录了 root wrapper 的 KeyError，以及当时未持久化的子进程 PID/退出码空值；这些历史不被改写成成功结果。原失败日志、运行收据和恢复记录保留在 [root-loopback-01.log](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-download-links-root-loopback-01.log)、[首轮运行收据](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-download-links-loopback-run-20261009T022444859985Z.json) 与 [root-loopback-failed-recovery-01.json](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-download-links-root-loopback-failed-recovery-01.json)。

## 实现边界

默认 identity resolver 原样返回 URL，不做签名。旧默认 helper 的环境/存储配置读取和临时目录探测已从默认路径移除，因此相关偶发异常面更窄；不能据此声称旧 helper 的所有副作用或错误路径完全等价。真实签名 resolver、存储 provider、浏览器直连、生产数据库与部署仍属于后续范围。当前文档完成后仍须由 Sol 复核文档并由根完成最终文件守卫；本记录不预报这些后续结果。


## 本地收尾阶段快照

本文前文的“任务 9/13”是 documentation-freeze-02 时记录的历史快照。此后 Sol 已接受七份文档的只读复核，未发现 P1、P2 或必要缺口；根在该 9/13 文件快照下完成 489 项文件守卫，以及 strict、status、apply 三项 CLI 检查，均以退出码 0 结束。实际收据分别见[文档复核](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-download-links-documentation-review-01.json)和[根检查](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-download-links-root-final-checks-01.json)。

本次仅将 4.3、5.1、5.2 标记完成，当前任务为 12/13；指定的 GPT-5.6 Sol/xhigh Grillme 离线外部评审 6.1 仍未完成。root-final-checks-01 对应的是勾选前的 9/13 快照；勾选后的最终文件快照仍需根另行核验。本段记录已发生的本地复核，不表示整项迁移或外部评审完成。
