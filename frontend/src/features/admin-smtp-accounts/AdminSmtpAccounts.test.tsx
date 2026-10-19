import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { AdminFetcher } from "../admin/adminApi";
import { createAdminSmtpAccountsApi } from "./adminSmtpAccountsApi";
import { AdminSmtpAccounts, type AdminSmtpAccountsProps } from "./AdminSmtpAccounts";
import type { AdminSmtpAccountRecord } from "./adminSmtpAccountsTypes";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function account(overrides: Partial<AdminSmtpAccountRecord> = {}) {
  return {
    id: "smtp-1",
    host: "smtp.example.test",
    port: 465,
    user: "sender@example.test",
    password: "synthetic-readback-secret-253",
    daily_sent: 0,
    is_active: true,
    created_at: "2026-10-16T08:00:00",
    updated_at: "2026-10-16T08:00:00",
    ...overrides,
  };
}

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), { status, headers: { "Content-Type": "application/json" } });
}

function renderAccounts(fetcher: AdminFetcher, mode: "list" | "create" | "edit", overrides: Partial<AdminSmtpAccountsProps> = {}) {
  const defaults: AdminSmtpAccountsProps = {
    api: createAdminSmtpAccountsApi(fetcher, () => "synthetic-token", () => "admin-253", "/api", "http://localhost/"),
    mode,
    ...(mode === "edit" ? { recordId: "smtp-1" } : {}),
    ownerScope: "session-253:admin-admin-253",
    viewKey: `smtp-accounts/${mode}`,
    administratorId: "admin-253",
    listVersion: 0,
    onAuthenticationError: vi.fn(),
    onCreate: vi.fn(),
    onEdit: vi.fn(),
    onCancel: vi.fn(),
    onSaved: vi.fn(),
    onExitStateChange: vi.fn(),
  };
  return render(<AdminSmtpAccounts {...defaults} {...overrides} />);
}

describe("SMTP account views", () => {
  it("loads the full list without query parameters, paginates locally, and never exposes returned passwords", async () => {
    const user = userEvent.setup();
    const rows = Array.from({ length: 11 }, (_, index) => account({ id: `smtp-${index}`, user: `sender-${index}@example.test` }));
    const fetcher = vi.fn<AdminFetcher>(async (_input, init) => {
      expect(init?.method ?? "GET").toBe("GET");
      return jsonResponse({ data: rows, total: rows.length });
    });
    renderAccounts(fetcher, "list");

    expect(await screen.findByText("sender-0@example.test")).toBeInTheDocument();
    expect(screen.queryByText("sender-10@example.test")).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain("synthetic-readback-secret-253");
    expect(new URL(String(fetcher.mock.calls[0]?.[0])).search).toBe("");
    await user.click(screen.getByRole("button", { name: "下一页" }));
    expect(await screen.findByText("sender-10@example.test")).toBeInTheDocument();
    expect(fetcher).toHaveBeenCalledOnce();
  });

  it("creates with only the five supported fields and leaves a safe acknowledgement on screen", async () => {
    const user = userEvent.setup();
    let submitted: unknown;
    const fetcher = vi.fn<AdminFetcher>(async (_input, init) => {
      submitted = JSON.parse(String(init?.body));
      return jsonResponse({ id: "smtp-created", user: "new@example.test", message: "添加成功" });
    });
    renderAccounts(fetcher, "create");

    await user.type(screen.getByLabelText("SMTP 邮箱"), "new@example.test");
    await user.type(screen.getByLabelText("SMTP 授权码"), "synthetic-create-secret-253");
    await user.click(screen.getByRole("button", { name: "保存账号" }));

    expect(await screen.findByRole("status")).toHaveTextContent("smtp-created");
    expect(submitted).toEqual({ user: "new@example.test", password: "synthetic-create-secret-253", host: "smtp.qq.com", port: 465, is_active: true });
    expect(document.body.textContent).not.toContain("synthetic-create-secret-253");
    expect((screen.getByLabelText("SMTP 授权码") as HTMLInputElement).value).toBe("");
  });

  it("does not write an unchanged edit and submits only changed metadata when the password stays blank", async () => {
    const user = userEvent.setup();
    const requests: Array<{ method: string; body: unknown }> = [];
    const fetcher = vi.fn<AdminFetcher>(async (_input, init) => {
      const method = init?.method ?? "GET";
      requests.push({ method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      return method === "PUT" ? jsonResponse({ message: "更新成功" }) : jsonResponse(account());
    });
    renderAccounts(fetcher, "edit");

    await screen.findByDisplayValue("sender@example.test");
    expect(screen.getByRole("button", { name: "保存账号" })).toBeDisabled();
    expect((screen.getByLabelText("SMTP 授权码") as HTMLInputElement).value).toBe("");
    expect(document.body.textContent).not.toContain("synthetic-readback-secret-253");
    await user.clear(screen.getByLabelText("SMTP 主机"));
    await user.type(screen.getByLabelText("SMTP 主机"), "smtp-new.example.test");
    await user.click(screen.getByRole("button", { name: "保存账号" }));

    expect(await screen.findByText("服务端已确认更新操作。该接口不返回字段回显。")).toBeInTheDocument();
    expect(requests.map(({ method }) => method)).toEqual(["GET", "PUT"]);
    expect(requests[1]?.body).toEqual({ host: "smtp-new.example.test" });
  });

  it("keeps an unknown edit frozen after a different readback and only releases after explicit discard", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const requests: Array<{ method: string; body: unknown }> = [];
    let detailReads = 0;
    const fetcher = vi.fn<AdminFetcher>(async (_input, init) => {
      const method = init?.method ?? "GET";
      requests.push({ method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      if (method === "PUT") return jsonResponse({ detail: "temporary failure" }, 503);
      detailReads += 1;
      return jsonResponse(account(detailReads > 1 ? { host: "changed-by-another-admin", user: "current@example.test" } : {}));
    });
    const onExitStateChange = vi.fn();
    renderAccounts(fetcher, "edit", { onExitStateChange });

    await screen.findByDisplayValue("sender@example.test");
    await user.clear(screen.getByLabelText("SMTP 主机"));
    await user.type(screen.getByLabelText("SMTP 主机"), "intended-host.example.test");
    await user.click(screen.getByRole("button", { name: "保存账号" }));
    expect(await screen.findByRole("region", { name: "SMTP 保存结果待核对" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存账号" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "只读核对当前账号" }));
    expect(await screen.findByText("changed-by-another-admin")).toBeInTheDocument();
    expect(screen.getByDisplayValue("intended-host.example.test")).toBeInTheDocument();
    expect(screen.getByDisplayValue("sender@example.test")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存账号" })).toBeDisabled();
    expect(requests.filter(({ method }) => method === "PUT")).toHaveLength(1);
    expect(document.body.textContent).not.toContain("synthetic-readback-secret-253");

    await user.click(screen.getByRole("button", { name: "明确放弃原意图" }));
    expect(confirm).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "保存账号" })).toBeEnabled();
    expect(screen.getByDisplayValue("intended-host.example.test")).toBeInTheDocument();
    expect(screen.getByDisplayValue("current@example.test")).toBeInTheDocument();
    expect(requests.filter(({ method }) => method === "PUT")).toHaveLength(1);
    const latestPending = onExitStateChange.mock.calls.at(-1)?.[0];
    expect(JSON.stringify(latestPending)).not.toContain("synthetic-readback-secret-253");
  });

  it("keeps an edit unavailable when the detail response belongs to a different record", async () => {
    const calls: string[] = [];
    const fetcher = vi.fn<AdminFetcher>(async (_input, init) => {
      calls.push(init?.method ?? "GET");
      return jsonResponse(account({ id: "smtp-other", user: "other@example.test" }));
    });
    renderAccounts(fetcher, "edit");

    expect(await screen.findByText("读取 SMTP 账号失败，请重试。")).toBeInTheDocument();
    expect(screen.queryByLabelText("SMTP 邮箱")).not.toBeInTheDocument();
    expect(calls).toEqual(["GET"]);
    expect(calls).not.toContain("PUT");
  });

  it("does not adopt a wrong-ID unknown-write snapshot or enable a second update", async () => {
    const user = userEvent.setup();
    const calls: Array<{ method: string; body?: unknown }> = [];
    let detailReads = 0;
    const fetcher = vi.fn<AdminFetcher>(async (_input, init) => {
      const method = init?.method ?? "GET";
      const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
      calls.push({ method, body });
      if (method === "PUT") return jsonResponse({ message: "temporary failure" }, 503);
      detailReads += 1;
      return jsonResponse(detailReads === 1
        ? account()
        : account({ id: "smtp-other", host: "mismatched-host.example.test" }));
    });
    renderAccounts(fetcher, "edit");

    await screen.findByDisplayValue("sender@example.test");
    await user.clear(screen.getByLabelText("SMTP 主机"));
    await user.type(screen.getByLabelText("SMTP 主机"), "intended-host.example.test");
    await user.click(screen.getByRole("button", { name: "保存账号" }));
    expect(await screen.findByRole("region", { name: "SMTP 保存结果待核对" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "只读核对当前账号" }));

    expect(await screen.findByText("只读核对失败，原保存结果仍未知。")).toBeInTheDocument();
    expect(screen.queryByText("mismatched-host.example.test")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存账号" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "明确放弃原意图" })).toBeDisabled();
    expect(calls.filter(({ method }) => method === "PUT")).toHaveLength(1);
  });

  it("clears an unknown create secret when the user explicitly abandons the intent", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const bodies: unknown[] = [];
    const fetcher = vi.fn<AdminFetcher>(async (_input, init) => {
      const body = JSON.parse(String(init?.body));
      bodies.push(body);
      return bodies.length === 1
        ? jsonResponse({ detail: "temporary failure" }, 503)
        : jsonResponse({ id: "created-after-reentry", user: "new@example.test", message: "添加成功" });
    });
    renderAccounts(fetcher, "create");

    await user.type(screen.getByLabelText("SMTP 邮箱"), "new@example.test");
    await user.clear(screen.getByLabelText("SMTP 主机"));
    await user.type(screen.getByLabelText("SMTP 主机"), "smtp-custom.example.test");
    await user.type(screen.getByLabelText("SMTP 授权码"), "synthetic-create-unknown-secret");
    await user.click(screen.getByRole("button", { name: "保存账号" }));
    expect(await screen.findByRole("region", { name: "SMTP 保存结果待核对" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "明确放弃原意图" }));
    expect(screen.getByLabelText("SMTP 邮箱")).toHaveValue("new@example.test");
    expect(screen.getByLabelText("SMTP 主机")).toHaveValue("smtp-custom.example.test");
    expect(screen.getByLabelText("SMTP 授权码")).toHaveValue("");
    expect(screen.getByRole("button", { name: "保存账号" })).toBeDisabled();
    expect(bodies).toHaveLength(1);

    await user.type(screen.getByLabelText("SMTP 授权码"), "synthetic-create-unknown-secret");
    await user.click(screen.getByRole("button", { name: "保存账号" }));
    expect(await screen.findByText(/服务端已确认创建 SMTP 账号 created-after-reentry/)).toBeInTheDocument();
    expect(bodies).toHaveLength(2);
  });

  it("clears an abandoned password-only edit and leaves the form unchanged", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    let detailReads = 0;
    const methods: string[] = [];
    const fetcher = vi.fn<AdminFetcher>(async (_input, init) => {
      const method = init?.method ?? "GET";
      methods.push(method);
      if (method === "PUT") return jsonResponse({ detail: "temporary failure" }, 503);
      detailReads += 1;
      return jsonResponse(account());
    });
    renderAccounts(fetcher, "edit");

    await screen.findByDisplayValue("sender@example.test");
    await user.type(screen.getByLabelText("SMTP 授权码"), "synthetic-password-only-secret");
    await user.click(screen.getByRole("button", { name: "保存账号" }));
    expect(await screen.findByRole("region", { name: "SMTP 保存结果待核对" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "只读核对当前账号" }));
    expect(await screen.findByText("当前邮箱")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "明确放弃原意图" }));

    expect(screen.getByLabelText("SMTP 授权码")).toHaveValue("");
    expect(screen.getByRole("button", { name: "保存账号" })).toBeDisabled();
    expect(methods.filter((method) => method === "PUT")).toHaveLength(1);
    expect(detailReads).toBe(2);
  });

  it("rebases abandoned metadata with snapshot values and never carries its secret into the next update", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const calls: Array<{ method: string; body?: Record<string, unknown> | undefined }> = [];
    let detailReads = 0;
    const fetcher = vi.fn<AdminFetcher>(async (_input, init) => {
      const method = init?.method ?? "GET";
      const body = typeof init?.body === "string" ? JSON.parse(init.body) as Record<string, unknown> : undefined;
      calls.push({ method, body });
      if (method === "PUT" && calls.filter((call) => call.method === "PUT").length === 1) {
        return jsonResponse({ detail: "temporary failure" }, 503);
      }
      if (method === "PUT") return jsonResponse({ message: "更新成功" });
      detailReads += 1;
      return jsonResponse(detailReads === 1
        ? account()
        : account({ host: "snapshot-host.example.test", user: "current@example.test", port: 587, is_active: false }));
    });
    renderAccounts(fetcher, "edit");

    await screen.findByDisplayValue("sender@example.test");
    await user.clear(screen.getByLabelText("SMTP 主机"));
    await user.type(screen.getByLabelText("SMTP 主机"), "intended-host.example.test");
    await user.type(screen.getByLabelText("SMTP 授权码"), "synthetic-metadata-secret");
    await user.click(screen.getByRole("button", { name: "保存账号" }));
    expect(await screen.findByRole("region", { name: "SMTP 保存结果待核对" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "只读核对当前账号" }));
    expect(await screen.findByText("snapshot-host.example.test")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "明确放弃原意图" }));

    expect(screen.getByLabelText("SMTP 主机")).toHaveValue("intended-host.example.test");
    expect(screen.getByLabelText("SMTP 邮箱")).toHaveValue("current@example.test");
    expect(screen.getByLabelText("SMTP 端口")).toHaveValue("587");
    expect(screen.getByLabelText("SMTP 已启用")).not.toBeChecked();
    expect(screen.getByLabelText("SMTP 授权码")).toHaveValue("");
    await user.click(screen.getByRole("button", { name: "保存账号" }));
    expect(await screen.findByText("服务端已确认更新操作。该接口不返回字段回显。")).toBeInTheDocument();
    const puts = calls.filter(({ method }) => method === "PUT");
    expect(puts).toHaveLength(2);
    expect(puts[1]?.body).toEqual({ host: "intended-host.example.test" });
  });

  it("blocks four-star password replacements and requires confirmation before a single delete", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const calls: string[] = [];
    const fetcher = vi.fn<AdminFetcher>(async (input, init) => {
      const method = init?.method ?? "GET";
      calls.push(method);
      if (method === "DELETE") return jsonResponse({ message: "删除成功" });
      if (method === "PUT") return jsonResponse({ message: "更新成功" });
      if (new URL(String(input)).pathname.endsWith("/smtp-1")) return jsonResponse(account());
      return jsonResponse({ data: [account()], total: 1 });
    });

    const form = renderAccounts(fetcher, "edit");
    await screen.findByDisplayValue("sender@example.test");
    await user.type(screen.getByLabelText("SMTP 授权码"), "new****secret");
    expect(screen.getByRole("alert")).toHaveTextContent("不能包含连续四个星号");
    expect(screen.getByRole("button", { name: "保存账号" })).toBeDisabled();
    expect(calls).toEqual(["GET"]);
    form.unmount();

    renderAccounts(fetcher, "list");
    await screen.findByText("sender@example.test");
    await user.click(screen.getByRole("button", { name: "删除" }));
    expect(await screen.findByText("服务端已确认删除 SMTP 账号。")).toBeInTheDocument();
    expect(confirm).toHaveBeenCalledOnce();
    expect(calls).toEqual(["GET", "GET", "DELETE"]);
  });
});
