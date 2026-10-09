# 下载链接后端模块

本说明对应 OpenSpec 变更 modularize-backend-download-links。隔离模块承接两个既有 HTTP 方法，并把身份解析、URL 变换与 HTTP 适配分开。

## HTTP 合同

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| GET | /api/download | 需要 url 普通字符串；filename 可省略。活动会员且参数合法时返回固定 403“下载代理已停用（服务器不承载下载流量）：请使用 POST /api/sign-download-urls 获取签名直链，由浏览器直连 OSS 下载”。不返回文件或代理内容。 |
| POST | /api/sign-download-urls | 接收 items 数组；每项 url 是普通字符串，filename 缺省为空字符串。成功响应包含按原顺序对应的 URL、原 filename 和 signed。 |

POST 不增加 URL parser、安全格式校验或 trim；schema 按默认规则忽略未知字段，显式错误字段类型仍是标准 422。先完成 DTO 校验，再判断有效项目数：空数组返回空结果且不调用 resolver；501 个有效项目返回 HTTP 400 和固定详情“单次最多 500 条”。单项 resolver 按输入顺序逐一 await，每项调用一次；重复项目保留。signed 仅表示返回 URL 是否不同于输入 URL。

## 身份与组合边界

应用工厂把统一可信 actor resolver 和可选 URL resolver传给下载路由。若认证 actor resolver 未接线，路由返回 503，不绕过身份检查；配置的认证运行时继续执行活动会员校验。下载链接用例不需要业务 Session factory 或 series access policy。认证身份解析自身可能创建和维护认证 Session，所以“不创建业务 Session”不等于“请求完全无 SQL”。

纯领域、应用与端口承载批量语义；schema 和 HTTP 层负责 DTO 与协议映射；app factory 是显式组合点。该模块不建立 Engine、数据库 schema 或存储客户端。

## Resolver 与副作用

未显式提供 URL resolver 时，应用使用 identity resolver，逐字返回输入 URL，signed 为 false。它不读取环境变量或 .env，不探测或创建临时目录，不调用 OSS/R2 SDK，不生成 HMAC，也不下载文件。需要变换 URL 的调用方必须显式注入异步 resolver。

显式 resolver 的异常由 HTTP 层转换为通用 500；请求不自动重试，也不返回部分批次。真实签名密钥、存储 provider、下载流和相关配置不属于本次变更。移除旧默认 helper 的配置读取和临时目录探测会收窄其偶发错误面，因此不声称完全复刻旧 helper 的环境/文件系统错误路径。

更细的实测与边界见[本批 verification](../../openspec/changes/modularize-backend-download-links/verification.md)。
