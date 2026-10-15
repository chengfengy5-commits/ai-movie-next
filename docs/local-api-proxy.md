# 本地 API 同源开发入口

本地 Vite 开发服务器和预览服务器可以选择代理 /api 到本机后端。这样浏览器请求仍发往当前页面的同源地址，Vite 再把请求原路径转给本地 API。代理只在启动 Vite 的 shell 显式设置 VITE_API_PROXY_TARGET 时启用；未设置时，现有开发和预览行为不变。

    cd frontend
    VITE_AUTH_MODE=api \
    VITE_API_BASE_URL=/api \
    VITE_API_PROXY_TARGET=http://127.0.0.1:8000 \
    npm run dev

VITE_API_PROXY_TARGET 必须是本机 API 的 HTTP(S) origin，可以使用 localhost、127.0.0.1 或 [::1]，并可带端口或单个根斜线。配置只读取启动 shell 中的变量；不要把代理目标写进 .env。预览服务器也使用同一规则：构建时将 VITE_AUTH_MODE=api 和 VITE_API_BASE_URL=/api 作为 shell 变量设置，启动 npm run preview 时再显式设置 VITE_API_PROXY_TARGET。

    VITE_AUTH_MODE=api VITE_API_BASE_URL=/api npm run build
    VITE_API_PROXY_TARGET=http://127.0.0.1:8000 npm run preview

目标只允许 http:// 或 https:// 下的 localhost、127.0.0.1、[::1]。配置会拒绝远端地址、数字或伪装回环地址、凭据、非根路径、查询、片段、控制字符和空白。Vite 将 /api 及其路径段原样代理到目标，不重写路径，并设置 changeOrigin: true；不启用 WebSocket、TLS 校验放宽或备用转发。服务端和预览端都按同一规则处理。

这个入口不改变客户端的 cache: "no-store"、credentials: "omit"、重定向或 referrer 策略，也不增加自动重试。它只提供一个显式的本地同源开发/预览路径。目前直接跨域浏览器连接仍有间歇失败；此代理入口没有确定或修复该底层原因，也不代表生产跨域部署配置。



## 验证代理目标边界

辅助函数的 Node 内置测试不会进入 Vitest 测试发现范围：

    cd frontend
    node --test devProxy.node-test.mjs

## 浏览器对照观察（2026-10-16）

在本地合成 SQLite/JWT、使用产品的 `configure_browser_cors` 和同一 API 进程时，`http://127.0.0.1:5182` 对 `http://127.0.0.1:46834/api/auth/me` 的 Bearer GET 进行了浏览器对照。默认 keep-alive 条件下，Chrome 为 40/40 成功，Codex 内置浏览器为 35/40；API 响应额外带 `Connection: close` 后，Chrome 为 20/20，内置浏览器为 18/20。

已记录的失败请求中，服务端收到 OPTIONS 并完整返回 200，包含精确的 `Access-Control-Allow-Origin`、允许的 GET 和 Authorization，响应正文也完整结束；对应 nonce 没有后续 GET，随后观察到 IAB 请求连接正常关闭。增加 `Connection: close` 后仍出现同类结果。这些记录把失败定位到本次 IAB 对照中成功预检之后、API GET 到达之前；尚不能据此确认是哪一层或哪项实现导致，也不代表其他 API 路由、浏览器版本或生产环境的表现。

本地开发时可用上面的显式 `/api` 同源代理入口，让内置浏览器走当前页面 origin；Chrome 本轮可用于直接 origin 的跨域对照。客户端继续使用 `cache: "no-store"`、`credentials: "omit"`，不增加写请求重试。`Connection: close` 仅用于这次 E 目录诊断，没有加入产品配置或代理行为。
