# Design

## Context

动机见 proposal.md。旧库冻结于 `23403806898550a7668a6ee7c0c457315655c39b`，来源均通过该提交的 `git show` 阅读。旧列表路由返回全量数组，按 `updated_at.desc()` 排序；前端以个人持有人、共享团队和认领人筛选。认证 GET 会更新会话，登录最多保留三个活跃设备，因此业务只读列表不能用于真实生产会话的无副作用探测。

新库主规格为空，采用 OpenSpec `spec-driven`，本轮已明确授权先规划再实现。Go/Gin 的 bootstrap 占位方向已撤回；后续后端继续保留 Python + FastAPI、PostgreSQL、R2 与独立 Worker，使用模块化单体。首切片没有数据库、Worker 或真实服务运行要求。

## Goals / Non-Goals

**Goals:**

- 清晰划分壳、认证、剧集、类型和网络适配，便于后续逐模块迁移。
- 同一 UI 可由隔离 mock 或显式 HTTP 适配驱动，验证真实 fetch 路径而不触碰旧服务。
- 用户数据、错误状态、调用范围及来源均能独立审查和验收。

**Non-Goals:**

- 不复制旧运行配置，不启动旧 API/数据库，不执行迁移、模型调用、队列消费、推送或部署。
- 不引入 Go、Redis、微服务、新队列，不迁移章节/素材/个人辅助/任务的实现。
- 本切片不新增团队目录、团队二级筛选或卡片进入制作入口。

## Decisions

### 前端工程与模块边界

用 React + TypeScript + Vite 建立 `frontend/`，优先少量 cohesive 模块：工作台壳、认证状态、剧集列表、共享契约类型、mock/HTTP 适配。页面组件不自行拼接 URL、不读取旧脚本全局对象。采用本地现成样式及占位图，不引入远端字体/封面/头像资源。相比整页移植旧原生脚本，明确适配层能隔离付费生成、缓存和 UI 状态；相比同时重写后端，本切片更容易核对现有契约。

### 运行模式与会话

默认 mock，仅操作内存和独立 `haoai_next_mock_*` 本地键；失败不触发 API 回退。显式 API 模式使用同源 `/api`，或验证为 loopback 且明确配置的隔离 fixture 地址；拒绝非同源远端 URL、URL 用户凭据及非 HTTP(S) scheme。绝不复制旧 `config.runtime.js` 或设置指向旧服务的 Vite proxy。

API token 使用兼容的 `muse_auth_token`；用户缓存即使存在也必须经过 `/api/auth/me` 核实，不能独立授权。退出仅清除当前模式相关认证和内存列表，不批量清空其他应用本地数据。登录和恢复状态显式区分，使用请求上下文/取消机制抑制退出及用户切换后的晚到响应。HTTP 适配不记录密码、token 或完整敏感响应。

### 只读列表与字段

类型依据旧 `SeriesResponse` 保留 `id/user_id/name/description/image_url/style_prompt_id/style_prompt/style_prompt_name/style_prompt_owner_name/team_id/team_name/owner_name/claimed_by/claimed_by_username/claimed_by_avatar_url/can_enter/created_at/updated_at`。UI仅消费列表需要的字段，不渲染提示词内容或原始 JSON。列表原样保留 API 顺序，不增加 `created_at/id` 排序规则；四筛选后再 slice，初始 20 条，每次追加 20 条。

“我创建的”包含本人分享到团队的剧集。`can_enter` 仅展示后端结论，所有卡片均无章节/详情导航。团队二级筛选依赖旧版 `/api/teams/my`，本切片明确延期，避免扩大三条请求白名单。日期兼容旧库无时区 ISO 字符串为 UTC 的规则；无图、破图、缺失可选作者/团队名均使用本地占位，缺失稳定 `id` 或关键类型时拒绝不合法列表响应。

### 错误处理

HTTP 错误统一保留 status 和安全 `detail`，支持 FastAPI 字符串及验证错误数组，空或无效错误 body 用可理解默认文案。仅认证 401 清会话；403 会员提示保留登录，其他 403 为权限错误。网络、超时、5xx 和解析失败保留错误状态，不读取旧缓存，不显示成空列表。重试由用户显式触发，只重发原只读 GET；登录提交中禁用重复提交，不自动重试 POST。

### 来源与验证

复制采用逐文件冻结 blob 白名单、SHA-256 manifest 和敏感内容检查；任何来源复制只能放入不参与运行的参考目录。首切片白名单为四个完整参考文件 `backend/app/schemas/auth.py`、`backend/app/schemas/series.py`、`backend/app/routes/auth.py`、`js/auth.js`，另加 `backend/app/routes/series.py` 第 103–174 行 GET 列表摘录；共五个参考产物。摘录须记录原文件 blob、来源范围、原文件 hash 和摘录 hash。其他路由、模型和大型脚本只记录 SHA 与精确行号，后续扩展须在独立切片明确白名单。tracked 干净不等于来源没有秘密。

验证分四层：来源静态核对；本地 mock UI 自动化；HTTP fixture 驱动 API adapter/契约自动化；真实浏览器操作 mock/隔离 HTTP fixture。HTTP fixture 可验证请求 JSON、Bearer、状态码和展示流，不能证明真实 FastAPI、PostgreSQL 查询、会话撤销或生产权限已运行。真实 FastAPI/PG 回归和后续模块验收另列未执行。Grillme 仅允许 GPT-5.6 Sol / xhigh；本机端口连接失败，记录离线未完成，与正常本地开发并行，不作为自动降档或启动配对的理由。

## Risks / Trade-offs

- [真实认证存在写副作用] → 默认 mock；API 测试只连新建隔离 fixture，真实登录另限隔离后端。
- [静态接口契约无法证明真实后端行为] → 验证报告按层注明实际执行与未执行范围。
- [沿用全量列表的规模限制] → 保留现有全量接口和前端 20 条展示；后续服务端分页需独立规格。
- [接口包含远端媒体地址] → mock 使用本地占位；HTTP fixture 只给本地媒体，验收禁止真实资源请求。
- [Grillme 离线] → 保留待办和固定模型记录，不宣称审查通过，之后可独立补审。

## Migration Plan

1. 完成并核查本 change 规划产物、来源与兼容说明；只修改新库。
2. 固定 Luna 6 / xhigh 实现前端、来源追踪及隔离 fixture 验证；分析和设计使用 GPT-6.1 Sol / xhigh。
3. 执行本地构建和契约/UI 检查，再以真实浏览器验收。写入实际结果，保留未执行的后端/Grillme项。
4. 本轮只交付本地可审查结果，不推送或部署；旧库作为回退入口保留且不修改。下一切片另建 OpenSpec change。
