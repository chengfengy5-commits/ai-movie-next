# Proposal

## Why

当前粗剪草稿把已纳入、已排除与待安排条目全部展示，用户需要逐行寻找尚待安排或已排除的镜头。为这份当前投影提供本地筛选与计数，可直接配合既有逐行定位核对；无需新增读取或制作写入。

## What Changes

- 增加“全部 / 已纳入 / 已排除 / 待安排”四个选项与完整快照计数；待安排独立计数，可以与纳入或排除重叠，不重解释两个原始布尔状态。
- 仅对当前读取 ready 且非空的原列表提供控制，未保存的非空初始投影同样可筛选；局部零命中可显式恢复全部。
- 保留原草稿序号、读取时章节位置、实际行引用及完整 snapshot 定位；草稿摘要、失效旧引用数量、媒体边界与可见行缺媒体说明沿原规则显示。
- 选择绑定同份读取 snapshot 与世代；重读、重开默认全部，旧回调及迟到结果不能污染新读取。普通资产迟到完成不重置筛选。
- 筛选只改变展示，不触发 GET、读取失效/登记、自动定位、持久化或媒体加载。此项是新的只读 UI 改善，不声称旧页已有该筛选。

## Capabilities

### New Capabilities

- `readonly-rough-cut-status-filter`: 当前章节个人粗剪投影按独立纳入/待安排条件本地筛选、计数与读取隔离。

### Modified Capabilities

无。主 specs inventory 当前为空；本 change 新增独立能力，不修改前十六批历史产物。

## Impact

产品仅修改 `PersonalRoughCutPanel.tsx/.test.tsx`、`Workspace.test.tsx`、`styles.css`，新增 `roughCutStatusFilter.ts/.test.ts`。ChapterBrowser、Workspace 产品、既有定位 helper、DTO/API/services、fixture/demo、样例及依赖均保护；十二种业务模式、exact-five 来源复制不扩展。

现粗剪 GET 的私有访问和认证会话副作用保持，不执行真实 FastAPI/DB/Worker/R2/队列/付费或生产服务，不称整个请求为数据库纯读取。无提交、推送、部署或归档。既有同回合规划后 apply 授权持续有效；本子任务仅生成规划，由 root 审读后交 Luna 实施。指定 GPT-5.6 Sol / xhigh Grillme 离线待办独立保留，不探测或替代；模型路由不冒称 runtime 独立核验。
