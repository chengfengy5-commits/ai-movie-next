# Proposal

## Why

新版已能查看剧集、章节、分镜和素材，但用户无法在已选章节内查看自己的制作状态、返工备注与续作位置。第四切片补齐个人记录只读浏览，并核对稳定镜头和加载时素材，避免把旧认可或失效记录误配到当前分镜。

## What Changes

- 在 ChapterBrowser 当前章节新增“查看我的制作记录”，仅显式打开时读取；面板显示本人记录、待重新确认和续作位置提示，支持关闭及重新读取。
- 仅新增 `GET /api/chapters/{chapter_id}/personal-production-notes`，当前业务白名单由八条扩展至九条；不加入 PUT、认可、定位写、粗剪、历史、锁、生成或媒体调用。
- 以当前章唯一 `storyboard[0]` 及本地 URL 身份 SHA-256 核对记录快照；摘要计算不请求媒体，无法核对或旧认可失效时明确提示，不按索引兜底。
- 沿用独立 demo 和隔离 loopback fixture；记录旧 GET 初始化/协调媒体及撤销认可的维护写副作用，可能影响同章其他用户，不宣称数据库纯只读。
- 切章、重读章节、离开或退出关闭并清面板；忽略取消的迟到成功/401及摘要结果不得覆盖新上下文。

## Capabilities

### New Capabilities

- `readonly-personal-production-notes`: 当前章的私有制作记录浏览、稳定身份和摘要核对、只读投影及请求隔离。

### Modified Capabilities

无。主 specs 当前为空；通过新 change 扩展现有应用，不回写前三切片的历史规划与验收。

## Impact

影响前端 notes DTO/服务/纯投影与 WebCrypto 摘要、ChapterBrowser 面板及对应测试，隔离 HTTP fixture、当前 README/兼容文档和本 change 验证记录。不增加依赖或后端实现，legacy-reference 仍为原 exact-five；冻结源码仅作静态证据。真实 FastAPI、数据库、Worker、R2及付费服务均不运行，无提交、推送或部署。Grillme 固定 GPT-5.6 Sol / xhigh 离线待审，当前规划/源码审查不能替代。
