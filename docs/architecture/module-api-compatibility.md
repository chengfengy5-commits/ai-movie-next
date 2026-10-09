# 模块与 API 兼容边界

本文件保留首切片的历史边界，并记录已批准的当前及后续迁移契约。旧库路径为 `/Users/yanghaibo/data/projects/ai/haoai-修改版/haoai.zhuwh.com`，来源提交固定为 `23403806898550a7668a6ee7c0c457315655c39b`。下列行号均来自该提交的 `git show`，是静态证据，不代表真实后端、数据库或生产回归。

## 架构与迁移顺序

新前端使用 React + TypeScript + Vite。后续后端继续使用 Python + FastAPI、PostgreSQL、R2 和独立 Worker，采用模块化单体。模块先在代码边界内分离，再按切片迁移；不实施 Go/Gin、不引入 Redis、微服务或新队列。

| 顺序 | 模块 | 当前切片 | 后续兼容重点与来源 |
| --- | --- | --- | --- |
| 1 | 工作台壳、认证、剧集列表 | 实现壳/登录/只读列表 | `routes/auth.py:225-290`、`routes/series.py:103-174` |
| 2 | 章节与分镜 | 只读章节列表、分镜文字及可信原图的本地实现与验收完成；Grillme 离线待审 | `routes/series.py:280-342,1647-1660`、`schemas/series.py:84-96,294-307`；锁快照和唯一同章 storyboard 身份 |
| 3 | 角色、场景、道具及素材 | 剧集内三类只读素材实现与本地/隔离验收完成；Grillme 离线待审 | 三 GET `routes/series.py:1278-1286,1382-1390,1467-1475`；继承 aliases/canonical_key，不进行归一/合并 |
| 4 | 个人制作辅助、粗剪 | 当前章节个人制作记录与粗剪草稿只读面板已实现；记录保存、播放和导出仍延期 | `routes/personal_production_notes.py:71-121`、`routes/rough_cut.py:79-200,221-230`、`models/rough_cut.py:19-42` |
| 5 | 准入、任务、账务 | 我的任务只读列表本地/隔离验收完成；复杂准入、账务与队列仍延期 | `services/task_lifecycle.py:153-161,202-236`；旧规格仅作背景目标 |
| 6 | 团队剧集筛选 | 当前用户团队目录与只读二级筛选已实现并完成本地/隔离验收；成员管理与团队权限仍延期 | `routes/teams.py:95-117`；六字段冻结来源 hash 见 change design |
| 7 | 素材关联章节导航 | 从已验证的关联章节按原始身份打开并重新核验目标；复用既有只读 GET，不扩展路由 | `routes/series.py:280-342,1647-1660`；身份和权限边界见本切片 change design |
| 持续保留 | R2、独立 Worker | 不启动、不复制运行配置 | `services/object_storage.py:648-678`、`backend/run_worker.py:1-22` |

表中没有 `backend/` 前缀的 Python 路径均相对旧库 `backend/app/`。旧 `backend/main.py:114-126` 的 startup 校验数据库并安排任务恢复，因此首切片验证不能通过直接启动旧应用获得所谓“只读测试”。

## 首切片 API

以下三条接口及卡片无导航是首切片的历史交付边界。第二切片通过独立 change 扩展当前应用，不回写首切片历史验收；新增契约见下文。

| 接口 | 请求与响应 | UI 与错误行为 | 精确来源 |
| --- | --- | --- | --- |
| `POST /api/auth/login` | JSON `{username,password}`；返回 `access_token`、`token_type=bearer`、`user` | 失败保持登录页；不自动重试 POST | `backend/app/schemas/auth.py:29-62`；`backend/app/routes/auth.py:225-284`；`js/auth.js:112-127` |
| `GET /api/auth/me` | Bearer；返回 `UserResponse` | 已有 token 先核实当前用户；401 清当前模式会话 | `backend/app/routes/auth.py:83-136,287-290`；`js/auth.js:133-147` |
| `GET /api/series` | Bearer；返回 `SeriesResponse[]`，没有分页参数或包装对象 | 四筛选、保留 API 顺序、每次展示 20 条 | `backend/app/routes/series.py:103-174`；`backend/app/schemas/series.py:28-49` |

HTTP fixture 只模拟这些契约并经过真实 fetch，不启动旧 FastAPI 或 PostgreSQL。真实登录只允许隔离后端，不用于生产探测。

### 会话副作用

- JWT 通过 `sub/jti/password_version/exp` 核查；失效、撤销及密码版本不符返回 401，见 `backend/app/routes/auth.py:72-135`。
- 带 `jti` 的认证 GET 会建立缺失历史会话、刷新 `last_seen_at` 并 `commit`，见 `backend/app/routes/auth.py:112-135`。
- 登录最多保留三个活跃会话，第四次会撤销最旧会话，见 `backend/app/routes/auth.py:237-271`；历史测试源码见 `backend/tests/test_auth_sessions.py:109-129`，本切片不执行旧测试。
- 列表依赖有效会员；超级管理员绕过会员校验，普通用户非 premium 或过期返回 403，见 `backend/app/routes/auth.py:151-172`。这不代表超级管理员自动获得所有列表项或他人认领剧集的访问权。

Mock 使用独立 `haoai_next_mock_*` 会话键，API token 兼容 `muse_auth_token`。工作台只注销自己的当前模式会话，不沿用旧 `logout()` 对所有 `muse_*` 键的清除。

### 剧集字段

`SeriesResponse` 保留以下字段：

- 身份与展示：`id`、`user_id`、`name`、`description`、`image_url`、`created_at`、`updated_at`。
- 风格：`style_prompt_id`、`style_prompt`、`style_prompt_name`、`style_prompt_owner_name`。
- 分享与作者：`team_id`、`team_name`、`owner_name`。
- 制作负责人及进入限制：`claimed_by`、`claimed_by_username`、`claimed_by_avatar_url`、`can_enter`。

`id/user_id/name` 为字符串；可选信息允许 null，`can_enter` 在旧 schema 默认 true。前端不能生成替代 id、修改所有权或从 user.is_superuser 自行推断 `can_enter`。风格字段保留在接口类型中，列表不展示提示词或原始 JSON。

### 列表、筛选与认领

| 行为 | 冻结来源的事实 | 首切片决定 |
| --- | --- | --- |
| 可见集合 | 本人持有，或共享到本人所属团队；`routes/series.py:113-124` | 使用服务端返回全量，不另造权限过滤 |
| 排序 | 后端 `updated_at.desc()`；`routes/series.py:124` | 保留 API 顺序，前端不增加同时间破同序规则 |
| 我创建的 | `user_id === me`，含本人分享到团队的剧集；`js/app.js:4623-4625` | 同样保留，不能用 `team_id == null` 代替 |
| 团队剧集 | `team_id` 非空；`js/app.js:4627-4631` | 所有列表内团队剧集 |
| 我认领的 | `claimed_by === me`；`js/app.js:4626` | 同样保留 |
| 加载更多 | 初始 20 条、每次加 20；`js/app.js:4143-4146,4515-4516` | 四筛选后 slice，切换筛选重置 20 条 |
| 他人认领 | 未认领/本人认领可进；其他看团队 owner 或 `enter_claimed_series`；`routes/series.py:148-153` | 展示后端 `can_enter`，本切片所有卡片均无进入入口 |
| 团队二级筛选 | `/api/teams/my` 包含暂无剧集的团队；`js/app.js:4285-4297,4650` | 明确延期，本切片不请求该接口 |

个人持有字段是 `Series.user_id`，分享字段是 `team_id`，认领字段是 `claimed_by`，见 `backend/app/models/series.py:37-52`。访问校验先检查团队认领锁，再检查个人持有或团队成员，作者也可能受他人认领限制，见 `backend/app/services/team_service.py:166-185`。

### 错误与媒体

旧 `js/api.js:123-129` 捕获列表失败返回 null，`js/storage.js:31-44` 随后读取 `muse_series_list`。本切片有意改变此行为：错误明确显示，只有合法 `[]` 是空列表；不加载旧缓存，不跨用户泄漏列表。

仅 401 清会话；会员 403 显示限制并保留登录，其他 403 显示权限错误；422、5xx、超时、网络及无效 JSON 允许显式 GET 重试。错误兼容 FastAPI `detail` 字符串和验证数组，见 `js/api.js:48-99`。首切片不实现购买、兑换、注册、会话管理、改密或密码重置。

无时区服务器时间按 UTC 解释，见 `js/utils.js:70-76`。Mock 不引用远端封面、头像和字体；HTTP fixture 提供本地资源或空值，破图退回本地占位。

## 第二切片当前迁移契约（本地实现与验收完成）

`migrate-readonly-chapters-storyboards` 的只读功能已于 2026-10-04 完成本地实现与验收，Grillme 离线补审仍未完成。以下是当前实现保留的迁移契约；规划、任务及分层证据位于 `openspec/changes/migrate-readonly-chapters-storyboards/`。冻结来源的全文 hash 见该 change 的 design，现有完整 `legacy-reference/backend/app/schemas/series.py` 已含新响应模型，来源复制仍限定原五项。

### 新增两个只读 GET

| 接口 | 冻结响应及查询行为 | 当前迁移约束 | 精确来源 |
| --- | --- | --- | --- |
| `GET /api/series/{series_id}/chapters` | 全量 `ChapterResponse[]`；按 `order.desc(), created_at.desc()` 返回，含 content 和有效编辑锁快照 | 保留 API 顺序，不新增排序；成功后按首项稳定 id 默认选章；合法空数组独立空态 | `backend/app/routes/series.py:280-342`；`backend/app/schemas/series.py:84-96` |
| `GET /api/series/{series_id}/storyboard-assets?chapter_id={chapter_id}` | `StoryboardAssetResponse[]`；按剧集和指定章节查询，按 frame_index 返回 | 只查询当前选中章；空 content 不查询；frame_index 不用于镜头配图 | `backend/app/routes/series.py:1647-1660`；`backend/app/schemas/series.py:294-307`；旧请求编码见 `js/api.js:374-377` |

当前业务白名单扩展为首切片三条接口加上述两条 GET。动态剧集 id 编码为单独 path segment，章节 id 编码为 query；空身份及单独 `.`/`..` 路径段在请求前拒绝，避免 URL 规范化穿越白名单。新增界面不开放通用请求入口，不调用详情、素材目录、lock GET/POST/DELETE、个人记录、粗剪、生成、账务或队列。默认 mock 无业务或媒体网络请求；API 模式仍限已配置的隔离 loopback fixture，不连接旧服务。

### 章节内容与锁快照

`ChapterResponse` 保留 `id/series_id/title/content/order/created_at/updated_at/lock`。`content` 是可为 null 的对象数组，schema 不保证每帧存在 text，不能把 `SceneFrame` 创建输入模型当成完整响应。外层结构、整数 order、日期、章节归属和唯一 id 须验证；不合法响应不变成 []。合法空标题可用占位显示，身份不能补造。null 或空 content 显示无分镜；旧 GET 已将数据库损坏 JSON 转成 []，前端不能据此声称区分了真实空内容与数据库损坏，见 `routes/series.py:323-328`。

分镜按 content 原顺序展示 `text` 和已有合法 `original_text`，缺失文字用占位，非法已知字段局部提示；HTML 作为文字，不展开原始 JSON、隐藏提示词或模型参数。`character/scene/prop` 的合法 ID 数组可以显示引用数量，不堆积裸 ID 或编造素材名称。冻结前端来源为 `js/episode.js:421-439,548-556,814,2250-2252,2297`。

章节 GET 的 lock 可为 null；当前返回快照字段为 `locked/locked_by_username/locked_by_avatar/is_mine/expires_at`，见 `routes/series.py:295-319`。界面只显示加载时的编辑状态，不请求头像、不轮询，不从 is_mine 推导可编辑权。被服务端授权的用户即使看到他人编辑锁也可只读查看。旧 `episode.js:330-345` 会先申请锁并加载七类模块；本切片不复用该流程。锁 POST 获取/续期/接管和 DELETE 释放都有写副作用，见 `routes/locks.py:31-92,120-137`；本切片不获取、续期或释放锁。

### 列表权限与稳定原图关系

“只读查看章节”入口只接受原列表 `can_enter=true`；false 时点击或键盘不能触发查询，不按超级管理员身份解锁。剧集名称、所有权和认领信息来自当前列表，不新增详情 GET 覆盖它们；旧详情构造 `routes/series.py:77-101,177-185` 省略 claimed_by/can_enter，会触发 schema 默认值。

两条新增 GET 都经 `verify_series_access` 复验剧集访问：先检查他人认领限制，再检查持有人或团队成员，作者也可能被拒绝，见 `team_service.py:124-131,166-185`。列表允许进入只是上次响应的提示，当前 GET 的 403 必须遵守；lock.is_mine 与用户超级管理员标记不构成前端旁路。

镜头身份仅取当前帧 `storyboard[0]` 的合法非空字符串，并要求该引用在当前章唯一、匹配资产在当前 series_id/chapter_id 中唯一。缺失、非字符串、重复引用、跨章/跨剧集或重复候选仅显示文字和关系不可用提示，不按 frame_index、数组位置或名称补配原图、不新建资产或替代 id。冻结只读投影来源为 `personal_production_media.py:91-161`，旧历史身份读取见 `js/episode.js:2980-2986`。镜头显示序号不是持久身份。

仅唯一可信资产的 image_url 可经同源 loopback 检查后加载；失败退回本地占位，远端图片、头像和 preview 都不请求或外链。`frame.preview` 在旧页可能是图片或视频（`js/episode.js:2311-2320`），本切片仅显示有无已有预览，不加载 URL、不播放、不下载、不生成或重提任务。

### 上下文清理与错误

- 返回列表保留四筛选及已展示条数；章节查看使用本地状态，不引入 URL 深链接。刷新回到列表，不恢复未经验证的章节快照。
- 切换剧集清章节及选中章，切换章节清可见旧原图；返回/退出卸载章节上下文。成功资产只在当前会话和当前剧集的内存中按 chapter_id 缓存，A→B→A 可复用成功快照，失败结果不缓存；返回列表、换用户、重新读取章节清缓存，显式资产重试清当前章快照。
- 请求取消及上下文世代同时覆盖成功、失败和 401；迟到结果不得填充界面/缓存、重新显示旧数据或注销新会话。重复选同一章不并发重取，超时覆盖响应头和 JSON body。
- 有效 401 清当前会话；403（含会员/访问拒绝）、404、422、5xx、超时、网络和无效响应保留会话并明确提示，可返回或显式 GET 重试。章节 GET 失败不是空章；资产 GET 失败保留当前文字，在原图区显示独立失败，不伪装为空资产或展示上一章图片。不读取旧 muse_* 章节/列表缓存。

这些 GET 只读业务内容，不代表真实认证完全无副作用：认证依赖的 session 更新仍见上文。本 change 的 verification 分别记录冻结来源静态核对、契约/UI 自动、隔离 HTTP 的真实 fetch，以及根代理执行的 demo/API 真实浏览器验收。根代理独立通过前端 typecheck、9 个测试文件的 111/111 用例和 44 模块构建；随后仅增加空章节列表及本人锁两个 UI 用例，再次通过 typecheck 与章节页定向 21/21，用例增加后未重跑全量测试或构建。最终 GPT-6.1 Sol / xhigh 只读源码与测试复审未发现未解决实证 P1/P2。

真实 FastAPI/PostgreSQL、生产权限、DB 排序与锁并发均未运行，也未推送或部署。Grillme 仍为固定 GPT-5.6 Sol / xhigh 离线待审；本地验收完成不代表该审查通过，当前 Sol 复审不替代 Grillme。

## 第三切片当前迁移契约（本地实现与验收完成）

`migrate-readonly-asset-library` 已于 2026-10-05 完成剧集内角色、场景、道具只读查看的本地实现与隔离验收；Grillme 离线补审单列待办，不代表全部审查完成。本段记录当前实现，不改前两切片历史。三个新增 GET 为 `/api/series/{series_id}/characters`、`/scenes`、`/props`，来源 `routes/series.py:1278-1286,1382-1390,1467-1475`；均依赖会员及同一剧集访问校验，查询 `.all()` 没有 order_by，只保留响应数组顺序。

角色/道具以 name、场景以 title 展示，完整响应继承 `AssetNamingResponse.aliases/canonical_key`（`schemas/series.py:101-119,149-166,186-197,217-228`）。aliases 只接受响应字符串数组或 null，JSON 仅由旧服务端解析；canonical_key 保留但不前端计算或用作去重/合并身份。name/title 合法空字符串使用占位；父字段可缺省为 null，其余没有默认值的 Optional 字段为 required nullable，必须实际存在且为字符串或 null，缺失/undefined 不归为合法 null，空字符串原值保留。解码验证外层、日期、当前 series 及单类型唯一 id；HTML 作为文字，角色音频和声音引用仅保留 DTO，不展示地址、加载、播放或外链。

入口受原 Series.can_enter 控制，使用同一选中剧集上下文，默认只取角色，三类型按需切换。成功快照仅限当前用户/会话、剧集和类型内存，合法 [] 独立空态，失败不缓存；返回保留原四筛选及展示数，离开卸载并清素材上下文。不同类型同 id 合法，卡片、图片失败及缓存按上下文隔离；重新读取只清当前类型快照和破图状态，取消旧请求，允许同 URL 恢复加载。默认 demo 无业务/媒体网络，隔离 API 图片限同源 loopback，本地破图和远端图片使用占位。

类型切换、重新读取、返回换剧集和退出后新登录均使旧请求失效；Abort 与当前请求检查覆盖成功、错误、401 和缓存写入。迟到结果不能显示或缓存旧素材、清新会话。当前有效 401 注销，会员/访问 403 分别提示；404、422、5xx、响应头/体超时、网络及无效响应保留会话并提供当前类型 GET 重试或返回。重复选择当前类型不重发；显式重新读取可取消待处理请求后重读。

当前业务白名单为八条，仅在既有五条上增加上述三个 GET，动态剧集 id 编码一次为单独路径段，空 id 及单独 `.`/`..` 在请求前拒绝。不调用 duplicates/ignored：旧重复检测 GET 经 `routes/series.py:1021-1023` 调 helper，失效记录在 `:970-995` 被删除并 commit；ignored GET 同样在 `:1153-1170` 删除并 commit，故 GET 方法不代表无写副作用。编辑、上传、生成、融合、锁、搜索、全局素材和章节引用名称解析仍延期。来源复制 exact-five 不扩展；真实认证 session 写副作用仍见上文。

验证分层记录在本 change：继承 schema 静态对照、严格解码及 UI 自动测试、隔离 HTTP 的真实 fetch，以及根代理的 demo/API 真实浏览器验收。根代理独立全量通过 11 个测试文件的 163/163 用例，并通过类型检查及 46 模块构建；源码未在构建后改变，随后仅拆分长 Workspace 测试并重跑全量。deferred 用例明确结束原 Promise 并 await act，再检验失效类型不写缓存、重新读取后的旧 401、返回后换剧集，以及退出后新用户的文字/token/注销次数。GPT-6.1 Sol / xhigh 独立只读源码与测试复审未发现未解决产品 P1/P2。

根代理实测 demo 三分类、返回保留 24 条、既有章节、退出、桌面/移动及键盘焦点；隔离 API 实测同 id 跨类型、别名、空数据、合成图片、破图同 URL 重读恢复、代表性错误与重试。API 55 条日志限八条业务接口、OPTIONS 和本地 PNG，临时 API 进程及页签已关闭。真实 FastAPI/PostgreSQL、生产权限、数据库排序及真实媒体链路均未运行；无提交、推送或部署。Grillme 固定 GPT-5.6 Sol / xhigh 仍离线待审，当前 Sol 复审不替代它。

## 后续素材、团队角色与锁

- 角色、场景、道具继承 `AssetNamingMixin`，保留 `aliases` 和 `canonical_key`；规范名与别名由 ORM 事件维护，见 `backend/app/models/series.py:23-34,347-364`。后续迁移不能只保存展示名而丢掉复用/别名语义。
- `AssetDuplicateExclusion` 保存同一剧集、素材类型及有序素材对的去重排除；唯一键包含 `series_id/asset_type/asset_id_a/asset_id_b`，见 `backend/app/models/series.py:312-341`。排除信息属于剧集素材上下文，不是跨用户全局资产所有权。
- 团队角色保留 owner/admin/member 和 permissions；队长权限恒为真，管理员依明确权限点，默认管理员不含进入他人认领权限，见 `backend/app/models/team.py:23-32`、`backend/app/services/team_service.py:20-31,88-131`。
- 章节编辑锁按 `chapter_id` 唯一，保存持有用户、活跃时间与过期时间，见 `backend/app/models/team.py:56-66`；获取/读取/释放接口见 `backend/app/routes/locks.py:31-133`，写操作只为持有者续期见 `backend/app/services/team_service.py:156-163`。首切片不取锁或续锁。

## 后续个人制作与粗剪契约

- 个人制作记录唯一键为 `(chapter_id,user_id)`，保存 `revision/frame_notes/resume_frame_id`，见 `backend/app/models/personal_production_notes.py:29-58`。读取、插入和更新都以当前用户为准，不接受客户端选择其他用户，见 `backend/app/routes/personal_production_notes.py:108-118,221-249`。
- 个人粗剪也按 `(chapter_id,user_id)` 隔离并带 revision，见 `backend/app/models/rough_cut.py:19-42`、`backend/app/routes/rough_cut.py:203-230,240-282`。
- 稳定镜头来自当前章节唯一有效的 `storyboard[0]`，不以展示索引替代身份；缺失、重复、跨章或歧义须拒绝操作，见 `backend/app/personal_production_media.py:91-147`。
- 保存制作记录同时检查 `expected_revision` 和逐镜头 `expected_media_revision`，冲突返回 409；原图和预览双空时不能认可，返回 422；续作目标必须唯一有效，见 `backend/app/routes/personal_production_notes.py:158-198`。
- 媒体变更增加单调 media revision 并撤销认可；A→B→A 不恢复旧认可，见 `backend/app/services/personal_production_state.py:302-370,585-648`。第四切片增加个人记录 GET 的只读展示与本地摘要核对；第七切片增加粗剪投影 GET。保存、认可、续作写入、播放及导出接口仍未迁移。

## 第四切片当前迁移契约（本地实现与隔离验收完成）

`migrate-readonly-personal-production-notes` 于 2026-10-05 完成个人制作记录只读查看与隔离验收。它仅新增 `GET /api/chapters/{chapter_id}/personal-production-notes`；API 使用当前 Bearer，不接受 `user_id` 参数。旧端按 `(chapter_id,user_id)` 隔离数据，见 `backend/app/models/personal_production_notes.py:29-58` 和 `routes/personal_production_notes.py:108-121`。来源全文 hash 核对记录在本 change 的 design；没有增加 `legacy-reference/` 文件。

这条 GET 不是数据库纯读取。没有个人记录行时，服务返回 revision 0、空便签与空续作位置，不创建个人记录行；已有当前用户记录会加锁。请求还会协调章节/资产/媒体状态并可能提交媒体维护，媒体变化可能推进同章版本、撤销同章其他用户认可或更新他们的记录，双空历史认可也可被维护性撤销，见 `backend/app/services/personal_production_state.py:133-196,222-248,273-392,462-474,585-653`。本切片仅在默认独立 demo 和隔离 loopback fixture 中验证，不连接真实 FastAPI、PostgreSQL、Worker、R2 或付费服务。

响应保留 `chapter_id/revision/media_state/frames/frame_notes/resume_frame_id`。每帧严格检查 `frame_index/storyboard_asset_id/media_revision/source_valid/asset_image_digest/preview_digest/invalid_reason`；旧 `frame_notes` 条目逐项宽松投影，已知字段类型错误、未知状态或无效对象成为“记录不可识别”，不误显示认可。未保存的条目是未标记，revision 0、empty、unreadable 和 orphan 分别显示；续作只显示可核对位置或无法对应提示，不改变页面位置。

制作记录默认不预读，必须由当前章节内的“查看我的制作记录”显式打开。镜头只按本章唯一 `storyboard[0]`、原图列表唯一同剧集/章节资产、服务端唯一 `storyboard_asset_id` 与相同 `frame_index` 关联；缺失、替换、重复或位置不一致会产生结构 mismatch，无法匹配的备注放入旧记录区，不按展示索引兜底。已认可还要求 `source_valid`、至少一个非空媒体摘要、正媒体版本与认可版本匹配且无重确认标记。

客户端本机对冻结旧规则规范化后的原图 `image_url` 和 `preview` 原始身份字符串使用 WebCrypto SHA-256；规范化保留未知 query 的次序及编码，只剔除冻结名单中的临时签名参数、去 fragment 并按旧行为处理 scheme/非法控制符。即使身份字符串是远端 URL，也只在本机做字符串摘要，不会因核对记录而新增媒体请求。个人记录面板不显示 URL、摘要或裸身份；章节原有原图显示仍遵循既有 loopback 检查。

章节重读、原图快照替换、切章、关闭、返回、退出、新登录与显式重读都会使当前 panel 或 HTTP/digest 世代失效。迟到成功/401或摘要不得覆盖新记录、填入私人缓存或注销新会话；当前有效 401 注销，403/404/422/5xx/超时/网络/无效响应保留会话并提供重试。Fixture 以第二个合成账号演练同章私有数据，只有这条 GET 把业务白名单由八条扩成九条，不请求写、锁、历史、粗剪或媒体接口。

本地验收由根代理分层执行：16 个测试文件、230/230 通过；最终新增一条403 UI重试测试后的类型检查通过。51 模块构建通过，gzip 前 CSS 30.47 kB、JS 299.19 kB。另有源合同 3/3、exact-five 来源 verifier 通过和 OpenSpec strict 通过；前三切片历史 18 份文件 hash 保持不变。根代理对 demo 与 API 进行了真实浏览器操作，隔离 fixture 端口及临时页签已关闭；没有运行真实 FastAPI/数据库维护副作用回归。最终 GPT-6.1 Sol / xhigh 只读复审无未解 P1/P2；Grillme 固定 GPT-5.6 Sol / xhigh 离线待审单独保留。

## 第五切片当前迁移契约（本地实现与隔离验收完成）

`migrate-readonly-my-tasks` 于 2026-10-05 完成本人任务记录分页只读页及隔离验收。只新增 `GET /api/chat/tasks/list?page=N&page_size=10`，业务接口白名单由九条变为十条。冻结路由按当前用户 `user_id` 取列表和总数，按 `created_at.desc()` 排序后分页；没有客户端用户、剧集或团队过滤参数，见 `backend/app/routes/chat.py:865-871,942-1007`。旧 helper 未显式写入任务，但认证可能插入或更新 session 并提交，见 `routes/auth.py:112-135,151-172`；本地验收不连接真实认证或数据库，不宣称后端 GET 完全没有数据库副作用。

任务页仅由用户显式打开后读取当前页，不预读、不轮询、不排序。请求页必须为正安全整数，页大小固定 10；响应的 `page/page_size` 必须精确回显，`tasks` 每页最多十条且 `id` 非空并在本页唯一。合法空页不当作结构错误，total 与实际条数不强行等同。总数变化导致当前页变为空时保留当前页状态，仅提供“回到第一页”；上一页和下一页对已知页数之外的页禁用，不自动发首页 GET。成功响应按服务端顺序替换当前页，不跨页合并或去重。

基础字段遵循冻结列表 helper 字典；必需 nullable 字符串必须以自有字段出现且值为字符串或 null，缺失与 null 不混同。`chapter_id/frame_count` 必需但宽松保留 unknown 历史 JSON 值，`prompt_id` 可缺省且亦保留 unknown；这些动态值不用于导航或任意 URL。`request_data` 最多截取 200 个 Python 字符，可能不是 JSON；前端不解析、不展示。冻结 helper 的 `frame_text` 始终为 null，DTO 保留但卡片不显示。`credit_cost` 与 `progress` 接受安全有符号整数，积分只称“记录积分”，无效进度不画百分比。

类型可读映射严格沿用旧页：`chat/image/image-single/batch-image/video/video-single/extract/storyboard/optimize-frame/batch-optimize/ai-review/fused`；状态映射为 queued、processing、cancelling、completed、failed、cancelled。未知/空状态显示未识别，不用 processing 或 failed 兜底；未知类型仅纯文本显示。failed 的结果只呈现纯文本记录说明，其他状态只说明结果记录有无，不把查询错误改写为任务失败、不推断实际扣费或退款。chapter_title 与 asset_name 都可以作为上下文名称显示；章节标题、镜头位置和素材名称不产生跳转、稳定镜头关联、详情请求或媒体加载。

冻结时间可以是无时区 ISO 字符串；前端严格验证后按 UTC 解释，再转为浏览器本地时间。带 Z 或明确偏移的记录按其偏移解释。`request_data` 仅按字段契约保留，不解析或显示；HTML/URL 在允许显示的进度或失败文本中保持纯文本。任务页不生成图片、音频、视频、链接、完整请求或详情查询。默认 demo 合成 12 条本地记录，无业务 fetch；隔离 fixture 用当前 Bearer 区分两个合成用户，各自分页记录，并不连接旧服务。

换页、pending 重新读取、离开后重开、退出及下一用户登录都使旧页请求失效；有效 401 才清当前会话，403/404/422/5xx、超时、网络和无效响应保留会话并提供显式重新读取。取消信号之外还检查挂载及请求世代；自动化 deferred 用例会真实结束旧 Promise 并 await act，确认迟到成功或 401 不覆盖当前页、不触发注销或清除新 token。

根代理于本 change 的最终验收通过 typecheck、19 个测试文件的 266/266 用例和 Vite 54 模块构建；另通过来源合同 3/3、exact-five 来源校验（五项、敏感模式命中 0）、fixture `node --check` 及本 change OpenSpec strict。demo 与 API 浏览器、合成 fixture 日志、测试层级及未运行范围详见本 change 的 `verification.md`。GPT-6.1 Sol / xhigh 最终只读复审未发现未闭环实证 P1/P2；Grillme 固定 GPT-5.6 Sol / xhigh 仍离线待审，不由本地验收或 Sol 复审替代。

## 第六切片当前迁移契约（本地实现与隔离验收完成）

`migrate-readonly-team-series-filter` 于 2026-10-05 完成当前用户团队目录与剧集本地筛选，只新增 `GET /api/teams/my`。业务接口白名单现为十一条：登录 `POST` 一条、`GET` 十条。冻结路由按认证用户查询所属团队并返回六字段数组；不接收 user、team 或分页查询参数，不显式排序，见 `backend/app/routes/teams.py:95-117`。六份冻结源文件的全文 SHA-256 记录在本 change 的 `design.md`；没有扩展 `legacy-reference/`。

团队 DTO 严格要求每个元素自有 `id/name/owner_id/created_at/member_count/my_role` 字段。身份和所有者为非空字符串；名称与角色为字符串且允许为空；创建时间须是合法 ISO 日期时间，无时区时按 UTC 解释；成员数须为非负安全整数。顶层必须是数组，重复 ID 或坏结构拒绝整份响应。ID 不要求 UUID，保持原值并按 ID 过滤，因此 `all`、`__proto__`、同名选项及无剧集团队都可单独呈现。页面不通过名称或对象原型索引目录。

目录仅供 `team_id` 本地过滤，不是剧集访问授权。全部团队用独立的 null 选择态，真实 ID `all` 不充当哨兵；过滤在 20 条增量展示前完成，保留服务端数组顺序。团队目录缺项不能删除 `/api/series` 已返回的剧集或共享到用户团队的本人剧集；`can_enter` 与后续章节/素材请求仍按各自响应处理，不由 `owner_id/my_role/member_count` 推断权限。未增加团队详情、成员查询、管理、邀请或剧集范围 API。

目录仅在剧集列表可见且进入“团队剧集”分类时按需请求。成功快照只保存在当前 `userId` 与 services 实例绑定的页面内存；隐藏后返回保留成功目录、选择和展示数，用户或 services scope 改变时首次呈现即隐藏旧目录、错误、选择和剧集卡片。pending 请求由 abort 与 generation 一起作废。离开分类或暂时隐藏后返回不会因已显示的真实错误自动重读；显式刷新可在 pending 时再次点击，响应按结算时的当前选项更新目录，已移除的选项原子清为 null 并恢复 20 条。仅当前有效的 401 清会话；其他错误保留已成功目录和剧集列表并提供显式重试。

默认 demo 保持一个合成账号和 24 条剧集，团队数据纯本地、无业务网络请求。loopback fixture 用两个合成账号分别返回目录，支持空目录、移除、HTTP 错误、无效响应和延迟；自动 HTTP 测试验证当前 Bearer、无 query、响应字段及允许路由边界。运行变量见仓库 `README.md`，分层验收证据见本 change 的 `verification.md`。真实 FastAPI/PostgreSQL、Worker、R2 与生产权限没有运行；GPT-6.1 Sol / xhigh 最终复审无未解决实证 P1/P2，Grillme 固定 GPT-5.6 Sol / xhigh 仍离线待审。

## 第七切片当前迁移契约（本地实现与隔离验收完成）

`migrate-readonly-personal-rough-cut` 仅新增 `GET /api/chapters/{chapter_id}/rough-cut`。路由使用当前 Bearer 和章节访问复验，不接收 `user_id` 或其他查询参数；个人草稿按当前用户与章节隔离，来源见 `backend/app/routes/rough_cut.py:203-230` 和 `backend/app/models/rough_cut.py:19-42`。六份新来源文件全文 SHA-256 记录在本 change 的 design；没有扩展 `legacy-reference/`，业务接口白名单现为十二条（十一条 GET 与登录 POST）。

响应严格检查 `chapter_id/revision/saved/frames/removed_asset_ids` 与每帧 `asset_id/frame_index/text/preview_url/missing_reason/included/pending`。revision 0 对应未保存初始投影，已保存草稿使用正版本；位置必须是唯一安全整数。非空稳定 ID 必须唯一，多个空 ID 与重复失效旧 ID 合法保留，不套用 PUT 的 500 项约束。冻结 helper 仅取当前章节 `storyboard[0]` 的唯一有效资产身份；正文和视频引用由当前章节源数据投影，重复、缺失或跨章引用按无身份处理。已保存行保持服务端顺序，新的有效源行追加为待安排；视频引用按旧 helper 的固定扩展名规则过滤，见 `backend/app/routes/rough_cut.py:79-200`。`included` 只表示粗剪列表纳入状态，不代表可播放、已生成或获得认可；`pending` 只标记新增的有效稳定身份。草稿保存只记录资产 ID 与纳入状态，不冻结媒体版本。粗剪 GET 本身读取草稿并构造响应，不协调媒体版本或维护认可状态；通用认证链路仍可能维护会话记录，因此不把整次 HTTP 请求描述成数据库纯读。

用户须在选中章节后显式打开粗剪面板才请求该 GET，刷新也由用户操作触发。面板按 wire 顺序展示草稿条目及其源章节位置、纳入/待安排、可用性原因和失效旧引用数量；原始稳定 ID 与 `preview_url` 不落入 DOM，也不形成链接、媒体节点或新请求。粗剪与个人制作记录互斥，不等待章节原图加载；关闭、切章、章节/原图重读、返回或会话变化卸载当前面板，之后需要再次显式打开，不缓存私有响应。旧请求必须同时满足当前 scope 与请求世代；自动 deferred 测试实际结束原 Promise 并 await act，验证迟到成功/401不覆盖新面板或注销新会话。

默认 demo 仅根据已有章节与素材合成投影，不改动 24 剧集及章节样本、不发粗剪网络请求；合法空草稿由 DTO/UI 自动用例与隔离 API 验收。loopback fixture 用两个合成账号提供同章差异数据，并可控制未保存、无视频、空列表、移除引用、历史 ID、待安排、HTTP 错误、无效结构、超时与延迟。其环境变量见 README。根代理完成 25 个测试文件 375/375、typecheck、fixture `node --check` 与 59 模块构建；其中产品冻结时完成首轮构建，之后只增加一条资产 pending 成功测试，没有改产品代码。root 的 demo/API 浏览器、119 条合成请求日志及截图路径见本 change 的 `verification.md`。真实 FastAPI、PostgreSQL、Worker、队列、R2、媒体及付费链路未运行；GPT-6.1 Sol / xhigh 只读复审无未解决实证 P1/P2，Grillme 固定 GPT-5.6 Sol / xhigh 离线待审且不由本地验收或 Sol 复审替代。

## 第八切片当前迁移契约（本地实现与隔离验收完成）

`migrate-readonly-resume-navigation` 在个人制作记录只读面板中增加了本地续作镜头定位，不增加 API、媒体或写入接口。旧页面定位行为及冻结来源基线见 `openspec/changes/migrate-readonly-resume-navigation/design.md`；实现只消费第四切片已有投影提供的 `resumePosition`，且必须是当前章节唯一稳定身份和媒体快照均已核对的目标。定位入口名为“定位到续作镜头”。状态不需要为 `approved`：`needs_reconfirmation` 在身份与媒体仍可核对时也可定位。无续作位置、pending/checking/error、无效或歧义身份、mismatch/unreadable 快照、未成功读取资产时不开放入口，不用历史序号、展示索引或首镜头兜底。

打开面板、读取完成及重读都不自动移动页面或焦点。用户明确点击后，ChapterBrowser 在当前章节已渲染的镜头中核对位置和当前上下文，再同步聚焦目标、滚动到可见区域并显示镜头位置反馈；记录面板保持打开，重复点击只重复本地定位，不增加业务请求。目标引用和回调绑定读取时的 user、services、series、chapter 对象、资产快照、媒体可用状态、open epoch 及请求 generation；关闭、重读、切章、章节或原图重读、切换个人面板、返回列表、退出和 scope 变化都会使旧 ready、目标与反馈失效。scope 变化的首帧即隐藏旧入口，当前面板实例不会因 A→B→A 恢复；重新显式打开才产生新读取。父级也会在消费 target 时再次检查其 `isCurrent()`。没有新增自动 GET、定位写入、媒体 URL/元素或 URL 深链接。

分层证据记录在本 change 的 `verification.md`。root 最终执行 typecheck、25 个测试文件共 393/393 用例及 59 模块 build，均 exit 0；来源合同 3/3、exact-five 校验通过且敏感模式命中 0。root 的 demo/API 浏览器实测覆盖桌面、390px 和键盘定位、两合成账号及点击前后业务请求计数，API 审计共 53 条记录（22 条业务、25 条 OPTIONS、6 条既有 loopback PNG），实际访问 5 种业务路由模式；白名单仍支持 12 条，不代表浏览器访问了全部 12 条。GPT-6.1 Sol / xhigh 独立只读复审未发现未解决实证 P1/P2。

以上本地测试、来源核对和浏览器证据不代表真实 FastAPI/PostgreSQL、Worker、R2、外部媒体或生产已验收。Grillme 固定 GPT-5.6 Sol / xhigh 仍离线待审，不由 Sol 复审替代；没有提交、推送或部署。

## 第九切片当前迁移契约（关联素材只读）

`migrate-readonly-frame-asset-references` 在当前章节的每个分镜内提供“查看关联素材”入口。显式打开面板时不预选分类、不读取目录；用户选择角色、场景或道具后，只有该分类存在有效引用才读取对应的既有剧集列表。缺字段、null、空列表和没有合法 ID 的分类不发 GET；引用外层结构不可识别时提示不可识别且不发 GET。mixed 数组中的有效引用仍按分类读取，非法项单独提示。成功目录仅在本次打开内按类型缓存，重读由用户显式触发。

引用按当前分类、原始 ID 与当前 series 精确匹配，保留分镜引用原序和重复项。不按名称、alias、canonical key 或首项兜底；同 ID 跨分类互不混用。详情只显示名称/标题、别名、描述及人物文字特征，HTML 作为文字；不显示 ID、规范字段、音频地址或原始 JSON，不增加图片、音频、视频节点或主动媒体请求，不调用重复/排除查询、新 API 或写操作。章节已有 lazy 图片仍可能随滚动自然加载，这不属于关联素材面板增加的媒体读取。

面板与请求绑定当前用户、services、series、chapter、frame、位置和打开代次；镜头切换、切章、显式章节/原图重读、互斥面板、返回及会话变化会使旧面板、回调和请求失效。失配首帧立即隐藏旧内容且不自动恢复；需要用户再次显式打开。原图普通加载完成不关闭该面板。它与个人制作记录、粗剪和续作反馈互斥。

demo 仅将既有合成引用映射到同 series、同类型素材，不改变引用数量、顺序、重复数或其余章节/媒体/记录样本。fixture 的默认 prop 引用仍为空；浏览器只证明它不触发 GET，道具匹配成功由 demo/component/adapter 自动化覆盖。验收分层与限制见本 change 的 `verification.md`。

## 第十切片当前迁移契约（素材关联镜头只读）

migrate-readonly-asset-frame-usage 在素材库卡片上提供“查看关联镜头：素材名”入口。只有当前目录内同剧集、非空且唯一的原始素材 ID 可打开。用户显式打开任一合格素材后，复用现有 listChapters(seriesId, signal) 读取当前剧集章节；即使最终没有关联镜头，也先完成这次章节读取。进入素材库、显示卡片或切换角色/场景/道具目录都不预读章节。没有新增 DTO、服务方法、路由或查询参数。面板一次只关联一个素材，重复当前入口不重发请求；显式重读和关闭后重开才读取新章节快照。

投影按当前素材类型与原始 ID 严格匹配，保留章节响应顺序和各章节 content 顺序。显示章节标题、章节列表位置、镜头位置、镜头文字、原文及同镜头重复引用次数；位置来自当前数组快照，不按 order 字段排序。同一镜头重复引用显示为一行并标出次数，不用名称、别名、canonical key 或其他类型兜底。缺失、null 和空引用不计命中；mixed 数组中的合法项保留匹配，非法项单独提示不确定。章节身份或 series 归属校验失败时拒绝成功快照。零匹配文案限定“当前章节快照未找到关联镜头”，不推断素材从未使用。面板只显示文字，不显示结构 ID、JSON 或媒体 URL，不新增图片/音频/视频/链接节点、主动媒体请求或跳转；现有 lazy 图片可能随页面滚动自然加载。

素材目录缓存仅绑定当前 user、services、series。反查面板 owner 另外绑定素材类型、素材对象、ready 目录快照及 open epoch。换素材、实际切分类、目录重读、离开、关闭和会话变化使旧 owner/回调/请求失效；新上下文首帧隐藏旧内容，A→B→A 不恢复。只有当前有效 401 注销；旧 success/401 不覆盖面板、不污染新用户或清除新 token。非 401 错误保留会话并提供显式重试。

第十批没有修改 demo 或 fixture 样本及其引用，也没有调整既有章节、素材、分镜、图片、个人记录或粗剪数据；保护文件与历史 hash 核验见 verification。fixture 沿用原十二条业务接口与现有章节 GET，不增加素材关联接口。第十批的分层证据及限制见 openspec/changes/migrate-readonly-asset-frame-usage/verification.md。

## 第十一切片当前迁移契约（素材关联章节导航）

`migrate-readonly-asset-usage-chapter-navigation` 从第十切片素材关联面板的章节分组提供“查看对应章节：<标题>”。来源按钮使用生成当前 ready 结果的同一份章节快照，只携带其中的非空原始章节 ID。工作区接受后复制绑定 `userId`、`services`、`seriesId`、`chapterId` 和 `navigationEpoch` 的一次性意图；来源面板卸载不取消已接受的意图，旧来源回调不能重写新意图。

目标章节页复用 `GET /api/series/{series_id}/chapters` 重新读取完整当前目录，先检查目标原始 ID 在整个新目录中是否仅出现一次，再核验该唯一项属于当前剧集。它不按标题、展示位置、响应索引或旧快照兜底，也不先选首章；缺失、重复或跨剧集目标保持无选中状态并允许显式重读。目标唯一命中后才消费意图；用户主动选择其他有效章节会放弃尚未确认的目标，后续普通重读不重放已消费意图。目标章节有分镜时只复用现有 `GET /api/series/{series_id}/storyboard-assets?chapter_id={chapter_id}`；空内容不发素材请求。当前有效 401 遵循认证流程，已失效响应及旧回调不能修改新用户或剧集状态。

返回操作使用现有剧集列表并保留展示数量和筛选，不恢复素材面板。此切片不新增 API、DTO、服务方法、fixture 路由、查询参数、素材样本、媒体元素或写入。业务白名单仍为 12 个模式；浏览器本次实际观察了其中 8 个，不能据此声称全部模式均已验收。实现和分层证据见 `openspec/changes/migrate-readonly-asset-usage-chapter-navigation/verification.md`。

## 第十二切片当前迁移契约（素材关联分镜定位）

`migrate-readonly-asset-usage-frame-navigation` 在第十切片素材关联面板保留“查看对应章节”入口的同时，为每个身份有效的命中分镜增加“定位对应镜头：章节 · 镜头<n>”。来源身份取自同一份 ready 原始章节响应：原始章节 ID 先在完整响应中计数并确认唯一、属于当前剧集；frame 的 `storyboard[0]` 是该章内唯一的非空原始字符串；所选角色、场景或道具字段至少包含一次当前素材原始 ID。同一类别重复引用仍合法并只生成一个入口。frame 的显示位置仅供 UI 命名，不是身份。

接受回调前，工作区逐层核对当前用户、services、素材视图、剧集、来源 owner/ready 目录及读取世代；接受时复制 user/services/series/chapter/navigation epoch，以及分开的 `storyboardAssetId` 与类别 `assetId`。已接受意图不再依赖来源面板，来源卸载和旧关闭回调不能取消它。工作区与面板 owner 还守卫同 Dashboard 内的 view A→B→A，旧回调即使把 `isCurrent()` 报为 true 也不能改写当前意图。

目标章节页先读取完整新目录，统计 raw chapter ID 后核验剧集归属，再在目标章内确认 storyboard ID 唯一并且类别引用仍包含原素材 ID；此阶段失败不选章、不请求分镜素材。通过后复用既有章节素材 GET 一次，并要求目标 `storyboardAssetId` 在完整素材响应中唯一且 `id`、`series_id`、`chapter_id` 精确匹配。类别 `assetId` 只核验 frame 引用，不与素材响应 ID 混用。不会按标题、顺序、`order`、`frame_index`、URL 或相邻项回退。后置素材错误保留已核验章节和未消费意图，显式重核从新章节目录重新开始。

定位要求目标卡片仍连接、可见且属于当前快照；调用 `focus` 后必须确认实际活动元素，随后再次核对上下文和节点，再滚动、显示反馈并消费意图。尚未完成时，手动选其他合法章节、退出目标页或 scope 改变会放弃意图；成功后普通章节重读不重播，失焦清除定位反馈。第十一切片 chapter-only 导航继续选中唯一章节后立即消费，不等待素材或聚焦 frame。流程只复用既有章节目录与分镜素材读取，不增 API/DTO/service/fixture、媒体接口、媒体元素、主动媒体读取、写入或播放；既有章节安全 loopback 原图路径保留。实现与证据分层见 `openspec/changes/migrate-readonly-asset-usage-frame-navigation/verification.md`。

## 第十三切片当前迁移契约（分镜引用素材导航）

migrate-readonly-frame-reference-asset-navigation 从已核对的分镜素材面板提供“查看素材：素材名”入口，导航到当前剧集的同类别素材卡片。来源端只接受当前章节快照中身份有效的引用；点击后工作台复核当前用户、services、剧集、类别和读取世代，再复制类别与原始素材 ID。来源面板卸载不撤销已接受意图；同一 Dashboard 内切视图 A→B→A 后重放的旧父回调，即使 isCurrent 返回 true，也不能覆盖新意图。

目标端只发起该类别现有目录读取。身份检查先在完整类别响应中统计原始素材 ID，再核验唯一记录的 series_id；名称、别名、顺序、数组位置或其他类别均不作为替代身份，同一原始 ID 跨类别独立处理。只有目标唯一卡片实际获得焦点、重新核对当前作用域后才消费意图。空、缺失、重复、跨剧集目标或非 401 错误不会导航到相似卡片；有效 401 沿用现有会话流程。失败后由用户显式重读，成功消费后普通浏览不重放；同类别 pending 重读保留当前目标，手动离开或切到其他类别会放弃它。

本切片复用已有角色、场景、道具列表读取和素材卡片展示，不新增 API、DTO、服务方法、fixture 路由、查询参数、样本或媒体接口，不改变此前章节素材投影或章节/分镜样本。浏览器中既有 loopback PNG 读取仍可能发生；本切片不代表媒体请求为零。实现与按来源、自动化、浏览器分层记录的证据见 openspec/changes/migrate-readonly-frame-reference-asset-navigation/verification.md。

## 第十四切片当前迁移契约（粗剪定位章节镜头）

`migrate-readonly-rough-cut-frame-navigation` 在本人粗剪只读面板中为可核对的条目提供显式“定位到对应镜头 N”。定位身份必须是当前 ready 粗剪快照实际包含的 row 的非空原始 `asset_id`，且该原始 ID 在完整粗剪快照中唯一；它还须在当前章节 `storyboard[0]` 和完整分镜素材响应中分别唯一。素材响应先按完整数组计数，再核对目标记录的 `series_id` / `chapter_id`。实际镜头位置从章节数组推导，草稿显示顺序和 `frame_index` 不作为身份，不以名称、文字、URL 或邻项回退。

资格绑定当前用户、服务实例、剧集、章节对象、面板打开世代、rough 读取世代与 ready 粗剪/素材快照。rough 重读、关闭重开、切章、显式章节或素材重读、离开章节、任务页和会话变化会使旧入口、回调与反馈失效。普通素材读取成功只更新当前定位资格，不关闭面板、不重新读取粗剪、不自动聚焦。目标需实际获得焦点并通过 owner、快照和 DOM 复核后才滚动并报告成功；失焦只清除对应粗剪反馈。

粗剪和章节继续使用既有只读读取链路；定位及重复定位不增加业务 GET。此切片不新增 API、DTO、service、fixture 路由、素材/媒体元素、播放或写入，也不改变 demo/fixture 样本、十二种业务路由、exact-five 来源复制边界及前十三批历史。原有安全图片展示仍可能按既有 lazy 路径读取。自动化、浏览器和本地边界见 `openspec/changes/migrate-readonly-rough-cut-frame-navigation/verification.md`。

## 第十五切片当前迁移契约（个人制作记录定位分镜）

migrate-readonly-production-note-frame-navigation 在当前个人制作记录列表中，为核对完成且可识别的真实记录行增加显式“定位到记录镜头 N”。入口只属于同一份 ready/readout 中 verified 且可识别的行，不要求状态为已认可；未标记、待修、待重新确认、已认可均可定位。没有已保存记录时不创建额外分镜行，旧孤立记录保持原有显示规则。读取中、摘要核对中、错误、空/无记录、不可读或未核对行不提供新入口。

目标身份从当前章节实际镜头 storyboard[0] 的原始 ID 得出；原始 ID 必须在当前章节引用和完整分镜素材响应中各自唯一，再核对当前章节与剧集归属。先对完整素材数组按原始 ID 计数，之后核验唯一素材项。父端独立检查当前 readout、实际行对象、用户、service、剧集/章节与资产快照、面板打开/读取世代；isCurrent() 声明不能代替父端身份核对，也不能复活已失效的 R1 读取。

定位由用户显式触发，不根据序号、frame_index、标题、正文、名称或相邻项推测身份。实际目标 DOM 连接、可见并属于当前镜头列表后，系统聚焦并再次核对；仅实际获得焦点、通过作用域检查并完成滚动才报告成功。记录面板保持打开，失败说明留在面板中，失焦（包括进入镜头内部按钮）清除该独立反馈。逐行/重复定位及拒绝旧回调不会附加读取；显式重读仍沿用原 notes GET 并作废旧定位，不自动重放。原续作定位行为保持不变。

本切片沿用个人记录 GET 和既有安全原图路径，不新增 API、DTO、service、fixture 路由、媒体元素、主动媒体读取或写入。既有 GET 的认证/媒体维护副作用不应表述为真实数据库纯读取。实际自动化、浏览器、API audit、守恒和未运行边界见 openspec/changes/migrate-readonly-production-note-frame-navigation/verification.md。

## 第十六切片当前迁移契约（个人制作记录本地状态筛选）

migrate-readonly-production-note-status-filter 在现有个人制作记录只读列表上提供“全部”与六种状态选项及完整快照计数。筛选只影响可见行，保留当前 readout 的顺序与行对象身份；空结果可恢复全部。警告、orphan 和续作信息维持独立显示。待重新确认及不可核对记录不会计为已认可。

筛选状态绑定当前 ready readout/read ticket。无记录、空记录、加载中、摘要核对中或错误时不新增筛选列表；筛选不增加 GET、capture、登记或失效调用。回调消费时仍检查最新 scope、readout 和世代，因此 R1/旧面板回调不能改写 R2 或重开后的选择。显式重读沿用既有 notes GET、恢复默认筛选并使旧筛选回调失效，不会自动重放定位。

鼠标或键盘切换筛选造成定位目标失焦时，仅清除定位成功反馈，不作废当前 readout，也不重新读取。此切片不新增 API、DTO、service、fixture 路由、持久化或媒体请求。既有个人记录 GET 可能进行媒体/认可状态维护，不能把它描述为数据库纯读取。

自动化、浏览器、HTTP audit 与本地边界见 [本 change verification](../../openspec/changes/migrate-readonly-production-note-status-filter/verification.md)。

## 第十七切片当前迁移契约

`migrate-readonly-rough-cut-status-filter` 在个人粗剪当前 ready 且非空的读取投影上提供“全部 / 已纳入 / 已排除 / 待安排”本地筛选。saved=false 的非空初始投影同样适用。纳入与排除按 `included` 独立匹配；待安排按 `pending` 独立计数，可与纳入或排除重叠。各计数来自完整 `frames`，不因筛选而改变。

可见行保持原顺序、原 row 对象、原完整草稿序号及读取时章节位置；筛选不通过空 ID、缺少视频或可定位状态删行。零命中只说明当前草稿投影无符合条件的条目，并提供恢复全部操作。筛选选择绑定当前 snapshot 与请求世代；显式重读、关闭重开或 scope 改变使旧选择失效，新 ready 读取默认全部。普通素材目录迟到完成不重置筛选，过期回调不能改写当前选择。

筛选及恢复全部仅改变本地展示，不触发 GET、读取登记/失效、自动定位、媒体读取、持久化或写入。原粗剪行定位继续核对完整 snapshot 和实际 row；鼠标或键盘筛选导致定位镜头失焦时只清除原成功反馈。此切片不新增 API、DTO、服务方法、fixture 路由、业务模式或样例。粗剪 GET 与通用认证仍可能具有既有会话维护副作用，不将整次请求称为数据库纯读取。分层验证见 [本切片 verification](../../openspec/changes/migrate-readonly-rough-cut-status-filter/verification.md)。

## 第十八切片当前迁移契约（素材库本地搜索）

migrate-readonly-asset-library-search 为素材库角色、场景和道具分类增加仅作用于已读取目录快照的本地搜索。角色/道具使用原 name，场景使用原 title；三类只额外匹配页面可见的非空 aliases。查询经过 trim 和小写化后进行字面子串匹配。描述、外观、角色说明、未命名占位文案、canonical 字段和任何原始 ID 都不参与搜索。

结果保留原目录顺序、完整索引及原始 item 引用。筛选不会删改目录或替换卡片 key；未命中卡片以 hidden/inert 隐藏，回到匹配时仍是原节点。身份唯一性和关联素材资格始终依据完整的当前目录，不会因搜索后只剩一个可见重复 ID 而放宽。空查询显示整个已读快照，零匹配只说明当前分类快照没有符合查询的项目。

搜索状态绑定当前 user/services/series、分类、ready 结果及请求世代。用户、services、series、分类、重读或新导航发生变化时，旧查询在首个提交即不可见，A→B→A 不恢复旧状态；新导航仍按原目标身份处理并重置查询。同分类重复选择不刷新也不清空。实际改变查询会关闭当前关联镜头面板并清除本地导航反馈，但不发目录/章节 GET、不重置图片失败状态、不改变目录缓存或父级身份。旧 input 和 clear 回调须同时通过当前读取、作用域和请求世代检查。

本切片不新增路由、DTO、服务方法、fixture 模式、媒体节点、主动媒体读取逻辑或持久化；搜索不新增目录/章节业务请求，也不改变素材读取顺序与服务器数据。既有图片仍按安全显示链路按需加载，实际 PNG 增量单独记录；显示过滤不构成授权。实现、自动化、浏览器和请求计数的分层证据见 [本切片 verification](../../openspec/changes/migrate-readonly-asset-library-search/verification.md)。

## 第十九切片当前迁移契约（个人制作记录显式编辑）

`migrate-personal-production-note-edit` 在当前章节个人记录面板中增加逐镜头编辑器。用户确认三种记录状态之一、输入备注并显式保存；备注限制为 2000 个 Unicode 码点。没有个人记录时，revision 0 可用于首次保存。认可只适用于当前章节与媒体摘要通过核对的记录；旧认可失效时不会自动恢复，待重新确认状态须由用户重新选择。

每次保存只提交选定镜头，使用当前 Bearer 身份，并以个人记录 revision 和媒体 revision 做并发比较。保存成功后使用返回的更高 revision 更新当前读取世代，不额外读取个人记录或请求媒体。已知冲突、权限拒绝和结构错误保留本地草稿，等待用户显式重新读取；已应用但响应无效或请求结果未知时，不自动重试 PUT，用户须先显式读取核实并重新确认状态。有效保存尝试由浏览器审计观察到一次 PUT；一次操作的 OPTIONS 与 PNG 增量另行计数，不推导页面整体媒体请求为零。

编辑器、demo 与隔离 fixture 均遵守当前用户/章节边界；demo 与 fixture 使用内存状态，不代表真实存储持久化。旧个人记录 GET/PUT 和通用认证链路仍可能进行既有媒体/认可维护，包括撤销同章其他用户的失效认可，不能将其称为真实数据库纯读取。本切片不改变既有来源投影，不新增媒体生成、任务或计费能力。分层证据见 [第十九切片 verification](../../openspec/changes/migrate-personal-production-note-edit/verification.md)。

```sh
openspec validate migrate-personal-production-note-edit --strict
```

## 第二十切片当前迁移契约（设置个人续作位置）

`migrate-personal-production-resume-edit` 在当前章节个人记录面板提供显式续作镜头选择、设置和清除。设置使用完整当前章节、原图目录与服务端快照中的唯一稳定原始 ID，并校验所属章节、剧集、位置和 `source_valid`。设置不要求便签存在、摘要一致或记录已认可；清除只要求当前本人同章 ready 快照，不要求旧目标仍有效。保存标记不授予认可或定位资格，既有 verified 定位规则保持。

设置和清除复用当前个人制作记录 PUT，正文严格为 `expected_revision` 与 `resume_frame_id` 两个字段；显式 `null` 表示清除。本切片不增加业务端点或 query。备注写入与续作写入共用个人 revision、同步门禁和读取世代。成功后采纳服务端完整快照，包括服务器维护的其他便签或认可状态；没有新增媒体 revision CAS。409、其他拒绝及结果未知保留意图并要求显式 GET 后重新决策，禁止自动重试或自动读取。当前有效 401 仍沿用现有会话处理，旧请求的迟到结果不得影响新 owner。

默认 demo 与隔离 fixture 只证明各自内存实例内的行为，不证明页面重载后持久化或真实数据库效果。旧 GET/PUT 与认证链路仍可能产生媒体元数据、认可及会话维护副作用，不能称作数据库纯读取。本切片不新增媒体读取或播放。自动化、浏览器、API 请求和未运行边界见[第二十切片 verification](../../openspec/changes/migrate-personal-production-resume-edit/verification.md)。

## 第二十一切片当前迁移契约

变更 migrate-personal-rough-cut-edit 为已有章节粗剪草稿增加显式编排保存。可选服务方法 savePersonalRoughCut 使用现有 PUT /api/chapters/{chapter_id}/rough-cut，不增加 HTTP 端点；请求只接受 expected_revision 和完整有序的 frames 列表，每项仅含 raw asset_id 与 included。服务端在当前章节和素材快照内核对完整 ID 集合、唯一性、纳入状态及 revision。空章节可以保存；待确认或已移除镜头须经完整当前快照核对后显式提交。

只有章节身份、revision 加一、已保存状态及 raw ID、顺序和纳入状态均与本次提交一致的响应才成为新的 canonical 粗剪快照。服务端可维护 frame_index、文本、预览 URL、缺失原因等非身份元数据；这些字段不参与媒体 CAS，也不代表媒体内容版本。有效响应会清除待确认和移除集合；无效或不确定响应保留本次提交意图为只读状态，不自动重试或读取。显式重新读取当前粗剪成功后，才以服务端快照恢复编辑。

粗剪编排只保存粗剪自身的 ID 顺序与纳入状态，不写个人制作记录、认可状态或媒体元数据，不新增媒体端点、媒体节点或主动媒体读取。演示与 fixture 的保存状态是进程内存，不提供真实后端持久化保证。本切片不实现真实 FastAPI/数据库模块化或生产联调。

## 第二十二批当前后端迁移契约

`modularize-backend-personal-rough-cut` 在 `backend/src/haoai_backend/personal_production/rough_cut` 建立一个隔离的个人粗剪后端模块。它是本迁移的首个真实 FastAPI/SQLAlchemy 业务模块，不是全站服务入口，也没有更改旧后端应用。

app factory 通过 `create_app(*, session_factory, resolve_actor, series_access_policy)` 显式接收三个必需端口。缺少任一端口返回 503 且不创建 Session，不提供默认 allow。可信 actor 由解析端口给出，剧集策略使用同一个 Session。新 adapter 只暴露 `GET` 与 `PUT /api/chapters/{chapter_id}/rough-cut`，关闭 OpenAPI、Swagger 和 ReDoc。

领域投影保留旧来源语义：以章节首个 `storyboard[0]` 原始 ID 和完整素材目录核对身份，不裁剪 ID，不按名称、索引或媒体 URL 回退。对当前完整来源先计数再判断唯一性，移除 ID 的重复顺序保留；既有视频 URL 扩展名匹配保持大小写不敏感。GET 允许超过 500 个来源条目。来源 JSON 或章节内容结构解析失败先于个人 revision 检查并返回 422；解析成功后先比较 revision，再校验稳定原始 ID 的唯一性与归属，最后校验完整来源数量。请求 body 超过 500 项按模型校验返回 422；身份有效的章节来源超过 500 项返回 413。空章节可首次保存空列表。

持久层只存本人、章节、revision 和 ordered `{asset_id, included}` 私人投影，不复制章节或素材快照。GET 不提交事务；PUT 使用章节行锁策略、个人 revision CAS 与私人唯一约束，并仅提交一次。只有应用数据库接线提供事务、Engine 和生产 schema；当前 app factory 不创建这些对象。测试及 loopback 使用最小 SQLite 表和临时文件，不是生产 schema，也不是生产迁移。PostgreSQL 锁只做方言编译检查，没有真实 PG 锁验收。

该 slice 未迁移旧站点入口、真实 JWT/密码版本/撤销、成员或团队授权实现、其他业务域、Worker、队列、媒体服务、生产持久层或部署。loopback 用显式合成身份与权限策略，不能作为真实授权证据。代码和分层验收见 [backend/README](../../backend/README.md)、[后端模块边界](backend-module-boundaries.md) 与 [本变更 verification](../../openspec/changes/modularize-backend-personal-rough-cut/verification.md)。

## 第二十三批当前后端迁移契约

modularize-backend-series-access 在 haoai_backend.series_access 中实现当前数据库剧集访问规则，并显式接入个人粗剪 GET/PUT。策略通过调用方传入的同一个 Session 读取剧集、成员和认领信息；它不创建或管理 Session，不写入，不增加 HTTP 方法。粗剪 app factory 要求显式提供 Session factory、可信 actor 解析器和同 Session 的访问策略，缺项返回 503 且不创建 Session。

授权顺序保留旧行为：先查剧集，不存在返回 404“剧集不存在”；对团队且由他人认领的剧集，先检查当前成员的 owner 角色或精确权限 enter_claimed_series，再检查作者或当前团队成员资格。认领人身份不绕过最终资格。权限列表保留原有已知键筛选和非法元素处理；认领拒绝使用当前认领人用户名，缺失时为空名；一般拒绝 detail 为“无权访问该剧集”。决策不跨请求缓存。

粗剪请求先查章节，该 SQL 包含 content 字段；访问策略在解析章节内容、查询源镜头素材和本人草稿之前执行。拒绝 GET 的测试证明后续素材与私人草稿查询为零；loopback PUT 用既有私人行不变且没有新草稿证明拒绝写入。不得把这些结果表述为零 chapter SELECT 或已取得 PUT 查询轨迹。

新包的 series、team_members 和 users 表仅是最小 SQLAlchemy Core 查询投影及临时测试 schema，不是生产 schema 或迁移。应用 factory 不创建 Engine/schema；SQLite 实验身份是合成可信身份。真实 JWT、会话撤销、会员/完整团队管理、PostgreSQL 运行与并发撤销、Worker、队列、媒体服务和生产部署均未由本批验收。策略细节见 [后端策略说明](series-access-policy.md)、[模块边界](backend-module-boundaries.md) 与 [本变更 verification](../../openspec/changes/modularize-backend-series-access/verification.md)。

## 第二十四批当前后端迁移契约

`haoai_backend.personal_production.notes` 为个人制作记录提供既有 GET/PUT；它与粗剪通过同一个显式工厂组合为四个业务方法。可信 actor 和 BusinessError 是共享类型，粗剪原 actor import 保持同一 class。工厂缺任一必需端口时返回 503 且不创建 Session，不提供默认授权。

notes UoW 在调用方的同一个 Session 中查询当前章节、源媒体与本人记录。授权先于媒体和私有记录读取/维护；章节 SELECT 已包含 `content`，不能把拒绝前的 SQL描述为零章节查询。GET 可在当前请求事务内提交媒体版本初始化、摘要变化、删除墓碑、同章认可撤销及本人双空历史认可维护，并返回新快照。只有当某次 PUT 自身发现双空历史认可仍需维护时，才先独立提交维护、随后返回 409 且不应用该次用户补丁，即使调用方猜中维护后的 revision；此前 GET 已完成维护后，携新 revision 的正常 PUT 不会因此无条件返回 409。

PUT 保留完整多帧服务端合同与现有前端局部补丁：严格字段、最多500个请求帧、GET 可读更大的当前来源、未知便签字段保留、note-only/status-only 语义、续作显式 null 清除、私人 revision CAS 和旧错误映射。成功 revision 来自本次操作；不做 post-commit 读取。媒体协调不拥有事务，未来来源写者需显式在同一事务调用；本批没有迁移旧 ORM hooks 或其他源写入方。

新表是当前实现所需的最小 SQL 投影，不是完整生产 schema 或 migration。尚未迁移/验收真实认证与会员、生产 PostgreSQL 锁或并发撤销、全站其他业务、Worker、队列、媒体服务及生产部署。详见 [后端说明](production-notes-backend.md)、[兼容边界](backend-module-boundaries.md) 和 [本变更 verification](../../openspec/changes/modularize-backend-production-notes/verification.md)。

## 第二十五批当前后端迁移契约

本批在隔离后端新增 authentication 模块，并把统一工厂从 4 个方法扩展到 15 个：11 个认证方法，以及既有粗剪与个人制作记录各两个 GET/PUT。认证路由为：

| 方法 | 路径 |
| --- | --- |
| POST | /api/auth/register |
| POST | /api/auth/login |
| GET | /api/auth/me |
| GET | /api/auth/sessions |
| DELETE | /api/auth/sessions/{session_id} |
| PUT | /api/auth/password |
| POST | /api/auth/forgot-password |
| POST | /api/auth/reset-password |
| GET | /api/auth/credits/me |
| POST | /api/auth/send-code |
| POST | /api/auth/verify-code |

既有业务方法与上述 11 个认证方法合计 15 个：

| 方法 | 路径 |
| --- | --- |
| GET | /api/chapters/{chapter_id}/rough-cut |
| PUT | /api/chapters/{chapter_id}/rough-cut |
| GET | /api/chapters/{chapter_id}/personal-production-notes |
| PUT | /api/chapters/{chapter_id}/personal-production-notes |

认证层延续旧规则：注册和重置密码要求至少 8 个 Unicode 字符并含字母与数字，修改密码为 6 字符下限；用户输入保持普通字符串，不统一 trim/lower。旧用户表没有 is_deleted 列。注册的非空邮箱码先按原邮箱消费，空码跳过；公开校验只标记已验证、不消费。忘记密码发送结果保持通用响应；重置只更新密码哈希，不改 password_updated_at、不撤销会话，也不把 token 改为一次性。修改密码则更新版本并撤销本人所有会话。

兼容 password_version 的旧语义：对 naive password_updated_at 直接调用 datetime.timestamp() 并取整秒，因此沿用主机时区解释；不会将 naive 时间重新解释为 UTC。

登录与请求鉴权使用真实 bcrypt/JWT 适配器和显式传入的算法、密钥、有效期。默认访问期 14 天，重置期 30 分钟；不读取 .env 或环境秘密。未知 jti 按旧顺序先持久化本人历史会话再更新 last_seen。认证 Session 完成本人身份和会话维护后，再核验当前会员资格；只有核验通过才创建独立业务 Session，该 Session 用于既有剧集访问 SQL 策略及素材/本人私有业务读取。认证失败时不创建业务 Session。超级用户通过会员门槛；普通用户须为有效 premium。显式 resolve_actor 仍优先于认证运行时接线。

只对注册 5/hour、登录 10/minute、忘记密码 3/hour 计固定窗口限额，按客户端地址与路径隔离，不以转发头作为客户端身份；认证 DTO 的 422 不消耗该限额。八个其他认证方法没有全局 60/minute 限额。三类限额超限均返回含 error 的 429 JSON，且不附 Retry-After；邮箱冷却 429 仍使用 detail，冷却秒数向零取整。验证码、邮件发送器和限流器为显式进程内端口，不代表共享多进程存储或真实 SMTP。

SQL 层使用最小 Core 投影，不创建生产 Engine/schema。认证与业务 Session 分开提交；会话维护可能在会员拒绝前完成，后续业务拒绝不会回滚这项既有维护。新适配器通过完整 HTTP、临时 SQLite 与真实 TypeScript parser 输入验证，但不代表生产密钥、SMTP、生产迁移、PostgreSQL 运行并发、前端整套测试或部署已完成。详细分层和证据见 [认证后端架构](authentication-backend.md) 与 [本变更 verification](../../openspec/changes/modularize-backend-authentication/verification.md)。

## 后续生成与任务的来源等级

本轮用户明确暂缓复杂计费和队列的大改，仅保留现有行为；结果未知不得自动重提付费任务、不得消费真实队列是本轮运行边界。该边界不授权实现旧规划中的全部恢复和结算目标。

**现有源码静态事实（本轮未运行）**：

- `EvidenceProof` 明确网络超时/供应商状态未知不是失败证明，见 `backend/app/services/task_lifecycle.py:153-161`。
- `prepare_step_intent` 对已有提交返回只读意图，允许查询/恢复且不授权第二次供应商 POST；先查现存提交再决定是否创建新意图，见同文件 `:202-236`。独立 Worker 入口继续委托现有可靠运行器，见 `backend/run_worker.py:1-22`。
- 现有源码包含固定截止与逾期处理、取消保留费用和成功已退款状态，见 `task_lifecycle.py:260-268,545-579,1046-1063`。这里只定位已有逻辑，未执行其端到端验证，也不批准扩展、重构或修改计费规则。

**旧规格背景目标（需后续对照，不是本轮已批准兼容规则）**：

- `openspec/changes/fix-personal-generation-admission/specs/single-generation-client/spec.md:31-76` 描述原幂等键、未知回执查询、停止观察和可信结果展示目标。
- `openspec/changes/harden-task-billing-and-redeployment/specs/recoverable-task-execution/spec.md:9-93` 描述 claim/lease、原提交恢复、逾期/迟到交付和取消目标。旧 change 的规划或历史测试记录不能提升为本切片的新批准契约。

后续生成/任务迁移应另建 OpenSpec change，逐项核对当前源码、已批准规则和可执行验收。首切片不新增调用链、免费旁路、结算协议、任务恢复中心或队列功能。

## 验收记录原则

来源静态核对、本地契约/UI 自动、真实浏览器、真实 FastAPI/PG、生产发布及 Grillme 审查分别记录。首切片计划验证前三层；真实 FastAPI/PG、后续模块及生产均未执行。Grillme 仅允许 GPT-5.6 Sol / xhigh，当前为“未完成，离线”，后续可独立补审，不冒充通过、不降档。
## 第二十六批当前后端迁移契约

统一工厂在既有十一项认证和四项个人制作/粗剪方法之外，增加下列十二个来源数据方法；不替换原路径或扩展请求字段：

| 方法 | 路径 | 成功状态 |
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

前述四项个人方法仍为 GET/PUT /api/chapters/{chapter_id}/personal-production-notes 与 GET/PUT /api/chapters/{chapter_id}/rough-cut；本工厂合计二十七项。新来源方法必须获得可信身份与同一请求的业务 Session；缺少配置时返回 503，不默认放行且不创建 Session。旧四项个人方法仍须显式传入其访问策略。

普通访问继续按原认领门槛、作者资格和当前团队成员关系判定；通过认领权限本身不授予最终访问。删除权限与普通访问分开，使用可信用户及原 creator/superuser/team-delete 规则。章节 SQL 可先读到 content；授权检查先于内容解析、源素材和私人状态查询。拒绝路径不能被描述为零章节读取。

Schema 和字段更新保留旧默认、空值、额外字段与错误顺序：创建帧会忽略 DTO 外字段，更新已有列表字典保留其余字段；缺失/null 字段不等同于空字符串或空列表。排序、聊天映射、重复/不可哈希引用及同值不更新时间的行为按固定来源保持。

媒体协调在每个真实来源阶段 DML 前使用当前数据库事实及该阶段 overlay。内容字节未变化且没有实际素材增删或元数据变化时，不强制初始化媒体状态；真实内容变化仍按旧阶段调用协调器。阶段 commit 独立，失败只回滚当前阶段；未知提交结果不自动重发，需显式读取核实。单帧删除可留存孤立 AITask，因为 message_id 没有外键；整章/整剧按原规则处理任务。账单外键拒绝删除时保留金融行，不新增金融清理策略。

模块分层、权限顺序、阶段边界与运行限制见[架构说明](series-data-backend.md)。本批实际验收与失败历史见[verification](../../openspec/changes/modularize-backend-series-data/verification.md)。



## 第二十七批当前后端迁移契约

统一工厂新增下列十五个角色、场景、道具和分镜素材方法。路径保持固定来源兼容；章节素材目录 GET 继续由既有 `series_data` 提供。

| 方法 | 路径 | 成功状态 |
| --- | --- | --- |
| GET | `/api/series/{series_id}/characters` | 200 |
| POST | `/api/series/{series_id}/characters` | 201 |
| PUT | `/api/characters/{character_id}` | 200 |
| DELETE | `/api/characters/{character_id}` | 204 |
| GET | `/api/series/{series_id}/scenes` | 200 |
| POST | `/api/series/{series_id}/scenes` | 201 |
| PUT | `/api/scenes/{scene_id}` | 200 |
| DELETE | `/api/scenes/{scene_id}` | 204 |
| GET | `/api/series/{series_id}/props` | 200 |
| POST | `/api/series/{series_id}/props` | 201 |
| PUT | `/api/props/{prop_id}` | 200 |
| DELETE | `/api/props/{prop_id}` | 204 |
| POST | `/api/storyboard-assets` | 201 |
| PUT | `/api/storyboard-assets/{asset_id}` | 200 |
| DELETE | `/api/storyboard-assets/{asset_id}` | 204 |

省略字段与显式 `null` 继续区分；更新不把缺字段改为空值，创建 DTO 按旧规则忽略额外字段。类别 DELETE 清理既有 `chapter.content` 中的引用时，保留非目标字典、未知字段、其它类别引用及非字典元素；本批不新增章节素材列表 PUT。更新按首次赋值意图确定来源与派生字段的写入集合；只有来源和派生字段均未变化的真正 no-op 才不发来源 DML 或更新时间。显式同值 name 仍可依旧规则修复过期 canonical/falsey aliases；别名、名称及旧错误优先级保持固定来源语义。

普通读取与写入使用可信身份、同一业务 Session 和当前数据库授权数据。分镜素材权限按素材所属 `series_id` 判定，媒体协调按实际 `chapter_id` 与章节来源进行，不新增跨表一致性过滤。类别 DELETE 清理目标类别引用，并只协调真正受影响的章节；实际 `image_url` 字段变化仍调用协调器，签名等价不推进已有媒体版本或撤销认可，缺失状态可初始化 R1，纯元数据变化不强制初始化。分镜素材 DELETE 保留章节原始引用并写墓碑，不自动关联章节或续期锁；显式协调仍可能为其它既有引用初始化缺失状态。每个写请求只提交一次，提交前任一阶段失败整体回滚；成功提交后刷新失败属于结果未知，需显式读取核实。详见[资产数据后端架构](asset-data-backend.md)。

工厂登记数为四十二；本批 TCP loopback 实际覆盖十五个新方法和四个既有个人制作/粗剪方法，不能表述为四十二个方法全都经过 TCP 调用。执行者与证据限制见[本批 verification](../../openspec/changes/modularize-backend-asset-data/verification.md)。


## 第二十八批：聊天数据 API 兼容契约

本批将旧聊天消息和 AI 统计方法接入 haoai_backend.chat_data。新方法及成功状态如下：

| 方法 | 路径 | 状态 |
| --- | --- | --- |
| GET | /api/chapters/{chapter_id}/chat-messages | 200 |
| POST | /api/chapters/{chapter_id}/chat-messages | 201 |
| PUT | /api/chapters/{chapter_id}/chat-messages/{message_id} | 200 |
| DELETE | /api/chapters/{chapter_id}/chat-messages | 204 |
| DELETE | /api/chapters/{chapter_id}/chat-messages/single/{message_id} | 204 |
| GET | /api/chapters/{chapter_id}/asset-chat-messages（必需查询参数 asset_type、asset_id） | 200 |
| POST | /api/chapters/{chapter_id}/asset-chat-messages | 201 |
| PUT | /api/chapters/{chapter_id}/asset-chat-messages/{message_id} | 200 |
| DELETE | /api/chapters/{chapter_id}/asset-chat-messages/single/{message_id} | 204 |
| GET | /api/chapters/{chapter_id}/ai-stats | 200 |

GET 普通聊天列表按章节、chat_mode 和可选 frame_index 查询，不额外排除素材关联记录；素材聊天列表再按 asset_type/asset_id 查询。请求 schema 保留旧默认值、可空字段和 extra-ignore 行为。单条普通消息删除使用 asset_type IS NULL，素材消息删除使用 asset_type IS NOT NULL，空字符串归入后者；批量删除按章节及可选帧索引执行。AI 统计只汇总旧规则中的 completed/failed 任务，按模型和状态分组；空模型名合并为“未知模型”，失败任务只计入失败数；积分仅累计 completed 任务，并保留可能为负的原始积分值。

身份与剧集访问复用既有可信 actor/series_access，同一请求使用一个业务 UoW。写入按用例提交；创建和内容更新提交后通过同一 UoW 重新读取实际消息行。提交后 readback 失败不能证明写入未发生，不自动重试，应显式读取核实。统一工厂登记数为五十二；本批 TCP 证据只覆盖十个新增方法及四个既有 smoke 方法，见[本批 verification](../../openspec/changes/modularize-backend-chat-data/verification.md)。

## 第二十九批：章节画布迁移契约

本批继续提供既有章节共享画布接口：

| 方法 | 路径 | 成功状态 |
| --- | --- | --- |
| GET | `/api/chapters/{chapter_id}/canvas` | 200 |
| PUT | `/api/chapters/{chapter_id}/canvas` | 200 |

章节不存在仍先返回原 404，再检查剧集访问；写入沿用本人锁续期、按 Python 字符数计算的 1,000,000 字符上限及已读版本快照冲突规则。缺失画布的 GET 返回完整字面默认对象且不写入。已有记录成功 PUT 会递增版本，即使正文相同；更新按主键执行，不增加数据库版本条件。成功提交后真实刷新元数据，响应正文回显本次请求。提交后的确认/刷新异常不自动重发，需显式 GET 核实。


## 第三十批：下载链接 API 兼容契约

| 方法 | 路径 | 关键边界 |
| --- | --- | --- |
| GET | /api/download | url 为必需普通字符串；filename 可省略，省略时为 null。活动会员且参数合法时返回固定 HTTP 403“下载代理已停用（服务器不承载下载流量）：请使用 POST /api/sign-download-urls 获取签名直链，由浏览器直连 OSS 下载”。 |
| POST | /api/sign-download-urls | items 为必需数组；每项 url 为普通字符串，filename 默认空字符串。默认忽略额外字段，不 trim、不验证 URL 格式；合法项目超过 500 项时返回固定 HTTP 400“单次最多 500 条”，无效 DTO 仍由标准 422 验证。 |

批次按输入顺序逐项 await resolver，一项恰好调用一次；重复项保留。结果保留每项原 filename，并以 resolver 返回 URL 与输入 URL 是否不同计算 signed。resolver 异常产生通用 HTTP 500，不重试、不返回部分结果。未接线的 actor resolver 失败关闭为 503；两路均先执行活动会员身份校验。默认 resolver 是逐字 identity，不代表真实签名或下载。其他实现及证据见[下载链接后端说明](download-links-backend.md)。


## 第三十一批：章节素材替换 API 兼容契约

| 方法 | 路径 | 兼容要点 |
| --- | --- | --- |
| POST | `/api/chapters/{chapter_id}/replace-asset` | 必填普通字符串 `old_asset_id`、`new_asset_id`、`asset_type`；类别仅为 `character`、`scene`、`prop`。 |

认证及活动会员校验后，保留旧新 ID 相同、类别、初始章节查找、普通剧集访问、新素材类别/剧集归属、内容格式和旧引用检查的优先级。只处理对象帧中目标类别列表，保留帧序、重复项、非对象帧及未知字段；目标列表已有新 ID 时删除该帧全部旧 ID，否则逐项替换。响应计数按受影响帧数，章节 JSON 使用兼容的非 ASCII 序列化。

同一业务 Session 在章节 DML 前运行现有媒体协调器；协调器可按既有规则写入媒体状态和私人认可。阶段一只按章节主键更新 `content` 与 `updated_at`，零行或非预期行数按通用 500 回滚，不增加 CAS。阶段一提交后在同一 Session 的新事务中重新读取章节及当前 `series_id`，扫描该剧三类引用，候选先按剧集/类别/旧 ID 查询，再只按候选主键删除；零行删除仍保留旧 warning 语义并提交阶段二。阶段二之后先读回章节，再按原新素材主键读取展示名。任何提交确认或提交后读取失败均不自动重试，需显式读取核实。完整模块边界见[架构说明](chapter-asset-replacement-backend.md)，实测层次及限制见[验证记录](../../openspec/changes/modularize-backend-chapter-asset-replacement/verification.md)。


## 团队 API 兼容记录

团队后端增加 25 个显式路由，分为 management 13 个、series 7 个和 reporting 5 个。该数量是团队接口面；应用总计 82 个注册路由，注册数不代表本轮 TCP 覆盖数。

claim=false 属于系列创建/分享请求，只关联团队，不清除已有 claimed_by/claimed_at；S05 团队认领路由不是该标志的入口。S01 列表包含 chapter_count 和 characters、scenes、props、storyboard 四类 asset_counts。写响应按路由来源处理：S02 读回新建 series 主键行，S03 读回 team 主键并返回 name/id，S05 读回认领字段；S04 和带 claim 的 S06 只返回 message，不执行来源不存在的刷新；S07 的 claimed_by 响应沿用原请求 body 意图。不存在统一的写后 PK 刷新约定。

M06 退队和 M07 移除成员只清理对应 membership 与该成员的团队章节锁，保留 claimed_by/claimed_at；系列移出团队或取消分享只清理 team_id 和相关锁，不解除已有认领。普通剧集访问规则未增加团队认领门禁。

事务兼容继续使用注入的同一 Session 和首次赋值净变化快照，不新增 CAS。dirty UPDATE 零行按通用失败回滚，合法 bulk DELETE 零行仍允许成功；历史无版本条件 DELETE 零行保留警告成功。S02/S03/S05 的指定读回、S04/S06 的 message 和 S07 的 body 意图响应按各自接口执行；提交结果未知或提交后读回失败时不自动重放写请求。
