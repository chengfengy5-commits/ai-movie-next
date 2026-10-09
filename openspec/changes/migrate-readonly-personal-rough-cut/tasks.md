# Tasks

本清单仅覆盖第七切片；逐项按实际证据勾选。本地实现、自动测试、浏览器验收与文档收口已完成。root 最终复核 strict valid，apply ready，12 项完成 11 项；唯一未完成项是固定模型 Grillme 离线待办 6.1。来源及历史边界结果见 verification.md。

## 1. 契约与服务

- [x] 1.1 实现独立 PersonalRoughCutSnapshot DTO/decoder，对照 design 冻结 GET helper；以单测验证 required own、null 与缺失、精确 chapter_id、revision/saved、safe integer/strict boolean、非空 ID/位置唯一、空 ID 重复、removed 重复和合法超过 500 项，不套用 PUT/UUID 限制。
- [x] 1.2 扩展 getPersonalRoughCut 并实现独立无网络 demo 合成生成器；服务单测验证当前会话、路径安全编码一次/no query、abort/session 保护、clone 与符合 source 的初始/已保存合并投影。证明原 24 剧集/章节样本不变，demo 无 fetch、私有写存储或媒体请求。

## 2. 只读界面与接入

- [x] 2.1 实现可重读/关闭的只读草稿面板；UI 单测验证 wire 顺序与原章位置不同、已保存/初始投影/合法空态、included/pending/不可用原因、重复旧引用条数、HTML 纯文本、空正文占位和 raw ID/URL 不落 DOM/links/media；至少代表性非401错误保留会话并可显式重读，当前401才触发退出。
- [x] 2.2 接入 ChapterBrowser 显式入口及互斥个人面板；自动测试证明进入章节零粗剪预取、打开不触发 notes/额外资产 GET，关闭/切章/章节及原图重读/返回/退出立即关闭，服务/user/chapter 对象变化首帧卸载且不自动读新 scope；重开需重新点击入口。

## 3. 隔离 HTTP 与迟到响应

- [x] 3.1 fixture 仅新增当前 Bearer、无 query 的粗剪 GET，以 env+restart 控制合成场景；真实 HTTP 测试验证两用户同章隔离、正常/unsaved/empty/removed/legacy/pending、代表性权限与错误、真实 invalid JSON/DTO 与 body timeout，静态核对日志不含 token/正文/ID/视频 URL，无控制 HTTP、外部请求/媒体/写调用，白名单恰 12 种（11 GET+登录 POST）。
- [x] 3.2 面板用独立 deferred Promise 验证 pending 重读、关闭重开及直接 rerender user/services/chapter scope 的旧 success/401；实际 settle 原 Promise 并 await act/flush，证明旧内容/错误不显示、旧401不退出、无跨 scope 自动GET或私有缓存，不以仅 signal.aborted 充当证明。
- [x] 3.3 ChapterBrowser/Workspace 用真实 settle+flush 反例覆盖切章、章节/原图重读、返回换剧集以及退出新用户登录后的旧粗剪 success/401；证明新面板/新token不被覆盖，互斥切换不恢复旧数据，返回保留列表筛选与已展示数量。按事件风险选代表性用例，不制造机械全组合矩阵。

## 4. 真实浏览器验收

- [x] 4.1 root 用默认 demo 在桌面/390px/键盘验收显式开关、重读、顺序/位置/状态、返回列表保留筛选与扩展数量、退出、hidden/inert/焦点及无横向溢出；合法空草稿由 DTO/UI 自动用例及 4.2 隔离 API 浏览器验收，默认 demo 保持原有非空章节样本。回归登录/章节/素材/制作记录/任务/团队筛选，记录实际截图和步骤，demo 保持无网络草稿读取。
- [x] 4.2 root 用 loopback API fixture 验收零粗剪预取、两用户同章差异、正常/未保存/空/失效引用与代表性错误重读、pending 关闭/切章/退出；记录请求日志的 12 种边界及无视频访问，关闭临时页与 API 服务。区分 browser abort 和自动 deferred 实际迟到证明，不称真实 FastAPI/数据库或媒体回归。

## 5. 集成与证据收口

- [x] 5.1 完成实际 frontend typecheck/test/build、fixture node syntax 与独立 GPT-6.1 Sol/xhigh 只读源码/测试复审；记录实跑数量/结果、未解决 P1/P2 和范围，不用定向代理结果冒充 root 全量或用审查代 Grillme。
- [x] 5.2 更新本 change verification、当前 README/兼容说明并严格校验；以实际文件文本扫描、exact-five verifier/hash/敏感模式检查、前六 change 36 份历史 hash 比对及 frozen HEAD/status 保持证明边界，区分无 tracked diff 与真实覆盖。仅新库隔离交付，无旧库写入、提交/推送/部署，证据分别列静态/自动/隔离HTTP/浏览器与未运行真实服务。

## 6. 独立待办

- [ ] 6.1 按固定 GPT-5.6 Sol/xhigh 完成 Grillme 并记录证据；当前离线则保持本项未完成，记录离线事实，不启动/配对/降档或由 Sol 代码审查替代，不阻断已授权本地开发。
