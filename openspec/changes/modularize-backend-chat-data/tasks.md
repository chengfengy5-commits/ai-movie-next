# Tasks

## 1. Planning

- [x] 1.1 根读取本批必要的固定来源窗口及五份完整规划，核对基线、精确白名单和任务正文，实际执行 OpenSpec strict/status/apply 并保存冻结与授权收据；只有根确认 ready 后才开始实现。

## 2. Implementation

- [x] 2.1 在获批准新包实现 DTO、消息领域/呈现与纯统计合并，专项测试证明 extra-ignore、省略帧与显式 null 区别、负帧/空字符串及原分组顺序、未知模型合并和失败积分忽略，保留实际失败与最终日志。
- [x] 2.2 实现同业务 Session 的纯 SELECT 访问、两种列表和章/剧集统计仓储，真实 SQL 测试证明查询顺序、普通列表包含资产、条件缺章行为、无 actor-task 筛选、孤立任务自然排除且完整历史行不变；生产无 schema/Engine 启动。
- [x] 2.3 实现创建、两种内容更新与三种删除，真实 SQL 测试证明跨剧 body FK 目标、INSERT 后仅已有本人锁续期、同 content 来源 UPDATE0 仍一次 commit/实际 readback、空串资产分类、bulk 全模式及零行合法，删除后完整 task/账务/队列行守恒。
- [x] 2.4 在现有新事务测试内证明两个物理 SQLite 连接读取后删除导致实际 UPDATE 零匹配失败与已加载单DELETE零匹配仍原commit/204、提交前消息/锁全回滚、真实提交后 ack/refresh 失败与新 GET durable truth及无自动重试；正常库 FK 开启，只有明确专用 orphan 例可 FK-off，不把顺序交错当 PG 并发。
- [x] 2.5 挂载十个 HTTP 方法及统一工厂 52 登记，仅更新批准 app 与六旧路由哨兵必要期望；测试证明状态/标准422/错误优先级、零配置503零业务Session、真实 resolver/会员/访问接线、旧四显式 policy 门槛与冷启动零外部副作用，原旧断言保留。

## 3. Independent Source Review

- [x] 3.1 由指定 GPT-6.1 Sol/xhigh 独立只读全文审查稳定 25 实现/测试路径与七旧真实差分，逐项核实 T1–T7、实际日志来源和完整 SHA；必要 P1/P2 与验证缺口闭合后保存接受收据，runtime 元数据未核验如实记录。

## 4. Root Acceptance

- [x] 4.1 根绑定稳定 25 SHA 独立执行完整 backend 回归、语法/精确清单和 47 固定来源及既有来源合同检查，保存真实命令、退出、日志、前后 hash 与自有临时资源清理；作者专项绿不替代根结果。
- [x] 4.2 根在独立临时 SQLite 与本机 loopback 中实际覆盖本批十方法、真实 JWT/SQL普通访问和既有 auth/access/private smoke，核对实际响应、查询/commit/readback及完整历史行；保存有序 HTTP trace、受控故障与精确资源清理，不新增实验 endpoint、TS/parser或前端/浏览器验收。
- [x] 4.3 根核对原 422 清单、230 旧代码、165 历史、411 不可变范围、七旧代码差分和规划正文/任务归一化守恒，完整真实记录现场 legacy HEAD/status/tracked/index且不设等式门槛；固定47来源字节必须相等，未知新增路径或自有资源残留不得放行。

## 5. Documentation and Closeout

- [x] 5.1 真实验收后由 Luna 在四旧说明 EOF 追加并新增聊天架构与 verification，保留完整旧前缀；Sol 只读核对七文档图、实际证据链接和 SQL/HTTP/历史/runtime/外审分层，缺口闭合后才接受正文。
- [x] 5.2 根对最终文档/checkbox版实际执行 OpenSpec strict/status/apply、UTF-8/文本、精确预计447清单及全部 source/code/doc/history守恒和自有资源检查，保存本地完成、完整目标仍 active、指定外审 pending 的最终收据；未执行结果不预勾。

## 6. External Review

- [ ] 6.1 指定 GPT-5.6 Sol/xhigh Grillme 离线独立外审；当前未执行、未探测或配对，内部 Sol/Luna、根测试和 HTTP 验收不替代，保持未勾直至该指定外审真实完成。
