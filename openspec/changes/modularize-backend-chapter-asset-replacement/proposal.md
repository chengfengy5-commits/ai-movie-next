# Proposal

## Why

章节素材替换目前位于大型 series 路由中，素材引用改写、个人媒体协调、全剧孤儿素材扫描和两次提交混在一个处理器内。将这一条完整工作流拆成独立模块，可在保持旧请求与错误语义的同时，使阶段边界、同一 Session 的媒体协调和失败后的持久化状态更容易复核。

## What Changes

- 新增 `backend-chapter-asset-replacement` 能力，封装章节素材替换的 DTO、纯领域规则、应用用例、SQLAlchemy Core 投影、媒体协调入口和 HTTP 边界。
- 保持 `POST /api/chapters/{chapter_id}/replace-asset` 的请求字段、错误优先级、帧替换规则、消息响应和两阶段提交行为；在应用工厂登记该路由。
- 增加针对领域规则、应用事务、HTTP、持久化、两阶段失败和模块边界的专用测试，并提供仅测试使用的完整数据库 owner fixture。
- 规划与代码阶段不写旧文档。后置文档阶段仅允许四份既有文档在 EOF 追加、两份新架构/验证文档，以及本变更 tasks.md 复选框调整，共 7 个路径；其他既有模块与前端保持范围外。

## Capabilities

### New Capabilities

- `backend-chapter-asset-replacement`: 章节内角色、场景或道具素材引用的替换、阶段化媒体协调、同剧素材孤儿清理及其 HTTP 兼容边界。

### Modified Capabilities

无。

## Impact

- 既有 `backend/src/haoai_backend/app.py` 和 8 个工厂/边界测试只增加该方法的登记预期；旧业务断言和冷导入副作用检查保留。
- 新增 10 个生产模块文件和 7 个测试文件，共 17 个新路径；配合 9 个既有路径，代码修改面为 26 个路径。
- 计划文件数按当前基线计算为 490 + 5 个规划文件 = 495；再增加 17 个新代码/测试路径后为 512；将来获准的 2 个事后文档会使计划数为 514。这些是范围算术，不是当前实际清点或验收结果。
- 不新增素材 CRUD、孤儿清理 API、并发 CAS、锁刷新、provider、存储或生成接口；不改变现有 notes/媒体规则，也不修改其他业务模块。
