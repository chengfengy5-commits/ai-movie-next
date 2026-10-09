# Tasks

## 1. Planning and Source Baseline

- [x] 1.1 根审读四规划并实际执行 strict/status/apply ready；冻结精确六旧/十九新代码路径、四旧/两新事后文档范围、368/197/191/153/358 基线及 45 固定来源 SHA，记录旧现场元数据；交付真实批准/保全收据后实施。

## 2. Complete Module Implementation

- [x] 2.1 实现纯 records/ports、DTO、字段变更与投影规则，以新 domain 测试验证类型转换/extra/default/null-vs-empty、创建帧与更新 dict 差异、列表/详情 claim 差异、style/头像、章顺序原规则，无新限制或外部调用。
- [x] 2.2 实现同 Session Core 仓储/UoW 与真实普通/删除访问规则，以 persistence/application 测试验证错误优先级、claim 阶段 membership 再读、可信数据库 superuser、最小真实列/FK、只实际变化 DML、空/同值 PUT 时间不变及仅持锁者续期。
- [x] 2.3 实现 storyboard 源阶段与同事务 notes media adapter，以真实 SQL source-phase 测试覆盖 T1–T5：旧 base R1→R2、多用户撤认/未知字段保留、新资产先插入后 refs、duplicate/unhashable/错 series、no-op 与非媒体变化、墓碑单调、CAS/提交前回滚与提交后未知结果、后阶段失败保留前阶段。
- [x] 2.4 实现章/剧删除及聊天映射，以 deletion 测试覆盖 T6–T7：空映射/null index/重复覆盖、删镜头孤留任务、整章/剧显式任务清理、错 series storyboard、orphan 归属/剩余引用、真实 billing FK 拒绝且完整账本与来源行回滚，不虚构 AITask.message_id 外键。
- [x] 2.5 接线十二 HTTP 方法与 factory 二十七方法；新 HTTP/boundary 测试验证标准 422、503 零 Session、显式 resolver 优先、新 SQL reader/旧四 policy 条件独立、auth 先提交独立 Session、授权拒绝 asset/private 查询为零；六旧路径只有许可 factory 增量，原业务和冷导入副作用哨兵不减。

## 3. Independent Source Review

- [x] 3.1 GPT-6.1 Sol/xhigh 按冻结来源、四规划及实际稳定二十五代码/测试路径 SHA 独立只读复核实现和 meaningful tests，逐项关闭真实 P1/P2 与必要证据缺口；记录请求路由与 runtime 未独立核验，不替代根实跑或指定外审。

## 4. Root Execution and Conservation

- [x] 4.1 根在新目录独立运行完整后端 pytest、syntax/精确文件 manifest、source contract3/exact copy5 与 45 固定 Git 字节核验，保存失败/修复/最终日志与实际退出码；不导入旧 app、不安装、不增 timeout/skip，PG 仅 SQL/锁编译。
- [x] 4.2 根用自有临时 SQLite/loopback 真实执行十二方法及认证/访问/个人状态集成，覆盖创建/更新/排序/删镜头/删章/删剧、权限/空章/无视频/阶段恢复/账单拒绝；冻结 parseSeries/parseSeriesList/parseChapterList/parseStoryboardAssetList 消费实际 HTTP body（单章包一元素数组），记录真实 SQL/HTTP/TS 层，不冒称整套前端或生产。
- [x] 4.3 根实际核 target 不可变文件、191 旧代码/153 历史/45 固定来源与批准代码 SHA，保留原四文档前缀和规划正文；完整记录旧现场 HEAD/status/tracked/index，不做 ambient equality 门槛；精确清理自有 PID/端口/temp 并保留失败收据，不声称 global 无外连。

## 5. Documentation and Local Closeout

- [x] 5.1 Luna 在代码/根证据冻结后只追加四旧文档并写两新文档，以实际 source/SQL/HTTP/TS/事务/清理日志说明范围、限制、历史失败及尚未迁移能力；Sol 只读核实际文档 SHA、链接、数字、执行者和外审边界，不预称未来检查完成。
- [x] 5.2 根对最终文档/checkbox 版本实际执行 strict/status/apply、文本与保全检查，冻结代码/文档 SHA 和真实本地验收收据；只在证据满足后关闭本地任务，规划 isComplete 不等于全任务或原总目标完成，不部署/归档。

## 6. External Review

- [ ] 6.1 指定 GPT-5.6 Sol/xhigh Grillme 独立外审；当前离线未执行，不探测/配对/替代，不用 Sol 源码复核或根验收冒充；本地后续迁移可继续，此项保留独立待办。
