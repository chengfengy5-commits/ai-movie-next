# Spec Delta

## Purpose

在隔离的新后端中提供可独立验证的个人粗剪读写业务，保留既有章节与用户私有草稿契约、完整镜头集合和并发版本规则。该模块通过明确的安全接线与事务边界为后续迁移提供基础，不把测试身份或本地数据库验收当作真实认证、PostgreSQL 或生产就绪。

## ADDED Requirements

### Requirement: Explicit trusted security boundary

系统 SHALL 只从注入的可信身份和有效会员边界获取当前用户，不接受请求正文或 query 指定草稿持有人；剧集访问校验 MUST 在同一粗剪事务内、源数据及私有草稿读取之前完成。安全边界 SHALL 继承接入策略返回的 401/403/404，不提供默认放行策略。

#### Scenario: Incomplete composition
- **WHEN** 一个合法请求访问未配置数据库、可信身份/会员或剧集访问端口的模块
- **THEN** 模块返回 503，且不创建数据库 Session，也不读取或写入草稿

#### Scenario: Denied access
- **WHEN** 身份/会员端口拒绝当前请求，或同事务剧集访问策略拒绝当前用户
- **THEN** 返回相应拒绝，后续源与私有草稿不读取，任何草稿均不改变

### Requirement: Legacy read projection

系统 SHALL 保留 `GET /api/chapters/{chapter_id}/rough-cut` 的完整响应。章节不存在返回 404；空内容产生空镜头，损坏 JSON 或非数组内容返回 422。稳定镜头身份仅取每帧 storyboard[0]，该值必须是非空字符串、在全章唯一并存在本章素材；系统 MUST 不按位置兜底、不修复旧记录、不新增剧集字段过滤。

#### Scenario: No saved draft
- **WHEN** 当前用户尚无此章草稿
- **THEN** 返回 revision 0、saved false；有效身份默认纳入，无视频不阻止纳入；无效身份以空 asset_id、排除状态显示

#### Scenario: Saved draft with source changes
- **WHEN** 已保存顺序中含当前已移除 ID，或当前源新增镜头
- **THEN** 保留匹配的已保存顺序与 included，新镜头追加并排除且 pending true；保留旧移除列表的重复 ID，不用新规则悄悄清理历史数据

#### Scenario: Read has more than five hundred frames
- **WHEN** 合法当前源超过 500 帧
- **THEN** GET 仍返回完整投影，不套用 PUT 的镜头上限

### Requirement: Strict complete write contract

系统 SHALL 保留同路径 PUT 的严格正文：顶层只有非负严格整数 expected_revision 和最多 500 项 frames；每项只有 1 至 36 Unicode 码点的原始 asset_id 和严格布尔 included。系统 MUST 不 trim ID、不强制 UUID、不新增 JavaScript 安全整数上限，也不引入媒体 revision CAS。只有完整当前稳定 ID 集合无重复且预期版本匹配时才能保存，JSON 只持久化 asset_id/included。

#### Scenario: First empty save
- **WHEN** 尚无草稿的合法空章节收到 expected_revision 0 和 frames []
- **THEN** 保存本人 R1 空草稿并返回 saved true，不要求 dirty、视频或个人制作记录

#### Scenario: Invalid body
- **WHEN** 正文含多余键、非严格整数/布尔、无效 ID 长度，或 frames 超过 500
- **THEN** HTTP 返回 422，不写草稿；请求列表超限与当前源超限的 413 分开记录

#### Scenario: Reordered complete source
- **WHEN** 合法正文提交当前源的完整 ID 集合与顺序/纳入状态
- **THEN** 仅改变本人粗剪，返回本次 R+1、所提交有序 ID 和 included，pending false 且移除列表为空；文本、位置、预览与缺失原因采用本次当前源，不改写源、便签或认可状态

### Requirement: Compatible error ordering

业务执行 SHALL 保留以下优先级：查章不存在、剧集访问、当前源内容解析、本人草稿 revision 冲突、源缺少唯一稳定 ID、源帧数超过 500、提交集合不完整/重复、数据库写入。损坏内容解析 MUST 先于草稿版本冲突；源缺身份 MUST 先于源数量上限。模块不改变框架的正文校验层顺序。

#### Scenario: Bad source and stale revision
- **WHEN** 当前源内容损坏且提交 revision 同时陈旧
- **THEN** 返回源内容 422，而非提前返回 409

#### Scenario: Oversized valid source
- **WHEN** 请求正文合法且不超过 500 项、版本匹配，源有超过 500 个有效稳定镜头
- **THEN** 返回 413；若源同时含无效身份，则按旧顺序先返回 422

### Requirement: Atomic private persistence

系统 SHALL 以 chapter_id+可信 user_id 定位草稿；PUT 在一个事务中锁定章节、验证权限与当前源、完成首次插入或 revision 条件更新，再成功提交。首次插入受私有唯一键约束，后续 UPDATE MUST 仅在旧 revision 匹配时写入；冲突返回 409。其他数据库错误 MUST 尝试回滚且不自动重试写入、不一概误标版本冲突；提交前失败不得留下变更，提交结果不确定时不得承诺数据库已撤销。成功响应 SHALL 属于本次 R+1，不在 commit 后重读并混入后继 R+2。

#### Scenario: Same chapter with two users
- **WHEN** 两个获准用户各自保存同一章节
- **THEN** 草稿、版本和读取结果相互隔离，不能从请求输入选择另一持有人

#### Scenario: Stale concurrent write
- **WHEN** 不同数据库连接读取同一旧版本后尝试写入
- **THEN** 条件更新只允许其中一个旧版本写入，另一写入返回冲突而不覆盖新结果

#### Scenario: Unrelated storage failure
- **WHEN** flush 或 commit 在数据库完成提交前因非私有唯一竞争的错误失败
- **THEN** 本次操作回滚，不报告成功或自动重试，既有草稿保持原状

#### Scenario: Commit result cannot be confirmed
- **WHEN** 数据库已提交后确认发生错误，导致调用者无法确认结果
- **THEN** 不报告可信成功、不自动重发写入，也不承诺回滚已撤销；后续显式读取能够核对实际 revision 与草稿

### Requirement: Integration and lifecycle limits

模块 SHALL 只提供本批两个粗剪业务方法，不隐式启动旧后端、数据库 schema 创建/反射/迁移、任务恢复、Worker 或媒体请求。GET 粗剪事务不显式 commit；请求结束关闭 Session，PUT 仅由应用事务边界 commit。真实认证策略未来可能维护会话，不能因此称全部 HTTP 请求是数据库纯读。

#### Scenario: Construct or import module
- **WHEN** 导入新包或创建未接线 app factory
- **THEN** 不读取 .env、不创建全局数据库 engine、不创建 Session、不注册其他业务路由或执行任务恢复

#### Scenario: Acceptance records
- **WHEN** 本批记录本地模块、SQLite、ASGI 或 loopback HTTP 验收
- **THEN** 明确测试身份/权限替身、SQLite 与 PostgreSQL 行锁/真实身份/生产验收的区别，并单列未执行的指定 Grillme 外审
