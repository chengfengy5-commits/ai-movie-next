# 剧集访问策略

## 范围

第二十三批新增 haoai_backend.series_access，并把该策略显式接入现有个人粗剪 GET/PUT。它是隔离后端中的一项业务能力，不是全站认证入口，也不迁移真实登录、JWT、会话撤销、会员校验、团队管理或其他业务域。原有 142 个代码/配置路径保持冻结。

策略的公开桥接 require_rough_cut_series_access(session, actor, series_id) 组合纯领域规则、应用服务和 SQLAlchemy Core 读取仓储，并把本领域 404/403 映射到粗剪现有错误类型及中文 detail。其余数据库异常原样传播，不转换为允许访问。

## 判定顺序

1. 应用先按 ID 查询剧集。缺失时返回 404 和“剧集不存在”，不查询成员或认领人。
2. 对有团队且由他人认领的剧集，先查询当前 actor 的团队成员记录。只有 role 精确为 owner，或有效权限列表包含 enter_claimed_series，才能通过认领关卡。普通成员若缺少该权限会收到带当前认领用户名的 403：“该剧集已由「{name}」负责制作，暂不可进入”；认领用户记录不存在时 name 为空字符串。
3. 通过认领关卡后，最终资格仍要求 actor 是剧集作者或属于当前剧集团队。认领者本人不自动获得资格。作者身份可通过最终资格，不需要 team owner_id 或 admin 特判；非作者必须有当前成员关系。若流程先前读取过成员记录，最终成员资格仍按合同重新读取，不复用前一阶段的结论。
4. 对其他拒绝情况返回 403 和“无权访问该剧集”。成员、权限及用户名不跨请求缓存，下一请求读取当前数据库值。

权限解析保留旧的已知权限键过滤和列表语义。非列表、非法 JSON，或列表中任一不可 hash 元素都会使整个有效权限集合为空；只接受精确字符串 enter_claimed_series，不修剪、不按名称猜测，也不额外限制 admin 角色。

## 与粗剪读取链路的关系

既有粗剪流程先查询章节；该 SQL 查询已经读取章节 content。随后策略在解析章节内容、查询源镜头素材及读取本人草稿之前执行。因而不得把拒绝保证描述成零 chapter SELECT：它保证未授权请求不会继续解析来源，也不会访问源素材或本人草稿。

拒绝 GET 的 ASGI SQL 记录覆盖后续素材和私人草稿查询为零。拒绝 PUT 的 root loopback 实验比较完整既有私人行并确认没有新增草稿；它证明未授权写入没有改变这些记录，不声称取得了 PUT 的 SQL SELECT 轨迹。原始查询次序和这一区分记录在[契约澄清收据](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-series-access-contract-clarification.json)。

## 分层与生命周期

domain/errors 仅依赖 Python 标准库；ports/application 不依赖 FastAPI 或 SQLAlchemy；persistence/tables 通过传入的同一个 SQLAlchemy Session 读取最小投影；rough_cut_policy 是唯一把策略映射到粗剪错误的适配边界。策略不创建、提交、回滚、关闭 Session，不 flush、不加写锁、不写业务记录、不缓存跨请求的决定。

新 app factory 显式接收 session_factory、resolve_actor 与 series_access_policy。任一必需端口缺失时返回 503，且在响应前不创建 Session，也没有默认允许分支。服务范围仍仅为 GET 与 PUT /api/chapters/{chapter_id}/rough-cut，不增加 endpoint 或响应字段。

三张最小 SQLAlchemy Core 表是生产读取 adapter 使用的最小查询投影；仅测试与隔离 lab 创建相应临时表，不代表完整生产 schema 或 migration。完整后端回归中的 PostgreSQL FOR UPDATE 方言编译验证属于既有粗剪 PUT 的章节写锁；本访问策略不加锁，也未执行真实 PG 锁或并发撤销测试。

## 证据与未覆盖范围

本地后端测试使用固定 Python 3.12 运行时、临时 SQLite 与显式合成身份；实际 series/team/claim SQL 策略参与请求，但身份不是生产 JWT。根 loopback 另以两个进程及临时 SQLite 检查当前权限、成员撤销、重启持久性和拒绝 PUT 后完整私人行不变。真实 JWT、password version、session revocation、active membership 接线、PostgreSQL 运行、Worker、队列、provider、R2 和生产部署仍未验收。

测试、来源契约、OpenSpec 与 loopback 的分层记录见[本变更 verification](../../openspec/changes/modularize-backend-series-access/verification.md)；实现与边界见[代码库模块说明](../../docs/architecture/backend-module-boundaries.md)。
