# Design

## Context

动机见 proposal。当前 `PersonalProductionNotesPanel.tsx:149-163,597-629` 已有六种状态标签，当前镜头列表只有全量 map；`632-670` 将旧记录与续作区域独立展示。`personal-production/projection.ts:27-53,245-269` 已提供原位置、状态、verified、hasSavedNote 与独立 orphan；状态筛选不能重新解释这些数据。第十五批逐行定位要求同份 readout 中实际行对象，本批只保留这条链路。

冻结来源 SHA 为 `23403806898550a7668a6ee7c0c457315655c39b`，本轮仅以 git show 静态核对：

| 全文来源 | SHA-256 | 静态依据 |
| --- | --- | --- |
| js/episode.js | 79b117ce13e633b6b641faca7232328358e8e6f3be40825dcf097f87994d0e9f | 9861-9875 全量渲染；9894-9946 状态、返工备注、重确认提示；9978-9995 旧记录独立区 |
| backend/app/routes/personal_production_notes.py | 3fd7acc882fbeea0ed7500ef2a5f901a613aae14ce7bd893879dd669a6842562 | 108-121 当前用户访问与 load_personal_production_snapshot(commit=True) |

旧来源提供状态和备注语义，已核对渲染段没有本地只读状态筛选；本批是新 UI 改善，不声称恢复旧有筛选。旧 GET 的媒体元数据/同章认可维护和认证 session 副作用仍按第四批保留，不执行真实 GET 或称数据库纯读取。两份全文均属既有冻结来源，不新增复制；root 已捕获前十五批90历史/91受保护代码/13固定来源基线。

## Goals / Non-Goals

**Goals:**

- 用既有状态整理这一份当前列表，七种筛选和数量对同一完整快照一致。
- 保持逐行原对象、原位置、可信定位及完整安全信息；新读取不能继承旧筛选。
- 在原样 demo、现 fixture 与自动 mock 的能力范围内分层验收。

**Non-Goals:**

- 不生成无保存记录的逐行列表，不筛选 orphan，不重新核对/归并状态，不改媒体摘要或身份算法。
- 不改读取登记、父端水位/失效、原续作或定位协议；不做搜索、排序、编辑、认可、保存、自动跳转、URL/持久状态、后台或媒体功能。

## Decisions

### 1. 小型纯筛选 helper，使用投影类型

新增 `features/chapters/productionNoteStatusFilter.ts/.test.ts`，仅以类型引用既有 CurrentNoteStatus 与 PersonalProductionFrameView。建议类型 `ProductionNoteStatusFilter = "all" | CurrentNoteStatus`，提供六状态与 all 的完整计数、按选项过滤原行的函数；选项顺序为全部、待修、待重新确认、未标记、已认可、当前媒体暂不可核对、记录不可识别。顺序是本批 UI 选择，不改变状态含义或原行顺序。

计数由完整 readout.frames 计算，与当前选中状态无关；all 为行数，各状态按已投影的 frame.status 精确统计，orphan 不参与。过滤只返回原行引用，不 clone 行、不重新编号、不构造替代 readout；现有定位仍收到完整原 readout 和原 frame。hasSavedNote 不作为额外筛选或定位权限。不可识别/不可核对仍是独立状态，不能从 raw approved 推为当前已认可。

选择局部 helper 是为了用小型纯测试说明顺序与对象不变；直接在 JSX 多处统计容易口径分裂。服务端过滤、projection 改动与搜索均不需要。

### 2. 控制和零命中只围绕原可见列表

Panel 仅在当前 visibleReadState 为 ready、readout 非 noSavedRecord 且 frames.length>0 时展示筛选按钮组；这是读取 ready，不额外要求 mediaState=ready。loading/checking/error、无保存记录或无当前行时不显示旧控制、数量或新增列表；原有 callout/orphan/resume 按原规则保留。

每个按钮有可访问名称、数量和 aria-pressed，零数量仍可选择。结果用原序号“镜头 N”；零命中说明为“这份当前镜头记录中没有该状态”，配显式“查看全部记录”按钮，不能使用无保存记录或接口空态文案。按钮组允许换行，390px 保持可阅读，正文仍纯文本。

全局警告、orphan 和 resume 不放进过滤条件，也不以过滤结果重算 hasUnverifiedFrames/canLocateResume。当前筛选不改变可信记录定位条件或新增入口；已有全局状态、未保存行展示规则及安全提示保持。

### 3. 选择绑定读取身份，首帧隔离而不改父协议

Panel 局部选择记录 `{readout, requestGeneration, filter}`，并绑定生成事件的当前 read ticket。渲染时仅当选择身份与当前 ready 的实际 readout 引用、requestGeneration 都相同且 ticket 仍 current，才使用其 filter；否则直接呈现 all。不能仅在 useEffect 后重置，以免 R2 的首个提交套用 R1 选项。

事件处理使用捕获的读取身份/ticket，并核对现 readStateRef、当前 readout 实际引用、请求世代与现有 scope guard；旧 R1 回调即使实际重放，也不能更新 R2 或重开后的选择。相同选项重复点击保持幂等。重读使旧 ticket 作废且控制隐藏，新的 ready 默认 all；关闭重开及 user/services/chapter 对象变化沿用既有首帧隐藏和永久 scope 失效，A→B→A 不自动读取或复活选择。

本地筛选 MUST 不进入 GET/摘要 effect 依赖，不调用 invalidateActiveRead、read register/invalidated、parent close、resume invalidation 或改变 requestGeneration。现 read identity/tombstone 完整保留。用户点击/键盘焦点到按钮造成 article 正常 blur 时，既有父反馈自然清理即可，不人为失效读取；被动呈现不得清除父读取身份。

### 4. 样例与验证层级

原样 demo 第一章当前两行是待重新确认和待修，另有一条 orphan；可演示数量、分开查看、已认可零命中、全部恢复、原行定位与续作保留。其他章无保存记录的原规则保持，不改24剧集、章节或个人数据。

现有 API fixture 第一账号合格章有已认可/待重新确认，第二账号有待修/已认可，mismatch 模式可呈现不可核对；原环境变量/restart 能力足够，不扩 fixture。未标记/记录不可识别及完整六状态使用自动 mock，浏览器只记录实际样例状态，不声称覆盖全部六状态。

helper 单测覆盖计数、保序、原对象和当前投影口径；Panel 覆盖零命中、安全区持续可见、读取绑定、旧筛选 callback 与真实旧 GET/401/摘要 Promise resolve/reject 后 await act。旧筛选闭包通过观察性公开 JSX runtime mock（如 react/jsx-runtime）保留正常元素创建、捕获原 button props.onClick，再真正调用并 await act；不得把 detached DOM fireEvent 当作闭包回放，不读取 React 私有 Fiber，不增加产品测试 hook。回调须核对最新 readStateRef/ticket/currentScope，而不是只看捕获时的 ready。已有首父布局及迟到用例可增强筛选断言，避免复制无关矩阵。Workspace 通过真实整合覆盖两用户、返回24条/筛选、任务页与原续作/逐行定位。

root 在1280和390视口验收键盘、数量和原导航；按点击前后日志观察 filter/locate 的零附加业务请求。开发 StrictMode 开面板和显式重读沿用已有 GET，按实际记录，不称整个交互零请求或零既有图片读取。浏览器取消和 server late200 不能替代自动 ignored-abort 原 Promise 真正 settle 证据；真实 FastAPI/PG/Worker/R2/付费与生产均未运行。

### 5. 变更文件白名单

本批仅允许修改四个现有文件：`PersonalProductionNotesPanel.tsx/.test.tsx`、`Workspace.test.tsx`、`styles.css`；新增上述 helper/test 两文件。ChapterBrowser 产品及测试、Workspace 产品、projection、DTO/services、所有媒体/导航 helper、fixture/demo、依赖和历史全部保护，十二业务模式/exact-five不变。验证及当前 README/兼容说明在实施后由 root 指定代理单独收口；本规划阶段仅写此 change 四 artifact。

## Risks / Trade-offs

- [数量被当作保存条数或全剧进度] → 明确当前镜头列表状态数量；按原可见规则展示，零命中只指这份读取。
- [raw approved 或历史记录混入当前认可] → 只读既有投影 status，orphan 独立，不改 verified 与定位资格。
- [过滤 clone 行或缩小 readout 破坏第十五批身份] → 原 row refs、原位置、完整 readout 传递，不改父端。
- [R1 选择或旧 callback 污染 R2] → render 时按读取身份默认 all，事件再核对 current ticket/世代，真正重放并 settle 举证。
- [effect 依赖引入重复 GET/摘要] → filter 只作本地呈现，服务与 capture spy/API delta 验证。
- [样例有限被包装为全状态浏览器覆盖] → 自动化完整状态与原样浏览器代表状态分开记录，不改样本。

## Migration Plan

root 复读四 artifact、strict/apply ready 后由 Luna 实施 helper、Panel 与局部样式，Workspace 测试独立配合；完成自动化后 root 本地 pipeline/浏览器和事后四文档收口。回退仅移除筛选 UI/局部状态/helper，原读取、完整列表、续作与定位保留。

本轮沿用已明确的同回合规划后 apply 授权；此子任务仅规划，不实施。无提交/推送/部署/归档或真服务。指定 GPT-5.6 Sol / xhigh Grillme 离线待办独立列出，不启动/探测/配对/降档，不以本 Sol 审查替代。模型为既有分析路由，不冒称新增 runtime 独立核验。
