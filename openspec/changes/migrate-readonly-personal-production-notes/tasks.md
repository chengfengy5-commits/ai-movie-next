# Tasks

## 1. 冻结契约与纯核对

- [x] 1.1 核对设计的GET维护/隐私/响应字典及五份hash；验证：frozen SHA和字段来源一致、原exact-five verifier/合同仍成功、前三change18份历史及旧tracked/untracked状态不变；不复制/导入/运行旧后端。
- [x] 1.2 增加notes DTO/严格外层decoder、旧JSON投影及媒体身份/WebCrypto helper；验证：wire全部字段/缺失/null/版本/状态/章归属、坏条目独立提示；临时签名精确名单、未知query编码顺序、fragment/scheme/控制符/authority、已知SHA256与摘要失败反例通过，不称Pydantic运行回归。

## 2. 私有服务与只读面板

- [x] 2.1 新增约定单GET及独立demo数据；验证：chapter路径一次编码、Bearer/abort/errors一致，无user_id或PUT；fixture/demo原图与preview摘要可核对、合法revision0/empty/unreadable可表达，demo业务/media fetch为零。
- [x] 2.2 在当前章接入显式面板与读取/关闭/重新读取；验证：默认无notes请求，成功只显示本人状态/备注/重确认及续作提示；切章/重读章节或原图/离开/退出关闭清上下文，返回保留列表状态，不添加写/定位/粗剪/历史/媒体元素。

## 3. 有意义的自动与 HTTP 验收

- [x] 3.1 扩展隔离fixture和真实fetch契约测试；验证：第九GET完整response/Bearer/路径及两个synthetic用户记录隔离，九接口日志白名单无秘密；代表性401/403/404/422/500、header/body timeout、invalid JSON/结构、cancel/空记录，不执行真实DB维护。
- [x] 3.2 验证稳定身份、投影与UI主路径；验证：缺失/重复/跨章资产不按index配便签，位置/帧数/摘要不符与WebCrypto失败明确不可核对；审批版本旧/A→B→A/双空不能认可，缺省note兼容、unknown/坏类型/HTML纯文本/orphan不计当前摘要、有效与失效resume仅提示；同章重开新读和既有章节/素材/列表回归通过。
- [x] 3.3 验证HTTP与摘要deferred上下文反例；验证：忽略signal的原Promise实际resolve/reject并await act后，切章/章节刷新、pending重读旧401、关闭重开旧digest、返回换series、退出新user旧成功/401均不覆盖新面板/token或恢复旧认可；当前有效401确实注销，其他错误不注销。

## 4. 真实浏览器验收

- [x] 4.1 根代理真实浏览器验收demo；验证：登录→章节→显式打开→记录/重确认/续作/旧记录→重读/关闭→切章→返回→退出，默认无notes预读/业务media请求，键盘可达，桌面与移动无溢出，记录本轮真实操作/截图，不用jsdom替代。
- [x] 4.2 根代理真实浏览器验收隔离API；验证：实际本地WebCrypto核对、无记录/不可核对/代表性错误与重试、重读pending及关闭/切章/退出，日志限九业务接口/OPTIONS/原有本地图片、没有PUT/roughcut/lock/history/媒体新增请求；临时API进程/tab关闭，不宣称真实FastAPI/PG权限或维护回归。

## 5. 收口与当前文档

- [x] 5.1 完成前端及来源/OpenSpec检查与独立Sol终审；验证：typecheck、全量test、build、来源合同/verifier、OpenSpec strict及实际文本空白/冲突/EOF检查成功，修复实证P1/P2，保留自动/真实HTTP/browser不同层级；不以untracked仓库git diff --check代替文件检查。
- [x] 5.2 更新当前README/兼容说明及本change中文verification；验证：明确私有只读交互与旧GET可维护同章其他用户、九接口与摘要/世代边界、真实命令结果及未运行服务，前三历史不变，无commit/push/deploy，Grillme单列未完成。

## 6. Grillme 独立待办

- [ ] 6.1 Grillme可用时固定GPT-5.6 Sol/xhigh补审；验证：记录实际模型和结果，解决实证问题；当前离线保持未完成且不阻断本地开发，不启动/配对/降档，不以当前Sol规划/源码复审冒充。
