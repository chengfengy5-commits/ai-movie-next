# Verification

记录日期：2026-10-08。本文按当前可核查收据记录本批本地源码、测试、loopback 与 TypeScript parser 证据；不把其中一层扩大为生产或整个平台验收。

## 冻结实现与来源

Sol 的只读源码/测试审查结论为 accepted-source-and-test-review，剩余 P1、P2 与必要验证缺口均为空；根冻结的本批代码映射含 25 个许可路径。45 项固定旧来源按 commit 23403806898550a7668a6ee7c0c457315655c39b 重读并与基线匹配。旧共享 checkout 的现场 HEAD/status 是元数据记录，不作为本批相等性门槛，也不读取其当前未跟踪内容。

- [代码冻结收据](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-series-data-code-freeze-03.json)
- [Sol 源码/测试复核](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-series-data-source-review-03.json)
- [45 项固定来源核验](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-series-data-root-fixed-source-guard-02.json)

## 根全量后端测试

根使用已安装 Python 3.12 环境、临时 basetemp 和禁用 bytecode/cache/plugin 的配置，运行 backend/tests：242 passed，exit 0；pytest 报告 8.08 秒，命令 runner 记录 8.609 秒。该结果绑定代码 freeze-03；同一收据记录 61 个 Python 源 AST 解析。没有安装依赖。

实际命令为：

<pre><code>PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=/Users/yanghaibo/data/projects/ai/haoai-next/backend/src /Users/yanghaibo/data/projects/ai/haoai-修改版/haoai.zhuwh.com/.venv/bin/python -B -m pytest -c backend/pyproject.toml backend/tests -p no:cacheprovider --basetemp=/private/tmp/haoai-next-series-data-root-full-02</code></pre>

- [全量回归收据](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-series-data-root-full-backend-02.json)
- [全量 pytest 日志](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-series-data-root-full-backend-02.log)

## 临时 SQLite loopback 与资源清理

根在 127.0.0.1 自有 listener 和临时 SQLite 上完成 19/19 场景。driver 与 server 的 63 条 HTTP 请求顺序一致，记录 666 条 SQL 事件；状态包括 200、201、204、400、403、404、422，以及故障注入/账单约束场景的 500。工厂共 27 项方法：11 项认证、四项既有个人制作/粗剪、十二项新来源方法。

验收覆盖系列/章节创建、更新、排序与删除，空章/typed 与 raw chapter、素材目录、跨用户便签认可、legacy media 初始化、A→B→A 媒体版本、聊天索引、孤立任务和真实账本外键拒绝。阶段故障用例实际证明：phase 2 提交前失败时 attempts=2、actualCommits=1，phase 1 仍耐久；提交后 acknowledgement 失败时 attempts=1、actualCommits=1，显式读取确认已提交数据，未自动重发。

authentication Session 创建/关闭/提交为 63/63/63；business Session 创建/关闭/提交为 56/56/56，rollback 10 次。五个自有 SQL/listener 监听器均移除，三个 Engine dispose，driver 和 server 线程关闭。独立进程收据确认 PID 23631 已退出，端口连接探测返回 61、随后 SO_REUSEADDR 重绑成功，自有临时目录已删除。

- [loopback 19 组与 SQL/HTTP 记录](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-series-data-root-loopback-02.json)
- [loopback 实际 HTTP body fixtures](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-series-data-root-loopback-02-http-bodies.json)
- [进程、端口及临时目录清理证明](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-series-data-root-loopback-process-02.json)

## 冻结 TypeScript parser 响应消费

根使用 Node v20.20.2 与 TypeScript 6.0.3，仅暂存冻结的 frontend/src/shared/api/contracts.ts，不修改前端文件。编译退出 0，25 个来自实际 loopback HTTP response body 的解析均通过：parseSeries 7、parseSeriesList 1、parseChapterList 15、parseStoryboardAssetList 2。fixture 的 SHA 是规范化解析后 body 的 hash，不是原始 wire bytes。该层不等于前端全量测试、浏览器或 React 验收。

- [parser 消费收据](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-series-data-root-typescript-consumer-01.json)
- [parser 实际输入 fixtures](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-series-data-root-loopback-02-http-bodies.json)

## 保留的失败历史与修复

首轮 loopback-01 整体未通过：在 14 个场景、51 条请求后，S15 前的数据库检查抛出 OperationalError；脱敏收据没有保留原始数据库错误文本，因此本记录不补写未记录的精确错误。随后独立静态分析定位到实验 schema 初始化顺序：series-data 的薄 rough_cut_drafts 投影先建表，SQLite checkfirst 不会用后续完整投影补列；修复仅调整 EVID loopback fixture 的建表顺序，25 个产品路径保持不变。修复后的 loopback-02 19 组全通过，旧失败文件继续保留。

- [首轮失败收据](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-series-data-root-loopback-01.json)
- [后续静态定位记录](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-series-data-root-loopback-failure-analysis-01.json)

本批还发生过一次误用 spec-superflow 的隔离命令：agent 报告该命令创建了额外目录 haoai-next-haoai-next，内含五项规划文件；后续清理命令因分支不存在而退出 1。根随后独立核验额外目录已不存在，唯一工作树为原 target 的 unborn main、没有 refs，保护文件及规划正文无变化。创建与清理命令是 agent 报告，根的收据记录的是事后状态；因此这里不写成“从未创建”或仍有额外 worktree。

- [root scope recovery 核验](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-series-data-root-isolation-scope-recovery-01.json)

## 尚未覆盖

- 本批使用合成账号、临时 SQLite 和本机 loopback；没有运行真实 PostgreSQL 锁/并发撤销、生产数据库迁移、真实 SMTP、Worker、队列或外部 provider。
- 没有重跑完整前端 Vitest/typecheck/build 或真实浏览器验收；只执行冻结 TypeScript parser 对实际 HTTP body 的消费。
- 指定 GPT-5.6 Sol/xhigh Grillme 独立外审仍未执行，本地源码复核不能替代它。
- 实施请求路线为 GPT-6 Luna/xhigh，源码审查路线为 GPT-6.1 Sol/xhigh；运行时模型元数据未独立核验。

## 本地任务快照

此前记录的 10/13 是文档收尾前的历史快照。之后，Sol 已接受本轮文档复核，未发现 P1/P2 或必要缺口；根的 scope guard-01 也已实际通过，核对了 394 项清单、UTF-8、受保护文件与历史、25 个代码路径、四个旧文档前缀、任务正文及 22 个链接。strict、status、apply 三项命令均退出 0。对应收据：[Sol 文档复核](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-series-data-doc-review-01.json)；[根 scope guard-01](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-series-data-root-final-scope-guard-01.json)。

当前 tasks.md 为 12/13，仅 6.1 指定的独立 Grillme 外审仍待执行。guard-01 是本次复选框更新前的快照；针对当前文档与复选框版本的后续 guard-02 尚未运行。该批仍不代表整个后端迁移完成，也未发布或归档。
