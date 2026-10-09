# Tasks

## 1. Planning

- [ ] 1.1 冻结本变更的五份规划、固定来源和精确代码白名单；以根 strict/status/apply 规划检查、基线摘要及 plan-freeze 收据验证规划输入一致，未完成实现验收前保持其余任务未勾选。

## 2. Implementation

- [ ] 2.1 实现纯领域错误、DTO 兼容规则、章节内容解析、三类别引用替换和阶段二引用收集；以 domain 测试验证错误输入、重复 ID、帧计数、非对象帧、未知字段和序列化分支。
- [ ] 2.2 实现 application、可信 actor 与普通剧集访问端口及错误顺序；以 application 测试验证初始章节缺失、普通访问拒绝、新素材类别/剧集与内容格式优先级。活动会员校验和 fail-closed 配置验证放在 HTTP/boundaries 测试，不让 application 自行查询会员。
- [ ] 2.3 实现最小 Core 持久化、同 Session public notes coordinator 适配和阶段一提交；以 SQLite owner fixture 验证 coordinator 在章节 DML 前完成、章节 UPDATE 仅按主键写 content/updated_at、零行时通用 500 并回滚，以及章节/媒体/private 同事务写入和完整非空非目标行守恒。
- [ ] 2.4 实现提交后阶段二真实重读章节与当前 series_id、三类引用扫描、按候选主键删除和提交后读回；以双阶段 transactions/persistence 测试验证新事务重读、阶段二 DELETE 零行仍警告并提交、阶段一/二提交确认未知及失败后耐久状态、无自动重放和真实顺序读回边界。
- [ ] 2.5 实现 FastAPI 路由和 app factory 登记；以 HTTP/boundaries 测试及获准的 8 个旧工厂/边界测试验证新路由登记、旧断言和冷导入副作用哨兵均保留。

## 3. Source Review

- [ ] 3.1 由 Sol 对冻结代码和真实测试差异进行只读审查；以明确的 accepted 收据及所有必要问题关闭作为完成依据。

## 4. Root Verification

- [ ] 4.1 根运行固定 49 来源、语法、完整后端回归及既有边界检查；以实际命令退出码、日志和代码哈希收据验证当前冻结版本。
- [ ] 4.2 根运行真实本地 HTTP 流程并核对请求顺序、错误响应、两阶段数据结果和实际读回；以 server/driver 顺序相同及完整 SQL/数据快照作为验收证据，不把 SQLite 或方言编译称为 PostgreSQL 并发验证。
- [ ] 4.3 根核对受保护代码/历史/旧文档、精确新增路径、原始差异和自有进程/临时资源清理；以最终 scope/conservation 收据中的各项通过为依据。

## 5. Documentation

- [ ] 5.1 在实施和根验收后按精确 7 路径范围完成事后文档：四份既有文档仅在 EOF 追加、两份新架构/验证文档，以及本 tasks.md 的复选框调整；再由 Sol 只读复核并以文档哈希、范围保全和 accepted 收据验证。
- [ ] 5.2 根针对最终代码、规划、任务复选框和文档版本重跑 strict/status/apply 与 scope guard；以全部实际退出码及 root final acceptance 收据验证本地收敛状态。

## 6. External Review

- [ ] 6.1 完成指定的 GPT-5.6 Sol/xhigh Grillme 离线评审及必要整改；以该指定外审的实际收据和对应版本复核结果验证，不由内部 review 或根回归代替。
