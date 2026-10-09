# Design

## Context

见 proposal.md 的动机。GPT-6.1 Sol/xhigh 静态分析确认现有 AssetLibrary 卡片拥有分类和完整素材对象，listChapters 已返回含 content 的全剧集快照，parseChapterList 检查同 series 和章节 ID 唯一性。当前 ChapterBrowser 保留章节响应顺序，不能用 order 排序或生成位置。

必要整合问题是 AssetLibrary 的缓存与 currentResult 仅按分类核对；直接更换 user/services 后可能显示旧目录或复用旧成功缓存。本批将其绑定上下文，否则旧卡片能创建新上下文 owner。

冻结旧 SHA：23403806898550a7668a6ee7c0c457315655c39b。root 实际以固定 git show 核对：
- js/episode.js:400–407 分类、原始 item.id === id；:712–731 引用字段和原序。
- backend/app/routes/series.py:285–342 整剧集章节 GET、会员与剧集访问判断、响应数组原序和不修改 ORM 属性；:293 服务端 order.desc()/created_at.desc()。
- backend/app/schemas/series.py:84–96 ChapterResponse.content 为可空对象列表。
- 原认证可能维护 session 的边界沿用兼容说明；这里的只读描述 UI，不宣称全 HTTP 请求数据库纯读。

这三份全文实际 hash/bytes 与第九批冻结清单一致：
| 来源 | SHA-256 | 字节数 |
| --- | --- | ---: |
| js/episode.js | 79b117ce13e633b6b641faca7232328358e8e6f3be40825dcf097f87994d0e9f | 1162775 |
| backend/app/routes/series.py | db9721e34a3c5f22f81ce7cf4bc5434d9f3661d732ab1e8a79ba2e74852243cf | 115619 |
| backend/app/schemas/series.py | 78b1aee6e31cc195fb9e9af7401738ed1c2b3a73a9563f196855dd9912e3bfb9 | 13748 |

来源只静态审阅，不复制或运行；exact-five 来源复制白名单不扩展。前九批历史 54 份产物已保存 hash manifest，实施后核对不变。

## Goals / Non-Goals

**Goals:** 在 assets 模块内增加一个独立文字投影和一次打开的请求面板；修复父级上下文隔离以支撑可靠入口；复用已有服务和样本，完成独立本地验收。

**Non-Goals:** 不做素材到 ChapterBrowser 的跳转/定位，不读取 storyboard-assets、notes、rough 或新增锁接口，不做全局索引或通用异步框架，不改旧投影、DTO/API、样本、依赖、旧库、后端或生产。

## Decisions

### 局部纯投影

增加 assetFrameUsage 投影，接收已验证章节快照、分类与素材原始身份。按列表下标生成章节列表位置与镜头位置；仅扫描所选引用字段，以严格相等计数，一镜头一行。返回已确认分组/镜头数和存在坏引用的局部提示；missing/null/[] 不增加坏项计数，非数组及 mixed 坏项不阻断合法匹配。保留 frame.text/original_text 的字面文本和缺失/无效区别。章节 title 空白使用未命名占位。

采用新局部投影以免改动既有 storyboardProjection、个人记录、粗剪或第九批引用投影；不复用其媒体与关联计数逻辑。可在边界验证返回章节归属/唯一身份，不将错误响应当作空结果。

### 一次打开一次章节快照

AssetFrameUsagePanel 显式挂载后只调用 listChapters(seriesId, signal)。重复当前入口不重发，面板内重新读取立即隐藏旧内容并产生新世代。关闭重开或换素材必须重新读取，不建立跨素材、跨分类或全局章节缓存；同一已打开面板的成功内容保持到重读/关闭，不轮询。相比共享索引，这个方案降低陈旧快照和权限缓存风险。

### 完整 owner 与父级目录隔离

owner 绑定 userId/services/seriesId/category/素材对象/当前 ready 目录对象/openEpoch。父级在 render 依据当前完整 scope 过滤可见 result/cache/owner；首次失配立即隐藏，并令原 owner 永久无效，A→B→A 不恢复。缓存只属于当前 scope，变化清空并重新读取当前素材分类。目录重读先关闭反查，实际切分类或换素材也关闭；重复同分类保留当前面板。

请求完成时核对挂载、AbortSignal、owner 有效性和 request generation，覆盖 success/error/401。旧关闭回调携带 owner，父级只关闭仍对应的面板。StrictMode effect setup/cleanup 的普通重放不能永久作废有效 owner；清理仅取消该次请求，真实 scope 失配/关闭才永久失效。父级目录读取也须用同等当前 scope/世代判断防止首 commit 后旧 401 与缓存写入。

### 有效卡片与文字 UI

入口仅为当前 ready 目录内同系列、非空原始 ID 且该原始 ID 唯一的素材提供。重复目录项保留原显示顺序但无读取入口，用位置消除重复 React key。面板包含素材名、快照说明、分组章节标题/位置、镜头位置/引用次数、文字/原文、局部不确定、空结果和当前错误。名称空白沿现有占位。新增结构无 img/audio/video/a，内部稳定 ID、canonical、preview/URL/JSON 不进入显示字段。

已有 AssetImage 的可信 loopback 图片不改，普通 load/error 更新不改变 ready 目录对象或 owner。桌面和 390px 复用成熟样式并提供具名键盘入口；不把技术 owner、原始 ID 等实现字段写入产品文案。

## Risks / Trade-offs

- [章节 GET 返回全 content，快照可变] → 只显式读取，不自动刷新；显示当前快照口径，提供重读，不宣称完整历史使用。
- [坏引用不代表无使用] → 展示可确认结果并提示局部无法核对，零匹配措辞限定当前快照。
- [父级 cache 旧上下文泄露] → 本批必需的 scope/result/cache 门禁和实际 deferred/首 commit/ABA 测试。
- [Abort 无法保证自定义服务真的取消 Promise] → 真正结束旧 Promise 后 await act，验证旧成功/401不污染当前状态。
- [API fixture 默认道具无引用] → API 浏览器道具只证明零匹配；道具成功由现有 demo 和自动化，不改变 fixture 为凑覆盖。
- [真实服务与固定 Grillme 未验证] → 分层记录本地与浏览器证据；Grillme 不可用保持待办，不降档、配对或代替。

## Migration Plan

在独立目录完成规划、Luna 6/xhigh 实现、GPT-6.1 Sol/xhigh 只读复审、root 本地与真实浏览器验收。只改当前源码/测试/样式、当前 README/兼容说明和本 change；不回写前九批、不提交/推送/部署。临时 API 服务与页签在验收后关闭，保留 demo。若需回退，移除本批入口、局部面板及投影即可，不涉及持久数据。
