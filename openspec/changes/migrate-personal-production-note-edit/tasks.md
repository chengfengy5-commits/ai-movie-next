# Tasks

## 1. 保存合同与隔离服务

- [x] 1.1 实现 `noteEdit.ts` 及测试和受限请求/响应类型：验证同份 raw snapshot/capture/readout 的唯一原始ID、完整素材先count后归属、正媒体版本、revision0、readable 与三状态/Unicode2000码点；覆盖普通备注摘要失配可存、认可必须verified且非双空、待重确认初始null、响应revision+1及approved摘要不同不误成功，并用纯函数测试证明不重写派生投影/未知记录。
- [x] 1.2 在 `services.ts` 提供可选保存能力，actual demo/API均实现：用新增服务测试通过实际fetch验证当前Bearer、单次路径编码、无query、严格单帧exactbody及13业务方法；验证demo仅实例内本人/章内存CAS、原GET种子/克隆/未知记录和resume保留，旧readonly provider不提供能力时仍只读，不改旧服务测试或样本。
- [x] 1.3 仅扩隔离fixture同路径PUT、CORS PUT、本人内存CAS与保存错误环境变量：新增真实HTTP测试证明revision0创建、第二次更新、两用户同章隔离、个人/媒体409、422、已应用但坏回复/超时结果未知、原其他GET与13方法白名单不变；检查node语法及日志不含note/body/token，无控制HTTP/真实后端或媒体请求。

## 2. 编辑器与读取世代

- [x] 2.1 实现 Editor 与 Panel 整合和样式：验证显式当前镜头选择、revision0首次入口、literal textarea/三状态/超限/空串、无能力和unreadable/orphan只读；单次保存同步owner门禁、pending导航失效、Cancel重开/另选镜头不能绕过、409/422/403/404保留草稿且显式GET后重新选状态、unknown不自动GET/PUT、成功高世代登记/筛选回全部，组件测试通过且不放宽父tombstone。
- [x] 2.2 补真实生命周期反例：user-only/services-only/chapter对象首父layout隐藏旧编辑+A→B→A永久无效；同snapshot不同读取世代重置、R2/R3先非默认草稿后实际回放旧public JSX input/save/cancel及旧register/invalidated/close/locate（含fake isCurrent）；旧PUT success/401与摘要Promise真正settle+await act，证明新draft/登记/反馈/token/请求数不变，loading/checking/error可关闭，不以detachedDOM或signal.aborted代替闭包/Promise证据。

## 3. 集成与独立源码复核

- [x] 3.1 在新 `PersonalProductionNoteEdit.integration.test.tsx` 使用真实Workspace/ChapterBrowser/Panel/服务链：验证首次和已有记录保存、关闭重开GET恢复、两账号私有/旧账号PUT成功或401真实settle、success更高世代原resume/逐行定位及filter、返回24条/原四筛选与任务入口回归；使用live-region查询减少全树成本，不更改旧Workspace/ChapterBrowser文件、默认timeout或skip。
- [x] 3.2 由沿用requested/reused GPT-6.1 Sol/xhigh的独立只读复核者对最终允许文件/规划/测试真实性核对并保存源码SHA与P1/P2闭环收据：特别核owner写门禁、媒体认可陷阱、CAS/未知结果、R1→R2及新账号原Promise，runtime未独立验证；此源码复核不替代root执行或指定Grillme。

## 4. 真实浏览器分层验收

- [x] 4.1 root 在default demo与隔离API记录1280桌面/390移动真实可见DOM、键盘/焦点/无溢出、文字状态保存及面板重开GET、revision0新增、保存后原筛选/续作/逐行定位仍可用；查看截图并记录实际业务/OPTIONS/安全PNG计数，save显式事件一次PUT，初次notes GET开发StrictMode按实计，不承诺跨page reload内存持久化或原lazyPNG全0。
- [x] 4.2 root 使用现fixture环境变量+重启验收两账号同章私有、409/422代表拒绝保留草稿、已应用坏回复/超时unknown无自动重复PUT、显式GET核实及重新状态确认恢复，离开/退出在途结果隔离；保存日志方法白名单13、敏感正文无泄漏，browser取消/旧server响应仅作边界，ignored-abort原Promise真实性由自动测试独立证明。

## 5. 独立全量与证据收口

- [x] 5.1 root 独立执行typecheck、完整前端测试、build、fixture syntax、source合同与exact-five验证、OpenSpec strict/status/apply及基于实际文本文件的检查/保全：保存命令exit/日志与失败历史，不提高timeout/skip；核原18历史109/不可变217/protected36窄子集/固定来源13与旧SHA/status、受保护projection/父产品/helpers/样本/deps无漂移，仅白名单6旧+7新代码可变，不把初始仓库无tracked diff当覆盖证据。
- [x] 5.2 验收后由Luna仅追加README第19段、compat第19段并写本change verification及任务checkbox，再由root/Sol只读核四文档/实际hash/路径/完成层级并重复最终strict/text/保全：明确demo/fixture内存、GET/PUT既有维护副作用及同章他人认可、真实FastAPI/PG/Worker/R2/paid未运行、业务13而非原12、源码/自动/浏览器/指定外审分层，原READMEprefix/compat全文与前18历史不改，无提交/推送/部署/归档。

## 6. 独立外部复审

- [ ] 6.1 指定 GPT-5.6 Sol/xhigh Grillme 复审：当前离线未执行，保持待办，不探测/启动/配对/降档或用Sol源码复核替代；仅实际完成该指定复审后才能勾选，独立待办不阻断已授权的隔离本地开发。
