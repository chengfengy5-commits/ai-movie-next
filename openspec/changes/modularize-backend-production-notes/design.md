# Design

## Context

见 proposal.md。当前312文件、155代码/配置、141历史变更文件；第23批本地已验收，指定外审未执行。旧来源固定commit23403806898550a7668a6ee7c0c457315655c39b，共29source hash。主要依据旧routes/personal_production_notes.py、纯media模块、services/personal_production_state.py、models/personal_production_notes.py及API/state/media测试；不导入旧app。

## Goals / Non-Goals

**Goals:** 将完整notes GET/PUT和媒体协调迁入独立纯层/端口/持久化/HTTP，并在现有根工厂直接接线，供既有前端合同调用。保留维护可写GET、同章跨用户认可撤销和双空独立commit后的PUT强制停止。

**Non-Goals:** 本批不实现其它写源模块的ORM before_flush hooks、生成调用、真实JWT/会员、生产schema迁移、PG运行/锁竞争、生产启动或语言替换；提供可复用媒体协调用例，后续源写模块须在同事务中组合，不冒称旧ORM监听器已迁移。当前SQLite与PG方言编译只证明各自层次，不替代真实数据库并发。

## Decisions

### 公共身份及错误基础层

新增shared/{__init__,identity,errors}.py；TrustedActor从现有rough ports重导出同一class，BusinessError承载status_code/detail，RoughCutError继承它。拒绝复制actor类型或让notes依赖rough内部。原series_access桥接仍可用，同一Session传入且旧错误继承共享base，被notes HTTP按原detail映射。未知异常继续传播。

### 全部业务规则而非拆成不相容endpoint

新增personal_production/notes/{__init__,domain,media,errors,ports,application,reconciliation,tables,persistence,schemas,http}.py。media纯函数保留旧normalize/projection；domain记录与局部补丁规则；reconciliation纯事实比较/认可撤销；application编排UoW、锁、CAS及维护提交。Core adapter使用当前数据库记录，不依赖ORM identity-map、隐式钩子或默认环境。HTTP保留同一个notes路径GET/PUT，Pydantic严格模型同时接受frames与显式resume字段。前端既有单帧写请求是服务器完整多帧合同的子集，不反向限制服务器。

### 事务归属与旧维护特例

请求各自创建/关闭Session，正常GET提交媒体维护，PUT先用户锁（保留旧with_for_update(key_share=True)方言语义）再章节/授权/媒体/私行锁。读取snapshot要求clean request-scoped Session，无new/dirty/deleted，Core查询在no_autoflush下。媒体初始化、摘要变化、墓碑及同章所有approved撤销按媒体及私行CAS在当前事务内执行。双空历史认可修复本人revision后必须独立commit；随后本次PUT即便revision猜对也409终止，因为锁已释放。保留旧错误映射；不能把普通PUT rollback扩大为已提交维护回滚。成功响应从当前snapshot+patch计算。

### 扩展实际根工厂与有界历史修改

现有create_app同时组装notes及rough UoW，三个原必需参数不变，不创建Engine/schema；缺项时两个模块503/零Session。不新增依赖。允许修改仅以下6旧代码：app.py，rough_cut/ports.py，rough_cut/errors.py，tests/test_module_boundaries.py，tests/test_rough_cut_http.py，tests/test_series_access_boundaries.py；后三者只更新工厂四方法/route-count及名称，不放宽副作用断言。其余149旧代码严格冻结，141历史变更文件不改。新6测试为test_production_notes_{domain,media,application,persistence,http,boundaries}.py。共20新代码+6修改旧代码。

## Risks / Trade-offs

- [GET可维护多用户数据] → 明确授权先于任何媒体/私行查询，测试拒绝查询/写入短路；用真实SQLite验证跨用户认可撤销仅同章affected approved。
- [双空独立commit破坏普通原子直觉] → 定向预填revision和混合patch反例，真实HTTP对完整旧私人记录比较并重启重读。
- [新旧模块最小表重叠] → notes独立声明兼容的users/chapters/storyboard_assets最小查询投影与自己的media/note表，不依赖rough内部表模块；测试按兼容共有列组合临时schema，不生产DDL；权限测试建立superset schema而非误称生产schema。
- [URL归一化误认同一素材] → 固定共享digest vectors、未知参数/控制字符/secondary引用/空白ID及重复身份测试。
- [SQLite不证明PG锁] → 编译检查锁顺序与语法、CAS/唯一性真实SQLite验收，PG真实并发仍单列待办。

## Migration Plan

Sol先审五规划，根strict/status/apply ready后冻结；Luna按既有GPT-6 Luna/xhigh实现，只改白名单并记录实际测试和失败修复；Sol只读source review，根运行全部backend、语法manifest、source baseline、loopback与完整hash/text保全。根实际HTTP覆盖两用户、partial note/resume、媒体变更撤销、双空409阻止补丁、拒绝不维护、重启持久化和资源清理。四许可历史文档采用原文追加、compat第24节插入；新增docs/architecture/production-notes-backend.md与本change verification。Sol审文档后根实际final CLI/text/conservation。外审未执行保持独立待办，不归档/commit/push/deploy；生产上线另需授权。
