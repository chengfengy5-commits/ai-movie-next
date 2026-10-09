# Tasks

## 1. 精确契约与服务

- [x] 1.1 新增独立 resume 类型/parser 与 resumeEdit helper；用新单测验证 own 两键、raw ID/36码点/安全 revision、设置全 raw count 后归属/位置/source_valid、revision0/无便签/摘要不匹配/双空仍可设置，及 clear 在 empty/unreadable/mismatch/旧目标失效时独立通过；成功响应 revision+1/精确 resume/设置身份验真与服务器维护其他notes，并核原读取/备注 parser/helper 未变。
- [x] 1.2 追加可选 savePersonalProductionResume，真实 demo/API 均提供且与备注共用私有存储/revision；新服务测试实际走 adapter，验证无能力只读、set→note→clear CAS/两用户/实例隔离、当前 token/单次编码/exact body、clear 无媒体门槛、失败分类/无自动请求及未知结果不重写。

## 2. 面板与原始事件

- [x] 2.1 完成独立续作选择/设置/清除 UI 和共享同步写门禁；新面板例验证 noSaved revision0/仅resume能力入口、同位置/null no-op、未修改默认草稿不阻止、打开编辑器或关闭后真正dirty草稿阻止位置写、Cancel保留草稿/显式GET可放弃；pending/失败意图可查看，关闭重开编辑器不绕过must-reread，非401保留会话，成功 higher null→ready 与原筛选/定位资格保留。
- [x] 2.2 新面板例通过公开 JSX runtime 观察捕获实际选择/Save/Clear、原备注 Save/Cancel 和 Close handlers，真正回放到R2/R3先有非默认选择、同snapshot新generation及close/reopen；断言当前选择/门禁/草稿/登记/失效计数和GET/PUT不变；同act内同步双击及跨类型保存至多1PUT，不用detached DOM/Fiber或只abort布尔替代旧闭包调用。
- [x] 2.3 新面板例在父useLayoutEffect观察 user-only/services-only/chapter-object首commit隐藏旧意图，A→B→A永久失效；旧原始GET/PUT success和401及digest Promise实际settle并await act，不覆盖新读取/token，不注销新用户；覆盖409/unknown的真显式GET、GET失败不解门禁、目标失效后不得auto rebase、清除可恢复，原父tombstone水位线保持。

## 3. 隔离 HTTP 与集成复核

- [x] 3.1 fixture仅扩原PUT resume分支/CAS，新增真实HTTP及真实Workspace集成测试；HTTP验证当前Bearer/两用户同章/设置再GET/clear再GET/同path13业务边界/精确拒绝/无敏感body日志，原note分支与GET首次样本不改；集成保留24条列表/筛选/任务/关闭重开，真实父登记、R0位置创建、dirty互斥、409保留与显式核实、新登录后的旧PUT success/401实际settle隔离，使用live region查询、不增timeout/skip。
- [x] 3.2 由请求复用的 GPT-6.1 Sol/xhigh 完成白名单源码/测试独立只读复核，关闭实证P1/P2与必要证据缺口；保存实际文件SHA、source/自动化/根浏览器分层及runtime未独立核验收据，不能替代指定外审。

## 4. 真实浏览器验收

- [x] 4.1 根代理用默认demo实际验收1280桌面、390移动和键盘选择/设置/清除、无溢出、重开GET恢复、dirty备注保留、原续作/逐行定位及只读页面回归；定位资格不足仅显示标记已存，不自动导航；如实记录安全图片/业务增量与实例内保存边界，不声称页面reload持久。
- [x] 4.2 根代理仅现有隔离fixture/API验收本人set/clear/重开GET、两账号同章私有、409与unknown代表模式保留意图/旧revision不可重复写/显式GET恢复、delay关闭退出新用户边界；记录实际十三允许方法/实际实访、每组PUT/GET/OPTIONS/安全PNG增量和拥有服务清理；empty/unreadable清除与忽略abort旧Promise用自动化补证，不增加GET modes/样本或把browser取消称实际迟到Promise证明。

## 5. 最终本地与文档收口

- [x] 5.1 根代理在最终冻结版本独立 typecheck、全部tests、build、fixture语法、来源合同/exact-five及敏感模式检查、OpenSpec strict/status/apply、实际文本与保全；对117历史/49窄保护/231不可变/13固定来源及原tests/样本/API行为保全，核允许5旧+6新、记录最终hash/真实失败历史/代码与浏览器版本匹配，不提timeout或skip、不用untracked仓库git diff空输出冒称检查覆盖。
- [x] 5.2 验收后由Luna仅追加README/compat第20段、生成verification并据实际更新tasks；根实际freeze/strict/text/保全后由Sol只读核4文档及链接，区分旧GET/PUT维护、位置无媒体CAS、demo实例持久、代理/根执行者、Promise/browser证据、runtime未核验、真实后端未运行和外审待办；保留前十九文档全文。

## 6. 指定外部复审

- [ ] 6.1 按指定 GPT-5.6 Sol/xhigh 执行 Grillme 独立外部复审并保留实际收据；当前离线、未执行，独立待办不阻本地开发。不探测/配对/启动、不降档，不以Sol源码复核代替，未做不得勾选。
