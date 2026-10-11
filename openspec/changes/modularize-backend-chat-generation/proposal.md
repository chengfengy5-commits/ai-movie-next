# modularize-backend-chat-generation

## 为什么迁移

用户需要六个生成入口的完整流程：等待、失败、部分成功、真实费用和结果应用。固定旧源证明原行为，accepted design04补齐具体可靠性合同；旧 reliable_worker已部署/已迁移、图已全被消费和canvas生命周期已完成均不能当事实。

这是普通OpenSpec的EVID staging五稿。design04、独立设计审查03和Root consumption接受的是设计准备。五稿交不同Sol独审；当前G/A/P真实验收、文档与中文Git收尾前不启动下一批产品代码。本稿不安装target，不转spec-superflow，不新增人工审批。

## 范围

| 现有POST | 内部operation | 兼容结果 |
| --- | --- | --- |
| /api/chat/stream | text.stream | 旧Web/gate/queue SSE，可选Task/message |
| /api/chat/image | image.single | 单图Task/原receipt |
| /api/chat/video | video.single | 单视频Task/异构provider |
| /api/chat/batch-generate-images | image.batch | 有序逐item Task、全原子准入 |
| /api/chat/ai-review | frame.review | 实际apply提交后独立收费 |
| /api/chat/batch-optimize | frame.optimize | 每frame生成→扣费→应用，真实partial |

保留DTO/限制/response/error及五费用顺序，实现冻结的非secret配置、原principal live credential、confirmed intent、可信evidence、phase-fenced必需Work和实际恢复/取消/期限消费者。canvas保留client自动选首成功结果并显式PUT，worker不自动canvas应用；image canvas无message，video canvas422。

GET proxy-download不随六POST迁移。公开SSE reconnect/cursor/task新字段未选择，graphless stream不改为可靠worker。unknown create不重新POST，旧无图/unsupported图owner可读隔离，不补图/补扣。

## 交付与完成标准

- [design.md](design.md)：D1–D9、五费用、三作者和Root独占/关键路径。
- [public-contracts.md](public-contracts.md)：P1–P6、40公共类型、状态/错误与完整规范。
- [spec.md](specs/chat-generation/spec.md)：原39精确四字段、SHALL/Scenario和修复子例。
- [tasks.md](tasks.md)：共享先冻结、三片独占、Root真正接线和逐层验收。

原39仍planned：0执行/0通过/0路线扣减。A14公开重连未选，A38真实浏览器未执行，A39指定GPT-5.6 Sol/xhigh外审OFFLINE PENDING，不探测、不替换。历史732后台测试不替代下一六入口或当前G/A/P验收。

Root恢复报告当前GAP175 run03为150/175通过、23G+2A正修复，target main95b6d4f clean；本作者未运行或查Git，此为Root提供的独立工作线状态。97/248注册、151未注册按既有已消费来源携带，不重新计数/扣减。

六接口之后继续完整后台迁移，再React全工作流与真实浏览器验证，最后语言方案评估。本族不授权语言重写，不承诺总体耗时/日期/整体百分比。发布授权与实际完成另层记录。
