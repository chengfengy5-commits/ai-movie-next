# Proposal

## Why

个人制作记录已能读取本人续作位置并定位已核对的镜头，但尚不能在新前端保存下一次制作位置。补齐显式设置和清除，可使用户在本章结束制作时留下私有续作标记，并复用既有位置定位。

## What Changes

- 在个人制作记录的续作区域提供显式镜头选择、设置及清除；首次 revision 0 同样可设置，不依赖已保存便签或备注写能力。
- 设置依据当前章节、原图目录和本人快照的唯一稳定镜头身份；不要求摘要一致、已认可或某种便签状态。清除依据当前同章 ready 读取，允许失效旧位置及 empty/unreadable/mismatch 媒体快照。
- 新增独立可选 `savePersonalProductionResume` 能力，复用现有 PUT，正文仅 `expected_revision` 和 `resume_frame_id`。与备注保存共用一个同步门禁、读取世代和显式重读恢复规则，保留原备注精确正文、认可门槛及续作定位资格。
- 成功采用服务器完整快照并推进读取世代；冲突、拒绝或结果未知时保留位置意图供查看，必须显式重读后重新选择或确认。禁止自动重发或自动 GET。

## Capabilities

### New Capabilities

- `personal-production-resume-edit`: 本人续作位置的显式设置、清除、私有隔离和冲突恢复。

### Modified Capabilities

无。CLI 实际主 specs 库为空；前十九切片历史产物保持。

## Impact

- 仅修改 `frontend/src/shared/api/{services.ts,personalProductionNotes.ts}`、`frontend/src/features/chapters/PersonalProductionNotesPanel.tsx`、`tools/api-fixture/server.mjs`，必要时追加 `frontend/src/styles.css`；新增独立 resume helper 和专用服务、HTTP、面板、Workspace 集成测试。
- 仍为十三种业务方法：原有十二种加第十九批已引入的同路径 PUT，本批不增加端点或方法。默认 demo 仅实例内本人状态，隔离 API 仅既有配置校验通过的 loopback；fixture 扩展该 PUT 的 resume-only 分支和内存 CAS，原 GET 样本及其他接口保持。
- exact-five 来源复制、DTO 读取解码、投影、媒体身份、原备注 helper/editor、父 ChapterBrowser/Workspace 产品、旧全部测试、样本和依赖保护。README/兼容文档只在验收后追加本批，verification 事后生成。
- 不运行真实 FastAPI、数据库、Worker、队列、R2 或付费服务，不提交、推送、部署、归档。旧 PUT 有事务及维护写入，不能称为数据库纯读取；本轮仅隔离实现与验收。指定 GPT-5.6 Sol/xhigh Grillme 独立待办，离线不替代、不探测。
