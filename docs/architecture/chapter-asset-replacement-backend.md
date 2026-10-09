# 章节素材替换后端架构

## 范围与接线

本模块新增 `haoai_backend.chapter_asset_replacement`，并在组合根登记 `POST /api/chapters/{chapter_id}/replace-asset`。请求包含必填普通字符串 `old_asset_id`、`new_asset_id` 和 `asset_type`；允许的类别为 `character`、`scene`、`prop`。工厂当前登记 57 个方法，其中 11 个认证方法、46 个其他业务方法；这只是路由登记数。旧后端盘点中的 245 条接口仍有 188 条未匹配到现有模块，这些数字仅说明清点范围，不是迁移完成比例。本批实际 TCP 覆盖的是本用例与有限既有 smoke 流程，不代表全部 57 个方法都已通过 TCP。

模块内 `domain` 解析章节 JSON、替换目标类别引用并收集阶段二引用；`schemas` 保留普通字符串校验与 extra-ignore；`application` 管理错误优先级和两阶段流程；`ports` 定义 actor、UoW 与事务接口；`persistence`/`tables` 用调用方 Session 执行 SQLAlchemy Core 查询；`http` 适配 FastAPI；`media_writes` 只适配既有 public notes coordinator。应用工厂显式提供可信身份解析、普通 `series_access` 策略和业务 UoW。模块不创建 Engine、不运行 DDL 或 migration，也不安装全局 ORM hook。

## 请求与内容兼容

认证和活动会员校验之后，处理器按旧顺序检查：旧新 ID 相同、类别、初始章节是否存在、普通剧集访问、新素材的同剧同类别归属、内容格式与旧引用。请求不新增 trim、长度、UUID 或其他格式约束。

替换只遍历对象帧中的目标类别列表，保留帧顺序、重复值、非对象帧、其他类别和未知字段。若该帧已经含有新 ID，则删除该帧中所有旧 ID 并保留既有新 ID 与其重复；否则逐项替换旧 ID。响应按受影响帧计数，写回沿用非 ASCII JSON 序列化。

## Session、媒体协调与提交边界

媒体协调与源章节写入共享同一业务 Session。章节 DML 前调用 public notes coordinator；它会读取持久化章节基础，并可按已有规则维护媒体版本、删除墓碑或撤销认可。因此不能把类别引用替换描述成绝不写媒体/private 数据，也不能把它误作分镜图片身份变更。

阶段一只按章节主键更新 `content` 和 `updated_at`，要求实际影响行数为 1；否则按通用 500 处理并回滚本阶段，不添加 409 或数据库版本 CAS。章节与协调器在阶段一提交前完成的写入属于同一事务。

阶段一提交后，阶段二在同一 Session 的新事务中按章节主键重新读取章节及当时的 `series_id`，再扫描该剧所有章节当前内容中的三类引用。候选素材先按当前剧集、请求类别和旧素材 ID 查询；删除只按已选行主键执行，不增加剧集或元数据条件。初始查询缺章保持原 404，阶段一提交后的重读缺章返回通用 500。没有候选行、旧 ID 仍被引用或 DELETE 实际影响零行时，仍执行阶段二提交并保留旧 warning 语义。

阶段二提交后先按章节主键真实读回章节，再按原新素材主键读取展示名，不追加剧集过滤或重新授权。角色/道具的假值 `name` 显示为“未知”；场景直接使用 `title`，空字符串保持为空。提交确认失败或任何提交后读回失败均按未知结果返回失败，不自动重试；回滚不能撤销已完成的前一阶段提交。

## 验证边界

源行为对照固定于旧仓库 Git 提交 `23403806898550a7668a6ee7c0c457315655c39b`，不是旧工作区当前文件。完整后端回归与本地 loopback 结果见[验证记录](../../openspec/changes/modularize-backend-chapter-asset-replacement/verification.md)。本批没有运行真实 PostgreSQL 并发、全 57 方法 TCP、浏览器、生产 provider 或部署验收；指定的 GPT-5.6 Sol/xhigh Grillme 外部评审仍待执行。
