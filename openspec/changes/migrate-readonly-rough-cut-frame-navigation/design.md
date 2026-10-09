# Design

## Context

见 proposal 的动机。当前 `PersonalRoughCutPanel` 仅展示草稿顺序、读取时位置和纳入/待安排状态；`ChapterBrowser` 已有镜头文章 refs 及个人续作、素材反查的焦点滚动流程。粗剪打开上下文只绑定用户、服务、剧集、章节对象和打开世代，允许原图尚未就绪时读取草稿，正常原图完成不关闭面板。

旧库仅静态读取冻结 SHA `23403806898550a7668a6ee7c0c457315655c39b`。来源证据与本批新增 UI 分开：

| 冻结来源 | 事实与本批用途 |
| --- | --- |
| `backend/app/routes/rough_cut.py:79–130` | 稳定身份取章节 `storyboard[0]`，章节内重复或找不到原图记录则失去稳定 ID；缺视频独立于稳定身份。 |
| `backend/app/routes/rough_cut.py:133–200` | 已保存草稿保留其顺序，新镜头追加为排除且 pending；文本、位置和 preview 取当前章节/记录，不是冻结媒体版本。空 ID 和重复 removed ID 有合法历史含义。 |
| `backend/app/routes/rough_cut.py:203–230`、`backend/app/models/rough_cut.py:19–42` | 草稿按 chapter_id+user_id 私有读取，GET 无 response_model、无显式 commit；认证仍可能维护会话，不能称整个请求为数据库纯读取。 |
| `js/rough-cut.js:375–429,515–530` | 旧 UI 提供纳入、排序、保存、试看、导出，没有从粗剪定位章节文章的动作。本批是新只读衔接，不搬迁旧管理/播放链路。 |
| `js/episode.js:10121–10160` | 个人续作流程曾校验本章唯一 storyboard ID；其旧页选择流程与认可/媒体核验不作为粗剪定位权威。 |

关键全文 SHA256：

| 文件 | SHA256 |
| --- | --- |
| `backend/app/models/rough_cut.py` | `a48499a709ceb59ac0217fa6f6829497b2b3f4691bdef30a4f146996c999e12b` |
| `backend/app/routes/rough_cut.py` | `ce8f1668eba0776d748e537b23441a6869406a1e36fbf62e1d59abf20673d642` |
| `js/episode.js` | `79b117ce13e633b6b641faca7232328358e8e6f3be40825dcf097f87994d0e9f` |
| `js/rough-cut.js` | `9504b3b22615dc640152d1f18661420ea39e82da78299036a12c02475515c5cf` |

其余来源共 11 份的全文 hash 见 `/Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/haoai-next-rough-cut-frame-navigation-sources.json`；这是静态来源清单，非运行后端的证据。此前 78 份历史、85 份受保护文件、exact-five 来源复制不扩张。

## Goals / Non-Goals

**Goals:**

- 新局部纯 helper 给出可核验的当前镜头位置，组件提供真实焦点、滚动和独立反馈。
- 保留现粗剪读取、错误重试和私有会话隔离；增加定位不改变读取时机。
- 入口可访问名包含当前核对出的“镜头 N”，保留草稿序号和原 wire frame_index 的原展示。

**Non-Goals:**

- 不改 DTO、services、API/fixture、demo 样本、已有投影/媒体摘要/helper；不增加 Workspace 导航意图或跨章导航。
- 不把 included/pending、preview、missing_reason、revision 或认可状态当作定位授权；不播放、保存、导出、编辑、生成、认可或取锁。
- 不要求媒体摘要：定位只标识当前镜头文章，不批准其媒体或恢复制作权限。耦合 SHA 摘要会错误阻止缺视频但身份合法的记录，并引入无关异步流程。

## Decisions

### 1. 独立稳定身份 helper

新增 `features/chapters/roughCutFrameNavigation.ts`。输入当前 chapter、seriesId、ready rough-cut snapshot、该快照的实际 row，以及完整 ready storyboard-assets。返回实际章节位置或明确不可定位结果：

1. 草稿 chapter_id、章节 series_id 与当前上下文精确相等，row 必须属于该快照；asset_id 是非空白字符串，保留原始值。
2. 完整草稿 frames 中该非空 raw ID 恰好一次。DTO 已拒绝重复，但 helper 仍防御绕过 decoder 的测试桩。
3. 当前章节 content 中 first storyboard raw ID 恰好一次，不检查其他 storyboard 元素；从此推导章节位置。
4. 完整 assets 响应先计同 raw ID，恰好一次后才核 series_id/chapter_id。不得先过滤归属再计数。

不读 frame_index 来选择或否决目标，不用草稿数组下标、文本、URL 或名称替代身份。空/重复/缺失/foreign 结果只说明不能核对，removed 列表仅保留原计数。复用现素材定位 helper 会要求虚构 category/asset 引用，因此采用独立小 helper，保护既有第十二批算法。

### 2. 打开 owner 与定位快照分层

`ChapterBrowser` 的粗剪 owner 保持 userId、services 对象、seriesId、chapter 对象、openEpoch；新增当前 owner ref，使定位、关闭和失效回调均带期望 owner 并严格匹配。scope 首帧不符立即卸载并永久作废旧 owner；A→B→A、关闭重开不能重新认可旧 owner。

定位 ticket 另绑定 Panel 当前读取 generation、ready snapshot 与 row、当前 ready assets 对象及 assets 请求 generation。Panel 持有最新 scope/readState/generation ref；ticket 的 current 检查同时核对所有引用和世代。父级在处理回调时独立核当前 owner、用户/服务/剧集/章节、assets 快照及请求 generation，并重新执行身份 helper；聚焦前后均再调用 ticket current 检查。旧读取回调不得通过缓存章节对象或同 ID 复用。

父 owner 不绑定打开时 loading/error assets。正常资产成功只替换当前定位 snapshot、更新可用入口，不关闭 Panel、不清空草稿、不重新 GET 或自动 focus。定位不可用时继续显示合法草稿及说明；不自动调用原图重读来补齐资格。

### 3. 真实 DOM 定位与独立反馈

按钮文本/可访问名使用当前身份匹配得到的镜头序号，例如“定位到对应镜头 2”。Panel 的 wire 序号仍按草稿展示，raw ID/preview 不写入 DOM 属性、链接、媒体节点或日志。

父级从已有 frame article refs 取得匹配位置的节点，核验节点连接、属于当前列表、未处于 hidden/inert 祖先。调用 `focus({ preventScroll: true })` 后确认 `document.activeElement` 是该节点，重新核验 owner、ticket、当前快照/请求世代与同一 DOM 节点，再执行 `scrollIntoView({ behavior: 'auto', block: 'center' })` 和显示“已定位到对应镜头 N”。没有真实焦点或当前性失效，不 scroll、不报成功；当前有效面板提供未定位说明，可再次显式点击。

粗剪反馈有独立 owner/ticket，镜头 article 失焦（含转向内部 button）即清除；旧失效回调只能清自身反馈，不能清新 owner 或其他定位流程的反馈。没有新计时器、自动重放或导航 intent 消费流程；当前合法入口可重复点击，仍为零请求。

### 4. 清理与在途读取

Panel 显式重读即增加读取 generation、中止在途请求并隐藏旧入口/反馈；新 ready 不自动定位。当前 401 沿用既有会话处理，非 401 错误与空草稿保持分层；旧成功/401 即使忽略 abort 后真正 settle 也无效。

关闭重开、切章、章节对象或用户/服务变化、显式章节/原图重读、返回列表、进入任务和退出均作废粗剪 owner 与反馈，保留现 notes/rough/关联素材面板互斥。普通原图请求正常完成是唯一特意不关闭面板的更新。旧 close、locate、invalidated 回调实际重放必须不能影响新面板或新焦点。

### 5. 精炼验证与保护范围

产品 mutable 仅六个既有文件：`PersonalRoughCutPanel.tsx/.test.tsx`、`ChapterBrowser.tsx/.test.tsx`、`Workspace.test.tsx`、`styles.css`；新增 helper 两个文件。文档收口另由主代理按实际证据更新当前 README/compat 与本 change verification/tasks，不修改历史产物。

自动测试优先验证唯一身份与 draft-order 反例、included/pending/无视频可定位、ready 资产迟到不关闭、真实 focus/scroll、旧 callback 回放、首 layout commit 隔离及真正 deferred settle+await act；不把 signal.aborted 或静态 guard 当作迟到响应/真实 DOM 验收。使用现有 demo 第一章已保存草稿的反向顺序及 pending 排除条目，不改 24 剧集、章节、媒体、notes/rough 样例；API 浏览器仅现有 fixture 能表达的正常/unsaved/legacy/error/delay。身份歧义与真实忽略 abort 的响应由自动 mock 覆盖。

## Risks / Trade-offs

- [原图未就绪时无法核验定位] → 粗剪照常可读，清晰区分“记录可读”和“定位暂不可用”，不增加补读。
- [同 ID 与缓存对象在 ABA 中复用] → 打开 owner 永久失效、请求世代与快照引用双层守卫，首提交和旧 Promise 真正 settle 反例验证。
- [无视频被误当不可定位] → 身份 helper 不读取媒体或编排资格；UI 保留缺视频提示，不赋予播放/导出含义。
- [jsdom 不等价原生焦点/滚动] → 自动测试验证实际节点和调用分支；主代理独立桌面/390px、键盘和请求日志验收，分别记录层级。
- [指定 Grillme 离线] → 固定 GPT-5.6 Sol / xhigh 保留独立待办，不启动、配对、降档或用本 Sol 设计/审查代替，不阻断已授权本地开发。

## Migration Plan

仅本地 React 垂直切片：Sol 规划复核后，Luna 6 / xhigh 实现上述范围；主代理独立自动检查及 demo/API 浏览器验收，按实际结果收口文档。不启动真实旧后端、DB、Worker、R2 或付费调用，不部署或归档；需回退时移除本批局部入口/helper，保留原粗剪只读浏览及原接口。
