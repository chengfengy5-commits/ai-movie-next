# Tasks

## 1. 行身份与显式入口

- [x] 1.1 新增 `productionNoteFrameNavigation.ts/.test.ts`，验证 actual frame/readout 对象归属、ready/verified/可识别状态、当前章唯一 first-ID 和完整 assets 先 rawID 计数再归属；测试有效未标记/待修/待重新确认/认可、重复/foreign/缺身份/替换对象/假位置及 unreadable/unverifiable/unknown 拒绝，无 projection/DTO/sample 变更。
- [x] 1.2 为现有 NotesPanel 逐行增加“定位到记录镜头 N”、完整读取 ticket、父端当前读取登记和 readout+requestGeneration 失效通知；测试读取中/checking/error/noSavedRecord/orphan/mismatch/无资产不开放新入口、不生成未保存镜头新列表，不要求 approved，原列表及续作入口/读取行为保持。

## 2. 父端定位与反馈

- [x] 2.1 ChapterBrowser 保存 expected owner 当前读取身份/世代，独立调用新 helper 验证 actual row/readout 与当前章/assets；测试旧 currentness 声明不能旁路父端、旧 ready 登记不能复活失效读取，当前合法定位仍成功，业务 GET 不增加。
- [x] 2.2 复用 article refs 完成焦点前后身份/DOM 复核、真实 activeElement 后 scroll 与独立逐行反馈；测试实际 hidden/detached/focus-fail 不滚动/不成功且当前 Panel 有失败说明，恢复后显式成功；内部关联素材 button 实际 focus 清逐行反馈，不误清其他定位流程，重复定位零 GET且面板保留。

## 3. 生命周期与真实迟到反例

- [x] 3.1 用 actual callback replay 验证同 owner R1→R2 旧 invalidated/locate/close 不清新反馈或关闭R2，关闭重开旧 locate/close/invalidated 不影响新 owner；记录重读旧成功/401与摘要 Promise 必须实际 resolve/reject 原 Promise 并 await act，不只断言 signal.aborted。
- [x] 3.2 针对 ready 合法入口，用父 useLayoutEffect 观察 user-only/services-only/chapter-object 首个 commit 隐藏旧内容/按钮及 A→B→A 永久失效、无 autoGET，重开新读取才有效；代表性当前assets/media scope变更、切章、章节/原图显式重读及互斥面板覆盖旧 ticket/反馈作废，保留原 notes 显示规则。
- [x] 3.3 真实 Workspace 集成覆盖退出→新用户旧 notes success/401 原 Promise settle+await act，新 token/用户/记录与定位有效；回归返回列表筛选/24展开、离开任务页、原续作/粗剪定位和章节/素材导航，不修改 Workspace 产品。

## 4. 原样 demo 与隔离 API 浏览器

- [x] 4.1 root 在默认 demo 1280px/390px 真实操作至少两条可核对记录，以鼠标/键盘定位对应镜头、实际 focus/scroll/独立反馈/child blur、面板保留、重复点击及切章/重读失效；观察无横向溢出并保存截图，原24剧集/章节/notes/rough/媒体样本不改。
- [x] 4.2 root 在原隔离 fixture 验收能表达的正常、mismatch/错误与显式重试、双用户和 delay 取消，点击前后业务日志增量0，打开/重读仍原 notes GET，记录实际访问模式与既有安全PNG；duplicate/foreign和忽略abort真正迟到仅由自动 mock 举证，不新增fixture样本/控制/接口，不冒称真实后端回归。

## 5. 集成检查与证据收口

- [x] 5.1 root 独立运行 frontend typecheck/full test/build、fixture syntax、来源合同/exact-five verifier、当前change strict与实际文件文本扫描；验证84历史/87保护/13冻结来源守恒及 mutable6+new2边界，记录真实结果与执行者，不用 unborn tracked 空diff 冒充覆盖。
- [x] 5.2 Sol 6.1/xhigh 独立只读源码/测试复审并在实现后收口 README/compat/当前 verification/tasks；核对真实焦点、自动 actual settle 与 browser取消、API实际模式/数量及未运行真实FastAPI/DB/Worker/R2/生产/付费层级，不改前十四历史，无提交/推送/部署/归档。

## 6. 独立外部复审待办

- [ ] 6.1 指定 GPT-5.6 Sol / xhigh 完成 Grillme 复审并保存实际结果；当前离线未执行保持待办，不启动/探测/配对/降档，不以本次 Sol 规划或复核替代，不阻断已授权本地开发。
