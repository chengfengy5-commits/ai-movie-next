# 完整团队 API 模块化草案

本稿为完整团队 API 的本地规划候选。拟变更名为 `modularize-backend-teams`，普通 OpenSpec `spec-driven`，正文语言 zh-CN。实施须以 root 建立本批实际基线、执行普通 OpenSpec CLI 并明确冻结批准为前提；文件存在或规划就绪本身不构成实施授权。

## Why

旧团队路由把管理、团队剧集及用量查询写在同一个文件，且成员任务借用牵连生成服务的 chat 路由函数。新后端已有可信身份、普通剧集访问和私人状态模块，但尚未迁入完整团队能力。需要在固定来源行为下拆成三个可并行实现的完整业务族，避免将目录查询或其中一族当成整个团队功能完成。

兼容基准是旧 Git 提交 `23403806898550a7668a6ee7c0c457315655c39b`，而不是现场旧工作区。事实及行锚见既有 source-analysis-01、supplement-01、design-draft-01。现有49固定来源拟仅增加 `backend/app/routes/teams.py`、`backend/app/routes/chat.py`、`js/teams.js` 三路径成为候选52；本稿没有修改或冻结来源库。

## What Changes

- 完整迁移25方法：management13、series7、reporting5。详情、成员/角色/权限、邀请码和加入、团队剧集与认领/分享、成员任务和四种用量接口均在范围内。来源成功状态为三项201，其余200，DELETE也保留200。
- 先由唯一作者建立公共权限/config/clock/quota/DTO/ports及同业务Session reader，再冻结公共接口；三组作者各自写独立子包和对应测试，最后由唯一集成作者修改 app 及登记哨兵。
- 复用现有可信活动会员 resolver，业务Session独立于认证维护Session；不接受输入角色，不把普通 SeriesAccess 的集合或认领门槛替代团队专用许可及详情列表。
- 保留DTO/422、限额、GET维护写、首次ORM赋值净变更、bulk/dirty/DELETE0差异、提交后实际读回、原报表聚合/排序与权限优先级。已知并发/权限差异记录为兼容，不悄加CAS、修锁清理顺序或新排序。
- 用纯Core查询/DML和实际SQLite证明，保留非目标素材/媒体/私人认可/任务/队列/账本历史；错误后不自动重放。任务、计费、队列与供应商均只读，不启动业务资源。

## Capabilities

### New Capabilities

- `backend-teams`：完整25方法的本地模块化与兼容契约，包括三族并行分工、可信身份、共享权限、事务与只读报表。

### Modified Capabilities

无。已接纳的 auth/access/series/asset/chat/canvas/download/replacement 业务、React页面与冻结历史不改；仅统一工厂登记及必要哨兵期望是后续拟许可例外。

## Impact

候选新增28生产文件、17测试/支持文件，共45新代码；修改旧9路径（app及8登记测试），最终代码冻结键为54。新方法使现57登记增为82，这不表示82个实际TCP方法验收。pyproject、已有业务模块、共享身份及前端均冻结。

未来文档仅四旧文件完整前缀保留后EOF追加、两新说明及tasks勾选，共7路径。若 root 新批实际基线有B路径，则规划后B+5、代码后B+50、文档后B+52；本批基线须在 batch31 本地收口后由 root 重新建立，历史代码阶段清单不能替代本批实际基线。需 root 在批准前建立实际基线/原9字节/原4文档并核算保护集合。

不包含SMTP、真实供应商/生成/队列执行、积分转移或退款、PG运行验证、前端页面/构建/浏览器、部署、语言重写或新增路由。生产上线须另行授权，不作为本地迁移额外门槛。指定GPT-5.6 Sol/xhigh Grillme保持离线独立待办；root本地验收不能替代外审，整体目标仍在进行。
