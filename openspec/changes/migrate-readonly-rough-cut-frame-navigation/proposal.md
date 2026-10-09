# Proposal

## Why

我的粗剪草稿保留编排顺序，用户核对文本时还需手动寻找原章节镜头。已有草稿稳定 ID、章节内容和原图记录可支持显式本地定位，补齐粗剪浏览与分镜核对的只读衔接。

## What Changes

- 为当前草稿中可核验身份的条目增加“定位到对应镜头”，聚焦并滚动当前章节的镜头文章，保留粗剪面板和只读记录。
- 按草稿、章节与完整原图记录中的唯一原始 ID 核验目标，不以草稿序号、`frame_index`、名称或文本兜底。
- 定位等待当前原图记录就绪；保留原图加载中或失败时仍可打开和阅读粗剪的既有行为。普通原图完成不会关闭面板、自动定位或补发粗剪请求。
- 定位与关闭回调绑定用户、服务实例、剧集、章节对象、打开世代和读取快照；显式重读及上下文变化使旧回调失效。成功反馈随焦点离开或上下文失效清除。
- 不新增请求、媒体元素、播放、保存、认可、锁或后端操作。`included`、`pending` 和视频可用性只作状态展示，不作为定位资格。

## Capabilities

### New Capabilities

- `readonly-rough-cut-frame-navigation`: 从本人当前章节粗剪的合法稳定身份条目显式定位章节镜头，并隔离过期请求、快照和回调。

### Modified Capabilities

无。主规格库存为空；前十三批 change 历史保持不变，本批以新增 capability 约束整合行为。

## Impact

- 允许修改 `PersonalRoughCutPanel.tsx` 及测试、`ChapterBrowser.tsx` 及测试、`Workspace.test.tsx`、`styles.css`，新增局部 `roughCutFrameNavigation.ts` 及测试。
- 保持 DTO、API adapter、services、demo/fixture 样例、已有身份与媒体投影、十二种业务接口和 exact-five 来源复制名单不变。
- 仅隔离本地 demo/HTTP fixture 验收；不运行真实后端、数据库、Worker、队列、R2 或付费服务，不提交、推送、部署或归档。指定 GPT-5.6 Sol / xhigh Grillme 审查仍独立待办。
