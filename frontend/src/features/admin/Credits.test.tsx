import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApiError } from "../../shared/api/errors";
import type { AdminCreditLog, AdminCreditsApi, AdminUserCredit } from "./adminCreditsTypes";
import { CreditLogs } from "./CreditLogs";
import { UserCreditEditor, UserCreditsList } from "./UserCredits";

afterEach(() => cleanup());

function credit(overrides: Partial<AdminUserCredit> = {}): AdminUserCredit {
  return {
    id: "credit-1",
    user_id: "user-1",
    credits: 12,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-02T00:00:00Z",
    ...overrides,
  };
}

function log(overrides: Partial<AdminCreditLog> = {}): AdminCreditLog {
  return {
    id: "log-1",
    user_id: "user-1",
    amount: 5,
    balance_after: 17,
    type: "charge",
    description: "积分调整",
    task_id: null,
    created_at: "2026-10-02T00:00:00Z",
    ...overrides,
  };
}

function creditsApi(overrides: Partial<AdminCreditsApi> = {}): AdminCreditsApi {
  return {
    listUserCredits: vi.fn(async () => ({ data: [credit()], total: 1 })),
    getUserCredit: vi.fn(async () => credit()),
    updateUserCredit: vi.fn(async (_id: string, amount: number) => credit({ credits: amount })),
    listCreditLogs: vi.fn(async () => ({ data: [log()], total: 1 })),
    ...overrides,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

describe("user credits list and editor", () => {
  it("sorts and paginates the list and opens the exact credit row for editing", async () => {
    const firstPage = Array.from({ length: 10 }, (_, index) => credit({ id: `credit-${index}`, user_id: `user-${index}` }));
    const listUserCredits = vi.fn<AdminCreditsApi["listUserCredits"]>(async (query) => query.skip === 0
      ? { data: firstPage, total: 11 }
      : { data: [credit({ id: "credit-last", user_id: "last-user" })], total: 11 });
    const onEdit = vi.fn();
    const api = creditsApi({ listUserCredits });
    const user = userEvent.setup();
    render(<UserCreditsList api={api} reloadVersion={0} onEdit={onEdit} onAuthenticationError={vi.fn()} />);

    await screen.findByRole("button", { name: "编辑积分记录 credit-0" });
    await user.click(screen.getByRole("button", { name: "下一页" }));
    await screen.findByRole("button", { name: "编辑积分记录 credit-last" });
    expect(listUserCredits).toHaveBeenLastCalledWith({
      skip: 10,
      limit: 10,
      sort_field: "updated_at",
      sort_order: "desc",
    }, expect.any(AbortSignal));

    await user.click(screen.getByRole("button", { name: "剩余积分" }));
    await waitFor(() => expect(listUserCredits).toHaveBeenLastCalledWith({
      skip: 0,
      limit: 10,
      sort_field: "credits",
      sort_order: "asc",
    }, expect.any(AbortSignal)));
    await user.click(screen.getByRole("button", { name: "编辑积分记录 credit-0" }));
    expect(onEdit).toHaveBeenCalledWith("credit-0");
  });

  it("allows zero and negative balances, rejects blank and fractional drafts, then saves by credit id", async () => {
    const updateUserCredit = vi.fn<AdminCreditsApi["updateUserCredit"]>(async (id, amount) => credit({ id, credits: amount }));
    const getUserCredit = vi.fn<AdminCreditsApi["getUserCredit"]>(async () => credit({ id: "credit-row-7" }));
    const onSaved = vi.fn();
    const user = userEvent.setup();
    render(<UserCreditEditor
      api={creditsApi({ getUserCredit, updateUserCredit })}
      creditId="credit-row-7"
      onCancel={vi.fn()}
      onSaved={onSaved}
      onAuthenticationError={vi.fn()}
    />);

    const input = await screen.findByLabelText("积分数量");
    await user.clear(input);
    await user.click(screen.getByRole("button", { name: "保存" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("空值和小数无法保存");
    expect(updateUserCredit).not.toHaveBeenCalled();

    await user.clear(input);
    await user.type(input, "1.5");
    await user.click(screen.getByRole("button", { name: "保存" }));
    expect(updateUserCredit).not.toHaveBeenCalled();

    await user.clear(input);
    await user.type(input, "0");
    await user.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
    expect(updateUserCredit).toHaveBeenCalledWith("credit-row-7", 0, expect.any(AbortSignal));
  });

  it("rejects fractional text before number conversion can round it to an integer", async () => {
    const updateUserCredit = vi.fn<AdminCreditsApi["updateUserCredit"]>(async (_id, amount) => credit({ credits: amount }));
    const onSaved = vi.fn();
    render(<UserCreditEditor
      api={creditsApi({ updateUserCredit })}
      creditId="credit-1"
      onCancel={vi.fn()}
      onSaved={onSaved}
      onAuthenticationError={vi.fn()}
    />);

    const input = await screen.findByLabelText("积分数量");
    fireEvent.change(input, { target: { value: "1.0000000000000001" } });
    await act(async () => {
      fireEvent.submit(input.closest("form")!);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(input).toHaveValue("1.0000000000000001");
    expect(screen.getByRole("alert")).toHaveTextContent("请输入有效的整数积分");
    expect(updateUserCredit).not.toHaveBeenCalled();
    expect(onSaved).not.toHaveBeenCalled();
  });

  it("keeps a rejected draft and does not retry a refused update", async () => {
    const updateUserCredit = vi.fn<AdminCreditsApi["updateUserCredit"]>(async () => {
      throw new ApiError("http", "更新积分被拒绝", 409, "更新积分被拒绝");
    });
    const user = userEvent.setup();
    render(<UserCreditEditor
      api={creditsApi({ updateUserCredit })}
      creditId="credit-1"
      onCancel={vi.fn()}
      onSaved={vi.fn()}
      onAuthenticationError={vi.fn()}
    />);

    const input = await screen.findByLabelText("积分数量");
    await user.clear(input);
    await user.type(input, "-8");
    await user.click(screen.getByRole("button", { name: "保存" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("保存请求已被拒绝：更新积分被拒绝");
    expect(screen.getByLabelText("积分数量")).toHaveValue("-8");
    expect(updateUserCredit).toHaveBeenCalledTimes(1);
  });

  it.each([
    { label: "network failure", error: new ApiError("network", "网络中断") },
    { label: "server error", error: new ApiError("http", "服务暂不可用", 503, "服务暂不可用") },
    { label: "unreadable success response", error: new ApiError("invalid-response", "服务返回的数据无法识别。") },
  ])("marks a $label as unknown and requires an explicit GET before another decision", async ({ error }) => {
    const getUserCredit = vi.fn<AdminCreditsApi["getUserCredit"]>()
      .mockResolvedValueOnce(credit())
      .mockResolvedValueOnce(credit({ credits: 4 }));
    const updateUserCredit = vi.fn<AdminCreditsApi["updateUserCredit"]>(async () => {
      throw error;
    });
    const user = userEvent.setup();
    render(<UserCreditEditor
      api={creditsApi({ getUserCredit, updateUserCredit })}
      creditId="credit-1"
      onCancel={vi.fn()}
      onSaved={vi.fn()}
      onAuthenticationError={vi.fn()}
    />);

    const input = await screen.findByLabelText("积分数量");
    await user.clear(input);
    await user.type(input, "-6");
    await user.click(screen.getByRole("button", { name: "保存" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("保存状态尚未确认");
    expect(screen.getByLabelText("积分数量")).toHaveValue("-6");
    expect(updateUserCredit).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "保存" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "重新读取当前积分" }));
    await waitFor(() => expect(getUserCredit).toHaveBeenCalledTimes(2));
    expect(await screen.findByLabelText("积分数量")).toHaveValue("4");
    expect(screen.getByRole("status")).toHaveTextContent("已重新读取当前积分");
    expect(screen.getByRole("button", { name: "保存" })).toBeEnabled();
    expect(updateUserCredit).toHaveBeenCalledTimes(1);
  });

  it("guards duplicate submits while a save is still pending", async () => {
    const pending = deferred<AdminUserCredit>();
    const updateUserCredit = vi.fn<AdminCreditsApi["updateUserCredit"]>(() => pending.promise);
    const api = creditsApi({ updateUserCredit });
    render(<UserCreditEditor api={api} creditId="credit-1" onCancel={vi.fn()} onSaved={vi.fn()} onAuthenticationError={vi.fn()} />);

    await screen.findByLabelText("积分数量");
    const form = screen.getByRole("button", { name: "保存" }).closest("form");
    expect(form).not.toBeNull();
    fireEvent.submit(form!);
    fireEvent.submit(form!);
    expect(updateUserCredit).toHaveBeenCalledTimes(1);

    await act(async () => {
      pending.resolve(credit({ credits: 12 }));
      await pending.promise;
    });
  });

  it("ignores a stale late authentication error after switching credit records", async () => {
    const oldRequest = deferred<AdminUserCredit>();
    const getUserCredit = vi.fn<AdminCreditsApi["getUserCredit"]>((id) => id === "old" ? oldRequest.promise : Promise.resolve(
      credit({ id: "new", user_id: "current-user", credits: 9 }),
    ));
    const onAuthenticationError = vi.fn();
    const api = creditsApi({ getUserCredit });
    const view = render(<UserCreditEditor api={api} creditId="old" onCancel={vi.fn()} onSaved={vi.fn()} onAuthenticationError={onAuthenticationError} />);
    await screen.findByRole("status");

    const oldSignal = getUserCredit.mock.calls[0]?.[1];
    view.rerender(<UserCreditEditor api={api} creditId="new" onCancel={vi.fn()} onSaved={vi.fn()} onAuthenticationError={onAuthenticationError} />);
    expect(await screen.findByDisplayValue("9")).toBeInTheDocument();
    expect(oldSignal?.aborted).toBe(true);

    await act(async () => {
      oldRequest.reject(new ApiError("http", "登录已过期", 401, "登录已过期"));
      await oldRequest.promise.catch(() => undefined);
    });
    expect(onAuthenticationError).not.toHaveBeenCalled();
    expect(screen.getByLabelText("用户 ID")).toHaveValue("current-user");
  });
});

describe("credit log list", () => {
  it.each(["__proto__", "constructor", "toString"])("renders prototype-named type %s as its original text", async (type) => {
    const listCreditLogs = vi.fn<AdminCreditsApi["listCreditLogs"]>(async () => ({
      data: [log({ type })],
      total: 1,
    }));
    render(<CreditLogs api={creditsApi({ listCreditLogs })} onAuthenticationError={vi.fn()} />);

    const table = await screen.findByRole("table", { name: "积分流水列表" });
    expect(within(table).getByText(type, { exact: true })).toBeInTheDocument();
  });

  it("shows known and unknown types, signed and zero amounts, escapes text, and resets paging for filter and sort", async () => {
    const records = [
      log({ id: "charge", type: "charge", amount: 5, description: "充值,说明" }),
      log({ id: "usage", type: "usage", amount: -7, description: "<img src=x>" }),
      log({ id: "unknown", type: "manual_adjustment", amount: 0, description: null }),
    ];
    const listCreditLogs = vi.fn<AdminCreditsApi["listCreditLogs"]>(async (query) => ({
      data: query.skip === 0 ? records : [log({ id: "last" })],
      total: 11,
    }));
    const user = userEvent.setup();
    render(<CreditLogs api={creditsApi({ listCreditLogs })} onAuthenticationError={vi.fn()} />);

    const table = await screen.findByRole("table", { name: "积分流水列表" });
    expect(within(table).getByText("充值")).toBeInTheDocument();
    expect(within(table).getByText("消耗")).toBeInTheDocument();
    expect(within(table).getByText("manual_adjustment")).toBeInTheDocument();
    expect(within(table).getByText("+5")).toBeInTheDocument();
    expect(within(table).getByText("-7")).toBeInTheDocument();
    expect(within(table).getByText("0")).toBeInTheDocument();
    expect(within(table).getByText("<img src=x>")).toBeInTheDocument();
    expect(within(table).queryByRole("img")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "下一页" }));
    await screen.findByText("第 2 / 2 页");
    await user.clear(screen.getByLabelText("用户 ID"));
    await user.type(screen.getByLabelText("用户 ID"), " user-2 ");
    await user.click(screen.getByRole("button", { name: "查询" }));
    await waitFor(() => expect(listCreditLogs).toHaveBeenLastCalledWith({
      skip: 0,
      limit: 10,
      sort_field: "created_at",
      sort_order: "desc",
      user_id: "user-2",
    }, expect.any(AbortSignal)));
    expect(screen.getByText("第 1 / 2 页")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "变动" }));
    await waitFor(() => expect(listCreditLogs).toHaveBeenLastCalledWith({
      skip: 0,
      limit: 10,
      sort_field: "amount",
      sort_order: "asc",
      user_id: "user-2",
    }, expect.any(AbortSignal)));
  });

  it("routes current authentication failures to the page owner", async () => {
    const onAuthenticationError = vi.fn();
    const api = creditsApi({
      listCreditLogs: vi.fn(async () => { throw new ApiError("http", "登录已过期", 401, "登录已过期"); }),
    });
    render(<CreditLogs api={api} onAuthenticationError={onAuthenticationError} />);
    await waitFor(() => expect(onAuthenticationError).toHaveBeenCalledTimes(1));
  });
});
