import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { User } from "../../shared/api/contracts";
import { ApiError } from "../../shared/api/errors";
import type { AccountSecurityServicePort, AccountSession } from "../../shared/api/accountSecurity";
import { AccountSecurityPanel, type AccountSecurityPanelProps } from "./AccountSecurityPanel";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const user: User = {
  id: "user-1",
  username: "security-user",
  email: "user@example.test",
  is_superuser: false,
  membership_type: "free",
  membership_expires_at: null,
  avatar_url: null,
  bio: "简介",
  created_at: "2026-10-01T09:00:00Z",
};

const otherSession: AccountSession = {
  id: "session-other",
  deviceName: "手机浏览器",
  ipAddress: "192.0.2.15",
  userAgent: "Mobile browser",
  createdAt: "2026-10-01T09:00:00",
  lastSeenAt: "2026-10-01T10:30:00+08:00",
  revokedAt: null,
  isActive: true,
  isCurrent: false,
};

const currentSession: AccountSession = {
  ...otherSession,
  id: "session-current",
  deviceName: "当前桌面设备",
  isCurrent: true,
};

function createService(): AccountSecurityServicePort {
  return {
    listSessions: vi.fn(async (): Promise<AccountSession[]> => [otherSession, currentSession]),
    revokeSession: vi.fn(async (sessionId: string) => ({ message: "设备已下线", session_id: sessionId })),
    changePassword: vi.fn(async () => ({ message: "密码修改成功，请重新登录" })),
  };
}

function renderPanel(options: {
  user?: User;
  userId?: string;
  service?: AccountSecurityServicePort | null;
  servicesIdentity?: object;
  sessionGeneration?: number | string;
  refreshCurrentUser?: AccountSecurityPanelProps["refreshCurrentUser"];
  onUnauthorized?: () => void;
  onLogout?: () => void;
  onBeforeSecurityMutation?: AccountSecurityPanelProps["onBeforeSecurityMutation"];
  onDirtyChange?: AccountSecurityPanelProps["onDirtyChange"];
} = {}) {
  const currentUser = options.user ?? user;
  const service = Object.prototype.hasOwnProperty.call(options, "service")
    ? options.service ?? null
    : createService();
  const servicesIdentity = options.servicesIdentity ?? {};
  const refreshCurrentUser = options.refreshCurrentUser ?? vi.fn(async () => currentUser);
  const onUnauthorized = options.onUnauthorized ?? vi.fn();
  const onLogout = options.onLogout ?? vi.fn();
  const onBeforeSecurityMutation = options.onBeforeSecurityMutation ?? vi.fn(async () => true);
  const onDirtyChange = options.onDirtyChange ?? vi.fn();
  const props: AccountSecurityPanelProps = {
    userId: options.userId ?? currentUser.id,
    servicesIdentity,
    service,
    sessionGeneration: options.sessionGeneration ?? 1,
    refreshCurrentUser,
    onUnauthorized,
    onLogout,
    onBeforeSecurityMutation,
    onDirtyChange,
  };
  const view = render(<AccountSecurityPanel {...props} />);
  return { ...view, props, service, refreshCurrentUser, onUnauthorized, onLogout, onBeforeSecurityMutation, onDirtyChange };
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

async function openPasswordConfirmation(
  interaction: ReturnType<typeof userEvent.setup>,
  values = { oldPassword: "old pass", newPassword: "123456", confirmedPassword: "123456" },
) {
  await interaction.type(screen.getByLabelText("当前密码"), values.oldPassword);
  await interaction.type(screen.getByLabelText("新密码"), values.newPassword);
  await interaction.type(screen.getByLabelText("确认新密码"), values.confirmedPassword);
  await interaction.click(screen.getByRole("button", { name: "修改密码" }));
  return screen.findByRole("alertdialog", { name: "确认修改密码？" });
}

describe("AccountSecurityPanel", () => {
  it("does not fabricate device data or report successful operations without a service port", async () => {
    const onLogout = vi.fn();
    const { service } = renderPanel({ service: null, onLogout });

    expect(await screen.findByText(/当前环境未接入账户安全服务/)).toBeInTheDocument();
    expect(screen.queryByText("手机浏览器")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("当前密码")).not.toBeInTheDocument();
    expect(service).toBeNull();
    expect(onLogout).not.toHaveBeenCalled();
  });

  it("renders the server order and device facts, keeps the current device disabled, and warns about the cap", async () => {
    const sessions = Array.from({ length: 20 }, (_, index) => ({
      ...otherSession,
      id: `session-${index}`,
      deviceName: `设备 ${index}`,
    }));
    sessions[19] = { ...otherSession, ...currentSession };
    const service = createService();
    vi.mocked(service.listSessions).mockResolvedValueOnce(sessions);
    renderPanel({ service });

    expect(await screen.findByText("设备 0")).toBeInTheDocument();
    expect(screen.getByText(/服务端每次最多返回20条/)).toBeInTheDocument();
    expect(screen.getAllByText("192.0.2.15")).toHaveLength(20);
    const currentButton = screen.getAllByRole("button", { name: "当前设备" }).at(-1);
    expect(currentButton).toBeDisabled();
    expect(service.listSessions).toHaveBeenCalledTimes(1);
  });

  it("does not invent a current device when the capped server list omits it", async () => {
    const service = createService();
    const sessions = Array.from({ length: 20 }, (_, index) => ({
      ...otherSession,
      id: `session-${index}`,
      deviceName: `设备 ${index}`,
    }));
    vi.mocked(service.listSessions).mockResolvedValueOnce(sessions);
    renderPanel({ service });

    expect(await screen.findByText("设备 0")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "撤销设备「设备 0」" })).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "当前设备" })).not.toBeInTheDocument();
    expect(screen.getByText(/最多返回20条记录/)).toBeInTheDocument();
  });

  it("confirms an other-device revoke once and independently reads the list again", async () => {
    const interaction = userEvent.setup();
    const receipt = deferred<{ message: string; session_id: string }>();
    const service = createService();
    vi.mocked(service.revokeSession).mockReturnValueOnce(receipt.promise);
    renderPanel({ service });

    await screen.findByText("手机浏览器");
    await interaction.click(screen.getByRole("button", { name: "撤销设备「手机浏览器」" }));
    const dialog = await screen.findByRole("alertdialog", { name: "确认撤销登录设备？" });
    expect(dialog).toHaveTextContent("此操作不会退出当前设备。");
    const confirm = screen.getByRole("button", { name: "确认撤销设备" });
    await interaction.dblClick(confirm);
    expect(service.revokeSession).toHaveBeenCalledTimes(1);
    expect(service.revokeSession).toHaveBeenCalledWith("session-other", expect.any(AbortSignal));

    await act(async () => receipt.resolve({ message: "设备已下线", session_id: "session-other" }));
    expect(await screen.findByText("设备已下线，设备列表已更新。")).toBeInTheDocument();
    expect(service.listSessions).toHaveBeenCalledTimes(2);
  });

  it("does not claim a 404 revoke succeeded and refreshes the list", async () => {
    const interaction = userEvent.setup();
    const service = createService();
    vi.mocked(service.revokeSession).mockRejectedValueOnce(new ApiError("http", "missing", 404));
    renderPanel({ service });

    await screen.findByText("手机浏览器");
    await interaction.click(screen.getByRole("button", { name: "撤销设备「手机浏览器」" }));
    await interaction.click(screen.getByRole("button", { name: "确认撤销设备" }));

    expect(await screen.findByText(/设备当前不存在或不属于本人；已刷新列表/)).toBeInTheDocument();
    expect(service.revokeSession).toHaveBeenCalledTimes(1);
    expect(service.listSessions).toHaveBeenCalledTimes(2);
  });

  it("freezes an unknown revoke, allows only readback, and does not silently retry DELETE", async () => {
    const interaction = userEvent.setup();
    const service = createService();
    vi.mocked(service.revokeSession).mockRejectedValueOnce(new ApiError("timeout", "timeout"));
    vi.mocked(service.listSessions)
      .mockResolvedValueOnce([otherSession, currentSession])
      .mockResolvedValueOnce([currentSession])
      .mockResolvedValueOnce([currentSession]);
    renderPanel({ service });

    await screen.findByText("手机浏览器");
    await interaction.click(screen.getByRole("button", { name: "撤销设备「手机浏览器」" }));
    await interaction.click(screen.getByRole("button", { name: "确认撤销设备" }));
    expect(await screen.findByText(/撤销结果未确定；设备当前不在有效列表中/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "撤销设备「手机浏览器」" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "修改密码" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "只读核对设备列表" })).toBeEnabled();
    expect(service.revokeSession).toHaveBeenCalledTimes(1);

    await interaction.click(screen.getByRole("button", { name: "只读核对设备列表" }));
    await waitFor(() => expect(service.listSessions).toHaveBeenCalledTimes(3));
    expect(service.revokeSession).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("alert")).toHaveTextContent(/撤销结果未确定/);
    await interaction.click(screen.getByRole("button", { name: "放弃本次撤销结果并允许其他操作" }));
    expect(screen.getByText(/明确放弃此次撤销核对/)).toBeInTheDocument();
  });

  it("routes a current-scope 401 to the existing exit callback without turning failure into an empty list", async () => {
    const service = createService();
    const onUnauthorized = vi.fn();
    vi.mocked(service.listSessions).mockRejectedValueOnce(new ApiError("http", "unauthorized", 401));
    renderPanel({ service, onUnauthorized });

    expect(await screen.findByText("登录状态已失效，请重新登录后再查看登录设备。")).toBeInTheDocument();
    expect(screen.queryByText("当前没有可显示的有效登录设备。")).not.toBeInTheDocument();
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it("keeps the current login on a sessions 403", async () => {
    const service = createService();
    const onUnauthorized = vi.fn();
    vi.mocked(service.listSessions).mockRejectedValueOnce(new ApiError("http", "forbidden", 403));
    renderPanel({ service, onUnauthorized });

    expect(await screen.findByText("当前账号无权读取登录设备，登录状态保持不变。")).toBeInTheDocument();
    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(screen.queryByText("当前没有可显示的有效登录设备。")).not.toBeInTheDocument();
  });

  it("rejects fewer than six Unicode codepoints without sending a password write", async () => {
    const interaction = userEvent.setup();
    const service = createService();
    renderPanel({ service });
    await screen.findByText("手机浏览器");

    await interaction.type(screen.getByLabelText("当前密码"), "old password");
    await interaction.type(screen.getByLabelText("新密码"), "😀😀😀");
    await interaction.type(screen.getByLabelText("确认新密码"), "😀😀😀");
    await interaction.click(screen.getByRole("button", { name: "修改密码" }));

    expect(await screen.findByText("新密码至少需要6个字符。")).toBeInTheDocument();
    expect(screen.queryByRole("alertdialog", { name: "确认修改密码？" })).not.toBeInTheDocument();
    expect(service.changePassword).not.toHaveBeenCalled();
  });

  it("allows six numeric characters, preserves exact password bytes, and logs out after exact confirmation", async () => {
    const interaction = userEvent.setup();
    const service = createService();
    const onLogout = vi.fn();
    const { onBeforeSecurityMutation } = renderPanel({ service, onLogout });
    await screen.findByText("手机浏览器");

    await openPasswordConfirmation(interaction, {
      oldPassword: " old password ",
      newPassword: "123456",
      confirmedPassword: "123456",
    });
    expect(onBeforeSecurityMutation).toHaveBeenCalledTimes(1);
    await interaction.click(screen.getByRole("button", { name: "确认修改密码" }));

    await waitFor(() => expect(service.changePassword).toHaveBeenCalledTimes(1));
    expect(service.changePassword).toHaveBeenCalledWith(
      { old_password: " old password ", new_password: "123456" },
      expect.any(AbortSignal),
    );
    expect(onLogout).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText("当前密码")).toHaveValue("");
    expect(screen.getByLabelText("新密码")).toHaveValue("");
    expect(screen.getByLabelText("确认新密码")).toHaveValue("");
  });

  it("requires exact confirmation and does not write when new and current passwords match", async () => {
    const interaction = userEvent.setup();
    const service = createService();
    renderPanel({ service });
    await screen.findByText("手机浏览器");

    await interaction.type(screen.getByLabelText("当前密码"), "123456");
    await interaction.type(screen.getByLabelText("新密码"), "123456");
    await interaction.type(screen.getByLabelText("确认新密码"), "654321");
    await interaction.click(screen.getByRole("button", { name: "修改密码" }));
    expect(await screen.findByText("新密码不能与当前密码相同。")).toBeInTheDocument();
    expect(service.changePassword).not.toHaveBeenCalled();
  });

  it("keeps credentials editable after a definite 400 and does not show server details", async () => {
    const interaction = userEvent.setup();
    const service = createService();
    vi.mocked(service.changePassword).mockRejectedValueOnce(new ApiError("http", "secret-bearing backend detail", 400));
    renderPanel({ service });
    await screen.findByText("手机浏览器");

    await openPasswordConfirmation(interaction);
    await interaction.click(screen.getByRole("button", { name: "确认修改密码" }));

    expect(await screen.findByText("密码未被修改，请检查当前密码和新密码后重试。")).toBeInTheDocument();
    expect(screen.queryByText(/secret-bearing/)).not.toBeInTheDocument();
    expect(screen.getByLabelText("当前密码")).toHaveValue("old pass");
    expect(screen.getByLabelText("新密码")).toHaveValue("123456");
    expect(screen.getByLabelText("确认新密码")).toHaveValue("123456");
  });

  it("preserves authentication on 403", async () => {
    const interaction = userEvent.setup();
    const service = createService();
    const onUnauthorized = vi.fn();
    vi.mocked(service.changePassword).mockRejectedValueOnce(new ApiError("http", "forbidden", 403));
    renderPanel({ service, onUnauthorized });
    await screen.findByText("手机浏览器");

    await openPasswordConfirmation(interaction);
    await interaction.click(screen.getByRole("button", { name: "确认修改密码" }));
    expect(await screen.findByText("当前账号无权修改密码，登录状态保持不变。")).toBeInTheDocument();
    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(screen.getByLabelText("当前密码")).toHaveValue("old pass");
  });

  it("clears secrets and never retries after an unknown password result; a successful me check still leaves it unknown", async () => {
    const interaction = userEvent.setup();
    const service = createService();
    const refreshCurrentUser = vi.fn(async () => user);
    const onLogout = vi.fn();
    vi.mocked(service.changePassword).mockRejectedValueOnce(new ApiError("http", "server error", 500));
    const { onDirtyChange } = renderPanel({ service, refreshCurrentUser, onLogout });
    await screen.findByText("手机浏览器");

    await openPasswordConfirmation(interaction);
    await interaction.click(screen.getByRole("button", { name: "确认修改密码" }));
    expect(await screen.findByRole("heading", { name: "改密结果未确定" })).toBeInTheDocument();
    expect(screen.getByLabelText("当前密码")).toHaveValue("");
    expect(screen.getByLabelText("新密码")).toHaveValue("");
    expect(screen.getByLabelText("确认新密码")).toHaveValue("");
    expect(service.changePassword).toHaveBeenCalledTimes(1);
    expect(onDirtyChange).toHaveBeenLastCalledWith(true);

    await interaction.click(screen.getByRole("button", { name: "只读检查当前登录" }));
    expect(await screen.findByText(/当前登录检查通过，但改密结果仍未确定/)).toBeInTheDocument();
    expect(refreshCurrentUser).toHaveBeenCalledTimes(1);
    expect(service.changePassword).toHaveBeenCalledTimes(1);
    expect(onLogout).not.toHaveBeenCalled();
    await interaction.click(screen.getByRole("button", { name: "退出并手动重新登录" }));
    expect(onLogout).toHaveBeenCalledTimes(1);
  });

  it("does not treat a 401 from the read-only me check as proof that password change succeeded", async () => {
    const interaction = userEvent.setup();
    const service = createService();
    const refreshCurrentUser = vi.fn().mockRejectedValue(new ApiError("http", "unauthorized", 401));
    const onUnauthorized = vi.fn();
    vi.mocked(service.changePassword).mockRejectedValueOnce(new ApiError("timeout", "unknown"));
    renderPanel({ service, refreshCurrentUser, onUnauthorized });
    await screen.findByText("手机浏览器");

    await openPasswordConfirmation(interaction);
    await interaction.click(screen.getByRole("button", { name: "确认修改密码" }));
    await screen.findByRole("heading", { name: "改密结果未确定" });
    await interaction.click(screen.getByRole("button", { name: "只读检查当前登录" }));

    expect(await screen.findByText(/当前登录已失效，但这不能单独证明改密成功/)).toBeInTheDocument();
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
    expect(service.changePassword).toHaveBeenCalledTimes(1);
  });

  const delayedReadWriteOutcomes = [
    {
      label: "400",
      error: () => new ApiError("http", "bad request", 400),
      message: "密码未被修改，请检查当前密码和新密码后重试。",
    },
    {
      label: "403",
      error: () => new ApiError("http", "forbidden", 403),
      message: "当前账号无权修改密码，登录状态保持不变。",
    },
    {
      label: "unknown",
      error: () => new ApiError("timeout", "unknown"),
      message: "改密结果未确定。为避免重复提交，密码输入已清除；请只读检查登录状态或退出后重新登录。",
    },
  ] as const;

  for (const writeOutcome of delayedReadWriteOutcomes) {
    for (const lateReadOutcome of ["success", "401"] as const) {
      it(`retires an initial device read after password ${writeOutcome.label} and ignores its late ${lateReadOutcome}`, async () => {
        const interaction = userEvent.setup();
        const initialRead = deferred<AccountSession[]>();
        const recoveryRead = deferred<AccountSession[]>();
        const service = createService();
        const onUnauthorized = vi.fn();
        const onLogout = vi.fn();
        vi.mocked(service.listSessions)
          .mockReturnValueOnce(initialRead.promise)
          .mockReturnValueOnce(recoveryRead.promise);
        vi.mocked(service.changePassword).mockRejectedValueOnce(writeOutcome.error());
        renderPanel({ service, onUnauthorized, onLogout });

        await waitFor(() => expect(service.listSessions).toHaveBeenCalledTimes(1));
        await openPasswordConfirmation(interaction);
        await interaction.click(screen.getByRole("button", { name: "确认修改密码" }));

        expect(await screen.findByText(writeOutcome.message)).toBeInTheDocument();
        expect(service.changePassword).toHaveBeenCalledTimes(1);
        expect(vi.mocked(service.listSessions).mock.calls[0]?.[0].aborted).toBe(true);
        expect(screen.queryByText("正在读取登录设备…")).not.toBeInTheDocument();
        expect(screen.getByRole("button", { name: "重试读取设备" })).toBeEnabled();

        await interaction.click(screen.getByRole("button", { name: "重试读取设备" }));
        await waitFor(() => expect(service.listSessions).toHaveBeenCalledTimes(2));

        if (lateReadOutcome === "success") {
          await act(async () => initialRead.resolve([{ ...otherSession, deviceName: "迟到的旧设备" }]));
        } else {
          await act(async () => initialRead.reject(new ApiError("http", "unauthorized", 401)));
        }

        expect(screen.queryByText("迟到的旧设备")).not.toBeInTheDocument();
        expect(onUnauthorized).not.toHaveBeenCalled();
        expect(onLogout).not.toHaveBeenCalled();
        expect(screen.getByText("正在读取登录设备…")).toBeInTheDocument();
        expect(service.changePassword).toHaveBeenCalledTimes(1);

        await act(async () => recoveryRead.resolve([{ ...otherSession, deviceName: "恢复后的设备" }]));
        expect(await screen.findByText("恢复后的设备")).toBeInTheDocument();
        expect(onUnauthorized).not.toHaveBeenCalled();
        expect(onLogout).not.toHaveBeenCalled();
        expect(service.changePassword).toHaveBeenCalledTimes(1);
      });
    }
  }

  it("disables an error-state device retry while a security write owns the operation", async () => {
    const interaction = userEvent.setup();
    const passwordWrite = deferred<{ message: string }>();
    const service = createService();
    vi.mocked(service.listSessions).mockRejectedValueOnce(new ApiError("http", "unavailable", 503));
    vi.mocked(service.changePassword).mockReturnValueOnce(passwordWrite.promise);
    const { onLogout } = renderPanel({ service });

    await screen.findByRole("button", { name: "重试读取设备" });
    await openPasswordConfirmation(interaction);
    await interaction.click(screen.getByRole("button", { name: "确认修改密码" }));
    await waitFor(() => expect(service.changePassword).toHaveBeenCalledTimes(1));

    expect(screen.getByRole("button", { name: "重试读取设备" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "正在处理…" })).toBeDisabled();
    await act(async () => passwordWrite.resolve({ message: "密码修改成功，请重新登录" }));
    await waitFor(() => expect(onLogout).toHaveBeenCalledTimes(1));
  });

  for (const finalPreflight of ["deny", "reject"] as const) {
    const outcomeLabel = finalPreflight === "deny" ? "is denied" : "throws";
    it(`rechecks parent permission before the final password write when the check ${outcomeLabel}`, async () => {
      const interaction = userEvent.setup();
      const service = createService();
      const onLogout = vi.fn();
      const onBeforeSecurityMutation = vi.fn(async () => true).mockResolvedValueOnce(true);
      if (finalPreflight === "deny") onBeforeSecurityMutation.mockResolvedValueOnce(false);
      else onBeforeSecurityMutation.mockRejectedValueOnce(new Error("permission check failed"));
      const { onBeforeSecurityMutation: check } = renderPanel({ service, onLogout, onBeforeSecurityMutation });
      await screen.findByText("手机浏览器");

      await openPasswordConfirmation(interaction);
      expect(check).toHaveBeenCalledTimes(1);
      await interaction.click(screen.getByRole("button", { name: "确认修改密码" }));

      await waitFor(() => expect(check).toHaveBeenCalledTimes(2));
      const expectedMessage = finalPreflight === "deny"
        ? "请先保存或明确放弃未保存的账户简介，再修改密码。"
        : "暂时无法确认账户简介状态，请先处理简介草稿后重试。";
      expect(await screen.findByText(expectedMessage)).toBeInTheDocument();
      expect(service.changePassword).not.toHaveBeenCalled();
      expect(onLogout).not.toHaveBeenCalled();
      expect(screen.queryByRole("alertdialog", { name: "确认修改密码？" })).not.toBeInTheDocument();
      expect(screen.getByLabelText("当前密码")).toHaveValue("old pass");
      expect(screen.getByLabelText("新密码")).toHaveValue("123456");
      expect(screen.getByLabelText("确认新密码")).toHaveValue("123456");
      expect(screen.getByRole("button", { name: "修改密码" })).toBeEnabled();
    });
  }

  it("does not write after the final parent permission resolves for a replaced scope or clear the new scope's busy owner", async () => {
    const interaction = userEvent.setup();
    const oldPermission = deferred<boolean>();
    const oldService = createService();
    const oldCheck = vi.fn(async () => true).mockResolvedValueOnce(true).mockReturnValueOnce(oldPermission.promise);
    const oldLogout = vi.fn();
    const first = renderPanel({ service: oldService, onBeforeSecurityMutation: oldCheck, onLogout: oldLogout });
    await screen.findByText("手机浏览器");

    await openPasswordConfirmation(interaction);
    await interaction.click(screen.getByRole("button", { name: "确认修改密码" }));
    await waitFor(() => expect(oldCheck).toHaveBeenCalledTimes(2));

    const newPermission = deferred<boolean>();
    const newService = createService();
    const newCheck = vi.fn(async () => true).mockResolvedValueOnce(true).mockReturnValueOnce(newPermission.promise);
    const newLogout = vi.fn();
    const nextProps: AccountSecurityPanelProps = {
      ...first.props,
      userId: "user-1",
      servicesIdentity: {},
      service: newService,
      sessionGeneration: 2,
      refreshCurrentUser: vi.fn(async () => user),
      onBeforeSecurityMutation: newCheck,
      onLogout: newLogout,
    };
    first.rerender(<AccountSecurityPanel {...nextProps} />);
    await screen.findByText("手机浏览器");

    await openPasswordConfirmation(interaction);
    await interaction.click(screen.getByRole("button", { name: "确认修改密码" }));
    await waitFor(() => expect(newCheck).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("button", { name: "正在处理…" })).toBeDisabled();

    await act(async () => oldPermission.resolve(true));
    expect(oldService.changePassword).not.toHaveBeenCalled();
    expect(oldLogout).not.toHaveBeenCalled();
    expect(newService.changePassword).not.toHaveBeenCalled();
    expect(newLogout).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "正在处理…" })).toBeDisabled();
    expect(screen.getByLabelText("当前密码")).toHaveValue("old pass");

    await act(async () => newPermission.resolve(false));
    expect(await screen.findByText("请先保存或明确放弃未保存的账户简介，再修改密码。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "修改密码" })).toBeEnabled();
    expect(oldService.changePassword).not.toHaveBeenCalled();
    expect(newService.changePassword).not.toHaveBeenCalled();
  });

  it("lets the user cancel confirmation without acquiring a write owner or clearing password inputs", async () => {
    const interaction = userEvent.setup();
    const service = createService();
    const { onBeforeSecurityMutation } = renderPanel({ service });
    await screen.findByText("手机浏览器");

    await openPasswordConfirmation(interaction);
    expect(onBeforeSecurityMutation).toHaveBeenCalledTimes(1);
    await interaction.click(screen.getByRole("button", { name: "取消" }));

    expect(screen.queryByRole("alertdialog", { name: "确认修改密码？" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("当前密码")).toHaveValue("old pass");
    expect(screen.getByLabelText("新密码")).toHaveValue("123456");
    expect(screen.getByLabelText("确认新密码")).toHaveValue("123456");
    expect(screen.getByRole("button", { name: "修改密码" })).toBeEnabled();
    expect(onBeforeSecurityMutation).toHaveBeenCalledTimes(1);
    expect(service.changePassword).not.toHaveBeenCalled();
  });

  it("does not let a late session read from an old user or port replace current data", async () => {
    const oldRead = deferred<AccountSession[]>();
    const oldService = createService();
    vi.mocked(oldService.listSessions).mockReturnValueOnce(oldRead.promise);
    const firstProps = {
      userId: "user-1",
      servicesIdentity: {},
      service: oldService,
      sessionGeneration: 1,
      refreshCurrentUser: vi.fn(async () => user),
      onUnauthorized: vi.fn(),
      onLogout: vi.fn(),
      onBeforeSecurityMutation: vi.fn(async () => true),
    } satisfies Omit<AccountSecurityPanelProps, "onDirtyChange">;
    const view = render(<AccountSecurityPanel {...firstProps} />);
    await waitFor(() => expect(oldService.listSessions).toHaveBeenCalledTimes(1));

    const nextUser = { ...user, id: "user-2" };
    const nextService = createService();
    const nextProps = {
      ...firstProps,
      userId: nextUser.id,
      servicesIdentity: {},
      service: nextService,
      sessionGeneration: 2,
      refreshCurrentUser: vi.fn(async () => nextUser),
      onUnauthorized: vi.fn(),
      onLogout: vi.fn(),
    };
    view.rerender(<AccountSecurityPanel {...nextProps} />);
    expect(await screen.findByText("手机浏览器")).toBeInTheDocument();

    await act(async () => oldRead.resolve([{ ...otherSession, deviceName: "旧账户设备" }]));
    expect(screen.queryByText("旧账户设备")).not.toBeInTheDocument();
    expect(nextProps.onUnauthorized).not.toHaveBeenCalled();
  });
});
