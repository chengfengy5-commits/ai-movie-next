import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";
import { fileURLToPath } from "node:url";
import { createApiProxyOptions } from "./devProxy.mjs";

const devPort = Number(process.env.VITE_DEV_PORT ?? "5173");

export function createApiAndUploadsProxy(value: string | undefined) {
  const apiProxyOptions = createApiProxyOptions(value);
  if (apiProxyOptions === undefined) {
    return undefined;
  }
  return {
    "^/api(?:/|\\?|$)": { ...apiProxyOptions },
    "^/uploads/[^?]+$": { ...apiProxyOptions },
  };
}

const apiProxy = createApiAndUploadsProxy(process.env.VITE_API_PROXY_TARGET);

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
  build: {
    rollupOptions: {
      input: [
        fileURLToPath(new URL("./index.html", import.meta.url)),
        fileURLToPath(new URL("./reset-password.html", import.meta.url)),
      ],
    },
  },
  test: {
    clearMocks: true,
    environment: "jsdom",
    restoreMocks: true,
    setupFiles: ["./src/testing/setup.ts"],
  },
});
