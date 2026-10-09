# 任务观察与取消控制模块化草案

## Why

当前 React“我的任务”调用 GET /api/chat/tasks/list，目标后端尚未登记该方法。固定旧源码中的查询还承担提交回执恢复、请求查看、AI审核/批量优化观察和取消控制。完整迁移八GET加一个取消POST，可以接通当前页面数据，并为后续生成与Worker改造提供明确边界。

本五份规划仅为EVID staging草案。根在团队收口后建立下一实际基线、安装普通OpenSpec规划、执行CLI并明确批准，才允许实施；当前teams54、target564和团队52来源冻结。本候选55来源不是当前团队语料扩围。

## What Changes

- 以固定提交23403806898550a7668a6ee7c0c457315655c39b迁移精确九方法，保留参数、错误顺序、身份和响应差异。
- 分开登录即可和活动会员身份；任务请求详情superuser只从真实SQL身份读取，原82接口及旧四私人接口显式policy规则保持。
- 任务回执取最早计费单元，无单元回退；提交回执要求恰一任务、恰一单元，不合并两种查询。
- 取消先set进程内已注册信号，再使用显式注入的独立连接事务，仅条件更新status；row0仍正常提交返回cancelling，未知提交无自动重放。
- 复用公开纯teams.reporting.task_payload与公开权限函数，不借团队私有UoW，不修改团队实现或Worker。
- 候选factory最终82→91只表示登记；根实际SQL/JWT/TCP及真实body消费现有TS parser分开验收。

## Capabilities

### New Capabilities

- task-observation：完整任务与提交回执、本人任务列表、受权限控制的请求查看、AI审核/批量优化观察及取消控制。

### Modified Capabilities

- 无业务政策修改；旧模块仅允许factory接线与必要登记哨兵增量。

## Impact

新增12生产+9测试/support=21代码文件，未来旧mutable为app+9登记测试=10，共精确31代码冻结键。旧测试只增九方法/82→91期望，保留所有业务和cold哨兵，不skip/xfail。精确路径见design和receipt。

文档后置另获根授权：旧README、backendREADME、boundaries、compat完整旧前缀保留EOF追加；新架构与verification，加tasks勾选共七路径。当前排期、frontend、pyproject、auth/sharedidentity、teams实现、notes/access及其他旧源冻结。以未来实际B为基线：规划B+5、代码B+26、文档B+28，不把当前564当未来B。

完整生成6、generic4、admin2、停用proxy、provider/计费/队列/Worker生命周期、完整React请求/取消/恢复/结果和最后语言评估仍待后续。现166声明差集全部保留，不能规划时扣九。部署需另行授权，不是本地迁移的新增门槛；指定GPT-5.6 Sol/xhigh Grillme保持离线pending。
