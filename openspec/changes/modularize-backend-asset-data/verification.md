# 第27批验证记录

记录日期：2026-10-09。本文区分固定来源/源码复核、根全量后端测试、本机 HTTP/SQL loopback 和真实响应体 parser 消费；它们不等同于真实 PostgreSQL、完整前端、浏览器、生产或 Grillme 验收。

## 固定来源与代码

- 固定旧来源检查通过：47 个固定 Git 源及四项基准输入均匹配。来源提交为 `23403806898550a7668a6ee7c0c457315655c39b`，检查没有读取旧 checkout 的现场文件正文。[根固定来源守卫收据](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-asset-data-root-fixed-source-guard-01.json)
- 27 个产品代码/测试路径绑定于 [代码冻结收据](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-asset-data-code-freeze-04.json>)；Sol 的源码与测试只读复核已接受。[源码复核收据](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-asset-data-source-review-04.json>)
- 根实际完整后端回归为 308 项通过，命令退出码 0，记录 9 条警告；该测试不包含 HTTP loopback 或前端运行。[根全量测试收据](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-asset-data-root-full-backend-03.json>)

## Loopback 与 TypeScript 响应解析

根实际运行的临时 SQLite/loopback 验收覆盖 18 组、62 条 TCP 请求和 467 个 SQL 事件。十五个新增方法均有实际成功请求；另有四个既有个人制作/粗剪 GET/PUT 代表 smoke。统一工厂登记 42 个方法，但本轮没有通过 TCP 调用全部 42 个。[根 loopback 收据](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-asset-data-root-loopback-02.json>)

Loopback 生成 30 个实际 HTTP 响应体；根用 Node.js 20.20.2 与 TypeScript 6.0.3 调用冻结的四个资产 parser，共 30 次，全部通过。冻结 parser 源文件 SHA 为 `0a40c25ac6117dee4bd16a81d580885cdee3d6f01593f35e7c0e9c7e60391836`。[TypeScript 消费收据](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-asset-data-root-typescript-consumer-01.json>)；[root HTTP/TS 阶段收据](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-asset-data-root-http-typescript-acceptance-01.json>)。

Loopback 使用隔离 SQLite、合成数据和本机 listener。请求结束后 driver、Engine、Session、监听器、服务线程与临时目录均按收据清理；进程完成记录见[进程收据](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-asset-data-root-loopback-process-02.json>)。这不是生产 PostgreSQL 运行或并发验收，也不证明生产 schema/migration 已完成。

## 保留的失败历史与限制

首轮 loopback 收据 `root-loopback-01` 在 12 组、49 条请求后失败，唯一失败为 L13 完整快照时间戳比较：表快照将 datetime 转成 ISO 字符串，而期望值仍是 datetime。失败收据显示本轮自有资源均已清理；随后只修正期望值序列化，第二轮 loopback 通过。该失败与产品代码缺陷无关。[首轮失败收据](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-asset-data-root-loopback-01.json>)

本轮没有运行完整前端套件、Vite build、浏览器验收、真实 PostgreSQL、生产数据库或部署。实现与审查请求路线分别为 GPT-6 Luna/xhigh 与 GPT-6.1 Sol/xhigh；实际运行时模型元数据未独立核验。指定的 GPT-5.6 Sol/xhigh Grillme 外审未执行，不以内部复核或根验收代替。

## 任务快照

本文档作者写入并勾选 3.1、4.1、4.2 后，任务清单为 9/13。4.3（文档/任务/资源保全）、5.1（文档复核）和 5.2（当前版本最终守卫）仍待执行；6.1 的 Grillme 独立外审保持未勾选。文档 strict/status/apply、文本与最终保全检查尚未在本次文档写入后运行，须以之后的根收据为准。整体迁移仍在进行中。
