# Tasks

## 1. Planning and Source Baseline

- [x] 1.1 根审核五份规划文件，核对固定来源与精确的 27 路径白名单，然后冻结规划并授权实施。规划就绪本身不构成实施验收。

## 2. Asset Data Module Implementation

- [x] 2.1 实现纯领域规则、DTO/展示、命名和引用逻辑；保留 A1/A2 中字段省略、null、别名、响应、规范化及 no-op 时间戳行为。
- [x] 2.2 实现角色、场景和道具的同一 Session Core CRUD、可信访问及原有 404/403 优先级；用真实隔离 SQL 和拒绝路径副作用断言覆盖 A3。
- [x] 2.3 实现类别删除对同剧章节引用的清理：先稳定顺序锁定全部受影响章节，在来源 DML 前协调媒体；验证一次提交、整体回滚及 A4 阶段行快照。
- [x] 2.4 实现分镜素材新增、更新、删除及显式媒体写入协调；保留锁续期、按媒体身份撤销跨用户认可、墓碑、原引用和不自动关联行为，覆盖 A5/A6。
- [x] 2.5 通过 HTTP 和统一 factory 接入精确的十五个路由；只更新许可的五个 factory/边界测试，并完成 A7 的真实 SQLite、ASGI 与来源边界测试。

## 3. Independent Source Review

- [x] 3.1 GPT-6.1 Sol/xhigh 对冻结的 27 个代码/测试路径和有意义的用例进行独立只读复核；分别记录请求路线与 runtime 元数据，并关闭有证据支持的 P1/P2 缺口。

## 4. Root Execution and Conservation

- [x] 4.1 根在冻结代码上执行语法、固定来源、source contract、exact-five manifest 和完整后端检查，并核对精确代码清单。
- [x] 4.2 根运行十五方法的本机 loopback 验收，接入现有认证/访问/notes/rough-cut，并将真实 HTTP 响应体交给四个冻结 TypeScript 资产 parser；不得将此称作完整前端测试或浏览器验收。
- [x] 4.3 根核对目标范围、受保护历史和固定来源字节、旧文档前缀、任务正文、本人资源/进程/端口/临时目录清理及本地收据；不把旧 checkout 的现场元数据当作必须相等的冻结值。

## 5. Documentation and Local Closeout

- [x] 5.1 实现证据通过后，追加四份既有文档并新增两份文档；Sol 对最终文档内容、链接和边界进行独立只读复核。
- [x] 5.2 根对当前文档和任务状态执行最终 strict/status/apply、文本与保全检查；仅依据实际收据关闭本地任务。

## 6. External Review

- [ ] 6.1 仅在指定的 GPT-5.6 Sol/xhigh Grillme 离线复核明确可用时执行独立外审。否则不得探测、配对、替代或标记完成，也不得用内部复核或根验收代替。
