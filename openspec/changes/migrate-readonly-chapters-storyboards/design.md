# Design

## Context

动机见 proposal.md。新库现有 React 工作台已完成登录、会话恢复和只读剧集列表，主规格尚为空，第一 change 为 15/16（Grillme 离线待审）。本 change 是架构迁移顺序中的第二切片，明确扩展第一切片的禁导航和三条请求边界；不修改其历史任务或验收结果。用户已授权同轮规划后实施，设计/审查使用 GPT-6.1 Sol / xhigh，业务与测试代码由 Luna 6 / xhigh 实现。

旧库仅通过冻结提交 `23403806898550a7668a6ee7c0c457315655c39b` 的 `git show` 读取。章节 GET 返回全量 content，未提供单章节 GET。故事板资产 GET 支持 chapter_id 查询。两条路由均验证剧集访问且只读业务数据；认证依赖仍可能刷新会话并 commit，故不能把它们称为真实后端完全无副作用的探测。

| 契约或风险 | 冻结来源及精确行号 |
| --- | --- |
| 章节访问、排序、content 解码、锁快照 | `backend/app/routes/series.py:280–342`；排序为 order.desc、created_at.desc，锁只查询未过期记录 |
| 章节字段 | `backend/app/schemas/series.py:84–96`；content 为 Optional[List[dict]]，lock 为 Optional[dict] |
| 原图资产 GET | `backend/app/routes/series.py:1647–1660`；按 series_id 与可选 chapter_id 查询，按 frame_index 排序 |
| 原图字段 | `backend/app/schemas/series.py:294–307`；id、series_id、chapter_id、frame_index、name、description、image_url、created_at、updated_at |
| 认领与成员访问 | `backend/app/services/team_service.py:124–131,166–185`；先认领限制，再持有人/团队成员，作者和超级管理员无前端特权旁路 |
| 编辑锁写副作用 | `backend/app/routes/locks.py:31–92,120–137`；POST 获取/续期/接管，DELETE 释放；写章节续期见 `routes/series.py:467` |
| 旧进入链 | `js/episode.js:244–282,330–345`；先 POST lock，之后加载七类模块，本切片不复用 |
| 分镜现有字段 | `js/episode.js:421–439,548–556,814,2250–2252,2297,2311–2320`；text、original_text、character/scene/prop ID 数组、preview（图片/视频） |
| 唯一稳定媒体身份 | `backend/app/personal_production_media.py:91–161`；唯一同章 storyboard[0]，缺失/重复/歧义无索引回退；旧历史入口见 `js/episode.js:2980–2986` |
| 剧集详情字段缺省 | `backend/app/routes/series.py:77–101,177–185`；详情构造省略 claimed_by 和 can_enter，不能用于覆盖列表认领结论 |

## Goals / Non-Goals

**Goals:**

- 用小范围章节模块完成可操作、可阅读、可返回的闭环，让未知镜头字段和不可信媒体关系可以被看见。
- 页面、数据适配和只读媒体投影分离，后续编辑/个人辅助不会继承展示索引作为持久身份。
- 契约、真实 fetch、UI 与真实浏览器验收各自提供可重复证据。

**Non-Goals:**

- 不迁移旧 episode.js、编辑、自动分镜、素材目录、个人笔记/粗剪、任务、账务或队列。
- 不调用任何锁接口，不播放 preview、不下载或导出，不复制旧运行配置或启动旧后端。
- 不新增后端、路由依赖、数据库、Worker、Go、Redis、微服务或新队列，不推送和部署。

## Decisions

### 导航与权限

在现有剧集卡片增加独立按钮“只读查看章节”；保留卡片受限状态及列表筛选，can_enter=false 时没有可执行进入行为。工作台用本地状态保存当前剧集和选中章节 id，显示“我的剧集 / 剧集名 / 章节名”、只读说明及返回入口。首次进入先加载章节，成功后按响应第一项的稳定 id 默认选章；空列表无选中章，选中章 content 为 null/空数组时不请求资产。后续用户按稳定 id 切章。列表组件维持挂载，在查看章节时放入 hidden 容器，以保留四筛选和已展示条数并退出可见/键盘焦点范围；返回时卸载章节模块及其请求，不触发额外列表查询。用户或会话变化重建工作台上下文。刷新回到剧集列表，不恢复未再次验证的章节内容；不引入 Router 或 URL 深链接。

剧集名称和持有人等来自已取得的 Series 列表项；不增加详情 GET，避免其缺省认领字段覆盖列表结果。can_enter 是入口提示，章节和资产 GET 的 403 才是当前访问拒绝；不能以 lock.is_mine 或 user.is_superuser 越过它。他人编辑锁只提示加载时占用快照，并不禁止被服务端授权的只读查看。

### 契约与内容投影

在共享契约中增加 Chapter 和 StoryboardAsset 类型；Chapter 外层逐项验证非空 id/series_id、字符串 title、整数 order、日期、content 为 null 或对象数组、lock 为 null 或对象，核查全部章节属于当前 series_id 且 id 不重复。保留 lock 原字段，不请求头像；只投影 locked、locked_by_username、is_mine、expires_at 的安全展示信息。类型不合法不得变成 []。标题允许服务端合法的空字符串时使用“未命名章节”展示，不生成 id；其余身份字段必须非空。

content 保留 Record<string, unknown>[]，不把 SceneFrame 创建输入模型当作完整响应字段。读取 text/original_text 的合法字符串，缺失用占位，已存在但类型不可读用局部提示；unknown 字段不展开原始 JSON。character/scene/prop 只对合法字符串数组计算引用数量，不展示裸 ID、不解析名称、不加载对应目录。API content 字符串不符合 ChapterResponse，不沿用旧页面的二次 JSON parse；旧路由已将损坏数据库 JSON 转成 []，客户端无法从响应恢复该历史原因，验收不声称已识别数据库损坏。

章节保持 GET 数组顺序；分镜保持 content 顺序，展示序号是 index+1。章节及 assets 的必需字段进行运行时校验，不凭静态 TS 类型接受未知 JSON。原图资产保留完整响应字段，frame_index 为整数，但不用于配图。日期沿用首切片规则：无时区 ISO 按 UTC 解释。

### 原图和预览

建立纯只读投影：对当前章全部帧统计合法 storyboard[0] 引用次数，再按当前 series_id、chapter_id 和 id 查找资产。候选引用只出现一次且同上下文资产唯一时，才提供对应 image_url；缺失/非字符串/重复引用、跨章/跨剧集资产和重复候选均显示关系不可用。可以用 index 作为无身份展示行的本地渲染辅助，但不能当作持久镜头 id、构造资产 id 或回退配图。重排检验刻意让 frame_index 与当前位置不一致，确保映射仍按 id。

原图 URL 复用现有同源 loopback 检查，图片失败退回本地占位；mock 图均为 null 或本地静态占位，不发业务/媒体网络请求。frame.preview 的合法非空字符串仅投影“已有预览”，不渲染地址、video、iframe、外链或下载。这样无需猜测预览图片/视频类型，也不会进入旧 CDN helper 或真实 R2 请求链。其他真实头像同样不请求。

### 请求、取消和错误

服务层新增 listChapters(seriesId, signal)、listStoryboardAssets(seriesId, chapterId, signal)，复用既有 Bearer、timeout（覆盖 JSON body）、abort 和 error 分类。动态 path segment 按 encodeURIComponent 编码，query 用 URLSearchParams；拒绝空身份和单独的 `.`/`..` 路径段，避免 URL 规范化穿越固定路由。不得让组件传入任意 URL，不公开通用写请求入口。

当前业务白名单恰为：

1. `POST /api/auth/login`
2. `GET /api/auth/me`
3. `GET /api/series`
4. `GET /api/series/{series_id}/chapters`
5. `GET /api/series/{series_id}/storyboard-assets?chapter_id={chapter_id}`

静态资源与明确允许的同源 loopback 原图不属于业务 API，fixture 日志和测试应分别核查。mock 不回退 API；API 页和服务地址必须符合现有 loopback 配置，不增加旧服务 proxy。

章节数据和资产数据有独立加载/成功/失败状态。切换剧集立即清空章节和选中章；切换章节立即清空原图并显示当前文字，返回或退出卸载章节上下文。效果清理同时 abort 和标记旧世代，调用方在处理成功及 401 前核查上下文，防止忽略 AbortSignal 的 fixture 返回迟到错误后注销新会话。重复选当前章不发新请求；已成功解析的资产数组只在当前会话/剧集内按 chapter_id 缓存，A→B→A 复用同一成功快照，不复用失败结果。切章只清可见旧图，返回列表/用户变化/重新读取章节清全缓存；章节重新读取后再次按新响应第一项选章。显式资产重试清当前缓存、建立新世代并只重发当前资产 GET。任何缓存写入同样核对请求世代，迟到结果不能填充缓存。

章节 GET 失败显示整页错误；资产 GET 失败保留当前文字，在原图区显示错误及重试，不当作成功空资产。仅有效 401 调用当前会话注销；403 区分会员/访问拒绝，404 显示剧集或内容不存在，其他类别复用首切片语义。空章节和空分镜独立于错误。不读取 muse_* 章节缓存，不持久保存章节/镜头数据。

### 来源追踪和验收层级

不扩展 legacy-reference。现有完整 schemas/series.py 已覆盖两类响应，保持四 full + GET103–174 excerpt 的精确五项 manifest、合同及验证器；不放宽 exact-five 校验。下表仅为设计引用的冻结全文 hash，源文件不新增复制、导入或执行：

| 来源文件 | SHA-256 |
| --- | --- |
| backend/app/routes/series.py | db9721e34a3c5f22f81ce7cf4bc5434d9f3661d732ab1e8a79ba2e74852243cf |
| backend/app/schemas/series.py | 78b1aee6e31cc195fb9e9af7401738ed1c2b3a73a9563f196855dd9912e3bfb9 |
| backend/app/services/team_service.py | 392f0792be876adda6b4b7629af0624a1dfbaef6fbf8555d7cacfd96cc81326f |
| backend/app/routes/locks.py | 3296f523a265e9327ab4536dadbb48173da3cdaa9e8ff1bbdc60c7689422a131 |
| backend/app/personal_production_media.py | 583db9cab32331cf37eafefbe4f71ad3ff7c7c7a56e809137f53063a2f038928 |
| js/episode.js | 79b117ce13e633b6b641faca7232328358e8e6f3be40825dcf097f87994d0e9f |
| js/api.js | ac6d3c2e913f91cede06e24cdfa6b100c95021b41f02adbcb594029bd8309b7c |

验证分为：冻结 schema/路由静态核对、mock/UI 自动化、隔离 HTTP fixture 的真实 fetch、真实浏览器 mock/API。fixture 扩展为当前剧集的多章及每章独立资产，提供空章/空内容、锁快照、坏字段、缺失/重复/跨章身份、403/404/500、超时和原始无效 JSON。日志记录请求方法/路径，不输出 token、密码或完整内容。原图只提供合成的本地图片，不复用真实媒体。

真实 FastAPI/PostgreSQL、生产权限、DB 排序/锁并发及认证会话副作用本轮未运行。源码审查或字段名比较不替代这些验证；Grillme 固定 GPT-5.6 Sol / xhigh，当前离线待审，不降档、不启动配对，与本地开发并行。

## Risks / Trade-offs

- [content 宽松且旧 GET 隐藏了损坏 JSON 原因] → 外层严格解析，镜头已知字段局部提示；报告 API 能观察的范围，不虚构后端完整性结论。
- [全量章节带全部 content] → 保留既有接口，不新增分页协议；本切片只在选章后读取资产，避免一次加载所有素材。
- [列表权限快照可能过时] → 每次进入由章节 GET 重新校验；403 明确返回，前端不认领、不绕权。
- [镜头索引和资产 frame_index 可能不同] → 纯投影按唯一同章稳定 id 配图，用重排反例验证。
- [用户切换与晚到 401] → 清理上下文、abort 与世代校验覆盖成功/错误两条路径，自动测试使用故意忽略 signal 的 deferred 服务。
- [原图失败或受限地址] → 保留文字，展示占位或独立错误，不生成/重提任务，不回退远端。
- [第一切片尚未归档且 Grillme 离线] → 新能力注明边界替代，首片历史不改；离线审查继续单列未完成。

## Migration Plan

1. 先核对本 change 四份规划产物与精确来源；根代理确认 apply ready 后由 Luna 6 / xhigh 实现新库功能和有意义的测试。
2. 扩展 mock 和隔离 HTTP fixture，执行 typecheck、契约/UI tests、build、现五项来源验证和严格 OpenSpec 验证，检查实际文件而非只依赖未跟踪仓库的 git diff。
3. 真实浏览器验证完整查看路径、受限入口、锁快照、切换/返回、错误/重试、桌面/移动和键盘；记录截图或可重现操作及请求证据。
4. 更新当前 README/模块说明和本 change 验证记录；不回写首片历史结果。不提交/推送/部署，旧库保持冻结只读。回退仅恢复新前端的列表入口，无数据库迁移或真实服务操作。
