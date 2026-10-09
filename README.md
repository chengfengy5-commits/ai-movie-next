# Hao AI 新版工作台

这是 React + TypeScript + Vite 的本地工作台。本阶段提供隔离登录会话、带按需团队目录二级筛选的只读剧集列表、剧集内章节与分镜浏览、镜头关联角色/场景/道具文字详情按需查看、角色/场景/道具素材浏览、当前章节个人制作记录与粗剪草稿只读面板、本人任务记录只读分页列表，从已核对个人记录显式定位续作镜头，以及从素材卡片查看关联镜头并按新章节和素材快照定位具体分镜、从分镜素材引用显式定位到同类素材卡片、从当前粗剪显式定位到对应镜头；剧集创建、认领、编辑、生成、上传和音频入口尚未开放。

## 本机演示

Node.js 版本需满足 `^20.19.0 || ^22.12.0 || >=24.0.0`；本机已验证版本为 20.20.2。每个终端都先进入这个仓库目录：

```sh
cd /Users/yanghaibo/data/projects/ai/haoai-next
npm --prefix frontend ci
npm --prefix frontend run dev
```

默认使用完全独立的演示服务，不发起业务或媒体网络请求。登录凭据为账号 `demo`、密码 `demo123`。演示会话只使用 `haoai_next_mock_session` 和 `haoai_next_mock_user` 两个浏览器存储键，不读取旧产品会话、剧集缓存或远端素材。样例剧集含本地章节、只读分镜、个人记录以及角色、场景与道具；制作记录只在章节内显式打开后显示。只有续作位置能与当前章节唯一镜头及媒体快照核对时，面板才显示“定位到续作镜头”；待重新确认状态仍可定位，但不会自动跳转，必须由用户点击。重读或关闭记录、切章及章节/资产快照变化会使旧定位入口失效。媒体身份由浏览器本地计算 SHA-256；远端原图和 preview 字符串只参与身份归一，不会被请求或渲染。

## 隔离 HTTP API 模式

先在一个终端启动仅监听 loopback 的本地 HTTP fixture：

```sh
cd /Users/yanghaibo/data/projects/ai/haoai-next
npm --prefix frontend run fixture
```

默认地址为 `http://127.0.0.1:4175/api`。fixture 有两个彼此隔离的合成账号：`demo` / `demo123` 和 `demo-two` / `demo-two123`；两者可读取同一章节，但看到不同个人记录和团队目录。fixture 提供十二条允许的业务接口（登录 POST 与十一条 GET）：会话校验、剧集列表、章节列表、选中章节的分镜资产列表、角色/场景/道具列表、个人制作记录、个人粗剪草稿、本人任务分页及当前用户的团队目录；另提供 loopback 本地合成 PNG。团队目录只在用户打开可见的“团队剧集”筛选时读取，不传用户或团队查询参数。任务列表和团队目录由当前合成账号决定，不接受客户端指定 user_id 或剧集范围。fixture 不访问旧 FastAPI、PostgreSQL、Worker、R2 或真实素材。

在另一个终端以显式配置启动 API 模式：

```sh
cd /Users/yanghaibo/data/projects/ai/haoai-next
VITE_AUTH_MODE=api \
VITE_API_BASE_URL=http://127.0.0.1:4175/api \
VITE_DEV_PORT=5174 \
npm --prefix frontend run dev
```

API 模式只允许在 loopback 页面上连接 loopback API；会话使用兼容键 `muse_auth_token` / `muse_auth_user`。演示键和 API 键互不读取、互不清理。fixture 默认提供四章样例，其中章节响应顺序刻意与 `order` 不同，前两章带有本地合成原图，另两章分别测试 `content: null` 和空分镜。会员限制需要同时设置 403 和对应说明：

```sh
cd /Users/yanghaibo/data/projects/ai/haoai-next
FIXTURE_SERIES_STATUS=403 FIXTURE_SERIES_DETAIL=membership npm --prefix frontend run fixture
```

剧集列表可用以下变量演练；每次启动只设置需要的一个或一组状态：

```sh
cd /Users/yanghaibo/data/projects/ai/haoai-next
FIXTURE_SERIES_STATUS=500 npm --prefix frontend run fixture
FIXTURE_SERIES_STATUS=timeout npm --prefix frontend run fixture
FIXTURE_SERIES_STATUS=body-timeout npm --prefix frontend run fixture
FIXTURE_SERIES_MODE=invalid-json npm --prefix frontend run fixture
FIXTURE_SERIES_EMPTY=true npm --prefix frontend run fixture
```

章节、分镜资产和素材列表接口可分别设置错误、超时或空响应。下例中的环境变量在启动 fixture 前设置：

```sh
cd /Users/yanghaibo/data/projects/ai/haoai-next
FIXTURE_CHAPTERS_STATUS=403 npm --prefix frontend run fixture
FIXTURE_CHAPTERS_MODE=body-timeout npm --prefix frontend run fixture
FIXTURE_CHAPTERS_MODE=invalid-json npm --prefix frontend run fixture
FIXTURE_CHAPTERS_MODE=invalid-structure npm --prefix frontend run fixture
FIXTURE_CHAPTERS_EMPTY=true npm --prefix frontend run fixture
```

素材列表也可各自设置状态、响应类型或空列表：

```sh
cd /Users/yanghaibo/data/projects/ai/haoai-next
FIXTURE_CHARACTERS_STATUS=403 npm --prefix frontend run fixture
FIXTURE_SCENES_MODE=body-timeout npm --prefix frontend run fixture
FIXTURE_PROPS_MODE=invalid-json npm --prefix frontend run fixture
FIXTURE_PROPS_EMPTY=true npm --prefix frontend run fixture
```

个人制作记录接口可控制 HTTP 错误、超时、无效 JSON/结构、媒体摘要不匹配、原图与 preview 双空旧认可，以及延迟响应：

```sh
cd /Users/yanghaibo/data/projects/ai/haoai-next
FIXTURE_PERSONAL_NOTES_STATUS=500 npm --prefix frontend run fixture
FIXTURE_PERSONAL_NOTES_MODE=body-timeout npm --prefix frontend run fixture
FIXTURE_PERSONAL_NOTES_MODE=invalid-json npm --prefix frontend run fixture
FIXTURE_PERSONAL_NOTES_MODE=mismatch npm --prefix frontend run fixture
FIXTURE_PERSONAL_NOTES_MODE=double-empty npm --prefix frontend run fixture
FIXTURE_PERSONAL_NOTES_DELAY_MS=5000 npm --prefix frontend run fixture
```

fixture 的 `fixture-series-01-chapter-03` 提供合法 unreadable 记录，`fixture-series-01-chapter-04` 是 revision 0 的空记录。个人记录按当前 Bearer 隔离，不接受 `user_id` 查询参数。客户端没有 PUT、编辑、认可、续作写入、取锁或历史请求；“只读”描述的是页面交互。旧 GET 可能初始化/协调媒体版本，或撤销同章其他用户过期认可；本切片只连接演示/隔离 fixture，不把接口描述为数据库纯读取。

个人粗剪草稿只在章节内显式打开后读取 `GET /api/chapters/{chapter_id}/rough-cut`，使用当前 Bearer，不带查询参数。页面只呈现读取快照的顺序、章节镜头位置、纳入/待安排状态和原因；它不播放、加载或展示 `preview_url`，也不暴露稳定素材 ID。草稿保存行只记录资产 ID 与纳入状态，不冻结媒体版本；正文和视频引用来自当前章节源数据。默认 demo 从现有章节/资产合成本地投影，不改变剧集和章节样本，不发起业务或媒体请求。隔离 fixture 的两账号可查看同章不同私有快照；设置变量后重启 fixture：

```sh
cd /Users/yanghaibo/data/projects/ai/haoai-next
FIXTURE_ROUGH_CUT_MODE=unsaved npm --prefix frontend run fixture
FIXTURE_ROUGH_CUT_MODE=unsaved-no-video npm --prefix frontend run fixture
FIXTURE_ROUGH_CUT_MODE=empty npm --prefix frontend run fixture
FIXTURE_ROUGH_CUT_MODE=removed npm --prefix frontend run fixture
FIXTURE_ROUGH_CUT_MODE=legacy npm --prefix frontend run fixture
FIXTURE_ROUGH_CUT_MODE=pending npm --prefix frontend run fixture
FIXTURE_ROUGH_CUT_STATUS=500 npm --prefix frontend run fixture
FIXTURE_ROUGH_CUT_STATUS=403 FIXTURE_ROUGH_CUT_FORBIDDEN=membership npm --prefix frontend run fixture
FIXTURE_ROUGH_CUT_MODE=invalid-shape npm --prefix frontend run fixture
FIXTURE_ROUGH_CUT_DELAY_MS=5000 npm --prefix frontend run fixture
```

粗剪还支持 `FIXTURE_ROUGH_CUT_STATUS=401|403|404|422|500`、`FIXTURE_ROUGH_CUT_FORBIDDEN=ordinary|membership`、`FIXTURE_ROUGH_CUT_MODE=timeout|body-timeout|invalid-json` 和延迟响应。本地验收只使用 demo 与隔离 fixture，不连接真实后端。

`CHAPTERS`、`ASSETS`、`CHARACTERS`、`SCENES`、`PROPS` 都支持各自的 `_STATUS`、`_MODE`、`_EMPTY` 变量；`_STATUS` 支持 401、403、404、422、500，`_MODE` 支持 `timeout`（不返回响应头）、`body-timeout`（先发送响应头和部分 body 后挂起）、`invalid-json` 和 `invalid-structure`。个人记录支持 `_STATUS`、`_MODE`、`_DELAY_MS`，其中 `_MODE` 还包括 `mismatch` 和 `double-empty`。分镜图和素材图片可用 `FIXTURE_MEDIA_STATUS=404` 或 `FIXTURE_MEDIA_MODE=invalid-image` 演练破图占位及重读恢复。设置 `FIXTURE_PORT=0` 可让测试在动态空闲端口启动。login、me 和 series 继续支持各自的状态变量。fixture 仅为接口适配和浏览器验收提供可控本地响应，不表示真实后端回归通过。

素材接口的 fixture 403 为普通访问拒绝；会员限制文案由素材 UI 自动用例验证，fixture 不提供每条素材接口独立的 detail 配置。

团队目录 `GET /api/teams/my` 只返回当前 Bearer 对应的目录。演练时修改下列变量后重启 fixture；每次启动可组合设置需要的变量：

```sh
cd /Users/yanghaibo/data/projects/ai/haoai-next
FIXTURE_TEAMS_MODE=empty npm --prefix frontend run fixture
FIXTURE_TEAMS_MODE=removed npm --prefix frontend run fixture
FIXTURE_TEAMS_STATUS=403 npm --prefix frontend run fixture
FIXTURE_TEAMS_STATUS=500 npm --prefix frontend run fixture
FIXTURE_TEAMS_MODE=invalid-json npm --prefix frontend run fixture
FIXTURE_TEAMS_MODE=invalid-shape npm --prefix frontend run fixture
FIXTURE_TEAMS_MODE=timeout npm --prefix frontend run fixture
FIXTURE_TEAMS_DELAY_MS=5000 npm --prefix frontend run fixture
```

`FIXTURE_TEAMS_STATUS` 还支持 401、404 和 422；`FIXTURE_TEAMS_REMOVED_ID` 可指定 removed 模式移除的合成团队，`FIXTURE_TEAMS_DELAY_MS` 控制成功响应延迟。目录只作为筛选元数据，不是访问授权；即使列表中保留某团队，也不能据此推断后续章节或素材请求一定可读。

任务列表 fixture 默认每个合成账号提供 23 条记录，每页固定 10 条；默认演示模式使用本地合成的 12 条记录、两页数据且没有业务 fetch。可用以下变量演练当前任务分页接口：

```sh
cd /Users/yanghaibo/data/projects/ai/haoai-next
FIXTURE_TASKS_STATUS=500 npm --prefix frontend run fixture
FIXTURE_TASKS_MODE=body-timeout npm --prefix frontend run fixture
FIXTURE_TASKS_MODE=invalid-json npm --prefix frontend run fixture
FIXTURE_TASKS_MODE=page-mismatch npm --prefix frontend run fixture
FIXTURE_TASKS_MODE=duplicate-id npm --prefix frontend run fixture
FIXTURE_TASKS_MODE=empty-page npm --prefix frontend run fixture
FIXTURE_TASKS_MODE=total-shrink npm --prefix frontend run fixture
FIXTURE_TASKS_DELAY_MS=5000 npm --prefix frontend run fixture
```

`FIXTURE_TASKS_STATUS` 可设置 401、403、404、422 或 500；`FIXTURE_TASKS_MODE` 还支持 `timeout`、`invalid-structure`、`page-size-mismatch` 等模式。`total-shrink` 将总数收缩为 7，让较后页返回合法空列表，页面提供显式回到第一页操作，不自动跳页。任务 fixture 的完整模式以 `tools/api-fixture/server.mjs` 为准；它仅用于本地接口与浏览器验收，不表示真实任务后端、数据库权限或计费回归通过。

## 素材卡片关联镜头

在素材库中，用户可从有效的素材卡片显式打开关联镜头面板。只有当前目录内同剧集、非空且唯一的原始素材 ID 会启用入口；打开后复用现有章节列表读取，按当前角色、场景或道具分类和原始 ID 匹配，保留章节/镜头顺序并显示同镜头重复引用次数。目录进入、切分类或显示卡片时不预读章节；打开关联面板不会自动导航章节，面板本身不新增 API、媒体元素或主动媒体读取。

用户显式打开任一合格素材卡片后，系统读取当前剧集章节，再由客户端判断是否命中；请求前不会预判章节里有没有引用。缺失、null 或空数组不计命中，mixed 数组保留合法命中并单独提示非法项；无效章节身份或剧集归属会拒绝快照。零匹配只表示当前章节快照未找到关联镜头。滚动可能使页面既有 lazy 图片自然加载；这不是关联镜头面板增加的媒体读取。

## 从素材关联结果导航章节

素材关联面板为每个命中章节分组提供“查看对应章节：<标题>”。入口绑定生成当前 ready 反查结果的原始章节 ID。点击后，工作台卸载来源面板并创建绑定当前用户、服务、剧集、章节和导航世代的一次性意图；章节页随后重新读取完整当前目录；只有目标原始 ID 在新目录中仅出现一次，且该唯一项属于当前剧集时才选中目标。标题、列表位置和旧快照顺序不作为身份，也不回退到首章。

目录错误、缺失、重复或跨剧集目标会保留待核对意图并显示重读入口；用户手动选择其他有效章节会放弃该目标，成功选中后会消费它，普通重读不会再次应用。旧来源回调不能更改当前剧集或导航意图。唯一目标有分镜时复用现有分镜素材读取；空章节不发起该读取。返回剧集列表沿用既有流程并保留已展示数量和筛选。此功能复用现有 GET，不新增路由、DTO 或写入；不新增媒体接口或播放链路，沿用既有章节安全原图展示。

## 从素材关联结果定位具体分镜

在关联镜头面板中，每个身份有效的分镜保留“定位对应镜头：章节 · 镜头<n>”入口。入口绑定当前 ready 章节响应中的原始章节 ID、该 frame 的 `storyboard[0]` 原始 ID，以及当前角色/场景/道具类别引用的素材原始 ID；章节 ID 先在整份来源响应中核验唯一并属于当前剧集，frame 的 storyboard ID 也须在该章唯一。类别字段重复引用同一素材仍只提供一个入口。

点击后，工作区先按当前来源上下文校验并复制身份。目标章节页重新读取章节目录，先在整份新响应中统计章节原始 ID，再确认其属于当前剧集；之后核验目标 frame 的 `storyboard[0]` 和相同类别素材引用，最后读取一次目标章节素材并核验完整响应中唯一匹配的素材 ID、剧集和章节归属。章节或 frame 前置失配时不选默认章、不读取目标素材；素材后置失配时保留已核验章节与待重核目标。显式重核从新章节目录开始。不根据标题、数组位置、`order`、`frame_index`、图片地址或相邻镜头猜测身份。

只有当前已核验的目标分镜 DOM 节点实际获得焦点，并再次通过 scope 与节点检查、滚动到视口后，系统才消费目标。失焦会清除定位反馈，成功后普通章节重读不重复定位；旧章节级入口仍只定位章节。该导航复用既有只读读取链路，不新增 API、DTO、服务方法、fixture 路由、媒体接口或播放链路；章节页既有安全原图展示保持不变。

## 从分镜素材引用导航到素材卡片

分镜关联素材面板中的有效素材引用提供“查看素材：素材名”入口。用户点击后，工作台按当前用户、services、剧集、素材类别和原始素材 ID 建立一次性导航意图；来源面板卸载不取消已接受的意图，过期来源回调不能改写当前视图。目标素材库只读取所选类别的当前目录，不预读其他类别。

目标目录按完整响应先统计原始 ID，再核验唯一记录属于当前剧集。系统不按名称、别名、数组位置或其他类别猜测身份；同一个原始 ID 可分别出现在不同类别。只有唯一匹配的当前素材卡片实际获得焦点并通过当前作用域复核后，导航意图才会消费。空目录、重复/缺失 ID 或跨剧集记录不会落到相似卡片；用户可显式重读重新核验。既有章节、资产列表读取和素材卡片展示保持原样，本切片不新增 API、DTO、服务方法、fixture 路由、媒体接口或播放链路。媒体行为沿用素材库现有图片路径。

## 从粗剪草稿定位对应镜头

当前章节的个人粗剪面板为可核对的草稿条目提供“定位到对应镜头 N”。定位身份要求同一非空原始 `asset_id` 在当前粗剪快照、章节 `storyboard[0]` 与完整分镜素材快照中各自唯一，并核对章节和剧集归属；系统不按草稿序号、`frame_index`、标题或文本猜测。`included`、`pending`、视频可用性和媒体摘要不改变定位资格。

只有用户点击才聚焦并滚动到当前章节镜头文章，粗剪面板保持打开；定位不播放或读取视频。打开粗剪仍只进行既有粗剪 GET，定位和重复定位不增加 GET。素材快照未就绪时粗剪仍可读，定位暂不可用；现有原图图片展示保持原样。显式重读、关闭、切章或会话变化会使旧入口和反馈失效，镜头失焦清除定位反馈。

实现与分层验收证据见 `openspec/changes/migrate-readonly-rough-cut-frame-navigation/verification.md`。

## 检查

```sh
cd /Users/yanghaibo/data/projects/ai/haoai-next
npm --prefix frontend run typecheck
npm --prefix frontend run test
npm --prefix frontend run build
npm --prefix frontend run verify:source
node --test scripts/source-baseline-contract.test.mjs
openspec validate migrate-react-shell-series-list --strict
openspec validate migrate-readonly-chapters-storyboards --strict
openspec validate migrate-readonly-asset-library --strict
openspec validate migrate-readonly-personal-production-notes --strict
openspec validate migrate-readonly-my-tasks --strict
openspec validate migrate-readonly-team-series-filter --strict
openspec validate migrate-readonly-personal-rough-cut --strict
openspec validate migrate-readonly-resume-navigation --strict
openspec validate migrate-readonly-frame-asset-references --strict
openspec validate migrate-readonly-asset-frame-usage --strict
openspec validate migrate-readonly-asset-usage-chapter-navigation --strict
openspec validate migrate-readonly-asset-usage-frame-navigation --strict
openspec validate migrate-readonly-frame-reference-asset-navigation --strict
openspec validate migrate-readonly-rough-cut-frame-navigation --strict
```

剧集、团队目录、章节/分镜、角色/场景/道具、个人记录、粗剪和任务列表契约测试对照冻结源码字段，并通过独立 loopback HTTP fixture 执行真实 fetch；它不会导入旧后端、连接数据库或启动 Worker。`legacy-reference/` 仅含原批准白名单源文件和受限剧集路由摘录，作为可校验的来源证据，不是可运行的后端副本。真实 FastAPI、PostgreSQL、权限/锁并发及生产媒体链路不属于本地 fixture 验收。素材浏览只发当前类型列表 GET，保留服务器响应顺序；规范名、音频地址等 wire 字段用于兼容校验，不向页面暴露。团队目录仅在剧集列表可见且进入团队分类时读取，成功快照仅保留在当前用户和服务上下文内存；它用于本地筛选，不代替后续接口的访问判断。

个人制作记录仅在当前章节显式打开后请求，按稳定镜头身份和当前媒体版本核对；本地 SHA-256 只处理 URL 身份字符串，不请求媒体。旧个人记录 GET 可能维护同章媒体元数据或认可状态；粗剪 GET 自身读取并投影当前章节草稿，不维护媒体版本或认可状态。通用认证链路仍可能维护会话记录，因此这里不把整个 HTTP 请求描述为数据库纯读取。自动 HTTP 与浏览器演练仅使用上述隔离 fixture，不启动真实后端、数据库、Worker 或 R2。任务页也只在显式进入后请求当前页：每页 10 条，保留服务端顺序；仅展示状态、进度、时间、名称和记录积分，不展示完整请求或结果媒体，也没有任务详情、重试、取消或写入入口。


## 从个人制作记录定位镜头

当前章节的“我的制作记录”在读取与媒体核对完成后，只为当前记录中可识别、已核对的实际分镜行提供“定位到记录镜头 N”；已认可、待修、待重新确认和未标记状态均可定位。系统不因打开面板或读取完成自动跳转，也不新增未保存行列表。定位继续保留已有“定位到续作镜头”行为。

定位身份来自同一份 ready 记录行及当前章节 storyboard[0] 原始 ID，并要求该 ID 在章节引用和完整分镜素材快照中唯一且属于当前章节/剧集；标题、位置或文本不作为替代身份。工作台再次核对用户、服务、章节/素材快照、读取世代与实际 DOM。只有目标镜头文章真实获得焦点并完成滚动后才显示成功反馈；失败显示未定位说明，镜头失焦（包括移入镜头内按钮）清除该反馈。记录面板保持打开，重复定位不增加 GET。

此能力复用既有个人记录 GET 与安全原图展示，不增加 API、DTO、service、fixture 路由、主动媒体读取或写入；也不改变原始 demo/fixture 样本。个人记录 GET 的既有认证和维护副作用仍按兼容边界处理，不能据此声称真实数据库纯读取。分层验收见 [第十五切片 verification](openspec/changes/migrate-readonly-production-note-frame-navigation/verification.md)。

当前 change 的 OpenSpec strict 命令：

```sh
openspec validate migrate-readonly-production-note-frame-navigation --strict
```


## 按状态筛选个人制作记录

当前章节的“我的制作记录”在记录读取与核对完成后，提供“全部”与六种状态选项及对应计数。计数基于完整的当前读取快照；筛选只改变可见记录，保留原顺序和行身份。没有匹配项时可恢复“全部”。警告、孤立记录和续作位置不随筛选隐藏。

筛选是本地展示操作，不触发额外 GET、重新登记读取或自动定位。过滤后仍可对同一份已核对记录行进行定位；定位失焦时只清除定位成功提示，不关闭或重读面板。显式重读仍使用既有个人记录 GET，并使旧筛选回调失效。

本切片不新增 API、DTO、service、fixture 路由或媒体读取；个人记录 GET 的既有认证与维护副作用仍适用。分层证据见 [第十六切片 verification](openspec/changes/migrate-readonly-production-note-status-filter/verification.md)。

当前 change 的 OpenSpec strict 命令：

openspec validate migrate-readonly-production-note-status-filter --strict

## 按状态筛选当前粗剪草稿

当前章节的粗剪面板在 ready 且非空的读取投影上显示“全部、已纳入、已排除、待安排”四个本地筛选和完整快照计数；未保存的非空初始投影也可筛选。待安排单独计数，可与纳入或排除重叠。筛选保留原草稿顺序、原行及章节镜头位置；零命中时说明只是当前投影没有符合条目，并可恢复全部。

筛选状态只属于当前读取快照和世代。显式重读或重新打开后默认显示全部，旧回调不能更改新读取；普通素材目录请求完成不会重置筛选。筛选不增加请求、不自动定位、不写入或加载媒体；鼠标或键盘切换使镜头失焦时只清除原定位反馈，粗剪面板保持打开。真实读取和认证边界见下方检查及[第十七切片 verification](openspec/changes/migrate-readonly-rough-cut-status-filter/verification.md)。

```sh
openspec validate migrate-readonly-rough-cut-status-filter --strict
```

## 第十八切片：素材库本地搜索

素材库角色、场景和道具当前分类支持本地搜索。角色和道具匹配名称，场景匹配标题；三类都可匹配页面已显示的非空别名。查询会去除首尾空格并忽略大小写，按字面子串匹配。不搜索描述、人物特征、未命名占位文案、规范字段或内部 ID。命中数相对于当前已读取分类快照；无命中时可清空搜索恢复全部条目。

过滤保留完整分类的原顺序、卡片节点与身份核验数据，只隐藏未命中的卡片。查询不新增目录或章节业务请求；图片仍按既有安全显示链路按需加载，PNG 实际增量单列记录。改变查询会关闭旧的关联镜头面板并清除素材导航反馈。更换分类、重新读取、用户或 services/series 作用域变化及新导航会按各自流程重置查询；重复点击当前分类不触发重新读取。

本切片不新增 API、DTO、service、fixture 路由、媒体节点或持久化。自动化、浏览器与 API 请求计数见 [第十八切片 verification](openspec/changes/migrate-readonly-asset-library-search/verification.md)。


## 第十九切片：个人制作记录显式编辑

当前章节中，创作者可以显式编辑一个已核对的个人制作记录：选择记录状态并填写最多 2000 个 Unicode 码点的备注，再单独保存。没有个人记录时可以从 revision 0 创建；认可状态仍须满足当前媒体与摘要核对条件，待重新确认的记录需重新选择状态。打开面板、编辑或切换状态都不会自动保存。

保存沿用当前登录身份，只更新指定镜头，并同时比较个人记录 revision 与媒体 revision。冲突、权限拒绝或结构校验失败会保留草稿并要求显式重新读取；结果未知时不自动重复写入，需显式读取确认后再继续。真实浏览器中保存动作的审计增量为一次 PUT、一次 OPTIONS、零次个人记录 GET 和零次 PNG 请求；这些是单次操作增量，不代表页面整体没有既有章节图片请求。

新建 demo 与隔离 API fixture 使用内存状态，不能据此声称刷新后持久化。既有个人记录 GET/PUT 及通用认证仍沿用旧维护链路，可能维护章节媒体元数据或撤销同章其他用户的失效认可；本地 fixture 不验证真实数据库副作用。分层验收见[第十九切片 verification](openspec/changes/migrate-personal-production-note-edit/verification.md)。

```sh
openspec validate migrate-personal-production-note-edit --strict
```


## 第二十切片：设置个人续作位置

“我的制作记录”现在可显式设置或清除本人续作位置。设置使用当前章节中唯一、可核对的原始分镜 ID，不要求便签存在、媒体摘要相同或记录已认可；清除现有位置也不要求旧目标仍可定位。设置标记本身不代表认可，也不会触发自动跳转。

保存复用已有个人制作记录 PUT，正文只包含 `expected_revision` 与 `resume_frame_id`，其中 `null` 表示清除。备注和续作设置共用本人章节 revision 与写入门禁；成功后采用服务端返回的完整快照。冲突、拒绝或结果未知会保留待处理意图，必须由用户显式重新读取后再选择，不自动重发或读取。demo 状态保存在当前 services 实例内存中，不代表页面重载后持久化。

既有 GET/PUT 仍可能维护媒体元数据或撤销失效认可，不应描述为数据库纯读取；本切片不新增端点、媒体 CAS、播放或真实后端能力。分层证据见[第二十切片 verification](openspec/changes/migrate-personal-production-resume-edit/verification.md)。

```sh
openspec validate migrate-personal-production-resume-edit --strict
```

## 第二十一切片：个人粗剪编排保存

本切片为章节粗剪草稿增加显式编排与保存。创作者可在“我的粗剪草稿”中编辑当前章节的镜头纳入状态和顺序：纳入与排除分组各自展示，组内移动时与原列表中最近的同纳入状态镜头交换位置，切换纳入状态不改变镜头原位置。取消保留当前面板内的草稿；关闭面板或离开当前章节会丢弃本地草稿。筛选与章节导航仍使用完整章节数据，不改变身份核对。

首次保存尚未保存的当前快照（包括空章节），或创作者显式提交有效变更，或对待确认、已移除镜头完成全量核对时，才执行保存。请求使用现有 PUT /api/chapters/{chapter_id}/rough-cut，不新增端点，正文仅含预期 revision 和有序的镜头 ID/纳入状态。响应必须匹配当前章节、revision 加一、保存状态和提交的 ID/顺序/纳入状态；服务端可维护镜头位置、文本、预览与缺失原因等非身份字段。未变化的已保存内容不重复写入。

保存进行中或结果无法确认时，界面保留提交意图供只读查看；不会自动重试、重读或重排。创作者须显式重新读取粗剪，成功读取后才能解除门禁。粗剪保存不改写个人制作记录、认可状态或媒体元数据，也不新增媒体接口、媒体节点或主动媒体读取；既有安全媒体展示仍按原链路工作。

本地演示与验收 fixture 使用内存状态，不能代表持久化后端或生产数据。严格校验命令：openspec validate migrate-personal-rough-cut-edit --strict。

## 第二十二切片：个人粗剪后端模块化

第22批在隔离的 `backend/` 包中实现个人粗剪的第一个真实 FastAPI/SQLAlchemy 后端模块。它采用领域规则、应用用例、工作单元端口、SQLAlchemy Core 持久化、HTTP adapter 和显式 app factory 的分层结构；当前范围仅覆盖粗剪读取与保存，不接入旧站点的完整后端入口。

应用工厂要求显式提供 Session factory、可信身份解析器和同一 Session 上的剧集访问策略。缺任一端口时服务返回 503，不创建 Session，也不默认放行。新应用只暴露 `GET`、`PUT /api/chapters/{chapter_id}/rough-cut`；OpenAPI、Swagger 和 ReDoc 均关闭。

当前 SQL 表仅为该能力定义最小投影，SQLite 测试库和验收实验使用临时文件，不是生产 schema 或数据库迁移。生产源码不创建 Engine 或 schema；测试和验收实验只在自有临时 SQLite 库中创建 Engine 与最小 schema。真实 JWT、成员与团队权限、全站入口、其他业务模块、PostgreSQL 运行时、Worker、队列、媒体服务和生产部署仍未迁移或验收。SQLite 的 CAS、唯一约束与两进程重启持久化验证不能替代真实 PostgreSQL 锁和生产认证验证。

实现边界、可复现命令与分层验证见 [后端模块边界说明](docs/architecture/backend-module-boundaries.md) 和 [本变更 verification](openspec/changes/modularize-backend-personal-rough-cut/verification.md)。依赖要求见 `backend/pyproject.toml`；本地验证使用 Python 3.12 与其中固定版本的 FastAPI、Pydantic、SQLAlchemy、httpx 和 pytest，不需要导入旧 app。

在已装好这些依赖的 Python 3.12 环境中，可运行：

```sh
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=backend/src python3.12 -B -m pytest -c backend/pyproject.toml backend/tests -p no:cacheprovider
```

该命令是运行方式说明，不表示本次安装或生产部署。Grillme 独立审查仍待执行，本变更未归档。


## 第二十三切片：后端剧集访问策略

隔离后端新增剧集访问策略模块，并将它显式接入既有个人粗剪 GET/PUT。策略按当前数据库中的剧集作者、团队成员与认领记录判定；没有策略、可信身份解析器或 Session factory 时，应用返回 503，不创建 Session，也不默认放行。新应用仍只暴露粗剪 GET/PUT。

本模块先处理他人认领限制，再判断作者或团队成员资格。认领限制允许团队 owner，或权限列表中包含精确的 enter_claimed_series；通过该关卡不会单独赋予最终访问资格。权限与成员资格在每次请求中按当前记录查询。该规则不包含真实登录、JWT、会员校验或团队管理。

验证使用新后端工厂、合成可信身份和临时 SQLite。它证明此迁移模块与现有粗剪请求的组合行为，不表示旧站点认证、生产数据库或 PostgreSQL 并发撤销已完成。实现边界见 [剧集访问策略说明](docs/architecture/series-access-policy.md)；分层证据见 [本变更 verification](openspec/changes/modularize-backend-series-access/verification.md)。

## 第二十四切片：个人制作记录后端模块化

第24批在隔离的 `backend/` 包中新增完整个人制作记录模块，并将现有粗剪能力与便签能力组合进显式应用工厂。新模块采用纯领域规则、应用用例、工作单元端口、独立 SQLAlchemy Core 持久化和 HTTP adapter；旧粗剪公开 actor 类型保持同一 class 身份，业务错误统一由共享基础类型承载。

模块实现 `GET`、`PUT /api/chapters/{chapter_id}/personal-production-notes`。记录按可信用户和章节隔离，支持严格局部备注/状态补丁、多镜头更新以及同一路径中的续作设置或清除。GET 可在当前事务内维护媒体版本、失效认可并更新私人 revision，因此不是纯读取。双空历史认可需要先独立维护提交；若同一 PUT 触发该修复，服务器会以 409 停止后续用户补丁，客户端必须重读再提交。

媒体维护保留旧稳定身份与摘要规则，包括删除墓碑、同章其他用户的已认可记录撤销、CAS 和事务回滚。新协调入口可由后续源写模块在同一事务复用；旧 ORM 写监听器及其他媒体写入方尚未迁移，不能推断全站写入都已接入。

实现结构见 [个人制作记录后端说明](docs/architecture/production-notes-backend.md)，分层证据见 [本变更 verification](openspec/changes/modularize-backend-production-notes/verification.md)。第24批只覆盖隔离后端这一业务模块，不代表真实 JWT、生产 schema、PostgreSQL 运行锁、其他旧业务、Worker、队列、媒体服务或生产部署已完成。

## 第二十五切片：后端认证模块化

第25批在隔离的 haoai_backend 包中新增认证与邮箱验证码模块，并把真实 JWT 身份接到统一后端工厂已有的四个业务方法。工厂现在注册 11 个认证方法与粗剪、个人制作记录的 4 个 GET/PUT；自动文档路由关闭。配置由调用方显式传入，缺少请求所需端口时返回 503，不默认放行，也不创建 Session。

认证层使用真实 bcrypt 密码校验、签名 JWT 和 SQL 会话记录。它保留注册、登录、我的资料/积分/会话、会话撤销、修改密码、忘记/重置密码、发送/校验邮箱验证码的旧顺序与响应。邮箱验证码和限流器是显式注入的进程内组件；验收使用受控发送器，不连接 SMTP。

四个已有业务方法仍先进行身份维护，再核对当前会员资格并进入原剧集访问策略。显式传入的 resolve_actor 仍优先；启用认证运行时后，缺省身份由真实 JWT 解析。认证 Session 与业务 Session 分开，已提交的登录会话维护不会被后续业务拒绝撤销。

实现边界与接口见 [认证后端架构](docs/architecture/authentication-backend.md)，兼容规则见 [模块 API 兼容说明](docs/architecture/module-api-compatibility.md) 的第二十五批一节，分层验收记录见 [本变更 verification](openspec/changes/modularize-backend-authentication/verification.md)。本批使用隔离 SQLite、合成账号和受控邮件发送器；真实 SMTP、生产密钥/数据库、PostgreSQL 并发、部署及其他旧业务仍需独立工作。


## 第二十六批：剧集、章节与分镜来源数据后端模块

本批在隔离后端新增剧集、章节、分镜来源数据模块，迁移列表、详情、创建、更新、排序、删除和素材目录共十二个既有方法。统一应用工厂现在组合十一项认证、四项既有个人制作/粗剪方法和这十二项来源方法。接口与持久化边界见[架构说明](docs/architecture/series-data-backend.md)，逐层验收记录见[本变更 verification](openspec/changes/modularize-backend-series-data/verification.md)。

实现保留可信身份、剧集访问策略、章节锁、聊天索引、素材稳定 ID 和个人媒体认可之间的原有关系。媒体版本和认可只在实际来源变化时协调；多阶段写入保留各自提交边界，后阶段失败不会撤销已提交的前阶段。带账单的任务仍由数据库外键阻止删除，不清除金融历史。

这轮证据来自新后端全量测试、临时 SQLite loopback 和冻结 TypeScript 响应解析器。它不代表真实 PostgreSQL 锁、生产数据库迁移、完整前端套件、浏览器验收或部署已完成；指定的独立 Grillme 审查仍单列待办。


## 第二十七批：角色、场景、道具与分镜素材后端模块

隔离后端新增 `haoai_backend.asset_data`，提供十五个来源素材方法：角色、场景、道具的列表、创建、更新、删除，以及分镜素材的创建、更新、删除。统一工厂登记四十二个方法；本批 loopback 实际调用了十五个新增方法和四个既有个人制作/粗剪 smoke 方法，不能据此称全部四十二个方法都经过 TCP 验证。

本批保留旧字段省略、显式 `null`、别名、命名和错误优先级。更新按首次赋值意图确定来源与派生字段；只有两者均未变化的真正 no-op 才不发来源 DML 或更新时间，显式同值 name 仍可修复过期 canonical/falsey aliases。签名等价不推进已有媒体版本或撤销认可；实际 `image_url` 字段变化仍调用协调器，媒体身份确有变化时才推进版本或撤认，缺失状态可初始化 R1；纯元数据变化不强制初始化。类别 DELETE 清理目标类别引用；分镜素材 DELETE 保留原始章节引用并写墓碑，不自动关联章节或续期锁，协调也可能为其它旧引用初始化缺失状态。每个写请求在同一业务事务中完成来源写入与媒体协调，只提交一次；提交前任一阶段失败整体回滚。提交后刷新失败属于结果未知，不自动重发，调用方应显式读取核实。

根实际后端回归为 308 项通过；隔离 SQLite loopback 覆盖十八组、六十二条 TCP 请求和 467 个 SQL 事件，三十个真实响应体通过四个冻结 TypeScript parser。它们不代表真实 PostgreSQL 并发、完整前端套件、浏览器验收或生产部署。模块边界见 [资产数据后端架构](docs/architecture/asset-data-backend.md)，分层验收见[本批 verification](openspec/changes/modularize-backend-asset-data/verification.md)。指定的独立 Grillme 外审仍是单独待办。


## 第二十八批：聊天数据与 AI 统计后端

隔离后端新增 haoai_backend.chat_data 模块，迁移聊天消息、素材关联消息及章节/剧集 AI 统计共十个方法。统一应用工厂现在登记五十二个方法；本批本机 loopback 实际调用十个新方法和四个既有个人制作/粗剪 smoke 方法，不能据此称五十二个方法都经过 TCP 验证。

模块沿用可信身份、同一业务 Session 内的剧集访问策略和原错误优先级。它只提供既有聊天与统计能力，不改认证方式，也不迁移旧站点完整后端入口。分层说明见[聊天数据后端架构](docs/architecture/chat-data-backend.md)，实际验收与未覆盖边界见[本批 verification](openspec/changes/modularize-backend-chat-data/verification.md)。

根实际全量后端回归为 344 项通过，另有九条既有依赖/弃用警告；loopback 和失败历史单独记录在 verification。SQLite 与本地 HTTP 证据不代表真实 PostgreSQL 并发、生产迁移、完整前端/浏览器验收或部署。指定的 GPT-5.6 Sol/xhigh Grillme 外审仍单独待办。

## 第二十九批：章节画布后端

隔离后端的 `canvas_data` 模块承接章节共享画布的 `GET` 与 `PUT`。首次读取返回完整默认画布但不写入；保存沿用原有版本快照、本人锁续期和错误顺序，不增加数据库版本 CAS 或自动重试。成功提交后同一 UoW 重新读取持久行，响应元数据以刷新结果为准，画布正文回显本次请求。

本批根验收包括 374 项后端测试和本地 SQLite/HTTP 验收；实际范围及 SQL 追踪边界见[画布后端说明](docs/architecture/canvas-data-backend.md)与[验收记录](openspec/changes/modularize-backend-canvas-data/verification.md)。这些证据不代表真实 PostgreSQL 并发、浏览器、生产部署或指定外部评审已完成。


## 第三十批：后端下载链接

隔离后端新增下载链接模块，提供 GET /api/download 与 POST /api/sign-download-urls。默认 resolver 原样返回 URL，不生成签名或承载下载；需要 URL 变换时由组合根显式注入异步实现。路由复用可信身份和活动会员校验，不使用业务 Session 或剧集访问策略。

模块边界与 API 约定见[下载链接后端说明](docs/architecture/download-links-backend.md)，实际验证及未覆盖范围见[本批 verification](openspec/changes/modularize-backend-download-links/verification.md)。
