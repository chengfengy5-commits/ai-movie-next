# 任务观察与取消控制验证记录

## 阶段快照与结论

本验证记录基于文档作者在指定 EVID 目录创建的 draft-02，在原 draft-01 冻结快照上续接根 TCP04 和原 TS parser 实际收据；原 draft-01 七文件原样保留。授权的七路径文档现已安装并经独立文档审查接受；普通 OpenSpec strict/status/apply 已针对 11/13 状态实际运行并全部 exit 0；随后根依据该通过结果勾选 5.2，当前 tasks 为 12/13，最终 12/13 状态 CLI 复核已通过（PID 16144、16145、16146；apply total 13、complete 12、remaining 1），见[final 12/13 ordinary OpenSpec 收据](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-root-task-observation-ordinary-cli-final12of13-01.json)。目标为 `/Users/yanghaibo/data/projects/ai/haoai-next`，固定兼容来源为 `23403806898550a7668a6ee7c0c457315655c39b`。普通 OpenSpec 规划和历史原文保留；planning 中的旧日期、82 注册面和 staging 描述属于其原始快照，不重写成当前事实。

当前完整 31 文件 source gate 已通过，根实际全量后端 617 项通过。原 82 方法保留，新增八个 GET 和一个 POST，总登记 91；这不是 91 方法 TCP 验收。真实 JWT/SQL/TCP04 和实际 body→原 TypeScript parser 已通过；七路径文档安装和独立文档审查已完成。普通 OpenSpec strict/status/apply 在 11/13 状态实际运行并全部 exit 0（PID 5333、5334、5335；apply 为 total 13、complete 11、remaining 2）；随后依据该结果勾选 5.2，当前 tasks 为 12/13。随后根在 12/13 状态重跑普通 OpenSpec strict/status/apply 并全部 exit 0（PID 16144、16145、16146；apply total 13、complete 12、remaining 1），见[final 12/13 ordinary OpenSpec 收据](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-root-task-observation-ordinary-cli-final12of13-01.json)。6.1 指定外审仍离线 pending。

## 源码与测试层次

| 证据层 | 本阶段实际结果 | 限制 |
| --- | --- | --- |
| 作者专项测试 | 查询 Q 五项；取消 C 的分次测试；S 最终 22 项；旧十文件整合日志 3/15/1 项结果 | 由已有收据/日志转述，不把重复运行累加为独立节点，不代根验收 |
| 独立源码审查 | 完整 31 冻结组成、旧十真实 diff、原测试业务/cold/private 哨兵、first/unique 与取消语义接受 | 静态审查及以前分阶段审查的绑定保留，不冒称全部来源新读或真实 HTTP |
| 根全量后端实际运行 | 617 passed、20 warnings，exit 0，28.22 秒 | 含本地 SQLite/ASGI/应用测试，不代联合真实 JWT/SQL/TCP 或生产环境 |
| 根语法/保护实际检查 | Python 3.12.13；242 文件 AST 和内存 compile；all592 前后相同；固定55字节/metadata复核 | 固定55字节复核不等于本次新读全部55正文 |
| 根联合 JWT/SQL/TCP | TCP04 实际 completed；29 attempted/29 completed，九个新增方法有正例 | 含拒绝/故障和旧接口 smoke；不代表 29 个不同 API、91 方法 TCP 或生产环境 |
| 实际响应→现 TS parser | Node 20.20.2 实际 exit 0；原 parser 消费真实 page_size10 body | total13/page1/page_size10/taskCount10；只证明局部 parser，非完整 React/UI/build |

source gate 的实际读取分层包括新读 app 全文、旧十 diff 全部 hunk、必要旧登记测试窗口，以及先前已独审接受且哈希相同的 shared/Q/C/S 正文；C 两文件仅最终 LF 变动以 AST 和精确字节关系接受。旧九测试没有 skip/xfail 或断言删除，其原业务函数 AST 在允许的注册数量/集合/名称变动之外保留。整合日志报告的 3/15/1 passed 不是 19 个已证明互异节点；auth fake context 和业务 factory 的分离列表也不证明联合成功时序。

关键收据：

- [完整31/all592冻结与独审授权](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-root-task-observation-final-code31-freeze-and-review-authorization-01.json)，SHA256 `66721f40eca0018c6f67995b8af3f09817853648171c57eb77b0f21287b81bb1`。
- [完整源码独审](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-task-observation-final-source-review-01.json)，SHA256 `b4dc6f697abb7950f9b757f528601e32093f47ec0c264396ef7efb8f430573a5`，决定 `accepted-complete31-static-source-gate`；requested route 与 runtime 元数据未独立核验的边界保留。
- [根全量实际运行](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-root-task-observation-fullbackend-run-01.json)，SHA256 `bfda1109ae374fb29374a0d8d8ca6fa69f8aae8b87107a00f4078d8c716b7202`，绑定[原始日志](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-root-task-observation-fullbackend-run-01.log)。早期 freeze 中的 not_run 字段是当时状态，后来的这份 actual receipt 才证明根全量运行。
- [本批实际基线、旧十/旧四完整原文字节副本及路径图](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-task-observation-baseline-01.json)。

根记录的实际 argv 为：

```text
/Users/yanghaibo/data/projects/ai/haoai-修改版/haoai.zhuwh.com/.venv/bin/python -B -m pytest -p no:cacheprovider -q backend/tests
```

根运行子 PID 为 35021，实际 wait 后 exit 0；日志末尾为 `617 passed, 20 warnings in 28.22s`。唯一自有目录 `/private/tmp/haoai-next-task-observation-root-pytest-tyxr0ymy` 在子进程终态后已清理。该资源结论覆盖这次根 pytest 的自有资源；TCP04 以自己的 listener/thread/client/连接和 strict-AND cleanup 收据单独证明，不描述全机网络状态。

## 已接受的本地语义与边界

查询使用供应的业务 Session，八 GET 无业务 commit/flush/DML，结束 rollback/close。认证维护仍有自己的 SQL/提交，公开 authenticated False/True 的上下文退出后才返回 actor；TCP04 用共享 request_id 的实际时序确认认证 commit/close 成功先于业务读取。详情先查任务，superuser 仅本请求 SQL；团队权限、目标成员与重复 membership 首次主键字段语义、原 JSON 错误及宽 int 保留。

本人任务回执按 firstBillingUnit/最早单元返回十三键，提交回执按原 idempotency_key query/uniqueBillingUnit 返回七键。完整 owner DDL 与 FK ON、非空任务/提交/计费/credit 自 FK/执行/result/media/private/auth 行的真实 SQLite 专测覆盖查询守恒，薄 Core 表不冒充完整 schema。查询和取消专项的来源语义由分阶段独审接受；根全617包含当前最终测试，不能把专项作者日志当这次根的真实命令。

取消先 set truthy 信号，再经显式独立连接只 UPDATE status。终态不 writer，row0 正常 commit/200；claim、updated_at、其它取消 metadata 和财务行保持。独立 B 已提交终态/删除与 A 写入的边界、实际 SQL/precommit rollback、actual commit 后 ack unknown 与后续 GET/no replay 由当前 SQLite 专测保留。它们不证明 PostgreSQL、Worker/退款；TCP04 已单独记录真实物理连接、Result 消费时点、driver/ASGI/SQL 时序和 strict-AND cleanup，结果见后文；业务/权限/JSON 更广的专项边界仍由对应源码和全617证据承担。

固定55字节及 metadata 已由根复核，未声称本次新读55全文。根运行收据完整记录 live legacy HEAD/status/tracked/index，tracked 数为 478；该 ambient 信息没有要求与早期 baseline 相等。目标 all592、固定来源与旧 history 的精确保护各按各自记录评估，不把 live legacy 的背景变化当本批源码漂移。

## 失败历史与处置

- Q 三次 launcher 因 noclobber 重定向失败，均未启动 pytest；其 exit 1 不算产品测试失败。三份自有空目录随后精确 `os.rmdir` 清理，原日志保留。[失败记录](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-task-observation-query-Q-launcher-failures-585608c56c.json)、[清理收据](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-task-observation-query-Q-cleanup-receipt-22742f6718.json)与[Q 独审](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-task-observation-query-Q-independent-review-01.json)分层记录。
- C 的早期五适配器测试有一条 cache 写入受限警告，后续使用 `no:cacheprovider`。三个应用集成用例曾因测试缺少 `TrackedSession` import 而失败，修正后分次通过；C run-log 是对应工具输出的转录，早期五项未在最终三项命令重跑，不能写成一次统一九项根运行。[C 转录](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-task-observation-c-cancellation-run-log-01.json)与之后根实际全617不同。
- S 曾有一次 1 failed/12 passed 和一次 7 failed/14 passed，修正测试行为/HTTP 测试适配后最终两文件 22 passed。混合历史 run03 的 argv 与 21 个结果元数据未由原生事件重新独核，保留该限制；最终两文件22项日志绑定一致。[S 独审及失败日志索引](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-backend-task-observation-s-independent-review-01.json)。
- 第一次分批 Git whitespace check 因 `cancellation.py` 与取消测试末尾空行返回 exit 2，commit 未执行。获准后各删除恰好一个末尾 LF，AST 不变，才完成正常提交。[失败收据](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-root-task-observation-three-lanes-git-failed-attempt-01.json)、[精确 LF 修正](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-task-observation-c2-whitespace-fix-author-receipt-01.json)。
- helper01 尚未运行，独审 `changes-required` 指出四个验收脚本问题：SQL event 与 UPDATE 计数索引单位混用、POST/GET owned-read 阶段混计、竞争 B 与 writer A 物理连接可能复用、失败清理未拥有实际 Session 且缺 strict AND。该结论未指控目标 source31 产品缺陷。helper02 后续完整独审又发现 N01/N02，修订03/04分别重审后才进入实际运行；这些过程不改 source31。[helper01 独审](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-task-observation-root-helper-independent-review-01.json)、[helper02 修订授权](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-root-task-observation-helper02-revision-authorization-01.json)。

- helper02 静态独审仍 `changes-required`：N01 把 `Session.in_transaction()` 的 bool 以 `is not None` 判断，导致已关闭 Session 被误判活跃；N02 把正常取消 POST 与另一个配置应用的 missing-writer 503 同路径请求混计。修订03关闭两项，保留 strict AND 和独立 503 场景；该阶段不运行旧02，不指控产品源码缺陷。[helper02 独审](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-task-observation-root-helper-independent-review-02.json)、[helper03 独审](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-task-observation-root-helper-independent-review-03.json)。
- 根实际 helper03 尝试 14 个请求，13 个 completed；第14个缺 query 探针把带首尾空格的原 key 放进 header，httpx/h11 在客户端以 `LocalProtocolError` 拒绝，未取得该探针 HTTP 响应。失败记录 `execution=failed`、cleanup strict AND 为 true、errors 为空，原记录不改。helper04 仅把该探针 header 转成合法值并增加零 business owned-read 断言；正常 query、fixture 和 SQL 检查仍用原未 trim key。[根失败03原记录](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/root-task-observation-tcp-25383bd479/root-tcp-acceptance-run.json)、[helper04 独审](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-task-observation-root-helper-independent-review-04.json)。04 后来的完整成功单独留证，不覆盖这次失败。

## 代码提交与推送

十个新增查询/取消/HTTP文件以中文提交“实现任务查询与取消模块并补齐专项测试”，commit `0443c02576c4660bf72151c3c4464a36aae5198d`。[提交收据](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-root-task-observation-three-lanes-git-commit-01.json)、[正常推送及远端核验](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-root-task-observation-three-lanes-git-push-01.json)。app 和九份旧登记测试以中文提交“接入九个任务观察接口并验证注册与权限边界”，commit `0e490ad35e10d9d78ff61fb09b99a9bdfb79ca81`。[提交收据](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-root-task-observation-old10-integration-git-commit-01.json)、[正常推送及远端核验](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-root-task-observation-old10-integration-git-push-01.json)。两次均普通 push，无 force；这些收据不证明文档提交、部署或联合 TCP/TS 已完成。

## 根联合 JWT/SQL/TCP 实际结果

当前状态：通过。根受审 helper04 的实际子 PID 为 84674，wait 后 exit 0；原启动/终态记录见[根进程收据](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/root-task-observation-tcp-4a0d9e7253-root-process.json)，完整[运行记录](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/root-task-observation-tcp-4a0d9e7253/root-tcp-acceptance-run.json) SHA256 为 `ef5cdd209623ab57d538c6856e8a9bc6abd56aa1c08173b776287878412932fb`。[helper04 独审](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-task-observation-root-helper-independent-review-04.json)只提供静态运行资格，实际通过由这次根运行证明。

根进程收据绑定指定 Python `-B`、冻结 helper04、目标 repo、EVID、`/private/tmp` 和唯一 run-prefix `root-task-observation-tcp-4a0d9e7253` 的完整 argv，以及原 stdout log hash。运行内 Python 版本为 3.12.13；十个阶段全部 completed，包括完整 fixture、真实应用构建/91注册面、九方法/拒绝合同、取消 SQL/时序、owner 守恒和资源终态。

| 记录 | 实际数量/结果 | 单位 |
| --- | --- | --- |
| HTTP 请求 | 29 attempted、29 completed，状态全部符合 expected_status | 多场景请求；覆盖九个新增方法正例及拒绝/故障、旧接口 smoke |
| SQL events | 176；包含一条预期 SQL fault 的 error 记录 | 含 fixture snapshot、认证及业务/取消，不都算业务读或写 |
| stages | 10，全部 completed | 验收阶段 |
| Sessions | 51，51 次 close attempted/51 completed，正常验收 exactly once | 认证和业务/旧 smoke 的 Session，总数不当事务数量 |
| 独立事务 context | 9，均终结并 close 成功 | 七个取消 writer、两个竞争者 B；不含认证 Session 提交计数 |
| 信号 | 7 次 set attempted/7 completed | 终态未取信号/未 writer |
| 物理 DBAPI 标签 | 3 | 竞态时业务、B、writer A 实际互异；跨场景可复用已归还连接 |
| 自有 TCP server/client | 各3套，全部关闭 | 主应用、missing-auth、missing-writer 三配置，分别记录监听/thread/client/port |

九个新增方法均有正例：任务与提交回执、page_size10 列表、本人请求详情、审核 counts/status、批量优化 running/status 和取消。拒绝/兼容请求覆盖过期会员的 account 回执 200 与 active 列表 403、跨用户回执 404、团队缺 `view_tasks` 403、缺 query 的合法 header 探针 422、坏 JWT 401、缺 auth/writer 配置 503；旧 auth/me、teams/my 与私人 rough-cut 的 smoke 分别留证。这些实际 HTTP 场景与更广的 SQL superuser、目标成员、坏 JSON 和宽 int 专项测试/源码证据分开，不补写不存在的 HTTP 场景。

共享 request_id/sequence 记录证明认证 commit/close 成功先于业务读，真实 owner-task 的 Result 已由原 UoW 消费后才注入竞争事务。queued/processing 正常 status 写入、终态跳过、两种 row0、SQL failure/precommit failure 的预期 500/rollback，以及真正 commit 后 ack unknown 的 500 均完成。随后独立 GET 读取 durable status，ack 分支累计 UPDATE 仍为1，没有自动重放；信号先 set，失败不撤回。

完整 owner 24 张表在前后快照均非空，共57行→56行，`comparisonCompleted/fullOwnerExpectedDiffMatched=true`。精确差异是三个取消任务 status 为 cancelling、竞争者把 `task-race-update` 的 status 改为 completed，以及竞争者删除 `task-race-delete` 一行；updated_at、claim、财务、其它字段/行按 expected diff 守恒。两份实际[前快照](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/root-task-observation-tcp-4a0d9e7253/snapshot-before.json)和[后快照](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/root-task-observation-tcp-4a0d9e7253/snapshot-after.json)的 hash 由运行记录绑定；不把 B 已提交变化归为 writer A 的修改或 rollback 能撤销的内容。

cleanup `completed/allOwnedResourcesReleased=true`，errors 为空。自有 Session/事务/连接/client/server、listener/thread/socket/端口探针终态、Engine 连接归还与 dispose、owner database close 和唯一自有目录 `/private/tmp/root-task-observation-tcp-4a0d9e7253-0bumofbq` 删除共同通过；attempt 与 successful completion 分开。根进程收据确认目标 all592 前后全等。该结论只覆盖此自有 SQLite/loopback 运行，不宣称全机无外连、PostgreSQL 并发、Worker 已停、退款或生产部署。

<!-- ROOT_RESULT_FILLED: JWT_SQL_TCP_ACTUAL. root TCP04 completed/child84674 waited exit0；29请求、9新增方法、10stages、176SQL、51Session、9独立事务、7信号分别计数；strict-AND owned cleanup passed；原03失败记录保留。 -->

## 实际 body 与原 TypeScript parser 结果

当前状态：通过。根实际 Node `v20.20.2` 子 PID 为86299，wait 后 exit0，stderr为空；[原 parser 运行收据](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-root-task-observation-actual-body-parser-run-01.json) SHA256 为 `5ffb77d556acc660d508e3cb7e9db27b9054095655ecc9607b7c2174f6e041ae`。argv 绑定冻结 bridge01、当前 repo、TCP04 原记录、真实 body 和 `--requested-page 1`，stdout 为 `parsed_actual_tcp_body`。

输入为取消场景前 request-0004 的真实分页[4972字节正文](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/root-task-observation-tcp-4a0d9e7253/actual-task-list-page-size-10.body)，body SHA256 `62009bd5c4fe8b0898d8bf9e9fb68de53197e7ad126e918e08ad4df567638ca1`，与 TCP04 artifact 和 parser receipt 一致。现有 `parseMyTaskPage(body, requestedPage)` 实际返回 total13、page1、page_size10、taskCount10。原 `frontend/src/shared/api/myTasks.ts` 与 `contracts.ts` 两份源 SHA 保持并写入 stdout/冻结记录；bridge01 本身 hash 不变。作者只对输入和现行源复核字节，没有重跑 Node。

这证明原 TS parser 能消费该真实响应。内存 transpile/局部消费不等于完整工程 typecheck、React 渲染、UI/browser 或完整前端 build。

<!-- ROOT_RESULT_FILLED: ACTUAL_BODY_TS_PARSER. original parser actual exit0/child86299 waited；真实page1/page_size10/body/source绑定通过；仅局部parser，完整React仍待。 -->

## 文档、普通 OpenSpec 收口与剩余工作

本次按[精确续接授权](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-root-task-observation-final-runtime-docs-draft-authorization-01.json)，在新 draft-02 仅新建四个 EOF append 草稿、一份架构、一份本验证记录和一份 author freeze，共七个 EVID 文件；原 draft-01 七文件与其冻结 hash 全部保留；目标仓库、原文副本、tasks checkbox、旧 history 均不写。四旧文档安装时须保留原完整字节前缀；六正文 hash、输入证据 hash、只读层和 pending 状态由同目录 `author-freeze.json` 绑定。以上记录的是 draft-02 冻结时尚未安装、尚未独审的状态；根随后在 11/13 状态下实际运行 strict/status/apply 并全部通过，之后勾选 5.2，当前为 12/13；最终 12/13 状态的 strict/status/apply 复核已全部通过（PID 16144、16145、16146；apply total 13、complete 12、remaining 1），见[final 12/13 ordinary OpenSpec 收据](/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-root-task-observation-ordinary-cli-final12of13-01.json)。

<!-- ROOT_RESULT_FILLED: DOC7_FINAL_CLOSEOUT. 七路径文档按授权安装并经独立文档审查接受，四个旧文档完整原字节前缀保持不变，两份新文档已创建。普通 OpenSpec strict/status/apply 先在 11/13 状态通过（PID 5333/5334/5335，apply total 13/complete 11/remaining 2），随后勾选 5.2；最终 12/13 状态复核也全部通过（PID 16144/16145/16146，apply total 13/complete 12/remaining 1）。当前 tasks 为 12/13。安装收据：/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-root-task-observation-doc7-installation-receipt-01.json；11/13 CLI 收据：/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-root-task-observation-ordinary-cli-11of13-01.json；最终 12/13 CLI 收据：/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-root-task-observation-ordinary-cli-final12of13-01.json。6.1 指定外审仍离线 pending；本标记不宣称整体目标完成。 -->

指定 GPT-5.6 Sol/xhigh Grillme 外审离线 pending，未探测、配对、替代或由内部审查代过。真实 PostgreSQL、provider、Worker、SMTP、生产计费、部署和完整 React/browser 各自未验证；不把它们新增为这九方法的本地业务或部署审批门槛。

按固定旧248与当前精确91登记面，声明差集为157个显式方法/154个业务API，只表示尚未登记接口数。本批九方法运行验收已过；普通 OpenSpec strict/status/apply 先在 11/13 状态通过，随后在 12/13 状态再次全部通过；当前为 12/13，本地 CLI 收口完成，最终运行收据见上。generic四个与admin两个方法、13类provider协议已有 EVID 分析/规划与共享候选准备，未安装/验收，不再扣这六个方法；相关规划中的82/166是当时快照。完整后端模块化→完整React→最后语言评估仍未完成，后续依据真实交付更新，不使用注册数量推算完成率。
