# Proposal

## Why

首切片已可登录和浏览剧集，但创作者仍无法查看章节及已有分镜。下一步开放一条独立只读路径，让用户检查章节内容与镜头对应关系，同时避免旧章节页进入时申请编辑锁、加载生成及素材编辑模块。

## What Changes

- 为服务端 `can_enter=true` 的剧集增加“只读查看章节”，提供章节列表、章节选择、只读分镜及返回剧集列表；受限剧集保持不可进入。
- 章节使用既有 `GET /api/series/{series_id}/chapters` 的全量内容与锁快照，保留服务端排序；分镜按章节 content 数组原顺序展示文字、原文和已有媒体状态。
- 只为选中章节请求 `GET /api/series/{series_id}/storyboard-assets?chapter_id=...`，按唯一同章 `storyboard[0]` 关联原图。缺失、重复或跨章身份仅显示文字及明确提示，不以索引配图。
- 默认本机 mock；显式 API 仍仅连接隔离 loopback fixture。新增 GET 的错误、取消及上下文世代复用首切片约束，401 清会话，403/404 保留会话并允许返回。
- 新增冻结契约、真实 fetch、UI 和真实浏览器验收；保持五项来源复制白名单，不复制旧后端或旧章节脚本。

本 change 依赖 `migrate-react-shell-series-list` 的现有实现。首 change 中“卡片无导航、仅三条请求”描述的是第一切片的交付边界，本 change 明确将当前应用扩展为只读章节导航和两条新增 GET；不改写首 change 的历史验收或把其待完成 Grillme 标为通过。

## Capabilities

### New Capabilities

- `readonly-chapter-storyboard-browser`：受剧集访问限制的章节及分镜只读查看、稳定镜头媒体映射、锁快照展示和跨上下文请求隔离。

### Modified Capabilities

无。`openspec list --specs --json` 当前返回空主规格；已有首 change 尚未归档，因此本次新增独立能力并显式声明依赖和边界替代。

## Impact

改动限新库 `frontend/` 的工作台导航、剧集入口、章节模块、契约及服务适配，以及 `tools/api-fixture/` 和对应测试、验收文档。沿用 React、TypeScript、Vite 和现有测试工具，不新增路由、状态或媒体依赖。

新增业务请求仅为上述两条 GET；不调用剧集详情、角色/场景/道具目录、锁接口、个人记录、粗剪、生成、账务或队列。原图只加载既有同源 loopback 媒体；预览只展示有无，不播放或外链真实视频。所有权、团队认领及个人状态规则保持现有语义。本轮不启动旧 FastAPI/PostgreSQL/Worker，不提交、推送或部署。Grillme 固定 GPT-5.6 Sol / xhigh，当前离线待审，与本地开发并行。
