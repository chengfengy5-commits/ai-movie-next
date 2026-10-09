# 第22批：个人粗剪后端模块化

第22批在隔离的 `backend/` 包中实现个人粗剪的第一个真实 FastAPI/SQLAlchemy 后端模块。它采用领域规则、应用用例、工作单元端口、SQLAlchemy Core 持久化、HTTP adapter 和显式 app factory 的分层结构；当前范围仅覆盖粗剪读取与保存，不接入旧站点的完整后端入口。

应用工厂要求显式提供 Session factory、可信身份解析器和同一 Session 上的剧集访问策略。缺任一端口时服务返回 503，不创建 Session，也不默认放行。新应用只暴露 `GET`、`PUT /api/chapters/{chapter_id}/rough-cut`；OpenAPI、Swagger 和 ReDoc 均关闭。

当前 SQL 表仅为该能力定义最小投影，SQLite 测试库和验收实验使用临时文件，不是生产 schema 或数据库迁移。生产源码不创建 Engine 或 schema；测试和验收实验只在自有临时 SQLite 库中创建 Engine 与最小 schema。真实 JWT、成员与团队权限、全站入口、其他业务模块、PostgreSQL 运行时、Worker、队列、媒体服务和生产部署仍未迁移或验收。SQLite 的 CAS、唯一约束与两进程重启持久化验证不能替代真实 PostgreSQL 锁和生产认证验证。

实现边界、可复现命令与分层验证见 [后端模块边界说明](../docs/architecture/backend-module-boundaries.md) 和 [本变更 verification](../openspec/changes/modularize-backend-personal-rough-cut/verification.md)。依赖要求见 `backend/pyproject.toml`：Python 3.12、FastAPI 0.104.1、Pydantic 2.5.0、SQLAlchemy 2.0.23；测试额外依赖 httpx 0.28.1 和 pytest 9.1.1。本地命令不需要导入旧 app。

在仓库根目录、已装好这些依赖的 Python 3.12 环境中，可运行：

```sh
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=backend/src python3.12 -B -m pytest -c backend/pyproject.toml backend/tests -p no:cacheprovider
```

该命令是运行方式说明，不表示本次安装或生产部署。Grillme 独立审查仍待执行，本变更未归档。


## 第二十三批：剧集访问策略

backend/src/haoai_backend/series_access 提供纯领域规则、应用服务、读取端口、SQLAlchemy Core 最小查询投影与粗剪策略桥接。桥接接收粗剪请求当前的 Session、可信 actor 与 series ID；它不创建、提交、回滚或关闭 Session。缺少 app factory 必需端口时，现有 503 行为保持不变。

策略在粗剪读取流程查到章节之后执行。既有章节查询已读取 content 字段；授权通过后才解析章节内容并查询源素材和本人草稿。拒绝 GET 的 ASGI 证据证明素材及私人草稿查询为零；拒绝 PUT 的 loopback 证据证明现有私人记录保持不变且未新增草稿。两者不等同于零 chapter SELECT。

最小表定义仅服务于查询投影与临时 SQLite 测试，不是生产 schema 或 migration。生产源码不创建 Engine 或 schema；测试与验收实验只在自有临时 SQLite 中创建这些对象。当前范围没有接入真实 JWT、会话撤销、会员或完整团队管理，也没有 PostgreSQL 运行验收。详见 [策略说明](../docs/architecture/series-access-policy.md)、[模块边界](../docs/architecture/backend-module-boundaries.md) 与 [verification](../openspec/changes/modularize-backend-series-access/verification.md)。


## 第24批：个人制作记录后端模块化

`haoai_backend.personal_production.notes` 为旧个人制作记录 GET/PUT 提供分层实现，与粗剪模块共用显式应用工厂、可信 actor 类型和业务错误基础。它支持多镜头局部补丁与续作字段，不把前端单镜头请求限制带入服务端；严格 DTO、原始稳定 ID、媒体版本、认可失效、私人 revision 和旧错误映射均按当前来源快照核对。

GET 可能提交媒体协调及私人认可维护。双空历史认可的独立维护提交后，本次 PUT 即返回 409，不继续应用即使猜中 revision 的用户补丁。新媒体协调器供后续同事务写入方复用；它不表示旧 ORM hooks 或所有既有源写入者已经接线。独立新表仅为当前 SQL 查询与私人数据所需的最小投影，不是生产建表迁移。

模块边界见 [个人制作记录后端说明](../docs/architecture/production-notes-backend.md)；命令、测试层次与待验收事项见 [本变更 verification](../openspec/changes/modularize-backend-production-notes/verification.md)。本批使用已安装 Python 3.12 环境和仓库固定依赖；没有安装依赖或导入旧 app。

## 第二十五批：认证模块

新包 haoai_backend.authentication 实现完整认证和邮箱验证码能力，并由显式 app factory 接线到现有粗剪与个人制作记录 API。它不导入旧 app、不读取 .env、不创建 Engine 或生产 schema。缺少当前请求必要的配置时，在创建 Session 前拒绝请求。

本地后端回归使用 Python 3.12、仓库声明的依赖和临时 SQLite。可在已具备依赖的环境中从仓库根目录运行：

```sh
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONNOUSERSITE=1 PYTHONPATH=backend/src python3.12 -B -m pytest -c backend/pyproject.toml backend/tests -q -p no:cacheprovider
```

本批 root 最终回归为 191 passed，pytest 报告用时 5.67 秒；独立命令 runner 用时 6.216 秒，两者是不同计时口径。实际 loopback 和 TypeScript 响应解析见本变更 [verification](../openspec/changes/modularize-backend-authentication/verification.md)。这些记录不表示安装了依赖、连接了生产数据库、发出了真实邮件或运行了完整前端测试。


## 第二十六批：剧集、章节与分镜来源数据

backend/src/haoai_backend/series_data 将剧集及来源数据读写分成领域规则、应用用例、端口、SQLAlchemy Core 持久化、HTTP/schema、分镜规则、媒体协调和响应投影。统一工厂注册十二个新方法；新接口需要可信身份解析器及业务 Session factory，缺配置时返回 503 且不创建业务 Session。既有四个个人制作与粗剪方法仍须显式提供原访问策略。

更新来源时，应用先按实际持久化阶段读取章节和素材状态，再在来源 DML 前调用同 Session 的个人媒体协调器。它维护媒体版本、删除墓碑及同章其他用户认可撤销；不把多次提交合并成整请求原子事务。删除镜头保留无 message_id 外键的孤留任务行为；整章/整剧按原规则清理聊天任务。有账单的任务由真实账本外键阻止删除并回滚当前阶段。

Core 表是该适配器和测试所需的最小投影，不是完整生产 schema 或 migration。生产源码不创建 Engine/schema；PostgreSQL 仅做锁语句编译核验。分层说明见[架构文档](../docs/architecture/series-data-backend.md)。

根在本批实际执行的全量测试命令如下，所有环境变量、解释器及临时目录均来自验收记录：

<pre><code>PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=/Users/yanghaibo/data/projects/ai/haoai-next/backend/src /Users/yanghaibo/data/projects/ai/haoai-修改版/haoai.zhuwh.com/.venv/bin/python -B -m pytest -c backend/pyproject.toml backend/tests -p no:cacheprovider --basetemp=/private/tmp/haoai-next-series-data-root-full-02</code></pre>

该命令对应 242 项通过；完整前后端边界和未覆盖项见[verification](../openspec/changes/modularize-backend-series-data/verification.md)。这不表示本批安装依赖、运行生产库或完成前端全量测试。


## 第二十七批：来源素材 API

`haoai_backend.asset_data` 将角色、场景、道具和分镜素材操作拆为纯领域规则、应用用例、端口、SQLAlchemy Core 持久化、HTTP/schema、响应投影和显式媒体协调。统一工厂新增十五个方法：前三类各四个 CRUD 方法，分镜素材含创建、更新和删除。现有章节分镜素材目录 GET 仍属于 `series_data`，不在本模块重注册。

应用复用同一请求的可信身份和业务 Session。普通素材访问保留作者、团队成员和认领门槛；删除权限使用可信数据库身份，不接受客户端权限标志。类别 DELETE 清理同剧章节中的目标类别原始引用，并在章节来源内容 DML 前完成受影响章节协调。分镜创建不自动向章节 `content` 追加引用；分镜素材 DELETE 保留原始引用并写墓碑，不自动关联章节或续期锁。来源变更按明确阶段协调：签名等价不推进已有媒体版本或撤销认可；实际 `image_url` 字段变化仍调用协调器，缺失状态可初始化 R1，纯元数据变化不强制初始化。每个写请求只提交一次；提交前任一阶段失败整体回滚，提交后刷新失败按结果未知处理。

本批根全量后端测试为 308 项通过；真实 loopback 与冻结 parser 的执行者、请求数、失败历史和边界见[本批 verification](../openspec/changes/modularize-backend-asset-data/verification.md)。临时 SQLite 不证明真实 PostgreSQL 锁或生产 schema；本批未执行完整前端测试、浏览器验收或部署。结构细节见[资产数据后端架构](../docs/architecture/asset-data-backend.md)。


## 第二十八批：聊天数据模块

backend/src/haoai_backend/chat_data 将聊天请求、领域记录、用例、UoW 端口、SQLAlchemy Core 适配器、schema/HTTP 与统计投影分层。app factory 显式接入路由、可信身份解析器和业务 Session factory；缺少必要接线时由路由返回 503，不在模块导入时创建生产 Engine 或 schema。

聊天读取先取得章节所属剧集，再在同一业务 UoW 中执行现有剧集访问策略和查询。创建消息会在同一事务内尝试续期本人章节锁，不续期他人锁；创建与内容更新提交后通过同一 UoW 重新读取行。提交后读取失败属于结果未知，调用方应显式读取核实，模块不自动重发写入。删除聊天消息不会顺带删除无消息外键约束的任务、账务或队列历史。

Core 表只是本适配器所需的查询投影，不是完整生产 schema 或 migration；生产源码不创建 Engine/schema。架构边界见[聊天数据后端说明](../docs/architecture/chat-data-backend.md)，真实运行层次和限制见[本批 verification](../openspec/changes/modularize-backend-chat-data/verification.md)。
