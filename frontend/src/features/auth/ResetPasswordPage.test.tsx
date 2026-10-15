import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createApiServices } from "../../shared/api/services";
import type { AccountAccessService } from "../../shared/api/accountAccess";
import { App } from "../../app/App";
import { captureResetTokenFromLocation, ResetPasswordPage } from "./ResetPasswordPage";

afterEach(() => {
  cleanup();
  window.history.replaceState(window.history.state, "", "/");
  vi.restoreAllMocks();
});

describe("password reset route", () => {
  it("captures the token in memory and removes it from the current URL", () => {
    window.history.replaceState({}, "", "/reset-password.html?token=short-lived-token&source=email");

    expect(captureResetTokenFromLocation()).toBe("short-lived-token");
    expect(window.location.pathname).toBe("/reset-password.html");
    expect(window.location.search).toBe("?source=email");
    expect(window.location.href).not.toContain("short-lived-token");
  });

  it("is directly reachable without restoring an existing account session", () => {
    window.history.replaceState({}, "", "/reset-password.html");
    const fetcher = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({}),
    }) as Response);
    const services = createApiServices("http://127.0.0.1:4175/api", { fetcher });

    const resetPasswordTokenHolder = { current: "in-memory-token" };
    render(<App services={services} resetPasswordTokenHolder={resetPasswordTokenHolder} />);

    expect(screen.getByRole("heading", { name: "设置新密码" })).toBeInTheDocument();
    expect(fetcher).not.toHaveBeenCalled();
    expect(document.body).not.toHaveTextContent("in-memory-token");
  });

  it("resets the password, clears the in-memory token, and does not log in", async () => {
    const user = userEvent.setup();
    const resetPassword = vi.fn(async () => ({ message: "密码重置成功" }));
    const accountAccess: AccountAccessService = {
      register: vi.fn(),
      sendEmailCode: vi.fn(),
      verifyEmailCode: vi.fn(),
      requestPasswordReset: vi.fn(),
      resetPassword,
    };
    render(<ResetPasswordPage
      token="one-time-token"
      accountAccess={accountAccess}
      serviceUnavailableMessage="当前演示模式不连接密码重置服务。"
      onTokenConsumed={() => {}}
    />);

    await user.type(screen.getByLabelText("新密码"), "newSecret123");
    await user.type(screen.getByLabelText("确认新密码"), "newSecret123");
    await user.click(screen.getByRole("button", { name: "保存新密码" }));

    await waitFor(() => expect(resetPassword).toHaveBeenCalledWith({
      token: "one-time-token",
      new_password: "newSecret123",
    }, expect.any(AbortSignal)));
    expect(await screen.findByText(/当前登录会话没有改变/)).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("one-time-token");
    expect(screen.getByRole("link", { name: "返回登录" })).toHaveAttribute("href", "/");
  });
});
