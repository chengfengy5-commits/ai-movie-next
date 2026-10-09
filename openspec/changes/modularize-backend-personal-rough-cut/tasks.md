# Tasks

## 1. 合同与来源冻结

- [x] 1.1 核对固定旧源码、初始 264/122 inventory/code、262 不可变与129历史文件，完成 Sol 规划审查和实际 OpenSpec strict/status/apply ready，再冻结五规划及新路径白名单。

## 2. 后端业务模块

- [x] 2.1 实现纯域投影/全集合规则与领域错误，定向测试验证原始首引用、坏/空内容、重复移除、500读写差异、无视频和空章首次保存，不引入框架依赖。
- [x] 2.2 实现应用用例和 UOW/访问端口，测试权限先于源/私人读取、错误优先级、GET无commit、PUT单commit/rollback/无重试和本次R+1响应归属。
- [x] 2.3 实现最小 Core table 投影与 SQLAlchemy adapter，真实临时文件SQLite测试持久化重开、双用户、不同连接的陈旧SQL CAS/首次唯一竞争、约束、非唯一错误和提交前后确认错误；单列PG锁SQL编译而非PG实测。
- [x] 2.4 实现严格HTTP模型、两个既有方法和显式app factory，ASGI测试贯通应用及真实SQL adapter，验证缺端口503/零Session、401/403策略、body/query持有人隔离、完整响应及422/409/413。
- [x] 2.5 新后端依赖/测试/包边界完整，执行层级与导入/构造无副作用测试；核实纯层无FastAPI/SQLAlchemy、生产source无旧app/环境读取/建表/engine/默认放行，现有前端及fixture全冻结。

## 3. 审查与根最终验证

- [x] 3.1 Sol只读审查最终后端实现、测试与合同，修复必要问题并记录实际源码SHA；独立审查不替代Grillme。
- [x] 3.2 根对最终冻结版本执行backend全量、Python语法/pyproject、固定来源与source baseline、OpenSpec strict/text/conservation，记录实际退出码；前端上一批787结果仅作原记录不冒称重跑。
- [x] 3.3 根通过loopback新FastAPI+独立SQLite文件验证本人保存/显式读取、不同身份、陈旧冲突及重建后持久化；合成身份/权限明确为lab专用，日志仅method/pathname/status，清理自有服务和临时数据。

## 4. 事实收尾

- [x] 4.1 按实际证据编写backend/README与后端边界文档、追加根README与兼容文档第22批、创建verification，保留所有旧前缀/历史；首模块不代表全后端/真实auth/PG/生产完成。
- [x] 4.2 Sol只读审查收尾文档及根最终strict/status/apply/text/代码和历史保全一致，形成本地验收记录；本change不归档。

## 5. 指定独立外审

- [ ] 5.1 GPT-5.6 Sol/xhigh Grillme 独立审查离线未执行；不得自动探测、启动、替代或以本地检查冒充完成。
