import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { demoSeries } from "../features/series/demoSeries";
import { AuthProvider, useAuth } from "../features/auth/AuthProvider";
import { API_TOKEN_KEY } from "../shared/api/storage";
import { createApiServices } from "../shared/api/services";
import type { Series, User } from "../shared/api/contracts";
import { Workspace } from "./Workspace";

const apiBaseUrl = "http://127.0.0.1:4175/api";
const authenticatedUser: User = {
  id: "series-ui-user",
  username: "剧集作者",
  email: "series@example.invalid",
  is_superuser: false,
  membership_type: "free",
  membership_expires_at: null,
  avatar_url: null,
  bio: null,
  created_at: "2026-10-01T00:00:00Z",
};

function jsonResponse(payload: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload,
  } as Response;
}

function emptyResponse(): Response {
  return {
    ok: true,
    status: 204,
    json: async (): Promise<unknown> => { throw new Error("204 must not be parsed as JSON"); },
  } as Response;
}

function seriesRow(values: Partial<Series> = {}): Series {
  return {
    ...demoSeries[0]!,
    id: "new-series",
    user_id: authenticatedUser.id,
    name: "新剧",
    description: "",
    image_url: "http://127.0.0.1:4175/uploads/series-cover.webp",
    style_prompt_id: "style-one",
    style_prompt_name: "悬疑风格",
    style_prompt_owner_name: "系统",
    team_id: null,
    team_name: null,
    owner_name: authenticatedUser.username,
    can_enter: true,
    created_at: "2026-10-14T00:00:00Z",
    updated_at: "2026-10-14T00:00:00Z",
    ...values,
  };
}

function AuthLogoutBridge({ capture }: { capture: (logout: () => void) => void }) {
  capture(useAuth().logout);
  return null;
}

function renderWorkspace(fetcher: typeof fetch, captureLogout?: (logout: () => void) => void) {
  window.localStorage.clear();
  window.localStorage.setItem(API_TOKEN_KEY, "creative-series-token");
  const services = createApiServices(apiBaseUrl, { storage: window.localStorage, fetcher });
  return render(
    <AuthProvider services={services}>
      {captureLogout === undefined ? null : <AuthLogoutBridge capture={captureLogout} />}
      <Workspace />
    </AuthProvider>,
  );
}

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.restoreAllMocks();
});

describe("mounted series management", () => {
  it("creates with API cover and style, edits by detail response, and deletes after exact confirmation", async () => {
    const user = userEvent.setup();
    const rows: Series[] = [];
    const calls: Array<{ url: URL; init: RequestInit }> = [];
    const fetcher = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const url = new URL(String(input));
      calls.push({ url, init });
      if (url.pathname === "/api/auth/me") return jsonResponse(authenticatedUser);
      if (url.pathname === "/api/series" && init.method === "GET") return jsonResponse(rows);
      if (url.pathname === "/api/prompts") {
        return jsonResponse([{
          id: "style-one",
          name: "悬疑风格",
          category: "style",
          is_system: true,
          system_prompt: "不显示提示词正文",
        }]);
      }
      if (url.pathname === "/api/upload/image") {
        return jsonResponse({ url: "http://127.0.0.1:4175/uploads/series-cover.webp" });
      }
      if (url.pathname === "/api/series" && init.method === "POST") {
        const body = JSON.parse(String(init.body)) as Partial<Series>;
        const created = seriesRow({ ...body, id: "new-series", name: String(body.name) });
        rows.unshift(created);
        return jsonResponse(created, 201);
      }
      const detailPath = url.pathname.match(/^\/api\/series\/([^/]+)$/);
      if (detailPath?.[1] !== undefined) {
        const id = decodeURIComponent(detailPath[1]);
        const existing = rows.find((item) => item.id === id);
        if (existing === undefined) return jsonResponse({ detail: "剧集不存在" }, 404);
        if (init.method === "GET") return jsonResponse(existing);
        if (init.method === "PUT") {
          const patch = JSON.parse(String(init.body)) as Partial<Series>;
          const updated = { ...existing, ...patch };
          rows[rows.findIndex((item) => item.id === id)] = updated;
          return jsonResponse(updated, 200);
        }
        if (init.method === "DELETE") {
          rows.splice(rows.findIndex((item) => item.id === id), 1);
          return emptyResponse();
        }
      }
      throw new Error(`Unexpected request: ${init.method} ${url.pathname}`);
    });

    renderWorkspace(fetcher);
    await screen.findByRole("heading", { name: "还没有剧集" });
    await user.click(screen.getAllByRole("button", { name: "创建剧集" })[0]!);
    await user.type(screen.getByLabelText("剧集名称"), "新剧");
    await user.type(screen.getByLabelText("剧集简介"), "项目简介");
    await user.selectOptions(screen.getByLabelText("风格提示词"), "style-one");
    const file = new File(["cover"], "cover.webp", { type: "image/webp" });
    await user.upload(screen.getByLabelText("上传剧集封面"), file);
    await waitFor(() => expect(calls.some((call) => call.url.pathname === "/api/upload/image")).toBe(true));
    await user.click(screen.getByRole("button", { name: "保存剧集" }));

    const card = await screen.findByRole("article");
    expect(within(card).getByRole("heading", { name: "新剧" })).toBeInTheDocument();
    const createCall = calls.find((call) => call.url.pathname === "/api/series" && call.init.method === "POST");
    expect(JSON.parse(String(createCall?.init.body))).toEqual({
      name: "新剧",
      description: "项目简介",
      image_url: "http://127.0.0.1:4175/uploads/series-cover.webp",
      style_prompt_id: "style-one",
    });
    expect(new Headers(createCall?.init.headers).get("Authorization")).toBe("Bearer creative-series-token");
    const uploadCall = calls.find((call) => call.url.pathname === "/api/upload/image");
    expect(new Headers(uploadCall?.init.headers).has("Content-Type")).toBe(false);
    expect([...(uploadCall?.init.body as FormData).keys()]).toEqual(["file"]);

    await user.click(within(card).getByRole("button", { name: "编辑剧集：新剧" }));
    await screen.findByDisplayValue("项目简介");
    await user.clear(screen.getByLabelText("剧集名称"));
    await user.type(screen.getByLabelText("剧集名称"), "新剧已修改");
    await user.click(screen.getByRole("button", { name: "保存剧集" }));
    await screen.findByText("已保存《新剧已修改》的剧集信息，正在刷新剧集列表。");
    const updatedCard = screen.getByRole("article");
    await user.click(within(updatedCard).getByRole("button", { name: "删除剧集：新剧已修改" }));
    const confirmation = screen.getByLabelText("输入剧集名称以确认");
    await user.type(confirmation, "新剧已修改");
    await user.click(screen.getByRole("button", { name: "删除剧集" }));
    await screen.findByRole("heading", { name: "还没有剧集" });
    expect(calls.some((call) => call.url.pathname === "/api/series/new-series" && call.init.method === "DELETE"))
      .toBe(true);
    expect(screen.getByText("已删除《新剧已修改》，正在刷新剧集列表。")).toBeInTheDocument();
    expect(rows).toEqual([]);
  });

  it("does not apply a late create response or log out a newer view after logout", async () => {
    const user = userEvent.setup();
    let logoutFromProvider: (() => void) | undefined;
    let resolveCreate!: (response: Response) => void;
    const fetcher = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const url = new URL(String(input));
      if (url.pathname === "/api/auth/me") return jsonResponse(authenticatedUser);
      if (url.pathname === "/api/series" && init.method === "GET") return jsonResponse([]);
      if (url.pathname === "/api/prompts") return jsonResponse([]);
      if (url.pathname === "/api/series" && init.method === "POST") {
        return new Promise<Response>((resolve) => { resolveCreate = resolve; });
      }
      throw new Error(`Unexpected request: ${init.method} ${url.pathname}`);
    });

    renderWorkspace(fetcher, (logout) => { logoutFromProvider = logout; });
    await screen.findByRole("heading", { name: "还没有剧集" });
    await user.click(screen.getAllByRole("button", { name: "创建剧集" })[0]!);
    await user.type(screen.getByLabelText("剧集名称"), "迟到响应");
    await user.click(screen.getByRole("button", { name: "保存剧集" }));
    await waitFor(() => expect(resolveCreate).toBeTypeOf("function"));
    await user.click(screen.getByRole("button", { name: "退出" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByLabelText("剧集名称")).toHaveValue("迟到响应");
    expect(window.localStorage.getItem(API_TOKEN_KEY)).toBe("creative-series-token");
    expect(screen.queryByRole("heading", { name: "欢迎回来" })).not.toBeInTheDocument();

    if (logoutFromProvider === undefined) {
      throw new Error("AuthProvider logout bridge was not initialized.");
    }
    act(() => logoutFromProvider?.());
    expect(await screen.findByRole("heading", { name: "欢迎回来" })).toBeInTheDocument();

    await act(async () => {
      resolveCreate(jsonResponse(seriesRow({ id: "late-series", name: "迟到响应" }), 201));
    });
    expect(window.localStorage.getItem(API_TOKEN_KEY)).toBeNull();
    expect(screen.queryByText(/已创建《迟到响应》/)).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "欢迎回来" })).toBeInTheDocument();
  });
});
