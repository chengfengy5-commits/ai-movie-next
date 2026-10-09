# Tasks

## 1. 来源与规划

- [x] 1.1 核对21份固定来源与292文件/142代码/135历史起点，Sol只读审查规划，根执行strict/status/apply ready，冻结五规划和新路径白名单。

## 2. 独立实现

- [x] 2.1 实现纯域记录、权限解析及错误，定向测试覆盖作者/团队/认领优先级、role非admin限制、非法列表全失效、当前姓名和claimed本人非独立资格。
- [x] 2.2 实现读取端口和应用服务，测试缺剧集短路、认领与最终成员分阶段重新查询、撤销反例、作者短路及普通DB异常不被放行或误分类。
- [x] 2.3 实现最小SQLAlchemy查询投影/仓储，真实临时文件SQLite验证当前记录、准确team/user过滤、同Session、不创建/提交/回滚/关闭/写锁/写入，无跨请求缓存。
- [x] 2.4 实现粗剪可调用桥接与HTTP组合测试，既有GET/PUT使用真实SQL授权、保持旧404/403 detail，拒绝后素材/私人SELECT为零且无新草稿，已授权可本人保存/重读；原142代码全部冻结。
- [x] 2.5 新包边界测试及原冷导入监控通过，纯层无框架/旧app依赖、生产source无engine/schema/env启动与额外endpoint，不新增依赖或使用原环境变量配置。

## 3. 独立审查与根验证

- [x] 3.1 Sol只读审查最终13文件与合同，必要修复后冻结实际SHA，不能以内部审查替代Grillme。
- [x] 3.2 根对最终版本运行整个backend回归、Python语法/manifest、固定source baseline、OpenSpec strict及text/conservation，记录实际退出码；前端历史787不冒称重跑。
- [x] 3.3 根loopback用新factory+临时SQLite+合成身份+真实SQL策略验证授权矩阵、成员变更下一请求、保存/重读，日志仅method/pathname/status并清理自有服务/数据；JWT/会员/PG分别标明未验收。

## 4. 证据与文档

- [x] 4.1 按实际证据编写访问策略说明和verification，四个许可历史文档按白名单追加；不把最小投影或实验身份描述成生产schema/全认证，保留原文与历史SHA。
- [x] 4.2 Sol只读审查最终文档与实际证据，根最终strict/status/apply/text/conservation一致，形成仅指定外审待办的本地验收收据；本change不归档。

## 5. 独立外审

- [ ] 5.1 GPT-5.6 Sol/xhigh Grillme独立审查离线未执行，不自动探测/启动/替代，不以本地通过冒充完成。
