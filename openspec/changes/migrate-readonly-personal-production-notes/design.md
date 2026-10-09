# Design

## Context

动机见 proposal。现有 ChapterBrowser 按当前章加载分镜和原图列表，可复用选中章节及其成功资产快照；当前业务白名单八条。前三 change 保持历史验收，主 specs 为空。此片仅增加个人记录浏览，不扩展后端。来源固定 `23403806898550a7668a6ee7c0c457315655c39b`，仅 git show 静态读取；legacy-reference 的 exact-five 不变。

| 冻结事实 | 精确来源（Python 相对 backend/app） |
| --- | --- |
| 当前用户身份、章节存在及 verify_series_access；GET 调 snapshot commit=True | routes/personal_production_notes.py:71-79,108-121 |
| 私有唯一键 chapter_id/user_id；个人 revision、frame_notes、resume_frame_id | models/personal_production_notes.py:29-58 |
| 响应由 dict 构造，无 response_model；帧七字段由 dataclass 定义 | routes/personal_production_notes.py:82-101；services/personal_production_state.py:33-63 |
| 当前用户 notes 加锁；无行返回 revision0/空记录/null续作；末尾提交 | services/personal_production_state.py:133-150,181-196 |
| 媒体协调锁 Chapter/Asset/State；初始化、改变摘要/来源、删除失效时维护版本 | services/personal_production_state.py:222-248,273-328,330-392,462-474 |
| 媒体变更撤销同章所有用户的已认可，保留备注/续作，推进个人 revision | services/personal_production_state.py:369-370,585-653 |
| 当前用户历史双空认可独立撤销及提交 | services/personal_production_state.py:74-99,152-180 |
| 唯一同章 storyboard[0]，索引只展示；服务端媒体身份及 SHA256 | personal_production_media.py:62-88,91-163；services/personal_production_state.py:541-544 |
| 旧浏览器签名名单、本地归一及摘要、当前素材核对/认可版本 | js/episode.js:11-17,9425-9489,9640-9737,9978-9995 |

## Goals / Non-Goals

**Goals:** 显式查看本人记录，核对加载时媒体与服务器快照，区分当前状态、需重新确认、旧记录和不可核对，完成隔离自动/HTTP/浏览器验收。

**Non-Goals:** 不 PUT、不编辑/CAS/认可/设续作/定位，不增加粗剪、历史、锁或生成 API；不新增媒体元素、播放、下载、导出、轮询、URL路由、依赖或后端纯读接口。真实 GET 的 DB 维护不在本轮执行或重构。

## Decisions

### 显式面板与上下文

在当前章头部提供“查看我的制作记录”，不另增剧集/全局入口、不预加载notes。独立面板显示当前章、本人只读说明、记录状态/纯文本备注、待重新确认、续作位置文字、旧记录区及关闭/重新读取。摘要只统计当前可核验帧，不用百分比宣称完成制作。无记录用 revision0 提示；empty 与 unreadable 独立显示，后者要求重读章节。

切章、重读章节、原图快照重读/替换、关闭、离开、退出或换用户均卸载并清面板；重新打开新请求，无私有持久缓存。列表挂载/筛选/展示数沿用既有流程。原图尚未成功读取时，记录可读但明确无法核对，不显示当前认可；notes模块不额外发原图请求，仅用 ChapterBrowser 已有结果。面板不展示原图/preview地址、摘要或裸ID。

### 第九条服务与 DTO

公开方法 `getPersonalProductionNotes(chapterId: string, signal: AbortSignal): Promise<PersonalProductionSnapshot>`，仅固定 GET `/api/chapters/{chapter_id}/personal-production-notes`。chapter_id encodeURIComponent一次，空值及单独点段拒绝；使用当前 API token，无 user_id/query、任意URL或 PUT方法。新增endpoint必须在旧series路径构建之前独立处理chapter路径。demo用独立当前用户数据与本地摘要，不调用业务/media fetch；fixture提供合成快照并沿用Bearer。

| DTO 字段 | wire 校验/保留 |
| --- | --- |
| chapter_id | 必需非空字符串，等于请求章节；UI所属series来自现有Chapter，不虚构响应series_id/user_id |
| revision | 必需非负安全整数；0代表尚无个人记录 |
| media_state | 必需 ready/empty/unreadable |
| frames | 必需数组；各frame_index为非负整数且按0起连续位置，ready与当前帧数/位置关系需再核对；empty/unreadable为[] |
| frame.storyboard_asset_id / media_revision | 必需 string或null / 正安全整数或null；source_valid=true仍需稳定ID、有效媒体版本及本地核对 |
| frame.source_valid / invalid_reason | 必需boolean / string或null |
| frame.asset_image_digest / preview_digest | 必需null或64位小写十六进制SHA256字符串，不显示 |
| frame_notes | 必需非数组对象，保留各自有key的unknown值；身份查找必须用自有key/Map，不用原型继承 |
| resume_frame_id | 必需null或非空字符串，不产生定位或写入 |

响应字段来源是 `_response` 和服务dataclass，不能把路由的 PUT Pydantic输入模型冒充 GET响应。五份全文hash及字段表作为源静态核对；自动测试针对已文档化wire/decoder与真实fixture fetch，不执行/import旧模型，不新增source复制，也不声称真实Pydantic/FastAPI回归。

### 旧 JSON 的保守投影

frame_notes不是严格旧响应模型。条目缺省status→unmarked、note→空字符串、approved_media_revision→null、needs_reconfirmation→false；提供的已知字段须分别是已知状态、字符串、正整数或null、boolean。未知status、非对象或坏类型提示“记录不可识别”，不计当前认可，其他可读条目保留；忽略未知附加字段，不显示rawJSON。不将PUT的2000字输入限额当GET历史note上限，不截断或转换非字符串备注。

认可有效必须 loadedMatchesServer、source_valid、至少一个非空媒体摘要、status approved、认可媒体版本等于当前media_revision且无needs_reconfirmation。版本不符或已有重确认标记仅显示待重新确认，不写回；A→B→A摘要相同但revision推进仍不能复活。无保存条目显示未标记，记录异常单列，不用未知状态冒充未标记。orphan仅独立旧记录区显示可识别文字，不混入当前统计；resume仅在同章唯一有效、摘要及位置核对通过时显示分镜序号，否则提示无法对应，始终不导航。

### 稳定身份与异步本地摘要

纯投影先按当前章唯一 `storyboard[0]` 与同series/chapter唯一资产匹配，再按快照唯一非空storyboard_asset_id关联；禁止index兜底、重复覆盖Map或生成身份。frame_index只校验绑定后的当前位置：帧数/绑定身份的位置与当前章结构不符时显式上下文不一致，提示重读章节，不展示错配记录；无稳定身份的行保持不可核对，旧记录独立展示。

新增纯媒体身份归一与异步WebCrypto SHA-256 helper，适配冻结旧浏览器实现，不通过WHATWG URL重序列化全URL。仅为验证括号authority沿旧实现作局部检查：开头剔C0/空格，内部C0/DEL或非法括号authority返回原串；scheme小写，去fragment；query按原顺序/编码保留，仅decode key（+为空格，失败用raw key）再忽略精确临时签名名单。名单固定为：ossaccesskeyid、signature、expires、security-token、awsaccesskeyid、x-oss-signature-version、x-oss-credential、x-oss-date、x-oss-expires、x-oss-security-token、x-oss-signature、x-amz-algorithm、x-amz-credential、x-amz-date、x-amz-expires、x-amz-signedheaders、x-amz-signature、x-amz-security-token、x-amz-content-sha256、x-amz-region-set；不扩展通配过滤。

null/空身份的digest为null；否则TextEncoder→SHA-256→64位小写hex。hash仅处理URL身份字符串，不fetch图片、preview或R2，不声称URL可访问/任务成功。WebCrypto不可用/失败或摘要不符时保留可读旧记录并明确无法核对，不显示当前认可。每次捕获chapter/asset快照和面板/请求世代；HTTP及digest都须在应用结果前确认仍当前，晚完成digest也不能覆盖新章或关闭后面板。

### 维护、错误与验证分层

旧GET不是数据库纯只读：读前协调媒体并维护单调media_revision、tombstone；媒体改变会撤销同章其他用户认可及推进其notes revision，当前用户双空历史认可也可持久撤销。认证session更新仍存在。这是冻结既有行为，不在fixture实现真实DB事务或伪造CAS并发回归。页面只读说明指没有编辑/保存交互；开发默认demo且显式API仍限隔离loopback，不自动连接旧/真实服务。

有效当前401注销；会员/访问403、404、422、5xx、头/体timeout、network、invalid-response保留会话。重新读取pending仍可用，建立新世代并中止旧HTTP；旧成功、失败、401及摘要不得填回界面。代表性deferred须实际settle旧Promise+await act，覆盖刷新、切章/章节重读、关闭重开、返回换series与退出新登录；不能只等已成立状态。

自动验证分源码字段静态、归一/摘要/投影、面板/Workspace接线和隔离HTTP。真实浏览器由root验收demo/API实际操作、键盘/移动、错误/重读与无新增媒体请求；不把jsdom/HTTP代浏览器。Grillme固定GPT-5.6 Sol/xhigh离线，单列未完成，与本地开发并行，不替代/启动/配对/降档。

### 冻结来源 SHA-256

| frozen 文件 | 全文 hash |
| --- | --- |
| backend/app/routes/personal_production_notes.py | 3fd7acc882fbeea0ed7500ef2a5f901a613aae14ce7bd893879dd669a6842562 |
| backend/app/models/personal_production_notes.py | 0b6509fc474de25df37d7c927600ec0e11c96d24bc5cc896d183998978331645 |
| backend/app/services/personal_production_state.py | 89ece9c2338bd9a2bef837adc0b101e498e5afa6c71c0e9d649fc9de6260c9b9 |
| backend/app/personal_production_media.py | 583db9cab32331cf37eafefbe4f71ad3ff7c7c7a56e809137f53063a2f038928 |
| js/episode.js | 79b117ce13e633b6b641faca7232328358e8e6f3be40825dcf097f87994d0e9f |

## Risks / Trade-offs

- [GET会维护同章其他用户状态] → 仅静态记录，所有动态验收用独立demo/fixture，不宣称纯DB读取或扩大后端重构。
- [当前章/素材快照落后服务端] → 稳定ID、位置及摘要核对；不符提示重读章节，不错误映射或认可。
- [旧JSON条目宽松且可能损坏] → 逐条保守投影，未知/坏字段不误批准，orphans不进入当前统计。
- [本地摘要不可用或迟到] → 显式不可核对，世代同时保护HTTP与摘要，无新媒体网络。

## Migration Plan

root复读四规划与apply ready后，由既有Luna负责代码/fixture/tests；不新增子代理。完成分层验证与独立Sol只读终审，再仅更新当前README/兼容文档和本change verification。前三change的18份历史产物不改；撤回仅移除个人记录入口/面板与第九接口，不触及数据库。无提交、推送、部署；Grillme保留独立待办。
