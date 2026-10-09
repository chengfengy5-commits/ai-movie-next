# Spec Delta

## Purpose

为个人创作者建立可独立启动和验收的新工作台入口，在不连接真实生产服务、不改变剧集所有权及生产账务规则的前提下，提供兼容现有接口的登录、会话恢复和只读剧集列表，支持逐步替换旧前端。

## ADDED Requirements

### Requirement: 默认隔离运行并明确切换模式
工作台 SHALL 默认使用本地隔离 mock，不发送网络请求，不复用旧版会话键。显式 API 模式 SHALL 仅访问同源相对 `/api` 或已明确配置的隔离 loopback 服务；非法远端地址 MUST 在请求前拒绝。工作台 MUST NOT 自动探测或回退连接旧服务。

#### Scenario: 默认启动
- **WHEN** 用户在未配置 API 模式时启动工作台
- **THEN** 登录与列表使用本地样例数据，界面标明演示模式，不发送 API、模型、存储、远端图片或队列请求

#### Scenario: 配置远端地址
- **WHEN** API 模式配置非同源且非 loopback 的地址
- **THEN** 工作台显示配置错误，不发送用户名、密码或 Bearer token

### Requirement: 登录保持现有 JSON 与 Bearer 契约
API 模式 SHALL 向 `POST /api/auth/login` 发送 JSON `{username,password}`，使用返回的 `access_token`、`token_type` 和 `user` 建立会话，并以 `Authorization: Bearer <token>` 请求受保护接口。登录失败 SHALL 显示明确错误，不保存失败会话；提交中 SHALL 阻止重复登录请求。真实登录 SHALL 限于隔离后端。

#### Scenario: 登录成功
- **WHEN** 登录返回合法 token 与用户信息
- **THEN** 工作台显示用户和剧集列表，后续请求携带该 token

#### Scenario: 登录失败
- **WHEN** 接口返回 401、422 或网络错误
- **THEN** 工作台保持登录页，显示安全可理解的错误，允许用户再次显式提交

### Requirement: 会话恢复与用户切换不混用数据
工作台 SHALL 在存在 API token 时先调用 `GET /api/auth/me` 验证会话，验证成功后使用服务端当前用户加载列表。401 SHALL 清除该模式的会话及列表并返回登录页；退出或切换用户 SHALL 清除旧用户可见数据，晚到的旧请求 MUST NOT 恢复旧用户列表。Mock 与 API 会话 SHALL 隔离保存。

#### Scenario: 刷新已登录页面
- **WHEN** 页面存在有效 token 并刷新
- **THEN** 先验证当前用户，再展示对应列表，不以缓存用户独立判断权限

#### Scenario: 会话失效或请求晚到
- **WHEN** 会话返回 401，或退出后原列表响应才返回
- **THEN** 页面展示登录状态，不重新填入旧用户数据

### Requirement: 列表遵守现有访问和所有权含义
工作台 SHALL 用 `GET /api/series` 获取全量数组，保留现有响应字段和 API 返回顺序。`user_id` SHALL 表示个人持有人，`team_id` SHALL 表示共享团队，`claimed_by` SHALL 表示制作负责人；共享或认领 MUST NOT 在前端改写持有人。工作台 SHALL 展示名称、作者、更新时间、团队和认领状态，保留 `can_enter` 的服务端判断，不根据管理员身份另行推导权限。

#### Scenario: 本人剧集分享到团队
- **WHEN** 一个列表项的 `user_id` 是当前用户且 `team_id` 非空
- **THEN** 该剧集同时属于“我创建的”和“团队剧集”，持有人仍是当前用户

#### Scenario: 他人已认领
- **WHEN** 列表返回他人认领及 `can_enter=false`
- **THEN** 页面展示制作负责人和受限状态，不提供可绕过认领限制的入口

### Requirement: 四类筛选和逐次展示保持稳定
工作台 SHALL 提供全部、我创建的、团队剧集、我认领的四类筛选，分别按全量、`user_id === 当前用户id`、`team_id` 非空、`claimed_by === 当前用户id` 过滤。过滤 SHALL 保留 API 顺序，每次展示 20 条；加载更多 SHALL 追加后续 20 条，切换筛选 SHALL 重置展示数量。本切片 SHALL 不请求团队目录或展示团队二级筛选。

#### Scenario: 超过二十条
- **WHEN** 当前筛选结果超过 20 条并点击加载更多
- **THEN** 已显示列表保持原顺序，追加下一批，显示剩余数量

#### Scenario: 筛选切换
- **WHEN** 用户加载更多后切换到其他类别
- **THEN** 新类别从前 20 条开始，其无匹配状态不被显示为全量列表错误

### Requirement: 区分空列表与请求错误
工作台 SHALL 区分加载、成功、有效空列表、401、会员相关 403、其他 403、422、5xx、超时、网络错误和无效响应。会员 403 SHALL 保留登录并显示受限原因；其他请求错误 SHALL 提供明确错误及显式只读重试。请求失败 MUST NOT 读取旧版 `muse_series_list` 或伪装成有效空列表；非 401 错误 MUST NOT 自动注销。

#### Scenario: 会员受限
- **WHEN** 列表返回会员相关 403
- **THEN** 页面保留当前用户、显示会员限制，不发起购买、兑换或付款操作

#### Scenario: 服务端错误与空列表
- **WHEN** 列表分别返回 500 或合法空数组
- **THEN** 前者展示请求失败，后者展示没有剧集；两者不混淆

### Requirement: 首切片只读业务边界可验证
工作台 SHALL 只展示登录和只读剧集列表，所有可交互行为 SHALL 不触发剧集创建、修改、删除、分享、认领、详情、章节、生成、账务或队列操作。API 请求 SHALL 仅包含 `POST /api/auth/login`、`GET /api/auth/me` 和 `GET /api/series`；退出只清除本地会话。

#### Scenario: 点击列表及工作台导航
- **WHEN** 用户点击卡片、筛选、加载更多或壳内导航
- **THEN** 只改变本地展示或执行白名单查询，不进入章节、创建任务或消费真实队列

### Requirement: 后续迁移保留个人生产和任务契约
兼容说明 SHALL 记录个人制作记录和粗剪按 `(chapter_id,user_id)` 私有隔离、稳定分镜身份和版本冲突处理，以及任务未知结果、原提交核实、停止观察与主动取消的区别。后续迁移 MUST NOT 自动重提结果未知的付费任务，MUST NOT 将浏览器断连推断为失败或退款。本切片 SHALL 不调用这些后续模块。

#### Scenario: 查阅后续迁移边界
- **WHEN** 开发者准备迁移章节或任务模块
- **THEN** 可以从冻结来源及兼容说明定位个人状态、稳定身份、CAS 与原任务恢复契约；本切片不声称已完成这些功能的回归
