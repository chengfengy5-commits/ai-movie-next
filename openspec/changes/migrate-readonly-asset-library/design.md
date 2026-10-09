# Design

## Context

动机见 proposal。新库已有 React/TypeScript/Vite、隔离 demo/API 服务、剧集列表及章节只读模块。Workspace 当前以 selectedSeries 驱动章节页，列表保持挂载并用 hidden/inert 保留筛选和展示数量；共享服务已有固定五条业务路由、Bearer、响应头/体 timeout 和请求取消。主 specs 为空，前两 change 各 15/16，唯一未完成项为 Grillme 离线；本 change 不改这些历史产物。

来源仅用旧库 `git show 23403806898550a7668a6ee7c0c457315655c39b:path` 静态核对，不导入/启动/测试旧 API、DB 或 Worker。新库 legacy-reference 的精确五项名单不扩展，完整 schemas/series.py 已含全部素材响应。未发现额外 AGENTS.md。

| 冻结事实 | 精确来源 |
| --- | --- |
| 角色/场景/道具 GET 各自按 series_id 查询 `.all()`，无 order_by、分页或包装对象 | `backend/app/routes/series.py:1278-1286,1382-1390,1467-1475` |
| 三 GET 依赖 require_active_membership，经 ownership helper 调 verify_series_access；可能 404、会员/认领/访问 403 | `routes/series.py:280-282`；`services/team_service.py:124-131,166-185` |
| 父响应 aliases 可缺省为 null 或字符串数组，canonical_key 可缺省为 null；DB aliases 字符串由服务端解析，坏 JSON 已转 [] | `schemas/series.py:101-119` |
| Character 含 name、gender/age/role/appearance/description、image_url/audio_url/voice_ref；Scene 用 title；Prop 用 name；其余 Optional 字段无默认值，为 required nullable | `schemas/series.py:149-166,186-197,217-228` |
| ORM aliases 为 Text，canonical_key 为可空规范名键；仅 before_insert/update 维护，不是 GET 的客户端算法 | `models/series.py:23-34,355-374`；`asset_naming.py:63-131` |
| duplicate_exclusions 只限同剧集同类型素材对；duplicates GET 会传 valid_ids 并触发失效排除记录的 delete/commit；ignored GET 同样可能清理 | `models/series.py:312-341`；`routes/series.py:970-995,999-1023,1128-1139,1153-1170` |
| 旧前端三个列表调用与路径 | `js/api.js:222-223,252-253,282-283` |

## Goals / Non-Goals

**Goals:** 在当前被授权剧集内提供三个按需只读分类，保留完整已知 DTO 与 API 顺序，复用现有隔离通信及上下文清理，完成有意义的自动/HTTP/浏览器验收。

**Non-Goals:** 无全局素材库、搜索、分页协议、编辑/别名维护、canonical 计算、去重/排除查询、合并、上传/提取/生成/融合、锁、音频播放/下载、章节引用名称解析、数据库迁移或新依赖。duplicates 与 ignored 虽为 GET，存在清理写副作用，明确不进入白名单。不新增来源复制、运行旧服务、付费调用或真实队列消费。

## Decisions

### 导航与三分类

SeriesCard 增加“只读查看素材”，既有“只读查看章节”继续独立可用；两入口都受原列表 can_enter 控制，不增加详情 GET。Workspace 用一个 selectedSeries 和局部 view（chapters/assets）区分当前查看模块，避免两个互不一致的剧集状态。不增加路由、深链接或全局素材入口；无需额外章节↔素材导航，返回列表后选择另一个入口即可。sidebar 如需更新，仅说明先选剧集查看素材，不能让无剧集上下文发查询。

素材页显示当前剧集、只读说明、返回入口，以及角色/场景/道具三个按钮。初始 characters；一次仅查询当前类型。列表沿用挂载 hidden/inert 方案；返回卸载素材页，保留四筛选和展示数量。刷新回剧集列表，不持久恢复素材。既有章节查看行为继续回归，不改第二切片历史。

### typed DTO 与服务约定

公共 AssetNamingFields 保留 `aliases: string[] | null`、`canonical_key: string | null`；SeriesAssetFields 保留 `id/series_id/description/image_url/created_at/updated_at`。Character 另有 `name/gender/age/role/appearance/audio_url/voice_ref`；Scene 另有 `title`；Prop 另有 `name`。wire 不含 type，不把 Scene.title 改成 name；UI 可用局部类型判别。

稳定公开方法：`listCharacters(seriesId: string, signal: AbortSignal): Promise<Character[]>`、`listScenes(...): Promise<Scene[]>`、`listProps(...): Promise<Prop[]>`。对应 `parseCharacterList(value, expectedSeriesId)`、`parseSceneList(...)`、`parsePropList(...)` 返回 typed 数组。每个 DTO 严格校验数组/对象、非空身份、同 series、单类型唯一 id、ISO 日期及字段类型。name/title 允许空字符串；required nullable 字段须实际存在且为 string 或 null，缺失/undefined 不可用旧 nullableString 自动归 null，空字符串原值保留。aliases/canonical_key 可缺省为 null；aliases 只接受 null 或 string[]，不二次 JSON parse；字符串数组可含合法空字符串，显示时略过空白项但不改 DTO。不要借此重写前两切片 decoder。

冻结字段对照须包含 AssetNamingResponse 的继承字段，不沿用仅寻找 `class X(BaseModel)` 的旧测试 helper 去漏掉父模型。契约测试同时验证必需可空字段、命名字段缺省及响应坏值；字段名比较仅为静态 schema 证据，不代表运行真实 Pydantic/FastAPI。

### 只读内容与图片

以 response id 渲染稳定卡片，角色展示名称、别名、描述及已有 gender/age/role/appearance，场景展示 title/别名/描述，道具展示 name/别名/描述；空名称用未命名占位，null/空展示信息用明确占位或省略。HTML 经 React 文本显示，不展开原始对象。canonical_key 保留但不显示，不计算归一键或按它合并数据；audio_url/voice_ref 保留但不展示地址、播放或请求。不同类型同 id 合法，显示及图片状态不能串用。

图片复用既有同源 loopback 检查，默认 demo 图片为 null、本地占位，不发送业务/媒体请求；fixture 提供合成图片。远端、缺图及破图保持文字并显示占位，不外链、不换源生成。图片失败状态按 series/type/id 隔离；显式重新读取使当前类型的图片失败状态重置，允许相同 URL 恢复后再加载，可用当前类型的读取版本重建卡片。所有 key 都只为显示隔离，不生成新资产身份。

### 请求、成功缓存与错误

业务白名单为既有 POST auth/login、GET auth/me、GET series、GET chapters、GET storyboard-assets，以及新增 GET `/api/series/{series_id}/characters`、`/scenes`、`/props`，共八条。沿用 encodeURIComponent 单次编码 series 路径段，拒绝空 id 和单独 `.`/`..`；服务方法不允许任意 URL/类型路径注入。明确允许的本地静态/合成图片独立于业务 API 核查。

素材页当前 user/session/series 内按 type 缓存成功解析的数组，合法 [] 也是成功；没有失败缓存。类型切换立即使旧可见数据失效，已成功的 A→B→A 可复用 A；同类型待处理时重复点击不重发。返回、换剧集、退出/新用户或切换到章节模块卸载素材页，清全缓存；重新读取只清当前类型成功快照和破图状态、建立新世代，并仅请求当前类型。

每次请求同时有 AbortSignal 和当前上下文/世代检查，成功、401、失败及缓存写入都先检查；故意忽略 signal 的迟到结果不能填回已失效类型缓存或注销新会话。当前类型加载/成功空/成功有数据/失败独立，切类型失败不显示上个类型。当前有效 401 调用现有 logout；403 区分会员与访问，404、422、5xx、timeout、network、invalid-response 保留会话并提供重新读取/返回。timeout 覆盖 fetch 和 body，复用既有 error 分类，不读取 muse_* 素材缓存。

### 验证与实施分工

主 Luna 负责 contracts/services、demo 素材、素材页/导航及契约/UI 测试；第二 Luna 可只负责 `tools/api-fixture/server.mjs` 和新增 `frontend/src/shared/api/assets-http.test.ts`，依赖上述签名，避免并写同文件。fixture 按 series/type 提供样例，至少有空/null/空名称/缺省父字段、同 id 跨类型、API 顺序反例、别名和合成图，并支持代表性的 HTTP/超时/坏响应/图片失败场景。

验证按风险选择代表例：三 GET 正常/Bearer/路径/DTO/空数组真实 fetch；共享错误分类选覆盖所有类别的代表类型，不机械铺三类型全矩阵。UI 验证按需、响应序、成功缓存、空数据和允许空名称、同 id 跨类型、别名、破图重读同 URL、无音频/远端/写入口；deferred 验证切类型后的迟到成功不缓存、重新读取后迟到 401、返回后不同剧集、退出新用户后旧成功/401，明确 settle 原 Promise 并 await act 后断言。本人/他人持有及团队分享只是列表上下文，不从素材推断所有权或编辑权限。

真实浏览器由根代理分别验收 demo 桌面/移动/键盘及隔离 fixture 图片、错误/重试、返回/退出；自动测试不替代浏览器。真实 FastAPI/PostgreSQL、认证 session 写副作用、生产访问、数据库排序或媒体链路均未运行。Grillme 固定 GPT-5.6 Sol / xhigh，当前 127.0.0.1:3939 curl exit7/HTTP000 离线，单列待审，不启动/配对/降档，不以当前 Sol 复审替代。

### 冻结来源追踪

仅新增文档引用，下列为 frozen 全文 SHA-256；legacy-reference 仍精确四 full + series GET103–174 excerpt 五项，旧唯一 untracked yaml 不读、不改、不复制。

| 冻结文件 | SHA-256 |
| --- | --- |
| backend/app/routes/series.py | db9721e34a3c5f22f81ce7cf4bc5434d9f3661d732ab1e8a79ba2e74852243cf |
| backend/app/schemas/series.py | 78b1aee6e31cc195fb9e9af7401738ed1c2b3a73a9563f196855dd9912e3bfb9 |
| backend/app/models/series.py | 89bcbff8c0e3a075f62af5ab1f8ab8c961a6a2d275dc99c43c1aad7ae126a7ff |
| backend/app/services/team_service.py | 392f0792be876adda6b4b7629af0624a1dfbaef6fbf8555d7cacfd96cc81326f |
| backend/app/asset_naming.py | a14dfb349456c57fb6a5ccb12e8bb080edea0c4d3c6d8685c8b952b517545b48 |
| js/api.js | ac6d3c2e913f91cede06e24cdfa6b100c95021b41f02adbcb594029bd8309b7c |

## Risks / Trade-offs

- [三个 GET 无声明排序] → 只保留本次响应数组，不承诺稳定数据库顺序，不新增 sort。
- [aliases 旧 DB 错 JSON 已被服务端变 []] → 严格核查响应数组，不声称识别了数据库损坏。
- [同 id 跨类型及图片失败] → type 参与卡片/缓存/失败状态隔离，重新读取重置当前类型图片状态。
- [列表权限或缓存会过时] → 新 GET 复验访问，显式重新读取清当前成功快照；缓存只限本次查看上下文。
- [只读字样可能掩盖 GET 写副作用] → 禁 duplicates/ignored 等额外 GET，保留真实认证 session 副作用说明。

## Migration Plan

1. 根代理复读本 change 并确认 apply ready，按稳定方法签名让两 Luna 分文件实施；现有前两 change 历史保持原样。
2. 执行有意义的契约/UI/真实 HTTP、typecheck/build、exact-five 来源和 OpenSpec strict 验证，再独立 Sol 复审与真实浏览器验收，分别记结果。
3. 更新 README、当前兼容说明及本 change verification；仅本地交付，无提交/推送/部署。回退移除新素材入口/模块，保留章节和列表，不涉及数据库。
