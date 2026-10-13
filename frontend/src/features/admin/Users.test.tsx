import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApiError } from "../../shared/api/errors";
import type { AdminUsersApi, AdminUserRecord } from "./usersTypes";
import { Users, type UsersProps } from "./Users";

afterEach(cleanup);

function userRecord(overrides: Partial<AdminUserRecord> = {}): AdminUserRecord {
  return {
    id: "user-1",
    username: "writer",
    email: "writer@example.test",
    is_superuser: false,
    membership_type: "free",
    membership_expires_at: null,
    credits: 8,
    created_at: "2026-10-01T00:00:00Z",
    ...overrides,
  };
}

function usersApi(overrides: Partial<AdminUsersApi> = {}): AdminUsersApi {
  return {
    listUsers: vi.fn<AdminUsersApi["listUsers"]>(async () => ({ data: [], total: 0 })),
    getUser: vi.fn<AdminUsersApi["getUser"]>(async () => userRecord()),
    createUser: vi.fn<AdminUsersApi["createUser"]>(async () => userRecord()),
    updateUser: vi.fn<AdminUsersApi["updateUser"]>(async () => ({ message: "用户更新成功" })),
    ...overrides,
  };
}

function props(api: AdminUsersApi, overrides: Partial<UsersProps> = {}): UsersProps {
  return {
    api,
    onAuthenticationError: vi.fn(),
    mode: "list",
    listVersion: 0,
    onCreate: vi.fn(),
    onEdit: vi.fn(),
    onCancel: vi.fn(),
    onSaved: vi.fn(),
    ...overrides,
  };
}

function makeUsers(count: number): AdminUserRecord[] {
  return Array.from({ length: count }, (_, index) => {
    const number = index + 1;
    return userRecord({
      id: "id-" + number,
      username: "writer-" + number,
      email: "writer-" + number + "@example.test",
      is_superuser: number === 3,
      membership_type: number % 2 === 0 ? "free" : "premium",
      membership_expires_at: number === 1 ? "2020-01-01T00:00:00Z" : null,
      credits: number * 10,
      created_at: "2026-10-" + String(number).padStart(2, "0") + "T00:00:00Z",
    });
  });
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

describe("admin users views", () => {
  it("filters, sorts, paginates, and resets to the first page when filters change", async () => {
    const allUsers = makeUsers(12);
    const listUsers = vi.fn<AdminUsersApi["listUsers"]>(async (query) => {
      const matches = allUsers.filter((user) => {
        if (query.username && !user.username.includes(query.username)) return false;
        if (query.email && !user.email.includes(query.email)) return false;
        if (query.membership_type && user.membership_type !== query.membership_type) return false;
        if (query.is_superuser !== undefined && user.is_superuser !== query.is_superuser) return false;
        return true;
      });
      return { data: matches.slice(query.skip, query.skip + query.limit), total: matches.length };
    });
    const api = usersApi({ listUsers });
    const callbacks = props(api);
    const user = userEvent.setup();
    render(<Users {...callbacks} />);

    expect(await screen.findByRole("button", { name: "writer-1" })).toBeTruthy();
    expect(within(screen.getByRole("table", { name: "用户列表" })).getAllByRole("row")).toHaveLength(11);
    expect(within(screen.getByRole("table", { name: "用户列表" })).getAllByText("会员").length).toBeGreaterThan(0);
    expect(listUsers.mock.calls[0]?.[0]).toMatchObject({
      skip: 0,
      limit: 10,
      sort_field: "id",
      sort_order: "desc",
    });

    await user.click(screen.getByRole("button", { name: "下一页" }));
    await waitFor(() => expect(listUsers).toHaveBeenLastCalledWith(
      expect.objectContaining({ skip: 10, limit: 10 }),
      expect.any(AbortSignal),
    ));
    expect(await screen.findByRole("button", { name: "writer-11" })).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "按用户名排序" }));
    await waitFor(() => expect(listUsers).toHaveBeenLastCalledWith(
      expect.objectContaining({ sort_field: "username", sort_order: "asc", skip: 0 }),
      expect.any(AbortSignal),
    ));

    await user.type(screen.getByLabelText("用户名"), "writer-11");
    await user.type(screen.getByLabelText("邮箱"), "writer-11@example.test");
    await user.selectOptions(screen.getByLabelText("会员"), "premium");
    await user.selectOptions(screen.getByLabelText("管理员"), "false");
    await waitFor(() => expect(listUsers).toHaveBeenLastCalledWith(
      expect.objectContaining({
        skip: 0,
        username: "writer-11",
        email: "writer-11@example.test",
        membership_type: "premium",
        is_superuser: false,
      }),
      expect.any(AbortSignal),
    ));
    expect(await screen.findByRole("button", { name: "writer-11" })).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "编辑" }));
    expect(callbacks.onEdit).toHaveBeenCalledWith("id-11");
  });

  it("creates a user with the legacy fields and reports the save for list refresh", async () => {
    const createUser = vi.fn<AdminUsersApi["createUser"]>(async () => userRecord({ id: "created-user" }));
    const api = usersApi({ createUser });
    const callbacks = props(api, { mode: "create" });
    const user = userEvent.setup();
    render(<Users {...callbacks} />);

    await user.type(screen.getByLabelText("用户名"), "new-writer");
    await user.type(screen.getByLabelText("邮箱"), "new-writer@example.test");
    await user.type(screen.getByLabelText("初始密码"), "private-password");
    await user.selectOptions(screen.getByLabelText("会员类型"), "premium");
    const expiry = screen.getByLabelText("会员到期时间（留空=永久有效）");
    fireEvent.change(expiry, { target: { value: "2026-11-20T09:45" } });
    await user.clear(screen.getByLabelText("初始积分"));
    await user.type(screen.getByLabelText("初始积分"), "25");
    await user.click(screen.getByLabelText("超级管理员"));
    await user.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(createUser).toHaveBeenCalledWith({
      username: "new-writer",
      email: "new-writer@example.test",
      password: "private-password",
      is_superuser: true,
      membership_type: "premium",
      membership_expires_at: "2026-11-20T09:45",
      credits: 25,
    }, expect.any(AbortSignal)));
    await waitFor(() => expect(callbacks.onSaved).toHaveBeenCalledTimes(1));
  });

  it("prefills the edit form, keeps the existing password untouched, and refreshes after update", async () => {
    const existingUser = userRecord({
      id: "user-27",
      username: "member-27",
      email: "member-27@example.test",
      is_superuser: true,
      membership_type: "premium",
      membership_expires_at: "2027-03-04T05:06:07Z",
      credits: 18,
    });
    const getUser = vi.fn<AdminUsersApi["getUser"]>(async () => existingUser);
    const updateUser = vi.fn<AdminUsersApi["updateUser"]>(async () => ({ message: "用户更新成功" }));
    const api = usersApi({ getUser, updateUser });
    const callbacks = props(api, { mode: "edit", userId: existingUser.id });
    const user = userEvent.setup();
    render(<Users {...callbacks} />);

    await waitFor(() => expect(screen.getByLabelText("用户名")).toHaveProperty("value", "member-27"));
    expect(screen.getByLabelText("邮箱")).toHaveProperty("value", "member-27@example.test");
    expect(screen.getByLabelText("会员类型")).toHaveProperty("value", "premium");
    expect(screen.getByLabelText("积分")).toHaveProperty("value", "18");
    expect(screen.getByLabelText("超级管理员")).toBeChecked();
    expect(screen.queryByLabelText("密码")).toBeNull();

    await user.clear(screen.getByLabelText("邮箱"));
    await user.type(screen.getByLabelText("邮箱"), "member-new@example.test");
    await user.clear(screen.getByLabelText("会员到期时间（留空=永久有效）"));
    fireEvent.change(screen.getByLabelText("积分"), { target: { value: "" } });
    expect(screen.getByLabelText("积分")).toHaveProperty("value", "");
    await user.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(updateUser).toHaveBeenCalledWith(existingUser.id, {
      username: "member-27",
      email: "member-new@example.test",
      is_superuser: true,
      membership_type: "premium",
      membership_expires_at: null,
      credits: null,
    }, expect.any(AbortSignal)));
    await waitFor(() => expect(callbacks.onSaved).toHaveBeenCalledTimes(1));
  });

  it("keeps an existing membership string and untouched expiry wire value when other fields change", async () => {
    const existingUser = userRecord({
      id: "custom-member",
      username: "custom-member",
      email: "custom@example.test",
      membership_type: "gold",
      membership_expires_at: "2027-03-04T05:06:07",
      credits: 30,
    });
    const getUser = vi.fn<AdminUsersApi["getUser"]>(async () => existingUser);
    const updateUser = vi.fn<AdminUsersApi["updateUser"]>(async () => ({ message: "用户更新成功" }));
    const api = usersApi({ getUser, updateUser });
    const callbacks = props(api, { mode: "edit", userId: existingUser.id });
    const user = userEvent.setup();
    render(<Users {...callbacks} />);

    await waitFor(() => expect(screen.getByLabelText("用户名")).toHaveProperty("value", "custom-member"));
    expect(screen.getByLabelText("会员类型")).toHaveProperty("value", "gold");
    await user.clear(screen.getByLabelText("邮箱"));
    await user.type(screen.getByLabelText("邮箱"), "custom-renamed@example.test");
    await user.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(updateUser).toHaveBeenCalledWith(existingUser.id, {
      username: "custom-member",
      email: "custom-renamed@example.test",
      is_superuser: false,
      membership_type: "gold",
      membership_expires_at: "2027-03-04T05:06:07",
      credits: 30,
    }, expect.any(AbortSignal)));
  });

  it("submits a changed expiry as the datetime-local value without converting time zones", async () => {
    const existingUser = userRecord({
      id: "expiry-user",
      membership_expires_at: "2027-03-04T05:06:07",
    });
    const updateUser = vi.fn<AdminUsersApi["updateUser"]>(async () => ({ message: "用户更新成功" }));
    const api = usersApi({
      getUser: vi.fn<AdminUsersApi["getUser"]>(async () => existingUser),
      updateUser,
    });
    const callbacks = props(api, { mode: "edit", userId: existingUser.id });
    const user = userEvent.setup();
    render(<Users {...callbacks} />);

    await waitFor(() => expect(screen.getByLabelText("用户名")).toHaveProperty("value", "writer"));
    fireEvent.change(screen.getByLabelText("会员到期时间（留空=永久有效）"), {
      target: { value: "2027-04-05T06:07" },
    });
    await user.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(updateUser).toHaveBeenCalledWith(existingUser.id, expect.objectContaining({
      membership_expires_at: "2027-04-05T06:07",
    }), expect.any(AbortSignal)));
  });

  it("allows an explicit zero credit balance", async () => {
    const existingUser = userRecord({ id: "zero-user", credits: 30 });
    const updateUser = vi.fn<AdminUsersApi["updateUser"]>(async () => ({ message: "用户更新成功" }));
    const api = usersApi({
      getUser: vi.fn<AdminUsersApi["getUser"]>(async () => existingUser),
      updateUser,
    });
    const callbacks = props(api, { mode: "edit", userId: existingUser.id });
    const user = userEvent.setup();
    render(<Users {...callbacks} />);

    await waitFor(() => expect(screen.getByLabelText("积分")).toHaveProperty("value", "30"));
    await user.clear(screen.getByLabelText("积分"));
    await user.type(screen.getByLabelText("积分"), "0");
    await user.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(updateUser).toHaveBeenCalledWith(existingUser.id, expect.objectContaining({ credits: 0 }), expect.any(AbortSignal)));
  });

  it("does not let a late save response from one edit identity affect the next", async () => {
    const pending = deferred<{ message: string }>();
    const getUser = vi.fn<AdminUsersApi["getUser"]>(async (userId) => userRecord({ id: userId, username: userId }));
    const updateUser = vi.fn<AdminUsersApi["updateUser"]>(() => pending.promise);
    const onSaved = vi.fn();
    const api = usersApi({ getUser, updateUser });
    const callbacks = props(api, { mode: "edit", userId: "user-A", onSaved });
    const user = userEvent.setup();
    const view = render(<Users {...callbacks} />);

    await waitFor(() => expect(screen.getByLabelText("用户名")).toHaveProperty("value", "user-A"));
    await user.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(updateUser).toHaveBeenCalledWith("user-A", expect.any(Object), expect.any(AbortSignal)));

    view.rerender(<Users {...callbacks} userId="user-B" />);
    await waitFor(() => expect(screen.getByLabelText("用户名")).toHaveProperty("value", "user-B"));
    const pendingSaveButton = screen.queryByRole("button", { name: "保存中…" });
    const saveEnabledBeforeOldResponse = pendingSaveButton === null || !(pendingSaveButton as HTMLButtonElement).disabled;
    const saveButton = screen.queryByRole("button", { name: "保存" });
    const saveEnabledAfterLoad = saveButton !== null && !(saveButton as HTMLButtonElement).disabled;
    pending.resolve({ message: "用户更新成功" });
    await act(async () => { await pending.promise; });

    expect({ saveEnabledBeforeOldResponse, saveEnabledAfterLoad, savedCalls: onSaved.mock.calls.length }).toEqual({
      saveEnabledBeforeOldResponse: true,
      saveEnabledAfterLoad: true,
      savedCalls: 0,
    });
  });

  it.each([401, 403])("ignores a late HTTP %i from a save for a previous user", async (status) => {
    const pending = deferred<{ message: string }>();
    const getUser = vi.fn<AdminUsersApi["getUser"]>(async (userId) => userRecord({ id: userId, username: userId }));
    const updateUser = vi.fn<AdminUsersApi["updateUser"]>(() => pending.promise);
    const onAuthenticationError = vi.fn();
    const api = usersApi({ getUser, updateUser });
    const callbacks = props(api, { mode: "edit", userId: "user-A", onAuthenticationError });
    const user = userEvent.setup();
    const view = render(<Users {...callbacks} />);

    await waitFor(() => expect(screen.getByLabelText("用户名")).toHaveProperty("value", "user-A"));
    await user.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(updateUser).toHaveBeenCalled());
    view.rerender(<Users {...callbacks} userId="user-B" />);
    await waitFor(() => expect(screen.getByLabelText("用户名")).toHaveProperty("value", "user-B"));
    await act(async () => {
      pending.reject(new ApiError("http", "expired", status, "expired"));
      await pending.promise.catch(() => undefined);
    });

    expect(onAuthenticationError).not.toHaveBeenCalled();
  });

  it.each([401, 403])("routes HTTP %i list failures through the authentication callback", async (status) => {
    const listUsers = vi.fn<AdminUsersApi["listUsers"]>(async () => {
      throw new ApiError("http", "无权访问", status, "无权访问");
    });
    const onAuthenticationError = vi.fn();
    render(<Users {...props(usersApi({ listUsers }), { onAuthenticationError })} />);

    await waitFor(() => expect(onAuthenticationError).toHaveBeenCalledTimes(1));
    expect(screen.queryByText("无权访问")).toBeNull();
  });

  it("ignores an edit response that arrives after the view is unmounted", async () => {
    let resolveUser: ((user: AdminUserRecord) => void) | undefined;
    const getUser = vi.fn<AdminUsersApi["getUser"]>((_userId, _signal) => new Promise((resolve) => {
      resolveUser = resolve;
    }));
    const callbacks = props(usersApi({ getUser }), { mode: "edit", userId: "late-user" });
    const view = render(<Users {...callbacks} />);

    expect(screen.getByRole("status").textContent).toContain("正在读取用户资料");
    view.unmount();
    expect(getUser.mock.calls[0]?.[1]?.aborted).toBe(true);
    await act(async () => {
      resolveUser?.(userRecord({ id: "late-user" }));
      await Promise.resolve();
    });
    expect(callbacks.onAuthenticationError).not.toHaveBeenCalled();
  });
});
