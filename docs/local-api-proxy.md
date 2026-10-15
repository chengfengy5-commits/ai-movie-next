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
