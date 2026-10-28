import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { AdminFetcher } from "../admin/adminApi";
import { createAdminSystemConfigApi } from "./adminSystemConfigApi";
import { AdminSystemConfig } from "./AdminSystemConfig";
import type { AdminSystemConfigExitSnapshot } from "./adminSystemConfigTypes";

afterEach(() => cleanup());

function config(overrides: Record<string, unknown> = {}) {
  return {
    id: "config-1",
    muse_token: "synthetic-muse-value",
    site_title: "Synthetic title",
    site_tagline: "Synthetic tagline",
    storyboard_cost_multiplier: 2,
    membership_benefits: "Synthetic benefits",
    payments_enabled: false,
    tutorial_url: "",
    purchase_qr_code_url: "",
    purchase_contact_info: "",
    team_created_limit: 3,
    team_joined_limit: 3,
    team_member_limit: 20,
    invite_code_ttl_hours: 24,
    chapter_lock_idle_minutes: 15,
    aistarslab_credit_multiplier: 1,
    frontend_url: "",
    allowed_origins: "",
    upload_dir: "",
    worker_max_concurrency: "0",
    ...overrides,
  };
}

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), { status, headers: { "Content-Type": "application/json" } });
}

function renderEditor(fetcher: AdminFetcher) {
  const onSaved = vi.fn();
  const onExitStateChange = vi.fn<(snapshot: AdminSystemConfigExitSnapshot) => void>();
  render(
    <AdminSystemConfig
      api={createAdminSystemConfigApi(fetcher, () => "synthetic-admin-token", "/api", "http://localhost/")}
      mode="edit"
      configId="config-1"
      ownerScope="session-1:admin-synthetic-admin"
      viewKey="system-config/edit/config-1"
      administratorId="synthetic-admin"
      listVersion={0}
      onAuthenticationError={vi.fn()}
      onEdit={vi.fn()}
      onCancel={vi.fn()}
      onSaved={onSaved}
      onExitStateChange={onExitStateChange}
    />,
  );
  return { onSaved, onExitStateChange };
}

describe("admin system config editor", () => {
  it("explains when public URL and service startup settings take effect", async () => {
    const fetcher = vi.fn<AdminFetcher>(async () => jsonResponse(config()));
    renderEditor(fetcher);

    await screen.findByLabelText("Worker 最大并发");
    expect(screen.getByText(/生成新密码重置链接时优先使用应用显式设置的前端地址/))
      .toHaveTextContent("后续新链接");
    expect(screen.getByText(/浏览器跨域来源规则在 API 服务启动时读取/))
      .toHaveTextContent("重启服务");
    expect(screen.getByText(/文件读取时会动态查询此目录/))
      .toHaveTextContent("不代表所有上传流程");
    expect(screen.getByText(/启用 Worker 的主机在启动时读取此上限/))
      .toHaveTextContent("重启 Worker 服务");
  });

  it("keeps an empty legacy list read-only and retries only the list GET", async () => {
    const user = userEvent.setup();
    const fetcher = vi.fn<AdminFetcher>(async () => jsonResponse({ data: [], total: 0 }));
    const onEdit = vi.fn();
    const api = createAdminSystemConfigApi(fetcher, () => "synthetic-admin-token", "/api", "http://localhost/");
    render(
      <AdminSystemConfig
        api={api}
        mode="list"
        ownerScope="session-1:admin-synthetic-admin"
        viewKey="system-config/list"
        administratorId="synthetic-admin"
        listVersion={0}
        onAuthenticationError={vi.fn()}
        onEdit={onEdit}
        onCancel={vi.fn()}
        onSaved={vi.fn()}
      />,
    );

    expect(await screen.findByText("当前服务没有返回系统配置记录。此页面不会自动新建配置。"))
      .toBeInTheDocument();
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0]?.[1]?.method).toBe("GET");
    expect(screen.queryByRole("button", { name: /新建/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重试读取" }));
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    expect(fetcher.mock.calls.every(([, init]) => init?.method === "GET")).toBe(true);
    expect(onEdit).not.toHaveBeenCalled();
  });

  it("submits only changed fields and accepts the service's zero-to-default projection", async () => {
    const user = userEvent.setup();
    let current = config();
    const writes: Array<Record<string, unknown>> = [];
    const fetcher = vi.fn<AdminFetcher>(async (_input, init) => {
      if ((init?.method ?? "GET") === "GET") return jsonResponse(current);
      const patch = JSON.parse(String(init?.body)) as Record<string, unknown>;
      writes.push(patch);
      current = { ...current, ...patch };
      if (patch.team_created_limit === 0) current.team_created_limit = 3;
      return jsonResponse(current);
    });
    const { onSaved, onExitStateChange } = renderEditor(fetcher);

    const title = await screen.findByLabelText("网站标题");
    expect(screen.getByLabelText("启用在线支付")).not.toBeChecked();
    expect(screen.getByRole("button", { name: "保存配置" })).toBeDisabled();
    await user.clear(title);
    const createdLimit = screen.getByLabelText("可创建团队数");
    await user.clear(createdLimit);
    expect(screen.getByText("可创建团队数需要填写安全范围内的整数。"))
      .toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存配置" })).toBeDisabled();
    await user.type(createdLimit, "0");
    await user.click(screen.getByRole("button", { name: "保存配置" }));

    expect(await screen.findByText("服务端已确认更新系统配置。"))
      .toBeInTheDocument();
    expect(writes).toEqual([{ site_title: "", team_created_limit: 0 }]);
    expect(screen.getByLabelText("网站标题")).toHaveValue("");
    expect(screen.getByLabelText("可创建团队数")).toHaveValue(3);
    expect(onSaved).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(onExitStateChange).toHaveBeenLastCalledWith(expect.objectContaining({ pending: null })));
    expect(JSON.stringify(onExitStateChange.mock.calls)).not.toContain("synthetic-muse-value");
  });

  it("keeps the submitted draft and exit guard when a matching-ID 200 does not apply the patch", async () => {
    const user = userEvent.setup();
    const current = config();
    let putCount = 0;
    const fetcher = vi.fn<AdminFetcher>(async (_input, init) => {
      if ((init?.method ?? "GET") === "GET") return jsonResponse(current);
      putCount += 1;
      return jsonResponse(current);
    });
    const { onSaved, onExitStateChange } = renderEditor(fetcher);

    await user.clear(await screen.findByLabelText("网站标题"));
    await user.type(screen.getByLabelText("网站标题"), "Submitted title");
    await user.click(screen.getByLabelText("启用在线支付"));
    await user.click(screen.getByRole("button", { name: "保存配置" }));

    expect(await screen.findByRole("region", { name: "系统配置写入结果待核对" })).toBeInTheDocument();
    expect(screen.getByLabelText("网站标题")).toHaveValue("Submitted title");
    expect(screen.getByLabelText("网站标题")).toBeDisabled();
    expect(screen.getByLabelText("启用在线支付")).toBeChecked();
    expect(screen.getByLabelText("启用在线支付")).toBeDisabled();
    expect(putCount).toBe(1);
    expect(onSaved).not.toHaveBeenCalled();
    expect(onExitStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      pending: expect.objectContaining({ kind: "unknown-write" }),
    }));
    expect(screen.queryByText("服务端已确认更新系统配置。")).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain("synthetic-muse-value");
    expect(JSON.stringify(onExitStateChange.mock.calls)).not.toContain("synthetic-muse-value");
  });

  it("shows a separate current snapshot without unlocking or replacing an unknown draft", async () => {
    const user = userEvent.setup();
    let getCount = 0;
    let putCount = 0;
    const fetcher = vi.fn<AdminFetcher>(async (_input, init) => {
      if ((init?.method ?? "GET") === "GET") {
        getCount += 1;
        return jsonResponse(getCount === 1
          ? config()
          : config({ site_title: "Current snapshot title", muse_token: "synthetic-snapshot-muse-secret" }));
      }
      putCount += 1;
      return jsonResponse({ detail: "synthetic unavailable" }, 503);
    });
    const { onSaved, onExitStateChange } = renderEditor(fetcher);

    await user.clear(await screen.findByLabelText("网站标题"));
    await user.type(screen.getByLabelText("网站标题"), "Frozen submitted title");
    await user.clear(screen.getByLabelText("Muse 令牌"));
    await user.type(screen.getByLabelText("Muse 令牌"), "synthetic-submitted-muse-secret");
    await user.click(screen.getByRole("button", { name: "保存配置" }));
    expect(await screen.findByRole("region", { name: "系统配置写入结果待核对" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "只读核对当前配置" }));

    const snapshot = await screen.findByRole("region", { name: "系统配置只读快照" });
    expect(snapshot).toHaveTextContent("Current snapshot title");
    expect(snapshot).toHaveTextContent("Frozen submitted title");
    expect(snapshot).toHaveTextContent("Muse 令牌");
    expect(snapshot).toHaveTextContent("已配置");
    expect(snapshot).toHaveTextContent("当前读取：已配置");
    expect(snapshot.textContent).not.toContain("synthetic-snapshot-muse-secret");
    expect(snapshot.textContent).not.toContain("synthetic-submitted-muse-secret");
    expect(document.body.textContent).not.toContain("synthetic-snapshot-muse-secret");
    expect(document.body.textContent).not.toContain("synthetic-submitted-muse-secret");
    expect(screen.getByLabelText("网站标题")).toHaveValue("Frozen submitted title");
    expect(screen.getByLabelText("网站标题")).toBeDisabled();
    expect(putCount).toBe(1);
    expect(getCount).toBe(2);
    expect(onSaved).not.toHaveBeenCalled();
    expect(onExitStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      pending: expect.objectContaining({ kind: "unknown-write" }),
    }));
  });

  it("keeps equal readback values unknown and ignores a late snapshot after an owner switch", async () => {
    const user = userEvent.setup();
    let oldGetCount = 0;
    let oldPutCount = 0;
    let finishEqualSnapshot!: (response: Response) => void;
    let finishLateSnapshot!: (response: Response) => void;
    const equalSnapshot = new Promise<Response>((resolve) => { finishEqualSnapshot = resolve; });
    const lateSnapshot = new Promise<Response>((resolve) => { finishLateSnapshot = resolve; });
    const oldFetcher = vi.fn<AdminFetcher>(async (_input, init) => {
      if ((init?.method ?? "GET") === "GET") {
        oldGetCount += 1;
        if (oldGetCount === 1) return jsonResponse(config());
        return oldGetCount === 2 ? equalSnapshot : lateSnapshot;
      }
      oldPutCount += 1;
      return jsonResponse({ detail: "synthetic unavailable" }, 503);
    });
    const onSaved = vi.fn();
    const oldApi = createAdminSystemConfigApi(oldFetcher, () => "synthetic-admin-token", "/api", "http://localhost/");
    const oldProps = {
      api: oldApi,
      mode: "edit" as const,
      configId: "config-1",
      ownerScope: "session-1:admin-synthetic-admin",
      viewKey: "system-config/edit/config-1",
      administratorId: "synthetic-admin",
      listVersion: 0,
      onAuthenticationError: vi.fn(),
      onEdit: vi.fn(),
      onCancel: vi.fn(),
      onSaved,
    };
    const view = render(<AdminSystemConfig {...oldProps} />);

    await user.clear(await screen.findByLabelText("网站标题"));
    await user.type(screen.getByLabelText("网站标题"), "Equal snapshot title");
    await user.click(screen.getByRole("button", { name: "保存配置" }));
    expect(await screen.findByRole("region", { name: "系统配置写入结果待核对" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "只读核对当前配置" }));
    expect(await screen.findByRole("button", { name: "核对中…" })).toBeDisabled();

    finishEqualSnapshot(jsonResponse(config({ site_title: "Equal snapshot title" })));
    const snapshot = await screen.findByRole("region", { name: "系统配置只读快照" });
    expect(snapshot).toHaveTextContent("Equal snapshot title");
    expect(screen.getByLabelText("网站标题")).toHaveValue("Equal snapshot title");
    expect(screen.getByLabelText("网站标题")).toBeDisabled();
    expect(screen.getByRole("region", { name: "系统配置写入结果待核对" })).toBeInTheDocument();
    expect(oldPutCount).toBe(1);

    await user.click(screen.getByRole("button", { name: "只读核对当前配置" }));
    expect(await screen.findByRole("button", { name: "核对中…" })).toBeDisabled();

    const newFetcher = vi.fn<AdminFetcher>(async () => jsonResponse(config({ site_title: "New owner title" })));
    const newApi = createAdminSystemConfigApi(newFetcher, () => "synthetic-admin-token-new", "/api", "http://localhost/");
    view.rerender(<AdminSystemConfig {...oldProps}
      api={newApi}
      ownerScope="session-2:admin-synthetic-admin"
      administratorId="synthetic-admin-new"
      onSaved={vi.fn()}
    />);
    expect(await screen.findByLabelText("网站标题")).toHaveValue("New owner title");

    await act(async () => {
      finishLateSnapshot(jsonResponse(config({ site_title: "Late old-owner snapshot" })));
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(oldGetCount).toBe(3);
    expect(screen.getByLabelText("网站标题")).toHaveValue("New owner title");
    expect(screen.queryByRole("region", { name: "系统配置写入结果待核对" })).not.toBeInTheDocument();
    expect(screen.queryByText("Late old-owner snapshot")).not.toBeInTheDocument();
    expect(oldPutCount).toBe(1);
    expect(onSaved).not.toHaveBeenCalled();
  });

  it("freezes an unknown write, performs only a readback, and permits a new intent only after explicit discard", async () => {
    const user = userEvent.setup();
    let current = config();
    let putCount = 0;
    let readCount = 0;
    const fetcher = vi.fn<AdminFetcher>(async (_input, init) => {
      const method = init?.method ?? "GET";
      if (method === "GET") {
        readCount += 1;
        return jsonResponse(current);
      }
      putCount += 1;
      if (putCount <= 2) return jsonResponse({ detail: "synthetic unavailable" }, 503);
      current = { ...current, ...(JSON.parse(String(init?.body)) as Record<string, unknown>) };
      return jsonResponse(current);
    });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderEditor(fetcher);

    await user.clear(await screen.findByLabelText("网站标题"));
    await user.type(screen.getByLabelText("网站标题"), "新标题");
    await user.click(screen.getByRole("button", { name: "保存配置" }));
    expect(await screen.findByRole("region", { name: "系统配置写入结果待核对" })).toBeInTheDocument();
    expect(putCount).toBe(1);
    expect(screen.getByRole("button", { name: "保存配置" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "只读核对当前配置" }));
    expect(await screen.findByText("只读快照已读取；当前值不能归因到刚才的保存请求。"))
      .toBeInTheDocument();
    expect(putCount).toBe(1);
    expect(readCount).toBe(2);

    await user.click(screen.getByRole("button", { name: "明确放弃并重新读取" }));
    await waitFor(() => expect(readCount).toBe(3));
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText("网站标题")).toHaveValue("Synthetic title");
    expect(screen.getByRole("button", { name: "保存配置" })).toBeDisabled();

    await user.clear(screen.getByLabelText("网站标题"));
    await user.type(screen.getByLabelText("网站标题"), "后续新意图");
    await user.click(screen.getByRole("button", { name: "保存配置" }));
    expect(await screen.findByRole("region", { name: "系统配置写入结果待核对" })).toBeInTheDocument();
    expect(putCount).toBe(2);
    expect(screen.getByRole("button", { name: "只读核对当前配置" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "只读核对当前配置" }));
    expect(await screen.findByText("只读快照已读取；当前值不能归因到刚才的保存请求。"))
      .toBeInTheDocument();
    expect(readCount).toBe(4);
    expect(putCount).toBe(2);
  });
});
