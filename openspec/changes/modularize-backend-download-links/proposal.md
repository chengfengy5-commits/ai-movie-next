# Proposal

## Why

下载链接接口目前混合了路由、身份校验和旧对象存储配置探测；默认路径会读取环境配置并逐项检查临时目录，但正常响应并不真正生成签名或下载文件。将接口迁入隔离后端模块，可保留已观察到的 HTTP 契约，同时把 URL 解析/签发能力改为显式异步端口，避免默认请求隐式触碰存储配置或文件系统。

## What Changes

- 新增 backend-download-links 能力，承接停用的下载代理 GET 和批量签名直链 POST。
- 默认注入逐字返回输入 URL 的 identity resolver；需要改变 URL 的行为只能通过显式注入的异步 resolver 提供，不在本变更实现 HMAC、下载或存储 SDK。
- 保留活动会员身份校验、请求 DTO 边界、最多 500 项限制、逐项顺序等待、重复项保留、原文件名回传和 signed 比较语义。
- 批处理中任一 resolver 异常都中断请求，不自动重试，也不返回部分成功结果。
- 从默认实现移除环境/.env 配置读取和每项临时目录探测，并记录由此带来的默认错误面差异；不增加 URL 安全校验或业务数据访问。
- 将路由挂载到统一应用工厂；仅在授权的旧 app.py 与八个既有测试文件中增加两个下载方法的登记/断言，使路由方法清单由 54 项变为 56 项，保留其余业务与副作用断言。

## Capabilities

### New Capabilities

- backend-download-links: 下载代理停用响应与批量下载 URL 解析接口的请求、响应和显式 resolver 契约。

### Modified Capabilities

- 无。本项目当前没有已登记的 OpenSpec capability specs；本变更建立独立能力。

## Impact

影响 backend/src/haoai_backend/app.py、新增 backend/src/haoai_backend/download_links/ 模块及其四个专用测试文件，并按授权范围更新既有应用/边界测试中的路由登记。公开路由为 GET /api/download 与 POST /api/sign-download-urls。授权代码路径为旧 9 条（app.py 与八个测试文件）加新 12 条，共 21 条；新代码含四个专用测试文件。规划路径共 5 条，文档冻结清单共 7 条。按本批基线 470 条计算，规划、代码和文档阶段分别预期 475、487、489 条；这些是计划算术，不是本轮库存实测。不会超出清单写入。除此之外不增加依赖，不触碰对象存储上传、文件代理、真实签名提供方或业务 UoW；认证会话维护仍可能产生其自身 SQL，不能据此称整个 HTTP 请求无 SQL。
