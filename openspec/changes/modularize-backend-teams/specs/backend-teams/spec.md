# Backend Teams Delta Spec (Draft)

本文件为本地规划候选；所有 SHALL/MUST 均为兼容性契约要求，须经 root 实际基线、普通 OpenSpec CLI 和冻结批准后实施；规范正文不表示已实现或已验收。

## ADDED Requirements

### Requirement: Complete existing route surface

系统 SHALL 迁移下表25个来源方法，保留方法、路径、成功状态和业务顺序。不得将13、7、5任一分组或 GET `/api/teams/my` 称完整团队迁移。旧来源为固定 `23403806898550a7668a6ee7c0c457315655c39b`。

| 分组 | 方法与路径 | 成功状态 | 来源窗口 | 必须保留的业务顺序 |
| --- | --- | ---: | --- | --- |
| management | `POST /api/teams` | 201 | `teams.py:62–92` | config→owner-created-count limit400→trim name→team INSERT flush→owner member INSERT→commit1；name原长1–255在trim之前验证，空白可落空字符串 |
| management | `GET /api/teams/my` | 200 | `teams.py:96–117` | current actor memberships→team ids→teams→各队成员count；无membership即[]；不按Team.owner_id扩充，不加排序；6字段id/name/owner_id/created_at/member_count/my_role |
| management | `GET /api/teams/{team_id}` | 200 | `teams.py:121–154` | team404先于member403；成员→用户批查；缺用户username空/avatarNone；permissions按原list保序保重复；my_role/my_permissions来自本人row |
| management | `PUT /api/teams/{team_id}` | 200 | `teams.py:158–170` | team404→member/owner403；body.name truthy才trim赋值；null或empty body仍commit1；Team无updated_at；同值赋值不发明源UPDATE |
| management | `DELETE /api/teams/{team_id}` | 200 | `teams.py:174–194` | team404→member/owner403→先bulk Series.team_id=None→按team归属查锁→bulk删invite/member→ORM删team→commit1；claimed_by/at不清；实际锁清理时点反例见criticalFacts |
| management | `POST /api/teams/{team_id}/leave` | 200 | `teams.py:200–215` | team404→无本人member400→owner400→删本人membership→按team及本人释放锁→commit1；不清认领 |
| management | `DELETE /api/teams/{team_id}/members/{user_id}` | 200 | `teams.py:219–245` | team404→remove_members gate→自己400→target404→targetowner400→targetadmin且操作者非owner403→删除target+释放target锁→commit1；不清认领 |
| management | `PUT /api/teams/{team_id}/members/{user_id}/role` | 200 | `teams.py:249–279` | team404→owner gate→self400→role.strip.lower且仅admin/member否则400→target404→owner400→同role直接返回无commit；变admin默认[view_tasks,manage_invites]，变member permissions=None，commit1 |
| management | `PUT /api/teams/{team_id}/members/{user_id}/permissions` | 200 | `teams.py:283–306` | team404→owner gate→target404→targetowner400→非admin400→保序去重合法key；空输出存NULL，commit1；bare list中不可hash项会原样TypeError500，不悄加强DTO |
| management | `POST /api/teams/{team_id}/invites` | 201 | `teams.py:312–342` | team404→manage_invites gate→falsey-default config→count1..5；同一个now；random8 chars，DB collision循环；pending INSERT在autoflush=False下并非查重；code列非unique；commit1 |
| management | `GET /api/teams/{team_id}/invites` | 200 | `teams.py:346–378` | team404→manage_invites gate→active且expires_at<now的该team invites改expired并仅有行时commit1→created_at desc全状态列表；GET可写，绝非只读slice |
| management | `DELETE /api/teams/{team_id}/invites/{invite_id}` | 200 | `teams.py:382–400` | team404→manage_invites gate→id+team匹配404→used400→其他状态置expired→commit1；不硬DELETE |
| management | `POST /api/teams/join` | 200 | `teams.py:407–448` | 10/hour remote-IP/endpoint wrapper；code.trim.upper→无invite/nonactive400→expires<now时持久expired commit1再400→team404→已成员400→member-role-only joined limit400→allrole team人数limit400→member INSERT+invite used fields→commit1；没有invite CAS/rowlock |
| series | `GET /api/teams/{team_id}/series` | 200 | `teams.py:469–535` | team404→member403，无ordinary claimed-series gate；page max1/page_size clamp1..50,total_pages至少1；updated_at desc分页，含作者/认领人头像+章/四类素材count |
| series | `POST /api/teams/{team_id}/series` | 201 | `teams.py:539–570` | team404→member403→trim name/保留optional值→可选claim当前actor+now→Series INSERT→commit1→实际refresh；响应只有id/name/team_id/user_id/style_prompt_id |
| series | `POST /api/series/{series_id}/share` | 200 | `teams.py:574–596` | series404→必须作者403→targetteam404→member403→team_id替换；claim true才覆盖claim否则原claim保留；无论归属是否同值都release全部该series章锁→commit1；不调用ordinary claim gate |
| series | `DELETE /api/series/{series_id}/share` | 200 | `teams.py:600–614` | series404→必须作者403→team_id=None→释放该series全部章锁→commit1；不查team成员，不清claimed_by/at |
| series | `POST /api/teams/{team_id}/series/{series_id}/claim` | 200 | `teams.py:633–648` | team404→member403→series id+team404→被其他人claim400→即使已本人claim仍claimed_at=now→commit1；源码无CAS/锁 |
| series | `DELETE /api/teams/{team_id}/series/{series_id}/claim` | 200 | `teams.py:652–671` | team404→member403→series id+team404→重新查membership；本人claimer/作者/memberroleowner可操作，否则403先于无claim早返；有claim清两列commit1，无claim允许者直接返回不commit |
| series | `POST /api/teams/{team_id}/series/{series_id}/transfer` | 200 | `teams.py:675–697` | team404→member403→series id+team404→重新查membership；claimer/作者/captain gate→接收人等本人400→非member400→claim两列赋新值+now→commit1；可由作者/captain在无claim时转交，不加入SeriesAccess gate |
| reporting | `GET /api/teams/{team_id}/members/{user_id}/tasks` | 200 | `teams.py:701–720` | view_tasks permission先行，无get_team404；targetmembership404→按target user全部tasks分页created desc、无team/series过滤/无page clamp→消息/章节/素材名称补全→target总UserCredit或0；只读，不扣点 |
| reporting | `GET /api/teams/{team_id}/usage` | 200 | `teams.py:782–872` | view_usage permission→team404→日期解析无效视为无筛选，aware转UTC naive→该team series updated desc→task/message/chapter innerjoin/status completed或failed/time闭区间→model/user/status分组；成功次数及credits，失败仅failed_calls；零用量series不输出；chapter_count按成功/失败AI涉及章去重 |
| reporting | `GET /api/teams/{team_id}/series/{series_id}/usage` | 200 | `teams.py:876–956` | view_usage permission先于series id+team404；join/日期/status同汇总；chapters order desc，无用量章排除；series_total按row再聚合；不做ordinary claimed-series gate |
| reporting | `GET /api/teams/{team_id}/usage/model` | 200 | `teams.py:960–1037` | view_usage permission→team404→原model_name精确SQL相等（不先normalize）→该team tasks innerjoin/status/time→成功credits/失败仅次数；by_series/by_member按(-calls,-credits)稳定sort，无额外tie排序 |
| reporting | `GET /api/teams/{team_id}/usage/export` | 200 | `teams.py:1041–1170` | view_usage permission→team404→series空即简短early response（不含model_order）→日期/status/join分组→model falsey=未知模型，用户/章名回退→rows按series_name/chapter_title/username/model_name排序，members按(-calls,username)；model类型查全库AITask原model_name IN(已归一化all_models)的DISTINCT对，不将SQL NULL/空串归一化入typemap，无team/time/status限定且setdefault实际首条；model_order按type priority默认9及(name or '').lower()稳定排序，member.models独立普通sorted；API返JSON，旧浏览器生成xls |

#### Scenario: Registry and runtime evidence are distinct
- **WHEN** 新路由接入统一工厂
- **THEN** 登记总方法数 SHALL 为57+25=82，原57业务断言保留；真实TCP数量、成功25方法、失败案例、认证请求 SHALL 按实际driver/server有序记录，不由登记数推导。

### Requirement: Trusted identity, shared policy and query priority

所有25方法 MUST 使用可信活动会员。auth维护先在其Session完成，业务UoW接收请求工厂创建的独立Session，全部团队许可与业务SQL在同一业务Session完成。缺 resolver/business配置返回503且不创建业务Session；join还需要显式quota adapter。不得信任body/header角色、Team.owner_id代替成员owner角色或superuser绕过。

#### Scenario: Ordered team permission checks
- **WHEN** 详情/管理/剧集调用团队许可
- **THEN** SHALL 按表中先team404、成员/角色或指定权限顺序查询；reporting维持permission先行以及各自team/series/target404顺序。require_team_permission来源的两次membership查询仍执行，不以缓存省略查询；同一事务已加载字段保持来源ORM快照语义，实际commit/refresh后读取新值。

#### Scenario: Permission list is not a set response
- **WHEN** 详情读取permissions
- **THEN** 六合法key筛选 SHALL 保序保重复；非法JSON、非list或任意不可hash成员使整次读解析为[]。owner恒允许，其余角色按权限判断，不额外要求admin。
- **WHEN** 权限PUT接收bare list
- **THEN** 合法key保序去重，空列表存NULL，不可hash项仍通用500；不得收紧为List[str]或改现有SeriesAccess实现。

### Requirement: DTO, local 422 and explicit configuration

九个DTO SHALL 保留默认extra-ignore：TeamCreate/name1..255；TeamUpdate可选name同长度且null忽略；InviteCreate/count默认1且1..5；JoinRequest/code6..20；RoleUpdate任意str再strip/lower业务判断；PermissionsUpdate裸list默认[]；TeamSeriesCreate/name1..255、可选description/style_prompt_id/image_url、claim默认false；ShareSeriesRequest/team_id非空str与claim默认false；TransferRequest/user_id非空str（min_length=1）。不新增UUID/枚举/严格bool/trim前置验证。

#### Scenario: Source trimming and first validation message
- **WHEN** name由空白组成但原长度合法
- **THEN** trim后空串 SHALL 按来源保留；null/未提供团队更名仍一次commit和实际读回，显式空串仍原DTO422。
- **WHEN** team路由验证失败
- **THEN** 仅这些路由 SHALL 将第一条msg移除 `Value error, ` 前缀后返回422 `{detail: msg}`；不改变旧方法标准验证格式。

#### Scenario: Falsey config and no ambient resources
- **WHEN** 获取system_configs第一行的五团队配置
- **THEN** SHALL 使用原 `value or default` 的3/3/20/24/15，0/None回退、负值保留；不读取env/生产凭据，不在导入或默认工厂创建Engine/schema/反射/provider/quota实例。

### Requirement: Join quota and invitation semantics

仅 POST `/api/teams/join` SHALL 受显式本进程10/hour、可信remote-IP+pathname quota限制；其余24方法不得因旧default60/minute附加限制。DTO及auth/会员通过后、邀请码查询前命中额度；非法DTO/身份拒绝不计，合法业务失败计。各(remote-IP,pathname)首个合法hit SHALL 以当前epoch建立expiry=epoch+3600，不按整点小时划桶；后续成功、合法业务失败及超额hit不续期，now>=expiry才开始新窗。429 SHALL 为 `{error: "Rate limit exceeded: 10 per 1 hour"}`，不新增来源未启用的限流headers。

#### Scenario: First-hit window and endpoint isolation
- **WHEN** 第一个合法hit在epoch100，随后达到10次，再在epoch3600、3699.999与3700检查，并分别使用另一个IP或pathname
- **THEN** SHALL 在整点3600仍受同一未到期窗口限制、到期前仍429、3700开始新窗；成功/合法失败/超额不移动3700到期点。DTO/auth拒绝不命中额度；另IP/path独立，其它24方法不限额，429内容和无额外headers保持。

#### Scenario: Code generation and real constraints
- **WHEN** 一次生成1..5邀请码
- **THEN** SHALL 共享本次now，保留8位 `ABCDEFGHJKLMNPQRSTUVWXYZ23456789` 和已提交数据库碰撞循环；autoflush=False下本批待提交INSERT不参加查重，允许本批重复code。code仅索引，不加UNIQUE/CAS；成员(team,user)真实UNIQUE保留。

#### Scenario: Maintenance writes and expired joins
- **WHEN** 已授权GET邀请发现active且expires_at<now
- **THEN** SHALL 只对这些行计划expired并commit一次，再查询全部状态created_at降序；无过期行不commit。
- **WHEN** 有效格式join找到已过期active邀请码
- **THEN** SHALL 先持久expired并commit一次再400；此400不是授权拒绝零写。成功则member INSERT和used字段一次提交，过期判断与used_at按原各自clock调用；不新增人数锁/邀请码消费原子策略。

### Requirement: Source assignments and exact transaction behavior

Core适配 SHALL 将已加载实体首次观察字段作为赋值净变更基准；同值不发源UPDATE，firstA/requestB/laterB仍保留原SET。源码autoflush=False的ORM赋值、待提交INSERT/DELETE应保留至对应flush/commit边界；显式bulk UPDATE/DELETE即时执行。每个方法按表中commit次数及必要读回，不因空请求省略commit，也不因读回新增commit。

#### Scenario: Dirty update, bulk zero and unversioned delete zero
- **WHEN** 实际净变更的team/member/invite/series PK UPDATE匹配0行
- **THEN** SHALL 通用500并rollback当前事务，不发明409/CAS；同值无UPDATE不得制造0行冲突。
- **WHEN** 来源bulk UPDATE/DELETE匹配0行或无version的ORM DELETE匹配0行
- **THEN** SHALL 保留bulk0合法以及ORM DELETE0警告后继续commit/message成功，不升级为失败。INSERT UNIQUE/FK/commit异常不可伪成功或重试。

#### Scenario: Durable commit and actual readback
- **WHEN** create/rename/role-change/join/create-series/share/claim按来源在commit后访问过期实体或显式refresh
- **THEN** SHALL 在原UoW真实PK读回，缺行/读失败返回500，已提交数据保持durable且不重试；permissions/transfer意图echo、仅message操作不加无来源SELECT。同角色role和允许者无claim的unclaim保留早返零commit。
- **WHEN** commit已实际完成后ack失败
- **THEN** SHALL 返回500，只rollback当前事务；另一个真实GET确认持久状态，无自动replay。

### Requirement: Team series, locks and source compatibility defects

七个series接口 SHALL 保留单事务metadata行为，不写章节content、素材、媒体状态或私人认可，不引入普通claimed-series访问gate。share/unshare即使同归属也清对应series章锁；离队/移除只清该用户在该team现series范围的锁；上述操作不额外清claimed_by/at。

#### Scenario: Dissolution order remains observable
- **WHEN** 解散团队
- **THEN** SHALL 先bulk清Series.team_id（保留来源onupdate效果），再用当前team归属查询series释放锁，再删除invite/member/team并commit；不得预取series IDs修复原先查不到的锁。非目标锁/认领、所有账本/任务/媒体/私人行守恒。

#### Scenario: Source claim rights and list output
- **WHEN** 普通团队成员列剧或claim，或作者/claimer/队长unclaim/transfer
- **THEN** SHALL 使用来源团队许可、目标匹配和重复membership查询；不让SeriesAccess拒绝原允许管理他人claim的作者/队长。保留page/page_size clamp、updated_at降序、作者/认领人头像、章与四类素材计数、total_pages至少1。claim本人也更新来源claimed_at；transfer响应claimed_by保持body意图。

### Requirement: Reporting permission and complete output semantics

五个reporting GET SHALL 零业务DML/commit。成员任务在view_tasks与targetmembership许可后读取该target全部AI任务和全账户总积分，不额外team/series过滤；分页不加clamp，created_at降序。完整任务输出保留result截断规则（failed5000，非failed>500或data:置空）、request_data前200、falsey cost/progress回退、frame+1及batch-optimize/ai-review上下文，不导入旧chat route或执行worker。

#### Scenario: Usage status, date and unknown models
- **WHEN** 查询四用量接口
- **THEN** SHALL 保留task/message/chapter innerjoin排除孤立任务，只completed/failed；completed累计次数及包括合法负值的credits，failed仅失败次数。日期无效不筛选，aware转UTC naive，闭区间且不新增start>end验证；NULL/空/字面未知模型按来源合并。单模型SQL先原字符串相等，不先归一化。

#### Scenario: Distinct ordering and empty responses
- **WHEN** 形成用量JSON
- **THEN** SHALL 保留团队series updated_at降序、单剧chapter order降序及无用量项排除；by_model等列表保留原插入顺序，单模型by_series/by_member原(-calls,-credits)稳定排序；export detail原四文本字段排序、member原(-calls,username)。all_models先将用量的NULL/空名合并为字面‘未知模型’；type map只对原AITask.model_name IN(all_models)做精确SQL查询，全库DISTINCT(model_name,model_type)实际返回顺序setdefault首条，不加team/time/status限制，不把SQL NULL/空串归一化后参与type映射。model_order SHALL 使用来源type priority（video/image/chat/optimize-frame，未知默认9）与(name or '').lower()组成的键稳定排序，相等键保留输入迭代次序，不新增大小写次级键或跨进程tie保证；member.models SHALL 独立使用普通sorted模型名集合。无team-series时export early body不含model_order；不能统一空shape/补次级排序或生成Excel文件。

#### Scenario: Export type filtering and independent sort proofs
- **WHEN** 非空用量包含同type的A/a、NULL/空/字面‘未知模型’，且全库存在同模型名不同type的团队外任务
- **THEN** SHALL 证明A/a的model_order同lower键保留输入迭代tie，而member.models为普通sorted；只有精确匹配字面未知名称的SQL行可贡献未知type映射，NULL/空串行不能贡献；同名多type保留实际DISTINCT返回的第一对，不虚构ORDER BY。rows/members原排序仍独立验证。

### Requirement: Disjoint parallel contract and meaningful acceptance

共享文件 SHALL 由单一作者先实现并由root冻结公开接口，管理/剧集/报表各写其白名单，不修改其它组或旧共享模块。唯一集成作者最后修改app及8旧登记测试；root统一守恒、集成、Git与验收。共享阶段的cold/types/ports/policy/DTO/quota/owner DDL断言 SHALL 独立成立；共享模块和collect不得顶层导入尚不存在的三族，router组合可在最终调用时延迟导入。预写82/all25/完整factory接线断言 SHALL 延后到唯一集成实际运行，阶段状态记not_run/not_yet_wired；允许记录实际选择器，但不得skip/xfail或虚称整个文件已通过。最终新boundary全部断言和原8登记测试所有业务/cold/explicit-policy哨兵 MUST 实际全跑。全部TODO初始未执行。

#### Scenario: Independent proof layers and resources
- **WHEN** root未来批准实施和本地验收
- **THEN** SHALL 分开作者专项、独立Sol源审、根完整后端与真实25方法TCP，覆盖原约束/两真实物理连接/未知commit-readback/完整非空历史守恒及IP/path/窗口quota。失败历史保留；只回收精确自有Session/Engine/listener/client/thread/socket/PID/temp，成功清理计数及实际完成全AND，不以attempt当完成。当前旧共享HEAD/status/tracked/index只真实记录，不作等于baseline门槛；固定Git52与目标保护字节必须准确绑定。
- **THEN** 指定GPT-5.6 Sol/xhigh Grillme SHALL 独立离线pending，不能由root测试或内审代替；不声称PG/SMTP/provider/前端/部署完成，不因生产部署未授权阻断本地批次。
