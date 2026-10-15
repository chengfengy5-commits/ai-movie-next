# 两步分镜生成：接入与操作

## 应用接线

`create_app` 的 `storyboard_generation_runtime` 默认是 `None`；未显式注入时，两步分镜路由返回 503“分镜生成服务尚未接线”。接入现有应用时复用当前数据库与认证对象：

```python
from haoai_backend.app import create_app
from haoai_backend.storyboard_generation import (
    AiohttpStoryboardTextTransport,
    OpenAICompatibleStoryboardRuntime,
)

app = create_app(
    session_factory=session_factory,  # 现有业务 Session 工厂
    authentication_runtime=authentication_runtime,  # 现有认证配置
    storyboard_generation_runtime=OpenAICompatibleStoryboardRuntime(
        AiohttpStoryboardTextTransport()
    ),
)
```

`session_factory`、`authentication_runtime` 和（若原应用显式传入）`resolve_actor` 都沿用原有实参；无需给 `create_app` 增加解密参数。应用内部使用 `authentication_runtime.secret_key` 构造既有 `LegacyFernet` 解密器，并将业务 Session、身份解析和解密器接入分镜服务。构造 transport 不读取环境变量、不导入网络客户端、不发请求；aiohttp 在实际生成调用时才惰性导入并连接 provider。

接入前按既有部署与结构检查流程确认 two-pass 直接依赖的 foundation schema 已就绪：`task_quotes`、`task_submissions`、`ai_tasks`、`billing_units`、`user_credits`、`credit_logs`，以及现有模型、提示词、剧集、站点配置和认证表。缺少报价基础表时，报价接口返回 503；请求不会自动建表。迁移 `backend/migrations/0001_generation_shared_records.sql` 要求这些 foundation 表已存在，只为 generation-core 创建八张 canonical record 表；它不是本功能 foundation 的初始化脚本，不要为接入本功能盲目运行。测试用 SQLite 建表不代表生产迁移已验收。

## 操作顺序与接口

选择已启用的服务器文本模型：`provider` 只能是 `openai` 或 `openai-compatible`，`category` 为 `text` 或 `reasoning`。拆章提示词类别必须是 `scene-segmentation`；生成分镜提示词类别为 `storyboard`。系统提示词须处于 active 状态，个人提示词须属于当前用户。先请求报价并由用户明确确认金额，再开始计费的流式生成。

| 方法与路径 | 用途 |
| --- | --- |
| `POST /api/storyboard/two-pass/quote` | 计算并返回报价；前端展示金额，等待用户确认 |
| `POST /api/storyboard/two-pass/stream` | JSON body 为 `{"quote_id":"…"}`，携带新的 `Idempotency-Key`；SSE 首事件含任务回执 |
| `GET /api/chat/submissions/storyboard.two_pass?idempotency_key=…` | 用同一个幂等键读取提交回执 |
| `GET /api/chat/tasks?task_id=…` | 读取任务状态与结果；连接中断后据此恢复预览 |
| `POST /api/series/{series_id}/chapters` | 用户检查、编辑预览并确认后，走普通章节创建接口保存 |

断开 SSE 或停止接收只会结束客户端读取，不代表取消后台 provider，也不表示退款。服务端 worker 负责完成或失败结算；失败时由服务端结算逻辑处理退款。连接中断后查询提交回执和任务状态，不要为同一次已提交操作换新幂等键重发。生成结果只进入可编辑预览，用户确认前不会创建章节。

JSON/Markdown 导入导出走剧集数据接口，不调用分镜 provider，也不走上述报价和计费流程。本地浏览器同源开发入口见[本地 API 代理说明](local-api-proxy.md)。

当前本地验证使用 FakeRuntime、fake transport 和本地测试；真实供应商连通与生产部署尚未验收，因此本文不表示真实 provider 已发布。
