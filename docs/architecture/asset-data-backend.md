# 角色、场景、道具与分镜素材后端模块

## 范围

`haoai_backend.asset_data` 在隔离 FastAPI 后端承接旧系统十五个来源素材方法：角色、场景、道具的列表、创建、更新和删除，以及分镜素材的创建、更新、删除。原有章节素材目录 GET 保持在 `series_data`。模块接入显式应用工厂，不扩展素材上传、生成、模糊合并、批量操作或媒体下载能力。

## 分层

- `domain.py`、`naming.py`、`references.py`：纯数据规则、名称与章节原始引用处理。
- `schemas.py`、`presentation.py`：输入 DTO、旧字段 presence/null 语义及响应投影。
- `application.py`、`ports.py`：可信身份、授权顺序、用例协调和 UoW 端口。
- `persistence.py`、`tables.py`：同一请求 Session 上的 SQLAlchemy Core 查询与写入最小投影。
- `media_writes.py`：在明确的来源写入点调用个人媒体协调器，不新增全局 ORM hook。
- `http.py`、统一 `app.py`：注册路由并组合显式依赖。

## HTTP 方法

| 方法 | 路径 | 成功状态 |
| --- | --- | --- |
| GET | `/api/series/{series_id}/characters` | 200 |
| POST | `/api/series/{series_id}/characters` | 201 |
| PUT | `/api/characters/{character_id}` | 200 |
| DELETE | `/api/characters/{character_id}` | 204 |
| GET | `/api/series/{series_id}/scenes` | 200 |
| POST | `/api/series/{series_id}/scenes` | 201 |
| PUT | `/api/scenes/{scene_id}` | 200 |
| DELETE | `/api/scenes/{scene_id}` | 204 |
| GET | `/api/series/{series_id}/props` | 200 |
| POST | `/api/series/{series_id}/props` | 201 |
| PUT | `/api/props/{prop_id}` | 200 |
| DELETE | `/api/props/{prop_id}` | 204 |
| POST | `/api/storyboard-assets` | 201 |
| PUT | `/api/storyboard-assets/{asset_id}` | 200 |
| DELETE | `/api/storyboard-assets/{asset_id}` | 204 |

## 兼容和事务边界

DTO 保留字段省略、显式 `null`、额外字段及旧别名规则。创建分镜素材不自动写入章节 `content`；更新按首次赋值意图确定来源与派生字段，只有二者均未变化的真正 no-op 才不发 DML 或更新时间。显式同值 name 仍可按旧规则修复过期 canonical/falsey aliases。签名等价不推进已有媒体版本或撤销认可；实际 `image_url` 字段变化仍调用协调器，媒体身份确有变化时才推进版本或撤认，缺失状态可初始化 R1。纯元数据变化不强制初始化媒体状态。

普通素材操作按可信 actor、当前剧集作者/团队关系及既有认领规则授权。删除权限依据可信数据库字段，不采信请求字段。分镜素材授权按资产 `series_id`，媒体协调则使用实际关联章节 `chapter_id`；不额外引入跨表归属校验。

类别 DELETE 清理同剧章节中目标类别的原始引用（包括重复引用）；受影响章节按稳定顺序锁定，并在章节来源内容 DML 前完成同 Session 媒体协调。分镜来源变更也在相应源 DML 前按明确阶段协调；签名等价不推进已有媒体版本或撤销认可，实际媒体身份变化时才维护版本与认可，缺失状态可初始化 R1，纯元数据变化不强制初始化。分镜创建不自动写入章节 `content`；只有分镜素材 DELETE 保留原始章节引用并写入墓碑，不自动关联到章节或续期锁。显式协调仍可能为其它既有引用初始化缺失状态。每个写请求只提交一次，提交前任一阶段失败整体回滚。

成功写入提交后，应用从同一 UoW 重新读取目标行再构造响应。提交后读取或确认失败可能留下持久化写入但返回错误；模块不自动重发，调用方须显式读取核实。Core 表仅为本模块所需查询投影，临时 SQLite 验收不构成生产 schema 迁移或真实 PostgreSQL 并发证明。

验收和未覆盖范围见[本批 verification](../../openspec/changes/modularize-backend-asset-data/verification.md)及[模块 API 兼容说明](module-api-compatibility.md#第二十七批当前后端迁移契约)。
