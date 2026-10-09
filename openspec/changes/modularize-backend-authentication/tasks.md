# Tasks

## 1. Source and Scope

- [x] 1.1 根复核并冻结实际四规划、元数据、42 份 Git 来源及 4 份安装库源码，确认空主规格库存、六旧代码白名单、22 新代码/测试路径和文档名单；以真实 CLI readiness、来源哈希和 339/175/147 基线保全收据验收后授权实施，不将 planningComplete 当作实现完成。

## 2. Implementation and Targeted Tests

- [x] 2.1 实现领域、DTO、头像和完整应用入口，专用测试验证 extra ignore、Unicode 八字符注册/重置与六字符改密差异、QQ 头像/login bio、原错误优先级及字段可空，不改既有四方法的 422 或虚构 is_deleted 列。
- [x] 2.2 实现真实 bcrypt/JWT、本人会话和会员规则，专用测试验证签名/过期/type、整秒 password_version、缺失旧声明、未知 jti 两次提交、旧会话维护一次、非当前在前的 20 条列表、顺序第四登录、本人/他人撤销、全设备改密，以及 reset 只改哈希且可重复使用。
- [x] 2.3 实现实际 SQL 投影与 UoW，临时文件 SQLite 测试证明注册用户+积分原子性、身份/积分维护、真实两个物理连接的注册/jti/积分唯一竞争、外键和空值、提交前失败回滚及真实 commit 后人为确认错误的新读取；不吞异常、不自动重试，不把 SQLite 交错当 PostgreSQL 实测。
- [x] 2.4 实现显式供应商、进程内验证码和三限额，专用测试验证 send-code 清理/占用/冷却/同码 false 重试/502 无新记录，verify 标记不消费、register raw 邮箱先 pop、到期相等和消费早于重复检查；实际验证客户端/pathname 限额、两种 429、422 不计数及八方法无新增默认限额，受控邮件结果不当 SMTP 完成。
- [x] 2.5 接线完整 11 认证+四业务=15 方法、局部认证 422 和真实会员 actor；更新仅六旧路径许可内容。ASGI 和干净子解释器验证显式 resolver 优先、缺配置 503/零 Session、auth 先提交再业务的独立生命周期、四业务 JWT/会员拒绝、所有生产包冷导入无环境/文件/Engine/schema/Session/供应商副作用，并保留旧测试全部业务和哨兵断言。

## 3. Independent Review

- [x] 3.1 指定 GPT-6.1 Sol/xhigh 对最终批准路径作只读源码与测试复核，保存实际 SHA、兼容反例、真实 SQL/密码/JWT 与冷导入证据和必要缺口；所有 P1/P2 与必需验证缺口闭合后方可完成，本收据不代替根执行或独立 Grillme，运行元数据未核验则如实记录。

## 4. Root Acceptance

- [x] 4.1 根独立执行全部新后端回归、语法/manifest/pins 与原 source 3/3、exact-five 来源检查，记录真实退出码、版本、命令与阶段失败历史；用受控共享解释器和自有临时库，不导入旧应用、不写旧缓存，不增加 timeout 或 skip。
- [x] 4.2 根以真实 loopback、真实密码/JWT 和 SQL 访问策略验收 11 认证方法及四业务方法：注册/验证码/登录、两用户本人隔离、撤销与会员拒绝、改密/重置差异、积分及认证维护持久化；用冻结 TypeScript parseUser/parseLoginResponse 解析实际返回，以受控 sender 验证码/link，不声称真实 SMTP、生产身份、PG 或前端测试/浏览器重跑。
- [x] 4.3 根核验全部来源、旧 169 代码、147 历史、六旧路径合法差异、旧文档正文及最终代码 SHA，并清理本批自有临时库、进程、端口和解释器测试临时目录；保留精确旧 HEAD/status/tracked/index 记录，不读取旧未跟踪内容，不把清理收据当全局无外连审计。

## 5. Documentation and Local Closeout

- [x] 5.1 Luna 根据真实根收据追加四旧说明并新增认证架构与本 change verification，严格保留旧前缀/compat 可还原全文；Sol 只读核对生命周期、全部 15 方法、执行者/失败历史、SQLite 与供应商限制、模型 requested/runtime、未部署与外审边界，实际哈希和链接无误后完成。
- [x] 5.2 根对最终冻结文档和 checkbox 实际执行 strict/status/apply、文本及完整保全，记录 planning 与执行进度区别、仅独立外审待办及最终哈希；本批本地完成不能声称整个迁移目标或真实生产认证完成，不覆盖失败和较早进度收据。

## 6. External Review

- [ ] 6.1 按用户指定 GPT-5.6 Sol/xhigh Grillme 独立审查最终实现与证据；服务离线时保持未完成，不启动、探测、配对、降档或用 Sol/Luna/根测试代替，仅在实际外审完成且问题闭合后更新。
