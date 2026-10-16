# HaoAI-next 完整剩余工作清单与排期

更新日期：2026-10-16（北京时间）

本文以 2026-10-16 的仓库与已接受收据校准完整剩余范围。它是唯一的迁移清单，不是新的架构设计，也不代表整站功能已验收。

## 当前基线与计数口径

本文原始范围基线为 main@9df03f0c083236b9b4bb18b722ad95fd9839a144（110 收据确认当时远端精确一致）；102 源码分析基线仍为 backend 5ca522cdc20ec2843fef48cd0d9a68f8ee128c78。102、104、110、113、116 的历史范围与证据归属保留；本批后台会员证据在末尾增补。149 收据确认会员管理前端已提交并推送至 main@eca1978bd2453ca1e4cb486804c904a6aa89b1ba。单图原 intent 到期 recovery/due 消费者随后提交并推送至 main@1e07f90bb203b8ff6e7b534eb0e8b22a12f5ddfc；E166 收据确认 remoteExact=true。该1e07提交是当前后端源码回归基线，不改变 102 的 backend 5ca522cdc20ec2843fef48cd0d9a68f8ee128c78 历史分析归属。系统配置 list/edit 前端已由本批提交并推送至 main@32b3f721a8436a270b58b73c2a2c138ca8dc6b5a；196 为前端门禁，201 为真实 HTTP/owned SQL，209 为局部 GUI 与独立 SQL，生命周期结果另列。后续文档或实现提交不改变既有证据归属；最新 Git 状态以仓库实时查询为准。102 的完整分析文件为 haoai-next-current-full-remaining-inventory-102.json，116 为 haoai-next-root-development-continuation-116.json。两者及原始验收收据均在任务证据目录，本文不改动它们。

固定旧源共有 248 个显式 method/path 声明，当前运行时登记 250 个方法，其中包括两个新增两步分镜接口；248 个旧声明均已登记、差集为 0。这里证明路由登记，不证明请求行为、完整实现、端到端运行或浏览器接受。固定旧后台有 9 个资源、23 个资源视图；当前已登记 16 个视图，另 7 个旧视图待实现和接线。登录和仪表盘独立于这 23 个资源视图。用户积分、用户积分编辑、积分流水 3 个扩展视图单独计算，不冲抵这 7 个待实现旧视图。

清单采用彼此独立且有重叠的计数：12 类工作流、51 个 UI 交付行。102 初始快照统计 33 行有开发动作、28 行有接线动作、51 行有验证或接受动作；116 覆盖 U11.2 后为 32、28、51。本批 U12.8/U12.9 两行完成开发和接线，按原计数口径为 30、26、51；本批系统配置 U12.6 再闭合开发动作，当前为 29、26、51：开发 33−U11.2−U12.8−U12.9−U12.6=29，接线 28−U12.8−U12.9=26，验证/接受仍为 51。原 action 统计按 ui_workflows[].deliverables[].remaining_actions.kind 对各交付行逐行去重后计数；非 C 单元格数不是原 action 统计；局部验收不减少 51 行各自的验证/接受义务。生成原始义务 A01–A39 共 39 项、附加规范义务 6 项、原规范修复子例引用 30 项、后台 9 个资源/23 个视图另行计数。这些分母彼此重叠，不相加，也不用于推算完成百分比。特别是 A01–A39 整项完整矩阵接受数为 0；这不表示相关组件为零，已有组件、接线和局部证据仍按下表分别保留。

状态简写：C＝已实现或已接入；P＝部分；M＝缺失；I＝进行中；U＝当前没有足够验证；E＝依赖外部授权或外部环境。验证和接受状态分别以 V/A 标明。实现、接线状态为 C 的行只在相应列记为完成；局部测试、历史浏览器和当前完整接受不可互相替代。

## 十二类工作流与 51 项交付

### U01 登录、注册、邮箱验证码、找回与重置密码、账户安全

| ID | 交付内容 | 实现 | 接入 | 验证/验收 | 下一步 |
| --- | --- | --- | --- | --- | --- |
| U01.1 | 认证入口和会话恢复 | C | C | V=P / A=U；页面及认证 API 已有 | 用当前真实 API 补齐注册、验证码、找回、重置、撤销会话和过期会员浏览器闭环。 |
| U01.2 | 简介、登录设备、撤销会话和修改密码 | C | C | V=P / A=P；113 已证简介保存回读、session 列表读取与两账号切换局部 | 基于已证真实 JWT/owned SQL 主链，补验密码提交、完整会话撤销（尚无服务端 logout 请求证据）、脏离开取消及网络/重启/迟到分支；只复核会员合并影响的交错。 |
| U01.3 | 真实邮件发送与重置链接交付 | P | M | V=P / A=E；sender callback 和 SMTP 配置 CRUD 已有，没有真实 SMTP transport 证据 | 实现 sender 和受控 SMTP sink 验收；真实邮箱交付另需适用授权，本轮不索要密钥。 |

### U02 剧集、章节和导入导出

| ID | 交付内容 | 实现 | 接入 | 验证/验收 | 下一步 |
| --- | --- | --- | --- | --- | --- |
| U02.1 | 剧集筛选及新建/编辑/删除 | C | C | V=P / A=U；主要 CRUD 与封面 API 已接入 | 验证当前浏览器的完整 CRUD、封面未知结果、个人/团队权限和筛选保全；缺验证时不重写已实现功能。 |
| U02.2 | 章节创建、改名、删除、选择和排序 | C | C | V=P / A=P；排序及正文保全有历史真实 API 浏览器证明 | 补创建/改名/删除、并发权限和迟到目录分支的当前浏览器证据。 |
| U02.3 | JSON/Markdown 导入与完整 JSON 导出 | C | C | V=P / A=P；历史真实 JWT/Target API/owned SQLite 主链已通过 | 补未覆盖负例和受影响接线；保留未知 frame 字段、原文、URL 和顺序。 |
| U02.4 | 跨章节、剧集、会话保全和权限联动 | P | P | V=P / A=P；scope/generation、稳定 ID、锁确认和编辑保护已存在 | 验证删除、重排、锁、多用户及生成迟到结果的交叉矩阵，并覆盖 U03/U04/U09。 |

### U03 团队、邀请、共享、权限与用量

| ID | 交付内容 | 实现 | 接入 | 验证/验收 | 下一步 |
| --- | --- | --- | --- | --- | --- |
| U03.1 | 团队创建/编辑/删除、详情、成员及角色权限 | P | M | V=P / A=U；团队/剧集/reporting 后端共 25 个旧接口已装配，前端仅有筛选用团队目录 | 新建团队中心和独立 API；经受信 actor 接入，最后由单一集成作者写共享 Workspace/services；覆盖 owner/admin/member/非成员。 |
| U03.2 | 邀请生成、列表、撤销、加入、离开与配额 | P | M | V=P / A=U；管理端邀请/加入/离开和 JoinQuota 已有 | 补邀请、加入/离开、过期撤销及人数配额 UI 和多用户 API 验收；授权必须由后端决定。 |
| U03.3 | 团队剧集共享/撤回、认领/取消、创建和转移 | P | M | V=P / A=U；团队剧集 7 接口和访问策略已有 | 接入共享、认领、释放、转移与确认回读；验证转移中的生成/编辑/认可权限及归属保全。 |
| U03.4 | 成员任务、团队/剧集/模型用量与导出 | P | M | V=P / A=U；reporting 5 接口已装配 | 先稳定共同 reporting 契约，再各自实现用量/导出 UI 与任务 UI；分别验证跨团队隔离、金额和导出一致性。 |

### U04 分镜编辑、AI 复核优化与结果应用

| ID | 交付内容 | 实现 | 接入 | 验证/验收 | 下一步 |
| --- | --- | --- | --- | --- | --- |
| U04.1 | 分镜浏览、文字/结构编辑及素材引用导航 | C | C | V=P / A=P；编辑器、引用面板及历史正文/排序主链已证 | 补结构插入/删除、引用清理、未知写入和团队权限的当前真实后端浏览器分支。 |
| U04.2 | AI 复核/批量优化、上下文、部分成功与统计 | P | P | V=P / A=U；API 准入和状态读取存在，当前仅排队，未装配实际 review/optimize worker | 完成真实 frame.review/frame.optimize producer、Work、费用及恢复，再接 React 操作、统计、来源和错误展示。 |
| U04.3 | 生成结果应用、媒体版本与认可失效 | P | M | V=P / A=U；owned result facade、SQL CAS 和协调器已有，真实消费者未接 | 将完成结果验证与 generation_application 接入 HTTP/Worker/UI；验证稳定 ID、no-ID、重复、迟到和回执未知，不重写已有 CAS。 |

### U05 素材浏览管理、维护、融合、替换和上传

| ID | 交付内容 | 实现 | 接入 | 验证/验收 | 下一步 |
| --- | --- | --- | --- | --- | --- |
| U05.1 | 角色/场景/道具浏览搜索、文字 CRUD 和引用影响预览 | C | C | V=P / A=P；E17 历史真实 JWT/Target API/合成 SQLite 在角色/场景/道具三类素材上合计完成 POST 3 次、PUT 3 次、DELETE 3 次，共 9 次写入；删除影响两章且保留他人章节 | 保留 E17 的 e6d2 时点证据；只补之后变更影响的当前交叉覆盖，不把历史提交证据扩大为当前全部流程已验收。 |
| U05.2 | 孤立项、重复项、忽略/取消忽略和合并 | P | M | V=P / A=U；series_maintenance 后端路由/UoW 已接，前端服务方法缺失 | 增加维护 UI 与 API，验证跨章引用、别名、分阶段失败、权限和媒体/认可保全。 |
| U05.3 | 融合素材查看/编辑/删除与生成 | P | M | V=P / A=U；准入/claim/应用和 ports 已有，create_app 默认 runtime/storage 为 None | 接入现有 runtime/storage 并做 owned 本地验证，再建 UI、任务回读和失败状态；真实 provider 另行验收。 |
| U05.4 | 跨章节素材替换 | P | M | V=P / A=U；后端已接入，保留源提交和独立清理阶段 | 增加选择/影响预览/确认和未知结果恢复；独立回读源章节与引用，验证权限及媒体保全。 |
| U05.5 | 图片/音频/视频上传、OSS 重试、生成历史和媒体引用 | P | M | V=P / A=U；后端上传/R2 adapter 存在，前端当前仅剧集封面上传 | 装配 MediaUploadApplication 和 storage/fetch 能力；补上传、预览、历史选择和引用链；本地 owned 文件先验，真实 R2 另授权。 |

### U06 AI 助手、生成、提取、Worker 与恢复

| ID | 交付内容 | 实现 | 接入 | 验证/验收 | 下一步 |
| --- | --- | --- | --- | --- | --- |
| U06.1 | 聊天消息、文本流与 source bubble | P | P | V=P / A=U；chat CRUD/统计、detached SSE、可选 Task 绑定和 stream 财务已有 | 配置真实 text runtime，补 React 聊天操作、断流/失败/任务回读和来源 bubble；按原协议矩阵补缺口。 |
| U06.2 | 两步分镜生成、报价、SSE 预览和确认保存 | C | C | V=P / A=P；真实 JWT/Target API/owned SQLite + FakeRuntime 主链已通过 | 保留主链收据，补受影响的失败、断流、原 key 恢复和权限分支；FakeRuntime 不代表真实 provider。 |
| U06.3 | 图片单张/批量与视频单条生成入口 | P | P | V=P / A=U；媒体准入、graph、计费及 image-single 无参考图同步执行已有 scoped PG/result 证据 | 完成批图、单视频、参考图/异步和其余 provider family consumer；接 React 状态和原 key 恢复；保留 A01–A12/A21–A29/A31–A38 原矩阵。 |
| U06.4 | 提取角色/场景/道具及单镜/全镜生成 | P | P | V=P / A=U；路由、解析和保存已有，提取 runtime 仍需注入 | 装配 StoryboardAssetRuntime，接提取结果编辑/确认和批量保存；验证失败不覆盖源内容及未知回读。 |
| U06.5 | Worker 可靠性、金融、结果应用、取消和重启恢复 | P | P | V=P / A=U；8 项 canonical records、P1–P6 合同、单图 claim/intent/result、P5 bridge 和 stream 金融已有；GEN-W01 recovery/due 消费者已实现；仅 1/5 worker 类型有 executor factory 登记 | image.single 到期恢复已通过有界 owned PostgreSQL 合成验证（E179/E181 固定 177，E185 固定 183，均对应同一 916 项 1e07 源清单）。E181 F 仅证明两个不同 worker PID 读取同一持久 PG 图，不等于 PG/完整服务重启或任意 crash 恢复。P6 HTTP/control cancel、poll、可验证原输出恢复、其他四类 worker consumer、六项附加义务和完整 A01–A39 验收仍待；U06.5 宽范围仍为 P/P、V=P/A=U。 |
| U06.6 | 音频相关原工作流 | U | M | V=U / A=U；旧入口有 uploadAudio、参考音频；本轮未完整审查旧音频界面，未见独立 TTS POST 证据 | 对照固定旧音频/音色行为，区分上传/参考音频和 TTS；保留原迁移范围，不凭名称新造协议或删目标。 |

### U07 多节点创作画布和结果回填

| ID | 交付内容 | 实现 | 接入 | 验证/验收 | 下一步 |
| --- | --- | --- | --- | --- | --- |
| U07.1 | 图结构、缩放平移、节点/边选择和布局 | M | M | V=U / A=U；后端 GET/PUT 有，React 尚无画布入口 | 开发画布视图，按章节加载并隔离 scope/session；验真实章节/素材图、键盘和窄屏。 |
| U07.2 | 节点编辑、手动连线、卡片/便签和撤销重做 | M | M | V=U / A=U；固定旧 canvas-manager 有对应交互，本次只核过方法元数据 | 读取旧行为后迁移交互并复用来源 CRUD；保全写入影响、选中状态及未知字段。 |
| U07.3 | 画布文档/视口保存、版本冲突和失败恢复 | P | M | V=P / A=U；后端 GET/PUT 有版本字段，当前不提供 SQL version CAS | 接 React 脏状态/防抖/重试/离开确认和真实 API；验证并发、迟到保存和失败，未知写入不自动重放。 |
| U07.4 | 画布触发生成及 owned 结果应用 | P | M | V=P / A=U；authority/result reader 和 writer 有，缺真实 HTTP/UI 消费者 | 接 generation_application 和结果回填；验证 owner、完成任务、当前权限、稳定目标/CAS、重复应用不增 revision。 |

### U08 连续预览、粗剪、媒体导出与实际输出

| ID | 交付内容 | 实现 | 接入 | 验证/验收 | 下一步 |
| --- | --- | --- | --- | --- | --- |
| U08.1 | 粗剪排序、纳入/排除和保存 | C | C | V=P / A=P；Erough 历史 fixture 覆盖 8 类、双用户/409/未知回读 | 补当前真实 Target API/SQL、多用户保存及源修改联动；保留已验编辑器事实。 |
| U08.2 | 视频/音频预览与连续试看 | M | M | V=U / A=U；旧版播放器语义可见，当前 UI 明确提示纳入不代表可播放/导出 | 实现播放器、队列、连续播放和失败恢复；验证真实安全媒体内容与生命周期，不以链接 HTTP 200 代替播放。 |
| U08.3 | 保存粗剪的素材批量导出与下载 | P | M | V=P / A=U；旧版有视频素材导出队列，当前 identity resolver 不提供实际文件字节 | 实现导出队列和下载文件链，按已保存快照核对顺序、排除、缺项、失败/取消/重试与输出字节。 |
| U08.4 | 成片/实际输出交付 | U | M | V=U / A=U；既有材料仅证明素材导出，没有证明合成为单个成片 | 明确目标产物并核对旧入口；若需成片，补媒体合成/音频/输出和恢复链，不能把素材下载称为成片。 |

### U09 制作缺项、便签、镜头认可和续作位置

| ID | 交付内容 | 实现 | 接入 | 验证/验收 | 下一步 |
| --- | --- | --- | --- | --- | --- |
| U09.1 | 私人便签/认可/续作写入与定位 | C | C | V=P / A=P；Eresume 历史 fixture 覆盖 14 例 | 补当前真实 API/SQL、多用户隔离及跨章迟到响应；保留已有局部证据。 |
| U09.2 | 缺项清单、筛选和导出 | P | P | V=P / A=U；notes/rough-cut 状态投影已有，旧 episode 有导出函数但未全读 | 对齐旧清单字段和真实数据来源，补当前未接操作/导出以及缺图、缺视频、认可重验流程。 |
| U09.3 | 媒体变化撤认和多用户一致性 | P | P | V=P / A=U；后端 source/media/notes 同事务协调器已有 | 确保 upload/generation/canvas/source writer 实际使用协调器；验双用户撤认、媒体 identity 和 notes CAS。 |

### U10 任务分页、详情、进度、取消和历史

| ID | 交付内容 | 实现 | 接入 | 验证/验收 | 下一步 |
| --- | --- | --- | --- | --- | --- |
| U10.1 | 本人任务分页、请求/结果/费用详情 | C | C | V=P / A=P；E17 历史浏览器证明 18 条结果帧、凭据脱敏；过期会员结果可读 200、请求受限 403、会话保留且钱包/账本/任务数不变 | 按未覆盖的分页、身份切换、页缩小、详情刷新和交叉权限补验；E17 是 e6d2 时点的历史局部证明，保留其证据层，不扩展成当前全流程接受。 |
| U10.2 | 运行任务进度/部分结果、取消与恢复 | P | M | V=P / A=U；旧状态/取消兼容面有，新控制链与 UI 缺失 | 接真实 worker 控制链和观察/取消 UI；完整验证 A28；cancelling 状态不能当成已停止或已退款。 |
| U10.3 | 任务历史、统计及后台/团队任务视图 | P | P | V=P / A=U；task_observation、generic/admin tasks 和团队 reporting 后端已有 | 先稳定共享 reporting 契约，再分别实现普通任务、团队及后台消费者；验证边界、来源、费用和导出一致性。 |

### U11 模型/提示词、供应商额度、积分会员与兑换

| ID | 交付内容 | 实现 | 接入 | 验证/验收 | 下一步 |
| --- | --- | --- | --- | --- | --- |
| U11.1 | 个人/系统模型与提示词 CRUD、复制、排序 | C | C | V=P / A=U；CreatorSettingsPage 和独立 port 的读写/排序已接入 | 用当前真实 API 覆盖系统/个人权限、脏稿、未知写入及排序冲突；不把连接测试等后续动作重算成 CRUD 缺失。 |
| U11.2 | 会员、套餐/联系说明、兑换及父页余额更新 | C | C | V=P / A=P；9df 已提交推送，97-02 finding 闭合；83 文件/1248 tests、TSC/build 全绿；113 两账户真实 SQL/HTTP 浏览器主路径通过 | 保留双账号拒绝重复码、简介保存回读、键盘返回/重开、390px 无横向溢出等证据；脏离开确认已弹出但取消因工具超时未证，密码提交及网络/重启浏览器变体待验，不能记为账户全完成。 |
| U11.3 | 模型连接测试、系统/个人转换、额度和账户偏好 | P | M | V=P / A=U；后端 ConnectionTester、transport、角色转换、额度/偏好接口已有 | 增加 React 操作/API；用本地 fake/owned transport 验权限、密钥处理和旧请求隔离，真实外部余额另层。 |

### U12 管理后台资源与运营配置

| ID | 交付内容 | 实现 | 接入 | 验证/验收 | 下一步 |
| --- | --- | --- | --- | --- | --- |
| U12.1 | 后台登录、会话隔离和仪表盘 | C | C | V=P / A=P；独立后台身份和 dashboard API 已接，209 已证真实管理员登录、dashboard 和 GUI 退出；服务端 session 撤销未证 | 补过期/撤销、权限及失败恢复；390px 无横向溢出已证，共享 68px 侧栏文字拥挤待修/复验。 |
| U12.2 | 系统模型 list/create/edit/delete | C | C | V=P / A=U；旧 3 个视图及删除已登记 | 用当前 SQL/API 浏览器验字段、密钥脱敏、未知写入回读和权限。 |
| U12.3 | 系统提示词 list/create/edit/delete | C | C | V=P / A=U；旧 3 个视图及删除已登记 | 验分类、原文保全、系统权限和真实 API 浏览器行为。 |
| U12.4 | 用户管理、积分/流水及关联记录 | C | C | V=P / A=U；旧用户 3 视图已登记；积分/流水/关联记录是额外扩展读取 | 验管理员自删、外键、未知写入与用户隔离；关联记录只读不等于旧全局剧集/任务资源。 |
| U12.5 | 后台剧集 3 视图和全局任务 1 视图 | P | M | V=P / A=U；admin_series/admin_tasks 已装配，关联用户任务只有部分读取 | 补独立后台 adapter、筛选/导出和写入确认；验证全局/按用户权限及前台访问保全。 |
| U12.6 | 系统配置 list/edit | C | P | V=P / A=P；32b3 已提交页面、19 字段、真实 API 与 host 接线；194 源码复核、196 前端门禁、201 真实 HTTP/owned SQL 通过，209 局部 GUI 及独立 SQL 已证 | 页面/API 接线已完成；运行配置桥接仍待，不能由保存 metadata 推断 CORS/frontend_url/worker 已生效。补脏离开取消保稿、in-flight/unknown/cross-session GUI 及移动侧栏可读性；其它消费者实际生效按原边界验。 |
| U12.7 | SMTP 账户 list/create/edit/delete、启停和计数 | P | M | V=P / A=U；加密配置与每日重置后端已有 | 补 3 视图和密钥保留/替换；接配置接口；发送行为仍归 U01.3，secret 不回显。 |
| U12.8 | 兑换码列表/筛选/导出/批量创建/删除 | C | C | V=P / A=P；129-02/03 与 148 源码复核完成；137 owned HTTP/SQL 覆盖后台方法、鉴权及 used/expired/unused 删除，144 桌面浏览器已验列表/创建 | 补验筛选、导出和 GUI 删除；CSV 下载工具超时未证，移动端及 unknown/in-flight/cross-session 浏览器负例仍待；137 的 API 删除不等于 GUI 删除，生成至用户兑换/账本闭环另待。 |
| U12.9 | 套餐 CRUD、启停、分类与排序 | C | C | V=P / A=P；129-02/03 与 148 源码复核完成；137 owned HTTP/SQL 覆盖管理 CRUD/公开读取，144 桌面浏览器已验列表、创建、编辑及 0/null/false 保存回读 | 继续补停用显示/排序及未知、在途、跨会话等浏览器负例；不虚构真实支付。 |

## 后端、运行时和跨模块工作

路由、目录和文件存在只证明登记或实现片段。以下家族保留已开发部分，工作落在真实 consumer、接线和缺失的事务/浏览器证据上。

| 家族 | 已有实现/接线 | 仍需处理 |
| --- | --- | --- |
| B-SERIES | 剧集/章节/分镜来源、访问策略、章节锁和导入导出模块已按显式 UoW/HTTP 接入 | 补跨模块 PG、多用户和对应 UI 接受；不因旧排期过期而重写。 |
| B-ASSETS | 基础素材 SQL、维护/替换模块已存在 | 接入 media upload application 与 fused runtime/storage，完成高级素材 UI 和存储链。 |
| B-PRODUCTION | 便签、粗剪和媒体协调器已有 | 新写入者消费协调器；补真实媒体预览、字节导出/输出及多用户证据。 |
| B-ACCOUNT | 账户、偏好、模型、提示词和兑换逻辑已有；会员批已并入 | 真实 SMTP sender 仍缺；各账户/运营工作流仍按 U01/U11/U12 补验证和 UI。 |
| B-TEAMS | 管理、剧集协作和 reporting 后端共 25 旧接口已装配 | 完整团队前端及多用户权限接受，不用目录存在代替授权。 |
| B-TASKS | 旧任务 read/status/cancel、通用/后台任务和 provider status 已有 | image.single 原 intent recovery/due 消费已实现；E179/E181/E185 的有界 owned PG 合成回归覆盖已声明的到期锁、双消费者、退款回滚/ACK确认和 cooperative shutdown 范围。P2/P6 可靠取消、poll/control、UI 真实状态和可验证原输出恢复仍缺；其他任务家族继续按各自消费者边界完成。 |
| B-ADMIN | 后台 API 家族已有，React 已有登录、仪表盘、16/23 个旧视图 | 完成剩余 7 个旧视图和真 SQL/浏览器动作验收；3 个积分扩展视图独立。 |
| B-GENERATION | 六入口准入、合同、凭据、图/计费；单图受限 runtime 与原 intent recovery/due consumer、文本 SSE 有实现，两步分镜有局部浏览器证据 | worker registry 五类中仅 image-single factory 有 executor 注册；另外四类需实际 executor。metadata/准入/claim 不是可执行证明。 |
| B-INTEGRATIONS | DB/provider/storage/quota/download/SMTP ports 与部分 adapter 已存在 | 补本地组合根和真实 caller；逐项区分 owned fake/loopback 与外部账号验收。 |
| B-ARCH | 扫描 106 个 domain/application 文件；多数模块使用 port/UoW 边界 | 收口 aistarslab application 直连 persistence/provider、generation_credentials application 直连 SQLAlchemy/concrete repository 的边界，同时保留现有同事务/主体约束。 |

| 集成 | 当前证据 | 后续工作/边界 |
| --- | --- | --- |
| INT-LOCAL-COMPOSITION | create_app 显式配置，lifespan 可拥有 image-single service；没有全特性 launcher 证据 | 建可复现本地组合根，明确 DB/auth/providers/media ports、readiness 和 shutdown；不依赖旧 app/env 自动发现或隐式建表。 |
| INT-DB | SQLAlchemy UoW、generation migration/readiness、credential migration 和 scoped owned PG 收据存在 | image.single 到期 recovery/due 的 owned PG 锁/双消费者、实际退款应用后回滚及 commit 后 ACK 未知确认已在 E181/E185 合成验证。继续核 continuation/cancel 受影响事务；这些收据不覆盖 PG 重启或真实数据库，不连接或写真实数据库。 |
| INT-PROVIDERS | 部分真实 transport adapter 和显式 ports 已有 | 完成剩余 adapter、loopback/fake 协议、原 credential principal/snapshot 绑定及配置消费者；实际账号/付费调用另授权。 |
| INT-STORAGE | media storage typed ports、OwnedMediaRuntime、R2 upload storage 和本地读取器存在 | 接入四类能力、upload application/fused storage/MediaStore；本地验证权限、大小/MIME、DNS、重定向、内容绑定和清理；R2 账号另授权。 |
| INT-SMTP | sender callbacks、加密 SMTP 账户配置与每日重置已有 | 开发 sender 和 owned SMTP sink；真实邮箱交付需后续适用授权，本轮不索要密钥。 |
| INT-QUOTA | provider quota/balance transports 与连接测试组件已有 | 接入 Creator Settings 配额/连接/角色动作；owned transport 本地验密钥处理，真实余额查询另授权。 |
| INT-DOWNLOAD | 兼容 GET 禁用路径和签名下载接口已有 identity resolver | 按需要实现 resolver 与本地安全下载/导出字节、命名/顺序/失败清理；链接或 200 不证明文件或可播放成片。 |
| INT-ARCHITECTURE | 106 个应用/领域文件已有静态 import 盘点 | 修复上述直接 persistence/provider 边界，并复核现有职责与同事务约束。 |

## Generation 原义务、附加义务与证据引用

完整 A01–A39 原始检查保留在 [chat-generation/spec.md](../openspec/changes/modularize-backend-chat-generation/specs/chat-generation/spec.md)；公共端口约定见 [public-contracts.md](../openspec/changes/modularize-backend-chat-generation/public-contracts.md)。本清单不复制协议全文、不重写 requiredCheck、不把接受过的修复子例当作原义务通过。完整矩阵接受仍为 0；逐项组件状态和剩余 consumer 已分别出现在 U04–U06、B-GENERATION、集成与下表。

A39 指定的 GPT-5.6 Sol/xhigh Grillme 外审仍为 OFFLINE_PENDING，尚未使用；普通 GPT-6.1 Sol 独立复核、作者测试、Root 测试/浏览器均不替代它。不探测、替换或伪记通过；release 授权与实际发布另层。该外审待办不阻止继续本地开发。

### 六项追加规范

| 引用 | 追加要求 | 映射原义务 | 当前状态与剩余主线 |
| --- | --- | --- | --- |
| N-APPEND-01（spec.md:730） | 生成后继续真实可 claim | A17/A18/A24/A26/A27/A29 | records/repository/P5 bridge 已有；真实 review/optimize producer、Work、新 phase claim/消费和已付交付续作仍缺。 |
| N-APPEND-02（spec.md:739） | 无 Task stream 的原金融证明 | A13/A14/A15/A27/A29 | HTTP stream 金融已有；补原始矩阵、single-use start、unknown commit、重复退款和无 graph 恢复。 |
| N-APPEND-03（spec.md:748） | poll 唯一预占与原 unknown truth | A24/A25/A26/A29/A32/A33 | repository、HaoAI codec/adapter 和 policy metadata 有；真实 planner/poll 消费、reservation、预算及原结果语义仍缺。 |
| N-APPEND-04（spec.md:757） | 原 policy 优先于取消 marker | A24/A27/A28/A29/A33 | 合同/schema 有；当前旧 cancel status-only 不满足新策略，需同锁、结算先行、全部 Work 停止和事务验收。 |
| N-APPEND-05（spec.md:766） | owned 完成结果和应用原子 CAS | A03/A17/A18/A21/A22/A24/A26/A38 | result facade 与 frame/asset writers 已有；真实 HTTP/worker/UI consumer、权限/目标校验和原子回执仍缺。 |
| N-APPEND-06（spec.md:775） | 四类资源 guard 与原 principal 绑定 | A10/A25/A26/A31/A32/A34/A35/A36/A37 | credential registry、四类 port 合同及媒体组件已有；剩余 family/launcher 接线与原矩阵验收仍需完成。 |

### 保留的 30 个修复子例引用

下表只保留原义务引用与来源 SHA-256；具体检查文本以原规范为准。编号和哈希来自 102 清单的 accepted_repair_subcases，不增加 A01–A39 通过数。

| 原义务 | 已接受修复子例引用及 source SHA-256 |
| --- | --- |
| A15 | A15:accepted-repair-subcase-1：62cec1ec0df95e2a00c9b80e3f9dd58618adbac4d1a63d6bd3c01fadc542ea51；A15:accepted-repair-subcase-2：96ec8c9c3d2e4acea13df44e3f68b046ffe355cf25dd5cf7bed14da61bbf9d3b；A15:accepted-repair-subcase-3：aeb6b13fe44c5bd1d7a4365987dc7ac24d61b377d4593c3105553f33ff043876 |
| A17 | A17:accepted-repair-subcase-1：e1296daa5d204d0a219f0f1cc40cb4ecf0944342e47ab1cbbf04ea70c9a6e176；A17:accepted-repair-subcase-2：0afd94b08ad1a1b851e478193216e5cf72afcf3e75136bc38f307ec478226d0a |
| A18 | A18:accepted-repair-subcase-1：69992e395d377cb4ba5f549ff1bd08192d8a2425cf005400ea55c63374767270；A18:accepted-repair-subcase-2：707f50d04c9599a6edb5c6bb97ba3c5bc75a1325ddafb5245c337588fad47a6c |
| A24 | A24:accepted-repair-subcase-1：cb6197d295b8917db91f3f74ba3225e9b1db352033aac3f79b07eafe4ab7ebce；A24:accepted-repair-subcase-2：20ce5e68e41d4c9e6ecc996934ff83c536159c1e6ea0f50369b4932e89c9a477；A24:accepted-repair-subcase-3：8497d180f0f0ee50040c30164839d5f569088fda34494987f9137f1b75578f9c |
| A26 | A26:accepted-repair-subcase-1：8f5b4d20ace62c6a8f29edf104a380d4a3534a103728f2d26440d811821bb10e；A26:accepted-repair-subcase-2：171b6d148334dc43041cca940de5bf78e01caa258084586f1cf1432d7b2e8e1c |
| A27 | A27:accepted-repair-subcase-1：2d6fb92edf76bc4d2ac2d8f4251fe4936d0c7ff6e3e815d9a97ca850a0508a91；A27:accepted-repair-subcase-2：2a940efd0e851bdcdad4332f46ad16ac3aa02fe98befc4e1b93d2a818b5b03ad；A27:accepted-repair-subcase-3：140f03faa1f5cf2c0e1353d772613ff66ee78b5845c33f69bef58324a9d0bbca；A27:accepted-repair-subcase-4：1814dbb5c4f148fd4ee9b57bec0f15075a4f654d2253e2e5b524b460de37edf5；A27:accepted-repair-subcase-5：0bd84c0306fc07775d5f9e6d35033c57d54ac98967fd7659ce9ad664890333bc |
| A28 | A28:accepted-repair-subcase-1：a51ddd5647d617fc8bdeac825e43daba20272f8b394427d90400ab73c7b07944；A28:accepted-repair-subcase-2：53b2954832d55ba76cac2de051764e2648069f1fa182a983cd5f58e8719d9d4a；A28:accepted-repair-subcase-3：7caff3faae4db9b5b3767db8d83226d4ed4075d1f38a3ed11d2e6c9403e5bb3f；A28:accepted-repair-subcase-4：3e333ee3b755afc4d6bada6c27c4ff0751fc3a37ddbbf29ab3c2f38ca3951b9a；A28:accepted-repair-subcase-5：b5928326b7562fdf06f97ba6a7a15b557074b5e339e7f3da630f57fd1e2d0e3d |
| A29 | A29:accepted-repair-subcase-1：a6fc525aac2b8e842c6e273988a542c9c51df569553112c89e2d8eea06690770；A29:accepted-repair-subcase-2：3cc5642f0f6fc9fbc6160b5afaf872e25471d741db15bdf8bc4ed7e06e827889；A29:accepted-repair-subcase-3：75114bb0292df54dbb5bbd05c2ffe1df4513bf085704ca02dd089751f2b6ac1a |
| A33 | A33:accepted-repair-subcase-1：e8d5e30f0566f1c2002fa81b3a52814ec87a127725da5cd357b8363c118b3df3；A33:accepted-repair-subcase-2：0939ee0203543ae70f1014c3a77764156a04795a58c1863fe1de6d679dcd6f5f；A33:accepted-repair-subcase-3：30da6ed110af4b45e1e4a4650f969955735f84e7b725fd3f56d3071e6171e136；A33:accepted-repair-subcase-4：2ab9109372ab3ecc3a663f2916a42c229eecacc078acd4a077bbfadcc8caba20 |
| A39 | A39:accepted-repair-subcase-1：7324579e87b5f7d09f2fbdc1e1a9b3f167e7645e60ba667aa43bffae72eab2b6 |

## 当前可界定工作包与排程边界

下表动态工作包状态截至本次清单更新。仅列现阶段可以界定的三个工作包。它们范围不同，可在既定窗口内选择并行，不能相加成整体剩余工时或整体完成日期。全目标其余未深审工作量与 calendar ETA 均为 unknown。

| 工作包 | 范围 | 粗略有效工时/信心 | 当前状态与下步 |
| --- | --- | --- | --- |
| GEN-W01 | image.single 原 intent 的独立 recovery/due 消费者 | 16–32 小时；低信心（原工作包估算，不是当前剩余工时或整体 ETA） | 已实现并提交推送 1e07f90bb203b8ff6e7b534eb0e8b22a12f5ddfc，E166 remoteExact=true。E160 三参数 SQL 边界 RED 为 2 failed/1 passed；加入 queued 恢复后 E163 为 3 passed/277 deselected。E165 全后端回归为 2434 passed、558 warnings、267 subtests passed（actualWaitExit=0、timeout=false、916 输入前后 exact、进程组及 TMP 清理）；create_app registry 的 250 methods/35 parameters 与 E88 一致。owned PG 合成批次 E179（foundation/A/G/H，4/4）与 E181（foundation/B/C/D/E/F-A/F-B，7/7）固定 cases-177；E185（foundation/B/C/E，4/4）固定 cases-183。三份收据均对应 1e07 源、同一 916 项 manifest，前后源码 exact 且 worker、PG 和临时目录清理通过。E181 F 是两个不同 worker PID 从同一持久 PG 图恢复；时钟由受控 SQL UTC 值推进，不是墙钟等待两小时，也不证明 PG/完整服务重启或任意 crash 恢复。仍待 P6 cancel control、poll、可验证原输出恢复、其他四类 worker consumer 和完整 A01–A39 验收。本工作包 16–32 小时仍是原始估算，不代表当前剩余工时或整体 ETA；本地回归不等于浏览器或真实 Provider 验收。 |
| 后台兑换码/套餐 | U12.8 与 U12.9，合计 5 个旧视图 | 8–16 小时原始估算；中低信心 | 已完成并由149收据确认提交推送 eca1978；源码复核见129-02/03、148。143是基于142冻结的前端门禁（87 files/1297 tests、TSC/build 均实际 exit 0）；137为独立真实 HTTP/owned SQL 验收，144为独立桌面浏览器验收。剩余证据缺口见末尾；8–16小时保留为原估算，不作为剩余工时或ETA；不包含真实支付。 |
| 团队管理/邀请 | U03.1 与 U03.2 首批 | 16–32 小时；低信心 | 待开发；后端接口已有。独立团队 feature/API，Workspace/services 由单一集成作者最后接线；团队剧集、reporting 另列。 |

其余 family、画布、媒体导出、真实 provider/SMTP/R2 和完整 A01–A39 接受未逐项深审，因此整体剩余工时和 ETA 保持未知，不复用 10 月 9 日的周数或日期，也不按 routes/files 数量线性外推。

共享 writer 协作仍按北京时间每日 12:00–14:00、20:00–次日 09:00 的窗口；窗口外在安全边界串行收口，不新建自动化。U03.4 与 U10.3 先依赖共同稳定的 reporting 契约，再各自建设消费者，避免相互依赖循环。GEN-W01 的 image.single 原 intent recovery/due 消费已落地；随后继续扩展 review/optimize 的真实 Work 和 continuation。结果应用、媒体、画布、缺项工作流按真实消费者边界衔接。

## 特殊证据边界与最终阶段

E17 的源实现提交为 e6d2f66f88d660a7bf4550f2062495b878426ca0；它在真实 Target API、合成 SQLite/JWT、本地浏览器范围内证明了角色/场景/道具三类素材上合计 POST 3 次、PUT 3 次、DELETE 3 次，共 9 次写入、跨两章只移除目标引用并保留其他章节/帧内容、任务详情 18 条结果帧脱敏，以及过期会员可读结果而原请求返回 403。它证明该历史分支的局部通过；当前共享 client 已继续更新，需保留历史证据并只验证受后续变化影响的分支。

U11.2 的最新证据来自 9df：MEMBERSHIP97-I01 已由 97-02 finding 闭合；完整前端门禁 83 files/1248 tests、TSC、build 全绿；113 在两账户、owned SQL/真实 loopback HTTP 上通过兑换主路径、同码同/异账户拒绝、简介保存回读、键盘返回/重开、390px 无横向溢出和账户隔离。dirty-leave 确认窗已出现，但因浏览器工具超时未验证取消行为；密码提交、网络/重启 browser 变体仍未完成。故账户工作流不是全部完成。

局部状态另外保留：E17 的直接跨域连接偶发差异仍未定位根因，同源 proxy 路径已验；章节锁局部 badge/交互有真实本地浏览器验收。两项都不扩展成全站 CORS 或全部锁流程已验收。

后台会员管理批次的验收层彼此独立：136 是基于前端冻结135的门禁（87 files/1295 tests，TSC、Vitest、build 实际 exit 0）；137 是基于同一前端冻结与 backend 5ca522c 的真实路由/owned SQL 检查，15 组、32 个真实 HTTP 请求通过，后台9种操作成功响应 exact 200，鉴权覆盖401/403，套餐/兑换码字段覆盖 0、null、false 和空字符串，used/expired/unused 码删除均通过；删除后两账户积分13/4、账本0、会员free不变。143 是更新的前端冻结142门禁（87 files/1297 tests，TSC、Vitest、build 实际 exit 0）；147 仅将11个新目录文件各去掉一个EOF LF，148证明可逆，因此143未在格式收尾后的字节上重跑。144 是独立桌面浏览器正向验收：套餐 list/create/edit、兑换码 list/create，0/null/false 保存回读和确认后离开；最终 SQL 为套餐5、兑换码6，两账户积分13/4、账本0、会员free不变，并留有桌面截图。144因硬TTL以exit 1结束，但进程组、端口、临时目录和凭据已清理，不把整个run记为PASS。CSV下载工具等待超时未验，移动端、unknown/in-flight/cross-session浏览器负例、GUI删除及生成码到用户兑换闭环仍待；137的API删除不能替代GUI删除。

系统配置 list/edit 批次已提交推送至 main@32b3f721a8436a270b58b73c2a2c138ca8dc6b5a。194 为源码复核；196 基于 233 项前端输入，90 files/1324 tests、TSC/build 均 actual exit 0，输入前后 exact、进程组和 TMP 清理通过。201 为真实 loopback HTTP/owned SQLite，21 请求/11 检查通过，19 字段转换、隐藏/非目标配置及 wallet/ledger 保全分层核对；统计经既有 BalanceReader fake 隔绝默认 127.0.0.1:8765，不代表真实余额上游。209 在同一 233 前端/916 后端冻结上完成 nonce/PID 和 HTML 探针，Root 实际 GUI 已验管理员登录、dashboard、19 字段装载、5 字段保存确认及保存后 disabled；独立 SQL 仅这 5 字段改变，其它目标字段、非目标行、users/wallet/ledger 不变，0 存储与回包默认 3 差异已证。桌面及 390px JPG 已留，client/scroll/body 宽均 390、无横向溢出，但 68px 共享侧栏文字拥挤仍待修。dirty 确认弹窗已见，dismiss 工具超时，未证取消保稿；in-flight/unknown/cross-session GUI、服务端 logout 撤销仍待。两个 owned tab 均已关闭并核 absent、viewport 已 reset。209 supervisor 实际 exit 0 且 lifecycle-stopped，233/916 source/snapshot/Git/保护尾 exact，groups/ports/TMP 及凭据清理通过；Root UI receipt 明确 partial，生命周期成功不等于全部 UI 接受，更不等于部署。204 phase A 仅在独立工作树进行，尚未合并到本基线，不增加 main 的 executor 登记或 A01–A39 接受数。后续修复与定向复核在隔离工作树推进，尚未并入 main，不增加 main 的 worker factory 登记数。

生成原规范另有 5 种 source worker type；当前默认 factory 只有 image-single 实际登记 executor，其他四类只有准入/策略/元数据不等于可执行。BATCH/优化/视频/提取等完整 consumer 和原始 A01–A39 矩阵仍待完成。image.single 的真实 ASGI auth→fake Provider 500→reconciling→原 deadline 后 failed/refund 路径已由本地合成 SQLite/fake 用例覆盖；非空 unknown 前后保留原 first/deadline、及时成功 retained、BYOK zero 不建 wallet、唯一退款后合法消费的 replay、commit-ACK/rollback 和 cooperative shutdown 也有合成用例。这不是浏览器或真实 Provider 证据。PG owned 合成收据 E179/E181（cases-177）与 E185（cases-183）在同一 916 项 1e07 源清单上通过各自选定范围，覆盖具体到期锁/双消费者、退款回滚与 ACK 确认及 shutdown gate。E181 F 是两个不同 worker PID 读取同一持久 PG 图，不代表 PG 或完整服务重启、任意 crash；受控 SQL UTC 时钟不是墙钟两小时等待。P6 cancel HTTP/control、poll、可验证原输出恢复及其余四类 worker 仍待完成。

全部 backend 与 12 类 React 工作流、跨用户/权限/故障恢复及适用 owned 验收完成后，最后才做后端语言评估。本批明确授权仅涵盖本地源码和临时 SQLite/PostgreSQL 合成测试。真实数据库写入、资金操作、Provider/付费调用、SMTP 真实发送、R2 账号/存储及生产部署仍是各自独立授权的验收层；本地实现与 owned/fake/loopback 验证继续推进。现在不索取密钥；外部等待不计入有效开发工时，也不视为整体目标已完成。
