# Spec Delta

## Purpose

让用户在当前章节显式保存或清除本人续作位置，并在下一次读取时恢复服务器记录的私有标记。此能力复用个人制作记录的 revision CAS 与读取隔离，区分稳定镜头身份、媒体认可和实际定位资格，避免结果未知时重复写入或覆盖其他本人记录。

## ADDED Requirements

### Requirement: 显式续作设置与清除

系统 SHALL 在当前本人制作记录读取 ready 后提供独立续作位置区域；具备位置写能力时允许选择有效镜头并显式保存，当前记录位置非 null 时允许显式清除。revision 0 和尚无便签的记录 SHALL 仍可设置，且不依赖备注写能力。相同位置和已经为 null 的清除 SHALL 不产生 PUT。系统 MUST 保留既有续作定位功能与原资格判定，不在设置、清除或读取后自动定位。

#### Scenario: 首次本人记录设置位置
- **WHEN** 本人快照 revision 为 0、没有便签、当前镜头身份有效，用户明确选定并保存
- **THEN** 发出一次位置 PUT，并采用服务器返回的新 revision 和位置；不提交便签修改

#### Scenario: 清除失效的旧位置
- **WHEN** 当前同章 ready 快照保存的位置非 null，但位置已无法映射或媒体状态为 empty、unreadable 或 mismatch，用户明确清除
- **THEN** 可发出清除 PUT，不要求该旧目标、媒体摘要或便签可认可

#### Scenario: 无位置写能力与无变化
- **WHEN** provider 未提供位置写能力，或用户选择的目标已是当前保存的位置，或位置已为 null
- **THEN** 不执行位置 PUT、不静默退回备注写方法；原有读取和定位仍按既有能力工作

### Requirement: 设置依据唯一当前镜头身份

系统 SHALL 仅在当前章节内容、原图完整目录、同次本地 capture 与同章服务器快照一致地绑定唯一原始 storyboard[0] 时开放设置。原始 ID 的完整计数 MUST 先于同章同剧集归属筛选，服务器目标 MUST 唯一、source_valid 为 true，且 frame_index 与此同份章节映射位置一致。系统 MUST 拒绝空、重复、替换、跨章、跨剧集及结构失配，不以列表序号或 frame_index 作身份兜底。位置设置 MUST 不要求摘要一致、approved 状态、可识别便签或非双空媒体；原有认可及定位规则保持。

#### Scenario: 媒体摘要不同但身份稳定
- **WHEN** 目标稳定身份、位置及 source_valid 均可核对，但摘要不同、便签待重新确认或图片与预览都为空
- **THEN** 仍可设置续作位置；系统不宣称认可或保证该位置当前可以定位

#### Scenario: 重复或跨归属目标
- **WHEN** 同一 raw ID 在章节、完整原图目录或服务器快照中重复，或目标归属/位置不符
- **THEN** 不提供有效设置动作，不发送 PUT；清除非 null 旧位置的资格保持独立

### Requirement: 独立精确保存契约

系统 SHALL 通过当前会话调用既有 PUT /api/chapters/{chapter_id}/personal-production-notes，正文 MUST 仅含 own 字段 expected_revision 与 resume_frame_id。expected_revision MUST 为非负安全整数且可安全递增；resume_frame_id MUST 为原始非空白、至多 36 个 Unicode 码点的字符串或显式 null。MUST 不含 frames、user_id、status、expected_media_revision、认可元数据或其他字段，也不放宽既有备注更新契约。API 仅使用既有配置校验通过的 loopback 地址、当前 Bearer 和单次路径编码，不增加端点、query 或自动请求。

#### Scenario: 设置与清除正文
- **WHEN** 用户保存某稳定 raw ID 或清除位置
- **THEN** 分别发送 {expected_revision, resume_frame_id: 原始ID} 或 {expected_revision, resume_frame_id: null}；响应仍按现有完整个人快照解码

#### Scenario: 非法正文与旧备注契约
- **WHEN** 位置正文缺字段、字段类型错误、含额外字段，或备注正文混入位置字段
- **THEN** 在请求前拒绝；既有单镜头备注正文和 parser 保持精确限制

### Requirement: 所有本人写入互斥且保留未保存备注

系统 SHALL 为同一面板 owner 的备注与位置写入共用同步门禁，pending 或必须重读时禁止两类后续写入；同步双击及旧动作回放 MUST 至多接受一次写入。备注编辑器打开或仍有相对当前 canonical 快照真正修改过的未保存 note/status 草稿时 SHALL 禁用位置写入，并提示先保存备注或显式重新读取以放弃修改。关闭编辑器 MUST 保留原有草稿语义；未修改的默认草稿 MUST 不永久阻止位置操作。

#### Scenario: 关闭编辑器仍有修改
- **WHEN** 用户修改备注或状态后关闭编辑器，再尝试设置或清除位置
- **THEN** 保留该草稿且不发位置 PUT；用户可保存该备注或沿用显式 GET 重读后重新选择位置

#### Scenario: pending 期间跨类型旧动作
- **WHEN** 已接受备注或位置写入，同步重放任一种旧保存、取消或位置动作
- **THEN** 不重复 PUT，不绕过门禁，不撤销当前操作，也不恢复原导航资格

### Requirement: 成功采用服务器权威快照

系统 SHALL 仅在完整响应通过解码、chapter_id 精确等于当前章、revision 精确等于提交值加一、resume_frame_id 精确等于提交值时确认成功。设置响应还 MUST 在完整 frames 中唯一匹配目标 raw ID、原提交位置和 source_valid；清除 MUST 不要求目标或媒体 ready。系统 SHALL 采用服务器完整 frame_notes、媒体元数据和位置，不对全章做本地合并，不要求旧便签或摘要逐字不变。成功 MUST 发布更高读取世代并重投影/登记，失效旧筛选及旧导航，且不自动 GET、重发 PUT 或定位。

#### Scenario: 设置响应包含维护变化
- **WHEN** 响应 revision 和位置正确、目标身份有效，但服务器维护改变其他便签、认可或媒体摘要
- **THEN** 采用返回真值，不补写旧状态，不误称媒体 CAS 或冻结媒体；新的定位资格由既有投影决定

#### Scenario: 伪成功响应
- **WHEN** 2xx 响应错误章、错误 revision/位置、重复或失效设置目标，或无法解码
- **THEN** 视为结果未知，保留提交意图并进入必须显式重读状态，不能宣称已保存

### Requirement: 拒绝及未知结果必须显式核实

系统 SHALL 在 409、422、其他非 401 拒绝（含 403/404）以及网络、超时、5xx、非法 2xx 等结果未知时保留本次具体设置/清除意图供只读查看、保留会话，并封锁同 owner 两类写入。系统 MUST 不自动 GET/PUT，不因关闭并重开编辑器而允许旧 revision 再写。用户必须显式 GET 成功取得同章当前真值后重新选择目标或确认清除，不自动移植旧意图；读取失败不能解除门禁。当前有效 401 SHALL 按既有规则退出，迟到旧 401 MUST 无效。

#### Scenario: 409 或结果未知后核实
- **WHEN** 写入被拒绝或结果未知，用户关闭编辑器或重复保存
- **THEN** 草稿意图仍可查看但无新写入；仅显式重读成功后允许用户重新决策，若服务器已保存则无须重复同位置写入

### Requirement: owner 与读取世代隔离

系统 MUST 绑定 user、services 实例、series、chapter 对象、打开世代、capture/资产快照、原始目标和读取世代；清除不要求可用媒体，但同样绑定当前 canonical 读取与 owner。scope 改变首个可见提交 SHALL 隐藏旧意图且永久失效，A→B→A 不复活。写入开始 SHALL 失效旧 resume/逐行导航与反馈，并发布高于旧登记的 null 读取身份；成功只登记更高 ready 身份，不放宽父级水位线。旧原生动作、旧 GET/PUT 成功或 401、旧摘要完成及关闭/失效回调 MUST 不影响新读取、新用户或已重开的面板。

#### Scenario: R1 动作进入 R2 或新 owner
- **WHEN** 原 R1 的真实保存/选择/取消/清除/关闭动作在 R2、R3 或关闭重开后再次被调用，包括旧回调声称自己 current
- **THEN** 最新同步身份守卫拒绝，当前非默认选择、门禁、反馈、父登记及 GET/PUT 数量不变

#### Scenario: 旧异步结果真正到达
- **WHEN** 用户重读、换章、离开、退出并新登录后，旧原始 GET、PUT 或摘要 Promise 实际 settle
- **THEN** 旧成功或 401 不覆盖当前私有状态、不注销新会话；只有新 owner 的显式读取可恢复编辑

### Requirement: 隔离兼容与范围保全

系统 MUST 保留前十九切片的只读页面、备注编辑、筛选、续作与逐行定位、私有数据和无能力 provider 行为。demo SHALL 仅在同 services 实例的私有内存状态保存，fixture SHALL 仅在既有 PUT 扩展位置分支与内存 CAS；原样本、GET 行为及其他路由保持，十三种业务方法与 exact-five 来源复制不增加。MUST 不新增媒体链路、播放、锁 API、队列、后端改造或真实服务调用。来源 GET/PUT 可能有会话、媒体元数据及认可维护，不能称数据库纯读取；本地/静态/隔离 HTTP/真实浏览器和指定外审的证据 MUST 分层。

#### Scenario: 代表性浏览器与外审
- **WHEN** 本地 demo 和隔离 API 完成桌面、390px、键盘及两用户验收
- **THEN** 如实记录实际请求和安全 PNG 增量；browser 取消不替代忽略 abort 的真实 Promise 自动化证据；未运行的 GPT-5.6 Sol/xhigh Grillme 继续独立待办
