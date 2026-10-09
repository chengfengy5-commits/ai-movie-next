# Tasks

本清单仅覆盖第十批；现有十二条业务接口、exact-five 来源复制和前九批 54 份历史产物保持。

## 1. 局部投影与面板

- [x] 1.1 新增 assets 局部反查投影，按分类及原始 ID 精确匹配，章节/镜头保序，一镜头一行及重复次数；用单元测试验证同 ID 跨类型、空/坏引用、mixed 不确定、缺失/坏文字、HTML 字面及归属/身份异常，不改旧投影。
- [x] 1.2 新增文字反查面板，显式打开一次 chapters GET、重读立即隐藏旧快照、关闭重开重新读取；用 UI 测试验证成功/零匹配/空章节/不确定/代表错误及 current401，无新媒体/链接/结构 ID/JSON。
- [x] 1.3 owner 与请求世代绑定完整上下文，首提交失配永久关闭及 ABA 不复活；用真实结束原 Promise + await act 的迟到 success/401、重读覆盖与 StrictMode 用例验证不污染或注销新会话。

## 2. 素材目录集成

- [x] 2.1 卡片具名入口、一次一个面板、有效身份门禁；切分类/换素材/目录重读/离开使旧 owner 与关闭回调失效，普通图片成功/失败不关闭；用集成测试验证零预读、保序、重复身份/跨剧集入口拒绝及旧回调实际重放。
- [x] 2.2 父级目录 result/cache/请求绑定 user/services/series，变化首帧隐藏旧目录并清缓存；用首 layout commit 和 A→B→A/deferred 反例验证，Workspace 退出新登录迟到 success/401 真实 settle 保护新账号。
- [x] 2.3 合成样本、DTO/services/旧投影和个人记录/粗剪保持，用保护文件 hash 与既有回归证明无样本和接口扩展。

## 3. 独立检查与浏览器

- [x] 3.1 root 对冻结最终代码执行 typecheck/full test/build、fixture node --check、来源合同及 exact-five verifier，记录真实退出码和总数；代理定向不替代全量。
- [x] 3.2 root demo 浏览器验收三类反查文字、跨章保序、打开/关闭/重读/切类/换素材、返回数量筛选、键盘和390px，保存截图与观察，不增加媒体。
- [x] 3.3 root loopback API 两账号验收成功/道具零匹配、GET 次数、既有错误/空/延迟取消，审计实访路由；关闭临时 API/fixture/页签，区分浏览器取消与自动化迟到 Promise。
- [x] 3.4 GPT-6.1 Sol/xhigh 最终只读复审实现/测试，无未解决实证 P1/P2；必要修复按 Luna 6/xhigh 路由复验，Sol 不代替 root 或 Grillme。

## 4. 文档与隔离

- [x] 4.1 Luna 更新当前 README/兼容说明及本 change verification/tasks，root 实跑 strict/实际文本扫描、历史54 hash/冻结来源全文hash/旧HEAD状态；分层记录真实后端及提交/推送/部署未运行。

## 5. 独立待办

- [ ] 5.1 固定 GPT-5.6 Sol/xhigh Grillme 独立审查；当前离线保持未完成，不启动/配对/降档或以本地验收替代，不阻断已授权本地开发。
