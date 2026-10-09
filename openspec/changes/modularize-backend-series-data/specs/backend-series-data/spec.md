# Spec Delta

## Purpose

在新目录提供完整剧集、章节与分镜来源数据的十二个既有 HTTP 方法，使其与真实认证、普通剧集访问策略和个人制作状态协调形成可运行的后端闭环。迁移保持固定旧来源的 DTO、权限、错误优先级、持久化阶段和遗留数据行为；不新增生成、计费、播放、上传或部署业务。

## ADDED Requirements

### Requirement: Complete HTTP Surface and Configuration

系统 SHALL 提供下列十二方法，统一 factory 总共提供十一认证、四既有个人业务和十二来源数据方法，共二十七方法；不新增实验或替代 endpoint。

| Method | Path | 正常状态 |
| --- | --- | --- |
| GET | /api/series | 200 |
| GET | /api/series/{series_id} | 200 |
| POST | /api/series | 201 |
| PUT | /api/series/{series_id} | 200 |
| DELETE | /api/series/{series_id} | 204 |
| GET | /api/series/{series_id}/chapters | 200 |
| PUT | /api/series/{series_id}/chapters/reorder | 200 |
| POST | /api/series/{series_id}/chapters | 201 |
| PUT | /api/chapters/{chapter_id} | 200 |
| PUT | /api/chapters/{chapter_id}/delete-frame | 200 |
| DELETE | /api/chapters/{chapter_id} | 204 |
| GET | /api/series/{series_id}/storyboard-assets | 200 |

新十二方法 MUST 使用可信身份和同一业务 Session 的真实 SQL 访问读取，缺少 Session factory 或身份 resolver 时返回 503 且不创建 Session。显式 resolver SHALL 优先于认证 runtime 自动 resolver。认证维护先在独立认证 Session 完成提交，再创建业务 Session。原四方法的显式 series_access_policy 配置条件 MUST 保持，不能因接入新十二方法而默许其访问。factory/冷导入 SHALL 不读取环境、秘密或文件，不创建 Engine/schema，不启动供应商。

#### Scenario: Missing Configuration Fails Closed
- **WHEN** 调用任一新方法而 Session factory 或可信身份 resolver 未接线
- **THEN** 返回 503，Session 创建计数为零，既有四方法仍遵守自身配置条件

#### Scenario: Explicit Resolver Remains Authoritative
- **WHEN** 同时提供显式 resolver 和认证 runtime
- **THEN** 新旧业务使用显式 resolver，factory 保持二十七方法且不增加启动副作用

### Requirement: Legacy DTO and Response Compatibility

系统 SHALL 保留固定来源的 Pydantic 类型、可空默认值、类型转换、extra ignore 和标准 422，不添加严格整数、ID 格式或前端 safe-integer 限制。SeriesCreate/Update 的 name、description、image_url、style_prompt_id 和 ChapterCreate/Update 的 title、content、raw_content、order SHALL 保持原输入语义；Update 中缺失或 null 不修改，空字符串和空 content 数组可以修改。ChapterCreate 的 SceneFrame 只保留 text 与可空 character/scene/prop 字符串，ChapterUpdate 的 dict 帧保留未知字段和列表引用。ChapterResponse 的 lock 默认 null，只有章节列表填充有效锁；分镜素材目录返回原 image_url，不新增签名或媒体请求。

#### Scenario: Create and Update Frames Retain Different Shapes
- **WHEN** 创建章的类型化帧含额外 storyboard 字段，而更新章的 dict 帧含相同字段
- **THEN** 创建输入按旧 DTO 忽略额外字段，更新输入保留原字段供后续来源同步

#### Scenario: Null and Empty Values Remain Distinct
- **WHEN** 更新请求分别省略字段、传 null、传空字符串或空 content 数组
- **THEN** 前两者不修改，后两者按原行为写入，非法 DTO 仍返回标准 422

### Requirement: Series Visibility and Projection

剧集列表 SHALL 返回本人剧集或本人所属团队剧集，按 updated_at 降序。列表的 owner/team/claimed username、avatar 和 can_enter MUST 保持旧读取与默认规则：team_name 只来自本人的团队集合，未知认领者姓名为空字符串；列表 can_enter 由未认领/本人认领或团队 owner/enter_claimed 决定。详情、创建和更新响应 SHALL 保留其未填认领字段而使用默认 null/can_enter=true 的差异，不把列表投影套给所有响应。style_prompt_id SHALL 优先查用户提示配置（无新归属筛选），否则查启用的系统配置，保持 user_prompt/system_prompt 回退、owner username/系统标签和无匹配 null，不调用 provider。

#### Scenario: List and Detail Keep Their Existing Difference
- **WHEN** 同一可访问剧集存在其他人的认领
- **THEN** 列表显示原认领与 can_enter 投影，详情仍保持原响应默认，不新增认领字段修复

#### Scenario: Prompt Configuration Is a Database Projection
- **WHEN** style_prompt_id 指向用户配置、系统配置或不存在的配置
- **THEN** 分别按原优先级返回原内容与归属标签，未命中返回 null，不发外部请求

### Requirement: Access Priority and Delete Privilege

普通访问 SHALL 保持先查剧集、团队认领限制、作者短路、最终成员资格分阶段重读的规则；认领本人不独立授予访问权，不增加 superuser 普通访问旁路。权限列表含不可 hash 元素时 MUST 保持 TypeError 全空语义，任意成员角色均可拥有原权限。查章方法 SHALL 先查章返回原 404，再校验所属剧集；普通剧集方法先返回原剧集 404。认领拒绝 MUST 保留动态用户名（缺用户为空名）的原 403 文案。
删除剧集 SHALL 使用独立原规则：查剧集后作者直接允许，非作者读取同业务 Session 的可信 users.is_superuser，再检查团队 membership.role=owner 或 delete_series 权限；无权限返回原“没有权限删除该剧集”。系统 MUST 不信任 body、header 或未验证声明中的 superuser。

#### Scenario: Claimed Self Is Not Membership
- **WHEN** 非作者认领了剧集但没有团队成员资格
- **THEN** 普通访问仍拒绝；作者和阶段重读的当前成员规则保持

#### Scenario: Delete Privilege Does Not Expand Ordinary Access
- **WHEN** 非作者可信数据库用户为 superuser，或成员具有 delete_series 权限
- **THEN** 可以按原规则删除剧集，但该资格不改变其他普通访问；伪造输入 flag 无效

### Requirement: Chapter Ordering and Lock Semantics

章节列表 SHALL 按 order 降序再 created_at 降序。创建时 order=0 使用同剧集 max(order) 或零再加一；其他整数保留。content 为真时使用类型化帧，否则 raw_content 为真时按原换行/trim/drop-empty 生成帧，不调用 AI。重排 SHALL 只更新同剧集匹配的 raw dict 条目，保持遗漏、重复、负序号与无新增唯一顺序政策。
只有更新章节 SHALL 续期本人持有的锁，不获取锁也不因别人锁新增拒绝；分钟数保留首个配置的 truthy 值或十五分钟回退。章节列表只有 expires_at>now 的锁有效，等于 now 已过期，保持用户名、avatar、is_mine 和时间响应。

#### Scenario: Existing Order and Raw Content Rules
- **WHEN** 创建 order=0 或负 order 的章，或者重排请求遗漏和重复某章
- **THEN** 分别执行原自动顺序或保留负值，重排只作用于匹配章并保留原重复条目结果

#### Scenario: Lock Renewal Is Limited to Chapter Update
- **WHEN** 更新本人持锁章或执行创建、重排、删镜头
- **THEN** 只有前者按原配置续期，不新增锁获取或其他持有者拒绝

### Requirement: Staged Source Persistence

系统 SHALL 保持旧提交边界：创建章首次插入提交后执行来源素材同步并再次提交；更新章第一阶段提交字段/持有者锁，指定非 null content 时再提交素材同步和聊天映射阶段；删镜头先提交内容/聊天删除与移动，再提交素材同步；删除章先提交章内删除，收集引用集合非空时再提交 orphan 清理；创建/更新/删除剧集和重排保持各自原提交边界。
每个阶段失败 MUST 回滚该阶段，不撤销已完成的前阶段。提交后 acknowledgement 失败 MUST 返回未知失败，不伪造可信成功、不自动重试；返回的成功数据对应本次完成的业务阶段，不额外增加不必要的提交。

#### Scenario: Later Stage Failure Retains Earlier Commit
- **WHEN** 创建或更新章的来源同步阶段失败，或删镜头第二阶段失败
- **THEN** 已提交的第一阶段仍持久化，失败阶段回滚，客户端不收到伪造完整成功

#### Scenario: Uncertain Commit Is Not Retried
- **WHEN** 真实提交完成后 acknowledgement 抛错
- **THEN** 不自动重放写入，显式新请求可读取持久化真值，记录此实验不等同 PostgreSQL 故障证明

### Requirement: No-op Writes Keep Source Timestamps

系统 SHALL 比较当前字段，仅对实际变化执行来源 UPDATE，保留旧 ORM 无 dirty UPDATE 时不触发 onupdate 的行为。空 PUT 和同值 PUT 仍保持原提交及后续指定 content 的阶段，但不刷新 series/chapter/已匹配 storyboard 的 updated_at；只有持有者锁续期也不刷新章时间。同字节 content 不触发媒体协调初始化，素材位置/name/description 全相同也不产生 UPDATE。

#### Scenario: Same Values and Lock-only Renewal
- **WHEN** 空 PUT、所有非 null 字段与当前相同、content 序列化字节相同或仅本人锁续期
- **THEN** 保留原 commit 边界和必要同步流程；当来源字段/content 同值且同步未产生真实资产 new/delete/变化时，来源 updated_at 保持且不强制初始化媒体状态

### Requirement: Storyboard Identity and Source Synchronization

来源同步 SHALL 从 chapter_id 资产目录的初始快照核对每帧原 storyboard[0]，不新增 series_id 过滤。已匹配 ID 保留原引用数组，更新 frame_index/name/description；重复已匹配 ID 复用同资产且后出现位置覆盖，不偷偷去重。缺少、悬空或不能匹配的首引用按原规则创建新稳定 ID；原不可 hash 引用继续失败，不能经新验证改成合法替代。新资产 insert/flush 的媒体协调发生在帧引用改写之前的持久化章内容上；最终内容引用改写与删除未引用初始资产作为后续真实阶段协调。分镜目录读取 SHALL 按 series_id、可选 truthy chapter_id 和 frame_index 查询，并保持 raw URL。
系统 MUST 不以视频、preview、个人认可或媒体 CAS 作为来源编辑资格。

#### Scenario: Duplicate and Mismatched Series Assets
- **WHEN** 同章目录含 series_id 不匹配的已引用资产，或者多个帧重复引用同一初始资产
- **THEN** 同步仍按 chapter_id 匹配，保留重复引用及原位置结果；目录读取仍按其原 series_id 条件

#### Scenario: New Asset Flush Precedes Reference Replacement
- **WHEN** 一个既有章有多个缺少或悬空的首引用
- **THEN** 各新资产阶段以当时数据库章内容协调，最后引用替换再协调，不能仅按提交次数合并媒体状态

### Requirement: Personal Media State Reconciliation

对既有章实际变更的媒体相关来源 DML，系统 SHALL 在同一业务 Session 先基于数据库当前内容与资产建立 base，再应用 pending overlay 并协调，之后执行来源 DML。无媒体记录时保留旧 base 初始化 R1 与实际变化 R2；跨用户认可撤销、媒体 revision/删除墓碑和各用户 private revision MUST 保持原阶段语义。新章第一次插入与整章/整剧删除 SHALL 保持原 hook 排除规则，删除由真实外键级联承担。
协调 MUST 保留未知便签字段、note、resume、无关用户/章/资产，且每真实阶段每受影响用户只推进一次。text、类别、顺序、素材位置/描述和临时签名 URL 的非媒体变化不推进媒体版本；真实原图/视频变化、删除/恢复保持单调墓碑而不恢复旧认可。协调失败/CAS 零匹配 SHALL 回滚该阶段；协调器不自行 commit/rollback/close。

#### Scenario: Legacy Approval and Uninitialized Media
- **WHEN** 无媒体状态的既有章中，已认可资产的实际媒体身份变化
- **THEN** 先形成旧来源 R1，再形成变化 R2，撤销同章所有受影响用户认可，同时保留 note、resume 和未知字段

#### Scenario: Delete Frame and Nonmedia Changes
- **WHEN** 删除一帧后再次同步，或仅修改 text、类别、顺序、描述和签名参数
- **THEN** 删除第一阶段产生墓碑/撤认，后续无重复推进；非媒体变化不推进媒体版本

### Requirement: Chat Mapping and Task Relationship Compatibility

更新章的聊天映射 SHALL 保持原首 storyboard ID 到旧/新位置的映射、重复最后覆盖、仅映射非空时处理 indexed chats、null frame_index 保留和空映射不清旧聊天的行为。删镜头 SHALL 删除对应 indexed chats 并移动后续索引。两者 MUST 不顺带删除 AITask，AITask.message_id 是无 ForeignKey 的 String(36)，不能在新表投影或测试 schema 虚构 chat FK。
只有整章/整剧删除 SHALL 按关联聊天 message_id 显式删除对应任务，并执行原章/剧范围的 locks、rough drafts、storyboard、canvas、chat、notes/media 和其他资产清理边界。

#### Scenario: Single Frame Removal Leaves Existing Task
- **WHEN** 删除或重排导致 indexed chat 删除，而有 AITask.message_id 指向该聊天
- **THEN** 聊天按原规则变动，任务仍可孤留，不因虚构外键导致失败或级联

#### Scenario: Empty Mapping Does Not Delete Chats
- **WHEN** 更新内容没有非空首 storyboard 映射
- **THEN** 不清除旧聊天；非空映射才按原规则移动/删除 indexed chats

### Requirement: Deletion Boundaries and Financial Foreign Keys

章删除 SHALL 按 chapter_id 清理分镜资产，即使其 series_id 不匹配；角色/场景/道具 orphan 清理只处理原收集 raw 引用、同剧集归属且未被剩余同剧集章引用的对象。整剧删除 SHALL 保留原额外 series 范围资产和章范围清理。
系统 MUST 保留 billing_units.task_id 等真实账务外键而不添加 CASCADE 或删除账本/steps/credit history；受账单保护的任务删除失败 SHALL 返回失败并回滚当前删除事务，不把金融历史损失当兼容迁移。

#### Scenario: Billing Foreign Key Rejects Deletion
- **WHEN** 整章或整剧的待删 AITask 被真实 billing_units 外键引用
- **THEN** 删除失败且事务回滚，任务、章/剧和完整账务行保持，不静默清账

#### Scenario: Mismatched Asset and Orphan Cleanup
- **WHEN** 删章存在 chapter_id 匹配但 series_id 不匹配的 storyboard，以及仍被其他章引用的角色/场景/道具
- **THEN** 前者按章清理，后者和不同剧集资产保持，不扩大清理范围

### Requirement: Evidence and Isolation Boundaries

本批 MUST 保全基线受保护路径、旧历史正文和固定 commit 来源字节；旧共享 checkout 的实际 HEAD/status/tracked/index 只完整记录，不要求其他会话保持其状态恒定。生产源码 MUST 不导入旧 app、读取环境秘密、创建 schema/Engine 或调用 queue/provider。验收 SHALL 分开记录静态来源复核、实际 SQLite/ASGI、根完整回归、真实 loopback 与冻结 TypeScript parser 消费。PostgreSQL 只做 SQL 编译，本地真实 JWT 不等于生产认证；真实 PostgreSQL、SMTP/provider、整套前端及指定外审尚未执行，不得冒充完成。

#### Scenario: Local Evidence Does Not Claim Deployment
- **WHEN** 临时 SQLite、真实本地 HTTP 和冻结 parser 验收通过
- **THEN** 只记录对应本地模块证据与自有资源清理，保留指定 GPT-5.6 Sol/xhigh Grillme 独立待办，不宣称上线或全项目完成
