import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState, type ComponentProps } from "react";
import type { AdminFetcher } from "../admin/adminApi";
import { createAdminSeriesApi } from "./adminSeriesApi";
import { AdminSeries } from "./AdminSeries";
import { DEFAULT_ADMIN_SERIES_LIST_STATE } from "./adminSeriesTypes";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function record(overrides: Record<string, unknown> = {}) {
  return {
    id: "series-1",
    user_id: "admin-1",
    name: "原剧集",
    description: "原简介",
    image_url: "cover.png",
    created_at: "2026-10-16T08:00:00",
    updated_at: "2026-10-16T08:00:00",
    ...overrides,
  };
}

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), { status, headers: { "Content-Type": "application/json" } });
}

function makeApi(fetcher: AdminFetcher) {
  return createAdminSeriesApi(fetcher, () => "synthetic-admin-token", () => "admin-1", "/api", "http://localhost/");
}

function renderSeries(api: ReturnType<typeof makeApi>, props: Partial<ComponentProps<typeof AdminSeries>> = {}) {
  const defaults: ComponentProps<typeof AdminSeries> = {
    api,
    mode: "list",
    ownerScope: "session-1:admin-admin-1",
    viewKey: "series/list",
    administratorId: "admin-1",
    listVersion: 0,
    listState: DEFAULT_ADMIN_SERIES_LIST_STATE,
    onListStateChange: vi.fn(),
    onAuthenticationError: vi.fn(),
    onCreate: vi.fn(),
    onEdit: vi.fn(),
    onCancel: vi.fn(),
    onSaved: vi.fn(),
    onExitStateChange: vi.fn(),
  };
  function Harness() {
    const [listState, setListState] = useState(props.listState ?? DEFAULT_ADMIN_SERIES_LIST_STATE);
    return <AdminSeries {...defaults} {...props}
      listState={props.listState ?? listState}
      onListStateChange={props.onListStateChange ?? setListState}
    />;
  }
  return render(<Harness />);
}

async function abandonUnknownEdit(snapshotName: string) {
  const user = userEvent.setup();
  const putBodies: Array<Record<string, unknown>> = [];
  const onExitStateChange = vi.fn();
  let detailReads = 0;
  let putCount = 0;
  const fetcher = vi.fn<AdminFetcher>(async (_input, init) => {
    const method = init?.method ?? "GET";
    if (method === "GET") {
      detailReads += 1;
      return jsonResponse(detailReads === 1 ? record() : record({
        name: snapshotName,
        description: "其他管理员新简介",
        image_url: "new-cover.png",
      }));
    }
    if (method === "PUT") {
      putCount += 1;
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      putBodies.push(body);
      if (putCount === 1) return jsonResponse({ detail: "synthetic unavailable" }, 500);
      return jsonResponse(record({
        name: body.name ?? snapshotName,
        description: body.description === undefined ? "其他管理员新简介" : body.description,
        image_url: body.image_url === undefined ? "new-cover.png" : body.image_url,
      }));
    }
    throw new Error(`Unexpected request: ${method}`);
  });
  vi.spyOn(window, "confirm").mockReturnValue(true);
  renderSeries(makeApi(fetcher), {
    mode: "edit",
    recordId: "series-1",
    viewKey: "series/edit/series-1",
    onExitStateChange,
  });
  await screen.findByDisplayValue("原剧集");
  await user.clear(screen.getByLabelText("剧集名称"));
  await user.type(screen.getByLabelText("剧集名称"), "目标名称");
  await user.click(screen.getByRole("button", { name: "保存剧集" }));
  expect(await screen.findByRole("region", { name: "剧集写入结果待核对" })).toBeInTheDocument();
  expect(putBodies).toEqual([{ name: "目标名称" }]);

  await user.click(screen.getByRole("button", { name: "只读核对当前记录" }));
  expect(await screen.findByText("其他管理员新简介")).toBeInTheDocument();
  expect(screen.getByText("new-cover.png")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "明确放弃原意图" }));
  await waitFor(() => expect(screen.queryByRole("region", { name: "剧集写入结果待核对" })).not.toBeInTheDocument());
  return { user, putBodies, putCount: () => putCount, onExitStateChange };
}

describe("admin series views", () => {
  it("applies raw filters, keeps server query order, exports the full matching query, and limits export to 1000", async () => {
    const user = userEvent.setup();
    const calls: URL[] = [];
    const fetcher = vi.fn<AdminFetcher>(async (input) => {
      const url = new URL(String(input));
      calls.push(url);
      return jsonResponse({ data: [record()], total: 1 });
    });
    const createObjectURL = vi.fn(() => "blob:series");
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectURL });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revokeObjectURL });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    renderSeries(makeApi(fetcher));

    expect(await screen.findByText("原剧集")).toBeInTheDocument();
    await user.type(screen.getByLabelText("按名称筛选"), " %_ ");
    await user.type(screen.getByLabelText("按用户 ID 筛选"), "writer /?");
    await user.click(screen.getByRole("button", { name: "查询" }));
    await waitFor(() => expect(calls.some((url) => url.searchParams.get("name") === " %_ ")).toBe(true));
    const filtered = calls.find((url) => url.searchParams.get("name") === " %_ ");
    expect(filtered?.searchParams.get("user_id")).toBe("writer /?");
    await user.click(screen.getByRole("button", { name: "名称" }));
    await waitFor(() => expect(calls.at(-1)?.searchParams.get("sort_field")).toBe("name"));
    await user.click(screen.getByRole("button", { name: "名称 asc" }));
    await waitFor(() => expect(calls.at(-1)?.searchParams.get("sort_order")).toBe("desc"));
    await user.click(screen.getByRole("button", { name: "导出 CSV（最多1000条）" }));
    await waitFor(() => expect(createObjectURL).toHaveBeenCalledOnce());
    const exportUrl = calls.at(-1);
    expect(exportUrl?.searchParams.get("skip")).toBe("0");
    expect(exportUrl?.searchParams.get("limit")).toBe("1000");
    expect(exportUrl?.searchParams.get("name")).toBe(" %_ ");
    expect(exportUrl?.searchParams.get("user_id")).toBe("writer /?");
    expect(exportUrl?.searchParams.get("sort_order")).toBe("desc");
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:series");
  });

  it("creates only the name and nullable description and leaves the exact receipt visible through onSaved", async () => {
    const user = userEvent.setup();
    const bodies: unknown[] = [];
    const fetcher = vi.fn<AdminFetcher>(async (_input, init) => {
      if (init?.method === "POST") {
        bodies.push(JSON.parse(String(init.body)));
        return jsonResponse(record({ id: "created-1", name: "", description: "新简介" }));
      }
      throw new Error("Unexpected request");
    });
    const onSaved = vi.fn();
    const api = makeApi(fetcher);
    renderSeries(api, { mode: "create", viewKey: "series/create", onSaved });

    await user.clear(screen.getByLabelText("剧集名称"));
    await user.type(screen.getByLabelText("剧集简介"), "新简介");
    await user.click(screen.getByRole("button", { name: "保存剧集" }));

    await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
    expect(bodies).toEqual([{ name: "", description: "新简介" }]);
    expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ id: "created-1", user_id: "admin-1" }), "create");
  });

  it("loads edits by encoded ID, writes only changed fields, represents clearing nullable values as null, and skips no-op PUT", async () => {
    const user = userEvent.setup();
    const calls: Array<{ url: URL; method: string; body?: unknown }> = [];
    const fetcher = vi.fn<AdminFetcher>(async (input, init) => {
      const url = new URL(String(input));
      const method = init?.method ?? "GET";
      const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
      calls.push({ url, method, body });
      if (method === "GET") return jsonResponse(record({ id: "series/a b" }));
      return jsonResponse(record({ id: "series/a b", name: "原剧集", description: null }));
    });
    const onSaved = vi.fn();
    const firstView = renderSeries(makeApi(fetcher), { mode: "edit", recordId: "series/a b", viewKey: "series/edit/series/a b", onSaved });

    await screen.findByDisplayValue("原剧集");
    await user.clear(screen.getByLabelText("剧集简介"));
    await user.click(screen.getByRole("button", { name: "保存剧集" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
    expect(calls[0]?.url.pathname).toBe("/api/admin/series/series%2Fa%20b");
    expect(calls.filter((call) => call.method === "PUT").map((call) => call.body)).toEqual([{ description: null }]);
    firstView.unmount();

    const noOpFetcher = vi.fn<AdminFetcher>(async () => jsonResponse(record({ description: "", image_url: "" })));
    renderSeries(makeApi(noOpFetcher), { mode: "edit", recordId: "series-1", viewKey: "series/edit/series-1" });
    await screen.findByDisplayValue("原剧集");
    expect(screen.getByRole("button", { name: "保存剧集" })).toBeDisabled();
    expect(noOpFetcher).toHaveBeenCalledTimes(1);
  });

  it("publishes in-flight state before awaiting and keeps unknown writes frozen through read-only mismatch", async () => {
    const user = userEvent.setup();
    let putCount = 0;
    let resolvePut!: (response: Response) => void;
    let markPutStarted!: () => void;
    const started = new Promise<void>((resolve) => { markPutStarted = resolve; });
    const pendingPut = new Promise<Response>((resolve) => { resolvePut = resolve; });
    const fetcher = vi.fn<AdminFetcher>(async (_input, init) => {
      if (init?.method === "GET") return jsonResponse(record({ name: putCount > 0 ? "服务端当前值" : "原剧集" }));
      putCount += 1;
      markPutStarted();
      return pendingPut;
    });
    const onExitStateChange = vi.fn();
    renderSeries(makeApi(fetcher), { mode: "edit", recordId: "series-1", viewKey: "series/edit/series-1", onExitStateChange });
    await screen.findByDisplayValue("原剧集");
    await user.clear(screen.getByLabelText("剧集名称"));
    await user.type(screen.getByLabelText("剧集名称"), "保留的草稿");
    await user.click(screen.getByRole("button", { name: "保存剧集" }));
    await started;
    expect(onExitStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      pending: expect.objectContaining({ kind: "write-in-flight", operation: "update" }),
    }));
    resolvePut(jsonResponse({ detail: "unknown" }, 500));
    expect(await screen.findByRole("region", { name: "剧集写入结果待核对" })).toBeInTheDocument();
    expect(screen.getByLabelText("剧集名称")).toHaveValue("保留的草稿");
    expect(screen.getByLabelText("剧集名称")).toBeDisabled();
    expect(putCount).toBe(1);
    await user.click(screen.getByRole("button", { name: "只读核对当前记录" }));
    expect(await screen.findByText("服务端当前值")).toBeInTheDocument();
    expect(screen.getByLabelText("剧集名称")).toHaveValue("保留的草稿");
    expect(screen.getByRole("region", { name: "剧集写入结果待核对" })).toBeInTheDocument();
    expect(putCount).toBe(1);
  });

  it("shows create candidates as read-only context without attributing or retrying the unknown POST", async () => {
    const user = userEvent.setup();
    let postCount = 0;
    let listCount = 0;
    const fetcher = vi.fn<AdminFetcher>(async (_input, init) => {
      if (init?.method === "POST") {
        postCount += 1;
        return jsonResponse({ detail: "synthetic unavailable" }, 500);
      }
      listCount += 1;
      return jsonResponse({ data: [record({ id: "candidate-1", name: "可能重复" })], total: 1 });
    });
    renderSeries(makeApi(fetcher), { mode: "create", viewKey: "series/create" });
    await user.type(screen.getByLabelText("剧集名称"), "可能重复");
    await user.click(screen.getByRole("button", { name: "保存剧集" }));
    expect(await screen.findByRole("region", { name: "剧集写入结果待核对" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "只读核对候选列表" }));
    expect(await screen.findByText("candidate-1 · 可能重复")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "剧集写入结果待核对" })).toBeInTheDocument();
    expect(screen.getByLabelText("剧集名称")).toHaveValue("可能重复");
    expect(screen.getByRole("button", { name: "保存剧集" })).toBeDisabled();
    expect(postCount).toBe(1);
    expect(listCount).toBe(1);
  });

  it("does not download a late export after its list owner changes", async () => {
    const user = userEvent.setup();
    let resolveExport!: (response: Response) => void;
    const exportResponse = new Promise<Response>((resolve) => { resolveExport = resolve; });
    let listReads = 0;
    const fetcher = vi.fn<AdminFetcher>(async (_input, init) => {
      if (init?.signal && new URL(String(_input)).searchParams.get("limit") === "1000") return exportResponse;
      listReads += 1;
      return jsonResponse({ data: [record()], total: 1 });
    });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    const view = renderSeries(makeApi(fetcher));
    await screen.findByText("原剧集");
    await user.click(screen.getByRole("button", { name: "导出 CSV（最多1000条）" }));
    view.rerender(<AdminSeries
      api={makeApi(fetcher)} mode="list" ownerScope="session-2:admin-admin-1" viewKey="series/list"
      administratorId="admin-1" listVersion={0} listState={DEFAULT_ADMIN_SERIES_LIST_STATE}
      onListStateChange={vi.fn()} onAuthenticationError={vi.fn()} onCreate={vi.fn()} onEdit={vi.fn()}
      onCancel={vi.fn()} onSaved={vi.fn()} onExitStateChange={vi.fn()}
    />);
    await waitFor(() => expect(listReads).toBeGreaterThanOrEqual(2));
    await act(async () => { resolveExport(jsonResponse({ data: [record()], total: 1 })); });
    expect(click).not.toHaveBeenCalled();
  });

  it("rebases non-intent fields from the read-only snapshot when the original name is already present", async () => {
    const { putBodies, putCount, onExitStateChange } = await abandonUnknownEdit("目标名称");

    expect(screen.getByLabelText("剧集名称")).toHaveValue("目标名称");
    expect(screen.getByLabelText("剧集简介")).toHaveValue("其他管理员新简介");
    expect(screen.getByLabelText("封面 URL")).toHaveValue("new-cover.png");
    expect(screen.getByRole("button", { name: "保存剧集" })).toBeDisabled();
    expect(putBodies).toEqual([{ name: "目标名称" }]);
    expect(putCount()).toBe(1);
    expect(onExitStateChange).toHaveBeenLastCalledWith(expect.objectContaining({ pending: null }));
  });

  it("retains only the original patch fields after abandoning a snapshot with a different name", async () => {
    const { user, putBodies, putCount, onExitStateChange } = await abandonUnknownEdit("名称仍未更新");

    expect(screen.getByLabelText("剧集名称")).toHaveValue("目标名称");
    expect(screen.getByLabelText("剧集简介")).toHaveValue("其他管理员新简介");
    expect(screen.getByLabelText("封面 URL")).toHaveValue("new-cover.png");
    expect(screen.getByRole("button", { name: "保存剧集" })).toBeEnabled();
    expect(onExitStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      pending: expect.objectContaining({
        kind: "dirty-draft",
        draft: { name: "目标名称", description: "其他管理员新简介", imageUrl: "new-cover.png" },
      }),
    }));
    await user.click(screen.getByRole("button", { name: "保存剧集" }));

    await waitFor(() => expect(putCount()).toBe(2));
    expect(putBodies).toEqual([{ name: "目标名称" }, { name: "目标名称" }]);
  });
});
