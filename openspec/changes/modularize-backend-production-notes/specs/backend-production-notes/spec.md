# Spec Delta

## Purpose

在新目录后端提供与旧系统一致的个人制作记录读写，供已迁移的 React 页面继续使用；保证稳定镜头身份、用户私有记录、媒体版本、认可失效和续作位置在同一业务契约下正确维护，避免前端迁移后仍需依赖混杂的旧后端实现。

## ADDED Requirements

### Requirement: 完整个人记录读写与可信访问

系统 SHALL 提供 GET、PUT `/api/chapters/{chapter_id}/personal-production-notes`，保留粗剪 GET/PUT 并仅组合四方法。缺任一可信身份、Session 或剧集访问端口 MUST 返回503且不开 Session。本人身份只能来自可信解析器；所有记录读写按 chapter/user 隔离。PUT先检查当前用户存在，再检查章节及同Session剧集访问，拒绝访问前不得进行媒体或私人记录维护。

#### Scenario: 本人读写及团队隔离
- **WHEN** 两个具有剧集访问资格的用户读取、保存同一章节
- **THEN** 各自仅见本人记录；未保存者revision0、空便签与空续作；已保存记录跨Session持久化。

#### Scenario: 缺接线与拒绝短路
- **WHEN** 工厂缺必需端口，或身份无效、用户不存在、章节缺失、剧集访问被拒绝
- **THEN** 缺接线返回503且零Session；其余依次保留401“登录状态已失效”、404“章节不存在”及访问策略原404/403；拒绝授权后不查询/修改媒体状态或私人记录。

### Requirement: 媒体身份与完整响应

系统 SHALL 返回 chapter_id、revision、media_state、连续帧快照、frame_notes、resume_frame_id。媒体身份只取原始 storyboard[0] 的非空字符串与唯一同章素材；不以名称、位置或secondary ID回退，不裁剪有效ID。空数组为empty，非数组或解析失败为unreadable，混合无效帧保持ready并标明缺失、非法、重复、跨章或歧义原因。URL摘要 MUST 只去已知临时签名参数及fragment，保留未知查询参数的原编码与顺序，控制字符/不可解析URL保持旧处理。

#### Scenario: 混合无效来源仍保留有效帧
- **WHEN** 章节中有缺失、重复、跨章和一个唯一合法素材ID
- **THEN** 每帧保持展示位置，无效帧ID及media_revision为空，有效帧仍可单独编辑；不以素材frame_index决定身份。

#### Scenario: 临时签名与真实对象变化
- **WHEN** URL仅临时签名或fragment变化，随后未知查询参数、路径或媒体源发生变化
- **THEN** 前者摘要不变，后者按实际归一化身份改变摘要；摘要为SHA256，双空来源摘要均null。

### Requirement: 单调媒体维护及同章认可撤销

系统 SHALL 在章节协调事务内从当前DB事实初始化媒体revision1、对摘要/有效性变化以revision CAS递增、对移除或重复ID留下失效墓碑；重新出现时继续递增而不复用1。每次媒体变化 MUST 将同章所有已存在用户记录中该ID的approved撤销为unmarked、approved_media_revision=null、needs_reconfirmation=true，并各自revision递增一次，保留备注、其他字段、续作及未受影响记录。重复访问无变化不得递增。GET允许提交维护写入，不得把它描述成纯数据库读取。

#### Scenario: 媒体变化影响多个用户
- **WHEN** 同章两个用户曾认可相同镜头，当前源媒体发生变化并被读取协调
- **THEN** 媒体revision及两个私有revision按规则递增，认可撤销且备注/续作保持；其他章节、非approved条目和未受影响用户不变。

#### Scenario: 删除后重建与无变化重读
- **WHEN** 镜头被删除后再以同ID重新出现，随后再无变化读取
- **THEN** 保存删除墓碑，重建沿原媒体revision递增；最后重读revision不再增长。

#### Scenario: CAS失败与事务回滚
- **WHEN** 媒体或认可维护条件更新未影响恰好一行，或未提交事务被回滚
- **THEN** 不接受丢失更新；维护错误按旧路径报告章节暂不可读取，未独立提交的媒体和认可修改一起回滚。

### Requirement: 双空历史认可独立维护

系统 SHALL 修复本人有效镜头双空但历史标记approved的条目，以独立revision CAS提交撤销。PUT发生此维护时 MUST 在比较客户端revision或实施任何用户补丁前停止，返回409“历史认可已撤销，请刷新后重试”，即使客户端预填了递增后的revision也不能继续写入。该维护提交不被随后冲突回滚；不影响原便签文字、续作或未受影响条目。

#### Scenario: 预填维护后的版本不能继续保存
- **WHEN** 本人revision7含双空历史认可，PUT使用expected_revision8并试图同时改备注/续作
- **THEN** 独立修复为revision8、认可撤销，返回上述409；客户端备注/续作完全未写，重读观察修复持久化。

### Requirement: 完整局部补丁与错误优先级

系统 SHALL 严格拒绝额外字段、bool充当整数、重复镜头ID、超过500条更新和超过2000字备注。expected_revision非负StrictInt，expected_media_revision正StrictInt，镜头ID字符串长度1至36；镜头至少有status或note，status是unmarked/needs_revision/approved之一或null，note是字符串或null。frames缺省为空，仅当明确提交resume_frame_id时允许无镜头补丁，允许显式null清空续作；同时支持多镜头与续作补丁。服务器不得增加前端单镜头限制或安全整数上限。维护关卡通过后先检查本人revision，再按请求顺序检查ID有效性、媒体revision与双空认可，最后检查续作目标。

#### Scenario: 局部备注保留其它信息
- **WHEN** 合法note-only、status-only、多镜头或续作-only补丁使用当前revision
- **THEN** 未提供字段保持现值，unknown entry字段保留；note-only继承approved时锁定当前media_revision，非approved显式状态移除needs_reconfirmation；省略续作不清空，明确null清空。

#### Scenario: 同时有个人冲突和坏镜头
- **WHEN** expected_revision过期且镜头ID非法
- **THEN** 先409“个人记录已变化，请刷新后重试”，不先返回镜头422。

#### Scenario: 镜头与续作校验
- **WHEN** 镜头失效、素材revision过期、双空却请求approved或续作目标非唯一有效
- **THEN** 按旧顺序返回422“镜头身份缺失、重复或已失效，不能保存该镜头”、409“镜头素材已变化，请刷新后重新确认”、422“原图和预览均为空，不能将该镜头标记为已认可”或422“续作目标缺少唯一有效的本章分镜身份”；不保存任何用户补丁。

### Requirement: 私人记录CAS与兼容组合

系统 SHALL 对首次保存使用chapter/user唯一约束，对后续保存使用expected_revision条件更新；成功revision恰好加一，响应来自本次保存结果而非提交后另一次查询。首次竞争或IntegrityError按旧409“个人记录已由另一请求创建或更新，请刷新后重试”，已存在CAS失配按本人revision409；未知DB异常不得转为允许访问。共享身份类保留粗剪原公开导入的class身份，原粗剪业务与端口语义不变；工厂扩展只新增同一个notes路径GET/PUT。

#### Scenario: 唯一竞争和条件失配
- **WHEN** 首次保存发生唯一竞争，或已存在记录revision条件更新失败
- **THEN** 分别返回对应409并回滚未提交用户写入；另一用户及已提交历史维护不被覆盖。

#### Scenario: 四方法工厂与历史粗剪
- **WHEN** 使用原create_app参数组合两个模块，并执行原粗剪回归
- **THEN** notes与粗剪均可用、只有四方法、docs关闭、导入不创建Session/Engine/schema或读取环境；原粗剪行为与共享Actor公开接口一致。
