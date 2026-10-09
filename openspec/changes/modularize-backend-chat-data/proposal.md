# Proposal

## Why

隔离后端已具备认证、剧集、来源素材和个人制作记录模块，章节聊天及资产聊天仍仅存在于旧单体路由。将完整聊天 CRUD 与只读 AI 调用统计迁入独立模块，可形成真实消息持久化与任务历史读取边界，而不把任务执行、供应商或账务写入混入聊天服务。

## What Changes

- 新增 `haoai_backend.chat_data`，承接固定提交 `23403806898550a7668a6ee7c0c457315655c39b` 的九个聊天/资产聊天 CRUD 方法及一个 `GET ai-stats`；统一工厂由 42 登记为 52 个方法。
- 保留旧 DTO、HTTP 状态、错误优先级、路径与 body 章节错配、条件章节授权、消息筛选和锁续期。已知安全差异明确记录，另行规格化，不在兼容迁移中悄悄修正。
- 应用依赖端口，SQLAlchemy Core 在同一业务 Session 中操作 `chat_messages`、按旧规则续期已有本人章节锁，并只读任务统计。创建/更新一次提交后实际刷新；未知提交或刷新失败不自动重发。
- 消息删除不删除 AI 任务、账单、队列或供应商历史；统计使用原自然 join、状态筛选与分组合并顺序。通过隔离 SQL、HTTP、故障和全行守恒用例证明这些边界。

## Capabilities

### New Capabilities

- `backend-chat-data`：隔离后端完整章节聊天、资产聊天及只读 AI 调用统计，保留原身份/会员/剧集访问语义并接入显式应用工厂。

### Modified Capabilities

无。实际 `openspec list --specs --json` 的主规格清单为空，不声明不存在的既有主能力。

## Impact

新增十一份生产源码与七份专项测试；仅允许修改统一工厂及六份工厂方法集合/数量哨兵测试，合计冻结 25 个实现/测试路径。其余 230 份旧代码、165 份历史和基线 411 份不可变文件保持字节不变，不修改依赖、前端、现有认证、访问、notes、rough-cut、series-data 或 asset-data 逻辑。

完成真实验收后才允许在四份既有说明文件 EOF 追加本批节，保留完整旧前缀，并新增聊天架构说明与 verification；规划阶段仅创建五份规划文件。基线 422 路径预计规划后 427、代码后 445、文档后 447；这是预期清单，不是当前验收结果。

固定 Git 来源字节必须守恒；旧共享 checkout 现场 HEAD/status/tracked/index 只真实记录，不作为必须相等的门槛。只在授权新目录工作，不运行旧应用、生产库、真实 PostgreSQL、SMTP、Worker、provider 或线上服务。本批没有既有冻结聊天 TypeScript consumer，不新增前端或臆造 parser 验收；不安装、提交、推送、部署或归档。指定 GPT-5.6 Sol/xhigh Grillme 离线外审独立待办，内部复核不能替代；请求模型路线与未独立核验的 runtime 元数据分开记录。
