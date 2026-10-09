# Proposal

## Why

现有工作台只能汇总查看团队剧集，无法选定一个团队；首切片明确延期的二级筛选尚未迁移。补齐此入口可让用户找到目标团队的剧集，并正确看到暂无剧集的所属团队。

## What Changes

- 在“团队剧集”分类增加“全部团队”和具体团队选择；选项来自当前用户的 `GET /api/teams/my`，保留响应顺序、同名团队和暂无剧集的团队。
- 有意将旧版登录预取与错误缓存兜底改为显式进入团队分类后读取、明确错误及手动重读；其他分类不发团队 GET。
- 具体团队只对现有 `/api/series` 响应做本地筛选，保留四分类、20 条递增展示、返回列表状态和后端 `can_enter` 结论。
- 处理团队名单重读、已选团队消失、隐藏视图与会话切换的请求隔离；空名单、选中团队无剧集和请求失败分别呈现。
- 使用默认无网络 demo 与隔离 HTTP fixture 验收；保留 exact-five 来源复制白名单，另记录冻结全文 hash，不修改前五切片历史。

## Capabilities

### New Capabilities

- `readonly-team-series-filter`: 当前用户团队目录读取、团队剧集二级筛选及请求/会话生命周期。

### Modified Capabilities

无。主规格目录目前为空；本 change 在已交付工作台上增加当前行为，不回写首切片或其他历史 change 的契约与验收。

## Impact

影响 `WorkspaceServices`、新增团队 DTO/decoder/demo、`SeriesPage` 与 Workspace 可见性接线、隔离 fixture 和相关契约/UI测试。业务接口白名单由十条扩为十一条（十 GET 加一 POST 登录），仅新增 `GET /api/teams/my`，无新依赖。

不增加团队详情、成员/角色管理、邀请、分享、认领、写入、锁、生成、队列或媒体调用；不连接真实旧后端/数据库/生产，不提交、推送或部署。冻结 GET handler 无显式写入，但认证可能维护 session，不能声明纯数据库只读。Grillme 固定 GPT-5.6 Sol / xhigh，离线待办与本地开发分开记录。
