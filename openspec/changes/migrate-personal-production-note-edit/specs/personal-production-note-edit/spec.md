# Spec Delta

## Purpose

在隔离 React 工作区中提供本人当前章节单镜头制作记录的显式编辑与保存，迁移冻结接口的私有归属、状态语义和双版本 CAS。让首次记录、保存冲突与结果未知可核实，同时保留已有只读查看、筛选及定位能力，避免隐式认可、覆盖他人记录或自动重复写入。

## ADDED Requirements

### Requirement: Explicit single-frame editing entry

系统 SHALL 仅在当前 provider 明确提供保存能力时显示可用编辑入口；默认 demo 与隔离 API MUST 提供实际保存实现，旧只读 provider MUST 保持可查看且不得伪装保存成功。用户 SHALL 显式选择一个可编辑的当前镜头，编辑本人文字备注与三种制作状态；revision 0 无个人行时也 MUST 可首次保存。孤立旧记录和不可识别记录 MUST 保持只读，编辑器不得改变原只读列表、筛选数量或续作位置。

#### Scenario: First personal record
- **WHEN** 本人当前快照 revision 为 0，章节具有可核对的镜头，用户显式打开该镜头编辑
- **THEN** 系统允许首次保存，并保持“尚无个人记录”的既有读取语义，直到合法保存响应确认新记录

#### Scenario: Provider has no write capability
- **WHEN** 当前 provider 只有原读取能力
- **THEN** 原查看、筛选与合法定位继续可用，系统不发送保存请求，也不显示成功保存承诺

### Requirement: Current structure and approval eligibility

编辑目标 MUST 绑定同份当前原始个人快照与本地媒体捕获、用户/会话、services、剧集、章节对象、面板打开世代和读取世代。当前个人投影结构 MUST 为 ready；当前章节的原始 `storyboard[0]` 必须非空且唯一，完整素材响应中该原始 ID 必须唯一并同章同剧集，服务端同 ID/位置必须唯一、source_valid 为真且 media_revision 为正安全整数。不可用、重复、替换、外章、孤立或 unreadable 记录 MUST 无编辑能力。未标记/待修 SHALL 在结构满足时允许本地摘要失配；认可 MUST 额外要求当前行 verified、本地两摘要精确匹配且至少一服务端媒体摘要非 null，不请求媒体验证可播放性。

#### Scenario: Digest mismatch without structural mismatch
- **WHEN** 镜头结构身份及服务端媒体版本有效，但本地媒体摘要与快照失配
- **THEN** 允许保存未标记或待修备注，禁止保存认可，不用数组位置替代稳定身份

#### Scenario: Invalid identity or empty approval media
- **WHEN** 原始镜头/素材身份重复或不属于当前章，或用户选择认可但媒体摘要双空
- **THEN** 系统不发送 PUT，并给出当前目标不能编辑或不能认可的说明

### Requirement: Explicit state and literal note

保存状态 MUST 仅为 `unmarked`、`needs_revision`、`approved`。普通可识别的当前状态 SHALL 作为可见初值；待重新确认或当前未核验的旧 raw approved MUST 以未选择状态开始，用户必须明确选择三状态后才能保存。不可把派生的 `needs_reconfirmation`、`unverifiable`、`unreadable` 写入接口，不可通过仅提交备注隐式重新认可。文字备注 SHALL 按原文字面量保存，空串可清除，最多 2000 个 Unicode 码点，正文及旧备注 SHALL 作为纯文本显示。

#### Scenario: Stale approval is not silently retained
- **WHEN** 原记录的 raw status 为 approved，但当前需重新确认或未核验
- **THEN** 编辑器保留可识别的文字草稿，状态初值为空；未明确选择状态不能保存，也不默认发送 approved

#### Scenario: Unicode note boundary
- **WHEN** 用户编辑包含补充平面字符的备注或清空文字
- **THEN** 系统按 Unicode 码点校验 2000 上限，合法原文或空串原样写入，超限不发送 PUT

### Requirement: Exact authenticated private patch and CAS

系统 MUST 仅新增 `PUT /api/chapters/{chapter_id}/personal-production-notes`，路径 ID 单次编码，无附加查询；使用发起时当前 Bearer，仅向既有配置校验通过的隔离 loopback API 发请求。正文 MUST 仅有 `expected_revision` 和单元素 `frames`，该元素严格包含 `storyboard_asset_id`、`expected_media_revision`、`status`、`note`，不带 user_id、resume_frame_id、frame_notes 或认可元数据。个人版本来自原始快照、媒体版本来自已绑定服务端帧，不得猜测或省略。保存遵守既有会员及剧集/团队/认领权限、本人 chapter_id+user_id 私有归属；界面不得声称获取编辑锁或真实数据库纯只读。不得自动重试、批量补丁或整表覆盖。pending 与必须重读的门禁 MUST 属于当前面板 owner，取消/重开编辑器或改选镜头不得绕过；保存开始作废原定位，pending 期间旧续作/逐行定位不可操作。

#### Scenario: Single private frame update
- **WHEN** 用户对一个有效当前镜头显式保存
- **THEN** 发送严格的单镜头双版本 PUT，仅更新认证用户的目标记录，保留未提交的其他镜头、孤立旧记录、未知记录字段和续作位置；服务端既有维护另按原契约处理

#### Scenario: Duplicate save gesture
- **WHEN** 同一保存请求仍在途，用户重复点击或重放旧保存 handler
- **THEN** 只存在一次 PUT，不自动排队第二次写入

### Requirement: Validated success creates a new read generation

系统 MUST 核对保存响应的章节、完整快照结构、个人 revision 恰为 expected_revision+1、同 ID/位置媒体版本、目标文字/状态及认可元数据符合此次请求；approved 的目标两摘要必须与本次认可核对的 capture 和保存前 snapshot 一致，不能因返回的相同媒体版本号而接受不同摘要。合法响应 SHALL 作为服务端权威快照，未编辑记录和续作不从本地旧值拼回。成功 MUST 作废旧读取与旧定位回调，以更高读取世代重新捕获/投影并登记新 readout，重置本地状态筛选为全部，仅清理此次保存绑定的草稿。不得在同一世代覆盖旧 ready 对象或放宽父端拒绝旧登记规则；成功不得附加 GET 或媒体请求。

#### Scenario: Successful save preserves read-only flows
- **WHEN** 当前单镜头 PUT 返回合法下一版本快照
- **THEN** 面板显示该响应的新记录，筛选回全部，原续作与逐镜头定位只对新读取生效，旧登记/失效/关闭/定位回放不影响新结果，保存成功不自动重读

#### Scenario: Incongruent successful response
- **WHEN** HTTP 成功但响应章节、版本、目标值或结构与此次保存不一致
- **THEN** 不显示保存成功，进入结果未知并保留草稿，不用旧数据伪造响应

### Requirement: Rejected save preserves draft for explicit reconciliation

当前 409/422 MUST 显示拒绝原因并保留文字草稿，不自动覆盖、更新 CAS 或重提。用户 SHALL 显式重新读取以核对服务端当前记录；同一当前编辑目标的草稿与新服务端快照分别保留，在身份仍合法时重新绑定新读取世代，并要求重新明确状态才能再次保存。新结构不合法则只显示草稿与不能保存说明。需要重读的门禁属于面板 owner，不因取消、重开编辑器或改选另一镜头而解除；只有成功的显式 GET 与重新状态确认可解除，关闭整个面板则清理该 owner 的本地私有草稿。403/404 等其余当前非 401 拒绝同样保留草稿并进入必须显式 GET 核实后才能再写的 owner 门禁，不得注销；当前 401 按原会话流程处理，迟到 401 不影响新会话。

#### Scenario: Conflict followed by explicit reread
- **WHEN** 个人版本或媒体版本冲突返回 409，用户显式重新读取
- **THEN** 旧草稿不被新服务端文字覆盖，系统显示当前记录供核对；未重新明确选择状态不能再次 PUT，不自动提交新版本

#### Scenario: Maintenance conflict
- **WHEN** 旧空媒体认可维护已独立提交，保存返回“历史认可已撤销”的 409
- **THEN** 界面按冲突保留草稿并要求显式核对，不宣称请求失败使维护回滚或可直接重试原认可

### Requirement: Unknown outcome never automatically resubmits

发出 PUT 后的网络错误、超时、响应体超时、取消、5xx 不确定结果或无法验证的成功响应 MUST 标为“不能确定是否已保存”，而非断言未保存。系统 MUST 保留当前草稿并阻止重复写入，直到用户显式 GET 核实并重新确认；不得自动 PUT、自动 GET、幂等重放或把 abort 等同服务端回滚。重开/离开使旧本地草稿失效，不得影响服务器既有结果或其他账号。

#### Scenario: Applied write loses its reply
- **WHEN** 隔离服务已写入下一版本但客户端收到超时或无效响应
- **THEN** 没有第二次 PUT；用户显式读取可看到真实记录并核对草稿，不通过直接换 expected_revision 自动覆盖

### Requirement: Full scope and real stale-event isolation

编辑输入、保存、取消和确认 handler MUST 绑定当前 owner、原始 snapshot/frame、捕获、读取及写入世代，并在消费时检查最新 scope。用户/services/章节对象/上下文变化或关闭重开 MUST 在首提交隐藏旧私有编辑内容，永久使旧世代失效；A→B→A 不复活。旧输入、旧保存、旧取消、旧响应、旧摘要及伪造 isCurrent 的回调 MUST 不能更改新草稿、新登记、焦点或会话；现读取 tombstone 及代际规则保持原样。

#### Scenario: Old callbacks after a new saved read
- **WHEN** R1 保存后已有 R2 新读取与新草稿，测试实际调用 R1 原 JSX 输入/保存/取消与登记/失效/关闭/定位 handler
- **THEN** R2 非默认状态和文字保持不变，PUT/GET/登记及注销次数不增加，旧 readout 不重新登记

#### Scenario: Original write settles after account change
- **WHEN** A 账号原 PUT Promise 在关闭、退出、新用户登录并建立新编辑后才真实成功或返回 401
- **THEN** await 原 Promise 并 flush React 后，B 账号记录/草稿/token/会话不受影响，不能仅以 signal.aborted 作为隔离证据

### Requirement: Isolated state and protected behavior boundaries

demo SHALL 仅在当前 services 实例内按本人/章节保存内存记录，后续同实例 GET 可读，不承诺页面重载持久化；隔离 fixture SHALL 用内存执行保存与 CAS，原所有 GET 样本及其他路由保持原行为。允许业务方法仅原 12 加此 PUT；不得增加锁、续作、任务、队列、生成、上传、媒体或真实服务调用，不把备注/body/token 写入日志。原个人只读投影、状态筛选、orphan、导航、防旧回调语义及前十八批历史 MUST 保持；外部指定 Grillme 仍独立待办。

#### Scenario: Local demo and HTTP fixture validation
- **WHEN** 在 demo 或隔离 fixture 保存后关闭重开个人面板，并切换到另一账号
- **THEN** 同实例当前账号 GET 显示已保存记录，另一账号同章数据独立；不发生真实后端、付费、队列、R2 或新媒体读取，不将隔离模拟称为真实 FastAPI/PostgreSQL 回归
