# Design

## Context

动机见 proposal；可观察契约见 `specs/backend-authentication/spec.md`。新后端已有个人粗剪、制作记录与剧集访问策略，统一工厂当前注册四个方法。认证迁移需要完整 11 个认证方法和真实 JWT 身份接线，统一工厂最终注册 15 个方法。原生产应用不导入或启动。

来源仅固定 commit `23403806898550a7668a6ee7c0c457315655c39b`。完整源分析与原九方法推荐保留在证据 `haoai-next-backend-authentication-source-analysis.json`；两辅助方法并入本批的复核另存 `source-scope-expansion.json`，不重写历史推荐。六条旧修改路径的补充依据见 `source-analysis-followup.json`。根 `source-frozen.json` 分别记录 42 份固定 Git 来源、4 份已安装 SlowAPI 库源码及 9 份目标源码，不将安装目录的文件冒称 Git 来源。

关键锚点：`routes/auth.py:29–174` 令牌、身份与会员，`:177–375` 注册、登录、会话和改密，`:378–495` 重置令牌、忘记/重置密码与积分；`routes/email_verify.py:35–153` 发送、公开校验与注册消费；`schemas/auth.py:1–88` 字段和密码；`models/user.py`、`user_session.py`、`system.py:60–70` 实际列；`middleware/rate_limit.py` 和固定环境 SlowAPI 的 wrapper/错误处理明确三限额覆盖。旧 `main.py:139–160` 无全局 SlowAPI 中间件、采用认证字符串 422；其他模块的历史说明不得改写为本批重跑。

## Goals / Non-Goals

**Goals:**

- 完整迁移认证和邮箱验证码业务，以真实密码/JWT、临时 SQL 和受控供应商证明兼容；四个个人业务方法实际使用 JWT、会员及已迁移 SQL 访问策略。
- 明确配置、事务所有权和模块边界；保持默认工厂无 I/O、缺配置拒绝、旧公开注入方式兼容。
- 在专用测试和根独立验收中证明兼容顺序、失败及提交不确定结果，保护 169 条其余代码和 147 个历史产物。

**Non-Goals:**

- 不迁移 SMTP 账号配置、密钥解密、轮发/日计数、真实邮件投递、部署配置管理、会员购买与计费、其他业务路由、worker/provider 或生产全站启动。
- 不读取 `.env`、生产密钥或旧未跟踪内容，不创建生产 Engine/schema，不做数据库迁移或自动反射，不安装/复制旧虚拟环境。
- 不修改前端、fixture、样本和旧四个业务模块的 HTTP/application/domain；不将未部署设为本批本地完成门槛。指定 Grillme 与其他尚未迁业务仍是整体目标的独立待办。

## Decisions

### 1. 独立认证模块与显式运行时

采用 `authentication` 独立业务包，使用不可变领域数据、明确端口、应用用例、最小 SQL 表投影和 HTTP DTO。领域层不依赖 HTTP/SQL；认证模块依赖既有 shared 身份和业务错误，不反向依赖个人粗剪或制作记录内部代码。避免把旧 497 行路由原样搬入统一工厂，也不复制旧应用全局初始化。

显式认证运行时汇集密码适配器、令牌配置/适配器、验证码存储与生成器、邮件供应商、前端 URL 提供者、限流器、时钟及 ID 工厂。`create_app` 仅接线和注册 15 个方法；默认构造不实例化密码/JWT/限流供应商。配置中的密钥仅由调用方传入，算法和有效期有明确值，访问期沿旧十四天、重置三十分钟；不从环境、文件或数据库自动获取。HTTP 请求所需配置/端口不足时，在 Session 创建前返回 503；不能用测试身份、默认允许或假发送代替配置。

真实 passlib/jose 在显式适配器使用时延迟导入；模块顶层和默认工厂不得触发第三方环境/文件 bootstrap。尤其不能直接构造已安装 SlowAPI Limiter：它会检查 `.env` 与环境配置。保留既有冷子解释器逐包 import/factory 断言，仅 Pydantic 自身精确标志探测按旧边界允许；不能删除环境、Engine、Session、schema、文件或网络哨兵。

### 2. HTTP 接线与认证生命周期

工厂增加可选认证运行时输入，原 `session_factory`、`resolve_actor`、`series_access_policy` 公开调用保持。显式 `resolve_actor` 优先处理四个旧业务方法；只有未提供它且认证运行时、Session 工厂和业务访问策略齐全时，才接通真实会员身份解析器。缺业务策略仍让四方法拒绝，不能借认证配置放行。

认证 HTTP 将本模块错误映射为 FastAPI HTTPException，带原认证 `WWW-Authenticate` 头；它可穿透既有粗剪及制作记录的依赖处理，不需要修改两者 HTTP 或 shared 错误。返回的 actor 是既有 shared `TrustedActor` 同一类。认证路由采用局部验证错误转换，维持第一条字符串 detail；不安装全局 422 handler，不改四方法的标准 422。自动 docs/redoc/openapi 保持关闭。

每个受保护认证用例持有自己的认证 UoW：身份维护与该认证动作使用同一 Session，按旧顺序显式提交；应用负责关闭、未提交事务回滚，仓储不自行关闭。向四个业务方法解析 actor 时，认证用例先在独立 Session 验签、读取、维护提交、会员校验和关闭，再进入原业务 UoW；业务内部的访问策略依然使用该业务同一 Session。旧 get_db 缓存曾在同请求共享 Session，本次跨模块独立 Session 是明确接线差异。已提交 last_seen 等维护不会被后续业务 403/404 或 PUT 回滚撤销，不声称全请求原子授权或共同事务。

### 3. 真实密码/JWT 与旧会话语义

密码适配器采用当前已验证环境的 passlib bcrypt 与 bcrypt 版本；令牌适配器采用真实 python-jose，测试使用临时自造密钥，不读取真实密钥。保留 jose 原有声明验证，不强制旧源没有要求的 exp、jti 或 password_version 存在，也不自行宽容错误声明类型。

password_version 沿原 `str(int((password_updated_at or created_at or utcnow).timestamp()))`；naive timestamp 的宿主时区及整秒语义不悄悄改成新 UTC 算法。缺失版本跳过比较，非 null 必须字符串比较。新访问令牌生成 jti；默认创建函数缺版本时使用原 literal legacy，登录则传入实际版本。已验证未知 jti 先插入历史设备并 commit，再更新 last_seen 并 commit；已有有效 jti 只维护一次；无 jti 不查询会话。会员拒绝发生在维护之后。

顺序登录清退到两个旧会话后创建一个；列表保留非当前在前、时间倒序及 20 条限制。改密六字符规则、全本人会话撤销与版本更新保持；重置仅改哈希，不更新版本、不撤销会话、不消费重置令牌。以上反直觉规则是本批兼容事实，不用安全重构替换。用户表没有 is_deleted 列，SQL 不能虚构列；可由领域默认 false 保留原 getattr 行为。

### 4. 验证码与供应商的独立副作用

使用本认证运行时持有的进程内验证码存储，不在模块导入时创建全局跨工厂缓存；同一已配置运行时在其请求间共享。存储为 email 到 code/expires_at/last_send/verified 的原结构，单个操作可受同步保护，但不承诺跨进程共享或整条业务流程原子。生成器默认使用标准库六位数字算法，时钟可注入以验证边界。

发送、公开校验和注册消费各保留原顺序。发送前清理过期，格式/占用先于冷却；发送两次使用同一码，仅 false 触发一次重试，不吞供应商抛出的未知错误。只有发送成功写记录，使用发送前 now 设置 last_send 和 TTL。公开校验正确码仅标记、错误码保留，注册使用 raw 邮箱并先 pop；不用统一 normalize/consume 函数混淆三者。验证码正确不要求先调用公开验证。

供应商端口明确提供验证码邮件与重置链接发送；运行时配置不能静默回退成无请求。测试提供受控记录发送器和可指定 false/异常行为，证明实际调用/码/link，而不是 SMTP 证据。前端 URL 来源为显式提供者，保留 rstrip 和原 reset 路径；不读取旧部署配置或环境。SQL 回滚不能恢复内存 code 消费或撤回邮件；忘记密码未匹配无发送，匹配 false 仍通用消息，异常不伪造已投递。

### 5. 无环境固定窗口限流

用端口和进程内、线程安全的固定窗口适配器实现三条旧实际限额：register 5/hour、login 10/minute、forgot-password 3/hour。计数键为 request.client.host（缺省 127.0.0.1）与 pathname；窗口从首次命中开始，默认无响应 rate headers。请求验证通过后、业务调用前计数；失败登录仍计数，422 不计数。生成 SlowAPI 旧 limit detail 的兼容文本和 `error` JSON；邮箱冷却仍独立 detail。

不使用直接 Limiter 初始化或增加全局 middleware，因为前者有配置 I/O，后者会给八个原本未覆盖的方法增加 60/minute。不使用转发头作为限流 key（设备记录的原 IP 规则仍保留）。本适配器只证明本进程行为，不能声称多进程或分布式限额。

### 6. SQL 投影、提交失败及竞争

新增独立 `users`、`user_sessions`、`user_credits` 最小真实列投影，保留实际长度、非空、唯一、外键和时间字段；没有 is_deleted。生产仓储仅用调用方的 Session，不 create_all/Engine/反射/迁移。测试合成库先建立认证完整 users，再组合既有粗剪/便签及访问策略投影，不能把旧 id-only users 当作完整生产用户 schema。

UoW 允许一个用例的原多次显式 commit；退出只回滚尚未提交事务并关闭，不将已提交部分说成已撤销。注册用户+积分及登录会话清退+创建各自保持原事务；身份维护允许两次 commit。应用不自动重试唯一竞争、未知数据库错误或提交确认异常，也不把任何 IntegrityError 泛化为凭据正确。未知 jti、缺失积分和重复注册的竞争须用真实两个 SQLite 物理连接及实际唯一约束核验，当前遗留竞争不被改造成新设备配额或首管理员锁政策。

真实 adapter 的提交前注入异常证明未提交 SQL 回滚；真实 commit 已完成后人为抛确认异常证明请求无可信成功、新 Session 显式读能确认 durable truth、没有自动重试。它不是 PostgreSQL 并发、网络故障或生产事务证明。会员/撤销可能与请求交错，保留原查询顺序，不宣称串行授权快照。

### 7. 精确文件白名单

仅允许修改下列六条旧代码路径，四个测试只调整 15 方法集合和必要工厂命名，所有旧业务、冷导入及副作用断言保留：

- `backend/src/haoai_backend/app.py`
- `backend/pyproject.toml`
- `backend/tests/test_module_boundaries.py`
- `backend/tests/test_rough_cut_http.py`
- `backend/tests/test_production_notes_http.py`
- `backend/tests/test_series_access_boundaries.py`

新增生产路径固定为以下 15 个文件：

- `backend/src/haoai_backend/authentication/__init__.py`
- `backend/src/haoai_backend/authentication/configuration.py`
- `backend/src/haoai_backend/authentication/domain.py`
- `backend/src/haoai_backend/authentication/errors.py`
- `backend/src/haoai_backend/authentication/ports.py`
- `backend/src/haoai_backend/authentication/application.py`
- `backend/src/haoai_backend/authentication/email_codes.py`
- `backend/src/haoai_backend/authentication/persistence.py`
- `backend/src/haoai_backend/authentication/tables.py`
- `backend/src/haoai_backend/authentication/schemas.py`
- `backend/src/haoai_backend/authentication/http.py`
- `backend/src/haoai_backend/authentication/passwords.py`
- `backend/src/haoai_backend/authentication/tokens.py`
- `backend/src/haoai_backend/authentication/rate_limit.py`
- `backend/src/haoai_backend/authentication/avatar.py`

新增七个测试路径，各自验证不同边界，不机械复制旧粗剪测试：

- `backend/tests/test_authentication_domain.py`
- `backend/tests/test_authentication_crypto.py`
- `backend/tests/test_authentication_application.py`
- `backend/tests/test_authentication_email_codes.py`
- `backend/tests/test_authentication_persistence.py`
- `backend/tests/test_authentication_http.py`
- `backend/tests/test_authentication_boundaries.py`

直接依赖仅在已允许 pyproject 增加实际使用的固定版本 `python-jose[cryptography]==3.5.0`、`passlib[bcrypt]==1.7.4`、`bcrypt==4.0.1`，既有固定版本不变。无需 email-validator，不新增 SlowAPI 配置 bootstrap 或依赖缓存。若上述成熟模块边界需要调整文件名单，必须先向根报告并更新明确白名单，不能顺手新增。

事后只允许追加 `README.md`、`backend/README.md`、`docs/architecture/backend-module-boundaries.md` 的旧全文末尾，以及在 `docs/architecture/module-api-compatibility.md` 插入第 25 节；旧全文须精确恢复。新增文档只有 `docs/architecture/authentication-backend.md` 与当前 change 的 `verification.md`。当前四规划及既有 `.openspec.yaml` 是本次规划范围，元数据不改；tasks 后续只改变 checkbox，规划正文冻结。前 147 历史和其他 169 代码保持。

### 8. 验收分层与可执行矩阵

| 层 | 必须证明 | 不冒称 |
| --- | --- | --- |
| 领域/密码/JWT | 8 对 6 字符、头像/bio、版本与旧声明、真实签名/过期/type、会员到期边界、列表顺序和原错误 | 真实生产密钥或新安全硬化 |
| 验证码/限流 | 清理/占用/冷却顺序、两 false 同码、verify 不消费/register 先消费、到期相等、客户端/pathname 三限额、两种 429、422 不计数 | SMTP、跨进程限额/存储 |
| 实际 SQLite | 多用户、唯一/FK、维护 commit 次数、注册/改密原子写、两个连接竞争、提交前 rollback 与提交后不确定可读 | PostgreSQL 并发、真实网络 commit 失败 |
| HTTP 与冷子进程 | 11 认证+4原方法、局部 422、default503/zeroSession、显式 resolver 优先、逐包首次 import 的原哨兵、真实 awaitable 接线如提供 | 全站部署、前端页面迁移 |
| 根真实 loopback | 全 15 方法，真实 JWT/password+SQL policy，双用户越权/撤销/会员拒绝，受控 sender 实际码/link，现有 TS parseUser/parseLoginResponse 解析实际响应 | 浏览器/前端全量重跑、真实邮件投递 |
| 保全与清理 | 全 backend 回归、语法/pins、42来源+4库哈希、旧 169/147及文档原文、仅自有进程/端口/临时目录清理、实际 CLI/text | 指定外审或生产授权 |

所有执行日志标明 Luna、根或只读 Sol；初次失败与修正前日志不覆盖。根实际测试用独立 `/tmp` SQLite 与基线共享解释器，禁旧应用导入、pytest 自动插件与旧 bytecode；不创建旧缓存，不改变 timeout/skip 来隐藏失败。固定前端解析器仅作为实际响应消费者验收，既有前端测试/构建/浏览器不在本批必跑清单。

## Risks / Trade-offs

- [首管理员与设备数量的旧检查并非原子] → 保留顺序与实际唯一约束、交错测试和限制说明；不未经批准增加全局锁或新错误策略。
- [旧令牌、整秒版本与可重复 reset 留有遗留行为] → 写成明确兼容场景，不能把模块化声称为安全整改；未来硬化另立契约。
- [认证独立 Session 会先于业务提交] → 明确接线差异，会员拒绝和后续业务失败仍可留下维护，原业务自己的事务不被放宽。
- [验证码与邮件不属于 SQL 事务] → 记录实际消费/发送时点和受控供应商结果，不宣称回滚可恢复 code 或发送。
- [第三方导入可能读环境] → 密码/JWT延迟导入，保留冷子进程全部旧哨兵；无配置工厂只注册方法，不初始化供应商。
- [SQLite 验收不等于生产环境] → PostgreSQL仅编译既有语句或将必要验证列待办，真实JWT会员支付/SMTP/生产依赖不假称完成。

## Migration Plan

根先完整审读规划与实际 CLI readiness，冻结来源、六旧文件备份及允许路径，随后由 Luna 在新模块实施。TDD 与专用回归完成后由 Sol 作只读源码/测试复核；根再独立执行全 backend、真实 loopback、TypeScript 契约、清理与保全。事后文档按实际收据追加，最终 CLI/text/hash 记录本地完成及外审待办。

本批不部署、不启动生产、不修改 schema；回退仅需停止使用新显式认证运行时并继续原调用方身份注入，不删除或回滚已发生的 SQL 数据。后续生产配置或发布需另行授权，不能把它新增为本批本地完成的门槛。当前步骤只作者规划，不代表实现已批准、测试已执行或外审已完成。
