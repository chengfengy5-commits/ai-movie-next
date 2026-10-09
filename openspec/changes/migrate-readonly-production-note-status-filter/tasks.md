# Tasks

## 1. Local Filtering

- [x] 1.1 新增 productionNoteStatusFilter.ts/.test.ts，仅消费既有投影类型；验证七种选项的完整数量、各状态过滤、原顺序/位置/行引用不变、orphan不参与，待重新确认/不可核对不计为当前已认可。
- [x] 1.2 在 Panel 原可见列表接入按钮组、数量、局部零命中与全部恢复；用组件测试验证 noSavedRecord/空行/loading/checking/error 不造控制或新列表，警告/orphan/resume 持续可见，筛选不增加 GET/capture/登记/失效调用，筛后原可信行仍可定位。

## 2. Read Identity and Lifecycle

- [x] 2.1 将选项绑定同份当前 readout/read ticket，render 时不匹配即默认 all，消费时核对最新 scope/ref/世代；组件参数例用第一父 useLayoutEffect 实证 user-only/services-only/chapter-object 首提交隐藏旧控制与数量，A→B→A不复活，显式重开/重读新读取默认全部。
- [x] 2.2 通过观察性公开 JSX runtime mock 捕获原 button props.onClick 并实际调用+await act，验证 R1旧filter回调不能改R2或重开后选择；增强真实旧 GET success/401及摘要 Promise resolve/reject+await act 用例，验证新内容/会话不受影响，不以 detached DOM fireEvent 或 signal.aborted 代替。

## 3. Integration and Read-only Review

- [x] 3.1 在唯一允许的 Workspace.test.tsx 加真实整合代表例，验证两用户读取/筛选隔离、筛后原逐行与续作定位、过滤鼠标/键盘失焦自然清反馈、返回24条剧集及原筛选/任务切换保持；旧用户原Promise真正settle+await act，不修改父产品/导航helper。
- [x] 3.2 GPT-6.1 Sol / xhigh 独立只读复核产品和实际测试：检查全量计数/原行身份、首提交、真实callback回放与迟到结果、纯本地无附加读取；以具体文件行号闭环P1/P2并记录模型路由与runtime独立核验边界，不代替Grillme。

## 4. Real Browser Acceptance

- [x] 4.1 root 用原样 demo 在1280桌面与390移动实测待修/待重新确认数量、已认可零命中/全部恢复、orphan与续作持续可见、原定位、键盘和无溢出；按实际样例记录，不改24剧集/章节数据或冒称全六状态浏览器覆盖。
- [x] 4.2 root 用现有隔离API fixture/env restart实测两用户实际状态与mismatch代表路径，按操作前后日志验证筛选/原定位零附加业务GET，显式重读仍沿用既有GET；真实取消与自动原Promise settle分开，记录既有安全PNG，清临时服务/标签，不新增fixture/HTTP模式或真服务。

## 5. Final Local Evidence and Documentation

- [x] 5.1 root 独立执行 typecheck、完整 frontend test（35个既有文件加本批新增测试，记录实际最终数量；可沿用 --no-file-parallelism，默认timeout不变）与 build，以及 fixture syntax、source-baseline 合同测试和exact-five验证；保存实际命令/退出码，失败与最终成功分层保留，不推断未核实原因。
- [x] 5.2 root 执行 OpenSpec strict/apply readiness、基于实际文件的文本/冲突/EoF扫描和保全核对；验证90历史/91保护/13来源与样例/依赖/十二接口/exact-five无漂移，legacy固定HEAD及原状态未变，所有本批改动限四mutable+两new，无提交推送部署归档。
- [x] 5.3 实施验证后按root授权更新 README、本批兼容说明、verification和tasks；逐项核对实际日志/截图/证据路径，区分来源/自动化/浏览器/未运行真实后端、旧GET维护副作用及模型runtime未独立核验，最终四文档冻结后再跑strict/文本/保全与apply收据，不修改前十五批历史。

## 6. External Review

- [ ] 6.1 指定 GPT-5.6 Sol / xhigh Grillme 审查独立待办：当前离线未执行，不启动/探测/配对/降档、不以Sol或本地验收替代；仅实际完成该指定审查并保存证据后才勾选，离线不阻断已授权本地开发。
