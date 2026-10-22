// @vitest-environment node
import { describe, expect, it } from "vitest";
import { createApiAndUploadsProxy } from "./vite.config";

describe("Vite API and uploads proxy", () => {
  it("maps only the API and uploads routes to the same validated loopback target", () => {
    const proxy = createApiAndUploadsProxy("http://127.0.0.1:4175");

    expect(proxy).toEqual({
      "^/api(?:/|\\?|$)": { target: "http://127.0.0.1:4175", changeOrigin: true },
      "^/uploads/[^?]+$": { target: "http://127.0.0.1:4175", changeOrigin: true },
    });
  });

  it.each([
    "https://media.example.invalid",
    "http://127.0.0.1:4175/api",
    "http://user@127.0.0.1:4175",
    "http://127.0.0.1:4175?token=secret",
  ])("keeps the existing loopback-only proxy boundary for %s", (target) => {
    expect(() => createApiAndUploadsProxy(target)).toThrow(TypeError);
  });

  it("does not create proxy routes when no local target is configured", () => {
    expect(createApiAndUploadsProxy(undefined)).toBeUndefined();
  });
});
