# Design

## Context

动机见 proposal。当前 ChapterBrowser 只显示 referenceCount，三个素材列表服务和严格 DTO 已可用；Workspace 保持剧集列表挂载以保留筛选。关联素材是剧集范围，不含 chapter_id，解析不能要求分镜资产或原图核对成功。第八批已完成本地验收，仅 Grillme 待审；前八批 48 个历史文件已有进入本批前 hash 快照，不修改历史。

固定来源提交 `23403806898550a7668a6ee7c0c457315655c39b` 的静态事实：

| 事实 | 来源 |
| --- | --- |
| 分类列表内以 item.id 精确匹配引用；按引用数组原序逐项解析 | js/episode.js:400-407,712-731 |
| 既有关联详情；本批排除其管理及媒体操作 | js/episode.js:747-799 |
| 三类素材归属 series，未按 chapter 归属 | backend/app/models/series.py:84-118,143-154 |
| 三个已有 GET 经会员与剧集访问校验、无声明排序 | routes/series.py:1278-1286,1382-1390,1467-1475；team_service.py:166-185 |
| 响应命名与可空字段、角色特征；aliases/canonical 不是前端解析身份 | schemas/series.py:84-119,149-166,186-197,217-228 |
| duplicates/ignored GET 可能 delete/commit，本批禁止调用 | routes/series.py:970-995,1021-1023,1153-1170 |
| 通用认证可能维护会话记录，不能称整次 HTTP 为数据库纯读 | routes/auth.py:112-135 |

| 冻结文件 | SHA-256 |
| --- | --- |
| js/episode.js | 79b117ce13e633b6b641faca7232328358e8e6f3be40825dcf097f87994d0e9f |
| backend/app/schemas/series.py | 78b1aee6e31cc195fb9e9af7401738ed1c2b3a73a9563f196855dd9912e3bfb9 |
| backend/app/routes/series.py | db9721e34a3c5f22f81ce7cf4bc5434d9f3661d732ab1e8a79ba2e74852243cf |
| backend/app/models/series.py | 89bcbff8c0e3a075f62af5ab1f8ab8c961a6a2d275dc99c43c1aad7ae126a7ff |
| backend/app/services/team_service.py | 392f0792be876adda6b4b7629af0624a1dfbaef6fbf8555d7cacfd96cc81326f |
| backend/app/routes/auth.py | 6cae3297fb5806c8668cbd245be974ff42ccf42999265440a6d6e27a7e6d1a78 |

## Goals / Non-Goals

**Goals:** 以已有目录解析当前镜头引用，完成明确错误状态、可访问界面、scope 隔离和独立验收。

**Non-Goals:** 不跨页面自动跳素材卡片；不新增 API/DTO、来源复制、依赖、编辑、删除、管理、canonical/alias 归一、去重、图片/播放或后台服务。不改个人记录及原图投影规则，也不等待原图成功才能查看关联素材。

## Decisions

### 章节内面板与显式分类

每个镜头 article 内提供具名“查看镜头 N 的关联素材”按钮；打开当前镜头面板时关闭 notes、rough 和续作反馈，反向打开 notes/rough 也关闭关联素材。面板标题包含当前章节镜头位置，初始分类为 null，提示选择分类；三个分类按钮在无引用时仍能解释未关联或坏结构，但不发 GET。这是一个章节内查看上下文，关闭后仍留在当前章节，避免跨页面返回恢复与额外导航复杂度。

分类选中时仅合法引用存在才读取对应 service。成功目录缓存在本次打开实例内、按分类分开；重复选择或 A→B→A 分类返回复用成功目录。显式重读只清当前类型目录，立即作废旧 ticket；pending 也可重读或关闭，不自动重试。源目录按响应原序保留，显示结果按引用原序，不排序、不合并重复项。

### 引用与详情的局部投影

新增独立引用解析/详情投影，不改 storyboardProjection 或 personal-production。character/scene/prop 映射到 characters/scenes/props；缺字段/null/[] 为未关联，非数组为不可识别，数组中的非字符串/空白项保留局部坏项提示。合法字符串只作非空检查，身份比较保留原串，不 trim/coerce。

每个合法引用先查当前分类目录的精确同 ID 项，再检查唯一性与 series_id；不按名称、aliases、canonical_key 或 frame_index 兜底。目录同类型重复/外剧集在现 HTTP decoder 已拒绝，投影仍作防御性核对，便于直接 services 与错配测试。引用重复保留行和原始数量；同 ID 跨类型合法。UI 不显示 ID，缺失行使用当前分类与引用序位作说明。

详情只使用 name/title、非空 aliases、description 及 character 的 gender/age/role/appearance，空名称用未命名占位、无描述有占位。使用 React text，不展开原始对象、不复用会产生 img 的 AssetCard。媒体和规范字段留在既有 DTO，不进入 DOM 或新请求。

### scope、请求与旧回调

父级打开 owner 包含 userId、services、seriesId、chapter 对象、frame 对象、本章 position、openEpoch；回调捕获对应 owner。章节及显式原图重读先关闭 owner；原图普通加载成功不影响关联引用查看。镜头 position 只供当前快照标题，frame 对象必须仍是该 chapter.content 对应项。

render 当帧核对 owner 与当前 scope，失配即隐藏并永久关闭；不能只靠 effect 清旧 ready。Panel 绑定完整打开 scope，独立请求 ticket 包含 scope、分类及请求 generation；类型选择/重读/关闭时同步失效，异步成功、错误、401 和缓存写入均先核对 ticket/current scope。layout 失配使当前实例永久无效并关闭；scope A→B→A 不复活，新的显式打开才创建新 owner。旧 onClose 也必须核对父级 owner，不能关闭重新打开的面板。

当前 401 交给原会话处理；非 401 权限/会员/网络/timeout/格式错误保留会话。面板复用已有错误表达方式，但避免为复用而重构无关素材库。自动用例让旧 Promise 真正 settle 并 await act，布局观察直接验证 first commit；不能把 abort 调用当隔离已证明。

### 合成引用与独立验证

demo 仅把现有 character-1/2、scene-1/2、prop-1/2 映射到同 series 的既有素材 IDs；fixture 仅把 createFrame 已有角色/场景引用对齐到已有 shared-asset。各分类数组长度、位置顺序和重复数量保持；其他章节/镜头/text/storyboard/preview/media/notes/rough 样本不变。默认 fixture prop 仍为空，其 API 浏览器只能证明道具未关联不发 GET；道具成功由 demo 第二章、组件与既有 props HTTP 契约证明，不能说默认 fixture 覆盖了道具成功。

无需新增 fixture 控制模式；既有分类列表错误、延迟、空列表与异常结构足以验收，混合/缺失/重复/归属反例由组件与真实 HTTP 契约覆盖。root 单独做 demo 桌面/390px/键盘与 loopback API 请求计数，记下实际访问的业务路由模式，不把支持 12 条说成实访全部。新面板无媒体节点；当前章节已有 lazy 图片可能自然加载，审计区别业务 GET 与既有 PNG。

## Risks / Trade-offs

- [关联引用包含未知字段或混合坏项] → 局部提示、不静默当作合法空，也不猜测素材身份。
- [类别缓存或旧 callback 串入新 scope] → 每次打开独立 owner、首帧永久失效、generation 与真实旧 Promise/回调重放测试。
- [原图读取尚未完成] → 引用读取仅依赖章节；显式原图重读关闭面板，普通加载完成无需关闭。
- [固定源 GET 与真实权限不同] → 只做静态/隔离证据，真实 backend/DB/生产未验收，Grillme 独立保留待办。

## Migration Plan

先完成本 change 规划与 strict、apply ready，再按持续同轮授权实施。两名 Luna 按无冲突文件分工：主实施负责新投影/面板及章节集成和测试，合成数据实施仅对齐 demo/fixture 引用及守恒验证。各自冻结后 root 跑最终全量与浏览器，Sol 最终只读复审，最后更新当前文档及本 change 验证。回退移除新面板入口/模块并撤销合成引用对齐，不涉及数据库或历史 change。无提交、推送、部署。
