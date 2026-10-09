# Tasks

## 1. 冻结来源与 typed 契约

- [x] 1.1 核对 design 的三 GET、继承响应字段、访问及 duplicates 隐含写证据；验证：冻结 SHA/全文 hash 对齐，exact-five 来源验证仍成功，legacy-reference 无新增文件，前两 change 历史与旧库 tracked/untracked 状态保持原样。
- [x] 1.2 增加完整 Character/Scene/Prop DTO 和三个严格 list decoder；验证：静态对照冻结 schema 时计入父模型 aliases/canonical_key，自动例覆盖 required nullable 的 null/缺失/undefined/空字符串、父字段缺省、空名称、坏 aliases/日期/外层、跨 series/单类型重复 id；不修改前两切片 decoder 行为。

## 2. 只读服务与界面

- [x] 2.1 增加约定的三个 typed GET 方法与本地 demo 素材；验证：三方法路径/Bearer/abort/error 一致，demo 有多类型、别名与角色特征且业务/媒体 fetch 零调用，只有新增三个 GET，不读取旧素材缓存或连接真实服务。
- [x] 2.2 实现允许剧集的素材入口、共享剧集上下文、三类按需浏览、返回与重新读取；验证：can_enter=false 无入口，默认角色，保留响应序、返回筛选/展示数、只读文字/可信图片，成功类型缓存只限当前上下文，图片失败按 series/type/id 隔离，重新读取重置当前类型破图状态，无写/音频/全局素材入口。

## 3. 契约与 UI 自动验收

- [x] 3.1 扩展隔离 HTTP fixture 与真实 fetch 测试；验证：三个 GET 正常、nullable/父字段/空数组/Bearer和路径编码符合冻结响应，八条业务白名单和日志无秘密；共享错误选代表类型覆盖 401/403/404/422/500、header/body timeout、invalid JSON/结构、cancel，不机械铺三类型全矩阵，不启动旧后端。
- [x] 3.2 增加有意义的素材 UI 完整路径与反例；验证：本人/团队上下文可读、受限入口不可执行，角色→场景→角色缓存、重复点击、合法空类型/空名称/别名、同 id 跨类型图片隔离、HTML纯文本、远端/音频/rawJSON拒绝、破图后同URL重新读取恢复，当前401注销、其他错误保留会话并只重试当前类型，旧章节流程及列表返回仍通过。
- [x] 3.3 增加忽略 AbortSignal 的 deferred 自动测试；验证：切类型旧成功不显示或缓存，重新读取旧401不覆盖新结果，返回后不同剧集和退出后新用户的旧成功/401不恢复数据或清新token；原 Promise 明确 resolve/reject并在 await act/React刷新后断言，避免只等待已成立状态。

## 4. 真实浏览器验收

- [x] 4.1 用真实浏览器验收 demo；验证：登录→筛选/加载→素材→三类型→返回→既有章节→退出，键盘可到达入口/类型/重新读取/返回，桌面与移动无横向溢出，hidden/inert列表不留焦点，无业务/远端媒体请求，记录实际操作/截图。
- [x] 4.2 用真实浏览器验收隔离 API；验证：同id跨类型/别名/合法空数据/本地合成图片、破图恢复与重新读取、当前401/403/500/invalidJSON及重试的代表场景符合契约，日志只有八业务接口/OPTIONS/允许的本地资源，无duplicates/ignored/锁/写/生成请求，退出并关闭临时API进程/tab；不宣称真实FastAPI/PG回归。

## 5. 收口与当前文档

- [x] 5.1 完成全量类型/测试/构建、来源/OpenSpec验证及独立 GPT-6.1 Sol / xhigh 复审；验证：frontend typecheck/test/build、来源合同测试/verifier、OpenSpec strict成功，实际文件空白/冲突/EOF检查通过，修复实证P1/P2，分别记录自动、真实HTTP及浏览器证据，不以无tracked diff的git diff --check代替。
- [x] 5.2 更新当前README、模块兼容说明及本change verification；验证：记录三新增GET/八业务白名单、schema继承/媒体/权限/清理边界、实际命令结果和未运行真实后端层级，保持前两change历史、不提交/推送/部署，Grillme离线独列。

## 6. Grillme 固定模型补审

- [ ] 6.1 Grillme可用时以GPT-5.6 Sol / xhigh审查并解决实证问题；验证：记录真实模型/结果。当前离线保持未完成，与本地开发并行，不启动/配对、降档或用当前Sol源码审查冒充。
