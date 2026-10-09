# 第24批验证记录：个人制作记录后端模块化

记录日期：2026-10-08。本文区分实现代理定向测试、根独立后端回归、loopback 实验、静态源码审查与尚未完成的验收；一层通过不替代另一层。

## 范围与实现

本批在 `backend/src/haoai_backend/personal_production/notes/` 新增个人制作记录模块，并将应用工厂扩为粗剪和制作记录各自的 GET/PUT 四个方法。授权修改旧代码仅为工厂接线、粗剪共享 actor/error 兼容层和三个原工厂边界测试；全路径及 SHA 见 [首次实现收据](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-production-notes-implementation.json)。随后只在持久化测试中补充两个真实 SQLite 连接竞争和两个 adapter 故障场景；旧快照、完整 unified diff 与26路径当前哈希见 [补充收据](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-production-notes-implementation-validation-followup.json) 及 [逐行 diff](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-production-notes-persistence-validation-followup.diff)。补测前基线 135 项；补测后实际目标测试文件 13/13，覆盖 R0 unique winner、现存 revision CAS、提交前失败回滚以及实际提交后确认丢失时显式读取持久化真值。它们是 SQLite 验证，不是 PostgreSQL 并发证明。

## 自动化后端验证

实现阶段的六组 notes 定向测试通过 55/55；根随后独立运行全后端套件通过 139/139，日志显示 3.43 秒。补充阶段再次单跑持久化测试通过 13/13，并在同一最终测试快照上运行全后端套件通过 139/139（3.13 秒）。根独立全后端结果见 [root 139 测试日志](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-production-notes-root-backend-validation-final.log)；实现代理补测与全量日志见 [定向日志](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-production-notes-validation-persistence-targeted-final.log) 和 [补测全量日志](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-production-notes-validation-full.log)。早期测试失败与修正记录保留在实现收据中；其中一次断言漏写规范化行中的 `approved_media_revision: null`，修正的是测试期望。

根的固定来源检查、来源契约测试和最终语法/依赖清单检查均以 exit 0 结束。见 [source baseline](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-production-notes-root-source-baseline.log)、[source contract](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-production-notes-root-source-contract.log) 和 [syntax/manifest 日志](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-production-notes-root-syntax-manifest-final.log)。根在 Python 3.12.13 下静态编译检查 51 个 Python 文件，并核对 53 个 backend 代码/配置路径及五个已安装依赖版本；这些是语法与清单证据，不是生产运行验证。

Sol 最终只读源码/测试复核接受冻结的 26 个文件并关闭 V1/V2；该审查本身没有运行测试、数据库或网络。见 [最终源码审查收据](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-production-notes-code-review-final.json)。

## Loopback 实验：初次失败与复验通过

根初次运行隔离 FastAPI app、合成身份和临时 SQLite：42 条请求日志及 23 个真实 HTTP 快照通过业务检查，两次实验进程退出且重启持久化通过；但初次整体收据为 `passed: false`，因为端口释放检查失败。该失败记录保留在 [初次收据](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-production-notes-loopback.json) 和 [初次日志](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-production-notes-root-loopback-final.log)，不能用后续结果覆盖。

端口探测改动经 Sol 只读静态复核后，根重新运行 42 请求复验并通过：23 个既有 TypeScript parser 快照可解析，四个业务方法、双账号隔离、媒体/私人记录维护与来源事实守恒、进程重启持久化检查均通过；两个服务进程 exit 0，连接拒绝和 SO_REUSEADDR 绑定探测均确认端口释放，临时数据库目录清理完成。复验整体 `passed: true`，见 [loopback 复验收据](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-production-notes-loopback-recheck.json) 与 [root 复验日志](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-production-notes-root-loopback-recheck-final.log)。独立 hash 确认显示复验前后 26 个代码文件及 17 个生产相关路径均与冻结哈希一致，见 [哈希确认收据](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-production-notes-loopback-recheck-hash-confirmation.json)。这仍是合成身份和临时 SQLite 实验，不是真实 JWT、生产数据库或 PostgreSQL 验收。

## 行为边界

个人数据按可信用户和章节隔离。GET 可在同一事务协调当前媒体版本、墓碑及所有用户的受影响认可；它不是纯读取。PUT 保持严格 DTO 和旧错误优先级，支持多帧/notes/resume 组合、unknown note fields 与局部继承。双空历史认可需要单独提交修复，之后本次 PUT 强制 409 停止，不因客户端预填 revision 而继续应用补丁。响应为本次操作所构造，不通过额外 post-commit 读取模拟成功。

新协调器供未来来源写方在同事务复用，但没有迁移旧 ORM before-flush hooks 或其他写入者。SQL 投影和隔离 SQLite 实验不等于生产 schema/migration；方言编译不等于真实 PostgreSQL 锁、并发或撤销测试。真实 JWT、password version/session 撤销、会员与完整团队管理、其他业务、Worker、队列、provider、生产部署均未完成。本批没有重跑前端历史 787 项、前端 build 或浏览器验收。

## 复现与待办快照

使用已安装的 Python 3.12 及仓库固定依赖，可在仓库根执行：

```sh
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=backend/src python3.12 -B -m pytest -c backend/pyproject.toml backend/tests -p no:cacheprovider
```

该命令说明可复现的本地后端回归方式，不表示本轮安装依赖或使用生产数据库。根的代码验收收据记录后端回归 139/139、51 个 Python 文件语法检查、53 条代码/配置清单、固定来源检查及 42 请求 loopback 复验均通过；Sol 最终源码/测试只读审查接受冻结的 26 个文件。根代码收据生成时任务快照为 9/12；本次文档收尾后，任务复选框为 10/12，4.2 文档复核与最终一致性收口仍待完成。指定 GPT-5.6 Sol/xhigh Grillme 当前离线未执行；实现与审查的请求模型路线分别为 GPT-6 Luna/xhigh 和 GPT-6.1 Sol/xhigh，runtime 元数据均未独立验证。不得把本地测试、静态审查或隔离实验表述为真实生产验收或发布授权。
