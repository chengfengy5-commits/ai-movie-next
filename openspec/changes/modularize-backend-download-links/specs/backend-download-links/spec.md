# Spec Delta

## Purpose

本能力为后端下载 URL 接口定义可验证的兼容边界：停止服务端下载代理，并以显式异步解析器承接有序批量 URL 处理，同时避免默认路径访问对象存储配置或临时文件系统。

## ADDED Requirements

### Requirement: 下载代理 GET 保持停用

使用应用配置的可信身份解析器访问下载代理时，系统 MUST 先完成活动会员身份校验，并且 MUST 不代理或下载响应体。身份有效且查询参数合法时，系统 MUST 返回 HTTP 403 和固定详情“下载代理已停用（服务器不承载下载流量）：请使用 POST /api/sign-download-urls 获取签名直链，由浏览器直连 OSS 下载”。查询参数 url MUST 为必需的普通字符串；filename MUST 为可省略字符串，省略时取 null。身份解析器未接线时，系统 MUST 失败关闭，不得默认放行；URL resolver 未显式提供时则使用规定的 identity 默认实现。

#### Scenario: 活动会员请求已停用的下载代理

- **WHEN** 活动会员以普通字符串 url 和可省略的 filename 请求 GET /api/download
- **THEN** 系统 MUST 返回 HTTP 403 和规定的固定详情，且 MUST 不返回下载内容或发起下载

#### Scenario: 下载代理缺少必需 url

- **WHEN** 请求 GET /api/download 时没有提供 url
- **THEN** 系统 MUST 保持普通必需查询参数的验证错误，不调用 URL resolver

#### Scenario: 身份解析器未接线

- **WHEN** 应用没有接线可信身份解析器
- **THEN** 系统 MUST 以服务不可用错误失败关闭，不得创建业务 Session 或绕过身份校验

### Requirement: 批量 URL 请求保持普通字符串 DTO 语义

POST /api/sign-download-urls MUST 先通过应用配置的活动会员身份校验，再处理请求；其请求 MUST 包含 items 数组；每项 MUST 包含普通字符串 url，filename MUST 默认为空字符串。模型 MUST 忽略未知字段，不得把 url 转成带额外校验的 URL 类型，也不得自动 trim。items 为空时系统 MUST 返回空结果且不调用 resolver。items 超过 500 项时系统 MUST 返回 HTTP 400 和详情“单次最多 500 条”，且不调用 resolver；单项字段类型或必需字段无效时 MUST 返回框架标准 HTTP 422。

#### Scenario: 非活动会员不能提交批量请求

- **WHEN** 免费或已过期会员提交其他字段格式有效的批量请求
- **THEN** 系统 MUST 返回活动会员校验的 HTTP 403，且不得调用 URL resolver

#### Scenario: 接收空批次

- **WHEN** 活动会员提交 items 为空数组
- **THEN** 系统 MUST 返回 items 为空数组，且 resolver 调用次数为零

#### Scenario: 接收边界大小的批次

- **WHEN** 活动会员提交至多 500 个格式有效的项目，项目可包含空字符串、空白、非 HTTP URL 字符串、重复 URL 或未知额外字段
- **THEN** 系统 MUST 保留普通字符串及重复项，不得 trim 或拒绝仅因其不是 HTTP URL 的值，并按 DTO 规则忽略未知字段

#### Scenario: 拒绝超过批次上限

- **WHEN** 活动会员提交 501 个格式有效的项目
- **THEN** 系统 MUST 返回 HTTP 400 和固定上限详情，且不得调用任一项目的 resolver

#### Scenario: 保持标准请求体验证

- **WHEN** POST 请求缺少 items、items 不是有效数组，或某项缺少 url 或提供非字符串字段
- **THEN** 系统 MUST 返回标准 HTTP 422，且不得开始批次处理

### Requirement: 批量解析按输入顺序执行并保留响应来源字段

系统 MUST 对每项恰好调用一次异步 resolver，按输入顺序逐项等待完成；不得并发、缓存、去重或自动重试。每个响应项 MUST 使用 resolver 返回的 url、原请求 filename，以及按 resolver 返回值是否等于输入 url 计算的 signed 布尔值。输出顺序和项目数量 MUST 与输入一致。

#### Scenario: 顺序处理并保留重复项目

- **WHEN** 批次包含重复项目且 resolver 对不同项目返回可区分的结果
- **THEN** 系统 MUST 依输入顺序逐项等待，保留重复项目，并逐项回传 resolver 的 url、原 filename 和正确的 signed 比较结果

#### Scenario: Resolver 改变 URL

- **WHEN** 显式注入的 resolver 为某项目返回与输入不同的字符串
- **THEN** 对应响应项 MUST 使用该返回字符串并将 signed 设为 true；未改变的返回值 MUST 对应 signed false

#### Scenario: Resolver 失败时中断

- **WHEN** 显式注入的 resolver 在第 N 项抛出异常
- **THEN** 系统 MUST 停止调用后续项目，不得重试或返回部分成功响应，并 MUST 以通用服务器错误结束请求

### Requirement: 默认 URL resolver 逐字返回输入且无隐式存储副作用

未显式注入 resolver 时，系统 MUST 使用 identity 行为逐字返回每项输入 url；其 signed 结果 MUST 为 false。默认 resolver MUST 不读取环境变量或 .env，不检查或创建临时目录，不调用对象存储 SDK，也不生成 HMAC 签名或下载文件。需要真正改变 URL 的实现 MUST 通过显式 resolver 注入。

#### Scenario: 默认 resolver 保持任意字符串

- **WHEN** 活动会员提交含空字符串、空白、非 URL 字符串或带查询参数 URL 的有效批次，且未注入 resolver
- **THEN** 系统 MUST 逐字返回输入 url 和原 filename，signed MUST 为 false，且不得触发配置探测或文件系统临时探针

#### Scenario: 默认实现不生成真实签名

- **WHEN** 未注入具有 URL 变换能力的 resolver
- **THEN** 系统 MUST 不得宣称已执行 HMAC 签名，也不得下载、代理或改写资源内容
