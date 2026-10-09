# Tasks

## 1. 身份与当前读取契约

- [x] 1.1 新增 `roughCutFrameNavigation.ts` 及测试，验证同章草稿实际 row、非空 raw ID 三份全量唯一后归属；覆盖草稿顺序与章节不同、错误 frame_index 不作兜底、草稿/章节/完整 assets 重复或 foreign、空 ID 与缺目标拒绝，以及 included/pending/缺视频不影响合法定位；保持受保护 helper/DTO/sample 未改。
- [x] 1.2 为 `PersonalRoughCutPanel` 增加当前 ready 读取 ticket 和带当前镜头序号的显式入口，验证粗剪读取中/读取错误/空草稿无有效入口、草稿仍可在原图 loading/error 时读取展示，原图正常迟到 ready 后只更新定位资格且 rough GET 仍一次、无自动 focus 或额外 GET。

## 2. 父级定位与反馈

- [x] 2.1 在 `ChapterBrowser` 用期望 rough owner、openEpoch、读取 ticket、当前章节/服务/用户与 ready assets 快照/请求世代守卫定位及关闭回调；验证关闭重开与章节/原图显式重读作废旧 owner，普通原图完成不关闭，notes/rough/关联素材面板继续互斥。
- [x] 2.2 接入现镜头文章 refs，聚焦前后重新核验身份、当前性与 DOM，只有真实 activeElement 成功才即时居中 scroll 和反馈；组件测试验证合法目标、重复点击零 GET、实际聚焦失败和 hidden/detached DOM 拒绝，以及 article 转入内部 button 时清粗剪反馈而不误清其他定位反馈。

## 3. 生命周期与过期反例

- [x] 3.1 补 Panel/ChapterBrowser 旧 locate/close/invalidated 回调真实回放，验证重读旧成功与 401 在原 Promise 实际 settle 并 await act 后不能定位、关闭新 owner、清新反馈或退出新会话；不得仅以 signal.aborted 代替该证据。
- [x] 3.2 补 user-only、services-only、chapter-object 变化的首个父 layout commit 观察与 A→B→A 反例，验证旧面板/入口首帧隐藏且永久失效、无自动重开/补读，旧 ticket 与实际迟到响应无效，重新显式打开后的当前读取和点击有效。
- [x] 3.3 补真实 Workspace 退出→新用户旧 rough 成功/401 的 settle 集成证据，核验新 token/用户不被旧请求影响；回归返回列表原筛选及已展开数量、任务离开、现章节/素材导航与个人续作等已有只读路径。

## 4. 原生浏览器验收

- [x] 4.1 主代理在默认 demo 1280px 与 390px 实测现第一章逆序草稿首项定位正确章节镜头、排除/pending 条目也可定位、面板保留、键盘实际 focus/滚动/反馈与失焦清除、无横向溢出；保留原 24 剧集/章节/媒体/notes/rough 样例，截图与 DOM 证据不冒充自动测试。
- [x] 4.2 主代理在现隔离 API fixture 验收可表达的正常/unsaved/legacy/error/显式重试与 pending 取消/新登录，操作前后日志确认定位零新增业务 GET、打开仅既有 rough GET、无 notes/锁/写/播放/下载；原有安全 PNG 如发生按实际记录。身份歧义与忽略 abort 的真实迟到 Promise 仅由自动 mock 举证，不新增 fixture 控制或业务接口。

## 5. 集成检查与事实收口

- [x] 5.1 主代理独立完成 frontend typecheck/full test/build、fixture syntax、来源合同与 exact-five verifier、OpenSpec strict、实际文件文本检查和历史 78/保护 85/冻结来源 11 守恒核查；记录实际通过/失败及执行者，不用 unborn 仓库空 tracked diff 代替文件覆盖。
- [x] 5.2 完成 Sol 6.1/xhigh 独立只读源码/测试复审与 README、compat、本 change verification/tasks 的证据收口；记录实际 API 模式/请求数、浏览器和自动 settle 层级及未运行真实 FastAPI/DB/Worker/R2/付费范围，当前十二业务路由、exact-five、样例和前十三历史保持，不提交/推送/部署/归档。

## 6. 独立外部复审待办

- [ ] 6.1 由指定 GPT-5.6 Sol / xhigh 完成 Grillme 复审并保存实际结果；当前离线未执行，保持未勾选，不探测/启动/配对/降档，不以本 Sol 规划或复审替代，不阻断已授权本地开发。
