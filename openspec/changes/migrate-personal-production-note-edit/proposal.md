# Proposal

## Why

个人制作记录已能查看、筛选和定位，但创作者仍不能在新前端保存单个镜头的文字备注与制作状态。本切片迁移冻结旧接口的单镜头保存契约，让演示和隔离 API 中的本人记录可编辑，同时明确版本冲突与保存结果未知的处理。

## What Changes

- 为可核对的当前镜头增加显式编辑器：文字备注与 `unmarked`、`needs_revision`、`approved` 三种状态；无个人记录的 revision 0 也可首次保存。待重新确认必须显式选状态，不默认沿用旧认可。
- 使用章节个人版本与媒体版本双重 CAS，只发送一个镜头的字段补丁；认可另须当前本地摘要核对通过且媒体不双空。不编辑孤立旧记录、不可识别记录、续作位置或批量镜头。
- 增加可选保存能力，默认 demo 与隔离 API 都提供真实实现；只读 provider 仍可查看。仅新增现有个人记录 URL 的 PUT，业务方法由 12 种增为 13 种。
- 保存成功验证原始响应，推进读取世代并更新本人快照；409/422 保留草稿，结果未知禁止自动重提。只有用户显式重读并重新确认后才能再次保存。
- 保留已有个人记录投影、状态筛选、续作与逐镜头定位、其他模块、原 GET 样本和媒体行为；不扩大真实后端、生产、队列、计费或播放范围。

## Capabilities

### New Capabilities

- `personal-production-note-edit`：当前章节本人单镜头备注与显式制作状态保存，含首次创建、权限与身份核对、双版本 CAS、读取世代隔离及未知结果确认。

### Modified Capabilities

无。实际 `openspec list --specs --json` 返回主规格空清单；前十八批 change 历史保持原样。

## Impact

- 产品仅允许修改 `services.ts`、`personalProductionNotes.ts`、`PersonalProductionNotesPanel.tsx` 及其测试、`styles.css`、隔离 fixture `server.mjs`；新增编辑组件、局部保存 helper、服务/真实 HTTP 合同测试与独立集成测试。完整路径白名单见 design。
- 复用冻结 SHA `23403806898550a7668a6ee7c0c457315655c39b` 的 PUT/内联 schema、个人行及媒体 CAS、旧 UI 状态语义；不复制新的 legacy-reference。GET/PUT 含媒体维护，不能声称真实数据库纯只读。
- demo 仅 services 实例内本人/章节内存保存；fixture 仅隔离内存模拟，不启动旧 FastAPI、数据库、Worker、R2 或付费服务，无新增依赖、提交、推送、部署或归档。
- 规划与后续本地实施沿既有同回合授权衔接。指定 GPT-5.6 Sol/xhigh Grillme 离线审查独立待办，本规划和本地验证均不替代它；模型请求/复用路线不等于运行时独立核验。
