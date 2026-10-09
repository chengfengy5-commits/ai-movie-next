# Tasks

## 1. 冻结契约与服务

- [x] 1.1 实现独立团队DTO/decoder并记录六份冻结全文hash，保持既有decoder及exact-five不变；验证六个required-own字段、合法UTC/带时区ISO日期、非负safeint、重复id/坏结构拒绝，以及空名称/未知角色/非UUID/all/__proto__/同名与合法空数组保留，定向契约测试通过。
- [x] 1.2 增加listMyTeams专用静态GET adapter与无网络demo目录，更新受影响mocks；验证当前Bearer、无query、abort/body-timeout/invalidJSON分类复用、登录和其他分类不预读；demo保持原单账号及24剧集，只新增目录数据，不增加依赖或媒体网络。

## 2. 列表筛选与生命周期

- [x] 2.1 接入带label原生团队select、独立null全部哨兵及三类空态，按team_id本地过滤后20条递增；验证同名/空名/无剧集团队、目录外本人共享剧集仍可见、can_enter原样、实际切换才reset20且重复选择幂等，不请求team详情/分页/写接口。
- [x] 2.2 接入Workspace active及按userId/services绑定的页面内存snapshot、generation/abort、显式重读与原子失选重置；验证隐藏/离分类取消、成功snapshot返回不GET、首次pending中止后返回新GET、实际错误只显式重试、换scope首次呈现无旧名称，以及重读pending仍可换团队并按响应应用时的当前选择判断。

## 3. 隔离HTTP与有意义的自动反例

- [x] 3.1 在fixture仅增GET /api/teams/my，通过环境变量和重启提供两账号正常目录、empty/removed、401/403/404/422/500、invalidJSON/invalidshape与delay；以真实fetch HTTP测试验证currentBearer账号隔离、完整六字段/响应顺序/无query/空与错误区别及十一条业务边界，node语法检查通过，日志无token/密码原文，不新增控制HTTP端点。
- [x] 3.2 添加列表/UI正常与错误测试：独立构造45条验证先filter后20→40、返回保持及同分类幂等；覆盖空目录/一个团队/无剧集团队、all合法id与HTML纯文本、刷新移除已选团队同步select/state/count、pending改选后响应按当前id应用；至少当前非401错误保留会话和原剧集后显式retry恢复，不把错误当empty。
- [x] 3.3 添加真正deferred生命周期反例并await原Promise settle与React actflush：pending refresh旧success/401不污染新目录，隐藏到任务再返回无snapshot时新GET且旧success/401无效，已成功往返章节/素材/任务不额外GET；退出→新用户旧success/401不写新缓存不清新会话，直接rerender新user/services即刻隐藏旧团队，错误离开返回不自动重试；断言请求计数、实际选中/卡片和token/onUnauthorized，不仅检查signal.aborted。

## 4. 真实浏览器隔离验收

- [x] 4.1 在默认demo以1280桌面与390移动验收四分类、all20→24、团队下拉/同名与空名/无剧集团队、切team重置、重复点击不重置、返回章节/素材/任务保留列表状态、受限卡片无入口、Tab可见焦点及无横向溢出；记录截图与实际交互，确认demo无业务网络。不把24条demo声称为40条浏览器验证，40条由自动构造集验证。
- [x] 4.2 在隔离loopback API真实浏览器验证两账号目录、正常/empty/removed状态、移除当前团队后回到全部且16条共享剧集仍可见、非401代表错误与retry、当前401、pending刷新/隐藏返回/退出和已有章节/素材/任务回归；用环境变量重启fixture，不连接旧服务；核对日志仅十一业务接口与本地合成媒体，关闭临时API进程/tab并记录实际范围。浏览器未声称观察到20条 API 剧集；20→40 与移除后分页重置由45条自动构造测试验证，browser abort与真正迟到settle自动证据分开。

## 5. 集成与交付记录

- [x] 5.1 最终冻结后独立运行frontend typecheck、完整test和build，以及source-baseline-contract测试/verify-source-baseline；验证exact-five及敏感模式结果、旧冻结HEAD/既有untracked状态、前五change三十文件hash不变，并扫描实际文本文件的空白/冲突/EOF，不能用untracked空git diff冒充覆盖；记录命令、退出码与实际数量。
- [x] 5.2 完成GPT-6.1 Sol/xhigh独立只读源码/测试终审并处理实证P1/P2，更新本change verification及当前README/compat迁移状态，中文区分静态/自动/真实浏览器/未运行真实FastAPI与DB/生产；验证openspec validate --strict与apply ready、六来源全文hash和十一接口口径一致，不回写五个历史change、不commit/push/deploy。

## 6. 独立Grillme待办

- [ ] 6.1 仅在Grillme可用时以固定GPT-5.6 Sol/xhigh完成本change独立审查，保存实际模型、输入和结论后才勾选；当前127.0.0.1:3939拒绝连接，记录“未完成，离线”，不启动/配对/降档、不以Sol终审替代，离线不阻断正常本地开发。
