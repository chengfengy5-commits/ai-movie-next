# Design

## Context

现有统一工厂通过认证运行时构造共享的可信身份解析器；配置认证运行时的路径会在返回 actor 前核验会员资格。其他业务路由在此之外按需创建自己的业务 UoW。下载 URL 路由不需要业务 Session 或剧集访问策略，因此应复用 actor 解析边界而不依赖这些业务接线。

固定来源分析确认，GET 下载代理当前只返回固定 403，不传输文件；POST 接收批量项目并按输入顺序处理。旧默认 URL helper 在返回原字符串前还会读取存储配置并检查临时目录；这不等于实现了 HMAC 签名。该分析属于固定来源静态证据，不代表本变更已运行旧服务。具体行为见 capability spec 与 proposal。

## Goals / Non-Goals

**Goals:**

- 以独立下载链接模块承载两个既有 HTTP 方法及其请求、响应和错误边界。
- 把 URL 变换定义为显式异步 resolver 端口，并以 identity 实现作为默认值。
- 让路由复用应用提供的可信 actor resolver，但不依赖业务 Session factory、系列访问策略或业务 UoW。
- 保持默认导入和工厂构造不读取存储配置、不探测或创建临时文件，也不连接对象存储。

**Non-Goals:**

- 实现真实 HMAC 签名、下载代理、文件读取、对象存储 SDK 或 R2 provider。
- 新增 URL 格式、安全、域名白名单、trim、去重、缓存、并发处理或自动重试规则。
- 改变认证或会员规则、共享身份类型、业务数据访问策略或上传链路。

## Decisions

1. **将身份解析与 URL 解析分为两个端口。** 路由使用统一工厂提供的可信 actor resolver。若其来自 AuthenticationRuntime，则继续采用认证应用已有的活动会员检查；未接线时失败关闭。URL resolver 独立负责将输入 URL 和 filename 转成结果 URL。它不提供用户身份，也不能替代会员检查。

2. **默认使用无副作用 identity resolver。** 当调用方未显式提供 URL resolver 时，应用工厂注入 identity 实现，原样返回输入 URL。这样保留旧 helper 在已证明正常及其专属配置错误降级路径中的通常返回值，同时明确不保留它的环境配置读取和临时目录探测。显式 resolver 由组合根传入；其 provider、密钥及副作用不属于本变更。

3. **在应用服务中逐项 await。** 空批次直接产生空响应。非空批次先执行 500 项上限检查，再按原列表顺序逐项调用 resolver 并收集响应。每项 filename 使用请求值，signed 仅比较返回 URL 与该项输入 URL。失败时向 HTTP 层传播，丢弃尚未返回的局部列表；不 gather、不缓存、不重试。

4. **保留普通字符串 DTO，不引入 URL parser 校验。** 请求和响应 schema 使用普通字符串字段，filename 沿用两个路由各自的默认值；Pydantic 保持默认未知字段忽略。GET 缺少必需 url 和 POST body 字段类型问题留在标准请求验证边界，501 项限制由应用服务映射到固定 400。

5. **只在应用工厂增加路由组合。** 新模块提供 router builder 与 mount 函数；create_app 将当前有效 actor resolver 和可选 URL resolver传入。下载路由不要求 effective_session_factory 或 series_access_policy。未配置认证 actor resolver时路由返回服务不可用，不能静默放行。认证 resolver 自身维护会话时可能产生认证 SQL；不得把“不创建业务 UoW”表述成整个请求没有 SQL。

6. **固定停用代理错误，不执行文件操作。** GET 在身份解析通过且查询验证完成后返回旧固定 403 详情，不打开流、不读取对象存储。resolver 失败沿通用 HTTP 500 处理；不返回部分结果。

7. **按模块边界组织代码和测试。** domain/application/ports 不依赖 FastAPI 或 SQLAlchemy；schemas 负责 DTO；http 负责路由、身份依赖和错误到 HTTP 的映射；app.py 负责显式组合。新增领域、应用、HTTP、边界四个测试文件。旧代码修改限于 app.py 与授权的八个既有测试文件；测试改动只把两个下载方法加入路由登记/断言，使原 54 项清单成为 56 项，并保留全部其他业务与副作用断言。

## Risks / Trade-offs

- **默认结果不是实际签名链接** → 默认 identity 路径会原样返回 URL，signed 为 false；需要真实签名的调用方必须显式注入实现，本变更不得以接口名称暗示已经签名。
- **默认错误面比旧 helper 窄** → 移除存储配置与临时目录探测后，相关文件系统或配置异常不再由默认 resolver 触发；将此视为明确的架构差异，而不是声称完全复刻旧 helper 的所有异常路径。
- **身份接线错误可能意外开放请求** → actor 缺失或认证未配置时失败关闭，并通过 unwired 工厂与活动、过期会员路径验证。
- **顺序处理限制单次批次并发度** → 这是保留逐项 await 和失败中断语义的有意取舍；不得以并发优化改变结果顺序或部分失败行为。

## Migration Plan

本变更不创建或迁移数据表。实现阶段将新 router 显式挂入应用工厂，并在后续根验收中验证真实身份、会员拒绝和隔离 HTTP 行为。若未来需要真实 URL 签发，应由另一个经审查的 resolver 适配器显式接入。回退仅需撤销本模块的工厂接线和路由模块，不涉及数据回滚。

