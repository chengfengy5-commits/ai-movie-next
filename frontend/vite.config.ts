import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";
import { createApiProxyOptions } from "./devProxy.mjs";

const devPort = Number(process.env.VITE_DEV_PORT ?? "5173");
const apiProxyOptions = createApiProxyOptions(process.env.VITE_API_PROXY_TARGET);
const apiProxy = apiProxyOptions
  ? { "^/api(?:/|\\?|$)": apiProxyOptions }
  : undefined;

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: devPort,
    strictPort: true,
    ...(apiProxy ? { proxy: apiProxy } : {}),
  },
  preview: {
    host: "127.0.0.1",
    port: 4173,
    strictPort: true,
    ...(apiProxy ? { proxy: apiProxy } : {}),
  },
  test: {
    clearMocks: true,
    environment: "jsdom",
    restoreMocks: true,
    setupFiles: ["./src/testing/setup.ts"],
  },
});
