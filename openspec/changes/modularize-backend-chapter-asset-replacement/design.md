# Design

## Context

See proposal.md - Why。旧处理器既改写章节引用，也在同一请求中协调个人媒体、提交替换，再重新扫描同剧章节并清理素材。新模块需保留此调用链的错误顺序和两次提交边界。固定来源契约见 `specs/backend-chapter-asset-replacement/spec.md`；本设计说明模块划分、Session 使用及测试方式。
旧行为依据固定旧仓库 /Users/yanghaibo/data/projects/ai/haoai-修改版/haoai.zhuwh.com 的 Git 提交 23403806898550a7668a6ee7c0c457315655c39b。主要处理器位于 backend/app/routes/series.py:2804–2880，引用与孤儿清理 helper 位于同文件 :654–730；media hook 和数据库/session 接线按该提交中的对应来源核对。EVID 来源分析收据 haoai-next-backend-chapter-asset-replacement-source-analysis-01.json（SHA-256 b0b81b8579a4f2f6ef860f307e0b3b3b52c098a626769ce5deed6deab72eda04）及设计前置收据 haoai-next-backend-chapter-asset-replacement-design-prerequisites-01.json（SHA-256 2678f638466753ca7aa8b1f1b65c2da394edd7901e5194842f6e58d740e7f746）记录固定提交、来源绑定和实际读取窗口；这些收据没有声称本轮完整重读全部八份旧源码，本设计也不作此声称。设计前置收据引用的 SQLAlchemy 2.0.23 源码是只读安装库依据，不属于 49 个固定 Git 源。
旧工作流依赖同一个 ORM Session 完成访问、写入、提交后刷新和第二阶段读取。已核对的 Session 工厂未设置 `expire_on_commit=False`，SQLAlchemy 默认会在提交后过期 ORM 属性；读取已过期的章节 `series_id` 会触发真实查询。因此第二阶段应在同一 Session 的新事务中显式重新加载章节及其当前 `series_id`，不能依赖第一次读取时缓存的值。阶段二若章节或素材提交后读取失败，按未知结果返回失败，不重放整个替换。

## Goals / Non-Goals

**Goals:**

- 将替换路径拆成纯领域变换、应用流程、最小 Core 持久化和 FastAPI 适配层。
- 在现有 actor、普通剧集访问策略和同 Session 媒体协调接口上接入，不改变权限规则。
- 明确阶段一提交、阶段二全剧引用扫描与清理提交、提交后重新读取三处边界，并以真实 SQL 行为测试其耐久性。

**Non-Goals:**

- 不迁移素材 CRUD、重复素材处理、融合、上传、生成、导出或独立孤儿清理 API。
- 不增加 chapter lock refresh、全剧串行锁、CAS/重试、schema migration 或新的媒体业务规则。
- 不修改 notes、asset_data、series_data 等其他既有模块的业务语义，不执行真实 PostgreSQL 并发验收。

## Decisions

### 采用单独的替换模块

新增 `backend/src/haoai_backend/chapter_asset_replacement/`，包含 `__init__.py`、`domain.py`、`errors.py`、`ports.py`、`application.py`、`schemas.py`、`http.py`、`persistence.py`、`media_writes.py` 和 `tables.py`。

- `domain.py` 负责内容解析、引用替换、计数和阶段二引用收集；不依赖 FastAPI 或 SQLAlchemy。
- `ports.py` 描述可信 actor、工作单元及事务操作；`application.py` 编排错误优先级和两个事务阶段。
- persistence.py 使用调用方传入的单一 Session 执行 Core 查询/写入；tables.py 只声明查询投影所需的 SQLAlchemy MetaData 与 Table 对象。生产运行期不创建 Engine、不读取配置、不反射数据库，也不调用 create_all 或初始化 schema。
- `media_writes.py` 通过现有 public notes coordinator 适配同一 UoW/Session；不修改 notes 规则，不安装全局 ORM hook。
- `schemas.py` 与 `http.py` 保持普通 Pydantic 校验和 FastAPI 的 HTTP 错误边界。

替代方案是把清理继续留在大型 series handler 中，或复用 asset_data 的类别删除流程。前者继续混合层次，后者会改变“按类别、按同剧完整引用扫描、在阶段一后独立提交”的语义，因此选择独立模块和专用应用流程。

### 保留旧校验顺序与内容算法

认证活动会员在处理器之前完成。处理器依次检查旧新 ID 相同、类别是否属于 character/scene/prop、章节是否存在、普通同剧访问权限、新素材是否属于该类别和同剧、章节内容格式及旧引用是否存在。字段保持必填普通字符串、默认 extra-ignore，不新增 trim、长度或 UUID 校验。

章节内容只解析一次并保留原列表结构。假值内容作为空列表；truthy 字符串经 JSON 解码，旧有 `JSONDecodeError`/`TypeError` 回退为空列表，解码后的非列表按格式错误处理。对象帧只修改目标类别列表；非对象帧和所有其他字段原样保留。已有新 ID 时删除该帧的全部旧 ID，否则逐项替换；计数按被改变的对象帧数。成功内容用 `json.dumps(..., ensure_ascii=False)` 的默认分隔符写回。

### 将媒体协调和章节写入留在阶段一

首次加载章节给出处理器的变换意图。应用先用已加载章节和替换后的内容调用 public coordinator；coordinator 在章节写入前按现有规则读取/锁定持久化基础并维护必要的 media/private 状态。随后在同一 Session 中只按章节主键更新 content 与 updated_at 并提交阶段一；UPDATE 影响行数不是 1 时作为通用 500 失败并回滚本阶段，不改成 409，也不添加 expected-content 条件或其他 WHERE 条件。类别引用替换不等于分镜首项媒体身份变化；coordinator 仍可按其现有规则为缺失或落后状态补齐状态、更新版本或撤销认可。

不自行刷新 ChapterLock，也不在模块内增加第二套 chapter lock。阶段一提交前异常由 UoW 回滚同一事务中的源与媒体/private 写入。任一阶段的 commit 若在数据已持久化后确认失败，结果均视为未知：返回通用 500，不自动重试；显式 GET 用来确认真实持久状态。回滚只能撤销仍未提交的当前事务，不能撤销已完成的阶段一或阶段二提交。

### 阶段二从提交后的数据库状态重新开始

阶段一提交后，调用方 Session 的 ORM 属性可能已过期。阶段二在同一 Session 的新事务中按章节主键重新读取章节，并使用该次读取的当前 series_id；随后查询该剧全部章节，从各行当前 content 收集三类素材引用。初始章节查询未命中仍是 404；阶段一已提交后的章节重读未命中则是通用 500，不能改成 404。若当前 series_id 已变化，就按它扫描和选择素材，不新增重新授权或剧集一致性门槛。坏 JSON 或非列表按源扫描规则忽略，非对象帧跳过；只把 truthy 引用加入集合，无法哈希的值保留原有异常语义。候选素材先按当前剧集、请求类别和旧素材 ID 查询；删除时仅按候选行主键执行，不追加剧集或元数据条件。

即使没有候选素材行、DELETE 影响零行，或旧 ID 仍被其他章节引用，也执行阶段二 commit；未版本化 DELETE 的零行结果保留旧 ORM 的 warning 语义。阶段二错误不会撤销已经提交的阶段一。不得缓存第一次读取的 series_id 或复用第一阶段帧快照代替第二阶段真实读取。

### 成功响应使用提交后读回

阶段二提交后，在同一 UoW/Session 中先按章节主键真实刷新章节，再按原新素材主键重新读取展示名；新素材读取不追加初始或当前剧集过滤，也不重新授权。章节或素材行缺失、刷新失败及提交后读取失败均不能伪造成功，也不能重放替换。成功响应仅为 {"message":"已替换 N 处分镜，使用 X"}。角色/道具的 name 为假值时使用 未知；场景直接使用 title，空标题仍为空字符串。

### 使用完整测试 owner schema

6 个新测试文件覆盖 domain、application、HTTP、persistence、transactions 和 boundaries；新增的 `backend/tests/chapter_asset_replacement_support.py` 提供测试工厂。SQLite 测试先创建真实完整 owner 表，再初始化模块投影；生产代码只接受调用方 Session，不调用 `create_all`。

测试至少覆盖 R01–R10：验证 422/400/404 优先级和三类别精确变换；记录同一 Session 的 coordinator、章节写入和两次 commit 顺序；在完整非空数据上比较 chapters、assets、media/private、AITask、账单/队列历史；分别构造阶段一回滚、阶段二已提交阶段一后的失败、提交后读取失败和未知提交确认；以真实 SQL 读写和独立连接检查 SQLite 可观察结果。方言编译只证明 SQL 表达，不作真实 PostgreSQL 锁或并发验收。

### 范围与路由接线

在 `app.py` 注册新 `POST` 路由，登记数从 56 变为 57；只允许改 `app.py` 与基线指定的 8 个工厂/边界测试中的必要路由期望。其余既有断言、冷导入/无副作用检查和其余模块均保持。生产无 DDL、schema 变更或新依赖，因此无需数据库迁移；回退时移除路由登记和新增模块即可。

## Risks / Trade-offs

- [阶段二失败时替换已经持久化] → 响应保持失败且不自动重试；测试和验证文档须明确阶段一 durable 与阶段二状态，并要求调用方显式读取确认。
- [两阶段间其他写入改变章节或引用] → 阶段二重新读取实际状态并限定删除范围；不宣称无全剧锁时可避免所有并发孤儿竞态。
- [历史数据含 truthy 且不可哈希的素材 ID] → 保留旧扫描异常及其阶段位置，不静默过滤或改变错误优先级。
- [Core 投影看起来像完整生产 schema] → 生产模块只使用最小列投影；真实 owner DDL 只在临时测试库创建，不把投影当迁移或生产表定义。

## Migration Plan

1. 完成领域、应用、持久化、协调和 HTTP 模块，并只在获准的工厂路径登记路由。
2. 先运行新测试和指定旧工厂/边界哨兵；失败须保留真实记录并修复后重跑。
3. 由独立 source review 和根完整回归、实际本地 HTTP/数据保全验收后，再写事后文档和推进对应任务复选框。
4. 回退不涉及数据迁移：撤销路由注册与新增模块；既有数据库结构和表内容不做清理或重写。

