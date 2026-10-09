# Proposal

## Why

当前个人制作记录面板按镜头顺序显示全部记录，查找待修或待重新确认内容需要逐行阅读。基于已完成的当前状态投影增加本地筛选与数量，帮助用户整理本章这一份读取中的记录，并继续使用既有可信镜头定位。

## What Changes

- 在现有可见的当前镜头记录列表上增加“全部”与六种现有投影状态筛选及数量；保持原顺序、镜头位置和原行对象。
- 筛选仅作用于当前 ready 读取中原本显示的列表；零命中说明限定于这份记录，旧记录、全局安全提示与续作区域持续可见。
- 筛选状态绑定当前 readout/read ticket，重读、重开或 scope 变化默认全部，旧筛选回调不得影响新读取；不改变既有读取身份、父端登记和定位资格。
- 以原样 demo 与隔离 fixture 验证本地筛选不附加请求、摘要或媒体操作；完整六状态与迟到 Promise 防护由自动化举证，不扩充样例。

## Capabilities

### New Capabilities

- `readonly-production-note-status-filter`：本人当前章节制作记录按既有投影状态本地筛选、显示数量并安全衔接原定位。

### Modified Capabilities

无。main specs 当前为空；本批新增能力，前十五批历史产物不改。

## Impact

允许修改 `PersonalProductionNotesPanel.tsx/.test.tsx`、`Workspace.test.tsx`、`styles.css`，新增 `productionNoteStatusFilter.ts/.test.ts`。ChapterBrowser 产品和测试、Workspace 产品、projection、DTO/services、媒体与导航 helper、样例、fixture、依赖、十二业务接口和 exact-five 来源复制全部保护。

这是基于冻结旧来源状态/备注语义的新只读 UI，旧页没有在已核对段落提供此筛选。只读指本批客户端行为；不执行可能维护媒体元数据、认可或认证 session 的旧 GET，不声称真实数据库纯读取。不含编辑、认可、保存、播放、后台生成、队列、生产操作、提交、推送、部署或归档。
