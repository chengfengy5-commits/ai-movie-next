# Proposal

## Why

React＋TypeScript 已能读取制作便签、修改镜头备注和保存续作位置，但隔离后端只有粗剪及剧集访问策略。将完整个人制作记录业务迁到分层模块，并在同一工厂组合，才能继续替换旧后端而保留私有记录与媒体版本语义。

## What Changes

- 实现旧 `GET`、`PUT /api/chapters/{chapter_id}/personal-production-notes` 的完整便签、状态及续作补丁；同一个 PUT 同时支持局部镜头、多镜头和显式续作清空，保持旧 DTO 与错误。
- 分离纯媒体身份与认可规则、应用用例、工作单元端口、SQLAlchemy 持久化及 HTTP；包含媒体版本初始化、单调递增、删除墓碑、同章各用户认可撤销与双空历史认可维护。
- 将可信 actor 与业务错误提取到共享基础层，保留粗剪原公开接口；统一 `create_app` 组合四个已有业务方法，缺少必需端口时全部 503、零 Session。
- 维护独立的私有 revision CAS、唯一竞争和维护提交边界，保留本人数据隔离；测试及实际 loopback 检验保存、冲突、媒体变更、另一用户隔离与重启持久化。

## Capabilities

### New Capabilities

- `backend-production-notes`: 新隔离后端的个人制作记录完整读写与媒体版本维护。

### Modified Capabilities

无已归档主规格；历史变更保持原验收快照，当前工厂组合从两方法扩展为四方法。

## Impact

仅 `/Users/yanghaibo/data/projects/ai/haoai-next`。新增 shared 与 personal_production.notes 包及六组测试；修改现有 app、粗剪 ports/errors 的兼容基础类型和三个工厂边界测试。其余149份旧代码、141份历史文件及29份固定旧来源守恒。四个历史说明按许可追加，新说明及 verification 记录实际证据。沿用既有依赖，不安装、不读旧环境、不调用供应商、不碰生产；真实认证、PG运行及其他业务模块继续独立迁移。指定 GPT-5.6 Sol/xhigh Grillme 尚未执行，保留独立待办且不阻本地开发。
