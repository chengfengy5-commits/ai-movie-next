# Design

## Context

动机见 proposal.md。当前个人记录投影已经把 resume_frame_id 关联到当前章节唯一 storyboard[0]、同章唯一资产及服务端同位置、同媒体摘要的行；只有 verified 行才返回 resumePosition。面板只显示位置文字。ChapterBrowser 已渲染完整当前章节的分镜，无需旧版分页选择或新的接口。

来源提交固定为 `23403806898550a7668a6ee7c0c457315655c39b`。旧 `js/episode.js:10120-10161` 的 continuePersonalProductionNotes 核查会话、当前已加载章节和 view，要求唯一帧引用及同章资产后选择分镜；不自动切到未加载章节。新实现沿用本地导航意图，但继续采用现有更严格的 verified 投影；镜头身份存在但媒体核对失败时不定位。

| 固定来源全文 | SHA-256 | 字节 |
| --- | --- | --- |
| js/episode.js | 79b117ce13e633b6b641faca7232328358e8e6f3be40825dcf097f87994d0e9f | 1162775 |
| backend/app/routes/personal_production_notes.py | 3fd7acc882fbeea0ed7500ef2a5f901a613aae14ce7bd893879dd669a6842562 | 10303 |
| backend/app/services/personal_production_state.py | 89ece9c2338bd9a2bef837adc0b101e498e5afa6c71c0e9d649fc9de6260c9b9 | 22871 |
| backend/app/services/team_service.py | 392f0792be876adda6b4b7629af0624a1dfbaef6fbf8555d7cacfd96cc81326f | 7792 |

仅固定 SHA 静态读取，不复制这些文件。旧个人记录 GET 和认证链的既有维护副作用仍见第四切片与当前兼容说明，本切片不执行真实后端。开始第八切片前已保存前七 change 共 42 份文件的 SHA-256 基线。

## Goals / Non-Goals

**Goals:**

- 在已经成功核对的快照中提供显式本地镜头定位，复用既有身份与媒体投影，不新增一种宽松位置判断。
- 让定位目标、按钮和反馈始终属于同一个当前上下文，覆盖 React props 改变后旧 ready 状态首帧仍可能存在的情况。
- 通过键盘焦点、镜头名称、静态位置指示和真实桌面/移动操作证明可用性。

**Non-Goals:**

- 不提供设置或清除续作位置、便签保存、认可、编辑、锁、生成、播放或导出。
- 不新增深链接、恢复历史导航、自动切章、分页或私有缓存；不改变默认 demo 24 剧集和章节内容。
- 不改业务服务 DTO、十二条白名单或 fixture 协议，不添加依赖，不运行真实后端或生产服务。

## Decisions

### 复用可信投影，保持读取来源绑定

继续用现有 resumePosition；它是在唯一稳定身份核对后派生的本章展示位置，不能成为未核对历史序号的兜底。ChapterBrowser 的 notes 打开上下文绑定 userId、seriesId、services、chapter 对象、assetResult 快照与 openEpoch；失配首帧隐藏并永久关闭，A→B→A 不恢复旧面板，重开须显式点击。ready 读取状态绑定请求 generation 和核对时 userId、services、chapter 对象与 assets 快照；props 或快照不一致时首帧即隐藏旧定位入口，并保持旧请求和摘要的 generation/abort 防护。仅在 ready、mediaSnapshotAvailable、匹配 scope 及当前有效 ticket 下把已核对位置传给本地导航；旧回调再次核对后作废。verified 表示身份和媒体核验通过，不额外要求个人状态为 approved；needs_reconfirmation 也可能拥有合法续作位置。

备选是另做基于 resume_frame_id 的较宽松导航解析，但会重复稳定身份规则并允许现有投影已拒绝的目标；本切片保留已验证契约。

### ChapterBrowser 管理当前分镜 DOM 与反馈

当前章节的分镜通过组件内引用定位；引用与目标位置只在当前有效章节作用域中使用，不把原始资产 ID 放入 DOM。导航事件再核对当前用户、services、剧集、章节对象和资产快照，要求目标存在、仍连接且属于当前可见分镜内容。使用 focus({ preventScroll: true }) 后 scrollIntoView({ behavior: "auto", block: "center" })，避免延迟定时器和动画跨 scope 的残留工作。

目标 article 使用程序化焦点、可识别镜头标签及清楚的当前位置标记；聚焦不增加所有镜头的日常 Tab 停靠。反馈在 scope 变化或相关重读、关闭、切换面板时失效；不通过 URL hash 或 document 全局 ID 查询目标，不操作 hidden/inert 页面。个人记录继续打开，重复点击仍仅本地定位。

### 不扩展业务和媒体链路

定位只操作既有 DOM，无 fetch、写入或新增媒体 src。滚动可能让既有 loading=lazy 的可信本地 PNG 进入视口，这仍是现有原图展示，不应错误记为新业务请求或新增外部媒体获取。验收对比点击前后的业务日志，单独允许既有 loopback PNG 和 OPTIONS；不得新增视频或外部 URL 访问。

### 分层验收

实施由 GPT-6 Luna/xhigh 完成，设计和独立只读复审由 GPT-6.1 Sol/xhigh 完成；root 独立执行最终 typecheck、全量测试、build、来源合同与 exact-five verifier。自动用例覆盖有效定位、无自动定位、无效/缺失/重复/mismatch 与 pending 阻止定位、业务调用不增加、scope 改变首帧和真实 settle+flush 的旧响应。使用风险代表性用例，不制造无意义全组合。

root 在默认 demo 和隔离 API fixture 验收桌面、390px 与键盘：目标焦点和名称、位置反馈、重复定位、重读及切章清理、无位置/失效/错误、两合成账号以及点击前后请求边界。保留截图、实际 DOM 观察及请求日志；已有服务和素材回归仅验证受影响入口。

## Risks / Trade-offs

- [旧 ready 数据被新 props 重新解释] → 将读取上下文保存在成功状态并在呈现和事件两处核对；代表性布局阶段/迟到 Promise 用例验证首帧与结算行为。
- [显示位置被当作稳定身份] → 仅消费原投影已核对的派生位置；父级核对其作用域及存在的当前目标，无 first/index/name 兜底。
- [失效焦点或定位反馈残留] → scope 绑定引用与反馈，重读/关闭/切章/退出作废，使用同步本地 DOM 操作。
- [颜色或滚动不能帮助键盘用户辨识] → 聚焦有镜头名称的容器、提供文本反馈和可见焦点，不依赖动画。
- [隔离 fixture 被误称真实权限验收] → 记录固定源和分层结果；真实 FastAPI/PG、Worker、R2、账务与生产仍未运行。

## Migration Plan

只在独立 haoai-next 中实施和验收。按 tasks 落地局部 React 变更，保留前七切片和来源复制边界；本轮不提交、推送或部署。回退仅需移除新的定位入口和局部目标反馈，既有只读读取不变。Grillme 固定 GPT-5.6 Sol/xhigh 离线则保留独立待办，不由本次 Sol 复审替代。
