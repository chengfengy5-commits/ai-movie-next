# 第 20 切片验证记录：个人续作位置编辑

本记录对应 `migrate-personal-production-resume-edit`。证据来自隔离的 `haoai-next` 工作区、自动化测试、本地 demo/loopback fixture 浏览器检查和根代理检查；这些层次不代表真实生产后端验收。

## 行为与边界

位置设置/清除复用既有 `/api/chapters/{chapter_id}/personal-production-notes` PUT，正文仅含 `expected_revision` 和 `resume_frame_id`。设置以完整当前章节、原图和服务端快照确认同一唯一 raw ID、归属、位置和 `source_valid`；不要求摘要匹配、认可状态或便签存在。清除可处理已经失效的旧位置，但仍要求当前同章读取有效。位置不是认可，也不保证可以定位。

备注保存和续作写入共享用户/章节 revision 及同步操作门禁。成功时采用服务端返回的完整 canonical 快照，因此服务器对其他便签、媒体元数据或认可状态的维护也会被保留；本切片没有媒体 revision CAS。拒绝或结果未知会保留具体意图并要求显式重读，再由用户重新选择；客户端不会自动重发 PUT 或自动 GET。demo 只在当前 services 实例中维护私有内存状态，页面重载不代表持久化。

## 自动化、类型与构建

下列根代理命令均在最终代码冻结版本执行，均退出码 0。

| 执行者 | 检查 | 结果与证据 |
| --- | --- | --- |
| root | `npm --prefix frontend test -- --no-file-parallelism` | 48 个测试文件、735/735 通过、无 skip、未提高 timeout。[完整测试日志](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-production-resume-edit-root-full-tests-final.log>) |
| root | `npm --prefix frontend run typecheck` | exit 0。[typecheck 日志](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-production-resume-edit-root-typecheck-final.log>) |
| root | `npm --prefix frontend run build` | exit 0；Vite 构建 74 modules。[build 日志](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-production-resume-edit-root-build-final.log>) |
| root | `node --check tools/api-fixture/server.mjs` | exit 0。[fixture 语法日志](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-production-resume-edit-root-fixture-syntax-final.log>) |
| root | `node --test scripts/source-baseline-contract.test.mjs` | 3/3 通过。[source contract 日志](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-production-resume-edit-root-source-contract.log>) |
| root | `node scripts/verify-source-baseline.mjs` | 固定 exact-five 5 项通过；改动行敏感模式未发现 console、浏览器存储写入、literal URL、新媒体元素或直接 fetch。[exact-five 日志](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-production-resume-edit-root-source-exact-five.log>)；[改动行扫描收据](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-production-resume-edit-sensitive-pattern-check.json>) |

实现代理在最后一项 V1 证据修正后单独重跑了新 Panel 测试：19/19 通过。[Panel 定向日志](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-production-resume-edit-panel-v1-final-retry.log>)。第一次该定向运行有 1 项失败，是测试把脏备注期间禁用的续作按钮误期望为启用；修正后通过，不是产品缺陷。

实现代理的单文件 HTTP 定向为 10/10，[日志](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-production-resume-edit-http-targeted.log>)；Workspace 集成由 fixture 代理定向运行，为 5/5，[本轮日志](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-production-resume-edit-integration-fix2.log>)。最终根全量测试覆盖了当前版本，不把代理定向运行等同于根全量结果。

GPT-6.1 Sol / xhigh 请求路由的独立只读复核接受了当前源与测试：V1–V4 证据关闭，无未解决 P1/P2。[复核收据](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-production-resume-edit-sol-code-review-accepted.json>)。模型 runtime 未独立核验；此复核不代替指定 Grillme。

## 本地浏览器与 API audit

root 记录了 14 个本地浏览器场景，覆盖 1280×720 桌面、390×844 移动视口、键盘设置/清除、备注脏状态互斥、R0 首次保存、读取恢复、两账号私有状态及任务/剧集回归。API 场景分为 normal、revision-conflict、invalid-applied、body-timeout、inflight-exit 和 maintenance 六个阶段；观测还包括无效成功响应、超时、未验证位置清除及服务器维护。浏览器收据中的 6 个产品文件哈希均与最终冻结版本一致。浏览器未验证 403 或 422；这类状态不作为浏览器结果记录。

- [browser 观察与实际截图清单](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-production-resume-edit-browser-observations.json>)： [桌面续作设置](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-production-resume-edit-demo-desktop-saved.jpg>)、[移动端 revision 0 首次保存](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-production-resume-edit-demo-mobile-r0-saved.jpg>)、[409 冲突](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-production-resume-edit-api-conflict.jpg>)、[未验证位置已保存但不能定位](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-production-resume-edit-api-maintained-saved.jpg>)。
- API 审计包含 217 条 loopback 记录：92 条业务请求、107 条 OPTIONS、18 条既有 PNG 请求；13 种获准业务方法模式中实际观察到 9 种，unexpected 为 0。[API audit](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-production-resume-edit-api-audit.json>) 是本地 fixture 浏览器记录，不是全局出站网络审计。
- 10 次单独保存/清除增量各为 1 PUT + 1 OPTIONS，0 次 notes GET、0 次 PNG。普通重开观察到 2 次 notes GET + 2 次 OPTIONS，与开发环境 StrictMode 重挂载一致。冲突、无效 200、body timeout 与维护阶段的显式重读分别观察到 1 次 GET；这些操作没有因失败自动重发。
- 延迟 PUT 的浏览器关闭/注销/新用户流程单列在混合身份转换中，包含旧 PUT 的 server response。浏览器取消不证明旧客户端 Promise 忽略 abort 后实际结算；该层证据来自 Panel 和 Workspace 自动化测试中真实 settle 原 Promise 并 await act。不能把浏览器晚到 server 200 当作同一证明。
- 服务器维护示例中，未验证目标可保存但没有定位入口，仍可清除；另一目标保存后，其他便签文字被保留，旧认可撤销，并由显式读取确认 canonical 状态。这里不声称有媒体 CAS。

## 失败与修复记录

早期收口期间发生过旧 `PersonalProductionNotesPanel.test.tsx` 被误改到保护范围。根的保全检查发现后，使用本地会话实际修改历史重建原文件并验证 SHA 与 slice 19 基线完全一致；本批续作测试在新文件 `PersonalProductionResumeEdit.test.tsx` 中。[路径偏差记录](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-production-resume-edit-test-path-violation.json>)、[恢复核对](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-production-resume-edit-panel-test-original-reconstruction.json>)。修复后的旧文件 SHA 为 `761e8bc69e6be61d1d5b1ec8645ccff443a86483cdb2d75c704f8d01b460ad4a`。

早期的 helper、Panel 和 fixture/Workspace 定向也出现过身份边界、测试夹具及异步等待失败；逐项修正后由上述最终全量覆盖。helper 曾因非目标帧的 canonical ID 为空而拒绝合法目标 A（目录 ID 为 `[A, B, B]`）；修复后仍要求目标唯一、归属和 `source_valid` 有效。另一项修复移除了 resume-only R0 对未保存便签列表的错误解锁。最后的 Panel V1 补测曾被脏备注门禁和同一 act 内两次选择的相同最终值掩盖；现在在干净的 R2/R3 ready 状态下分别调用旧选择、保存、清除闭包并逐项断言状态和请求计数不变，再测试旧备注处理回调。[V1 首次失败日志](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-production-resume-edit-panel-v1-final.log>)、[修复后日志](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-production-resume-edit-panel-v1-final-retry.log>)。

body-timeout 浏览器阶段曾请求 20 秒的 CUA 选择器等待，但工具约 3 秒后提前返回 selector deadline；root 随后独立观察到产品原有 15 秒期限形成的 unknown 结果。产品 timeout 未更改，工具等待期限也不作为产品失败。

## 保全、范围与待办

代码冻结检查覆盖 249 个 inventory 文件，其中 114 个代码文件；231 个既有不可变文件、117 个历史文件、49 个窄保护文件和 13 个冻结来源均无变化，legacy HEAD/status 未变，DTO 原读取/parser 前缀保持。6 个浏览器产品文件哈希与最终代码冻结哈希一致。来源扫描只针对本批改动行，不是全仓安全或网络审计。[代码冻结清单](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-production-resume-edit-code-frozen-hashes.json>)、[保全核对](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-production-resume-edit-conservation-browser-accepted.json>)。

在最终任务勾选前，root 对文档快照实际执行了 OpenSpec strict、status、apply（均 exit 0；apply 快照为 ready 9/12）、文本扫描和保全检查：237 个文本文件、13 个固定排除项、issues 0；250 个 inventory 文件/114 个代码文件，231 个不可变文件、117 个历史文件、49 个窄保护文件和 13 个冻结来源均无变化。Sol 随后接受了三项文档窄修，确认文档问题与链接缺口均为 0。[Sol 文档复核收据](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-production-resume-edit-sol-documentation-review-narrow-accepted.json>)；root 检查见 [strict 收据](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-production-resume-edit-root-strict-documentation-narrow-repaired.json>)、[文本收据](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-production-resume-edit-text-check-documentation-narrow-repaired.json>)、[保全收据](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-production-resume-edit-conservation-documentation-narrow-repaired.json>)和[根检查汇总](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-production-resume-edit-root-post-documentation-checks.json>)。本地任务当前为 11/12；这表示 `tasks.md` 的勾选状态，不是最终 checkbox 版 apply 命令的结果。root 还需对本次收口后的文件重跑 strict/status/apply、文本和保全检查；[最终收口收据](</Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-production-resume-edit-final-acceptance.json>)当前仍待更新。唯一未完成任务为指定 GPT-5.6 Sol / xhigh Grillme；当前离线、未执行，不启动、探测、替代或降档。请求的 Luna 实施与 Sol 复核路由没有作独立 runtime 元数据核验。

本地未运行真实 FastAPI、PostgreSQL、Worker、队列、R2、provider 或付费流程；无生产发布、提交、推送或归档。临时 loopback 服务已停止，本次自有浏览器标签已关闭，端口检查无自有监听。
