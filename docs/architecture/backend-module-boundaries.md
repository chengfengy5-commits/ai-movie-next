# 后端模块边界：个人粗剪

## 范围

`haoai_backend.personal_production.rough_cut` 是新隔离后端中的首个业务模块。它处理一项能力：当前章节个人粗剪草稿的读取与保存。当前代码不是全站后端入口，也没有改写旧应用或迁移其他业务模块。

## 依赖方向

- `domain.py` 定义纯 Python 领域记录、输入约束、投影和规则，不依赖 FastAPI 或 SQLAlchemy。
- `application.py` 编排领域规则与 `UnitOfWork` 端口；`ports.py` 定义可信 actor 和工作单元接口。
- `persistence.py` 将端口适配到 SQLAlchemy Core `Session`；`tables.py` 只列出粗剪所需的最小章节、素材和私人草稿投影。
- `schemas.py` 定义 HTTP 输入校验；`http.py` 提供 GET/PUT adapter 和错误映射；`app.py` 只负责显式组合依赖。

应用层不自行创建连接、Engine 或 schema。请求各自创建并关闭一个 Session；剧集访问策略接收同一个 Session、可信 actor 和 series ID。GET 不提交事务，PUT 在一次应用事务中进行个人 revision 与 SQL CAS 检查。

## 端口与 HTTP 边界

调用方通过 `create_app(*, session_factory, resolve_actor, series_access_policy)` 提供三个必需端口。`resolve_actor` 可同步返回或异步解析 `TrustedActor(user_id)`；查到章节后，剧集访问策略在查询源镜头素材目录及本人草稿之前执行。缺少任一端口时，应用返回 503 且不创建 Session，不存在默认允许访问的回退。

HTTP 范围仅为 `GET` 和 `PUT /api/chapters/{chapter_id}/rough-cut`，API 文档端点关闭。严格输入模型拒绝额外字段。读写共用既有粗剪来源规则；请求中的用户 ID、查询参数和客户端素材顺序不作为授权依据。

## 持久化与并发边界

粗剪表仅保存用户、章节、revision 和有序 `{asset_id, included}` 私有投影。章节内容和素材目录从当前来源读取，不将其复制为个人数据。chapter/user 唯一约束处理首次保存竞争；已有草稿更新使用 revision 条件更新。实际代码将最终响应构造成本次写入的 revision 加一，不以提交后的额外读取冒充写入结果。

测试使用临时 SQLite 文件验证持久化、唯一竞争、CAS、回滚及进程重启；PostgreSQL `FOR UPDATE` 仅做方言 SQL 编译检查，没有真实 PostgreSQL 运行或锁竞争验证。最小 Core 表不是完整生产 schema，也没有生产建表或迁移流程。

## 来源及兼容语义

只按章节各帧 `storyboard[0]` 的原始 ID 统计候选，再确认唯一候选存在于按 `chapter_id` 读取的素材目录；不新增 `series_id` 过滤。ID 不裁剪空格，不使用名称、位置或媒体 URL 做身份回退；重复的移除 ID 保留原有投影语义。视频 URL 正则保留旧大小写不敏感行为。读取允许大于 500 个来源条目。来源 JSON 或章节内容结构解析失败会先于个人 revision 检查并返回 422；解析成功后，模块先比较 revision，再验证稳定原始 ID 的唯一性与归属，最后校验完整来源数量。请求 body 超过 500 项按模型校验返回 422；身份有效的章节来源超过 500 项返回 413。空章节可首次保存空列表。

## 当前没有迁移的能力

本模块没有实现或接线旧站点的完整认证、JWT、password version、session 撤销、真实成员/团队授权适配、全站 API、其他业务表、生产数据库迁移、Worker、队列、媒体/存储服务或部署入口。loopback 验收使用显式合成身份和访问策略，只能证明此模块在该实验适配下的行为，不能视作真实授权验证。


## 第二十三批：剧集访问策略

新包 haoai_backend.series_access 把剧集作者、团队成员及认领访问判断拆为领域规则、应用服务、读取端口、Core 仓储和粗剪桥接。策略使用调用方传入的同一 SQLAlchemy Session；它不拥有 Session 生命周期，也不写入、提交、回滚、建表或创建 Engine。粗剪 app factory 显式要求 Session factory、可信身份解析器和访问策略，缺项时返回 503 且不创建 Session。

访问流程先按剧集 ID 查询当前剧集；不存在时返回 404“剧集不存在”。对团队中由他人认领的剧集，先检查当前成员的 owner 角色或精确权限 enter_claimed_series；该检查通过后，再按作者或当前团队成员关系判断最终资格。认领人本人不会因此自动获得访问资格。若认领关卡拒绝，响应保留当前认领用户名；用户名缺失时使用空名。一般授权拒绝返回“无权访问该剧集”。成员、权限及认领用户名不跨请求缓存。

此策略在粗剪既有的章节查询之后执行。章节 SQL 已读取 content；策略先于 content 解析、源镜头素材查询和本人草稿查询。ASGI GET 测试验证拒绝后没有源素材或私人草稿查询；独立 loopback PUT 验证拒绝时既有私人行未变且没有新草稿。不能将两项证据扩大为零 chapter SELECT 或 PUT 查询轨迹。

模块定义的三张最小 Core 表是查询投影及临时测试 schema，不是生产表定义或迁移。PostgreSQL FOR UPDATE 只做方言编译检查。详细规则、兼容边界和分层验收见 [剧集访问策略](series-access-policy.md) 与 [本变更 verification](../../openspec/changes/modularize-backend-series-access/verification.md)。


## 第24批：个人制作记录模块

`personal_production.notes` 将既有记录读写分为 schemas/HTTP、application、纯 domain/media/reconciliation、ports 与独立 SQLAlchemy Core persistence。它与粗剪共用同一可信 actor class、BusinessError 基础和根工厂，但 notes persistence 不依赖粗剪表模块。调用方仍负责 request-scoped Session 和事务生命周期；媒体协调入口不自行 commit/rollback，未来源写者须在自己的同一事务内显式调用。

访问拒绝发生在媒体协调和本人私有记录维护之前。章节查询本身已经包含 content；策略通过后，模块解析该内容并读取源素材和私人记录。GET 成功时可能提交媒体版本、墓碑及同章认可撤销，所以不可描述为纯只读。私人 note/续作 PUT 使用 revision CAS 和首次行唯一约束，维护已提交与本次用户补丁的冲突边界依照旧行为处理。

第24批兼容边界、自动化结果、初次 loopback 清理失败与后续通过的 42 请求复验见 [verification](../../openspec/changes/modularize-backend-production-notes/verification.md)。真实 JWT/会员、完整生产 schema、PostgreSQL 运行、旧 ORM hooks、其他业务模块、Worker、队列、媒体服务及部署入口仍在范围之外。

## 第二十五批：认证模块边界

haoai_backend.authentication 将认证域规则、应用用例、SQLAlchemy Core 持久化、密码/JWT 适配器和 HTTP DTO 分开。create_app 负责显式组装；默认导入与构造不读取环境、不创建 Engine/schema/Session，也不初始化密码、JWT、限流或邮件供应商。认证运行时所需端口必须由调用方配置；配置不完整时请求在 Session 创建前返回 503。

认证 Session 与四个既有业务方法使用的 Session 独立。身份解析先维护本人会话，再检查会员资格；会员拒绝不会撤销已经提交的身份维护，但不会进入业务素材或本人私有数据读取。未带显式 resolve_actor 时，配置完整的认证运行时提供真实 JWT actor；调用方显式 resolver 的既有优先级保持不变。

模块兼容固定旧来源中的 11 个认证方法；它与既有粗剪、个人制作记录各两个 GET/PUT 合计 15 个方法。SQL 表是认证读取/写入所需的最小投影，不是生产 schema 或迁移；SQLite 是本地证据，未运行生产 PostgreSQL、真实 SMTP、会员支付或 Worker。结构与事务边界见 [认证后端说明](authentication-backend.md)，实际执行层次见 [verification](../../openspec/changes/modularize-backend-authentication/verification.md)。


## 第二十六批：剧集与来源数据模块

series_data 将旧剧集/章节/分镜路由拆成纯 domain、application/UoW ports、Core persistence、schema/HTTP 和 presentation。应用由显式工厂接线，不引入全局 ORM hook。缺少 Session factory 或可信身份 resolver 时，新来源方法返回 503 且不创建业务 Session；既有四个个人状态方法仍使用其显式访问策略。

普通剧集访问保留认领门槛、作者短路和之后重新读取的团队成员资格；删除操作使用可信数据库中的超级用户标志或原删除权限，不接受请求传入的权限标志。章节查询本身可能读取 content；访问规则在内容解析以及源素材、私人记录查询之前执行，不能据此声称授权前没有章节查询。

来源写入在同一业务 Session 中按旧 flush-equivalent 阶段协调媒体状态。实际变化才更新时间、推进媒体版本或撤销跨用户认可；已提交阶段不因后续失败而回滚，也不会自动补偿或重试。AITask.message_id 没有外键，单帧删除可保留孤留任务；整章/整剧才显式按消息映射清理任务。带账单的任务受真实 billing 外键约束，删除失败时保留账本和当前阶段。

生产表定义仍只覆盖所需列，不是迁移或完整生产 schema。PostgreSQL 仅验证锁语句编译，没有真实 PostgreSQL 并发验收。设计与证据见[剧集来源数据架构](series-data-backend.md)和[本批 verification](../../openspec/changes/modularize-backend-series-data/verification.md)。


## 第27批：来源素材模块边界

`haoai_backend.asset_data` 负责角色、场景、道具及分镜素材的十五个新 HTTP 方法。`domain.py`、`naming.py`、`references.py` 处理纯规则；`schemas.py` 与 `presentation.py` 定义输入和响应；`application.py` 编排用例；`ports.py` 描述应用依赖；`persistence.py`、`tables.py` 适配同一 SQLAlchemy Core Session；`media_writes.py` 显式调用既有个人媒体协调器。`app.py` 只负责将 Session factory、可信身份解析器和模块路由接入统一工厂。

新方法在同一业务 Session 内执行访问判定、来源写入和所需媒体协调。类别 DELETE 清理目标类别引用；受影响章节按稳定顺序锁定并在章节来源内容 DML 前完成协调。每个写请求只提交一次，提交前任一阶段失败整体回滚。分镜素材创建不自动追加章节引用；分镜素材 DELETE 保留原始引用并写墓碑，不自动关联章节或续期锁。签名等价不推进已有媒体版本或撤销认可；实际 `image_url` 字段变化仍调用协调器，缺失状态可初始化 R1，纯元数据变化不强制初始化；媒体身份确有变化时才推进版本并撤销其他用户的对应认可。显式协调也可能为其它旧引用初始化缺失状态。

响应在提交后通过同一 UoW 重新读取当前行。提交成功但后续读取或确认失败时结果可能未知；模块不自动重发写入，调用方应显式读取核实。新增 Core 表只覆盖该适配器所需列，不是生产 schema 或迁移；验收未运行真实 PostgreSQL。分层实现和兼容边界见[资产数据后端架构](asset-data-backend.md)，本地证据见[本批 verification](../../openspec/changes/modularize-backend-asset-data/verification.md)。


## 第二十八批：聊天数据模块边界

haoai_backend.chat_data 将纯消息记录和统计合并放在 domain/statistics；application 组织十个用例和 UoW 生命周期；ports 定义应用所需接口；persistence 以 SQLAlchemy Core 在单个 request-scoped Session 中实现查询与写入；schemas/http 适配既有请求；presentation 将记录投影成响应。统一 app factory 负责显式接线，不在导入时启动生产数据库或创建 schema。

访问策略复用公开 series_access 核心。应用先按各端点既有顺序解析章节所属剧集，再在同一业务 UoW 中检查可信 actor 的访问资格并读取消息或统计；认证 Session 与业务 Session 仍由认证层分开管理。缺少身份或 UoW 接线时新路由返回 503。

模块保留单请求提交和提交后同 UoW readback 的边界。消息写入成功而后续读取失败时结果可能未知，不自动重试；单条删除只按聊天/素材消息既有类别条件执行，不删除关联任务和账务历史。数据表定义是最小投影，不是生产 schema 或 migration。细节与实际证据见[架构说明](chat-data-backend.md)和[本批 verification](../../openspec/changes/modularize-backend-chat-data/verification.md)。
