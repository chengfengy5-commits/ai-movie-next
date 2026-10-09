# Tasks

## 1. 来源与规划

- [x] 1.1 核对312文件/155代码/141历史/29固定来源起点，Sol只读完整规划审查通过，根strict/status/apply ready后冻结规划及明确6旧代码修改白名单。

## 2. 完整模块实现

- [x] 2.1 实现shared身份/业务错误兼容基础与notes纯媒体/局部补丁规则；定向测试覆盖同class重导出、固定digest vectors、身份反例、unknown字段、note-only续认、完整500项/严格DTO边界。
- [x] 2.2 实现notes端口、应用及媒体协调，测试单调版本/墓碑/同章跨用户认可撤销、独立双空commit后预填revision仍409、错误顺序及CAS回滚，提供可由后续源写事务复用的协调入口。
- [x] 2.3 实现同Session SQLAlchemy Core UoW及最小投影，实际临时SQLite证明私人隔离、lazy维护提交、当前事实读取、全体认可CAS及首次唯一竞争，PG锁只记录方言编译证据。
- [x] 2.4 实现notes HTTP并扩展原create_app为四方法，严格旧DTO支持多帧/notes与resume组合/显式null；ASGI验证真实SQL策略、403拒绝无媒体/私表操作、缺端口503零Session、成功快照和未知DB异常不放行。
- [x] 2.5 更新仅三个原工厂边界测试的四方法期望，新增六组完整模块测试并运行全backend回归；冷导入证明无环境/Engine/Session/schema启动，其他149旧代码和141历史hash不变。

## 3. 独立复核与根验收

- [x] 3.1 Sol按冻结合同只读审查最终20新代码+6许可修改代码和测试，必要问题闭环后冻结实际SHA，保留既有公开接口及原粗剪业务。
- [x] 3.2 根对最终代码真实运行全backend、Python语法/依赖manifest、source contract/baseline、OpenSpec严格检查、text与conservation，记录退出码并证明其余旧文件守恒；不冒称前端历史787重跑。
- [x] 3.3 根实际loopback验收两用户隔离、partial note/resume、多镜头原子校验、媒体变更撤销、双空历史维护409阻止补丁、拒绝无维护、重启持久化及自有进程/临时数据清理；身份为合成，JWT/PG另列未验收。

## 4. 文档收口

- [x] 4.1 根据实际证据编写生产记录后端说明和verification，四许可历史说明原文追加及compat第24节插入；明确GET维护写与历史独立commit、未迁移其它源writer/hook、最小投影及真实联调边界。
- [x] 4.2 Sol只读文档与证据审查后根最终strict/status/apply/text/conservation实际一致，形成仅指定外审待办的本地验收收据；不归档。

## 5. 独立外审

- [ ] 5.1 指定GPT-5.6 Sol/xhigh Grillme外审当前离线未执行；不自动探测/启动/替代，不以本地通过冒充完成，保持独立待办且不阻已授权本地开发。
