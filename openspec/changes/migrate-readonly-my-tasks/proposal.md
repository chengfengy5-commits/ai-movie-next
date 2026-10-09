# Proposal

## Why

前四切片已经支持剧集、章节、素材和个人制作记录，但用户还不能在新版工作台查看自己的任务记录。第五切片补齐独立的只读分页列表，避免把未知状态、查询故障或记录积分误解释为任务失败、实际扣款或退款。

## What Changes

- 增加“我的任务（只读）”独立入口，显式进入后按页读取本人任务，提供上一页/下一页、重新读取及返回剧集列表。
- 仅新增 `GET /api/chat/tasks/list?page=N&page_size=10`，沿用旧页大小及服务端顺序，业务白名单由九条增至十条。
- 保留分页响应和历史任务字段，以纯文本展示可核验的状态、进度、时间及关联名称；宽松处理历史动态上下文字段，不解析被截断请求，不加载结果媒体。
- 退出、离开、换页和重新读取使旧请求失效；返回保留剧集四筛选与已加载展示数。
- 保持默认 demo / 显式隔离 loopback API、exact-five 来源及前四 change 历史；Grillme 离线仍单列待办。

## Capabilities

### New Capabilities

- `readonly-my-tasks`：当前用户任务记录的只读分页浏览及请求/会话隔离。

### Modified Capabilities

无。主 specs 当前为空；本 change 扩展工作台入口，前四 change 的历史验收不回写。

## Impact

前端 Workspace/服务/DTO、任务页、本地 demo 和 HTTP fixture/tests；不新增依赖或运行旧后端。无提交、重试任务、停止、取消、恢复、实时轮询、详情/请求/供应商查询、媒体加载、账务或队列改造。
