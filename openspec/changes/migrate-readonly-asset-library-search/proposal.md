# Proposal

## Why

素材目录目前需要逐张浏览，已知名称或别名时仍难以快速找到对应素材。本切片增加当前分类的本地名称/别名搜索，保留已有只读详情、关联镜头和导航能力。

## What Changes

- 在当前已读取且非空的角色、场景或道具目录增加单关键词搜索：去除查询首尾空白后，按大小写不敏感的字面子串匹配原名称/标题及可见非空别名。
- 展示当前快照的命中数量与完整分类数量；零命中允许显式清空查询，不把零命中表现成服务端空目录。
- 保留完整卡片节点、原顺序、原对象和原索引，以 `hidden/inert` 隐藏未命中卡片；完整目录继续作为归属与唯一身份校验依据。
- 查询实际变化关闭当前关联镜头面板并清除定位反馈；分类、读取世代、scope 或新导航意图变化时清空查询，拒绝旧输入/清空回调污染新读取。
- 搜索只改变本地展示，不增加业务接口、后台读取、媒体链路、写入、样本或依赖。本项是新增只读 UI 改善，不声称旧页面已有同样搜索功能。

## Capabilities

### New Capabilities

- `readonly-asset-library-search`：当前素材分类按名称/标题和别名本地搜索，保持完整目录身份、读取隔离及既有导航契约。

### Modified Capabilities

无。实际 `openspec list --specs --json` 返回主 specs 为空；前十七切片历史 change 保持不变。

## Impact

- 允许修改：`frontend/src/features/assets/AssetLibrary.tsx`、其测试、`frontend/src/app/Workspace.test.tsx`、`frontend/src/styles.css`。
- 允许新增：`frontend/src/features/assets/assetLibrarySearch.ts` 及其测试。
- Workspace 产品、ChapterBrowser、关联镜头投影/面板/导航 helper、DTO/API/services、fixture、demo、样本、依赖与 exact-five 来源复制名单保持原样；业务接口仍为既有十二种模式。
- 默认 demo 与隔离 HTTP fixture 验收分别记录业务请求和现有安全 PNG 实际增量，不预先承诺图片请求为零。真实后端、数据库、Worker、R2、队列、付费服务和生产均不调用。
- 指定 GPT-5.6 Sol/xhigh Grillme 外部复审独立待办；离线不阻断本地开发，不以其他只读复审替代。
