# Design

## Context

动机见 proposal.md；行为契约见 specs/readonly-personal-rough-cut/spec.md。现有 ChapterBrowser 提供章节列表、原图读取和个人制作记录面板；Workspace 保留剧集列表筛选/已展示数量并在导航时卸载章节页。当前 WorkspaceServices 白名单有 11 种业务接口，demo 与 API 会话存储分离。

### Frozen source evidence

旧源码仅通过 `git show 23403806898550a7668a6ee7c0c457315655c39b:<path>` 静态读取，不导入或运行。全文 SHA256 如下；这是来源核对表，不扩充 legacy-reference 的 exact-five 复制名单。

| 冻结来源 | 全文 SHA256 |
| --- | --- |
| backend/app/routes/rough_cut.py | ce8f1668eba0776d748e537b23441a6869406a1e36fbf62e1d59abf20673d642 |
| backend/app/models/rough_cut.py | a48499a709ceb59ac0217fa6f6829497b2b3f4691bdef30a4f146996c999e12b |
| js/rough-cut.js | 9504b3b22615dc640152d1f18661420ea39e82da78299036a12c02475515c5cf |
| backend/app/services/team_service.py | 392f0792be876adda6b4b7629af0624a1dfbaef6fbf8555d7cacfd96cc81326f |
| backend/app/routes/auth.py | 6cae3297fb5806c8668cbd245be974ff42ccf42999265440a6d6e27a7e6d1a78 |
| backend/app/models/series.py | 89bcbff8c0e3a075f62af5ab1f8ab8c961a6a2d275dc99c43c1aad7ae126a7ff |

- rough_cut.py:221–230 的 GET 无 response_model；44–58 使用默认 lock=false 查询章节并复验剧集权限；203–211 只按 chapter_id + 当前 user_id 查个人草稿。model:24–25 保证个人唯一键和正保存版本。
- rough_cut.py:79–130 只认当前章节唯一的 `storyboard[0]`，并要求 StoryboardAsset 属于该章；重复、缺失或跨章 ID 变为无稳定 ID。model/series.py:124–134 是资产关系证据。文本与位置来自当前章节，视频引用仅按服务端扩展名规则筛选；不访问媒体。
- rough_cut.py:138–153 无草稿时返回 revision=0/saved=false 的初始投影，不创建行。155–200 对已保存顺序做内存合并：保留有效旧 ID 顺序，追加新项 pending=true/included=false；多个空 ID 合法，失效旧 ID 可重复，因为未匹配项未加入 used_ids。
- rough_cut.py:269–272 的保存内容只有 asset_id/included，GET 的正文、位置、视频引用均取当前 source；无 media_revision、认可、时长、更新时间或用户 ID 响应字段。500 条限制位于 PUT:252–256，GET 不受此限制。
- team_service.py:166–185 保留认领/团队/个人权限复验；auth.py:112–135 可能维护 session 并 commit，151–174 检查会员。粗剪 GET 自身未见草稿初始化、媒体 revision 维护、他人记录更新、锁或提交，但不能称整个认证请求数据库纯读取。
- js/rough-cut.js:129–163、249–278 是显式读取及 wire 映射；17–20 将可播放另行判定，375–429 区分编排项与原章节位置，469–495 区分未保存/保存和旧引用数量。旧模块含编辑、媒体播放和导出，不复制或执行；其 frame_index 兜底/coercion 不迁入新 decoder。

## Goals / Non-Goals

**Goals:** 在现有章节上下文中提供严格、可关闭、无媒体消费的本人草稿投影；以源码/DTO、真实隔离 HTTP、自动 UI/deferred 和真实浏览器分别验收。

**Non-Goals:** 不迁移 PUT/乐观写并发、重排/纳入编辑、播放器/时间轴、下载/导出、媒体摘要或冻结版本、认可/notes 写、锁、生成、轮询。无真实 FastAPI/PG/Worker/R2/付费调用，不改变后端架构、队列或计费。

## Decisions

### 1. 单一路由与独立 DTO

新增 `getPersonalRoughCut(chapterId: string, signal: AbortSignal): Promise<PersonalRoughCutSnapshot>`，建议类型文件 `shared/api/personalRoughCut.ts`，沿用现有 request/session/error 机制，不增加存储键或私有持久缓存。路径使用现有 ID 校验和 encodeApiPathId 一次编码，防止 dot-segment/分隔符改变路由；没有 query、user_id 参数或请求正文。

| wire 字段 | DTO/校验 |
| --- | --- |
| chapter_id | required own string，非空且与请求精确一致 |
| revision / saved | required own 非负 safe integer / boolean；未保存为 0，已保存为正版本 |
| frames | required own array；不设 500 上限 |
| frame.asset_id | required own string；非空 ID 唯一，空字符串可重复；不限制 UUID/长度，不 trim 改身份 |
| frame.frame_index | required own 非负 safe integer，各项不重复；仅展示原章位置 |
| frame.text | required own string，允许空 |
| frame.preview_url / missing_reason | required own string 或 null；missing/undefined 不是 null；原值不规范化、不解析媒体 |
| frame.included / pending | required own strict boolean；不由 truthy 值或其他字段推断 |
| removed_asset_ids | required own array of nonempty strings；允许重复，保留条数，不展示原值 |

Decoder 不按数组位置补 ID/位置，不忽略坏项后当成功；未知附加字段不作为能力扩展。移除数量直接取数组长度，不能按集合计数。渲染只取所需字段，不输出原始 JSON。GET 无 Pydantic 输出模型，本切片证明静态 helper 对照与客户端/fixture 合同，不声称 FastAPI response_model 或真实数据库回归。

考虑过复用个人制作记录的媒体 digest/镜头关联：粗剪没有该媒体版本契约，wire 已是当前 source 合并投影，因此不引入额外 notes/资产请求或本地摘要。考虑过旧 UI 宽松 coercion：会把坏数据变成有效身份，选择独立严格 DTO。

### 2. 面板显示与身份口径

建议 `features/chapters/PersonalRoughCutPanel.tsx`，在章节 header 后、原分镜列表前显示。入口“查看我的粗剪草稿”，操作仅“重新读取”和“关闭”；加载中仍可重读/关闭。面板展示读取状态、未保存初始投影/已保存当前投影和版本、失效旧引用条数，再按 wire 顺序列正文及状态。

每项“草稿列表第 N 项”由数组展示序号生成，“读取时章节第 M 个镜头”由 frame_index+1 生成；两者不用于稳定匹配、跳转或补造 ID。空正文显示“未提供正文”。included 分别显示“已纳入/已排除”，pending=true 另标“待安排”；missing_reason 按原纯文本呈现。说明“正文和视频引用来自读取时的章节；纳入状态不代表可播放、导出或已认可”。视频引用和素材 ID 留在内部 DTO，不作为 DOM 属性、正文、链接、日志或媒体 src；React 内部 key 不构成业务身份或可见属性。

保持列表顺序可核对，比沿旧 UI 拆成两个分组更适合只读核对。合法空 frames 独立显示“当前投影没有镜头”，仍保留 saved/revision 和失效旧引用提示。

### 3. 生命周期与首帧隔离

ChapterBrowser 保存显式打开上下文：userId、services 实例、seriesId、选中 chapter 对象引用及打开世代。当前上下文不匹配即卸载旧面板并关闭本次打开世代；不能只靠 useEffect 事后清理，也不能把新 props 自动当作用户已经打开而发新 GET。openContext 必须同时保存 services 和 chapter 引用；新上下文仅在用户重新点击入口后挂载。Panel 直接 rerender 的独立测试同样采用本次显式打开绑定，首帧隐藏旧内容、旧回调失效且不自动重读新 scope。个人制作记录/粗剪面板互斥，打开一方先关闭另一方，不触发其 GET。粗剪不依赖原图成功才能打开，也不为打开追加资产读取。

| 事件 | 粗剪行为 |
| --- | --- |
| 进入章节/默认选择/正常原图完成 | 不打开、不预取粗剪 |
| 显式打开 | 新世代 GET；无前次私有缓存 |
| pending/成功/错误时重新读取 | 立即隐藏前次结果，旧世代失效并 abort，只发一次新 GET |
| 关闭/打开制作记录 | 卸载并失效；后来重开需显式新 GET |
| 切章/章节重新读取/原图重新读取 | 事件开始即关闭并失效，不等新数据；无自动粗剪 GET |
| 返回/任务页/素材页/退出/新登录 | 上下文卸载；旧成功或 401 无效 |
| 直接 rerender 新 user/services/chapter 对象 | 首帧无旧内容/错误/版本，旧回调无权应用；关闭旧面板，新上下文需显式打开 |

面板每次请求持有 AbortController + generation/current guard，结果、错误和 finally 均校验；卸载/重读增代。服务层保持 token/session 世代保护，旧 401 不清新会话。只对有效当前 401 调用 onUnauthorized；会员/普通 403、404、422、500、网络、body timeout、invalid JSON/DTO 分别通过已有错误规范保留会话和显式重读。错误不转为空态，不自动重试。

### 4. 默认 demo 与隔离 HTTP

新增独立 `demoPersonalRoughCut.ts` 合成生成器，在当前 demo 章节/资产静态数据上生成符合冻结 helper 的投影，返回 clone，不修改已有 24 剧集或章节样本，不改单一 demo 登录。代表性保存草稿可将同章有效 ID 顺序反转、排除一项、追加新 pending 项和重复失效引用；无草稿必须是 revision=0/saved=false、有效 ID 默认 included=true/pending=false 的初始投影。不能把 synthetic 同章不存在的非空 ID 伪造为有效 source，也不能把有视频引用当作已验证媒体。

fixture 只增加匹配上述 GET、无 query、当前 Bearer 的路由；两账号同章数据不同，不能由 query/header 的其他用户参数越权。环境变量 + restart 控制正常、unsaved、empty、removed、legacy/pending、401、会员/普通403、404、422、500、invalid JSON/DTO、响应/body delay。不要新增控制 HTTP 路由。日志只记方法、允许路径、状态和统计，不记 Authorization、请求/响应正文、视频引用或原始素材 ID；无需任何外部 fetch/写队列/真实媒体。

业务白名单完整为：POST /api/auth/login；GET /api/auth/me、/api/series、/api/teams/my、/api/series/{id}/chapters、/api/series/{id}/storyboard-assets?chapter_id=、/api/series/{id}/characters、/api/series/{id}/scenes、/api/series/{id}/props、/api/chapters/{id}/personal-production-notes、/api/chat/tasks/list?page=&page_size=10、以及新增 /api/chapters/{id}/rough-cut，共 12 种。既有本地合成图片设施不增加业务路由，粗剪从不请求其 preview_url。

### 5. 验证重点与证据分层

- DTO/服务：own fields、null/缺失区分、章节精确匹配、空 ID 重复和 removed 重复、非空 ID/位置重复拒绝、HTML 纯文本、合法 >500 GET；默认 demo 无网络、无写入/媒体请求。
- 真实隔离 HTTP：两账号隔离、路径编码一次/no query、正常/初始投影/空/removed/legacy/pending、代表性权限与错误、真实坏 JSON 和 body timeout；不以直接抛 ApiError 假装 HTTP。
- 自动 UI：wire 顺序与原位置不同、纳入不等于视频可用、空/错误/重读、raw URL/ID 不落 DOM、互斥面板、所有关闭事件。Deferred 用忽略 abort 的独立 Promise，实际 resolve/reject 原 Promise 并 await act/flush，代表性覆盖刷新旧 success/401、关闭重开、切章/章节或原图重读、返回换剧集及退出新用户；只检查 signal.aborted 不算迟到保护证明。直接 rerender 独立覆盖 user/services/chapter 对象的首帧隐私。
- 真实浏览器：demo 与隔离 API 分别记录，桌面/390px/键盘、显式 0-prefetch、正常/empty/代表性错误重读、返回保留筛选与展示数、退出和现六切片回归；API 日志核对恰只允许业务和已有合成图片，无粗剪媒体访问。browser abort 不保证迟到响应，迟到由 deferred 层实证。
- 集成：typecheck/test/build、fixture syntax、来源 exact-five/hash/敏感模式检查、实际文本检查和 strict；初始仓库未跟踪，git diff --check 不冒称覆盖全部文件。固定 GPT-5.6 Sol/xhigh Grillme 离线仅保留未完成项，不启动/替代/降档、不阻断已授权本地开发。

## Risks / Trade-offs

- [当前章节可能在读取间变化] → 使用“读取时章节位置/当前投影”口径，不与本地旧 chapter 按索引匹配，不声称媒体被冻结；章节或原图重读立即关闭。
- [取消请求仍可晚到/私有数据跨上下文] → 首帧 scope guard + 每次请求世代与服务 session guard，真正 settle/flush 的反例验证。
- [GET 非结构化输出/遗留无效草稿] → 对照冻结 helper 校验 required own/type；允许 source 明确合法的重复空 ID、removed 重复及 GET 大列表，错误不伪装为空。
- [不播放使用户无法验证实际视频] → 明确只读记录口径；本切片无媒体质量/可播放性验收。

## Migration Plan

先完成 DTO/service/demo 与 fixture HTTP，再接显式面板及上下文失效；自动与 root 真实浏览器独立验收后更新本 change 验证及当前 README/兼容说明。只写 haoai-next，不修改前六 change 的 36 份历史文件，不扩充 exact-five。出现回归可移除新增入口/路由映射与独立模块回退此切片，不做旧后端数据迁移、提交、推送或部署。本轮已有计划后实施授权，规划 ready 后交 root 验核并由 Luna 实施。
