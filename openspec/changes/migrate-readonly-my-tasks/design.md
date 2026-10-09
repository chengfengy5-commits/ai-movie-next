# Design

## Context

动机见 proposal。当前 Workspace 保留挂载的 SeriesPage，以 hidden/inert 隔离章节/素材；服务白名单为九条。主 specs 为空，前四切片历史24份文件不改。冻结来源为 `23403806898550a7668a6ee7c0c457315655c39b`，仅 git show 静态读取，exact-five 不扩展。

| 冻结事实 | 来源（Python 相对 backend/app） |
| --- | --- |
| 私有任务按 user_id 查询，created_at.desc，count/offset/limit，返回分页字典 | routes/chat.py:865-871,942-1007 |
| 批量查询关联名称，没有当前剧集/team访问校验；普通message分支不补chapter_id | routes/chat.py:873-929,963-976 |
| result按状态折叠；request_data最多200字符；历史动态字段直接取JSON | routes/chat.py:933-940,948,977-993 |
| AITask基础列与nullable；状态只是str，不是封闭枚举 | models/series.py:248-286 |
| 会员依赖；认证可插入/更新session并commit | routes/auth.py:112-135,151-172 |
| 旧UI每页10条、响应顺序、状态标签；旧轮询/积分/媒体/请求详情不迁移 | js/modal-manager.js:2515-2553,2604-2718,2735-2786 |
| 单任务回执、完整请求、外部状态是额外接口 | routes/chat.py:739-770,816-862；routes/tasks.py:257-299 |

列表GET没有 response_model；现有 `AITaskResponse` 只含部分通用字段，不能冒充本列表schema。按冻结字典/ORM作字段静态对照，自动回归针对文档化wire及隔离HTTP，不导入或运行旧FastAPI/Pydantic/数据库。helper本身没有显式写、锁或外部调用，认证仍可能写会话。

## Goals / Non-Goals

**Goals:** 显式查看本人任务分页记录，准确表达读取时状态及历史字段，保留剧集列表体验并隔离迟到结果。
**Non-Goals:** 不按剧集过滤/跳章，不迁移详情、完整请求、供应商GET、轮询、媒体、购买或任何任务写操作；不接真实后端、改账务/队列或新增依赖。

## Decisions

### 入口与生命周期

采用独立“我的任务（只读）”页，不嵌入剧集：列表没有series_id且只有当前用户作用域。在认证后的Workspace导航显式进入，章节/素材及个人记录卸载，selectedSeries清除；SeriesPage保持挂载并hidden/inert以保留四筛选/已展示数。任务返回只回剧集列表，不自动恢复旧章/私有面板。任务页仅当前用户挂载，无默认预读；重复点击已选入口不重发，离开卸载并清私人记录，重开第一页新请求，无持久/跨页缓存。

### 单服务与分页

约定 `listMyTasks(page: number, signal: AbortSignal): Promise<MyTaskPage>`。固定GET `/api/chat/tasks/list`，URLSearchParams仅page/page_size=10，page必须正安全整数，current Bearer沿既有abort/timeout/errors，十条业务白名单。无需动态任务ID路径、任意筛选/用户参数。页大小选10沿旧UI，不选20或客户端加载更多/排序。

每次成功替换当前页，按API数组顺序；页内重复非空task.id拒绝为invalid-response，不跨页去重合并（新任务插入可能挪动分页）。total/page须非负/正安全整数，page/size严格回显请求，tasks数组最多10项。count和读取是两次查询，不假定同一时刻：不强制total等于页数据推算，也不将正确空页拒绝。total减少导致本页为空时显示当前页/总数和显式“回到第一页”；不自动纠页、轮询或发额外GET。上一页/下一页按当前成功元数据启用，pending可禁分页但“重新读取”始终可用；刷新保持当前请求页，清旧数据并中止旧请求。

### DTO与历史动态边界

`MyTaskPage={total,page,page_size,tasks:MyTaskRecord[]}`。除条件prompt_id外，下表字段均是冻结dict的必需自有字段：缺失与null区分，不静默补默认；未知额外字段忽略、不展开rawJSON。

| 字段 | wire/投影 |
| --- | --- |
| id | 非空string，作为页内稳定身份；不靠数组索引生成身份 |
| type/message_id/status | 必需string，可为空；type/status保留原值，不转换为封闭enum |
| result/request_data/progress_message | 必需string或null，保留空字符串 |
| credit_cost/progress | 必需有符号安全整数；不拒绝合法负数，也不按原模型注释强制0..100 |
| created_at/updated_at | 必需合法ISO时间字符串或null，允许冻结来源的无时区ISO；未提供时间独立占位，不由时间/耗时判断任务失败 |
| asset_type/asset_id/asset_name/chapter_title | 必需string或null，不据关联身份授予剧集权限 |
| chapter_id/frame_count | 必需unknown：旧JSON可给非预期类型，不因此拒绝整页；不用于URL或导航 |
| frame_index | 必需整数或null，保留负/零；仅正安全整数显示旧已+1的位置，不再+1，不替代stable身份 |
| frame_text | 必需string或null；冻结helper始终给null，仅保留不展示 |
| prompt_id | 条件可缺省unknown，ai-review解析成功才赋值；不要求所有ai-review必有，不调用提示词接口 |

动态chapter_id/frame_count/prompt_id不以模板预期类型拒绝历史页；frame_count仅非负安全整数可展示帧数，其他值忽略/提示未提供，prompt_id不展示。request_data是最多200个Python字符的片段，可能不是完整JSON，保留但不解析/显示，不套JS UTF-16长度200限制。result失败文本至多5000个Python字符；非失败原值若超过500或以data:开头会被折叠为空。客户端不再按UTF-16长度误拒Unicode历史值，不把空result当无任务或失败，不恢复完整内容。

### UI口径

卡片最少显示类型、读取时记录状态、创建/更新时间、记录进度/说明及可用章节/资产名称和镜头位置。known状态严格区分queued/processing/cancelling/completed/failed/cancelled；未知或空状态为“状态未识别”，不采用旧UI的processing兜底。类型用旧可读映射，未知类型作为纯文本；没有type时为未识别类型。

进度仅0..100合法整数展示为“记录进度”，坏值不画百分比，不凭100/时间超限推断结束。credit_cost只称“记录积分”，不称实际扣费、余额或退款；列表没有billing/recovery/model_name，不推导补齐。failed result仅纯文本记录说明（不解释为客户端查询错误）；其他result只作有/无结果记录提示，并说明列表不提供完整结果。不添加链接、媒体元素、复制/下载、详情请求，HTML/URL只作文字，不执行。关联名称/位置只是旧记录描述，无章节进入权限或当前分镜映射承诺。

冻结来源的datetime.utcnow().isoformat()可省略时区：严格解析后按UTC解释，再以用户本地时区显示；含Z/明确偏移的ISO按其时区解释。禁止直接new Date(naive)默认本地造成偏移。时间测试覆盖无时区UTC与显式偏移得到相同瞬间，例如2026-10-05T00:00:00在Asia/Shanghai显示08:00，null独立占位。

### 错误与迟到响应

页/请求世代同时受user/挂载/abort保护。换页、pending刷新、返回重开、退出新登录后，旧成功、错误或401不能填回界面/私人缓存或调用当前注销。当前有效401注销；会员/访问403、404/422/5xx、header/body timeout、network、invalid-response只显示查询错误，保留会话与显式GET重试。查询故障不改task.status、不当失败，更不付费重提。代表性deferred实际settle原Promise并await act，检查新页/账号文字、token及logout次数。

### 演示与验收

默认demo不fetch业务/媒体。隔离fixture提供两个合成账号、至少两页任务、空页/总数缩小、已知/未知状态、负记录整数、动态旧JSON及HTML/URL/Unicode说明；Bearer决定所有者。禁止启动旧应用、队列、Worker/R2/外部供应商，fixture不伪造真实权限或账务回归。自动分字段/投影、UI接线、真实HTTP、deferred；root另做真实浏览器demo/API、键盘/移动与网络白名单。Grillme固定GPT-5.6 Sol/xhigh离线独列，不替代/启动/降档。

### 冻结来源全文 SHA-256

| 文件 | hash |
| --- | --- |
| backend/app/routes/chat.py | ed8e979e1ad7f4117256e537f60f7d3f1cf719162b6e254838b2f5957b48357a |
| backend/app/models/series.py | 89bcbff8c0e3a075f62af5ab1f8ab8c961a6a2d275dc99c43c1aad7ae126a7ff |
| backend/app/routes/auth.py | 6cae3297fb5806c8668cbd245be974ff42ccf42999265440a6d6e27a7e6d1a78 |
| js/modal-manager.js | fa063a7acb5a8867ae6e782958055586a3c0a2681cf4a436a15458314700f462 |
| backend/app/routes/tasks.py | 039ea57937e1e214ac3747b4c0a5cdad87c7ca1de971d5e3370a9091b8a433d1 |

## Risks / Trade-offs

- [历史JSON字段类型不稳定] → 严格基础字段、动态值保守投影；不解析截断请求、不误拒合法旧记录。
- [列表无剧集/账务完备上下文] → 独立本人页，只读名称/记录积分，不授予跳章或结算语义。
- [分页总数可能并发变动] → 正确空页可展示，用户显式回第一页，不自动GET或跨页缓存合并。
- [迟到401与未知结果] → 请求世代阻止旧结果；查询错误独立于任务状态，无付费重提。

## Migration Plan

root复读四份规划并确认apply ready后派现有Luna实施，不增代理。通过自动/HTTP/browser及Sol只读复审后，更新当前README/兼容说明和本change verification；前四history24份、旧tracked/untracked及exact-five不变。回撤仅移除任务入口/页及第十接口，不涉及数据库；无commit/push/deploy，Grillme保持独立待办。
