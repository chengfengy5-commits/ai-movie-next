# Design

## Context

动机见 proposal。根固定旧 HEAD `23403806898550a7668a6ee7c0c457315655c39b`；`backend/app/services/team_service.py:88–101,124–131,166–185` 是权限解析与准入顺序来源，models/series.py、team.py、user.py提供最小查询字段。旧rough_cut.py先查章再调用verify_series_access。本批起点292个文件、142份代码/配置、135份既有change历史；第22批54项根测试及22次loopback已完成，只有指定外审待办。

已实际读取 OpenSpec root/context/config 和主规格清单（当前无主规格），采用现有spec-driven/zh-CN。21个固定旧来源SHA用于保全，其中本批必要函数由独立Sol实际只读核对；全文hash核对不等于所有源码已逐行评审。

## Goals / Non-Goals

**Goals:** 独立的series_access业务包，读取当前数据库记录并显式注入原粗剪策略端口；纯规则可审查，授权错误准确，拒绝不触及下游私人数据。

**Non-Goals:** 不迁移真实身份、会员、认领或成员管理写入，不更改142份既有代码，不扩展endpoint、不创建生产schema、不承诺PG竞争或并发撤销原子性，不启动旧app/Worker/provider或生产。

## Decisions

### 分层与可调用桥接

新增目录 `haoai_backend/series_access`。domain/errors只依赖标准库；ports/application只引用纯层。persistence/tables以SQLAlchemy Core查询；rough_cut_policy仅组合仓储/应用，并显式将本领域NotFound/Denied映射到第22批已有NotFound/Forbidden，传递精确detail。选择桥接而非修改rough_cut模块，使原接口及代码逐字节冻结，避免把业务规则塞入顶层app。公开可调用接口接收 `(Session, TrustedActor, series_id)`，可直接提供给原工厂参数；只有adapter引用另一个业务模块的公开错误/actor端口，纯层不依赖粗剪。

仓储每次策略调用绑定传入Session，不创建/关闭Session，不commit、rollback、flush、加写锁或建表。不用HTTP参数、Bearer原串、用户名或team.owner_id当授权身份；身份只来自已解析actor。身份端口仍由调用方显式提供。

### 决策、查询及兼容边界

先load_series；无结果404。团队且claimed_by真值且不等于user时才进行认领检查。按同一剧集team_id与user_id查询membership；owner或精确有效permission可进入；否则只在此拒绝分支查询claimed user的当前username形成动态403。之后作者或该team membership可访问；认领本人不是额外最终授权。无团队的非作者不查询membership；无认领的作者无需成员记录。按旧阶段重新读取membership：认领检查用一次，认领已通过且非作者时，最终团队准入再查询一次；不能把前次owner/permission结果缓存为后阶段成员资格。作者在认领通过后直接返回，无需最终membership查询。不跨调用缓存决定；这不是PG下对并发撤销的原子承诺。

保留旧六个TEAM_PERMISSIONS键，仅过滤已知键。json解析后非list返回空；原实现对任意不可hash元素触发TypeError并丢弃整个list，本批不把有效前缀偷偷保留下来。不限制admin、不trim角色/permission/raw IDs、不增加superuser和团队归属猜测。claimed用户名缺行是空串；原数据库username为非空约束字符串，本批不额外维护用户行。

### 最小查询投影与测试数据库

tables声明独立MetaData中的series(id,user_id,team_id,claimed_by)、team_members(id,team_id,user_id,role,permissions)及users(id,username)最小读取投影；这些投影不是生产schema或migration。测试可先创建访问模块投影，再创建原粗剪metadata（已有users表被跳过），使同一临时数据库同时容纳用户名与已有private FK；不修改第22批tables。完整源表约束、其他列与生产migration不在本批证明范围。实际file SQLite证明绑定同一个Session、只读SELECT、不同请求重新查当前记录及与真实粗剪持久化组合。

新增代码/测试白名单13路径：
- backend/src/haoai_backend/series_access/__init__.py
- backend/src/haoai_backend/series_access/domain.py
- backend/src/haoai_backend/series_access/errors.py
- backend/src/haoai_backend/series_access/ports.py
- backend/src/haoai_backend/series_access/application.py
- backend/src/haoai_backend/series_access/tables.py
- backend/src/haoai_backend/series_access/persistence.py
- backend/src/haoai_backend/series_access/rough_cut_policy.py
- backend/tests/test_series_access_domain.py
- backend/tests/test_series_access_application.py
- backend/tests/test_series_access_persistence.py
- backend/tests/test_series_access_http.py
- backend/tests/test_series_access_boundaries.py

新文档仅 docs/architecture/series-access-policy.md 与本change/verification.md；已有README.md、backend/README.md、backend-module-boundaries.md仅EOF追加，兼容文档仅在“后续生成与任务的来源等级”前插入“第二十三批当前后端迁移契约”。其余288既有文件不可变；135历史及142代码全部冻结。五份规划正文审查后冻结，仅tasks复选框可变。

### 验收与执行顺序

沿用现有Python3.12解释器及固定依赖，不安装、不读取旧配置或运行旧测试。仅新src的PYTHONPATH，-B及关闭pytest缓存/plugin自动载入；实际SQL数据库置于自有临时目录。纯域和应用测试覆盖优先级/查询短路/异常保留；真实SQL测试覆盖不同剧集/成员/角色/权限/用户名、撤销与同Session不commit等；ASGI调用原GET/PUT并实际查询授权，记录拒绝后素材/草稿SELECT为零及数据库无新draft。新边界测试与原冷导入监控共同覆盖新包。

Sol按最终源SHA只读审查；根跑整个backend回归、语法、来源baseline、OpenSpec严格校验、text/conservation。前端787结果仍属第21批旧证据，不冒称重跑。根loopback仅新factory+临时SQLite+合成身份及本批真实SQL策略，验证作者、普通成员、认领拒绝、队长、permission、认领孤立用户、下一请求撤销及本人保存/重读；请求日志只method/pathname/status，清理服务与数据库。SQLite不代替PG，合成身份不代替JWT/会员。

## Risks / Trade-offs

- [注释误导为admin专属权限] → 依据当前实际源码保留任意角色的明确permission，不自行改业务授权。
- [作者或claimed本人被过度放行] → 用作者他人认领拒绝与孤立claimed本人拒绝的反例固定优先级。
- [最小投影被误当生产建表] → 生产source无建表/engine，测试建表明确只为临时库，完整schema另验。
- [策略错误被粗剪默认detail吞掉] → bridge显式映射404/403原detail，其他数据库错误不转为允许或业务拒绝。
- [真实auth或原子撤销误报] → 实验身份、会员/PG及访问时点分别记录；不扩展本批保证。

## Migration Plan

只增新业务包、测试及许可文档，不切换旧服务。后续独立change迁移身份/会员与PG readiness再决定全站组合；本批没有生产部署、回滚或归档动作。实现Luna6/xhigh请求路线，分析审查6.1Sol/xhigh请求路线；实际运行元数据未独立确认。指定5.6Sol/xhigh Grillme外审保持离线未执行，不探测或替代。
