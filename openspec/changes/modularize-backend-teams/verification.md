# 团队后端验证记录

本文区分实现、静态审查、后端测试与 Root loopback 结果。任务状态以本次文档写入后的 tasks.md 为准；团队批次完成不代表整个 HaoAI 后端、React/TypeScript 前端和末尾语言评估已完成。

## 范围与基线

团队后端新增 25 个接口：management 13、series 7、reporting 5。应用登记总量为 82；本轮 TCP/ASGI 验收只针对 25 个团队接口。路由与兼容行为见[接口说明](../../../docs/architecture/teams-backend.md)。

授权基线记录 564 个目标文件 SHA，并固定 52 条历史来源的 Git 对象元数据；52 条记录只证明来源身份核对，不代表完整语义阅读。54 个团队实现文件代码冻结 SHA 为 9572bc916f1e115351d669c364129ad838e045764aa0629e99fbbcf693fd6199，独立最终源码审查 SHA 为 e93135cb16a802dadedaddd7ba8f70469fc86917a251c4303b3a7bf860105275。

## Root 完整后端测试

Root 实际 pytest 等待退出码为 0，观察到 553 passed、0 failed、0 skipped、20 warnings。原外层解析器仅识别 548 个节点，runner 曾返回 1；Root 从原 verbose 日志修正节点解析并补齐另外 5 个节点，没有重跑测试。原日志 SHA 为 6a05b9a1e9eb3ff093172fa82ae3328ebb31dd2efa74732f000fe4b820676916；解析修正收据 SHA 为 e87c5063ec7481fbfe3e3e5c20a2e50f0f705aaaaad975fb735247f1b54d763a。pytest 实际退出码与外层首次退出码分别保留。

## Root loopback 验收

Root 使用已接入应用、显式 JWT 认证与会员数据，对真实 HTTP/ASGI 请求和 SQL 结果执行 loopback 验收。Root 接受收据 SHA 为 4647bafb6e1c5d223043ed49b9bb33c2c9e9d8150e23b17dc2c01af29dc1fae7；helper08 attempt04 主收据 SHA 为 ce0d6badf96c142e3446fd37d3853f4dcf26dddce61d727e9df1b5371d7b3aef。

收据记录 86 个阶段、6,725 项断言和 117 条有序 HTTP/ASGI 请求，全部通过，覆盖 25 个团队方法。验证包括真实认证、团队角色与旧私密权限边界、M11 邀请读取维护写、M13 配置额度后的加入，以及其它 24 个接口无需额度。H18 在同一应用内先 GET /api/auth/me 返回 200，再 POST /api/chapters/chapter-a/replace-asset 返回 503；detail 为“素材替换服务尚未接线”，响应体 43 bytes，SHA-256 为 83d1ce4608d782b6e12475eaa9042749e7bae55bcfb59ca55cbdda7be04ce444。这是素材替换 UoW 未接线，不是 M13 的 JoinQuota 缺失。SQL 观察和 26 表 before/after 快照用于区分响应、实际写入和非目标行守恒。

此次运行记录 86 个唯一 owned 数据库实例，且 86 个实例均有终态记录并全部关闭；这不是 Session 数。Session 计数另为认证 116、业务 105，合计 221。helper PID 已退出，端口 51295 清理后拒绝连接并成功重绑，探测 socket 已关闭，精确临时目录不存在。Root 接受收据核对了目标 564 文件和 54 个代码文件。该结果限于本地 loopback 与资源检查，不延伸为 82 路由全部 TCP、PostgreSQL 或外部服务验收。

## 保留的失败历史

- helper04 attempt01 原生会话 99202 以 exit 1 结束，最终 JSON 序列化遇到 tuple key；主收据没有生成，主聚合结果/最终 AND 未重建。另有独立 partial-stage 收据记录 86 条阶段结果，其中 77 true、9 false；这些阶段证据既不能推成全通过，也不应写成完全未知。
- helper06 attempt02 原生会话 44153 以 exit 1 结束，M01 receipt 序列化遇到 SQLAlchemy quoted_name key；该次运行保留为序列化失败。
- helper07 attempt03 原生会话 35210 以 exit 1 结束，运行时固定时钟使邀请和额度加入场景失败，H18 也因访问不存在的 Dispatcher.current_app 失败；helper 的资源 AND 保持 false。Root 随后仅对已确认为空的精确 owned 临时目录执行 rmdir。该独立清理没有重建或改写 helper 失败 AND。

这些失败没有被覆盖或改标通过。成功验收来自 helper08 attempt04 主收据与 Root 接受记录，不是重新运行 helper04。

## 尚未执行的层级与任务状态

首份文档作者候选曾记录 tasks 为 11/13；这是当时的状态快照。本次修订未改 tasks。当前任务标记以仓库 tasks.md 为准，Root 最终 strict/status/apply 与任务闭合结果以之后的实际收据为准；本段不预判最终门禁状态。外部 Grillme 审查不属于本地 loopback 验收，是否完成应由其独立收据确认。

本轮没有执行 PostgreSQL、provider、SMTP、worker、React/TypeScript 前端、浏览器、部署或归档验证。后续完整前端工作和末尾语言评估仍属于整个 HaoAI 目标。
