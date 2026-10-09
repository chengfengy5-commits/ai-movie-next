# Proposal

## Why

个人制作记录已经展示核对后的逐镜头状态与备注，但用户只能定位已保存的续作位置，查看其他记录时仍需手动寻找镜头。为当前可信记录提供显式本地定位，可连接记录与分镜而不增加接口、写入或媒体读取。

## What Changes

- 在现有逐镜头记录列表的合格行增加“定位到记录镜头 N”；保留现有列表及“定位到续作镜头”的资格、读取和导航行为。
- 仅当前 ready/readout 内的实际 frame 对象、`verified=true` 且记录可识别时允许定位；继续核对当前章节唯一原始 `storyboard[0]`、完整分镜素材唯一性及同章同剧集归属。不要求状态为已认可。
- 以当前用户、services、章节对象、资产/媒体快照、打开和读取世代绑定入口与回调；父端独立复核，实际焦点成功后滚动、显示独立反馈，失焦清理，面板保持打开。
- 读取中、摘要核对中、错误、空/无保存记录、旧孤立记录、未核对或无法识别的记录不提供新入口。旧响应、摘要、定位和失效回调不能操作新的读取或会话。
- 定位及重复定位不增加 GET，不设置续作位置、不认可、不保存、不播放。既有读取、原图安全展示和样本保持。

## Capabilities

### New Capabilities

- `readonly-production-note-frame-navigation`：从本人当前已核对的逐镜头制作记录，显式定位到同章分镜，包含身份、生命周期和可访问性边界。

### Modified Capabilities

无。main specs 当前为空；前十四批 change 历史和既有续作能力不改。

## Impact

- 产品只涉及 `PersonalProductionNotesPanel.tsx`、`ChapterBrowser.tsx`、`styles.css`；相关测试限定这两组件及 `Workspace.test.tsx`，新增局部 `productionNoteFrameNavigation.ts` / `.test.ts`。
- 不改现有 projection、DTO、媒体身份、services、demo、fixture、Workspace 产品、依赖及业务十二接口；exact-five 来源复制仍为五项，84 份历史、87 份保护文件和13份固定来源保持。
- 来源固定 `23403806898550a7668a6ee7c0c457315655c39b`，仅静态引用。只在独立 haoai-next 本地和隔离 fixture 验收；真实后端/数据库/Worker/R2/付费/生产不运行，无提交、推送、部署或归档。固定 GPT-5.6 Sol / xhigh Grillme 离线补审单列待办。
