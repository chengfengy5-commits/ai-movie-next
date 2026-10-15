import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../shared/api/errors";
import type { AccountAccessService, EmailCodeReceipt } from "../../shared/api/accountAccess";
import type { User } from "../../shared/api/contracts";
import { LoginPage } from "./LoginPage";

const createdUser: User = {
  id: "created-user",
  username: "new-user",
  email: "new@example.invalid",
  is_superuser: false,
  membership_type: "free",
  membership_expires_at: null,
  avatar_url: null,
  bio: null,
  created_at: "2026-10-16T00:00:00Z",
};

function makeAccountAccess(overrides: Partial<AccountAccessService> = {}): AccountAccessService {
  return {
    register: vi.fn(async () => createdUser),
    sendEmailCode: vi.fn(async () => ({ message: "验证码已发送", expireInSeconds: 300 })),
    verifyEmailCode: vi.fn(async () => ({ message: "验证成功" })),
    requestPasswordReset: vi.fn(async () => ({ message: "如果信息正确，重置链接已发送" })),
    resetPassword: vi.fn(async () => ({ message: "密码重置成功" })),
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("account entry forms", () => {
  it("registers with the entered code without requiring separate verification", async () => {
    const user = userEvent.setup();
    const accountAccess = makeAccountAccess();
    const onSubmit = vi.fn(async () => {});
    render(
      <LoginPage mode="api" error={null} accountAccess={accountAccess} onSubmit={onSubmit} />,
    );

    await user.click(screen.getByRole("button", { name: "创建账号" }));
    await user.type(screen.getByLabelText("账号"), "  new-user  ");
    await user.type(screen.getByLabelText("邮箱"), "new@example.invalid");
    await user.click(screen.getByRole("button", { name: "发送邮箱验证码" }));
    await waitFor(() => expect(accountAccess.sendEmailCode).toHaveBeenCalledWith({
      email: "new@example.invalid",
      username: "new-user",
    }, expect.any(AbortSignal)));

    await user.type(screen.getByLabelText("邮箱验证码"), "123456");
    await user.type(screen.getByLabelText("密码"), "secret123");
    await user.type(screen.getByLabelText("确认密码"), "secret123");
    await user.click(screen.getByRole("button", { name: "创建账号" }));

    await screen.findByText("账号已创建，请使用新账号和密码登录。");
    expect(accountAccess.register).toHaveBeenCalledWith({
      username: "new-user",
      email: "new@example.invalid",
      password: "secret123",
      email_code: "123456",
    }, expect.any(AbortSignal));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(accountAccess.verifyEmailCode).not.toHaveBeenCalled();
    expect(screen.getByLabelText("账号")).toHaveValue("new-user");
    expect(screen.getByLabelText("密码")).toHaveValue("");
  });

  it("rejects a whitespace-only username without registering and still allows sending a code", async () => {
    const user = userEvent.setup();
    const accountAccess = makeAccountAccess();
    render(
      <LoginPage mode="api" error={null} accountAccess={accountAccess} onSubmit={vi.fn(async () => {})} />,
    );

    await user.click(screen.getByRole("button", { name: "创建账号" }));
    await user.type(screen.getByLabelText("账号"), "   ");
    await user.type(screen.getByLabelText("邮箱"), "new@example.invalid");
    await user.click(screen.getByRole("button", { name: "发送邮箱验证码" }));
    await waitFor(() => expect(accountAccess.sendEmailCode).toHaveBeenCalledWith({
      email: "new@example.invalid",
      username: "",
    }, expect.any(AbortSignal)));

    await user.type(screen.getByLabelText("邮箱验证码"), "123456");
    await user.type(screen.getByLabelText("密码"), "secret123");
    await user.type(screen.getByLabelText("确认密码"), "secret123");
    await user.click(screen.getByRole("button", { name: "创建账号" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("账号不能为空或只包含空白字符");
    expect(accountAccess.register).not.toHaveBeenCalled();
    expect(screen.getByLabelText("账号")).toHaveValue("   ");
    expect(screen.getByLabelText("账号")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("邮箱")).toHaveValue("new@example.invalid");
    expect(screen.getByLabelText("邮箱验证码")).toHaveValue("123456");
    expect(screen.getByLabelText("密码")).toHaveValue("secret123");
  });

  it("aborts an account request when the user leaves the form", async () => {
    const user = userEvent.setup();
    let requestSignal: AbortSignal | undefined;
    const accountAccess = makeAccountAccess({
      sendEmailCode: vi.fn((
        _input: Parameters<AccountAccessService["sendEmailCode"]>[0],
        signal: AbortSignal,
      ) => {
        requestSignal = signal;
        return new Promise<EmailCodeReceipt>((_resolve, reject) => {
          signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
        });
      }),
    });
    vi.stubGlobal("confirm", vi.fn(() => true));
    render(
      <LoginPage mode="api" error={null} accountAccess={accountAccess} onSubmit={vi.fn(async () => {})} />,
    );

    await user.click(screen.getByRole("button", { name: "创建账号" }));
    await user.type(screen.getByLabelText("账号"), "new-user");
    await user.type(screen.getByLabelText("邮箱"), "new@example.invalid");
    await user.click(screen.getByRole("button", { name: "发送邮箱验证码" }));
    await waitFor(() => expect(requestSignal).toBeDefined());
    await user.click(screen.getByRole("button", { name: "返回登录" }));

    expect(requestSignal?.aborted).toBe(true);
    expect(screen.getByRole("heading", { name: "欢迎回来" })).toBeInTheDocument();
  });

  it("keeps editable registration fields after a rejected email code", async () => {
    const user = userEvent.setup();
    const accountAccess = makeAccountAccess({
      verifyEmailCode: vi.fn(async () => {
        throw new ApiError("http", "验证码错误", 400, "验证码错误");
      }),
    });
    render(
      <LoginPage mode="api" error={null} accountAccess={accountAccess} onSubmit={vi.fn(async () => {})} />,
    );

    await user.click(screen.getByRole("button", { name: "创建账号" }));
    await user.type(screen.getByLabelText("账号"), "new-user");
    await user.type(screen.getByLabelText("邮箱"), "new@example.invalid");
    await user.click(screen.getByRole("button", { name: "发送邮箱验证码" }));
    await user.type(screen.getByLabelText("邮箱验证码"), "000000");
    await user.click(screen.getByRole("button", { name: "校验" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("验证码错误");
    expect(screen.getByLabelText("账号")).toHaveValue("new-user");
    expect(screen.getByLabelText("邮箱")).toHaveValue("new@example.invalid");
    expect(screen.getByLabelText("邮箱验证码")).toHaveValue("000000");
  });

  it("reports the generic password-reset response without claiming delivery", async () => {
    const user = userEvent.setup();
    const accountAccess = makeAccountAccess();
    render(
      <LoginPage mode="api" error={null} accountAccess={accountAccess} onSubmit={vi.fn(async () => {})} />,
    );

    await user.click(screen.getByRole("button", { name: "忘记密码？" }));
    await user.type(screen.getByLabelText("账号"), "new-user");
    await user.type(screen.getByLabelText("注册邮箱"), "new@example.invalid");
    await user.click(screen.getByRole("button", { name: "申请重置链接" }));

    expect(await screen.findByText(/不能确认邮件已送达/)).toBeInTheDocument();
    expect(accountAccess.requestPasswordReset).toHaveBeenCalledWith({
      username: "new-user",
      email: "new@example.invalid",
    }, expect.any(AbortSignal));
  });

  it("keeps account actions unavailable in demo mode", async () => {
    const user = userEvent.setup();
    render(<LoginPage mode="demo" error={null} onSubmit={vi.fn(async () => {})} />);

    await user.click(screen.getByRole("button", { name: "创建账号" }));

    expect(screen.getByText(/演示模式不连接注册或邮件服务/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "发送邮箱验证码" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "创建账号" })).toBeDisabled();
  });
});
