# Design

## Context

固定来源为提交 23403806898550a7668a6ee7c0c457315655c39b 的章节画布 GET/PUT。隔离后端已有显式应用工厂、认证端口、系列访问模块及同 Session SQLAlchemy Core 模式。已有 series_data 画布表只是章节/剧集删除使用的薄投影；它不能充当画布完整 owner 表。

## Goals

- 以单独的 canvas_data 模块承接两个既有 HTTP 方法。
- 保持身份、系列访问、异常优先级、DTO、锁、序列化、版本和持久化行为。
- 用可验证的 UoW 边界表达提交前回滚及提交后未知结果。
- 通过真实 SQLite 与 HTTP 专项测试证明行为，不把计划性检查记作已通过。

## Non-Goals

- 不增加其他路由、用户私有画布、canvas 前端、TypeScript parser 或图编辑规则。
- 不增加数据库 revision CAS、行级锁、自动重试或锁策略。
- 不将 PostgreSQL 方言编译当作真实 PostgreSQL 锁并发验收。
- 不迁移旧全站 ORM/schema、部署、外部服务、Worker 或生产身份实现。

## Decisions

### 模块边界与接线

新生产模块路径限定为 backend/src/haoai_backend/canvas_data 下的十个授权文件。HTTP 适配层只声明两个 canvas 路由，应用层依赖端口；SQLAlchemy Core 细节集中于 persistence/tables。应用复用可信 actor 与公开 series-access 能力，在同一业务 Session 内完成章节读取、访问检查、锁刷新和画布操作。纯 domain 不依赖 FastAPI 或 SQLAlchemy；boundary 测试禁止从新模块导入旧 app。

### 查询和验证顺序

GET 顺序：读章节；不存在则章节 404；读取并执行现有 series-access policy；再查画布。不存在画布时直接生成固定完整默认对象，不插入记录，不提交。

PUT 顺序：读章节并保留缺章 404 优先；同一 Session 执行 series access；尝试刷新本人的已有锁；按既有 json.dumps 规则序列化并按字符数限额；读取当前画布；对非 null version 做快照比较；进行插入或按主键更新。不要新增基于版本的 SQL 条件、互斥锁或隐藏重试。

本人锁只有已存在且 user_id 等于当前 actor 时才更新；过期本人锁仍参与续期。无锁及他人锁保持原行为，不创建锁。锁时长沿用现有配置语义：falsey 值回退 15，负值不额外规范化。失败在提交前发生时，由当前事务回滚锁更新及业务写入。

### DTO、编码和版本

画布是共享章节资源。写请求 document_json 为 dict，version 是可省略/显式 null 的普通 Optional[int]，保留 Pydantic 常规转换及 extra-ignore。用 json.dumps(document_json, ensure_ascii=False) 且保持默认 separators，字符数大于 1,000,000 才拒绝。domain 只负责纯转换，不就地修改调用方 payload。

没有当前文档时创建 version 1，忽略输入 expected version。已有行只比较已读版本快照；请求版本非 null 且不匹配则 409，否则写为 (current_version or 1)+1。即使 payload 相同也执行一次成功写入与版本递增。数据库更新仍以画布 id 为主键，不添加 WHERE version CAS；两个并发写入的覆盖语义不在本迁移中强化。

### 持久化与提交后刷新

canvas_data.tables 声明完整画布 owner 投影，包含系列/章节/用户外键、章节唯一约束、版本、JSON 文本、创建者和更新时间字段。fixture/schema 初始化必须先创建完整 owner 表，再建立其他模块的薄投影；生产模块不得用测试建表替代真实迁移。

成功 PUT 只进行一次业务 commit，随后在同一 UoW 真实刷新写入行。返回记录元数据取自刷新后的记录，但 document_json 回显本次输入；后续 GET 再反映数据库当前值。刷新失败或 commit 确认异常不能当作写入未发生，也不能自动重放；调用方必须显式 GET 核实。事务开始前的拒绝或 commit 前异常按现有异常映射并回滚。

### 错误映射

保留现有服务错误类型与顺序。章节缺失先于 series-access；access 拒绝先于画布查询；锁刷新先于大小和版本；大小限制先于版本冲突；版本不匹配使用原 409；超限仍为原 400。未知 SQL/刷新/提交确认错误不转换成成功，也不自动重试。

## Validation Strategy

六个新测试文件分别覆盖纯域、应用顺序、SQL 表与约束、跨连接及故障事务、HTTP 接线、导入/边界。必须包含真实 SQLite 两个物理连接交错、真实行更新为零、唯一约束竞争、提交前回滚、commit 已成功但确认/刷新失败后的独立显式读取。快照需包含非空画布及用户私有记录、源、任务、账单和队列历史，验证非目标数据守恒。之后由根执行完整回归与真实 loopback；专项单测或 ASGI 测试不替代该验收。真实 PostgreSQL 并发、浏览器和指定外部 Grillme 评审另行分层，不由本规划声称完成。

## Authorized Implementation Paths

未来实现冻结包含 24 个路径：新生产文件 10 个、新测试文件 6 个，以及 app 工厂和七个既有工厂/边界测试路径。新增的既有测试路径 `backend/tests/test_chat_data_http.py` 仅因根实际全量回归发现其 52 个方法总数断言落后于现有 54 个注册方法，允许补充画布 GET/PUT 登记断言；原十个聊天方法和其他业务断言保持不变。精确列表以 root-planning-authorization-01.json 及范围修订授权的 allowed scope 为准；不应扩展或改写其他受保护模块。
