# Spec Delta

## Purpose

让用户在当前章节个人粗剪草稿的同一份合法读取投影中，按纳入或待安排条件快速查看条目及数量。筛选仅整理现有文字列表，保留草稿序号、行身份、缺媒体说明及可信定位，并避免旧读取或其他用户的筛选影响当前内容。

## ADDED Requirements

### Requirement: Independent inclusion and pending filters

系统 SHALL 提供“全部 / 已纳入 / 已排除 / 待安排”四个选项。它们分别匹配全部条目、included 为 true、included 为 false、pending 为 true；MUST 不为 pending 定义覆盖 included 的优先级，也不修改原行两种状态标签。数量 SHALL 来自完整当前投影，已纳入与已排除合计等于全部；待安排单独计数，并明确它可与纳入或排除重叠。

#### Scenario: Pending overlaps another inclusion state

- **WHEN** 当前投影含 pending=true 的已纳入或已排除条目
- **THEN** 该条目同时匹配待安排及其原纳入条件，切换选项只改变可见行，全部数量与各选项数量不因当前选择而变化

### Requirement: Controls surround the existing nonempty ready list

系统 SHALL 仅在当前粗剪读取完成且投影条目非空时展示筛选控制；saved=false 的非空初始投影也 SHALL 允许筛选。读取中、读取错误或合法空投影 MUST 不显示旧控制、旧数量或人为生成列表。筛选零命中 SHALL 说明仅指“这份当前草稿投影”无匹配条件，并提供显式恢复全部操作，MUST 不暗示接口无数据、没有保存草稿或整部剧已无待安排条目。

#### Scenario: An unsaved nonempty projection can be filtered

- **WHEN** 合法读取返回 saved=false 且条目非空
- **THEN** 原未保存标题保留，用户可选择任一筛选；零数量选项仍可选择并显式恢复全部

#### Scenario: Loading error and a valid empty projection remain separate

- **WHEN** 读取尚未完成、返回错误或成功返回空条目列表
- **THEN** 分别展示原读取、错误或有效空态，筛选控制和旧列表不可见；错误只沿用显式重试，不自动读取

### Requirement: Original rows positions and safety information are preserved

系统 SHALL 保持匹配条目的响应顺序、实际行身份、原完整草稿列表序号及原读取时章节位置。定位 SHALL 继续核对完整原投影和实际行，MUST 不用筛选后的数组位置或克隆行替代身份。草稿保存状态、版本、失效旧引用数量和“不加载媒体”的说明 SHALL 独立于筛选；可见行原有缺媒体与无法定位说明 SHALL 保留。空稳定 ID、重复 removed ID 或没有视频 MUST 不因此从原投影删除、合并或重新解释；included/pending 不赋予定位、播放、导出或认可资格。

#### Scenario: A later draft row stays in its original position

- **WHEN** 选择某条件后只显示完整草稿中的后续条目
- **THEN** 其草稿序号和章节位置仍是原值，既有定位通过原完整投影核对；摘要与 removed 数量保持原值

#### Scenario: A pending row with no video retains its existing behavior

- **WHEN** 待安排条目没有视频，但符合现有稳定身份定位条件
- **THEN** 缺视频说明仍显示，原定位仍可用；筛选不要求媒体就绪或生成新媒体资格

### Requirement: Selection is private to the current read

筛选选择 SHALL 绑定同一当前读取投影及读取世代；新读取首次可见时 MUST 默认为全部。显式重读、关闭重开、用户或服务或章节对象变化 SHALL 使旧选择和旧事件失效；scope 变化的首个可见提交 MUST 隐藏旧内容与控制，A→B→A 不复活旧面板或自动读取。旧筛选回调、旧成功或旧401即使实际晚到，也 MUST 不能改变新读取选择、内容或会话。普通资产请求正常迟到完成 SHALL 保留当前粗剪投影及其选择，不新增粗剪读取。

#### Scenario: A stale filter callback arrives after a new read

- **WHEN** 新读取已经选中非全部条件，旧读取的筛选事件随后被实际调用
- **THEN** 新读取的选项、零命中或列表结果保持不变，不增加读取或失效操作

#### Scenario: Scope changes and later returns to the original objects

- **WHEN** 用户、服务或章节对象从 A 变为 B 后回到 A，旧请求实际成功或401结束
- **THEN** 旧控制和选择不复活、不影响当前会话；只有用户重新显式打开后才显示新读取且默认全部

#### Scenario: Asset completion does not reset the draft filter

- **WHEN** 原图仍未就绪时粗剪已读取完成并选择了某条件，原图请求随后正常成功
- **THEN** 草稿和当前选择继续显示，原定位资格按既有规则更新，不重读草稿、不自动定位

### Requirement: Filtering adds no reading or writing side effects

筛选与恢复全部 SHALL 仅改变本地展示，MUST 不发 GET、摘要核对、读取登记/失效、自动定位、存储、URL 更新、媒体请求或写入。鼠标或键盘切换筛选造成当前镜头失焦时 SHALL 只沿用原定位反馈清理，不作废当前读取。显式重读 SHALL 沿用既有粗剪 GET；当前401与其他错误仍按既有会话及错误语义处理。十二种业务接口、exact-five 来源复制及原样例 MUST 不扩展；隔离验收 MUST 不冒称真实后端或数据库纯读取验收。

#### Scenario: Filter interactions leave requests unchanged

- **WHEN** 用户以鼠标或键盘选择条件、恢复全部并使用原可信行定位
- **THEN** 操作前后业务GET、媒体读取和读取失效次数不增加；定位目标失焦仅清原反馈，草稿仍打开
