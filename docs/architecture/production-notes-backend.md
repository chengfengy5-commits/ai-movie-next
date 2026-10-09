# 个人制作记录后端模块

本说明记录第24批在隔离 `backend/` 包中迁移的完整个人制作记录业务。它与既有粗剪模块共用根应用工厂、可信 actor 和业务错误基类，但记录模块有独立领域、应用、端口、持久化和 HTTP adapter；实现没有让 notes 模块依赖粗剪的内部表模块。

## HTTP 与工厂

- `GET /api/chapters/{chapter_id}/personal-production-notes` 读取当前用户在该章节的媒体快照、个人便签与续作位置。
- `PUT /api/chapters/{chapter_id}/personal-production-notes` 使用 expected revision 提交一个或多个镜头的状态/备注，以及可选的续作位置补丁；显式 `null` 用于清除续作。
- 新根工厂统一组合粗剪 GET/PUT 与个人制作记录 GET/PUT。缺少 Session factory、可信 actor 解析器或同 Session 的访问策略时返回 503，不创建 Session，也不默认放行。

HTTP adapter 使用严格输入模型，保留旧字段、错误状态和错误优先级。请求最大500条镜头，GET 不把超过500条的合法来源截断；备注上限2000字符；不把前端逐镜头请求形式变成服务端限制。

## 身份、媒体与局部补丁

镜头身份来自章节原始 `storyboard[0]` ID 与同章素材；不以名称、数组位置、`frame_index` 或 secondary ID 回退，也不裁剪合法的原始字符串。媒体摘要只按旧规则去除临时签名参数与 fragment，保留未知查询参数的原始编码和顺序。各章节可以含缺失、重复、歧义或不可读镜头；有效镜头仍按完整顺序投影。

只提交的字段才更新。状态-only 保留原备注；note-only 按旧规则继承认可并绑定当前媒体 revision；不提供续作字段不清除续作，显式 null 才清除。未知 note-entry JSON 字段保留。本人记录以用户/章节隔离，R0首次行创建使用唯一约束，后续更新使用私人 revision CAS。

## GET 维护与事务

GET 可能在读取路径协调 `storyboard_media_states`：初始化媒体 revision、检测当前来源变化、递增 revision、保留删除墓碑，并撤销同章所有用户受影响的旧认可。此维护与私人记录修改在同一请求事务中按旧边界执行，因此 GET 不是纯数据库读取。

若 GET 发现本人历史认可在原图和 preview 双空时仍标为 approved，会在当前 GET 请求事务中提交维护并返回修复后的 revision 快照。只有当本次 PUT 自身发现这类双空历史认可仍需维护时，才先独立提交维护，再以 409 结束且不执行该次客户端补丁，即使客户端猜中维护后的 revision；此前 GET 已完成维护后，携新 revision 发起的正常 PUT 不会因此无条件返回 409。成功写入的响应由当前已读 snapshot 和补丁构造，不依赖提交后的第二次读取。

应用用例创建并关闭 UoW、决定提交或回滚；SQLAlchemy UoW 管理 request-scoped Session。media coordinator 不创建或关闭 Session，也不自行 commit 或 rollback。未来素材写方可以在自身事务内复用协调入口。旧站点 ORM hooks、其他媒体写入方和其事务并没有因此自动接入。

## 持久化边界

生产 adapter 使用 SQLAlchemy Core 与 request-scoped Session。当前模块的 Core metadata 是实现所需的最小查询投影与私有表定义，不是完整生产 schema，也没有创建 Engine、生产建表或 migration。真实生产部署仍需要由既有系统正式数据库接线和迁移流程承担。

当前证据包括 SQLite 持久化/唯一竞争/CAS/回滚测试、ASGI synthetic identity 测试及根实际完成的 loopback 临时 SQLite 复验。初次 loopback 收据因端口释放失败而未通过；静态复核后的第二次 42 请求复验通过并确认端口与临时数据清理，两个结果均保留于 [verification](../../openspec/changes/modularize-backend-production-notes/verification.md)。SQLite 和 PG 方言编译不证明真实 PostgreSQL 行锁、并发撤销、真实认证或生产事务行为。

真实 JWT、密码版本、session 撤销、会员与完整团队管理、其他业务模块、旧 ORM hooks、Worker、队列、媒体 provider、生产 schema/migration、部署与外审都不属于本批已验收范围。
