# HaoAI-next 完整剩余工作清单与排期

更新日期：2026-10-19（北京时间）

本文以 2026-10-19 的主线源码与已接受收据校准完整剩余范围。它是唯一的迁移清单，不是新的架构设计，也不代表整站功能已验收。

## 当前基线与计数口径

本文原始范围基线为 main@9df03f0c083236b9b4bb18b722ad95fd9839a144（110 收据确认当时远端精确一致）；102 源码分析基线仍为 backend 5ca522cdc20ec2843fef48cd0d9a68f8ee128c78。102、104、110、113、116 的历史范围与证据归属保留；会员、系统配置、后台和早期全量检查等历史收据仍按各自源码时点解释。功能整合基线为 `e094224e7751ba92c25d22fe17d51e89bb15a264`；Root收据395确认该批整合后 main 与 AI 分支远端均精确指向该提交。e094整合团队管理13个方法、团队剧集协作7个方法及 frame.review 后端链。前一桌面卡片CSS与清单回写已由Root400确认推送至 main@`d91d98a46ea117fcbea3cd0d8a0c4fb5a5e7688e`；本批V02移动topbar CSS尚待中文提交。本文后附的最新记录区分这些新增源码、分层验证与尚未完成范围；102 的完整分析文件为 haoai-next-current-full-remaining-inventory-102.json，116 为 haoai-next-root-development-continuation-116.json。两者及原始验收收据均在任务证据目录，本文不改动它们。

固定旧源共有 248 个显式 method/path 声明；backend385 收据中的 create_app registry 为 250 个方法、36 个参数项，旧声明已登记差集为0。这里证明路由登记，不证明请求行为、完整实现、端到端运行或浏览器接受。固定旧后台有 9 个资源、23 个资源视图；当前 React 侧 23/23 个旧资源视图均已实现并接入源码，浏览器全面验收仍未完成。登录和仪表盘独立于这 23 个资源视图。用户积分、用户积分编辑、积分流水 3 个扩展视图单独计算，不冲抵这 23 个旧资源视图。

清单采用彼此独立且有重叠的计数：12 类工作流、51 个 UI 交付行。102 初始快照统计 33 行有开发动作、28 行有接线动作、51 行有验证或接受动作；116 覆盖 U11.2 后为 32、28、51。U12.8/U12.9 完成两行开发和接线后为 30、26、51；U12.6 再闭合开发动作后为 29、26、51。U12.5 与 U12.7 又各完成一行开发和接线后为27、24、51。e094将 U03.1、U03.2、U03.3 各一行的开发与接线义务并入主线，当前为24、21、51：开发 `27−3=24`，接线 `24−3=21`，验证/接受仍为51。U12.6 的运行时消费者接线仍待完成。原 action 统计按 ui_workflows[].deliverables[].remaining_actions.kind 对各交付行逐行去重后计数；非 C 单元格数不是原 action 统计；局部验收不减少 51 行各自的验证/接受义务。生成原始义务 A01–A39 共 39 项、附加规范义务 6 项、原规范修复子例引用 30 项、后台 9 个资源/23 个视图另行计数。这些分母彼此重叠，不相加，也不用于推算完成百分比。特别是 A01–A39 整项完整矩阵接受数为 0；这不表示相关组件为零，已有组件、接线和局部证据仍按下表分别保留。

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
| U03.1 | 团队创建/编辑/删除、详情、成员及角色权限 | C | C | V=P / A=U；13个团队管理/邀请方法对应UI与HTTP源码已整合到e094，并有真实HTTP与局部GUI证据；解散确认结果仍未知 | 补团队解散确认与完整跨会话/失败分支验收；覆盖 owner/admin/member/非成员，不把已接源码扩大为整行接受。 |
| U03.2 | 邀请生成、列表、撤销、加入、离开与配额 | C | C | V=P / A=P；邀请、加入/离开及配额对应UI与HTTP源码已整合，真实HTTP和浏览器正向闭环有局部证据 | 补完整邀请/配额负例、unknown/in-flight/cross-session浏览器分支；授权仍由后端决定。 |
| U03.3 | 团队剧集共享/撤回、认领/取消、创建和转移 | C | C | V=P / A=U；7个团队剧集方法及对应UI/HTTP源码已整合到e094，整行多用户GUI接受仍不足 | 补未覆盖确认/未知/跨页/跨会话、多用户权限及生成/编辑/认可交叉验收。 |
| U03.4 | 成员任务、团队/剧集/模型用量与导出 | P | M | V=P / A=U；reporting 5 接口已装配 | 先稳定共同 reporting 契约，再各自实现用量/导出 UI 与任务 UI；分别验证跨团队隔离、金额和导出一致性。 |

### U04 分镜编辑、AI 复核优化与结果应用

| ID | 交付内容 | 实现 | 接入 | 验证/验收 | 下一步 |
| --- | --- | --- | --- | --- | --- |
| U04.1 | 分镜浏览、文字/结构编辑及素材引用导航 | C | C | V=P / A=P；编辑器、引用面板及历史正文/排序主链已证 | 补结构插入/删除、引用清理、未知写入和团队权限的当前真实后端浏览器分支。 |
| U04.2 | AI 复核/批量优化、上下文、部分成功与统计 | P | P | V=P / A=U；frame.review 后端链已接，frame.optimize、React AI入口和统计整体仍待 | 补 frame.optimize 与其它 family producer/consumer，连接 React 状态、来源和错误展示；保持 review 子范围证据与整体未完状态分开。 |
| U04.3 | 生成结果应用、媒体版本与认可失效 | P | P | V=P / A=U；frame.review 的 P5 apply/fee consumer 已接；其它 family、媒体版本和完整 UI 仍待 | 补剩余结果家族、媒体版本/认可失效与 UI 流程；验证稳定 ID、no-ID、重复、迟到和回执未知，不重写已有 CAS。 |

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
| U06.5 | Worker 可靠性、金融、结果应用、取消和重启恢复 | P | P | V=P / A=U；image.single 原链与 frame.review 独立注入后端链各有局部实现；总体多 family、React 控制与完整恢复接受仍待 | image.single 到期恢复的有界PG证据见E179/E181/E185；frame.review 的 I06 recovery、C01 shutdown、I07 cancel另有e094/PG392范围证据。它们不证明完整服务重启或任意crash恢复。其它family consumer、React控制、可验证原输出恢复、六项附加义务和完整A01–A39验收仍待；U06.5宽范围仍为P/P、V=P/A=U。 |
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
| U12.1 | 后台登录、会话隔离和仪表盘 | C | C | V=P / A=P；独立后台身份和 dashboard API 已接，209 已证真实管理员登录、dashboard 和 GUI 退出；服务端 session 撤销未证 | 补过期/撤销、权限及失败恢复；390px 无横向溢出已证。209 曾见共享 68px 侧栏文字拥挤，移动 CSS 已在 c8f 修复，仍待新 GUI 复验。 |
| U12.2 | 系统模型 list/create/edit/delete | C | C | V=P / A=U；旧 3 个视图及删除均已接入源码 | 用当前 SQL/API 浏览器验字段、密钥脱敏、未知写入回读和权限。 |
| U12.3 | 系统提示词 list/create/edit/delete | C | C | V=P / A=U；旧 3 个视图及删除均已接入源码 | 验分类、原文保全、系统权限和真实 API 浏览器行为。 |
| U12.4 | 用户管理、积分/流水及关联记录 | C | C | V=P / A=U；旧用户 3 视图已接入源码；积分/流水/关联记录是额外扩展读取 | 验管理员自删、外键、未知写入与用户隔离；关联记录只读不等于旧全局剧集/任务资源。 |
| U12.5 | 后台剧集 3 视图和全局任务 1 视图 | C | C | V=P / A=U；剧集功能与只读 TaskList 已接入。232/233 为剧集局部 HTTP/GUI 证据，233 不是全量浏览器验收；移动 CSS 在 c8f 修复后仍待 GUI 复验。全局任务列表真实 HTTP/浏览器验收仍待。 | 补 TaskList 的真实 HTTP/owned SQL 与浏览器验收，覆盖筛选、服务端分页/排序、当前 query 全量 CSV、身份隔离和迟到导出；剧集补移动视觉复验与剩余全流程/权限验收。 |
| U12.6 | 系统配置 list/edit | C | P | V=P / A=P；32b3 已提交页面、19 字段、真实 API 与 host 接线；194 源码复核、196 前端门禁、201 真实 HTTP/owned SQL 通过，209 局部 GUI 及独立 SQL 已证 | 页面与 API 接线已完成；运行配置消费者桥接仍待，不能由保存 metadata 推断 CORS/frontend_url/worker 已生效。补脏离开取消保稿、in-flight/unknown/cross-session GUI；移动 CSS 已源码修复但尚待新 GUI 复验，其它消费者实际生效按原边界验。 |
| U12.7 | SMTP 账户 list/create/edit/delete、启停和计数 | C | C | V=P / A=U；SMTP 账户管理前端与 host 已接入并由 78a4d9f 推送；真实 HTTP/owned SQL、浏览器 CRUD 尚未验收，未证明真实 SMTP 发送 | 补列表和 CRUD 的真实 HTTP/owned SQL 及浏览器验收，覆盖密钥保留/替换与未知退出；发送行为仍归 U01.3，secret 不回显，真实邮件另需适用授权。 |
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
| B-TEAMS | 团队管理/邀请13方法与团队剧集协作7方法已随e094进入主线；reporting仍未接入对应UI/消费者 | 补多用户权限与团队剧集的完整接受；完成U03.4 reporting，不用目录或源码接入代替授权。 |
| B-TASKS | 旧任务 read/status/cancel、通用/后台任务和 provider status 已有；只读全局 TaskList 页面已接入 | image.single 原 intent recovery/due 消费及 frame.review 独立后端链已实现。E179/E181/E185覆盖所列 image.single 有界范围；PG392覆盖 frame.review 的真实owned取消/重放五类场景（late正价与0价）。TaskList HTTP/浏览器、完整服务重启、其它任务家族和UI仍待；不将两个局部PG范围提升为完整任务工作流接受。 |
| B-ADMIN | 后台 API 家族已有，React 登录、仪表盘和 23/23 个旧资源视图均已实现并接入源码 | 完成全面浏览器、真实 HTTP/SQL 与跨会话/未知结果验收；剧集已有局部验收，TaskList 与 SMTP 真实 HTTP/浏览器仍待。3 个积分扩展视图独立。 |
| B-GENERATION | 六入口准入、合同、凭据、图/计费；image.single受限runtime与recovery/due、文本SSE、两步分镜局部浏览器证据，以及frame.review后端链均已接 | frame.optimize、其它family worker和React AI入口仍需完成；metadata/准入/claim不能替代真实producer、consumer或完整工作流证据。 |
| B-INTEGRATIONS | DB/provider/storage/quota/download/SMTP ports 与部分 adapter 已存在 | 补本地组合根和真实 caller；逐项区分 owned fake/loopback 与外部账号验收。 |
| B-ARCH | 扫描 106 个 domain/application 文件；多数模块使用 port/UoW 边界 | 收口 aistarslab application 直连 persistence/provider、generation_credentials application 直连 SQLAlchemy/concrete repository 的边界，同时保留现有同事务/主体约束。 |

| 集成 | 当前证据 | 后续工作/边界 |
| --- | --- | --- |
| INT-LOCAL-COMPOSITION | create_app 显式配置，lifespan 可拥有 image-single service；frame.review各边界也采用显式依赖注入；没有全特性 launcher 证据 | 建可复现的全特性本地组合根，明确 DB/auth/providers/media ports、readiness 和 shutdown；不依赖旧 app/env 自动发现或隐式建表。 |
| INT-DB | SQLAlchemy UoW、generation migration/readiness、credential migration 和 scoped owned PG 收据存在 | image.single 到期 recovery/due 的owned PG范围见E181/E185；frame.review I07的五类owned PG范围由PG392确认通过并清理。两者不覆盖完整服务/PG重启或真实数据库，不连接或写真实数据库。 |
| INT-PROVIDERS | 部分真实 transport adapter 和显式 ports 已有 | 完成剩余 adapter、loopback/fake 协议、原 credential principal/snapshot 绑定及配置消费者；实际账号/付费调用另授权。 |
| INT-STORAGE | media storage typed ports、OwnedMediaRuntime、R2 upload storage 和本地读取器存在 | 接入四类能力、upload application/fused storage/MediaStore；本地验证权限、大小/MIME、DNS、重定向、内容绑定和清理；R2 账号另授权。 |
| INT-SMTP | sender callbacks、加密 SMTP 账户配置/每日重置及后台账户管理页面已有并接入 | 先完成账户管理真实 HTTP/owned SQL 与浏览器验收；开发 sender 并用 owned SMTP sink 验输送语义。真实邮箱交付需后续适用授权，本轮不索要密钥。 |
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
| 后台兑换码/套餐 | U12.8 与 U12.9，合计 5 个旧视图 | 8–16 小时原始估算；中低信心 | 已完成并由149收据确认提交推送 eca1978；源码复核见129-02/03、148。143是基于142冻结的前端门禁（87 files/1297 tests、TSC/build 均实际 exit 0）；137为独立真实 HTTP/owned SQL 验收，144为独立桌面浏览器验收。最新全前端门禁284另行覆盖当前 main。剩余证据缺口见末尾；8–16小时保留为原估算，不作为剩余工时或ETA；不包含真实支付。 |
| 团队管理/邀请 | U03.1–U03.3 | 16–32 小时；低信心（原估算） | 13个管理/邀请方法与7个团队剧集方法已在e094整合到main；GUI376历史收据仍为PARTIAL。GUI399已通过卡片V01视觉检查，GUI405已通过移动topbar V02窄视觉检查；解散确认对话框、跨页正向及完整跨会话场景未验。U03.4 reporting仍待。该估算是原工作包估算，不表示当前剩余工时或整体ETA。 |

其余 family、画布、媒体导出、真实 provider/SMTP/R2 和完整 A01–A39 接受未逐项深审，因此整体剩余工时和 ETA 保持未知，不复用 10 月 9 日的周数或日期，也不按 routes/files 数量线性外推。

共享 writer 协作仍按北京时间每日 12:00–14:00、20:00–次日 09:00 的窗口；窗口外在安全边界串行收口，不新建自动化。U03.4 与 U10.3 先依赖共同稳定的 reporting 契约，再各自建设消费者，避免相互依赖循环。GEN-W01 的 image.single 原 intent recovery/due 消费已落地；frame.review 后端链亦已接入，后续继续完成 frame.optimize、React AI入口及其它 family。结果应用、媒体、画布、缺项工作流按真实消费者边界衔接。

## 特殊证据边界与最终阶段

E17 的源实现提交为 e6d2f66f88d660a7bf4550f2062495b878426ca0；它在真实 Target API、合成 SQLite/JWT、本地浏览器范围内证明了角色/场景/道具三类素材上合计 POST 3 次、PUT 3 次、DELETE 3 次，共 9 次写入、跨两章只移除目标引用并保留其他章节/帧内容、任务详情 18 条结果帧脱敏，以及过期会员可读结果而原请求返回 403。它证明该历史分支的局部通过；当前共享 client 已继续更新，需保留历史证据并只验证受后续变化影响的分支。

U11.2 的最新证据来自 9df：MEMBERSHIP97-I01 已由 97-02 finding 闭合；完整前端门禁 83 files/1248 tests、TSC、build 全绿；113 在两账户、owned SQL/真实 loopback HTTP 上通过兑换主路径、同码同/异账户拒绝、简介保存回读、键盘返回/重开、390px 无横向溢出和账户隔离。dirty-leave 确认窗已出现，但因浏览器工具超时未验证取消行为；密码提交、网络/重启 browser 变体仍未完成。故账户工作流不是全部完成。

局部状态另外保留：E17 的直接跨域连接偶发差异仍未定位根因，同源 proxy 路径已验；章节锁局部 badge/交互有真实本地浏览器验收。两项都不扩展成全站 CORS 或全部锁流程已验收。

后台会员管理批次的验收层彼此独立：136 是基于前端冻结135的门禁（87 files/1295 tests，TSC、Vitest、build 实际 exit 0）；137 是基于同一前端冻结与 backend 5ca522c 的真实路由/owned SQL 检查，15 组、32 个真实 HTTP 请求通过，后台9种操作成功响应 exact 200，鉴权覆盖401/403，套餐/兑换码字段覆盖 0、null、false 和空字符串，used/expired/unused 码删除均通过；删除后两账户积分13/4、账本0、会员free不变。143 是更新的前端冻结142门禁（87 files/1297 tests，TSC、Vitest、build 实际 exit 0）；147 仅将11个新目录文件各去掉一个EOF LF，148证明可逆，因此143未在格式收尾后的字节上重跑。144 是独立桌面浏览器正向验收：套餐 list/create/edit、兑换码 list/create，0/null/false 保存回读和确认后离开；最终 SQL 为套餐5、兑换码6，两账户积分13/4、账本0、会员free不变，并留有桌面截图。144因硬TTL以exit 1结束，但进程组、端口、临时目录和凭据已清理，不把整个run记为PASS。CSV下载工具等待超时未验，移动端、unknown/in-flight/cross-session浏览器负例、GUI删除及生成码到用户兑换闭环仍待；137的API删除不能替代GUI删除。

系统配置 list/edit 批次已提交推送至 main@32b3f721a8436a270b58b73c2a2c138ca8dc6b5a。194 为源码复核；196 基于 233 项前端输入，90 files/1324 tests、TSC/build 均 actual exit 0，输入前后 exact、进程组和 TMP 清理通过。201 为真实 loopback HTTP/owned SQLite，21 请求/11 检查通过，19 字段转换、隐藏/非目标配置及 wallet/ledger 保全分层核对；统计经既有 BalanceReader fake 隔绝默认 127.0.0.1:8765，不代表真实余额上游。209 在同一 233 前端/916 后端冻结上完成 nonce/PID 和 HTML 探针，Root 实际 GUI 已验管理员登录、dashboard、19 字段装载、5 字段保存确认及保存后 disabled；独立 SQL 仅这 5 字段改变，其它目标字段、非目标行、users/wallet/ledger 不变，0 存储与回包默认 3 差异已证。桌面及 390px JPG 已留，client/scroll/body 宽均 390、无横向溢出；209 当时所见 68px 共享侧栏拥挤后已由移动 CSS 源码修复，仍待新 GUI 复验。dirty 确认弹窗已见，dismiss 工具超时，未证取消保稿；in-flight/unknown/cross-session GUI、服务端 logout 撤销仍待。两个 owned tab 均已关闭并核 absent、viewport 已 reset。209 supervisor 实际 exit 0 且 lifecycle-stopped，233/916 source/snapshot/Git/保护尾 exact，groups/ports/TMP 及凭据清理通过；Root UI receipt 明确 partial，生命周期成功不等于全部 UI 接受，更不等于部署。历史204 phase A与 backend AIreview 279 在当时尚未并入 main；后续 frame.review 合入情况见本节后附的最新整合记录。A01–A39 完整矩阵接受数仍为 0。

后台剧集、全局任务和 SMTP 账户页已接入当前 main：剧集为后台 3 视图，另有 232/233 局部 HTTP/GUI 收据；c8f 修复移动导航与剧集表格可读性，尚待 GUI 复验。全局 TaskList 于 3e8c525b41f064b0feecc60fe74005f6931c579f、SMTP 账户管理于 78a4d9f877eaf8c964a4f7c69a656b5b9bd5f5a5 合入并推送；TaskList 与 SMTP 的真实 HTTP/owned SQL 和浏览器验收仍待，SMTP 真实发送不在此证据内。由此 23/23 旧后台资源视图源码已接入，不等于浏览器全面验收。

前端284全量门禁收据 haoai-next-root-admin-final-full-frontend-284.json（SHA-256 a7531451de9d20d3e517c6b934bd037d02346e079bc557f22ab98fb64e10d747）显示 263 项输入前后 exact；TSC、Vitest 102 files/1404 tests、build 均 actual exit 0，进程组清空且临时目录移除。历史274全量门禁为 1403 passed，唯一失败是同一 Workspace 用例在默认 5000ms 下以 5493ms 超时；282 使用临时隔离配置重跑单例与 Workspace 75 项，未复现，但不是根因定位。284 使用原配置与默认 5000ms 全量通过；根因仍未证，不能称已定位修复。

生成系统的多个 source worker type仍未全部接通；image-single与frame.review分别有已验证的局部后端链，frame.optimize、批量/视频/提取等其它 family consumer 与React AI入口仍待。准入、策略、metadata或claim不能替代真实producer和consumer，也不能替代完整A01–A39矩阵。image.single 的真实 ASGI auth→fake Provider 500→reconciling→原 deadline 后 failed/refund 路径已由本地合成 SQLite/fake 用例覆盖；非空 unknown 前后保留原 first/deadline、及时成功 retained、BYOK zero 不建 wallet、唯一退款后合法消费的 replay、commit-ACK/rollback 和 cooperative shutdown 也有合成用例。这不是浏览器或真实 Provider 证据。PG owned 合成收据 E179/E181（cases-177）与 E185（cases-183）在同一 916 项 1e07 源清单上通过各自选定范围，覆盖具体到期锁/双消费者、退款回滚与 ACK 确认及 shutdown gate。E181 F 是两个不同 worker PID 读取同一持久 PG 图，不代表 PG 或完整服务重启、任意 crash；受控 SQL UTC 时钟不是墙钟两小时等待。frame.review I07 PG392的局部结果见最新整合记录；其它任务 family 的 cancel/control、poll、可验证原输出恢复仍待按各自范围完成。

全部 backend 与 12 类 React 工作流、跨用户/权限/故障恢复及适用 owned 验收完成后，最后才做后端语言评估。真实数据库写入、资金操作、Provider/付费调用、SMTP 真实发送、R2 账号/存储及生产部署仍是各自独立授权的验收层；本清单的局部源码、测试、HTTP/SQL 或浏览器证据均不扩大这些授权。

## 团队管理与邀请开发批交接（2026-10-17）

隔离分支 `codex/team-management-294` 已实现并接入团队管理、成员角色与六项权限、邀请生成/读取/撤销、加入/离开等 13 个方法，包含 Workspace 导航、同步草稿保护和 SeriesPage 团队目录失效通知。团队剧集协作 7 方法、reporting 5 方法及其页面仍待后续。本批尚未合入 main，因此前文按主分支统计的 27 行开发、24 行接线、51 行验证/接受义务保持原口径，不提前扣减。

源码复核305发现同团队切换面板后旧邀请码读取可迟到登出并丢弃新草稿，以及共名按钮样式缺少局部作用域。修复306先取得4项真实 RED，再在修复后取得4项 GREEN；独立复核308确认两项关闭。相关团队/适配器/SeriesPage 定向套件为56项通过，Workspace真实适配器与假fetch宿主回归为2项通过、75项按名称过滤；TypeScript检查通过。上述宿主证据属于假传输测试，不代表真实后端HTTP、JWT或SQL验收。

Root完整前端检查309固定271项输入，TypeScript通过，Vitest为1267通过、162失败：154项对应本地HTTP夹具启动被拒绝（listen EPERM），另8项超时。随后310仅修正本地监听执行权限，使用相同源码、原项目配置和默认5000ms测试超时；该次Vitest达到420秒进程上限后被终止，没有完整汇总，不能记作全量通过。旧界面超时根因仍未证实。独立生产构建313实际exit 0；各次检查源码前后均一致。310即时进程组检查为未清空，312后续实测该组已消失，无需发送清理信号；自有依赖链接目录、构建目录和临时目录已移除。

本批以独立分支开发检查点保存，当前main基线仍为 `bedffc03f94adb8368dfd6bdd38c99eac9e53eff`。完整前端回归、这13个管理/邀请接口的真实后端HTTP与owned SQL、多账号权限/会员/JoinQuota，以及桌面和移动浏览器验收均待完成；尚未部署。按用户要求，保存本批提交并推送后暂停整体开发，后续恢复时从这些待验收事项接续。

### 恢复开发与前端回归（2026-10-19）

用户已恢复开发。相同源码的首次完整回归为1428通过、1项Workspace超时；该次失败收据保留。单例CPU采样显示，重复全局角色查询中的无障碍名称与样式计算占用较多时间；这只说明该采样的热点，不证明全部偶发超时的原因。随后将三处Workspace流程和续作流程的相关查询限定到已有具名区域或筛选组，并为粗剪用例既有异步断言补上await。原有交互、卡片数量、隐藏列表无障碍断言及异步标题等待均保留。

冻结271项输入后的完整前端检查 `haoai-next-team-resume-20261019-query-full.json` 实际通过：104个测试文件、1429个用例，TypeScript与生产构建均exit 0。使用原配置、单worker和默认单条5000ms超时；900秒外层进程期限未触发。源码前后精确一致，自有进程、依赖链接目录、构建输出和临时目录均已清理。独立复核318通过；仍保留既有act和构建chunk警告。本次只修改测试查询与异步断言，不改变产品行为。

截至该阶段，下一步是这13个团队管理/邀请方法的真实Target HTTP、owned SQL、多账号权限/会员/JoinQuota及桌面、移动浏览器验收；当时团队分支尚未合入main，主分支计数因此保持原口径。该状态随后由e094合入更新，见下方最新主线记录。本次前端通过不代表整个项目、浏览器或发布验收完成。

### 团队真实HTTP与浏览器验收进展（2026-10-19）

前端测试修复已以中文提交 `ee9f9cb603508324538ae81a4418df1201421c39` 推送至团队分支，远端核对一致。夹具319经独立复核320发现两处与真实权限/邀请码保留语义不符的断言，以及进程组清空核验缺项；集中修复后第二轮复核通过，产品行为未改。

Root收据 `haoai-next-root-team-http-321.json` 对应该提交、271项前端及916项后端冻结输入：实际create_app、真实bcrypt登录/JWT/session、显式InMemoryJoinQuota与owned临时SQLite上，13方法、67次请求、8组HTTP/独立SQL检查全部通过，实际exit 0。覆盖角色/六权限及清空、邀请码生成/使用/作废/过期、会员/权限/数量/限流拒绝、退出/移除的锁与保留认领、解散的原detach顺序；指定合成用户、钱包、账本和非目标团队图守恒。请求状态包含200/201/400/401/403/404/422/429/503。进程组、端口和自有临时目录已实际清理，前后源码一致。显式注入配额不证明默认运行装配已启用配额。

浏览器323已实际验证两账号登录、创建/改名/目录/详情、GUI生成码到加入闭环、复制与关闭回执、角色提升、六权限保存及空权限清空、普通成员与零权限管理员入口限制、作废/读取邀请码、退出/重新加入/移除成员。独立SQL确认相关成员/权限/邀请码状态；空权限按既有后端语义存NULL、接口返回空数组。未保存名称的离开取消与退出取消已保留页面状态。390px下权限页布局可读，无横向溢出，移动端返回及团队入口可用；桌面与移动截图已保留。

解散确认的控制结果仍未知：323尝试取消时控制调用超时，恢复后团队确已解散；保护用户/钱包/账本行哈希前后相同，但不能把这次取消或有意确认记为通过。独立诊断324未发现确定的产品源码缺陷。全新夹具325分步复验读取到confirm，随后dismiss返回对话框已失效，AX读取又超时；独立SQL确认当时团队/成员/邀请及保护行与操作前精确一致，但未取得取消完成回执，仍不能记为通过。没有为自动化修改产品确认逻辑。

323与325的前后端进程组、端口、私有凭据和临时目录已实际清理；323标签已关闭、视口已恢复。325自有标签4关闭调用超时，最终列表仍可见该失去服务的标签，标签清理单列待处理。浏览器完整在途/未知写/跨会话负例，以及解散取消和明确确认的独立复验仍待。该段记录e094合入前的隔离状态，后续主线状态见下节；未部署。

### 主线整合与 frame.review 后端链（2026-10-19）

Root 收据395确认中文整合提交 `e094224e7751ba92c25d22fe17d51e89bb15a264` 在 main 与 AI 分支远端均精确一致。团队管理/邀请13方法及团队剧集协作7方法对应的UI与HTTP源码现已接入主线；U03.1、U03.2、U03.3 的实现与接线状态均为 C/C。它们的验证与接受状态仍按各自证据边界保留在表中：HTTP与GUI有局部正向证据，解散确认、跨会话/unknown 分支及完整团队剧集多用户接受仍有缺口；U03.4 reporting仍待。

同一整合提交包含显式依赖注入的 frame.review 后端路径：真实准入→claim/intent/result→P5应用与费用→I06 recovery/C01 shutdown→I07用户取消。Root385验证的944项后端源与整合后后端逐项exact；394确认可复用该源对应的2568 tests、267 subtests及registry250/36结果，未在e094重新执行。Root392的五类真实 owned PostgreSQL I07 场景通过，late case覆盖正价与0价，且资源清理通过。这些结果只覆盖该后端子路径；frame.optimize、React AI入口、其它 worker family和完整 A01–A39 接受仍未完成。

前端371门禁为109个文件、1474项通过，TypeScript与生产构建均exit 0。GUI376历史收据仍为 PARTIAL；Root399已通过团队卡片V01视觉检查：1265px桌面六类操作自然换行，390px移动视口内六类操作可见且无横向溢出，导出长提示可折行。Root405报告 `PASS_V02_CSS_VISUAL_ONLY`：390px下六个导航控制完整横排；320px下合法20字中文剧集名面包屑自然换行且无页面横向溢出；1265px桌面header保持row布局、移动按钮隐藏、六类卡片操作保持正常。405使用已审363/334与旧916 backend fixture，不是main 944整站验收。两批仅证明限定视觉；导出下载文件字节、团队解散确认对话框、跨页正向及完整跨会话场景仍未验证，不把局部视觉结果提升为整体GUI PASS。

全量矩阵边界保持不变：A01–A39完整矩阵接受数为0，A39指定的 GPT-5.6 Sol/xhigh Grillme 外审仍为 OFFLINE_PENDING。上述源码接入、局部后端/前端检查及 owned PG 证据均不提升整站完成度，也不产生新的整体 ETA。
