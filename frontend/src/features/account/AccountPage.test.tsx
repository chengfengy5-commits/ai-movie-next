import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { User } from "../../shared/api/contracts";
import { ApiError } from "../../shared/api/errors";
import type { AccountProfileReceipt, AccountProfileServicePort } from "../../shared/api/accountProfile";
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
  const onBack = vi.fn();
  const onDirtyChange = options.onDirtyChange ?? vi.fn();
  const props: AccountPageProps = {
    user,
    services,
    profile,
    refreshCurrentUser,
    sessionGeneration: options.sessionGeneration ?? 1,
    onUnauthorized,
    onBack,
    onDirtyChange,
  };
  const view = render(<AccountPage {...props} />);
  return { ...view, props, user, profile, onUnauthorized, onBack, onDirtyChange, refreshCurrentUser };
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
