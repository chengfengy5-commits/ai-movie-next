import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { User } from "../../shared/api/contracts";
import { ApiError } from "../../shared/api/errors";
import type { AccountProfileReceipt, AccountProfileServicePort } from "../../shared/api/accountProfile";
import type { AccountSecurityServicePort } from "../../shared/api/accountSecurity";
import type { WorkspaceServices } from "../../shared/api/services";
import { AccountPage, type AccountPageProps } from "./AccountPage";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const baseUser: User = {
  id: "account-user-1",
  username: "账户用户",
  email: "creator@example.test",
  is_superuser: false,
  membership_type: "premium",
  membership_expires_at: "2999-03-04T12:30:00+05:30",
  avatar_url: "https://q1.qlogo.cn/g?b=qq&nk=123&s=100",
  bio: "原始简介",
  created_at: "2026-10-01T09:10:00",
};

function workspaceServices(mode: "api" | "demo" = "api"): WorkspaceServices {
  return { mode, apiBaseUrl: mode === "api" ? "http://127.0.0.1:8000/api" : null } as WorkspaceServices;
}

function profileService(credits = 42): AccountProfileServicePort {
  return {
    getCredits: vi.fn(async () => ({ credits })),
    updateProfile: vi.fn(async (input): Promise<AccountProfileReceipt> => ({
      id: baseUser.id,
      username: baseUser.username,
      avatar_url: baseUser.avatar_url,
      bio: input.bio,
    })),
  };
}

function renderAccount(options: {
  user?: User;
  profile?: AccountProfileServicePort | null;
  services?: WorkspaceServices;
  refreshCurrentUser?: AccountPageProps["refreshCurrentUser"];
  onUnauthorized?: () => void;
  onDirtyChange?: (dirty: boolean) => void;
  sessionGeneration?: number | string;
} = {}) {
  const user = options.user ?? baseUser;
  const profile = options.profile === undefined ? profileService() : options.profile;
  const services = options.services ?? workspaceServices();
  const refreshCurrentUser = options.refreshCurrentUser ?? vi.fn(async () => user);
  const onUnauthorized = options.onUnauthorized ?? vi.fn();
  const onLogout = vi.fn();
  const onBack = vi.fn();
  const onDirtyChange = options.onDirtyChange ?? vi.fn();
  const props: AccountPageProps = {
    user,
    services,
    profile,
    refreshCurrentUser,
    sessionGeneration: options.sessionGeneration ?? 1,
    onUnauthorized,
    onLogout,
    onBack,
    onDirtyChange,
  };
  const view = render(<AccountPage {...props} />);
  return { ...view, props, user, profile, onUnauthorized, onLogout, onBack, onDirtyChange, refreshCurrentUser };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((accept, fail) => {
    resolve = accept;
    reject = fail;
  });
  return { promise, resolve, reject };
}

describe("AccountPage", () => {
  it.each(["closed", "open"] as const)(
    "locks the bio editor when a password PUT is in flight with the editor %s",
    async (editorState) => {
      const user = userEvent.setup();
      const pendingPassword = deferred<{ message: string }>();
      const profile = profileService();
      const accountSecurity: AccountSecurityServicePort = {
        listSessions: vi.fn(async () => []),
        revokeSession: vi.fn(async (sessionId) => ({ message: "设备已下线", session_id: sessionId })),
        changePassword: vi.fn(() => pendingPassword.promise),
      };
      const services = { ...workspaceServices(), accountSecurity } as WorkspaceServices;
      const { onLogout } = renderAccount({ services, profile });

      if (editorState === "open") await user.click(screen.getByRole("button", { name: "编辑简介" }));
      await user.type(screen.getByLabelText("当前密码"), "current-secret");
      await user.type(screen.getByLabelText("新密码"), "new-secret-123");
      await user.type(screen.getByLabelText("确认新密码"), "new-secret-123");
      await user.click(screen.getByRole("button", { name: "修改密码" }));
      await user.click(await screen.findByRole("button", { name: "确认修改密码" }));
      await waitFor(() => expect(accountSecurity.changePassword).toHaveBeenCalledTimes(1));

      if (editorState === "open") {
        const bio = screen.getByRole("textbox", { name: "个人简介" });
        expect(bio).toHaveAttribute("readonly");
        await user.type(bio, "新简介B");
        expect(bio).toHaveValue(baseUser.bio);
        expect(screen.getByRole("button", { name: "保存简介" })).toBeDisabled();
      } else {
        expect(screen.getByRole("button", { name: "编辑简介" })).toBeDisabled();
      }
      expect(profile.updateProfile).not.toHaveBeenCalled();

      await act(async () => {
        pendingPassword.resolve({ message: "密码修改成功，请重新登录" });
        await pendingPassword.promise;
      });
      expect(onLogout).toHaveBeenCalledTimes(1);
      expect(profile.updateProfile).not.toHaveBeenCalled();
    },
  );

  it("keeps the bio lock after an unknown password outcome and a successful read-only /me check", async () => {
    const user = userEvent.setup();
    const accountSecurity: AccountSecurityServicePort = {
      listSessions: vi.fn(async () => []),
      revokeSession: vi.fn(async (sessionId) => ({ message: "设备已下线", session_id: sessionId })),
      changePassword: vi.fn(async () => { throw new ApiError("timeout", "unknown"); }),
    };
    const services = { ...workspaceServices(), accountSecurity } as WorkspaceServices;
    const { onLogout } = renderAccount({ services });

    await user.type(screen.getByLabelText("当前密码"), "current-secret");
    await user.type(screen.getByLabelText("新密码"), "new-secret-123");
    await user.type(screen.getByLabelText("确认新密码"), "new-secret-123");
    await user.click(screen.getByRole("button", { name: "修改密码" }));
    await user.click(await screen.findByRole("button", { name: "确认修改密码" }));
    expect(await screen.findByRole("button", { name: "只读检查当前登录" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "只读检查当前登录" }));
    expect(await screen.findByText(/当前登录检查通过，但改密结果仍未确定/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "编辑简介" })).toBeDisabled();
    expect(onLogout).not.toHaveBeenCalled();
    expect(accountSecurity.changePassword).toHaveBeenCalledTimes(1);
  });

  it.each([400, 403, 422])("releases only the active bio lock after definite password HTTP %i", async (status) => {
    const user = userEvent.setup();
    const accountSecurity: AccountSecurityServicePort = {
      listSessions: vi.fn(async () => []),
      revokeSession: vi.fn(async (sessionId) => ({ message: "设备已下线", session_id: sessionId })),
      changePassword: vi.fn(async () => { throw new ApiError("http", "rejected", status); }),
    };
    const services = { ...workspaceServices(), accountSecurity } as WorkspaceServices;
    renderAccount({ services });

    await user.type(screen.getByLabelText("当前密码"), "current-secret");
    await user.type(screen.getByLabelText("新密码"), "new-secret-123");
    await user.type(screen.getByLabelText("确认新密码"), "new-secret-123");
    await user.click(screen.getByRole("button", { name: "修改密码" }));
    await user.click(await screen.findByRole("button", { name: "确认修改密码" }));
    await screen.findByText(status === 403
      ? "当前账号无权修改密码，登录状态保持不变。"
      : "密码未被修改，请检查当前密码和新密码后重试。");

    expect(screen.getByRole("button", { name: "编辑简介" })).toBeEnabled();
  });

  it("does not let an old password finally release the new session's bio lock", async () => {
    const user = userEvent.setup();
    const oldPassword = deferred<{ message: string }>();
    const newPassword = deferred<{ message: string }>();
    const accountSecurity: AccountSecurityServicePort = {
      listSessions: vi.fn(async () => []),
      revokeSession: vi.fn(async (sessionId) => ({ message: "设备已下线", session_id: sessionId })),
      changePassword: vi.fn()
        .mockReturnValueOnce(oldPassword.promise)
        .mockReturnValueOnce(newPassword.promise),
    };
    const services = { ...workspaceServices(), accountSecurity } as WorkspaceServices;
    const profile = profileService();
    const onLogout = vi.fn();
    const props: AccountPageProps = {
      user: baseUser,
      services,
      profile,
      refreshCurrentUser: vi.fn(async () => baseUser),
      sessionGeneration: 1,
      onUnauthorized: vi.fn(),
      onLogout,
      onBack: vi.fn(),
    };
    const view = render(<AccountPage {...props} />);

    await user.type(screen.getByLabelText("当前密码"), "old-current");
    await user.type(screen.getByLabelText("新密码"), "old-new-123");
    await user.type(screen.getByLabelText("确认新密码"), "old-new-123");
    await user.click(screen.getByRole("button", { name: "修改密码" }));
    await user.click(await screen.findByRole("button", { name: "确认修改密码" }));
    await waitFor(() => expect(accountSecurity.changePassword).toHaveBeenCalledTimes(1));

    view.rerender(<AccountPage {...props} sessionGeneration={2} />);
    await screen.findByRole("button", { name: "编辑简介" });
    await user.type(screen.getByLabelText("当前密码"), "new-current");
    await user.type(screen.getByLabelText("新密码"), "new-session-123");
    await user.type(screen.getByLabelText("确认新密码"), "new-session-123");
    await user.click(screen.getByRole("button", { name: "修改密码" }));
    await user.click(await screen.findByRole("button", { name: "确认修改密码" }));
    await waitFor(() => expect(accountSecurity.changePassword).toHaveBeenCalledTimes(2));

    await act(async () => {
      oldPassword.resolve({ message: "密码修改成功，请重新登录" });
      await oldPassword.promise;
    });
    expect(onLogout).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "编辑简介" })).toBeDisabled();

    await act(async () => {
      newPassword.reject(new ApiError("http", "rejected", 403));
      await newPassword.promise.catch(() => undefined);
    });
    await waitFor(() => expect(screen.getByRole("button", { name: "编辑简介" })).toBeEnabled());
  });

  it("requires explicit save verification before allowing a password-change confirmation", async () => {
    const user = userEvent.setup();
    let currentUser = baseUser;
    const profile = profileService();
    vi.mocked(profile.updateProfile).mockImplementation(async (input) => {
      currentUser = { ...currentUser, bio: input.bio };
      return {
        id: currentUser.id,
        username: currentUser.username,
        avatar_url: currentUser.avatar_url,
        bio: currentUser.bio,
      };
    });
    const changePassword = vi.fn(async () => ({ message: "密码修改成功，请重新登录" }));
    const services = {
      ...workspaceServices(),
      accountSecurity: {
        listSessions: vi.fn(async () => []),
        revokeSession: vi.fn(async (sessionId: string) => ({ message: "设备已下线", session_id: sessionId })),
        changePassword,
      },
    } as WorkspaceServices;
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderAccount({
      services,
      profile,
      refreshCurrentUser: vi.fn(async () => currentUser),
    });

    await user.type(screen.getByLabelText("当前密码"), "current-secret");
    await user.type(screen.getByLabelText("新密码"), "new-secret-123");
    await user.type(screen.getByLabelText("确认新密码"), "new-secret-123");
    await user.click(screen.getByRole("button", { name: "编辑简介" }));
    const bio = screen.getByRole("textbox", { name: "个人简介" });
    await user.clear(bio);
    await user.type(bio, "已保存并核对的简介");

    await user.click(screen.getByRole("button", { name: "修改密码" }));

    expect(await screen.findByRole("alertdialog", { name: "确认修改密码？" })).toBeInTheDocument();
    expect(profile.updateProfile).toHaveBeenCalledTimes(1);
    expect(currentUser.bio).toBe("已保存并核对的简介");
    expect(changePassword).not.toHaveBeenCalled();
  });

  it("keeps profile changes on cancel and requires an explicit discard choice", async () => {
    const user = userEvent.setup();
    const profile = profileService();
    const services = {
      ...workspaceServices(),
      accountSecurity: {
        listSessions: vi.fn(async () => []),
        revokeSession: vi.fn(async (sessionId: string) => ({ message: "设备已下线", session_id: sessionId })),
        changePassword: vi.fn(async () => ({ message: "密码修改成功，请重新登录" })),
      },
    } as WorkspaceServices;
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderAccount({ services, profile });

    await user.type(screen.getByLabelText("当前密码"), "current-secret");
    await user.type(screen.getByLabelText("新密码"), "new-secret-123");
    await user.type(screen.getByLabelText("确认新密码"), "new-secret-123");
    await user.click(screen.getByRole("button", { name: "编辑简介" }));
    const bio = screen.getByRole("textbox", { name: "个人简介" });
    await user.clear(bio);
    await user.type(bio, "保留草稿");

    await user.click(screen.getByRole("button", { name: "修改密码" }));
    expect(confirm).toHaveBeenNthCalledWith(1, "个人简介有未保存的修改。确定先保存并核对后再修改密码吗？");
    expect(confirm).toHaveBeenNthCalledWith(2, "明确放弃未保存的个人简介草稿并继续修改密码？");
    expect(profile.updateProfile).not.toHaveBeenCalled();
    expect(screen.getByRole("textbox", { name: "个人简介" })).toHaveValue("保留草稿");
    expect(screen.getByRole("button", { name: "保存简介" })).toBeEnabled();
    expect(screen.queryByRole("alertdialog", { name: "确认修改密码？" })).not.toBeInTheDocument();

    confirm.mockReset().mockReturnValueOnce(false).mockReturnValueOnce(true);
    await user.click(screen.getByRole("button", { name: "修改密码" }));
    expect(await screen.findByRole("alertdialog", { name: "确认修改密码？" })).toBeInTheDocument();
    expect(profile.updateProfile).not.toHaveBeenCalled();
    expect(screen.queryByRole("textbox", { name: "个人简介" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "编辑简介" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.getByRole("button", { name: "编辑简介" })).toBeEnabled();
  });

  it("keeps profile and security dirty state combined when either child becomes clean", async () => {
    const user = userEvent.setup();
    const onDirtyChange = vi.fn();
    const accountSecurity: AccountSecurityServicePort = {
      listSessions: vi.fn(async () => []),
      revokeSession: vi.fn(async (sessionId) => ({ message: "设备已下线", session_id: sessionId })),
      changePassword: vi.fn(async () => ({ message: "密码修改成功，请重新登录" })),
    };
    const services = { ...workspaceServices(), accountSecurity } as WorkspaceServices;
    renderAccount({ services, onDirtyChange });

    await screen.findByRole("heading", { name: "账户安全" });
    await user.click(screen.getByRole("button", { name: "编辑简介" }));
    const bio = screen.getByRole("textbox", { name: "个人简介" });
    await user.clear(bio);
    await user.type(bio, "修改后的简介");
    await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(true));

    await user.type(screen.getByLabelText("当前密码"), "old-secret");
    await user.type(screen.getByLabelText("新密码"), "new-secret-123");
    await user.type(screen.getByLabelText("确认新密码"), "new-secret-123");

    await user.clear(bio);
    await user.type(bio, baseUser.bio ?? "");
    await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(true));

    await user.clear(screen.getByLabelText("当前密码"));
    await user.clear(screen.getByLabelText("新密码"));
    await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(true));
    await user.clear(screen.getByLabelText("确认新密码"));
    await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(false));
  });

  it("loads profile and credits independently, keeps profile facts on balance failure, and retries credits", async () => {
    const accountUser = { ...baseUser, bio: null };
    const profile = profileService(0);
    vi.mocked(profile.getCredits)
      .mockRejectedValueOnce(new ApiError("network", "offline"))
      .mockResolvedValueOnce({ credits: -3 });
    const { onUnauthorized } = renderAccount({ user: accountUser, profile });

    expect(await screen.findByText("会员 · 有效至 2999-03-04 12:30:00+05:30")).toBeInTheDocument();
    expect(screen.getByText("尚未填写简介。" )).toBeInTheDocument();
    expect(await screen.findByText(/积分读取暂时无法连接服务/)).toBeInTheDocument();
    expect(screen.getByText("账户用户")).toBeInTheDocument();
    expect(onUnauthorized).not.toHaveBeenCalled();

    const interaction = userEvent.setup();
    await interaction.click(screen.getByRole("button", { name: "重试读取积分" }));
    expect(await screen.findByText("-3")).toBeInTheDocument();
    expect(profile.getCredits).toHaveBeenCalledTimes(2);
    expect(screen.getByText("账户用户")).toBeInTheDocument();
  });

  it("preserves a null bio as a no-op and does not send a profile PUT", async () => {
    const user = userEvent.setup();
    const profile = profileService();
    renderAccount({ user: { ...baseUser, bio: null }, profile });

    await screen.findByRole("button", { name: "刷新账户资料" });
    await user.click(screen.getByRole("button", { name: "编辑简介" }));
    const textbox = screen.getByRole("textbox", { name: "个人简介" });
    expect(textbox).toHaveValue("");
    await user.click(screen.getByRole("button", { name: "保存简介" }));

    expect(profile.updateProfile).not.toHaveBeenCalled();
    expect(screen.queryByRole("textbox", { name: "个人简介" })).not.toBeInTheDocument();
  });

  it("sends an empty string to clear a non-empty bio and confirms it with an independent full-user read", async () => {
    const user = userEvent.setup();
    const updatedUser = { ...baseUser, bio: null };
    const profile = profileService();
    const refreshCurrentUser = vi.fn()
      .mockResolvedValueOnce(baseUser)
      .mockResolvedValueOnce(updatedUser);
    renderAccount({ profile, refreshCurrentUser });

    await screen.findByText(baseUser.username);
    await user.click(screen.getByRole("button", { name: "编辑简介" }));
    await user.clear(screen.getByRole("textbox", { name: "个人简介" }));
    await user.click(screen.getByRole("button", { name: "保存简介" }));

    expect(profile.updateProfile).toHaveBeenCalledWith({ bio: "" }, expect.any(AbortSignal));
    expect(await screen.findByText("简介已由独立账户读取确认。")).toBeInTheDocument();
    expect(screen.getByText("尚未填写简介。")).toBeInTheDocument();
    expect(refreshCurrentUser).toHaveBeenCalledTimes(2);
  });

  it("keeps an unknown write frozen after a baseline read and only clears it after a target readback", async () => {
    const user = userEvent.setup();
    const profile = profileService();
    vi.mocked(profile.updateProfile).mockRejectedValueOnce(new ApiError("timeout", "unknown"));
    const updatedUser = { ...baseUser, bio: "新简介" };
    const refreshCurrentUser = vi.fn()
      .mockResolvedValueOnce(baseUser)
      .mockResolvedValueOnce(baseUser)
      .mockResolvedValueOnce(updatedUser);
    renderAccount({ profile, refreshCurrentUser });

    await screen.findByText(baseUser.username);
    await user.click(screen.getByRole("button", { name: "编辑简介" }));
    await user.clear(screen.getByRole("textbox", { name: "个人简介" }));
    await user.type(screen.getByRole("textbox", { name: "个人简介" }), "新简介");
    await user.click(screen.getByRole("button", { name: "保存简介" }));

    expect(await screen.findByText(/保存结果仍不明确/)).toBeInTheDocument();
    expect(profile.updateProfile).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("textbox", { name: "个人简介" })).toHaveValue("新简介");
    expect(screen.getByRole("textbox", { name: "个人简介" })).toHaveAttribute("readonly");

    await user.click(screen.getByRole("button", { name: "只核对服务器状态" }));
    expect(await screen.findByText("简介已由独立账户读取确认。")).toBeInTheDocument();
    expect(profile.updateProfile).toHaveBeenCalledTimes(1);
    expect(refreshCurrentUser).toHaveBeenCalledTimes(3);
  });

  it.each(["target", "different", "failure"] as const)(
    "does not let a late %s verification change a replacement draft",
    async (outcome) => {
      const user = userEvent.setup();
      const profile = profileService();
      const oldVerification = deferred<User>();
      const onDirtyChange = vi.fn();
      vi.mocked(profile.updateProfile).mockRejectedValueOnce(new ApiError("timeout", "unknown"));
      const refreshCurrentUser = vi.fn()
        .mockResolvedValueOnce(baseUser)
        .mockResolvedValueOnce(baseUser)
        .mockReturnValueOnce(oldVerification.promise);
      vi.spyOn(window, "confirm").mockReturnValue(true);
      renderAccount({ profile, refreshCurrentUser, onDirtyChange });

      await screen.findByText(baseUser.username);
      await user.click(screen.getByRole("button", { name: "编辑简介" }));
      await user.clear(screen.getByRole("textbox", { name: "个人简介" }));
      await user.type(screen.getByRole("textbox", { name: "个人简介" }), "旧目标A");
      await user.click(screen.getByRole("button", { name: "保存简介" }));
      expect(await screen.findByText(/保存结果仍不明确/)).toBeInTheDocument();

      await user.click(screen.getByRole("button", { name: "只核对服务器状态" }));
      expect(await screen.findByText("正在独立读取账户资料核对本次保存。"))
        .toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "放弃本次核对并退出编辑" }));
      await waitFor(() => expect(screen.queryByRole("textbox", { name: "个人简介" })).not.toBeInTheDocument());

      await user.click(screen.getByRole("button", { name: "编辑简介" }));
      await user.clear(screen.getByRole("textbox", { name: "个人简介" }));
      await user.type(screen.getByRole("textbox", { name: "个人简介" }), "新草稿B");
      await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(true));

      await act(async () => {
        if (outcome === "failure") {
          oldVerification.reject(new ApiError("network", "offline"));
          await oldVerification.promise.catch(() => undefined);
        } else {
          oldVerification.resolve({
            ...baseUser,
            bio: outcome === "target" ? "旧目标A" : "服务器其他简介",
          });
          await oldVerification.promise;
        }
      });

      const replacementDraft = await screen.findByRole("textbox", { name: "个人简介" });
      expect(replacementDraft).toHaveValue("新草稿B");
      expect(replacementDraft).not.toHaveAttribute("readonly");
      expect(screen.queryByRole("button", { name: "只核对服务器状态" })).not.toBeInTheDocument();
      expect(profile.updateProfile).toHaveBeenCalledTimes(1);
      expect(onDirtyChange).toHaveBeenLastCalledWith(true);
    },
  );

  it.each(["target", "different", "failure"] as const)(
    "offers a real profile retry after retiring delayed initial and verification reads with late %s results",
    async (outcome) => {
      const user = userEvent.setup();
      const profile = profileService();
      const initialRead = deferred<User>();
      const oldVerification = deferred<User>();
      const freshUser = { ...baseUser, bio: "重试后的服务器简介" };
      const refreshCurrentUser = vi.fn()
        .mockReturnValueOnce(initialRead.promise)
        .mockReturnValueOnce(oldVerification.promise)
        .mockResolvedValueOnce(freshUser);
      vi.spyOn(window, "confirm").mockReturnValue(true);
      renderAccount({ profile, refreshCurrentUser });

      await user.click(screen.getByRole("button", { name: "编辑简介" }));
      await user.clear(screen.getByRole("textbox", { name: "个人简介" }));
      await user.type(screen.getByRole("textbox", { name: "个人简介" }), "旧目标A");
      await user.click(screen.getByRole("button", { name: "保存简介" }));
      expect(await screen.findByText("正在独立读取账户资料核对本次保存。"))
        .toBeInTheDocument();
      expect(profile.updateProfile).toHaveBeenCalledTimes(1);

      await user.click(screen.getByRole("button", { name: "放弃本次核对并退出编辑" }));
      await waitFor(() => expect(screen.queryByRole("textbox", { name: "个人简介" })).not.toBeInTheDocument());

      const retryButton = await screen.findByRole("button", { name: "重试读取资料" });
      expect(retryButton).toBeEnabled();
      await user.click(retryButton);
      expect(await screen.findByRole("button", { name: "刷新账户资料" })).toBeInTheDocument();
      expect(refreshCurrentUser).toHaveBeenCalledTimes(3);

      await user.click(screen.getByRole("button", { name: "编辑简介" }));
      const replacementDraft = screen.getByRole("textbox", { name: "个人简介" });
      await user.clear(replacementDraft);
      await user.type(replacementDraft, "新草稿B");
      expect(screen.getByRole("button", { name: "保存简介" })).toBeEnabled();

      await act(async () => {
        if (outcome === "failure") {
          initialRead.reject(new ApiError("network", "offline"));
          oldVerification.reject(new ApiError("network", "offline"));
        } else {
          const lateUser = {
            ...baseUser,
            bio: outcome === "target" ? "旧目标A" : "另一个服务器简介",
          };
          initialRead.resolve(lateUser);
          oldVerification.resolve(lateUser);
        }
        await Promise.allSettled([initialRead.promise, oldVerification.promise]);
      });

      expect(screen.getByRole("textbox", { name: "个人简介" })).toHaveValue("新草稿B");
      expect(screen.getByRole("textbox", { name: "个人简介" })).not.toHaveAttribute("readonly");
      expect(screen.getByRole("button", { name: "保存简介" })).toBeEnabled();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(profile.updateProfile).toHaveBeenCalledTimes(1);
    },
  );

  it("does not let the error retry replace an in-flight write verification", async () => {
    const user = userEvent.setup();
    const profile = profileService();
    const verification = deferred<User>();
    const refreshCurrentUser = vi.fn()
      .mockRejectedValueOnce(new ApiError("network", "offline"))
      .mockReturnValueOnce(verification.promise);
    renderAccount({ profile, refreshCurrentUser });

    const retryButton = await screen.findByRole("button", { name: "重试读取资料" });
    await user.click(screen.getByRole("button", { name: "编辑简介" }));
    await user.clear(screen.getByRole("textbox", { name: "个人简介" }));
    await user.type(screen.getByRole("textbox", { name: "个人简介" }), "新简介");
    await user.click(screen.getByRole("button", { name: "保存简介" }));
    expect(await screen.findByText("正在独立读取账户资料核对本次保存。"))
      .toBeInTheDocument();

    expect(retryButton).toBeDisabled();
    await user.click(retryButton);
    expect(refreshCurrentUser).toHaveBeenCalledTimes(2);
    expect(refreshCurrentUser.mock.calls[1]?.[0].aborted).toBe(false);

    await act(async () => {
      verification.resolve({ ...baseUser, bio: "新简介" });
      await verification.promise;
    });
    expect(await screen.findByText("简介已由独立账户读取确认。")).toBeInTheDocument();
    expect(profile.updateProfile).toHaveBeenCalledTimes(1);
    expect(refreshCurrentUser).toHaveBeenCalledTimes(2);
  });

  it.each([
    { writeOutcome: "success", readOutcome: "target" },
    { writeOutcome: "success", readOutcome: "different" },
    { writeOutcome: "success", readOutcome: "failure" },
    { writeOutcome: "unknown", readOutcome: "target" },
    { writeOutcome: "unknown", readOutcome: "different" },
    { writeOutcome: "unknown", readOutcome: "failure" },
  ] as const)(
    "releases only the retired editor after a $writeOutcome PUT and $readOutcome first verification",
    async ({ writeOutcome, readOutcome }) => {
      const user = userEvent.setup();
      const profile = profileService();
      const firstVerification = deferred<User>();
      const onDirtyChange = vi.fn();
      if (writeOutcome === "unknown") {
        vi.mocked(profile.updateProfile).mockRejectedValueOnce(new ApiError("timeout", "unknown"));
      }
      const refreshCurrentUser = vi.fn()
        .mockResolvedValueOnce(baseUser)
        .mockReturnValueOnce(firstVerification.promise);
      vi.spyOn(window, "confirm").mockReturnValue(true);
      renderAccount({ profile, refreshCurrentUser, onDirtyChange });

      await screen.findByText(baseUser.username);
      await user.click(screen.getByRole("button", { name: "编辑简介" }));
      await user.clear(screen.getByRole("textbox", { name: "个人简介" }));
      await user.type(screen.getByRole("textbox", { name: "个人简介" }), "旧目标A");
      await user.click(screen.getByRole("button", { name: "保存简介" }));
      expect(await screen.findByText("正在独立读取账户资料核对本次保存。"))
        .toBeInTheDocument();
      expect(profile.updateProfile).toHaveBeenCalledTimes(1);

      await user.click(screen.getByRole("button", { name: "放弃本次核对并退出编辑" }));
      await waitFor(() => expect(screen.queryByRole("textbox", { name: "个人简介" })).not.toBeInTheDocument());

      await user.click(screen.getByRole("button", { name: "编辑简介" }));
      const replacementDraft = screen.getByRole("textbox", { name: "个人简介" });
      expect(replacementDraft).not.toHaveAttribute("readonly");
      await user.clear(replacementDraft);
      await user.type(replacementDraft, "新草稿B");
      expect(screen.getByRole("button", { name: "保存简介" })).toBeEnabled();
      await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(true));

      await act(async () => {
        if (readOutcome === "failure") {
          firstVerification.reject(new ApiError("network", "offline"));
          await firstVerification.promise.catch(() => undefined);
        } else {
          firstVerification.resolve({
            ...baseUser,
            bio: readOutcome === "target" ? "旧目标A" : "服务器其他简介",
          });
          await firstVerification.promise;
        }
      });

      expect(screen.getByRole("textbox", { name: "个人简介" })).toHaveValue("新草稿B");
      expect(screen.getByRole("textbox", { name: "个人简介" })).not.toHaveAttribute("readonly");
      expect(screen.getByRole("button", { name: "保存简介" })).toBeEnabled();
      expect(screen.queryByRole("button", { name: "只核对服务器状态" })).not.toBeInTheDocument();
      expect(screen.queryByText(/保存结果仍未确认/)).not.toBeInTheDocument();
      expect(profile.updateProfile).toHaveBeenCalledTimes(1);
      expect(onDirtyChange).toHaveBeenLastCalledWith(true);
    },
  );

  it("does not let retired A verification unlock B while B's own PUT is pending", async () => {
    const user = userEvent.setup();
    const profile = profileService();
    const oldVerification = deferred<User>();
    const newWrite = deferred<AccountProfileReceipt>();
    const refreshCurrentUser = vi.fn()
      .mockResolvedValueOnce(baseUser)
      .mockReturnValueOnce(oldVerification.promise)
      .mockResolvedValueOnce({ ...baseUser, bio: "新草稿B" });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderAccount({ profile, refreshCurrentUser });

    await screen.findByText(baseUser.username);
    await user.click(screen.getByRole("button", { name: "编辑简介" }));
    await user.clear(screen.getByRole("textbox", { name: "个人简介" }));
    await user.type(screen.getByRole("textbox", { name: "个人简介" }), "旧目标A");
    await user.click(screen.getByRole("button", { name: "保存简介" }));
    expect(await screen.findByText("正在独立读取账户资料核对本次保存。"))
      .toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "放弃本次核对并退出编辑" }));
    await waitFor(() => expect(screen.queryByRole("textbox", { name: "个人简介" })).not.toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "编辑简介" }));
    const replacementDraft = screen.getByRole("textbox", { name: "个人简介" });
    await user.clear(replacementDraft);
    await user.type(replacementDraft, "新草稿B");
    vi.mocked(profile.updateProfile).mockImplementationOnce(() => newWrite.promise);
    await user.click(screen.getByRole("button", { name: "保存简介" }));
    await waitFor(() => expect(profile.updateProfile).toHaveBeenCalledTimes(2));

    expect(screen.getByRole("textbox", { name: "个人简介" })).toHaveValue("新草稿B");
    expect(screen.getByRole("textbox", { name: "个人简介" })).toHaveAttribute("readonly");
    expect(screen.getByRole("button", { name: "正在保存…" })).toBeDisabled();

    await act(async () => {
      oldVerification.resolve({ ...baseUser, bio: "旧目标A" });
      await oldVerification.promise;
    });

    expect(screen.getByRole("textbox", { name: "个人简介" })).toHaveValue("新草稿B");
    expect(screen.getByRole("textbox", { name: "个人简介" })).toHaveAttribute("readonly");
    expect(screen.getByRole("button", { name: "正在保存…" })).toBeDisabled();

    await act(async () => {
      newWrite.resolve({
        id: baseUser.id,
        username: baseUser.username,
        avatar_url: baseUser.avatar_url,
        bio: "新草稿B",
      });
      await newWrite.promise;
    });
    expect(await screen.findByText("简介已由独立账户读取确认。")).toBeInTheDocument();
    expect(profile.updateProfile).toHaveBeenCalledTimes(2);
  });

  it("preserves a dirty draft across profile refresh and keeps a membership 403 in-session", async () => {
    const user = userEvent.setup();
    const profile = profileService();
    vi.mocked(profile.updateProfile).mockRejectedValueOnce(
      new ApiError("http", "membership denied", 403, "会员资格暂不可用"),
    );
    const refreshCurrentUser = vi.fn()
      .mockResolvedValueOnce(baseUser)
      .mockResolvedValueOnce({ ...baseUser, bio: "服务端新简介" });
    const onUnauthorized = vi.fn();
    renderAccount({ profile, refreshCurrentUser, onUnauthorized });

    await screen.findByRole("button", { name: "刷新账户资料" });
    await user.click(screen.getByRole("button", { name: "编辑简介" }));
    await user.clear(screen.getByRole("textbox", { name: "个人简介" }));
    await user.type(screen.getByRole("textbox", { name: "个人简介" }), "本地草稿");
    await user.click(screen.getByRole("button", { name: "刷新服务器资料" }));
    expect(await screen.findByText("服务器资料已刷新，当前草稿仍保留。", { selector: "p" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "个人简介" })).toHaveValue("本地草稿");

    await user.click(screen.getByRole("button", { name: "保存简介" }));
    expect(await screen.findByText(/被服务端拒绝/)).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "个人简介" })).toHaveValue("本地草稿");
    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(profile.updateProfile).toHaveBeenCalledTimes(1);
  });

  it("ignores a late old-session 401 after the same user opens a new session generation", async () => {
    const oldProfileRead = deferred<User>();
    const oldCreditsRead = deferred<{ credits: number }>();
    const sameUserNewSession = { ...baseUser, bio: "新会话资料" };
    const profile = profileService();
    vi.mocked(profile.getCredits)
      .mockImplementationOnce(() => oldCreditsRead.promise)
      .mockResolvedValueOnce({ credits: 9 });
    const refreshCurrentUser = vi.fn()
      .mockImplementationOnce(() => oldProfileRead.promise)
      .mockResolvedValueOnce(sameUserNewSession);
    const onUnauthorized = vi.fn();
    const services = workspaceServices();
    const props: AccountPageProps = {
      user: baseUser,
      services,
      profile,
      refreshCurrentUser,
      sessionGeneration: 1,
      onUnauthorized,
      onLogout: vi.fn(),
      onBack: vi.fn(),
    };
    const view = render(<AccountPage {...props} />);
    view.rerender(<AccountPage {...props} sessionGeneration={2} />);

    expect(await screen.findByText("新会话资料")).toBeInTheDocument();
    expect(await screen.findByText("9")).toBeInTheDocument();
    oldProfileRead.reject(new ApiError("http", "expired old session", 401));
    oldCreditsRead.resolve({ credits: 44 });
    await waitFor(() => expect(onUnauthorized).not.toHaveBeenCalled());
    expect(screen.getByText("新会话资料")).toBeInTheDocument();
    expect(screen.getByText("9")).toBeInTheDocument();
  });

  it("keeps the account read-only and does not invent a balance when no account port is connected", async () => {
    const refreshCurrentUser = vi.fn(async () => baseUser);
    renderAccount({
      profile: null,
      services: workspaceServices("demo"),
      refreshCurrentUser,
    });

    expect(await screen.findByText("账户资料编辑服务尚未接入，目前仅显示已验证的账户信息。")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "编辑简介" })).not.toBeInTheDocument();
    expect(screen.queryByText(/积分余额/)).toBeInTheDocument();
    expect(screen.queryByText("0", { selector: "strong" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "刷新" })).not.toBeInTheDocument();
  });

  it("shows a safe initial avatar and does not load the external avatar URL", async () => {
    renderAccount();
    await screen.findByText(baseUser.username);

    expect(screen.getByRole("img", { name: "头像首字" })).toHaveTextContent("账");
    expect(document.querySelector("img[src*='qlogo']")).toBeNull();
  });
});
