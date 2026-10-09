# Tasks

## 1. 来源引用身份与入口

- [x] 1.1 新增局部来源身份 helper，从分镜类别引用原数组索引读取 raw ID，并与同一 ready 类别目录中的唯一、同剧集记录逐行对应；覆盖原序、合法重复引用、重复/跨剧集/空 ID、缺项和同名异 ID，确认既有关联素材投影 `frameAssetReferences.ts` 及测试保持不动且不增加来源 GET。
- [x] 1.2 在已解析引用行显示可访问的素材导航入口，对无法核对的行显示不含 ID 的说明；验证角色、场景、道具、旧面板关闭/重读及迟到来源回调行为，并确认只有用户点击才触发工作区导航。

## 2. 工作区素材意图

- [x] 2.1 新增独立的一次性素材导航 intent，接受前由 Panel→ChapterBrowser 桥接→Workspace 复核来源 ticket、当前 owner、user/services/series、chapters 视图和 `can_enter`；接受后复制 raw asset ID 与作用域，且来源自然卸载不撤销它。验证父级当前上下文对象不匹配、同会话同剧集视图 A→B→A 与旧 callback 重放均无额外 intent/GET，当前 callback 仍能成功。
- [x] 2.2 将素材意图接入素材库，首个可见提交清除旧分类、卡片、错误、缓存与反馈，并直接读取目标类别，不预读角色或复用来源缓存；验证三分类从对应的新 GET 开始、StrictMode 有效意图只读一次、分类回退和首帧 scope 隔离。

## 3. 目标目录核对与定位

- [x] 3.1 在完整目标类别响应中先统计原始素材 ID，再核对唯一记录的剧集归属；仅精确匹配当前卡片身份后执行实际 focus、`document.activeElement` 确认、scope/快照复核、滚动与意图消费。用原序/重排、同名、同 ID 跨剧集、重复和缺失记录验证不依赖显示文本或位置。
- [x] 3.2 对空目录、缺失/重复/跨剧集目标和非 401 错误显示安全未定位状态并保留完整 intent；显式重核只重读完整目标类别，当前 401 走现有注销流程。验证恢复后定位、失败期间不回退、hidden/detached 节点与 focus 未转移均不消费。
- [x] 3.3 覆盖 category/owner/目录重读/关闭/返回/任务页/登出及 user/services/series scope 生命周期；同类重复点击不取消 pending，其他类别手动切换放弃 pending，成功后的普通刷新不重放。用实际 settle 的 deferred success/401 与 `await act` 验证旧请求不会污染或注销新 scope，定位反馈在 blur 时清除。

## 4. 兼容与隔离验收

- [x] 4.1 保持既有章节和分镜导航语义、返回剧集列表并保留既有四筛选、团队选择和已展示数量，并验证不同类别身份可相同但不能跨类别混淆；定向组件/Workspace 测试覆盖 chapter-only 与既有 frame navigation。
- [x] 4.2 在本地 demo 桌面/390px 移动端验证角色、场景和可用道具入口、键盘聚焦、返回与无溢出；隔离 API 浏览器验证两账号、角色/场景新目录读取、空目录/500 后同类别完整重核及延迟请求离开取消。实际忽略 abort 的迟到结果由自动化 deferred 测试证明，不将浏览器取消表述为 Promise settle。

## 5. 最终检查

- [x] 5.1 执行定向和全量 typecheck/test/build、严格 OpenSpec 校验与冻结代码范围检查；保存实际命令、结果和浏览器证据，分开记录自动化、浏览器及 source-level 结果。

## 6. 独立复核

- [ ] 6.1 由固定 GPT-5.6 Sol / xhigh 执行 Grillme 离线复核；本轮未运行该复核时保持未完成，不以其他模型、静态检查或本地测试替代。
