import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState, type ComponentProps } from "react";
import type { AdminFetcher } from "../admin/adminApi";
import { createAdminTasksApi } from "./adminTasksApi";
import { AdminTasks } from "./AdminTasks";
import { DEFAULT_ADMIN_TASK_LIST_STATE } from "./adminTasksTypes";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function task(overrides: Record<string, unknown> = {}) {
  return {
    id: "task-1",
    user_id: "writer-1",
    type: "future-task-type",
    status: "queued-for-review",
    credit_cost: 0,
    message_id: null,
    model_name: "",
    created_at: null,
    updated_at: "2026-10-16T08:00:00",
    ...overrides,
  };
}

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), { status, headers: { "Content-Type": "application/json" } });
}

function makeApi(fetcher: AdminFetcher, getToken = () => "synthetic-admin-token") {
  return createAdminTasksApi(fetcher, getToken, "/api", "http://localhost/");
}

function renderTasks(api: ReturnType<typeof makeApi>, props: Partial<ComponentProps<typeof AdminTasks>> = {}) {
  const defaults: ComponentProps<typeof AdminTasks> = {
    api,
    ownerScope: "session-1:admin-admin-1",
    viewKey: "admin-tasks/list",
    listState: DEFAULT_ADMIN_TASK_LIST_STATE,
    onListStateChange: vi.fn(),
    onAuthenticationError: vi.fn(),
  };
  function Harness() {
    const [listState, setListState] = useState(props.listState ?? DEFAULT_ADMIN_TASK_LIST_STATE);
    return <AdminTasks {...defaults} {...props}
      listState={props.listState ?? listState}
      onListStateChange={props.onListStateChange ?? setListState}
    />;
  }
  return render(<Harness />);
}

describe("admin tasks list", () => {
  it("uses default server paging, preserves raw filters, accepts unknown values, and sorts through the whitelist", async () => {
    const user = userEvent.setup();
    const urls: URL[] = [];
    const fetcher = vi.fn<AdminFetcher>(async (input) => {
      const url = new URL(String(input));
      urls.push(url);
      return jsonResponse({ data: [task()], total: 60 });
    });
    renderTasks(makeApi(fetcher));

    expect(await screen.findByText("queued-for-review")).toBeInTheDocument();
    expect(screen.getByText("future-task-type")).toBeInTheDocument();
    expect(screen.getByText("0")).toBeInTheDocument();
    expect(screen.getAllByText("—")).toHaveLength(2);
    expect(urls[0]?.search).toBe("?skip=0&limit=25&sort_field=created_at&sort_order=desc");

    await user.type(screen.getByLabelText("按用户 ID 筛选"), " writer /?");
    await user.selectOptions(screen.getByLabelText("按类型筛选"), "chat");
    await user.selectOptions(screen.getByLabelText("按状态筛选"), "failed");
    await user.type(screen.getByLabelText("按模型名称筛选"), " %_ ");
    await user.click(screen.getByRole("button", { name: "查询" }));
    await waitFor(() => expect(urls.at(-1)?.searchParams.get("model_name")).toBe(" %_ "));
    expect(urls.at(-1)?.searchParams.get("user_id")).toBe(" writer /?");
    expect(urls.at(-1)?.searchParams.get("type")).toBe("chat");
    expect(urls.at(-1)?.searchParams.get("status")).toBe("failed");

    await user.click(screen.getByRole("button", { name: "按类型排序" }));
    await waitFor(() => expect(urls.at(-1)?.searchParams.get("sort_field")).toBe("type"));
    expect(urls.at(-1)?.searchParams.get("sort_order")).toBe("asc");
    await user.click(screen.getByRole("button", { name: "下一页" }));
    await waitFor(() => expect(urls.at(-1)?.searchParams.get("skip")).toBe("25"));
    await user.click(screen.getByRole("button", { name: "重置" }));
    await waitFor(() => expect(urls.at(-1)?.search).toBe("?skip=0&limit=25&sort_field=type&sort_order=asc"));
  });

  it("exports the current filters and sort from offset zero with a 1000-row limit", async () => {
    const user = userEvent.setup();
    const urls: URL[] = [];
    const fetcher = vi.fn<AdminFetcher>(async (input) => {
      const url = new URL(String(input));
      urls.push(url);
      return jsonResponse({ data: [task()], total: 1 });
    });
    const createObjectURL = vi.fn(() => "blob:admin-tasks");
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectURL });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revokeObjectURL });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    renderTasks(makeApi(fetcher));
    await screen.findByText("queued-for-review");
    await user.type(screen.getByLabelText("按模型名称筛选"), "model %_ ");
    await user.click(screen.getByRole("button", { name: "查询" }));
    await waitFor(() => expect(urls.at(-1)?.searchParams.get("model_name")).toBe("model %_ "));
    await user.click(screen.getByRole("button", { name: "导出 CSV（最多1000条）" }));
    await waitFor(() => expect(createObjectURL).toHaveBeenCalledOnce());

    const exportQuery = urls.at(-1);
    expect(exportQuery?.searchParams.get("skip")).toBe("0");
    expect(exportQuery?.searchParams.get("limit")).toBe("1000");
    expect(exportQuery?.searchParams.get("model_name")).toBe("model %_ ");
    expect(exportQuery?.searchParams.get("sort_field")).toBe("created_at");
    expect(exportQuery?.searchParams.get("sort_order")).toBe("desc");
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:admin-tasks");
  });

  it("moves back to the last valid server page when the result count shrinks", async () => {
    const urls: URL[] = [];
    const fetcher = vi.fn<AdminFetcher>(async (input) => {
      const url = new URL(String(input));
      urls.push(url);
      return url.searchParams.get("skip") === "50"
        ? jsonResponse({ data: [], total: 20 })
        : jsonResponse({ data: [task()], total: 20 });
    });
    const api = makeApi(fetcher);
    const onAuthenticationError = vi.fn();
    function Harness() {
      const [listState, setListState] = useState({ ...DEFAULT_ADMIN_TASK_LIST_STATE, page: 3 });
      return <AdminTasks
        api={api}
        ownerScope="session-1:admin-admin-1"
        viewKey="admin-tasks/list"
        listState={listState}
        onListStateChange={setListState}
        onAuthenticationError={onAuthenticationError}
      />;
    }
    render(<Harness />);

    expect(await screen.findByText("queued-for-review")).toBeInTheDocument();
    expect(urls.map((url) => url.searchParams.get("skip"))).toEqual(["50", "0"]);
    expect(screen.getByText(/第\s*1\s*\/\s*1\s*页/)).toBeInTheDocument();
  });

  it("does not download a deferred export after the applied query changes", async () => {
    const user = userEvent.setup();
    let resolveExport!: (response: Response) => void;
    const exportResponse = new Promise<Response>((resolve) => { resolveExport = resolve; });
    const urls: URL[] = [];
    const fetcher = vi.fn<AdminFetcher>(async (input) => {
      const url = new URL(String(input));
      urls.push(url);
      if (url.searchParams.get("limit") === "1000") return exportResponse;
      return jsonResponse({ data: [task()], total: 1 });
    });
    const createObjectURL = vi.fn(() => "blob:late-tasks");
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectURL });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    renderTasks(makeApi(fetcher));
    await screen.findByText("queued-for-review");
    await user.click(screen.getByRole("button", { name: "导出 CSV（最多1000条）" }));
    await waitFor(() => expect(urls.at(-1)?.searchParams.get("limit")).toBe("1000"));
    await user.type(screen.getByLabelText("按模型名称筛选"), "changed-model");
    await user.click(screen.getByRole("button", { name: "查询" }));
    await waitFor(() => expect(urls.at(-1)?.searchParams.get("model_name")).toBe("changed-model"));
    await act(async () => { resolveExport(jsonResponse({ data: [task()], total: 1 })); });

    expect(createObjectURL).not.toHaveBeenCalled();
    expect(click).not.toHaveBeenCalled();
  });

  it("ignores an old owner's late 401 after switching API instances", async () => {
    let resolveOld!: (response: Response) => void;
    const oldResponse = new Promise<Response>((resolve) => { resolveOld = resolve; });
    const oldFetcher = vi.fn<AdminFetcher>(async () => oldResponse);
    const newFetcher = vi.fn<AdminFetcher>(async () => jsonResponse({ data: [task({ type: "new-owner-task-type" })], total: 1 }));
    const onAuthenticationError = vi.fn();
    const firstApi = makeApi(oldFetcher);
    const nextApi = makeApi(newFetcher);
    const view = render(<AdminTasks
      api={firstApi}
      ownerScope="session-1:admin-admin-1"
      viewKey="admin-tasks/list"
      listState={DEFAULT_ADMIN_TASK_LIST_STATE}
      onListStateChange={vi.fn()}
      onAuthenticationError={onAuthenticationError}
    />);
    await waitFor(() => expect(oldFetcher).toHaveBeenCalledOnce());
    view.rerender(<AdminTasks
      api={nextApi}
      ownerScope="session-2:admin-admin-2"
      viewKey="admin-tasks/list"
      listState={DEFAULT_ADMIN_TASK_LIST_STATE}
      onListStateChange={vi.fn()}
      onAuthenticationError={onAuthenticationError}
    />);
    expect(await screen.findByText("new-owner-task-type")).toBeInTheDocument();
    await act(async () => { resolveOld(jsonResponse({ detail: "expired" }, 401)); });
    expect(onAuthenticationError).not.toHaveBeenCalled();
    expect(screen.getByText("new-owner-task-type")).toBeInTheDocument();
  });

  it("ignores a late 401 from an earlier filter query on the same view", async () => {
    let resolveOld!: (response: Response) => void;
    const oldResponse = new Promise<Response>((resolve) => { resolveOld = resolve; });
    const fetcher = vi.fn<AdminFetcher>(async (input) => {
      const url = new URL(String(input));
      return url.searchParams.has("user_id")
        ? jsonResponse({ data: [task({ type: "new-query-type" })], total: 1 })
        : oldResponse;
    });
    const api = makeApi(fetcher);
    const onAuthenticationError = vi.fn();
    const view = render(<AdminTasks
      api={api}
      ownerScope="session-1:admin-admin-1"
      viewKey="admin-tasks/list"
      listState={DEFAULT_ADMIN_TASK_LIST_STATE}
      onListStateChange={vi.fn()}
      onAuthenticationError={onAuthenticationError}
    />);
    await waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
    view.rerender(<AdminTasks
      api={api}
      ownerScope="session-1:admin-admin-1"
      viewKey="admin-tasks/list"
      listState={{ ...DEFAULT_ADMIN_TASK_LIST_STATE, userId: "writer-2" }}
      onListStateChange={vi.fn()}
      onAuthenticationError={onAuthenticationError}
    />);
    expect(await screen.findByText("new-query-type")).toBeInTheDocument();
    await act(async () => { resolveOld(jsonResponse({ detail: "expired" }, 401)); });
    expect(onAuthenticationError).not.toHaveBeenCalled();
    expect(screen.getByText("new-query-type")).toBeInTheDocument();
  });
});
