import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AdminFeature } from "./AdminFeature";
import type { AdminFetcher } from "./adminApi";
import type { AdminModel, AdminStats, AdminStorage } from "./adminTypes";
import type { AdminUserRecord } from "./usersTypes";
import type { SystemPrompt } from "./systemPromptsTypes";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

class MemoryStorage implements AdminStorage {
  private readonly values = new Map<string, string>();

  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }

  removeItem(key: string): void {
    this.values.delete(key);
  }
}

function adminStorage(): MemoryStorage {
  const storage = new MemoryStorage();
  storage.setItem("admin_token", "admin-token");
  storage.setItem("admin_user", JSON.stringify({
    id: "admin-1",
    username: "operator",
    is_superuser: true,
  }));
  return storage;
}

function model(overrides: Partial<AdminModel> = {}): AdminModel {
  return {
    id: "model-1",
    category: "text",
    name: "推理模型",
    provider: "openai",
    model: "gpt-4",
    api_key: "decrypted-secret",
    base_url: "",
    cost_per_call: 5,
    description: "默认文本模型",
    is_active: true,
    sort_order: 0,
    params: null,
    created_at: "2026-10-01T00:00:00",
    updated_at: "2026-10-01T00:00:00",
    ...overrides,
  };
}

function prompt(overrides: Partial<SystemPrompt> = {}): SystemPrompt {
  return {
    id: "prompt-1",
    category: "storyboard",
    name: "分镜提示词",
    system_prompt: "系统内容",
    user_prompt: "用户内容",
    sort_order: 0,
    is_active: true,
    created_at: "2026-10-01T00:00:00",
    updated_at: "2026-10-01T00:00:00",
    ...overrides,
  };
}

function managedUser(overrides: Partial<AdminUserRecord> = {}): AdminUserRecord {
  return {
    id: "user-1",
    username: "writer",
    email: "writer@example.test",
    is_superuser: false,
    membership_type: "free",
    membership_expires_at: null,
    credits: 0,
    created_at: "2026-10-01T00:00:00",
    ...overrides,
  };
}

function stats(): AdminStats {
  return {
    upstream: { balance: 2400 },
    users: { total: 100, premium: 20, active_members: 18, new_this_month: 8 },
    credits: { total_balance: 1000, used_this_month: 300, charged_this_month: 120 },
    tasks: { total: 60, this_month: 12, completed: 48, failed: 12 },
    content: { series: 15, chapters: 40, characters: 22, scenes: 19, props: 7 },
    revenue: { total: 0, this_month: 0, subscriptions: 20 },
  };
}

function inputValue(fetcher: ReturnType<typeof vi.fn>, callNumber: number): string {
  return requestPath(fetcher.mock.calls[callNumber]?.[0]);
}

function requestPath(input: RequestInfo | URL | undefined): string {
  if (input === undefined) return "";
  const url = new URL(String(input), window.location.href);
  return url.pathname + url.search;
}

afterEach(() => cleanup());

describe("admin feature user flows", () => {
  it("keeps both admin destinations and logout reachable by their accessible names", async () => {
    const user = userEvent.setup();
    const storage = adminStorage();
    storage.setItem("access_token", "creative-account-token");
    const fetcher = vi.fn<AdminFetcher>(async (input) => {
      const url = requestPath(input);
      if (url === "/api/admin/stats") return jsonResponse(stats());
      if (url.startsWith("/api/admin/system-models?")) return jsonResponse({ data: [], total: 0 });
      throw new Error("Unexpected request: " + url);
    });

    render(<AdminFeature apiBaseUrl="/api" fetcher={fetcher} storage={storage} />);

    expect(await screen.findByRole("heading", { name: "运营概览" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "系统模型" }));
    expect(await screen.findByRole("heading", { name: "系统模型" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "运营概览" }));
    expect(await screen.findByRole("heading", { name: "运营概览" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "退出登录" }));
    expect(await screen.findByRole("heading", { name: "管理后台" })).toBeInTheDocument();
    expect(storage.getItem("admin_token")).toBeNull();
    expect(storage.getItem("admin_user")).toBeNull();
    expect(storage.getItem("access_token")).toBe("creative-account-token");
  });

  it("rejects a non-admin login without writing admin credentials or clearing the creative token", async () => {
    const user = userEvent.setup();
    const storage = new MemoryStorage();
    storage.setItem("access_token", "creative-account-token");
    const fetcher = vi.fn<AdminFetcher>(async () => jsonResponse({
      access_token: "member-token",
      user: { id: "member-1", username: "member", is_superuser: false },
    }));

    render(<AdminFeature apiBaseUrl="/api" fetcher={fetcher} storage={storage} />);

    await user.type(screen.getByLabelText("用户名"), "member");
    await user.type(screen.getByLabelText("密码"), "password");
    await user.click(screen.getByRole("button", { name: "登录" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("权限不足，仅管理员可登录后台");
    expect(storage.getItem("admin_token")).toBeNull();
    expect(storage.getItem("admin_user")).toBeNull();
    expect(storage.getItem("access_token")).toBe("creative-account-token");
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("stores the full administrator identity separately and opens the dashboard", async () => {
    const user = userEvent.setup();
    const storage = new MemoryStorage();
    storage.setItem("access_token", "creative-account-token");
    const administrator = {
      id: "admin-2",
      username: "operator",
      email: "operator@example.invalid",
      is_superuser: true,
      membership_type: "free",
      membership_expires_at: null,
      avatar_url: null,
      bio: null,
      created_at: "2026-10-01T00:00:00",
    };
    const fetcher = vi.fn<AdminFetcher>(async (input) => (
      requestPath(input) === "/api/auth/login"
        ? jsonResponse({ access_token: "admin-session", user: administrator })
        : jsonResponse(stats())
    ));

    render(<AdminFeature apiBaseUrl="/api" fetcher={fetcher} storage={storage} />);
    await user.type(screen.getByLabelText("用户名"), "operator");
    await user.type(screen.getByLabelText("密码"), "password");
    await user.click(screen.getByRole("button", { name: "登录" }));

    expect(await screen.findByRole("heading", { name: "运营概览" })).toBeInTheDocument();
    expect(storage.getItem("admin_token")).toBe("admin-session");
    expect(JSON.parse(storage.getItem("admin_user") ?? "null")).toEqual(administrator);
    expect(storage.getItem("access_token")).toBe("creative-account-token");
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("clears only administrator session state after a protected request returns 401", async () => {
    const storage = adminStorage();
    storage.setItem("access_token", "creative-account-token");
    const fetcher = vi.fn<AdminFetcher>(async () => jsonResponse({ detail: "登录已过期" }, 401));

    render(<AdminFeature apiBaseUrl="/api" fetcher={fetcher} storage={storage} />);

    expect(await screen.findByRole("heading", { name: "管理后台" })).toBeInTheDocument();
    await waitFor(() => expect(storage.getItem("admin_token")).toBeNull());
    expect(storage.getItem("admin_user")).toBeNull();
    expect(storage.getItem("access_token")).toBe("creative-account-token");
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(new Headers(fetcher.mock.calls[0]?.[1]?.headers).get("Authorization")).toBe("Bearer admin-token");
  });

  it("creates a model, returns to the list and reads the saved row back with the server total", async () => {
    const user = userEvent.setup();
    const storage = adminStorage();
    let created = false;
    const saved = model({
      id: "created-1",
      category: "video",
      name: "新模型",
      provider: "zhangyuge",
      api_key: "encrypted-key",
    });
    const fetcher = vi.fn<AdminFetcher>(async (input, init) => {
      const url = requestPath(input);
      if (url === "/api/admin/stats") return jsonResponse(stats());
      if (url.startsWith("/api/admin/system-models?")) {
        return created
          ? jsonResponse({ data: [saved], total: 22 })
          : jsonResponse({ data: [], total: 21 });
      }
      if (url === "/api/admin/system-models" && init?.method === "POST") {
        created = true;
        return jsonResponse(saved);
      }
      throw new Error("Unexpected request: " + url);
    });

    render(<AdminFeature apiBaseUrl="/api" fetcher={fetcher} storage={storage} />);
    await user.click(screen.getByRole("button", { name: "系统模型" }));
    expect(await screen.findByText("共 21 个模型")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "新建模型" }));
    await user.clear(screen.getByLabelText("模型名称"));
    await user.type(screen.getByLabelText("模型名称"), "新模型");
    await user.type(screen.getByLabelText("API Key"), "new-secret");
    await user.selectOptions(screen.getByLabelText("分类"), "video");
    expect(screen.getByLabelText("提供商")).toHaveValue("zhangyuge");
    await user.click(screen.getByRole("button", { name: "保存" }));

    expect(await screen.findByRole("button", { name: "新模型" })).toBeInTheDocument();
    expect(await screen.findByText("共 22 个模型")).toBeInTheDocument();

    const createCall = fetcher.mock.calls.find((call) => (
      requestPath(call[0]) === "/api/admin/system-models" && call[1]?.method === "POST"
    ));
    expect(createCall).toBeDefined();
    expect(JSON.parse(String(createCall?.[1]?.body))).toMatchObject({
      category: "video",
      name: "新模型",
      provider: "zhangyuge",
      api_key: "new-secret",
      is_active: true,
      sort_order: 0,
    });
    expect(inputValue(fetcher, fetcher.mock.calls.length - 1)).toContain("skip=0&limit=10");
  });

  it("loads the detail view and omits a blank masked API key from the update payload", async () => {
    const user = userEvent.setup();
    const storage = adminStorage();
    let updated = false;
    const fetcher = vi.fn<AdminFetcher>(async (input, init) => {
      const url = requestPath(input);
      if (url === "/api/admin/stats") return jsonResponse(stats());
      if (url.startsWith("/api/admin/system-models?")) {
        return jsonResponse({
          data: [model({ name: updated ? "修改后名称" : "推理模型", provider: "legacy-provider" })],
          total: 1,
        });
      }
      if (url === "/api/admin/system-models/model-1" && (init?.method ?? "GET") === "GET") {
        return jsonResponse(model({ provider: "legacy-provider" }));
      }
      if (url === "/api/admin/system-models/model-1" && init?.method === "PUT") {
        updated = true;
        return jsonResponse(model({ name: "修改后名称", provider: "legacy-provider" }));
      }
      throw new Error("Unexpected request: " + url);
    });

    render(<AdminFeature apiBaseUrl="/api" fetcher={fetcher} storage={storage} />);
    await user.click(screen.getByRole("button", { name: "系统模型" }));
    await user.click(await screen.findByRole("button", { name: "推理模型" }));
    expect(screen.getByLabelText("提供商")).toHaveValue("legacy-provider");
    expect(screen.getByRole("option", { name: "legacy-provider（当前值）" })).toBeInTheDocument();
    const apiKey = await screen.findByLabelText("API Key");
    expect(apiKey).toHaveAttribute("type", "password");
    expect(apiKey).toHaveValue("");
    expect(screen.getByText("已配置密钥；只有输入新值时才会提交 API Key。")).toBeInTheDocument();

    await user.clear(screen.getByLabelText("模型名称"));
    await user.type(screen.getByLabelText("模型名称"), "修改后名称");
    await user.click(screen.getByRole("button", { name: "保存" }));

    expect(await screen.findByRole("button", { name: "修改后名称" })).toBeInTheDocument();
    const updateCall = fetcher.mock.calls.find((call) => (
      requestPath(call[0]) === "/api/admin/system-models/model-1" && call[1]?.method === "PUT"
    ));
    expect(updateCall).toBeDefined();
    expect(JSON.parse(String(updateCall?.[1]?.body))).toMatchObject({ provider: "legacy-provider" });
    expect(JSON.parse(String(updateCall?.[1]?.body))).not.toHaveProperty("api_key");
    expect(fetcher.mock.calls.filter((call) => (
      requestPath(call[0]) === "/api/admin/system-models/model-1" && (call[1]?.method ?? "GET") === "GET"
    ))).toHaveLength(1);
  });

  it("refreshes the current query after a deferred delete instead of applying an old page rollback", async () => {
    const user = userEvent.setup();
    const storage = adminStorage();
    const defaultModels = Array.from({ length: 10 }, (_unused, index) => model({
      id: `text-${index}`,
      category: "text",
      name: `文本模型${index}`,
    }));
    const videoModels = Array.from({ length: 10 }, (_unused, index) => model({
      id: `video-${index}`,
      category: "video",
      name: `视频模型${index}`,
      provider: "zhangyuge",
    }));
    const lastVideoModel = model({
      id: "video-last",
      category: "video",
      name: "视频模型10",
      provider: "zhangyuge",
    });
    let deleteResolved = false;
    let allRefreshesAfterDelete = 0;
    let finishDelete: ((response: Response) => void) | undefined;
    const deleteResponse = new Promise<Response>((resolve) => { finishDelete = resolve; });
    const fetcher = vi.fn<AdminFetcher>(async (input, init) => {
      const url = new URL(String(input), window.location.href);
      if (url.pathname === "/api/admin/stats") return jsonResponse(stats());
      if (url.pathname === "/api/admin/system-models" && url.search) {
        const skip = Number(url.searchParams.get("skip"));
        if (url.searchParams.get("category") === "video") {
          return skip === 10
            ? jsonResponse({ data: [lastVideoModel], total: 11 })
            : jsonResponse({ data: videoModels, total: 11 });
        }
        if (skip === 0) {
          if (deleteResolved) {
            allRefreshesAfterDelete += 1;
            return jsonResponse({ data: defaultModels, total: 10 });
          }
          return jsonResponse({ data: defaultModels, total: 11 });
        }
        throw new Error("Unexpected list offset: " + skip);
      }
      if (url.pathname === "/api/admin/system-models/video-last" && init?.method === "DELETE") {
        return deleteResponse;
      }
      throw new Error("Unexpected request: " + url.pathname + url.search);
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<AdminFeature apiBaseUrl="/api" fetcher={fetcher} storage={storage} />);
    await user.click(screen.getByRole("button", { name: "系统模型" }));
    await user.selectOptions(await screen.findByLabelText("分类"), "video");
    await screen.findByRole("button", { name: "视频模型0" });
    await user.click(await screen.findByRole("button", { name: "下一页" }));
    await screen.findByRole("button", { name: "视频模型10" });

    await user.click(screen.getByRole("button", { name: "删除" }));
    await screen.findByRole("button", { name: "删除中…" });
    await user.click(screen.getByRole("button", { name: "重置" }));
    await screen.findByRole("button", { name: "文本模型0" });
    expect(screen.getByText("第 1 / 2 页")).toBeInTheDocument();

    deleteResolved = true;
    await act(async () => {
      finishDelete?.(jsonResponse({ message: "模型已删除" }));
      await deleteResponse;
    });

    await waitFor(() => expect(screen.getByText("第 1 / 1 页")).toBeInTheDocument());
    expect(allRefreshesAfterDelete).toBeGreaterThan(0);
    const listCalls = fetcher.mock.calls.filter((call) => (
      new URL(String(call[0]), window.location.href).pathname === "/api/admin/system-models"
    ));
    const lastListCall = listCalls[listCalls.length - 1];
    expect(lastListCall).toBeDefined();
    expect(new URL(String(lastListCall?.[0]), window.location.href).searchParams.get("skip")).toBe("0");
    expect(listCalls.every((call) => (
      Number(new URL(String(call[0]), window.location.href).searchParams.get("skip")) >= 0
    ))).toBe(true);
  });

  it("does not replay a create after a network failure leaves the write outcome unknown", async () => {
    const user = userEvent.setup();
    const storage = adminStorage();
    const fetcher = vi.fn<AdminFetcher>(async (input, init) => {
      const url = requestPath(input);
      if (url === "/api/admin/stats") return jsonResponse(stats());
      if (url.startsWith("/api/admin/system-models?")) return jsonResponse({ data: [], total: 0 });
      if (url === "/api/admin/system-models" && init?.method === "POST") {
        throw new TypeError("connection closed");
      }
      throw new Error("Unexpected request: " + url);
    });

    render(<AdminFeature apiBaseUrl="/api" fetcher={fetcher} storage={storage} />);
    await user.click(screen.getByRole("button", { name: "系统模型" }));
    await screen.findByText("没有符合条件的模型");
    await user.click(screen.getByRole("button", { name: "新建模型" }));
    await user.type(screen.getByLabelText("模型名称"), "待创建");
    await user.click(screen.getByRole("button", { name: "保存" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("无法连接管理后台服务");
    expect(fetcher.mock.calls.filter((call) => (
      requestPath(call[0]) === "/api/admin/system-models" && call[1]?.method === "POST"
    ))).toHaveLength(1);
  });

  it("connects system prompt navigation, create, edit, cancel and refreshed lists to the admin API", async () => {
    const user = userEvent.setup();
    const storage = adminStorage();
    storage.setItem("access_token", "creative-account-token");
    let currentPrompt = prompt();
    let promptExists = false;
    const fetcher = vi.fn<AdminFetcher>(async (input, init) => {
      const url = new URL(String(input), window.location.href);
      if (url.pathname === "/api/admin/stats") return jsonResponse(stats());
      if (url.pathname === "/api/admin/system-prompts" && url.search) {
        return promptExists
          ? jsonResponse({ data: [currentPrompt], total: 1 })
          : jsonResponse({ data: [], total: 0 });
      }
      if (url.pathname === "/api/admin/system-prompts" && init?.method === "POST") {
        const body = JSON.parse(String(init.body)) as Omit<SystemPrompt, "id" | "created_at" | "updated_at">;
        currentPrompt = prompt({ ...body, id: "created-prompt" });
        promptExists = true;
        return jsonResponse(currentPrompt);
      }
      if (url.pathname === "/api/admin/system-prompts/created-prompt" && (init?.method ?? "GET") === "GET") {
        return jsonResponse(currentPrompt);
      }
      if (url.pathname === "/api/admin/system-prompts/created-prompt" && init?.method === "PUT") {
        const body = JSON.parse(String(init.body)) as Partial<SystemPrompt>;
        currentPrompt = prompt({ ...currentPrompt, ...body });
        return jsonResponse(currentPrompt);
      }
      throw new Error("Unexpected request: " + url.pathname + url.search);
    });

    render(<AdminFeature apiBaseUrl="/api" fetcher={fetcher} storage={storage} />);
    await user.click(screen.getByRole("button", { name: "系统提示词" }));
    expect(await screen.findByText("共 0 个提示词")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "系统提示词" })).toHaveAttribute("aria-current", "page");

    await user.click(screen.getByRole("button", { name: "新建提示词" }));
    await user.selectOptions(screen.getByLabelText("分类"), "storyboard");
    await user.type(screen.getByLabelText("名称"), "新建分镜");
    await user.type(screen.getByLabelText("系统提示词", { selector: "textarea" }), "新的系统内容");
    await user.type(screen.getByLabelText("用户提示词"), "新的用户内容");
    await user.click(screen.getByRole("button", { name: "取消" }));
    expect(await screen.findByText("共 0 个提示词")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "新建提示词" }));
    await user.selectOptions(screen.getByLabelText("分类"), "storyboard");
    await user.type(screen.getByLabelText("名称"), "新建分镜");
    await user.type(screen.getByLabelText("系统提示词", { selector: "textarea" }), "新的系统内容");
    await user.type(screen.getByLabelText("用户提示词"), "新的用户内容");
    await user.click(screen.getByRole("button", { name: "保存" }));
    expect(await screen.findByRole("button", { name: "新建分镜" })).toBeInTheDocument();
    expect(screen.getByText("共 1 个提示词")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "编辑" }));
    expect(await screen.findByDisplayValue("新建分镜")).toBeInTheDocument();
    await user.clear(screen.getByLabelText("名称"));
    await user.type(screen.getByLabelText("名称"), "更新后的分镜");
    await user.click(screen.getByRole("button", { name: "保存" }));
    expect(await screen.findByRole("button", { name: "更新后的分镜" })).toBeInTheDocument();

    const promptRequests = fetcher.mock.calls.filter(([input]) => (
      requestPath(input).startsWith("/api/admin/system-prompts")
    ));
    expect(promptRequests.some(([input, init]) => (
      requestPath(input).startsWith("/api/admin/system-prompts?") && init?.method === undefined
    ))).toBe(true);
    expect(promptRequests.some(([input, init]) => (
      requestPath(input) === "/api/admin/system-prompts" && init?.method === "POST"
        && JSON.parse(String(init.body)).name === "新建分镜"
    ))).toBe(true);
    expect(promptRequests.some(([input, init]) => (
      requestPath(input) === "/api/admin/system-prompts/created-prompt" && init?.method === "PUT"
        && JSON.parse(String(init.body)).name === "更新后的分镜"
    ))).toBe(true);
    expect(promptRequests.every(([, init]) => (
      new Headers(init?.headers).get("Authorization") === "Bearer admin-token"
    ))).toBe(true);
    expect(storage.getItem("access_token")).toBe("creative-account-token");
  });

  it("connects user navigation, create, edit and refreshed lists to the admin API", async () => {
    const user = userEvent.setup();
    const storage = adminStorage();
    storage.setItem("access_token", "creative-account-token");
    let currentUser = managedUser({ id: "created-user", username: "new-writer" });
    let userExists = false;
    const fetcher = vi.fn<AdminFetcher>(async (input, init) => {
      const url = new URL(String(input), window.location.href);
      if (url.pathname === "/api/admin/stats") return jsonResponse(stats());
      if (url.pathname === "/api/admin/users" && url.search) {
        return userExists
          ? jsonResponse({ data: [currentUser], total: 1 })
          : jsonResponse({ data: [], total: 0 });
      }
      if (url.pathname === "/api/admin/users" && init?.method === "POST") {
        const body = JSON.parse(String(init.body)) as Record<string, unknown>;
        currentUser = managedUser({
          id: "created-user",
          username: String(body.username),
          email: String(body.email),
          is_superuser: body.is_superuser === true,
          membership_type: String(body.membership_type),
          membership_expires_at: typeof body.membership_expires_at === "string" ? body.membership_expires_at : null,
          credits: typeof body.credits === "number" ? body.credits : 0,
        });
        userExists = true;
        return jsonResponse(currentUser);
      }
      if (url.pathname === "/api/admin/users/created-user" && (init?.method ?? "GET") === "GET") {
        return jsonResponse(currentUser);
      }
      if (url.pathname === "/api/admin/users/created-user" && init?.method === "PUT") {
        const body = JSON.parse(String(init.body)) as Record<string, unknown>;
        currentUser = managedUser({
          ...currentUser,
          username: typeof body.username === "string" ? body.username : currentUser.username,
          email: typeof body.email === "string" ? body.email : currentUser.email,
          is_superuser: typeof body.is_superuser === "boolean" ? body.is_superuser : currentUser.is_superuser,
          membership_type: typeof body.membership_type === "string" ? body.membership_type : currentUser.membership_type,
          membership_expires_at: typeof body.membership_expires_at === "string" ? body.membership_expires_at : currentUser.membership_expires_at,
          credits: typeof body.credits === "number" ? body.credits : currentUser.credits,
        });
        return jsonResponse({ message: "用户更新成功" });
      }
      throw new Error("Unexpected request: " + url.pathname + url.search);
    });

    render(<AdminFeature apiBaseUrl="/api" fetcher={fetcher} storage={storage} />);
    await user.click(screen.getByRole("button", { name: "用户管理" }));
    expect(await screen.findByText("共 0 个用户")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "用户管理" })).toHaveAttribute("aria-current", "page");

    await user.click(screen.getByRole("button", { name: "新建用户" }));
    await user.type(screen.getByLabelText("用户名"), "new-writer");
    await user.type(screen.getByLabelText("邮箱"), "new-writer@example.test");
    await user.type(screen.getByLabelText("初始密码"), "private-password");
    await user.clear(screen.getByLabelText("初始积分"));
    await user.type(screen.getByLabelText("初始积分"), "35");
    await user.click(screen.getByRole("button", { name: "保存" }));
    expect(await screen.findByRole("button", { name: "new-writer" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "编辑" }));
    expect(await screen.findByDisplayValue("new-writer@example.test")).toBeInTheDocument();
    await user.clear(screen.getByLabelText("邮箱"));
    await user.type(screen.getByLabelText("邮箱"), "updated-writer@example.test");
    await user.click(screen.getByRole("button", { name: "保存" }));
    expect(await screen.findByRole("button", { name: "new-writer" })).toBeInTheDocument();

    const userRequests = fetcher.mock.calls.filter(([input]) => (
      requestPath(input).startsWith("/api/admin/users")
    ));
    expect(userRequests.some(([input, init]) => (
      requestPath(input).startsWith("/api/admin/users?") && init?.method === undefined
    ))).toBe(true);
    expect(userRequests.some(([input, init]) => (
      requestPath(input) === "/api/admin/users" && init?.method === "POST"
        && JSON.parse(String(init.body)).credits === 35
    ))).toBe(true);
    expect(userRequests.some(([input, init]) => (
      requestPath(input) === "/api/admin/users/created-user" && init?.method === undefined
    ))).toBe(true);
    expect(userRequests.some(([input, init]) => (
      requestPath(input) === "/api/admin/users/created-user" && init?.method === "PUT"
        && JSON.parse(String(init.body)).email === "updated-writer@example.test"
    ))).toBe(true);
    expect(userRequests.every(([, init]) => (
      new Headers(init?.headers).get("Authorization") === "Bearer admin-token"
    ))).toBe(true);
    expect(storage.getItem("access_token")).toBe("creative-account-token");
  });

  it("navigates credit views, saves an absolute balance by credit id, and refreshes with the admin token", async () => {
    const user = userEvent.setup();
    const storage = adminStorage();
    storage.setItem("access_token", "creative-account-token");
    let currentCredit = {
      id: "credit-row-7",
      user_id: "writer-7",
      credits: 12,
      created_at: "2026-10-01T00:00:00Z",
      updated_at: "2026-10-02T00:00:00Z",
    };
    let creditListCalls = 0;
    const fetcher = vi.fn<AdminFetcher>(async (input, init) => {
      const url = new URL(String(input), window.location.href);
      if (url.pathname === "/api/admin/stats") return jsonResponse(stats());
      if (url.pathname === "/api/admin/user-credits" && init?.method === undefined) {
        creditListCalls += 1;
        return jsonResponse({ data: [currentCredit], total: 1 });
      }
      if (url.pathname === "/api/admin/user-credits/credit-row-7" && init?.method === undefined) {
        return jsonResponse(currentCredit);
      }
      if (url.pathname === "/api/admin/user-credits/credit-row-7" && init?.method === "PUT") {
        const body = JSON.parse(String(init.body)) as { credits: number };
        currentCredit = { ...currentCredit, credits: body.credits };
        return jsonResponse(currentCredit);
      }
      if (url.pathname === "/api/admin/credit-logs") {
        return jsonResponse({
          data: [{
            id: "log-1",
            user_id: "writer-7",
            amount: -7,
            balance_after: 5,
            type: "manual_adjustment",
            description: "后台调整",
            task_id: null,
            created_at: "2026-10-03T00:00:00Z",
          }],
          total: 1,
        });
      }
      throw new Error("Unexpected request: " + url.pathname + url.search);
    });

    render(<AdminFeature apiBaseUrl="/api" fetcher={fetcher} storage={storage} />);
    await user.click(screen.getByRole("button", { name: "用户积分" }));
    expect(await screen.findByRole("button", { name: "编辑积分记录 credit-row-7" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "用户积分" })).toHaveAttribute("aria-current", "page");

    await user.click(screen.getByRole("button", { name: "编辑积分记录 credit-row-7" }));
    const amount = await screen.findByLabelText("积分数量");
    await user.clear(amount);
    await user.type(amount, "-9");
    await user.click(screen.getByRole("button", { name: "保存" }));
    expect(await screen.findByText("-9")).toBeInTheDocument();
    expect(creditListCalls).toBe(2);
    expect(fetcher.mock.calls.some(([input, init]) => (
      new URL(String(input), window.location.href).pathname === "/api/admin/user-credits/credit-row-7"
        && init?.method === "PUT"
        && JSON.parse(String(init.body)).credits === -9
    ))).toBe(true);

    await user.click(screen.getByRole("button", { name: "积分流水" }));
    expect(await screen.findByText("manual_adjustment")).toBeInTheDocument();
    const creditRequests = fetcher.mock.calls.filter(([input]) => {
      const pathname = new URL(String(input), window.location.href).pathname;
      return pathname.startsWith("/api/admin/user-credits") || pathname === "/api/admin/credit-logs";
    });
    expect(creditRequests.length).toBeGreaterThanOrEqual(5);
    expect(creditRequests.every(([, init]) => (
      new Headers(init?.headers).get("Authorization") === "Bearer admin-token"
    ))).toBe(true);
    expect(creditRequests.some(([, init]) => (
      new Headers(init?.headers).get("Authorization") === "Bearer creative-account-token"
    ))).toBe(false);
    expect(storage.getItem("access_token")).toBe("creative-account-token");
  });

  it("aborts a credit-list read on navigation and ignores its late authentication response", async () => {
    const user = userEvent.setup();
    const storage = adminStorage();
    let finishCreditList: ((response: Response) => void) | undefined;
    let creditListSignal: AbortSignal | null | undefined;
    const creditListResponse = new Promise<Response>((resolve) => { finishCreditList = resolve; });
    const fetcher = vi.fn<AdminFetcher>((input, init) => {
      const url = new URL(String(input), window.location.href);
      if (url.pathname === "/api/admin/stats") return Promise.resolve(jsonResponse(stats()));
      if (url.pathname === "/api/admin/user-credits") {
        creditListSignal = init?.signal;
        return creditListResponse;
      }
      if (url.pathname === "/api/admin/credit-logs") return Promise.resolve(jsonResponse({ data: [], total: 0 }));
      return Promise.reject(new Error("Unexpected request: " + url.pathname));
    });

    render(<AdminFeature apiBaseUrl="/api" fetcher={fetcher} storage={storage} />);
    await user.click(screen.getByRole("button", { name: "用户积分" }));
    expect(await screen.findByText("正在读取用户积分…")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "积分流水" }));
    expect(await screen.findByRole("heading", { name: "积分流水" })).toBeInTheDocument();
    expect(creditListSignal?.aborted).toBe(true);

    await act(async () => {
      finishCreditList?.(jsonResponse({ detail: "登录已过期" }, 401));
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });
    expect(storage.getItem("admin_token")).toBe("admin-token");
    expect(storage.getItem("admin_user")).not.toBeNull();
    expect(screen.getByRole("heading", { name: "积分流水" })).toBeInTheDocument();
  });

  it.each([401, 403])("clears only the administrator session after a user-management request returns %s", async (status) => {
    const storage = adminStorage();
    storage.setItem("access_token", "creative-account-token");
    const fetcher = vi.fn<AdminFetcher>(async (input) => (
      requestPath(input) === "/api/admin/stats"
        ? jsonResponse(stats())
        : jsonResponse({ detail: "权限不足" }, status)
    ));

    const user = userEvent.setup();
    render(<AdminFeature apiBaseUrl="/api" fetcher={fetcher} storage={storage} />);
    await user.click(screen.getByRole("button", { name: "用户管理" }));
    expect(await screen.findByRole("heading", { name: "管理后台" })).toBeInTheDocument();
    expect(storage.getItem("admin_token")).toBeNull();
    expect(storage.getItem("admin_user")).toBeNull();
    expect(storage.getItem("access_token")).toBe("creative-account-token");
    const usersRequest = fetcher.mock.calls.find(([input]) => (
      requestPath(input).startsWith("/api/admin/users?")
    ));
    expect(usersRequest).toBeDefined();
    expect(new Headers(usersRequest?.[1]?.headers).get("Authorization")).toBe("Bearer admin-token");
  });
});


describe("admin membership host integration", () => {
  it("mounts all five views through the real adapter and keeps confirmed receipts until return", async () => {
    const user = userEvent.setup();
    const storage = adminStorage();
    const redemption = {
      id: "code-1", code: "SYNTHETIC-CODE-1", type: "membership", value: 30, is_used: false,
      used_by: null, used_at: null, created_by: "admin-1", expires_at: null, created_at: "created",
    };
    let packageRow = {
      id: "package-empty", category: "", name: "", price: 4, credits: 8, days: -2,
      sort_order: 0, is_active: true, created_at: "created", updated_at: "updated",
    };
    let rejectNextCodeDelete = true;
    const fetcher = vi.fn<AdminFetcher>(async (input, init) => {
      const url = new URL(String(input), window.location.href);
      const method = init?.method ?? "GET";
      if (url.pathname === "/api/admin/stats") return jsonResponse(stats());
      if (url.pathname === "/api/admin/redemption-codes" && method === "GET") {
        return jsonResponse({ data: [redemption], total: 1 });
      }
      if (url.pathname === "/api/admin/redemption-codes" && method === "POST") {
        const body = JSON.parse(String(init?.body));
        return jsonResponse({ ...redemption, type: body.type, value: body.value, _generated_count: body.count });
      }
      if (url.pathname === "/api/admin/redemption-codes/code-1" && method === "GET") return jsonResponse(redemption);
      if (url.pathname === "/api/admin/redemption-codes/code-1" && method === "DELETE") {
        if (rejectNextCodeDelete) {
          rejectNextCodeDelete = false;
          throw new TypeError("synthetic connection drop");
        }
        return jsonResponse({ message: "兑换码已删除" });
      }
      if (url.pathname === "/api/admin/purchase-packages" && method === "GET") {
        return jsonResponse({ data: [packageRow], total: 1 });
      }
      if (url.pathname === "/api/admin/purchase-packages/package-empty" && method === "GET") {
        return jsonResponse(packageRow);
      }
      if (url.pathname === "/api/admin/purchase-packages" && method === "POST") {
        const body = JSON.parse(String(init?.body));
        packageRow = { ...packageRow, ...body, id: "package-created" };
        return jsonResponse(packageRow);
      }
      if (url.pathname === "/api/admin/purchase-packages/package-empty" && method === "PUT") {
        const body = JSON.parse(String(init?.body));
        packageRow = { ...packageRow, ...body };
        return jsonResponse(packageRow);
      }
      if (url.pathname === "/api/admin/purchase-packages/package-empty" && method === "DELETE") {
        return jsonResponse({ message: "套餐已删除" });
      }
      throw new Error("Unexpected membership integration request: " + method + " " + url.pathname);
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<AdminFeature apiBaseUrl="/api" fetcher={fetcher} storage={storage} />);

    await user.click(screen.getByRole("button", { name: "兑换码管理" }));
    expect(await screen.findByRole("heading", { name: "兑换码" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "生成兑换码" }));
    expect(await screen.findByRole("heading", { name: "生成兑换码" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("面值"), { target: { value: "30" } });
    fireEvent.change(screen.getByLabelText("数量"), { target: { value: "2" } });
    await user.click(screen.getByRole("button", { name: "生成" }));
    expect(await screen.findByText("服务端已确认生成 2 个兑换码。")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "生成兑换码" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "返回目录" }));
    expect(await screen.findByRole("heading", { name: "兑换码" })).toBeInTheDocument();
    const codeRow = screen.getByText("code-1").closest("tr");
    if (!codeRow) throw new Error("expected synthetic redemption row");
    await user.click(within(codeRow).getByRole("button", { name: "删除" }));
    expect(await screen.findByLabelText("删除结果待核对")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "只读查询记录" }));
    expect(await screen.findByText(/只读核对：记录当前仍存在/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "购买套餐" }));
    expect(await screen.findByRole("heading", { name: "购买套餐" })).toBeInTheDocument();
    expect(await screen.findByText("package-empty")).toBeInTheDocument();
    const packageRowElement = screen.getByText("package-empty").closest("tr");
    if (!packageRowElement) throw new Error("expected empty legacy package row");
    await user.click(within(packageRowElement).getByRole("button", { name: "编辑" }));
    expect(await screen.findByRole("heading", { name: "编辑购买套餐" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存更改" })).toBeDisabled();
    expect(fetcher.mock.calls.some(([input, init]) => (
      new URL(String(input), window.location.href).pathname === "/api/admin/purchase-packages/package-empty"
        && init?.method === "PUT"
    ))).toBe(false);
    fireEvent.change(screen.getByLabelText("价格（整数元）"), { target: { value: "0" } });
    fireEvent.change(screen.getByLabelText("积分（空值表示 null）"), { target: { value: "" } });
    await user.click(screen.getByLabelText("启用"));
    await user.click(screen.getByRole("button", { name: "保存更改" }));
    expect(await screen.findByText("服务端已确认套餐记录")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "编辑购买套餐" })).toBeInTheDocument();
    const packageUpdate = fetcher.mock.calls.find(([input, init]) => (
      new URL(String(input), window.location.href).pathname === "/api/admin/purchase-packages/package-empty"
      && init?.method === "PUT"
    ));
    expect(JSON.parse(String(packageUpdate?.[1]?.body))).toEqual({ price: 0, credits: null, is_active: false });
    expect(packageRow.category).toBe("");
    expect(packageRow.name).toBe("");
    expect(packageRow.price).toBe(0);
    expect(packageRow.credits).toBeNull();
    expect(packageRow.is_active).toBe(false);

    await user.click(screen.getByRole("button", { name: "返回目录" }));
    expect(await screen.findByRole("heading", { name: "购买套餐" })).toBeInTheDocument();
    const deletePackageRow = screen.getByText("package-empty").closest("tr");
    if (!deletePackageRow) throw new Error("expected synthetic package row for delete");
    await user.click(within(deletePackageRow).getByRole("button", { name: "删除" }));
    await waitFor(() => expect(fetcher.mock.calls.some(([input, init]) => (
      new URL(String(input), window.location.href).pathname === "/api/admin/purchase-packages/package-empty"
      && init?.method === "DELETE"
    ))).toBe(true));
    await user.click(screen.getByRole("button", { name: "新建套餐" }));
    expect(await screen.findByRole("heading", { name: "新建购买套餐" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "创建套餐" }));
    expect(await screen.findByText("服务端已确认套餐记录")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "新建购买套餐" })).toBeInTheDocument();

    const methodPath = fetcher.mock.calls.map(([input, init]) => (
      (init?.method ?? "GET") + " " + new URL(String(input), window.location.href).pathname
    ));
    expect(methodPath).toEqual(expect.arrayContaining([
      "GET /api/admin/redemption-codes", "POST /api/admin/redemption-codes",
      "GET /api/admin/redemption-codes/code-1", "DELETE /api/admin/redemption-codes/code-1",
      "GET /api/admin/purchase-packages", "GET /api/admin/purchase-packages/package-empty",
      "POST /api/admin/purchase-packages", "PUT /api/admin/purchase-packages/package-empty",
      "DELETE /api/admin/purchase-packages/package-empty",
    ]));
  });

  it("uses the host guard for dirty, in-flight, and unknown states on sidebar and logout", async () => {
    const user = userEvent.setup();
    const storage = adminStorage();
    const create = deferred<Response>();
    let createPending = false;
    const fetcher = vi.fn<AdminFetcher>(async (input, init) => {
      const url = new URL(String(input), window.location.href);
      if (url.pathname === "/api/admin/stats") return jsonResponse(stats());
      if (url.pathname === "/api/auth/login") return jsonResponse({
        access_token: "new-session-token",
        user: { id: "admin-2", username: "operator-two", is_superuser: true },
      });
      if (url.pathname === "/api/admin/purchase-packages" && (init?.method ?? "GET") === "GET") {
        return jsonResponse({ data: [], total: 0 });
      }
      if (url.pathname === "/api/admin/purchase-packages" && init?.method === "POST") {
        createPending = true;
        return create.promise;
      }
      if (url.pathname.startsWith("/api/admin/system-models")) return jsonResponse({ data: [], total: 0 });
      return jsonResponse({ data: [], total: 0 });
    });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<AdminFeature apiBaseUrl="/api" fetcher={fetcher} storage={storage} />);

    await user.click(screen.getByRole("button", { name: "购买套餐" }));
    await screen.findByText("没有套餐记录");
    await user.click(screen.getByRole("button", { name: "新建套餐" }));
    await user.type(screen.getByLabelText("名称"), "dirty draft");
    await user.click(screen.getByRole("button", { name: "用户管理" }));
    expect(confirm).toHaveBeenCalled();
    expect(screen.getByRole("heading", { name: "新建购买套餐" })).toBeInTheDocument();
    await user.click(screen.getByRole("link", { name: /Hao AI管理后台/ }));
    expect(screen.getByRole("heading", { name: "新建购买套餐" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "创建套餐" }));
    await waitFor(() => expect(createPending).toBe(true));
    await user.click(screen.getByRole("button", { name: "退出登录" }));
    expect(confirm.mock.calls.at(-1)?.[0]).toMatch(/请求已发出/);
    expect(screen.getByRole("heading", { name: "新建购买套餐" })).toBeInTheDocument();

    confirm.mockReturnValue(true);
    await user.click(screen.getByRole("button", { name: "退出登录" }));
    expect(await screen.findByRole("heading", { name: "管理后台" })).toBeInTheDocument();
    expect(storage.getItem("admin_token")).toBeNull();
    await user.type(screen.getByLabelText("用户名"), "operator-two");
    await user.type(screen.getByLabelText("密码"), "synthetic-password");
    await user.click(screen.getByRole("button", { name: "登录" }));
    expect(await screen.findByRole("button", { name: "退出登录" })).toBeInTheDocument();
    await act(async () => { create.resolve(jsonResponse({
      id: "package-created", category: "credits", name: "dirty draft", price: 0, credits: null, days: null,
      sort_order: 0, is_active: true, created_at: "created", updated_at: "updated",
    })); });
    expect(storage.getItem("admin_token")).toBe("new-session-token");
    expect(screen.getAllByText("operator-two")).toHaveLength(2);
  });

  it("requires an explicit choice before abandoning an unknown package write and does not replay it", async () => {
    const user = userEvent.setup();
    const storage = adminStorage();
    let createCalls = 0;
    const fetcher = vi.fn<AdminFetcher>(async (input, init) => {
      const url = new URL(String(input), window.location.href);
      if (url.pathname === "/api/admin/stats") return jsonResponse(stats());
      if (url.pathname === "/api/admin/purchase-packages" && (init?.method ?? "GET") === "GET") {
        return jsonResponse({ data: [], total: 0 });
      }
      if (url.pathname === "/api/admin/purchase-packages" && init?.method === "POST") {
        createCalls += 1;
        throw new TypeError("synthetic connection drop");
      }
      if (url.pathname === "/api/admin/users") return jsonResponse({ data: [], total: 0 });
      return jsonResponse({ data: [], total: 0 });
    });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<AdminFeature apiBaseUrl="/api" fetcher={fetcher} storage={storage} />);

    await user.click(screen.getByRole("button", { name: "购买套餐" }));
    await screen.findByText("没有套餐记录");
    await user.click(screen.getByRole("button", { name: "新建套餐" }));
    await user.type(screen.getByLabelText("名称"), "uncertain intent");
    await user.click(screen.getByRole("button", { name: "创建套餐" }));
    expect(await screen.findByRole("region", { name: "套餐写入结果待核对" })).toBeInTheDocument();
    expect(createCalls).toBe(1);

    await user.click(screen.getByRole("button", { name: "返回目录" }));
    expect(screen.getByRole("heading", { name: "新建购买套餐" })).toBeInTheDocument();
    await user.click(screen.getByRole("link", { name: /Hao AI管理后台/ }));
    await user.click(screen.getByRole("button", { name: "用户管理" }));
    await user.click(screen.getByRole("button", { name: "退出登录" }));
    expect(screen.getByRole("heading", { name: "新建购买套餐" })).toBeInTheDocument();
    expect(storage.getItem("admin_token")).toBe("admin-token");

    confirm.mockReturnValue(true);
    await user.click(screen.getByRole("button", { name: "用户管理" }));
    expect(await screen.findByText("没有符合条件的用户"))
      .toBeInTheDocument();
    expect(createCalls).toBe(1);
    await user.click(screen.getByRole("button", { name: "购买套餐" }));
    await user.click(screen.getByRole("button", { name: "新建套餐" }));
    expect(screen.getByRole("heading", { name: "新建购买套餐" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "套餐写入结果待核对" })).toBeNull();
    expect(createCalls).toBe(1);
  });

});


describe("admin membership confirmed exit states", () => {
  it("keeps a non-default create receipt clean for beforeunload, return, and sidebar navigation", async () => {
    const user = userEvent.setup();
    const storage = adminStorage();
    const createdPackages: Array<Record<string, unknown>> = [];
    const fetcher = vi.fn<AdminFetcher>(async (input, init) => {
      const url = new URL(String(input), window.location.href);
      const method = init?.method ?? "GET";
      if (url.pathname === "/api/admin/stats") return jsonResponse(stats());
      if (url.pathname === "/api/admin/purchase-packages" && method === "GET") {
        return jsonResponse({ data: createdPackages, total: createdPackages.length });
      }
      if (url.pathname === "/api/admin/purchase-packages" && method === "POST") {
        const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
        const created = {
          id: "package-confirmed-" + (createdPackages.length + 1), category: "credits", name: "",
          price: 0, credits: null, days: null, sort_order: 0, is_active: true,
          created_at: "synthetic-created", updated_at: "synthetic-updated", ...body,
        };
        createdPackages.push(created);
        return jsonResponse(created);
      }
      if (url.pathname === "/api/admin/users" && method === "GET") return jsonResponse({ data: [], total: 0 });
      throw new Error("Unexpected synthetic request: " + method + " " + url.pathname);
    });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<AdminFeature apiBaseUrl="/api" fetcher={fetcher} storage={storage} />);

    await user.click(screen.getByRole("button", { name: "购买套餐" }));
    await screen.findByText("没有套餐记录");
    await user.click(screen.getByRole("button", { name: "新建套餐" }));
    await user.type(screen.getByLabelText("名称"), "Priority plan");
    await user.type(screen.getByLabelText("价格（整数元）"), "37");
    await user.click(screen.getByRole("button", { name: "创建套餐" }));

    expect(await screen.findByText(/Priority plan · credits · 37 元/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "新建购买套餐" })).toBeInTheDocument();
    const beforeUnload = new Event("beforeunload", { cancelable: true });
    expect(window.dispatchEvent(beforeUnload)).toBe(true);

    await user.click(screen.getByRole("button", { name: "返回目录" }));
    expect(confirm).not.toHaveBeenCalled();
    expect(await screen.findByRole("heading", { name: "购买套餐" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "新建套餐" }));
    await user.type(screen.getByLabelText("名称"), "Sidebar plan");
    await user.click(screen.getByRole("button", { name: "创建套餐" }));
    expect(await screen.findByText(/Sidebar plan · credits · 0 元/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "用户管理" }));
    expect(confirm).not.toHaveBeenCalled();
    expect(await screen.findByText("没有符合条件的用户")).toBeInTheDocument();
  });

  it("does not report a dirty edit after the host confirms its delete", async () => {
    const user = userEvent.setup();
    const storage = adminStorage();
    const packageRecord = {
      id: "package-delete-confirmed", category: "credits", name: "Original plan", price: 12,
      credits: null, days: null, sort_order: 0, is_active: true,
      created_at: "synthetic-created", updated_at: "synthetic-updated",
    };
    const fetcher = vi.fn<AdminFetcher>(async (input, init) => {
      const url = new URL(String(input), window.location.href);
      const method = init?.method ?? "GET";
      if (url.pathname === "/api/admin/stats") return jsonResponse(stats());
      if (url.pathname === "/api/admin/purchase-packages" && method === "GET") {
        return jsonResponse({ data: [packageRecord], total: 1 });
      }
      if (url.pathname === "/api/admin/purchase-packages/package-delete-confirmed" && method === "GET") {
        return jsonResponse(packageRecord);
      }
      if (url.pathname === "/api/admin/purchase-packages/package-delete-confirmed" && method === "DELETE") {
        return jsonResponse({ message: "套餐已删除" });
      }
      throw new Error("Unexpected synthetic request: " + method + " " + url.pathname);
    });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<AdminFeature apiBaseUrl="/api" fetcher={fetcher} storage={storage} />);

    await user.click(screen.getByRole("button", { name: "购买套餐" }));
    const row = (await screen.findByText("package-delete-confirmed")).closest("tr");
    if (!row) throw new Error("expected synthetic package row");
    await user.click(within(row).getByRole("button", { name: "编辑" }));
    await user.type(await screen.findByLabelText("名称"), " edited");
    await user.click(screen.getByRole("button", { name: "删除套餐" }));

    expect(await screen.findByText("服务端已确认删除套餐。返回目录前保留此确认。")).toBeInTheDocument();
    expect(confirm).toHaveBeenCalledTimes(1);
    const beforeUnload = new Event("beforeunload", { cancelable: true });
    expect(window.dispatchEvent(beforeUnload)).toBe(true);

    await user.click(screen.getByRole("button", { name: "返回目录" }));
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole("heading", { name: "购买套餐" })).toBeInTheDocument();
  });
});
