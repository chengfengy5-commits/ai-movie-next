# 团队后端接口与兼容边界

本文记录 HaoAI 团队后端的公开路由、模块职责和当前保留的业务行为。请结合 OpenSpec 状态及[验证记录](../../openspec/changes/modularize-backend-teams/verification.md)阅读。

## 模块组成与装配

团队能力由三个模块族构成：management 管理团队、成员、权限、邀请和加入；series 管理剧集共享、认领和团队关联；reporting 提供成员任务和团队额度统计。应用组合层装配三个族的 router，并注入显式 actor 解析、团队策略、共享 Team Unit of Work 和可选 JoinQuota。默认不创建额度实例；缺少额度策略时加入接口按接线缺失返回 503。

团队族沿用应用提供的单个 SQLAlchemy Session。UoW 使用 autoflush=False；即时 bulk 操作与 ORM 写入意图遵循源代码规定的顺序。应用层通过框架无关端口调用持久化适配器；HTTP 层使用显式依赖注入。模块不隐式访问认证密钥、环境文件、provider 或 worker。

## 路由表

下表列出本轮新增的 25 个团队方法及成功状态码。

| 族 | ID | 方法 | 路径 | 成功状态码 |
| --- | --- | --- | --- | ---: |
| 管理 | M01 | POST | /api/teams | 201 |
| 管理 | M02 | GET | /api/teams/my | 200 |
| 管理 | M03 | GET | /api/teams/{team_id} | 200 |
| 管理 | M04 | PUT | /api/teams/{team_id} | 200 |
| 管理 | M05 | DELETE | /api/teams/{team_id} | 200 |
| 管理 | M06 | POST | /api/teams/{team_id}/leave | 200 |
| 管理 | M07 | DELETE | /api/teams/{team_id}/members/{user_id} | 200 |
| 管理 | M08 | PUT | /api/teams/{team_id}/members/{user_id}/role | 200 |
| 管理 | M09 | PUT | /api/teams/{team_id}/members/{user_id}/permissions | 200 |
| 管理 | M10 | POST | /api/teams/{team_id}/invites | 201 |
| 管理 | M11 | GET | /api/teams/{team_id}/invites | 200 |
| 管理 | M12 | DELETE | /api/teams/{team_id}/invites/{invite_id} | 200 |
| 管理 | M13 | POST | /api/teams/join | 200 |
| 剧集 | S01 | GET | /api/teams/{team_id}/series | 200 |
| 剧集 | S02 | POST | /api/teams/{team_id}/series | 201 |
| 剧集 | S03 | POST | /api/series/{series_id}/share | 200 |
| 剧集 | S04 | DELETE | /api/series/{series_id}/share | 200 |
| 剧集 | S05 | POST | /api/teams/{team_id}/series/{series_id}/claim | 200 |
| 剧集 | S06 | DELETE | /api/teams/{team_id}/series/{series_id}/claim | 200 |
| 剧集 | S07 | POST | /api/teams/{team_id}/series/{series_id}/transfer | 200 |
| 统计 | R01 | GET | /api/teams/{team_id}/members/{user_id}/tasks | 200 |
| 统计 | R02 | GET | /api/teams/{team_id}/usage | 200 |
| 统计 | R03 | GET | /api/teams/{team_id}/series/{series_id}/usage | 200 |
| 统计 | R04 | GET | /api/teams/{team_id}/usage/model | 200 |
| 统计 | R05 | GET | /api/teams/{team_id}/usage/export | 200 |

应用共有 82 个路由注册，其中团队接口占 25 个。82 是注册计数，不是 82 个方法全部经过 TCP 测试的声明。

## 管理与权限兼容

管理接口继续按 owner、admin、member 规则和逐项权限判断执行，校验与权限失败遵循原有错误顺序和响应格式。owner 不能通过普通 leave 离开自己拥有的团队。M11 获取邀请列表仍会维护已过期邀请，因此 GET 不保证零写。加入额度由可选策略提供；策略未接入时返回服务不可用。M06 退队和 M07 移除成员只清除对应 membership 与该成员自己的团队章节锁，不清除已有 claimed_by/claimed_at。

删除团队保留当前次序：先解除剧集的 team_id 关联，再按 team_id 释放章节锁。后续锁查询发生在解除关联后，可能无法找到原团队剧集；这里记录兼容现状，不把它表述为已修复的锁清理。

## 剧集分享与认领

S01 按 updated_at 降序列出团队剧集，保留分页边界、头像投影、chapter_count，以及 characters、scenes、props、storyboard 四类 asset_counts；不返回 frame_count。创建、分享、取消分享、认领、解除认领和转移继续遵循原角色、所有者与队长判断。

claim=false 属于系列创建/分享入口，只关联团队，不覆盖当前 claimed_by 和 claimed_at；S05 团队认领路由不接收该标志。系列移出团队或取消分享只清理 team_id 及相关章节锁，已有 claimed_by/claimed_at 继续保留。分享和取消分享按源时序清除相关章节锁。首次读取的 assigned_values 快照用于净变化判断；同值或回到快照值的写入不产生无意义 UPDATE。当前没有新增 ordinary claimedSeriesAccess 门禁。

写入响应遵循各路由的来源行为：S02 读取新建系列主键行；S03 读取团队主键并返回 team name/id；S05 读取认领字段。S04 与带 claim 的 S06 只返回 message，不执行来源中不存在的刷新；S07 的 claimed_by 响应按原请求 body 意图生成。不能把这些路径概括成所有写接口统一按主键刷新。实际提交后读回失败或提交确认异常时不自动重放写请求。dirty UPDATE 零行按通用 500 和事务回滚处理；合法 bulk DELETE 零行仍可成功；历史无版本条件 DELETE 零行保留警告成功行为。

## 统计范围

R01 使用 view_tasks，并先校验目标成员仍属于该团队；随后仅按目标 user_id 分页查询该账户全部任务，并返回该账户 user_credits 总积分余额。此范围包括外团队、个人和孤立任务，不按当前团队剧集过滤。R02–R05 使用 view_usage，按当前属于团队的剧集与相关任务统计。R02 返回各系列 items 以及团队 total_calls、total_credits、total_failed；R03 返回单系列汇总，R04/R05 保留原 DTO 与导出形状。完成与失败调用分开计数，只有 completed 调用计入费用，合法负 credit_cost 保留。

统计业务接口不写任务、账务或额度记录；邀请列表过期维护属于 management。

## 验证入口与限制

当前勾选状态和验收范围见[验证记录](../../openspec/changes/modularize-backend-teams/verification.md)。源码冻结、独立代码审查、作者专项、Root 完整后端测试和 25 方法 loopback 是不同证据层。外部 Grillme 审查仍待办；本轮没有宣称 82 路由 TCP 全量、PostgreSQL、provider、SMTP、worker、前端、浏览器或部署验收。
