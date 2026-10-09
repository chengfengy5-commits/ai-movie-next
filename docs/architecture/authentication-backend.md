# 认证后端模块

## 范围

本批在隔离的 haoai_backend 包中实现认证与邮箱验证码，并把 JWT actor 接到统一工厂已有业务方法。模块支持 11 个认证方法，与粗剪和个人制作记录各两个 GET/PUT 合计 15 个业务方法。它不代表旧站点已切换到新认证，也不包含 SMTP 账户轮发、生产配置、会员购买、Worker 或其他旧业务迁移。

## 分层与接线

authentication 包将纯领域规则、应用用例、端口、最小 SQLAlchemy Core 持久化、密码/JWT 适配器和 HTTP DTO 分开。create_app 只在调用方提供明确配置时接入认证能力；模块导入和默认工厂不读取环境、不读取 .env、不创建 Engine/schema/Session，也不初始化外部供应商。缺少当前请求必要配置时返回 503，并在创建 Session 前拒绝。

调用方显式提供的 resolve_actor 保留优先级。没有它时，完整 AuthenticationRuntime 可用真实签名 JWT 查询当前用户及会话，再按当前数据库成员状态判断四个业务方法的会员资格。认证与业务使用独立 Session：未知 jti 的历史会话维护按旧顺序提交，随后才检查会员；若会员拒绝，业务 Session、素材和本人私有记录不会因此被访问。

## 方法与兼容行为

认证路由包括注册、登录、本人资料、本人会话列表/撤销、修改密码、忘记/重置密码、本人积分、发送验证码和验证验证码。既有粗剪与个人制作记录 GET/PUT 保持自己的应用与持久化边界。API 错误和字段验证沿旧认证格式处理，不改既有四业务方法的标准 422。

注册和重置密码保留 8 个 Unicode 字符并含字母与数字的要求；修改密码保留 6 字符下限。注册的非空 email_code 在用户名/邮箱重复检查前按原邮箱消费；空码跳过。公开验证码校验只置 verified，不消费；注册消费则先移除记录。密码修改更新 password_updated_at 并撤销本人所有会话；重置只更新密码哈希，保留旧 password_updated_at、会话及可重复使用的重置 token 行为。旧 users 表没有 is_deleted 列，新查询不假设该字段。

三个固定窗口限额为注册 5/hour、登录 10/minute、忘记密码 3/hour，依据客户端地址与路径，不用 X-Forwarded-For 绕过；其他认证路由没有全局 60/minute 限制。认证校验 422 不计端点配额。发送验证码、进程内验证码存储和 limiter 均由显式端口提供；本地 sender spy 不是 SMTP 投递。

## 持久化与限制

SQLAlchemy Core adapter 使用最小投影，不负责创建生产 Engine 或 schema。认证用例拥有认证 Session 的读写边界，既有业务方法继续使用独立业务 Session；因此认证维护与后续业务拒绝并非一个跨两类 Session 的原子事务。每个方法按固定旧顺序执行，未知 SQL 错误向上传播，不自动重试。SQLite 唯一约束和事务测试不等同于 PostgreSQL 并发或生产隔离验证。

完整方法表、旧源码锚点、运行限制和实际验收分层见 [模块 API 兼容说明](module-api-compatibility.md)、[本变更 verification](../../openspec/changes/modularize-backend-authentication/verification.md) 与冻结旧源码 commit 23403806898550a7668a6ee7c0c457315655c39b 的复核记录。
