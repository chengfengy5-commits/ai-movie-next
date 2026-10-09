# Tasks

## 1. Local Search and Display

- [x] 1.1 实现 assetLibrarySearch.ts/.test.ts；单测验证三分类原 name/title 与可见非空 aliases、trim/大小写字面子串、空查询、未命名占位排除、隐藏字段排除、特殊字符、原顺序/完整索引及 row `toBe` 身份，输入不被修改。
- [x] 1.2 在 AssetLibrary 增加有可访问名称的输入/清空、命中与完整总数、限定当前快照的零命中提示；组件测试实际等候 ready 后验证 loading/error/合法 empty 无搜索控制，非空零命中保留控制；原 key 完整卡片以 hidden/inert 隐藏，实际恢复同 DOM 节点与破图状态。

## 2. Read Identity and Navigation

- [x] 2.1 查询绑定完整 scope/category/result/request generation；组件首父 layout commit 验证 user-only/services-only/series 变化与 ABA 隐藏旧查询，R2 复用同 items 对象仍重置；分类实际切换/重读/重开/新 navigation epoch 为空，同分类重复点击保留。验证完整目录 duplicate raw ID 只剩单可见项仍无资格，新目标首帧清空查询且沿原实际 focus/scroll 消费。
- [x] 2.2 通过公开 JSX runtime 观察捕获真实旧 input onChange 与清空 onClick，R2/R3 先有新的非空查询再实际重放 R1 并 await act；断言当前 query/可见结果/usage/feedback、GET/请求世代不被旧回调改变。旧成功/401 与 usage 原 Promise 实际 settle+await act 验证不会覆盖新目录/新用户；查询实际变化关闭旧 usage/反馈且不改变缓存、父身份或媒体链路。

## 3. Integration and Independent Review

- [x] 3.1 在真实 Workspace demo/service 集成测试中验证三类名称/别名、零命中/清空、搜索后的关联镜头查看与已有素材导航，保留 24 条剧集/筛选/返回/任务及两用户迟到 Promise/token/logout 隔离断言；采用当前 live region 查询与 click/paste，真实 keyboard/focus 与 await act 不省略，不提高 timeout/skip。
- [x] 3.2 请求/复用 GPT-6.1 Sol/xhigh 对最终六个源码/测试文件和已批准规划做独立只读复核，保存实际读取 hash、P1/P2 与证据缺口闭合收据；区分代码阅读、代理定向执行及 root 实跑，runtime 未独立核验，不替代指定 Grillme。

## 4. Real Browser Acceptance

- [x] 4.1 root 用默认现有 demo 在实际 1280 和 390 视口验收三分类名称/别名、零命中/显式清空、Tab/hidden/inert、原 usage/导航及返回 24 条列表、无横向溢出；记录实际 focus/scroll 与截图，不修改样本，不把重复身份/空名称自动化构造声称为浏览器样本覆盖。
- [x] 4.2 root 用原样隔离 API fixture 验收代表分类搜索/清空操作的业务 GET 增量零、原错误/空目录/显式 retry、两用户与 delay 取消隔离；独立记录现有安全 PNG/OPTIONS 实际增量和允许十二模式的实访子集。导航原 fresh GET 与 SeriesPage 重校验独立记账；browser cancel 不冒充 ignored-abort 原 Promise 证明，清理临时服务/tab并记录事实，不新增 fixture mode/endpoint。

## 5. Root Verification and Documentation

- [x] 5.1 root 独立执行最终 typecheck、全量 test、build、fixture syntax、source 合同测试和 exact-five 来源验证，保存实际命令/exit/数字/时间及失败历史；默认 timeout 不变、无 code skip，source 五项与敏感模式结果不扩称全面秘密审计，不运行真实后端/DB/Worker/R2/付费服务。
- [x] 5.2 root 执行 strict/status/apply、实际文本检查与基线保全；证明既有 102 个历史产物、95 个保护文件和十三份固定来源无变化，代码仅四 mutable+两 new，API/DTO/services/fixture/demo/依赖/原样本不变，README prefix/compat 既有全文保持。记录最终 inventory/code/hash 和实际状态，不以全 untracked 的空 git diff 代替文件检查。
- [x] 5.3 由授权 Luna 仅事后补 README 新第十八段、compat 本批段、本 change verification 和 tasks；如实分层静态来源/代理定向/root 全量/浏览器/只读复审、业务与 PNG 计数、runtime 边界和外审待办；冻结四文档后完成只读证据核对与 root 最终 strict/apply/text/保全收据，无 commit/push/deploy/archive。

## 6. External Review

- [ ] 6.1 指定 GPT-5.6 Sol/xhigh Grillme 外部复审独立待办；离线保持未完成，不探测、启动、配对、降档或用 Sol/Luna 本地复审替代，不能将前述本地验收表述为此项已通过。
