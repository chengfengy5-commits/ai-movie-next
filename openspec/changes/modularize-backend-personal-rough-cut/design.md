# Design

## Context

动机见 proposal。固定旧 HEAD 为 `23403806898550a7668a6ee7c0c457315655c39b`，本批 baseline 核对 20 个来源文件的全文 SHA/字节、旧 HEAD/status；Sol 的独立静态分析另列实际阅读的 8 个来源，不混为执行证据。旧粗剪路由 221–316、投影 64–200 和 model/migration 定义现有契约；旧 main 启动会数据库校验及任务恢复，禁止直接复用启动。

起点为 264 个文件/122 个代码文件、前 21 个 change 的 129 份历史文件，尚无 backend 应用。现有前端、fixture、来源 snapshot 和依赖保持逐文件 SHA 冻结；只有 README/兼容清单允许事后追加，因此 262 个既有文件不可变。

## Goals / Non-Goals

**Goals:** 交付真实新后端业务包，纯规则可单测，数据库事务和 HTTP 可独立接线；以实际 SQLite 和新 FastAPI 请求链验证持久化及 CAS。

**Non-Goals:** 不把源表投影当全量数据库模型或迁移，不提供真实 JWT/会话/会员/团队适配器，不提供可部署全站入口；不接其他业务、真实 PostgreSQL、Worker、队列、R2、provider 或生产。

## Decisions

### 业务目录与依赖方向

选择 src 布局避免与旧 `app` 包冲突，业务目录为 `haoai_backend/personal_production/rough_cut`。domain/errors 仅依赖标准库；ports 引用纯值对象；application 只依赖 domain/ports/errors；schemas/http 处理 Pydantic/FastAPI；tables/persistence 处理 SQLAlchemy；顶层 app factory 仅负责组合。不把 SQLAlchemy、FastAPI 或旧 app 引入纯规则与应用层。独立层级测试和导入/构造无副作用测试维护边界。

本批固定新增代码/配置 20 路径：
- backend/.gitignore
- backend/pyproject.toml
- backend/src/haoai_backend/__init__.py
- backend/src/haoai_backend/app.py
- backend/src/haoai_backend/personal_production/__init__.py
- backend/src/haoai_backend/personal_production/rough_cut/__init__.py
- backend/src/haoai_backend/personal_production/rough_cut/domain.py
- backend/src/haoai_backend/personal_production/rough_cut/errors.py
- backend/src/haoai_backend/personal_production/rough_cut/ports.py
- backend/src/haoai_backend/personal_production/rough_cut/application.py
- backend/src/haoai_backend/personal_production/rough_cut/tables.py
- backend/src/haoai_backend/personal_production/rough_cut/persistence.py
- backend/src/haoai_backend/personal_production/rough_cut/schemas.py
- backend/src/haoai_backend/personal_production/rough_cut/http.py
- backend/tests/conftest.py
- backend/tests/test_rough_cut_domain.py
- backend/tests/test_rough_cut_application.py
- backend/tests/test_rough_cut_persistence.py
- backend/tests/test_rough_cut_http.py
- backend/tests/test_module_boundaries.py

事后新增文档只 backend/README.md、docs/architecture/backend-module-boundaries.md 和本 change/verification.md；旧文档仅 README EOF 与兼容文档「第二十二批当前后端迁移契约」在后续生成章节前插段。本 change 五份规划正常创建，tasks 正文冻结仅改复选框。

### 最小接线与安全端口

采用 app factory，显式接收每请求 Session factory、可信身份+有效会员解析 callable 和同事务剧集访问 policy。缺少任一端口时粗剪合法请求返回 503，且不调用 Session factory，不用 Bearer 原串或外部 user_id 当身份。已接线身份端口可返回 401/403；策略由 UOW 使用同一 Session+可信 user_id+series_id 调用，可保留 403/404。策略不得 commit/rollback，应用层掌管事务；访问拒绝后源/草稿查询为零。工厂禁用自动 docs/redoc/OpenAPI 路由，仅注册 GET/PUT 这两个业务方法。

选择注入端口而非复制旧 auth/team 链，避免将 JWT、password_version、会话撤销及认领规则的未完整迁移偷偷简化为默认放行。生产接入这些安全端口是明确后续范围；测试与 loopback 的两个合成身份/访问策略只存在 tests 或根证据脚本，生产 source 禁止 test principal/default allow。

### 应用工作单元与错误优先级

一个 use case 持有一个 UOW；接口暴露 load_chapter(lock)、require_series_access、source_assets、private_draft、insert/CAS、commit。SQL adapter 为每次调用创建/关闭 Session，GET 离开时仅回滚读事务，PUT 应用只显式 commit 一次，失败尝试 rollback，adapter 内不散落 commit。提交前失败须无落地变更；若数据库已经提交而确认出错，rollback 不能撤销已提交结果，模块不误报成功、不自动重试，由后续显式读取核对。

PUT 顺序严格保留旧实际源码：404 查章 → 同事务 access → source JSON 解析（坏 422）→ private draft 与 revision（409）→ 任意源身份无效（422）→ 合法源 >500（413）→ 完整集合重复/缺项（422）→ insert/CAS。请求 frames>500 是 Pydantic 422，不是源上限 413。domain 与 HTTP 校验分层，不给后台新增 JS 安全整数上限。GET 没有 500 限制；保留 falsy/空 content、text/original_text 真值 fallback、视频 regex、额外 storyboard 引用与重复 removed ID。

### SQLAlchemy Core 与事务

对现有 users/chapters/storyboard_assets/rough_cut_drafts 声明最小列投影，不导入旧 ORM、不读取 DB URL、不全局建 engine，不做反射、create_all、seed 或迁移。投影不代表完整 schema，生产上线须另验现有 schema/约束。素材查询保持仅 chapter_id，不新增 series_id 条件。PUT 对章节 SELECT FOR UPDATE；私人唯一键和 revision>0/FK cascade 的来源来自旧 model/0003 migration。

首次写入竞争用真实唯一约束处理；只有同一 chapter+user 已存在的竞争能变 409，其他 IntegrityError 不能吞掉。选择应用层单次提交，返回本次已写 R+1 和本次 source 投影，避免 commit 后重新 SELECT 带入他人后继 R+2。可用 savepoint/错误类型在 adapter 区分 insert 的特定唯一竞争，应用整个用例仍须失败回滚，不自动重试。

### 验收运行与新依赖

依赖保持固定来源所用 FastAPI 0.104.1、Pydantic 2.5.0、SQLAlchemy 2.0.23；测试使用已验证的 Python 3.12.13/httpx 0.28.1/pytest 9.1.1，声明 Python 3.12 支持范围。可只读复用现有解释器已安装库，用 -B/PYTHONDONTWRITEBYTECODE 防止写旧缓存，PYTHONPATH 只指新 src；不 import 旧 app/main/tests，不复制虚拟环境或缓存。backend/.gitignore 只屏蔽本模块未来的虚拟环境、字节码、测试及打包缓存，不修改根 .gitignore。HTTP 测试用 AsyncClient+ASGITransport，避免 TestClient 版本不兼容。测试只在临时目录创建明确的 SQLite schema、合成 source/两身份和权限策略；同文件不同连接证明持久化、独立用户、确定性陈旧 CAS、唯一竞争、约束和回滚。

根最终执行 backend 全量、Python 语法与 pyproject、源 baseline/strict/text/conservation；既有 122 代码全冻结，前端 787 的上一批成功记录不冒称本批重跑。根用独立 lab bootstrap 在 loopback 启动新 factory+真实 SQLite，仅 GET/PUT，身份和权限替身单列；保存/重读/重建app或进程后仍可从同一测试文件读出。日志仅 method/pathname/status，清理自有服务和临时数据。当前无 UI 或前端修改，因此不重复浏览器验收。

## Risks / Trade-offs

- [SQLite 无 PG 行锁语义] → 只把 SQLite 记为持久化/SQL CAS/rollback，PG dialect 锁 SQL 编译是静态证据，PG真实并发与 schema readiness 保留独立待办。
- [遗漏安全接线被误当可上线] → 缺端口 503 且 Session 0，所有身份/权限替身标为验收专用；不创建全站入口。
- [部分 schema 投影误用于建表] → 生产代码无 schema 创建，建表只在 tests/lab 临时文件；文档明确不能拿投影迁移生产。
- [通用 DB 错误被伪装冲突或误报成功] → 测非唯一 IntegrityError、提交前失败的回滚、提交后确认错误不自动重试且可读取实际结果，以及独立新事务可用；成功只在 commit 后返回。
- [前端继续堆切片而后端进度被夸大] → 此批记录首个真实粗剪模块及其接线边界，其他后端模块、真实 auth/PG/生产仍不算完成。

## Migration Plan

本批只在隔离目录添加模块及验证记录，不接管旧服务、不改真实 DB，不运行部署。后续以独立 change 迁移真实身份/访问策略与 PostgreSQL readiness，再决定全站组合和上线；没有本批生产切换或回滚动作。
