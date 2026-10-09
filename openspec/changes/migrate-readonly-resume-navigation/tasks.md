# Tasks

本清单仅覆盖第八切片；按实际证据勾选。仅使用既有十二条业务接口，不扩展复制来源或真实服务；固定模型 Grillme 离线待办独立保留。

## 1. 定位与读取上下文

- [x] 1.1 为 notes 打开上下文和 ready 读取结果绑定 user/services/series/chapter 对象/资产快照/openEpoch/请求世代；首帧失配即隐藏并永久关闭，旧定位回调不可用。以 direct rerender、布局阶段和 A→B→A 代表性用例证明旧 ready 不绑定新 scope，重开需显式点击。
- [x] 1.2 在已核对 resumePosition 处提供显式定位入口；pending/checking/error/null/invalid/mismatch 或资产不可核对时不开放。以面板用例验证有效、needs_reconfirmation 可定位、无自动定位、无效和重读时即时不可定位；不修改原投影判断。

## 2. 本地导航与生命周期验证

- [x] 2.1 ChapterBrowser 用当前作用域的镜头引用同步聚焦和滚动，目标有名称、tabIndex=-1、可见焦点/位置指示及文本反馈；保留面板打开，关闭/切章/重读/互斥切换/离开使反馈失效。UI 用例证明正确目标与重复点击，无断开或旧目标定位、无新增业务调用或媒体节点。
- [x] 2.2 针对旧 success/401/摘要与旧定位 callback 的生命周期完成真实 settle+await act/flush 代表性反例，覆盖重读、关闭重开、scope 变化、切章与退出新登录；旧结果不恢复定位或注销新会话。保留既有服务接口/fixture/demo 样本与只读返回筛选行为。

## 3. 真实浏览器与集成

- [x] 3.1 root 在默认 demo 完成桌面、390px 与键盘定位/焦点/滚动/反馈、重复定位、重读/关闭/切章和返回验收，并留截图与实际观察；确保 no automatic jump、hidden/inert 焦点与无横向溢出。
- [x] 3.2 root 在 loopback API fixture 以两合成账号验证定位及无位置/失效/读取错误，比较点击前后业务请求次数；十二条现有业务路由、既有安全 PNG 与 OPTIONS 边界保持，无新媒体/外部或写调用。关闭临时 API 页签与服务，不以 fixture 代真实后端。
- [x] 3.3 root 独立执行实际 frontend typecheck/test/build、源合同和 exact-five verifier，完成 GPT-6.1 Sol/xhigh 最终只读代码复审，记录各层实际结果和未运行范围；不以定向代理结果替代 root 全量或 Grillme。

## 4. 文档与隔离边界

- [x] 4.1 更新本 change verification、当前 README/兼容说明，严格校验与最终实际文本扫描；核对前七 change 42 文件历史 hash、四个固定源全文 hash、旧 HEAD/status 与 exact-five 复制名单，记录未提交/推送/部署及未运行真实服务。

## 5. 独立待办

- [ ] 5.1 按固定 GPT-5.6 Sol/xhigh 完成 Grillme 并记录证据；服务离线时保持未完成，不启动/配对/降档或由 Sol 复审替代，不阻断已授权本地开发。
