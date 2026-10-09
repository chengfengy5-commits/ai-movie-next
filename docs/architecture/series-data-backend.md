# 剧集、章节与分镜来源数据后端

## 范围

本模块把旧系统的剧集、章节、排序、删镜头和分镜素材目录十二个既有 HTTP 方法迁入 haoai_backend.series_data。它与既有认证、剧集访问策略、粗剪和个人制作记录模块组合成二十七方法的统一工厂。本批没有新增前端行为或生产 schema migration。

## 模块边界

| 模块 | 职责 |
| --- | --- |
| domain、errors | typed source records、字段比较、素材/聊天映射规则及领域错误；不依赖 FastAPI 或 SQLAlchemy |
| ports、application | 业务 UoW 与 clock/ID 端口、权限和来源用例；应用控制阶段、commit、rollback 与 close |
| tables、persistence | 最小 Core 查询投影和同 Session 仓储；按字段比较后才发 DML，并检查期待命中的行数 |
| schemas、http | 请求 DTO、十二个路由和兼容的响应/错误映射 |
| storyboard、media_writes | 原始分镜/素材匹配，以及在来源写入阶段复用个人制作媒体协调器 |
| presentation | 旧列表、详情、团队/owner、头像与 style prompt 的投影差异；不主动抓取头像 |

app factory 只在 Session factory 与可信 resolver 同时可用时接线新十二方法；否则请求以 503 结束且业务 Session 创建数为零。显式 resolver 优先级保持不变。既有粗剪和个人制作四方法仍要求原调用方显式传入访问策略；新模块不填充其默认策略。

## HTTP 方法

| 方法 | 路径 |
| --- | --- |
| GET | /api/series |
| GET | /api/series/{series_id} |
| POST | /api/series |
| PUT / DELETE | /api/series/{series_id} |
| GET | /api/series/{series_id}/chapters |
| PUT | /api/series/{series_id}/chapters/reorder |
| POST | /api/series/{series_id}/chapters |
| PUT | /api/chapters/{chapter_id} |
| PUT | /api/chapters/{chapter_id}/delete-frame |
| DELETE | /api/chapters/{chapter_id} |
| GET | /api/series/{series_id}/storyboard-assets |

HTTP 层保留成功状态、标准验证错误、路由依赖和旧 DTO 投影。创建 SceneFrame 使用其显式 DTO 字段，不复制额外引用；更新现有 List[dict] 则保留字典中的未知字段。缺失和 null 不被统一解释为空值。章排序、claim list/detail 字段差异、style/头像选择和 owner/team 展示均由纯投影和用例完成。

## 授权读取顺序

新方法使用真实可信 actor 和业务 Session。普通访问先查询剧集，再执行他人认领权限门槛、作者短路和最终团队成员资格读取；认领权限通过本身不等于访问资格。删除走原 creator、可信数据库 superuser 与团队删除权限规则，不能使用请求中的权限标志，也不将删除资格扩展为普通访问。

章节查询可能在同一 SELECT 中读取 content。授权判断发生在解析该内容及查询素材、私人制作状态之前；因此拒绝后素材/私人查询为零的测试不等同于零 chapter SELECT。团队成员或认领变化按每次请求重新读取，不跨请求缓存。

## 来源写入与事务阶段

Core 不会自动提供 ORM dirty tracking/onupdate。仓储只在实际字段改变时发 UPDATE 和刷新对应时间；相同字段、相同序列化字节与仅锁续期遵循旧时间戳行为。对 chapter content 的真实字节变化，即使只改文本，也会在源 UPDATE 前调用媒体协调器；签名查询参数、title、category、order、位置、描述等非媒体变化不会凭空推进媒体版本。

媒体协调器与来源适配器共享本次业务 Session，但不拥有其生命周期，也不自行 commit/rollback。操作按旧 flush-equivalent 阶段读取持久化事实、准备 overlay、协调媒体、执行 DML，再由应用提交该阶段：

| 操作 | 阶段边界 |
| --- | --- |
| 创建章节 | 插入新章先提交；随后按逐资产插入、替换本地引用和最终内容/删除阶段协调 |
| 更新章节 | 对实际变化的 content 在源 DML 前协调；章节变更、指定 content 的 ensure 和聊天映射按原阶段提交 |
| 删除镜头 | 删除内容与索引聊天为首阶段；之后重建引用并协调素材为下一阶段 |
| 删除章节 | 删除章节阶段先提交；若原章存在角色/场景/道具引用，再按同剧其它章剩余引用进行孤立素材清理并独立提交 |
| 删除剧集 | 按原规则一次处理剧集、章节及关联清理，不加入账务清理 |

Ensure 使用章节初始素材目录，不附加新的 series 过滤；逐个新增资产时以当时数据库 content 与新增 overlay 协调，然后才修改本地引用。匹配资产元数据只在真实值变化时 DML。真实媒体身份变化、删除和恢复维持单调 revision/墓碑并撤销同章相关用户认可；同阶段每位受影响用户仅推进一次。文本或非媒体字段不产生媒体 CAS。

各阶段有独立提交边界，不是请求级原子事务。提交前失败只回滚当前阶段，已提交的前一阶段保留；提交后确认丢失时不返回可信成功，也不自动重试，调用方需显式读取。

## 聊天、任务和账本边界

固定模型中的 AITask.message_id 是普通字符串列，没有外键。单帧删除按旧规则删除/移动索引聊天，但可以留下孤立任务；只有整章/整剧才按消息映射显式删除任务。角色/场景/道具素材只按真实章节剩余引用及所属剧集清理。billing_units.task_id 的真实非级联约束会阻止带账单的任务随章节或剧集删除；失败时保留账本和当前删除阶段，不删除金融历史。

Core 表只是当前适配器需要的最小列投影，测试在临时 SQLite 自建 schema。生产源码不创建 Engine/schema；PostgreSQL 只编译锁语句，没有真实 PostgreSQL 锁或并发撤销验收。源写入协调器可由后续调用者在同一事务复用，但其他旧 ORM hooks/写入者尚未迁移。

## 实际证据边界

源码审查、242 项后端测试、19 组本地 SQLite loopback 和冻结 TypeScript parser 消费结果记录在[本变更 verification](../../openspec/changes/modularize-backend-series-data/verification.md)。这些证据不代表完整前端测试/浏览器验收、生产身份/数据库、SMTP/provider、Worker、部署或指定独立外审已经完成。
