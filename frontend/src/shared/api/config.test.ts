import { describe, expect, it } from "vitest";
import {
  ApiConfigurationError,
  isSafeCoverUrl,
  readEnvironmentConfiguration,
  resolveApiBaseUrl,
} from "./config";

describe("loopback API configuration", () => {
  it("defaults to a no-network demo mode and allows local relative or loopback API URLs", () => {
    expect(readEnvironmentConfiguration({}, "https://remote.example/page")).toEqual({ mode: "demo" });
    expect(resolveApiBaseUrl("/api", "http://127.0.0.1:5174/"))
      .toBe("http://127.0.0.1:5174/api");
    expect(resolveApiBaseUrl("http://localhost:4175/api", "http://127.0.0.1:5174/"))
      .toBe("http://localhost:4175/api");
    expect(readEnvironmentConfiguration(
      { VITE_AUTH_MODE: "api", VITE_API_BASE_URL: "http://[::1]:4175/api" },
      "http://localhost:5174/",
    )).toEqual({ mode: "api", apiBaseUrl: "http://[::1]:4175/api" });
  });

  it.each([
    ["remote page", "http://127.0.0.1:4175/api", "https://remote.example/"],
    ["remote API", "https://api.example/api", "http://localhost:5174/"],
    ["wrong path", "http://127.0.0.1:4175/other", "http://localhost:5174/"],
    ["URL credentials", "http://user:pass@127.0.0.1:4175/api", "http://localhost:5174/"],
    ["query string", "http://127.0.0.1:4175/api?token=x", "http://localhost:5174/"],
  ])("rejects %s configuration", (_label, apiUrl, pageUrl) => {
    expect(() => resolveApiBaseUrl(apiUrl, pageUrl)).toThrow(ApiConfigurationError);
  });

  it("allows only same-origin loopback media URLs", () => {
    const apiUrl = "http://127.0.0.1:4175/api";
    expect(isSafeCoverUrl("/media/cover.webp", apiUrl)).toBe("http://127.0.0.1:4175/media/cover.webp");
    expect(isSafeCoverUrl("https://cdn.example/cover.webp", apiUrl)).toBeNull();
    expect(isSafeCoverUrl("http://127.0.0.1:4176/cover.webp", apiUrl)).toBeNull();
    expect(isSafeCoverUrl("data:image/svg+xml,unsafe", apiUrl)).toBeNull();
  });
});
