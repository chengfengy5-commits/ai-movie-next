import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { App } from "./App";
import { createApiServices, createDemoServices } from "../shared/api/services";
import {
  API_TOKEN_KEY,
  API_USER_KEY,
  MOCK_SESSION_KEY,
  MOCK_USER_KEY,
} from "../shared/api/storage";

let previousUrl = "";
let previousStorage: Array<[string, string]> = [];

beforeEach(() => {
  previousUrl = window.location.href;
  previousStorage = [];
  for (let index = 0; index < window.localStorage.length; index += 1) {
    const key = window.localStorage.key(index);
    if (key === null) continue;
    const value = window.localStorage.getItem(key);
    if (value !== null) previousStorage.push([key, value]);
  }
  window.localStorage.clear();
  window.history.replaceState(null, "", "/");
});

afterEach(() => {
  cleanup();
  window.history.replaceState(null, "", previousUrl);
  window.localStorage.clear();
  for (const [key, value] of previousStorage) {
    window.localStorage.setItem(key, value);
  }
});

describe("App admin entry", () => {
  it("mounts the admin login on an API admin path without restoring the creative session", async () => {
    window.history.replaceState(null, "", "/admin/settings");
    window.localStorage.setItem(API_TOKEN_KEY, "creative-session-token");
    window.localStorage.setItem(API_USER_KEY, "creative-user-record");
    const fetcher = vi.fn<typeof fetch>();
    const services = createApiServices("http://127.0.0.1:4175/api", {
      storage: window.localStorage,
      fetcher,
    });
    const restore = vi.spyOn(services, "restore").mockResolvedValue(null);

    render(<App services={services} />);

    expect(await screen.findByRole("heading", { name: "管理后台" })).toBeInTheDocument();
    expect(screen.getByLabelText("用户名")).toBeInTheDocument();
    expect(restore).not.toHaveBeenCalled();
    expect(fetcher).not.toHaveBeenCalled();
    expect(window.localStorage.getItem(API_TOKEN_KEY)).toBe("creative-session-token");
    expect(window.localStorage.getItem(API_USER_KEY)).toBe("creative-user-record");
  });

  it("explains that the admin entry requires local API in demo mode without changing either session", () => {
    window.history.replaceState(null, "", "/admin");
    window.localStorage.setItem(API_TOKEN_KEY, "creative-session-token");
    window.localStorage.setItem(MOCK_SESSION_KEY, "demo-session");
    window.localStorage.setItem(MOCK_USER_KEY, "demo-user-record");
    const services = createDemoServices(window.localStorage);
    const restore = vi.spyOn(services, "restore").mockResolvedValue(null);

    render(<App services={services} />);

    expect(screen.getByRole("heading", { name: "管理后台需要本地 API" })).toBeInTheDocument();
    expect(screen.getByText("请启用 API 模式并配置本机 /api 服务后重试。")).toBeInTheDocument();
    expect(restore).not.toHaveBeenCalled();
    expect(window.localStorage.getItem(API_TOKEN_KEY)).toBe("creative-session-token");
    expect(window.localStorage.getItem(MOCK_SESSION_KEY)).toBe("demo-session");
    expect(window.localStorage.getItem(MOCK_USER_KEY)).toBe("demo-user-record");
  });

  it("preserves the normal workspace authentication flow outside admin paths", async () => {
    const services = createDemoServices(window.localStorage);
    const restore = vi.spyOn(services, "restore").mockResolvedValue(null);

    render(<App services={services} />);

    expect(await screen.findByRole("button", { name: "登录工作台" })).toBeInTheDocument();
    expect(screen.getByText("演示模式 · 数据仅保存在本机")).toBeInTheDocument();
    expect(restore).toHaveBeenCalledTimes(1);
  });
});
