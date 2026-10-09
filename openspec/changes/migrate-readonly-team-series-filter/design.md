# Design

## Context

动机见 proposal.md。当前 `SeriesPage.tsx:51-98` 保存四分类和20条递增数量，按 `/api/series` 全量数组本地筛选；`Workspace.tsx:119-137` 通过 hidden/inert 保留列表挂载，进入章节、素材或任务后返回恢复列表状态。现有接口共十条；没有团队目录 service，也没有团队二级选择。

所有旧源码只通过提交 `23403806898550a7668a6ee7c0c457315655c39b` 的 `git show` 静态读取。`routes/teams.py:95-117` 以当前用户 TeamMember 查询团队，返回六字段数组，无 response_model、排序或显式 commit；认证 `routes/auth.py:112-135,151-172` 可能创建/维护 session 并提交。旧 UI 在 `js/app.js:4283-4297` 登录加载时预取目录，失败静默使用旧缓存；`4603-4633,4644-4658` 按团队 ID 本地筛选并包含无剧集团队选项。本切片有意改为按需读取与明确错误，其余分类、来源顺序及权限语义保留。

## Goals / Non-Goals

**Goals:** 在现有列表上增加可单独验收的团队目录和本地二级筛选；给保留挂载的列表明确请求可见性；使刷新、隐藏、返回及会话变化的状态一致。

**Non-Goals:** 不实现团队管理、额外剧集查询、角色授权、成员列表、邀请、分享、认领或锁；不改既有 DTO decoder、任务/素材/章节业务；不增依赖、URL路由或持久化缓存。前五 change 三十份历史文件和 exact-five 来源复制均不修改。

## Decisions

### 1. 独立六字段 DTO 与唯一静态 GET

新增团队 DTO/decoder（建议 `frontend/src/shared/api/teams.ts`）及 `WorkspaceServices.listMyTeams(signal: AbortSignal): Promise<MyTeam[]>`；API adapter 增加专用静态分支 `/teams/my`，复用既有 current Bearer、受限 API base、abort/timeout 和错误分类。不得落入现有动态 series 路径分支，不传 user_id、team_id、page 或 page_size。

| 字段 | wire 与校验 | UI用途 |
|---|---|---|
| id | required own，非空 string；不要求 UUID，保留原值 | 稳定选项与精确过滤 |
| name | required own string，允许空字符串 | 普通文本；空值回退“团队” |
| owner_id | required own，非空 string | 保留，不推断当前权限 |
| created_at | required own，合法 ISO date-time string | 保留；无时区的冻结 UTC 值按 UTC 解释，不由本地 Date 隐式偏移 |
| member_count | required own，非负 safe integer | 保留，不与其他响应做强一致断言 |
| my_role | required own string，允许空/未知值 | 保留，不用枚举拒绝、不推导权限 |

合法日期允许冻结来源的无时区 UTC 形式，以及明确时区的 ISO 形式；须检查实际日历日期，拒绝缺省、null、非法日期和错误类型。顶层仅数组；id重复拒绝整份响应，不静默合并。同名、`all`、`__proto__` 等非 UUID ID 仍是有效独立身份；不得用普通对象原型查找或名称当索引。六字段依据返回 dict 及 `models/team.py:15-18,26-33`，不伪造 Pydantic schema 或真实 FastAPI 回归。

选择目录 GET 而非从 series 反推：后者丢失暂无剧集的所属团队。选择本地筛选而非 `GET /teams/{id}/series`：后者是不同分页结构且没有现有 `can_enter`，见 `routes/teams.py:468-535`，无本轮必要性。邀请 GET 会维护过期记录并提交（345-365），明确禁入。

### 2. 名单不是剧集授权

新增选择值 `selectedTeamId: string | null`，null 独立表示“全部团队”，不可用字符串 `all` 当哨兵。使用带明确 label 的原生 select：全部选项的空 DOM value 与非空真实 ID 分离；同名选项仍按 id区分，名称作为纯文本。团队分类下零个或一个团队也显示选择器。

全部团队继续按现有 `team_id` 有值筛选；具体团队按原始 id严格相等，然后按原数组顺序 slice。`routes/series.py:117-124` 包含本人持有剧集，因此目录没有某个 team_id 不代表其剧集应被删除。`can_enter` 继续来自 `routes/series.py:148-153`，不由 my_role、owner_id 或 member_count改写；权限点保留 `team_service.py:104-131,166-185` 的既有口径。

具体团队/主分类实际切换才重置展示数20，离开团队主分类清 specific选择；重复点击当前分类或选择当前团队是幂等操作，不重置数量、不重发目录GET。进入章节、素材、任务时不清列表选择或展示数；返回恢复。目录结果不排序，剧集仍遵循服务端 updated_at.desc原序。实际成员/权限变动仍由后续已有章节/素材GET复验，目录仅提供筛选元数据。

### 3. 可见性、成功快照与请求世代

Workspace 给 SeriesPage 明确 `active` 可见性：selectedSeries为空且selectedView为series时为true。新增团队请求仅在 active且主分类team时允许。保留挂载负责保存选择/数量；active不改变现有剧集列表来源或增添series请求。

目录成功快照仅存在当前 userId、services/会话上下文的组件内存，`null`表示未成功读取，`[]`表示成功无团队；不读写 localStorage或旧 StorageManager。userId/services变化或退出销毁快照、选择、错误和请求状态，展示数回20。快照与所有目录state绑定userId及services对象身份；渲染时先检查scope，只渲染匹配当前scope的值，不能等effect清理才隐藏旧团队名。Workspace实际退出会卸载Dashboard；独立rerender测试仍须验证换user和换services的第一次呈现不泄露旧目录。采用独立 AbortController及递增 generation；离开team分类、active=false、重读、用户/服务变化和卸载时中止并作废旧世代。旧成功与旧401都必须先通过 current generation/context检查，才允许设置状态或调用onUnauthorized。

| 场景 | 请求与恢复 |
|---|---|
| 登录或all/mine/claimed分类 | 不请求teams |
| 可见team分类首次打开 | 无成功快照且无实际错误时发一个GET |
| 具体团队切换 | 仅本地filter，不发GET |
| 已成功后隐藏/返回 | 保留成功snapshot/选择/数量；返回不发GET |
| 无成功snapshot，pending被隐藏或切出中止后返回team | 原世代失效；状态回待读取，返回发一个新GET |
| 读取曾实际失败后离开/返回 | 保留明确错误，等显式重试；不自动GET循环 |
| 已有snapshot的重读被隐藏中止 | 保留之前成功snapshot；返回恢复，需显式重读再请求 |
| 退出/换用户 | 清空上下文；旧请求随后settle不能影响新用户 |

保留成功快照优于隐藏时卸载整个列表，后者会丢失既有筛选与展示数。当前401仅在请求仍有效时退出；其他错误保持会话。service本身不缓存目录、不因旧401删除存储，新UI复用既有会话边界。

### 4. 刷新、错误与三个空状态

“重新读取团队”在pending仍可点击，每次先作废并abort旧请求，再发新的唯一GET；不补seriesGET。重读时保留之前成功snapshot、selectedTeamId与数量，并明确loading。应用成功响应时须检查此刻有效的selectedTeamId，不得使用GET发起时的过期闭包；pending期间用户仍可在上次成功选项中切换团队。当前选中id若存在则保持；若不存在，原子设置null与20条并提示“团队名单已变化，已显示全部团队”。不能依赖native select自动显示第一项，却让过滤state留旧id。

错误明确显示、允许手动重试；如有成功snapshot，标注“当前为上次成功的团队名单”，不静默旧缓存fallback。没有成功snapshot时只提供全部团队，不虚构具体选项。会员403和其他403/404/422/5xx/timeout/network/invalid-response均保留会话与现有series。合法空目录显示“暂无所属团队”；具体团队0条显示“该团队暂无剧集”；现有全部series为空沿用列表空态，不互相覆盖。

### 5. demo 与隔离 fixture

新增无网络demo目录，保持现有单一demo账号和24条剧集（16条team），目录含同名/空名/未知role/非UUID及暂无剧集团队；两账号隔离只在API fixture验证，不扩展demo认证。不为浏览器20→40验收扩大历史demo数据。过滤单测可以独立构造45条，真实浏览器使用all20→24、切team重置及返回状态。fixture新增唯一业务路由 `GET /api/teams/my`，只currentBearer，无用户参数；用本地合成的两账号数据验证隔离，通过环境变量和重启fixture模拟正常/empty、名单移除、非401错误/401、invalidJSON/invalidshape和delay，不增加控制HTTP端点。日志不写token、密码或敏感原文。

当前业务十一条为：POST `/api/auth/login`；GET `/api/auth/me`、`/api/series`、`/api/series/{seriesId}/chapters`、`/api/series/{seriesId}/storyboard-assets?chapter_id=...`、`/api/series/{seriesId}/characters`、`/api/series/{seriesId}/scenes`、`/api/series/{seriesId}/props`、`/api/chapters/{chapterId}/personal-production-notes`、`/api/chat/tasks/list?page=N&page_size=10`、`/api/teams/my`。不改既有GET行为或引入媒体请求。

## Risks / Trade-offs

- [目录与剧集非原子快照] → 不以成员名单删除剧集或推断权限；成功目录只影响选项，名单删除时同步选择state；后续已有GET仍复验访问。
- [隐藏列表仍挂载，旧401可能影响任务页] → active+generation+abort隔离；自动测试须真正settle旧Promise并await React act，不能仅断言abort signal。
- [无response_model，历史角色与名称宽松] → 六必备字段严格解码，名称/角色保持string宽容，错误独立可重试；来源静态与fixture契约不称真实Pydantic/PG回归。
- [登录预取改为按需，首次team可能短暂loading] → 保留已有剧集和全部团队选项，loading/错误明确；此处是有意迁移选择。
- [认证GET可维护session] → API仅loopback隔离fixture，真实FastAPI/DB/旧服务未运行，不声称无维护写。

## Migration Plan

仅在 haoai-next 实施此独立change：先DTO/service/fixture，再UI接线及风险测试，完成本地typecheck/test/build与真实demo/API浏览器验收后更新当前文档。按来源、自动、浏览器、真实后端未执行、Grillme离线分别记录。本轮不提交、推送、部署；若需回退只撤销此change新增team接口/入口，不改前五历史产物。固定GPT-5.6 Sol/xhigh Grillme离线任务不阻断本地开发，不由其他模型替代通过。

冻结全文SHA-256仅为文档来源，不复制新增legacy-reference：

| 源码路径 | 全文SHA-256 |
|---|---|
| backend/app/routes/teams.py | fd6eecf5d427aeedb18c73fae88882c9cdbb8d5483d1d5e5321649fff40fc964 |
| backend/app/models/team.py | 5163c6c4627bd0e519793ce8054c6ed38ac854bef9143c2ec0d9992de82a22cd |
| backend/app/services/team_service.py | 392f0792be876adda6b4b7629af0624a1dfbaef6fbf8555d7cacfd96cc81326f |
| backend/app/routes/auth.py | 6cae3297fb5806c8668cbd245be974ff42ccf42999265440a6d6e27a7e6d1a78 |
| backend/app/routes/series.py | db9721e34a3c5f22f81ce7cf4bc5434d9f3661d732ab1e8a79ba2e74852243cf |
| js/app.js | a72e631987b23ec0dbd3c90a0f2be7bd1da4e49c4ddd10c71fee4a9c07763a78 |
