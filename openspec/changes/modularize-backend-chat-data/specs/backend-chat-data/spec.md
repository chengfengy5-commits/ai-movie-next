# Spec Delta

## Purpose

在隔离后端提供完整章节聊天、资产聊天与只读 AI 调用统计，保留固定旧源码的请求、权限、持久化和错误顺序，使消息数据与任务执行、账务及供应商历史形成可独立验证的模块边界。

## ADDED Requirements

### Requirement: Complete existing HTTP surface
系统 SHALL 提供十个既有方法：`GET/POST/DELETE /api/chapters/{chapter_id}/chat-messages`、`PUT /api/chapters/{chapter_id}/chat-messages/{message_id}`、`GET/POST /api/chapters/{chapter_id}/asset-chat-messages`、`PUT /api/chapters/{chapter_id}/asset-chat-messages/{message_id}`、`DELETE /api/chapters/{chapter_id}/chat-messages/single/{message_id}`、`DELETE /api/chapters/{chapter_id}/asset-chat-messages/single/{message_id}` 和 `GET /api/chapters/{chapter_id}/ai-stats`。创建 SHALL 返回 201，读取及更新 SHALL 返回 200，删除 SHALL 返回无响应体的 204；统一工厂 SHALL 登记 52 个方法而保留此前 42 个方法的行为。

#### Scenario: Complete registration and status
- **WHEN** 调用具有必要显式配置的统一工厂，并分别请求本批十个方法
- **THEN** 十个方法按既有路径及状态处理，既有方法继续可用；52 是登记数量，不代表已实际请求全部方法

### Requirement: Preserve request and response values
系统 SHALL 保留既有普通请求校验、额外字段忽略、空字符串及任意字符串角色和模式。普通创建的 `frame_index: int = None` SHALL 允许省略为默认 None，但显式 JSON null SHALL 按旧校验拒绝；合法负整数及既有整数转换 SHALL 保留。资产创建 SHALL 不通过额外 `frame_index` 字段写入帧序号。消息响应 SHALL 保留 nullable 帧、资产及模型字段与 `created_at`，MUST 不引入 `updated_at`、长度或枚举收紧。

#### Scenario: Omitted field differs from explicit null
- **WHEN** 普通创建省略帧序号，或显式传入 null，或传入合法负整数；资产创建附带额外帧序号
- **THEN** 省略采用默认 None，显式 null 返回标准 422，负整数保留；资产创建忽略额外字段，响应帧序号为 null

#### Scenario: Empty strings and ignored extras
- **WHEN** 请求含空 content、空或任意 role/chat_mode、可选模型名和未知字段
- **THEN** 既有可接受字符串原样保留，未知字段被忽略，不增加新的非空或枚举规则

### Requirement: Preserve access and error precedence
系统 SHALL 复用可信身份、会员及剧集访问规则。普通聊天读取、两种创建、三种删除和统计 SHALL 先查路径章节，缺失返回 404“章节不存在”，再验证访问。两个更新 SHALL 先按消息 ID 与路径章节查消息，缺失返回 404“消息不存在”，随后仅在章节存在时验证访问。资产聊天读取 SHALL 仅在章节存在时验证访问，章节缺失不新增 404。两个更新 MUST 不增加资产类型 discriminator；这些已知兼容差异 MUST 不被悄悄改为新的权限策略。

#### Scenario: Message error precedes chapter access
- **WHEN** 两个更新请求的消息与路径章节不匹配，或消息不存在
- **THEN** 返回 404“消息不存在”，不改为先返回章节或剧集访问错误

#### Scenario: Conditional access when chapter is absent
- **WHEN** 资产读取的章节不存在，或两个更新命中一条章节缺失的既有消息
- **THEN** 保留旧条件访问行为；不因本批迁移增加新的章节存在门槛

### Requirement: Preserve list filters and order
普通读取 SHALL 按章节与 chat_mode 筛选，只有 `frame_index` 不为 None 时才加帧筛选，不排除资产消息。资产读取 SHALL 要求 asset_type 与 asset_id 查询参数，并按章节、二者及 chat_mode 筛选。两个列表 SHALL 按 `created_at` 升序，不添加新的排序规则。

#### Scenario: Different message kinds share the ordinary list
- **WHEN** 同章、同模式下同时存在普通和资产消息，且请求未指定帧
- **THEN** 普通列表包含两类命中消息；指定负帧只按该值筛选，资产列表按自身必需参数筛选

### Requirement: Preserve creation target and lock renewal
两种创建 SHALL 按路径章节授权，却将请求 body 的 chapter_id 写入消息；合法不同剧集的真实目标章节 SHALL 保留该错配行为。创建 SHALL 只续期路径章节已有的本人锁，包括已过期本人锁；无锁不创建锁，他人锁不拒绝请求也不续期。续期 SHALL 使用既有章节锁空闲分钟配置及默认语义，保留 acquired_at 和其他锁字段。插入与续期 SHALL 在同一业务事务一次提交后实际重读消息。

#### Scenario: Path and body target differ
- **WHEN** 本人可以访问路径章节，body 指向另一剧集存在且满足真实外键的章节
- **THEN** 消息写入 body 章节，路径章节的已有本人锁按旧规则续期；不新增 body 章节访问校验或跨章禁止

#### Scenario: Lock ownership and expiry
- **WHEN** 创建分别遇到已有过期本人锁、他人锁或无锁
- **THEN** 仅本人锁的 last_active_at 与 expires_at 续期；另两种仍按原规则创建消息，锁行不被新增或修改

### Requirement: Preserve content update and readback
两个更新 SHALL 只赋值 content，并在一次业务提交后实际重读消息。同值赋值 SHALL 不强制消息 UPDATE，不更改 created_at 或其他字段，但 MUST 仍提交并重读；非同值 SHALL 只改变 content。MUST 不添加公开 CAS 字段或资产类型限制。

#### Scenario: Same content remains a source no-op
- **WHEN** 更新已加载消息为相同 content
- **THEN** 消息来源字段不发出净变化 UPDATE，仍一次提交并实际重读；不声称认证维护或整个请求没有数据库活动

### Requirement: Delete messages without deleting task history
批量删除 SHALL 删除路径章节全部模式和两种消息类型，仅在可选帧不为 None 时加帧筛选；零命中仍一次提交并返回 204。普通单删 SHALL 要求 asset_type 为 NULL，资产单删 SHALL 要求其非 NULL，包含空字符串。单删查章与访问验证 SHALL 先于消息 404。删除 MUST 只删除消息，不删除或改写 AI 任务、账单、步骤、队列或供应商历史，不虚构任务到消息的外键级联。

#### Scenario: Bulk deletion and orphan task history
- **WHEN** 批量删除同章不同模式、普通及资产消息，或再次删除零命中集合
- **THEN** 符合可选帧条件的消息删除并返回 204，相关任务及完整账务、队列行保持原值，孤立任务不被清理

#### Scenario: Null differs from empty asset type
- **WHEN** 单删目标 asset_type 分别为 NULL 与空字符串
- **THEN** NULL 仅匹配普通单删，空字符串仅匹配资产单删；错误类别返回“消息不存在”

### Requirement: Preserve statistics grouping and history reads
统计 SHALL 通过任务、消息和章节的既有自然 join 读取当前章节及同剧集历史，仅取 completed、failed。completed SHALL 累加 calls 与 credits，failed SHALL 只累加 failed_calls，忽略其 credit_cost。先按原 model_name 与 status 分组、以组计数降序读取，再将 None 和空模型名合为“未知模型”，保留首次遇到的插入顺序，MUST 不对最终合并列表重排序或收紧原列表响应类型。孤立任务 SHALL 自然不参与统计，MUST 不被改写；已授权章节/剧集统计不按当前 actor 的 task user_id 再筛选。模型名实字“未知模型”与 None/空名 SHALL 合并同一键；合法原负积分保留，不加 clamp；组计数并列不添加 secondary sort。

#### Scenario: Merge order is not final count sorting
- **WHEN** 多个原模型/status 分组按组计数顺序读取，包含 None、空字符串、completed 和 failed
- **THEN** 返回列表按合并键首次出现顺序形成；未知模型合并、失败积分不计入，其他真实模型字符串不被额外归一化

### Requirement: Preserve transaction failures and uncertain outcomes
业务读取 SHALL 不显式提交。创建、更新、删除 SHALL 各有一次业务提交；提交前失败 SHALL 回滚本次消息及锁变更。实际 UPDATE 所期待的既有行在写入时零匹配 SHALL 返回原未捕获数据库失败的通用 500并回滚，MUST 不新增 409 或公开 CAS；批量零命中合法，已加载的无版本单删在并行删除后零匹配 SHALL 保留原提交与 204 行为，不新增失败门槛。提交或提交后重读失败 SHALL 不伪造成功，不自动重试写入；提交后数据已持久化的未知结果 SHALL 能由新显式读取确认，MUST 不承诺异常能撤销已完成的提交。

#### Scenario: Failure before and after commit
- **WHEN** 提交前出现数据库故障，或实际提交后确认/重读抛错
- **THEN** 前者本次消息与锁全部回滚；后者不返回成功且不自动重发，新读取可确认已持久化真值，错误不被解释为可靠未写入

### Requirement: Explicit configuration and bounded acceptance
系统 SHALL 在缺少有效身份解析与业务 Session 配置时对本批方法返回 503 且不创建业务 Session。显式身份解析器 SHALL 优先，既有认证维护独立 Session 与提交顺序 SHALL 保留；此前四个个人记录方法仍要求显式访问策略。应用工厂 SHALL 不读取环境或密钥、不创建 Engine/表、不启动 provider。验收 MUST 区分来源静态复核、本机 SQL/HTTP 结果、工厂登记、历史前端、真实生产及独立外审；本批无冻结聊天 TypeScript consumer，不新增或声称其验收。

#### Scenario: Unconfigured factory remains inert
- **WHEN** 未配置必要端口而创建统一应用并请求本批方法
- **THEN** 工厂无外部启动副作用，请求返回 503 且零业务 Session；既有四方法的配置门槛保持不变
