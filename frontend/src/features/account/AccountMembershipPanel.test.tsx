import { act, cleanup, render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { User } from "../../shared/api/contracts";
import { ApiError } from "../../shared/api/errors";
import type { AccountProfileServicePort } from "../../shared/api/accountProfile";
import type {
  AccountMembershipServicePort,
  AccountRedemptionReceipt,
  PurchasePackage,
  PublicSiteConfig,
} from "../../shared/api/accountMembership";
import { AccountMembershipPanel, type AccountMembershipPanelProps } from "./AccountMembershipPanel";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const baseUser: User = {
  id: "account-user-1",
  username: "账户用户",
  email: "creator@example.test",
  is_superuser: false,
  membership_type: "free",
  membership_expires_at: null,
  avatar_url: null,
  bio: null,
  created_at: "2026-10-01T09:10:00",
};

const publicConfig: PublicSiteConfig = {
  site_title: "HaoAI",
  site_tagline: "创作工作台",
  membership_benefits: "权益一\n权益二",
  tutorial_url: "",
  purchase_qr_code_url: "",
  purchase_contact_info: "管理员联系方式尚未设置",
  storyboard_cost_multiplier: 1,
  payments_enabled: false,
};

const membershipPackages: PurchasePackage[] = [
  { id: "membership-2", category: "membership", name: "长期套餐", price: 99, credits: null, days: 365, sort_order: 9 },
  { id: "membership-1", category: "membership", name: "短期套餐", price: 19, credits: 25, days: 30, sort_order: 1 },
];

const creditPackages: PurchasePackage[] = [
  { id: "credits-1", category: "credits", name: "积分包", price: 29, credits: 300, days: null, sort_order: 4 },
];

function createMembership(overrides: Partial<AccountMembershipServicePort> = {}): AccountMembershipServicePort {
  return {
    getPublicConfig: vi.fn(async () => publicConfig),
    listPurchasePackages: vi.fn(async (category: "membership" | "credits") => (
      category === "membership" ? membershipPackages : creditPackages
    )),
    redeem: vi.fn(async (): Promise<AccountRedemptionReceipt> => ({
      message: "兑换已完成",
      type: "credits",
      value: 300,
      detail: null,
    })),
    ...overrides,
  };
}

function createProfile(credits = 25): AccountProfileServicePort {
  return {
    getCredits: vi.fn(async () => ({ credits })),
    updateProfile: vi.fn(async () => ({
      id: baseUser.id,
      username: baseUser.username,
      avatar_url: baseUser.avatar_url,
      bio: baseUser.bio,
    })),
  };
}

function renderPanel(options: Partial<AccountMembershipPanelProps> = {}) {
  const user = options.user ?? baseUser;
  const membership = options.membership === undefined ? createMembership() : options.membership;
  const profile = options.profile === undefined ? createProfile() : options.profile;
  const refreshCurrentUser = options.refreshCurrentUser ?? vi.fn(async () => user);
  const onUnauthorized = options.onUnauthorized ?? vi.fn();
  const onDirtyChange = options.onDirtyChange ?? vi.fn();
  const onRedemptionStateChange = options.onRedemptionStateChange ?? vi.fn();
  const authorizeRedeem = options.authorizeRedeem ?? vi.fn(async () => true);
  const props: AccountMembershipPanelProps = {
    user,
    membership,
    profile,
    refreshCurrentUser,
    sessionGeneration: options.sessionGeneration ?? 1,
    onUnauthorized,
    onDirtyChange,
    onRedemptionStateChange,
    canRedeem: options.canRedeem ?? true,
    authorizeRedeem,
  };
  const view = render(<AccountMembershipPanel {...props} />);
  return { ...view, props, user, membership, profile, refreshCurrentUser, onUnauthorized, onDirtyChange, onRedemptionStateChange, authorizeRedeem };
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

describe("AccountMembershipPanel", () => {
  it("renders live benefits and server-ordered packages without inventing contact or payment actions", async () => {
    const script = vi.fn();
    const membership = createMembership({
      getPublicConfig: vi.fn(async () => ({
        ...publicConfig,
        membership_benefits: `<img src=x onerror="window.__membershipScript()">`,
        payments_enabled: false,
      })),
    });
    renderPanel({ membership });

    expect(await screen.findByText("<img src=x onerror=\"window.__membershipScript()\">", { exact: true })).toBeInTheDocument();
    expect(screen.getByText("长期套餐").compareDocumentPosition(screen.getByText("短期套餐"))
      & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByText("价格：¥29 元")).toBeInTheDocument();
    expect(screen.getByText("积分：未提供")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "查看购买联系信息" })).not.toBeInTheDocument();
    expect(screen.getByText(/当前不提供套餐购买联系入口/)).toBeInTheDocument();
    expect(membership.listPurchasePackages).toHaveBeenCalledTimes(2);
    expect(membership.redeem).not.toHaveBeenCalled();
    expect(script).not.toHaveBeenCalled();
  });

  it("shows safe contact text fallback and never interprets configured HTML", async () => {
    const user = userEvent.setup();
    const membership = createMembership({
      getPublicConfig: vi.fn(async () => ({
        ...publicConfig,
        purchase_qr_code_url: "javascript:alert(1)",
        purchase_contact_info: "<b>管理员微信</b>",
        payments_enabled: true,
      })),
    });
    renderPanel({ membership });

    const contactButtons = await screen.findAllByRole("button", { name: "查看购买联系信息" });
    await user.click(contactButtons[0]!);
    expect(screen.getByText("<b>管理员微信</b>", { exact: true })).toBeInTheDocument();
    expect(screen.getByText("二维码地址不符合安全展示要求，已改为显示联系文本。")).toBeInTheDocument();
    expect(document.querySelector(".account-membership-contact-text b")).toBeNull();
    expect(membership.redeem).not.toHaveBeenCalled();
  });

  it("loads only safe HTTP(S) or same-origin relative QR images and falls back after image failure", async () => {
    const user = userEvent.setup();
    const membership = createMembership({
      getPublicConfig: vi.fn(async () => ({
        ...publicConfig,
        purchase_qr_code_url: "/assets/purchase-qr.svg",
        payments_enabled: true,
      })),
    });
    renderPanel({ membership });
    const contactButtons = await screen.findAllByRole("button", { name: "查看购买联系信息" });
    await user.click(contactButtons[0]!);

    const image = screen.getByRole("img", { name: "管理员提供的购买联系二维码" });
    expect(image).toHaveAttribute("src", "/assets/purchase-qr.svg");
    fireEvent.error(image);
    expect(await screen.findByText(/二维码加载失败/)).toBeInTheDocument();
    expect(screen.getByText("管理员联系方式尚未设置")).toBeInTheDocument();
  });

  it("keeps configuration and category failures independent and retries without adding defaults", async () => {
    const user = userEvent.setup();
    const getPublicConfig = vi.fn()
      .mockRejectedValueOnce(new ApiError("network", "offline"))
      .mockResolvedValueOnce(publicConfig);
    const listPurchasePackages = vi.fn(async (category: "membership" | "credits") => {
      if (category === "membership") throw new ApiError("http", "server unavailable", 503);
      return [];
    });
    const membership = createMembership({ getPublicConfig, listPurchasePackages });
    renderPanel({ membership });

    expect(await screen.findByText(/公开配置读取暂时无法连接服务/)).toBeInTheDocument();
    expect(screen.getByText(/会员套餐读取暂时不可用/)).toBeInTheDocument();
    expect(screen.getByText("当前没有可展示的积分套餐。")).toBeInTheDocument();
    expect(screen.queryByText(/¥10 元/)).not.toBeInTheDocument();

    await user.click(screen.getAllByRole("button", { name: "重试读取" })[0]!);
    await waitFor(() => expect(screen.getByText(/权益一\s+权益二/)).toBeInTheDocument());
    expect(getPublicConfig).toHaveBeenCalledTimes(2);
    expect(listPurchasePackages).toHaveBeenCalledTimes(2);
  });

  it("validates only trimmed emptiness and length locally, then refreshes me and credits after a receipt", async () => {
    const user = userEvent.setup();
    const updatedUser = { ...baseUser, membership_type: "premium", membership_expires_at: "2099-01-02T03:04:05Z" };
    const membership = createMembership();
    const profile = createProfile(325);
    const refreshCurrentUser = vi.fn(async () => updatedUser);
    const { onDirtyChange, onRedemptionStateChange } = renderPanel({ membership, profile, refreshCurrentUser });

    const input = screen.getByRole("textbox", { name: "兑换码" });
    const submit = screen.getByRole("button", { name: "兑换" });
    await user.type(input, "   ");
    await user.click(submit);
    expect(await screen.findByText("请输入兑换码。")).toBeInTheDocument();
    expect(membership.redeem).not.toHaveBeenCalled();

    await user.clear(input);
    await user.type(input, "  Strange / code ?!  ");
    await user.click(submit);
    expect(await screen.findByText("兑换已完成")).toBeInTheDocument();
    expect(membership.redeem).toHaveBeenCalledTimes(1);
    expect(membership.redeem).toHaveBeenCalledWith({ code: "Strange / code ?!" }, expect.any(AbortSignal));
    expect(refreshCurrentUser).toHaveBeenCalledTimes(1);
    expect(profile.getCredits).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("325", { exact: true })).toBeInTheDocument();
    expect(screen.getByText("会员 · 有效至 2099-01-02 03:04:05 UTC")).toBeInTheDocument();
    expect(onDirtyChange).toHaveBeenLastCalledWith(false);
    expect(onRedemptionStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      phase: "confirmed",
      hasCodeDraft: false,
      writeInFlight: false,
      outcomeUnknown: false,
    }));
  });

  it("counts the 32-character limit by Unicode code point and allows arbitrary content within it", async () => {
    const user = userEvent.setup();
    const membership = createMembership();
    renderPanel({ membership });
    const input = screen.getByRole("textbox", { name: "兑换码" });
    const submit = screen.getByRole("button", { name: "兑换" });
    const codePoints33 = "💡".repeat(33);
    fireEvent.change(input, { target: { value: codePoints33 } });
    await user.click(submit);
    expect(await screen.findByText("兑换码最多 32 个字符，请检查后修改。")).toBeInTheDocument();
    expect(membership.redeem).not.toHaveBeenCalled();

    const codePoints32 = "💡".repeat(32);
    fireEvent.change(input, { target: { value: codePoints32 } });
    await user.click(submit);
    expect(await screen.findByText("兑换已完成")).toBeInTheDocument();
    expect(membership.redeem).toHaveBeenCalledWith({ code: codePoints32 }, expect.any(AbortSignal));
  });

  it("uses a synchronous single-flight guard for repeated form submissions", async () => {
    const user = userEvent.setup();
    const response = deferred<AccountRedemptionReceipt>();
    const redeem = vi.fn(() => response.promise);
    const membership = createMembership({ redeem });
    renderPanel({ membership });
    await screen.findByText(/权益一\s+权益二/);
    await user.type(screen.getByRole("textbox", { name: "兑换码" }), "one-time-code");

    const form = screen.getByRole("textbox", { name: "兑换码" }).closest("form");
    expect(form).not.toBeNull();
    act(() => {
      fireEvent.submit(form!);
      fireEvent.submit(form!);
    });
    await waitFor(() => expect(redeem).toHaveBeenCalledTimes(1));
    await act(async () => {
      response.resolve({ message: "已确认", type: "credits", value: 0, detail: null });
    });
    expect(await screen.findByText("已确认")).toBeInTheDocument();
  });

  it("treats an ambiguous network result as frozen and allows only readback or explicit abandonment", async () => {
    const user = userEvent.setup();
    const membership = createMembership({
      redeem: vi.fn(async () => { throw new ApiError("network", "connection lost"); }),
    });
    const updatedUser = { ...baseUser, membership_type: "premium", membership_expires_at: null };
    const refreshCurrentUser = vi.fn(async () => updatedUser);
    const profile = createProfile(7);
    const { onDirtyChange } = renderPanel({ membership, refreshCurrentUser, profile });
    await user.type(screen.getByRole("textbox", { name: "兑换码" }), "ambiguous-code");
    await user.click(screen.getByRole("button", { name: "兑换" }));

    expect(await screen.findByText(/兑换结果未确定/)).toBeInTheDocument();
    expect(screen.getByText(/变化或“已使用”都不能证明本次兑换成功/)).toBeInTheDocument();
    expect(membership.redeem).toHaveBeenCalledTimes(1);
    expect(refreshCurrentUser).not.toHaveBeenCalled();
    expect(screen.getByRole("textbox", { name: "兑换码" })).toHaveAttribute("readonly");
    expect(screen.queryByText(/兑换已确认/)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "只刷新账户状态" }));
    expect(await screen.findByText("永久会员", { exact: true })).toBeInTheDocument();
    expect(screen.getByText("7", { exact: true })).toBeInTheDocument();
    expect(membership.redeem).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(/兑换已确认/)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "放弃核对并清除本次输入" }));
    expect(await screen.findByText(/已明确放弃本次结果核对/)).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "兑换码" })).toHaveValue("");
    expect(screen.getByRole("textbox", { name: "兑换码" })).toHaveAttribute("readonly");
    expect(membership.redeem).toHaveBeenCalledTimes(1);
    expect(onDirtyChange).toHaveBeenLastCalledWith(false);
  });

  it("does not treat a malformed 200 receipt as success and never retries it", async () => {
    const user = userEvent.setup();
    const membership = createMembership({
      redeem: vi.fn(async () => ({ message: "ok", type: "other", value: 1, detail: null } as unknown as AccountRedemptionReceipt)),
    });
    const profile = createProfile();
    const refreshCurrentUser = vi.fn(async () => baseUser);
    renderPanel({ membership, profile, refreshCurrentUser });
    await user.type(screen.getByRole("textbox", { name: "兑换码" }), "malformed-response");
    await user.click(screen.getByRole("button", { name: "兑换" }));

    expect(await screen.findByText(/兑换结果未确定/)).toBeInTheDocument();
    expect(screen.queryByText("ok")).not.toBeInTheDocument();
    expect(refreshCurrentUser).not.toHaveBeenCalled();
    expect(profile.getCredits).not.toHaveBeenCalled();
    expect(membership.redeem).toHaveBeenCalledTimes(1);
  });

  it("allows an explicit server rejection to be corrected and retried", async () => {
    const user = userEvent.setup();
    const redeem = vi.fn()
      .mockRejectedValueOnce(new ApiError("http", "rejected", 422, "兑换码格式无效"))
      .mockResolvedValueOnce({ message: "完成", type: "membership", value: 3, detail: null });
    const membership = createMembership({ redeem });
    renderPanel({ membership });
    const input = screen.getByRole("textbox", { name: "兑换码" });
    await user.type(input, "bad-code");
    await user.click(screen.getByRole("button", { name: "兑换" }));
    expect(await screen.findByText(/兑换未被服务端接受：兑换码格式无效/)).toBeInTheDocument();
    expect(input).toHaveValue("bad-code");
    expect(screen.queryByRole("button", { name: "只刷新账户状态" })).not.toBeInTheDocument();

    await user.clear(input);
    await user.type(input, "corrected-code");
    await user.click(screen.getByRole("button", { name: "兑换" }));
    expect(await screen.findByText("完成")).toBeInTheDocument();
    expect(redeem).toHaveBeenCalledTimes(2);
  });

  it("retries only the two account reads after a confirmed receipt could not refresh state", async () => {
    const user = userEvent.setup();
    const membership = createMembership();
    const profile = createProfile();
    vi.mocked(profile.getCredits)
      .mockRejectedValueOnce(new ApiError("network", "offline"))
      .mockResolvedValueOnce({ credits: 301 });
    const refreshCurrentUser = vi.fn()
      .mockRejectedValueOnce(new ApiError("timeout", "slow"))
      .mockResolvedValueOnce(baseUser);
    renderPanel({ membership, profile, refreshCurrentUser });
    await user.type(screen.getByRole("textbox", { name: "兑换码" }), "confirmed-on-server");
    await user.click(screen.getByRole("button", { name: "兑换" }));

    expect(await screen.findByText("兑换已确认，最新账户读取失败；可只读重试读取。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "只重试账户读取" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "只重试账户读取" }));
    expect(await screen.findByText("兑换已确认；账户资料与积分余额已分别重新读取。")).toBeInTheDocument();
    expect(screen.getByText("301", { exact: true })).toBeInTheDocument();
    expect(membership.redeem).toHaveBeenCalledTimes(1);
    expect(refreshCurrentUser).toHaveBeenCalledTimes(2);
    expect(profile.getCredits).toHaveBeenCalledTimes(2);
  });

  it.each([
    { nextOutcome: "pending", retryOutcome: "success" },
    { nextOutcome: "pending", retryOutcome: "failure" },
    { nextOutcome: "unknown", retryOutcome: "success" },
    { nextOutcome: "unknown", retryOutcome: "failure" },
  ] as const)("does not let a confirmed receipt read retry overwrite the next redemption ($nextOutcome, late retry $retryOutcome)", async ({ nextOutcome, retryOutcome }) => {
    const user = userEvent.setup();
    const nextRedemption = deferred<AccountRedemptionReceipt>();
    const lateRetryUser = deferred<User>();
    const lateRetryCredits = deferred<{ credits: number }>();
    const redeem = vi.fn()
      .mockResolvedValueOnce({ message: "A 已确认", type: "credits", value: 1, detail: null })
      .mockImplementationOnce(() => nextOutcome === "pending"
        ? nextRedemption.promise
        : Promise.reject(new ApiError("network", "B connection lost")));
    const membership = createMembership({ redeem });
    const profile = createProfile();
    vi.mocked(profile.getCredits)
      .mockRejectedValueOnce(new ApiError("network", "A initial credits read failed"))
      .mockImplementationOnce(() => lateRetryCredits.promise)
      .mockResolvedValueOnce({ credits: 101 });
    const refreshCurrentUser = vi.fn()
      .mockRejectedValueOnce(new ApiError("network", "A initial account read failed"))
      .mockImplementationOnce(() => lateRetryUser.promise)
      .mockResolvedValueOnce(baseUser);
    const onRedemptionStateChange = vi.fn();
    renderPanel({ membership, profile, refreshCurrentUser, onRedemptionStateChange });

    await user.type(screen.getByRole("textbox", { name: "兑换码" }), "receipt-A");
    await user.click(screen.getByRole("button", { name: "兑换" }));
    expect(await screen.findByText("兑换已确认，最新账户读取失败；可只读重试读取。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "只重试账户读取" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "只重试账户读取" }));
    await waitFor(() => {
      expect(refreshCurrentUser).toHaveBeenCalledTimes(2);
      expect(profile.getCredits).toHaveBeenCalledTimes(2);
    });

    const input = screen.getByRole("textbox", { name: "兑换码" });
    await user.type(input, "receipt-B");
    await user.click(screen.getByRole("button", { name: "兑换" }));
    await waitFor(() => expect(redeem).toHaveBeenCalledTimes(2));
    if (nextOutcome === "unknown") {
      expect(await screen.findByText(/兑换结果未确定/)).toBeInTheDocument();
    } else {
      expect(await screen.findByRole("button", { name: "正在提交…" })).toBeDisabled();
    }

    await act(async () => {
      if (retryOutcome === "success") {
        lateRetryUser.resolve(baseUser);
        lateRetryCredits.resolve({ credits: 100 });
      } else {
        lateRetryUser.reject(new ApiError("timeout", "late A account read failed"));
        lateRetryCredits.resolve({ credits: 100 });
      }
      await Promise.allSettled([lateRetryUser.promise, lateRetryCredits.promise]);
    });

    expect(screen.queryByText(/兑换已确认；账户资料与积分余额已分别重新读取。/)).not.toBeInTheDocument();
    expect(screen.queryByText(/兑换已确认，最新账户读取失败/)).not.toBeInTheDocument();
    expect(screen.queryByText("A 已确认")).not.toBeInTheDocument();
    expect(input).toHaveValue("receipt-B");
    expect(onRedemptionStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      phase: nextOutcome === "unknown" ? "outcome-unknown" : "redeeming",
      hasCodeDraft: true,
      writeInFlight: nextOutcome === "pending",
      outcomeUnknown: nextOutcome === "unknown",
    }));
    expect(redeem).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("button", { name: "只重试账户读取" })).not.toBeInTheDocument();
    if (nextOutcome === "unknown") {
      expect(screen.getByText(/兑换结果未确定/)).toBeInTheDocument();
    }

    if (nextOutcome === "pending") {
      await act(async () => nextRedemption.resolve({ message: "B 已确认", type: "credits", value: 2, detail: null }));
      expect(await screen.findByText("B 已确认")).toBeInTheDocument();
      expect(await screen.findByText("101", { exact: true })).toBeInTheDocument();
    } else {
      await user.click(screen.getByRole("button", { name: "放弃核对并清除本次输入" }));
      expect(await screen.findByText(/已明确放弃本次结果核对/)).toBeInTheDocument();
    }
    expect(redeem).toHaveBeenCalledTimes(2);
  });

  it("requires the parent preflight and blocks typing while its security gate is closed", async () => {
    const user = userEvent.setup();
    const membership = createMembership();
    const authorizeRedeem = vi.fn(async () => false);
    const { rerender, props } = renderPanel({ membership, canRedeem: false, authorizeRedeem });
    const input = screen.getByRole("textbox", { name: "兑换码" });
    await user.type(input, "blocked");
    expect(input).toHaveValue("");
    expect(membership.redeem).not.toHaveBeenCalled();

    rerender(<AccountMembershipPanel {...props} canRedeem />);
    await user.type(screen.getByRole("textbox", { name: "兑换码" }), "preflight-denied");
    await user.click(screen.getByRole("button", { name: "兑换" }));
    expect(await screen.findByText(/父级账户检查尚未授权/)).toBeInTheDocument();
    expect(authorizeRedeem).toHaveBeenCalledTimes(1);
    expect(membership.redeem).not.toHaveBeenCalled();
  });

  it("checks the parent security gate again after an asynchronous preflight", async () => {
    const user = userEvent.setup();
    const preflight = deferred<boolean>();
    const membership = createMembership();
    const authorizeRedeem = vi.fn(() => preflight.promise);
    const first = renderPanel({ membership, authorizeRedeem });
    await user.type(screen.getByRole("textbox", { name: "兑换码" }), "preflight-race");
    await user.click(screen.getByRole("button", { name: "兑换" }));
    expect(await screen.findByRole("button", { name: "正在检查账户…" })).toBeDisabled();

    first.rerender(<AccountMembershipPanel {...first.props} canRedeem={false} />);
    await act(async () => preflight.resolve(true));
    expect(await screen.findByText(/账户安全操作已进入临界阶段/)).toBeInTheDocument();
    expect(membership.redeem).not.toHaveBeenCalled();
  });

  it.each([true, false])("retires an outstanding preflight when the parent authorization callback changes (stale result %s)", async (stalePermission) => {
    const user = userEvent.setup();
    const preflight = deferred<boolean>();
    const membership = createMembership();
    const authorizeRedeem = vi.fn(() => preflight.promise);
    const replacementAuthorize = vi.fn(async () => true);
    const panel = renderPanel({ membership, authorizeRedeem });
    const input = screen.getByRole("textbox", { name: "兑换码" });
    await user.type(input, "retry-after-preflight-change");
    await user.click(screen.getByRole("button", { name: "兑换" }));
    expect(await screen.findByRole("button", { name: "正在检查账户…" })).toBeDisabled();

    panel.rerender(<AccountMembershipPanel {...panel.props} authorizeRedeem={replacementAuthorize} />);

    expect(await screen.findByText(/账户安全检查已更新/)).toBeInTheDocument();
    expect(input).toHaveValue("retry-after-preflight-change");
    expect(input).not.toHaveAttribute("readonly");
    expect(membership.redeem).not.toHaveBeenCalled();

    await act(async () => preflight.resolve(stalePermission));
    expect(membership.redeem).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "兑换" }));
    expect(await screen.findByText("兑换已完成")).toBeInTheDocument();
    expect(replacementAuthorize).toHaveBeenCalledTimes(1);
    expect(membership.redeem).toHaveBeenCalledTimes(1);
  });

  it.each([
    {
      result: "a valid receipt",
      settle: async (response: ReturnType<typeof deferred<AccountRedemptionReceipt>>) => {
        await act(async () => response.resolve({ message: "确认后换回调", type: "credits", value: 1, detail: null }));
      },
      expected: /兑换已确认/,
      unknown: false,
      unauthorized: false,
    },
    {
      result: "a definite rejection",
      settle: async (response: ReturnType<typeof deferred<AccountRedemptionReceipt>>) => {
        await act(async () => response.reject(new ApiError("http", "rejected", 422, "码无效")));
      },
      expected: /兑换未被服务端接受：码无效/,
      unknown: false,
      unauthorized: false,
    },
    {
      result: "an unknown outcome",
      settle: async (response: ReturnType<typeof deferred<AccountRedemptionReceipt>>) => {
        await act(async () => response.reject(new ApiError("network", "connection lost")));
      },
      expected: /兑换结果未确定/,
      unknown: true,
      unauthorized: false,
    },
    {
      result: "a current-scope 401",
      settle: async (response: ReturnType<typeof deferred<AccountRedemptionReceipt>>) => {
        await act(async () => response.reject(new ApiError("http", "expired token", 401)));
      },
      expected: /登录状态已失效/,
      unknown: false,
      unauthorized: true,
    },
  ])("keeps an already sent redemption owned by its account scope after an authorization callback change ($result)", async ({ settle, expected, unknown, unauthorized }) => {
    const user = userEvent.setup();
    const response = deferred<AccountRedemptionReceipt>();
    const redeem = vi.fn(() => response.promise);
    const membership = createMembership({ redeem });
    const replacementAuthorize = vi.fn(async () => true);
    const onUnauthorized = vi.fn();
    const panel = renderPanel({ membership, onUnauthorized });
    await user.type(screen.getByRole("textbox", { name: "兑换码" }), "sent-before-callback-change");
    await user.click(screen.getByRole("button", { name: "兑换" }));
    await waitFor(() => expect(redeem).toHaveBeenCalledTimes(1));

    panel.rerender(<AccountMembershipPanel {...panel.props} authorizeRedeem={replacementAuthorize} />);
    await settle(response);

    expect(await screen.findByText(expected)).toBeInTheDocument();
    expect(redeem).toHaveBeenCalledTimes(1);
    expect(onUnauthorized).toHaveBeenCalledTimes(unauthorized ? 1 : 0);
    expect(replacementAuthorize).not.toHaveBeenCalled();
    if (unknown) {
      expect(screen.getByRole("textbox", { name: "兑换码" })).toHaveAttribute("readonly");
      await user.click(screen.getByRole("button", { name: "放弃核对并清除本次输入" }));
    }
  });

  it("does not abandon in-flight public catalog reads when the authorization callback changes", async () => {
    const config = deferred<PublicSiteConfig>();
    const membershipPackagesResponse = deferred<PurchasePackage[]>();
    const creditsPackagesResponse = deferred<PurchasePackage[]>();
    const membership = createMembership({
      getPublicConfig: vi.fn(() => config.promise),
      listPurchasePackages: vi.fn((category: "membership" | "credits") => (
        category === "membership" ? membershipPackagesResponse.promise : creditsPackagesResponse.promise
      )),
    });
    const panel = renderPanel({ membership });
    panel.rerender(<AccountMembershipPanel {...panel.props} authorizeRedeem={vi.fn(async () => true)} />);

    await act(async () => {
      config.resolve(publicConfig);
      membershipPackagesResponse.resolve(membershipPackages);
      creditsPackagesResponse.resolve(creditPackages);
    });

    expect(await screen.findByText(/权益一\s+权益二/)).toBeInTheDocument();
    expect(screen.getByText("长期套餐")).toBeInTheDocument();
    expect(screen.getByText("积分包")).toBeInTheDocument();
    expect(screen.queryByText(/正在读取/)).not.toBeInTheDocument();
  });

  it("keeps confirmed account reads alive when the authorization callback changes after the POST", async () => {
    const user = userEvent.setup();
    const response = deferred<AccountRedemptionReceipt>();
    const freshUser = { ...baseUser, membership_type: "premium" as const, membership_expires_at: "2099-01-02T03:04:05" };
    const refreshCurrentUser = vi.fn(async () => freshUser);
    const profile = createProfile(99);
    const membership = createMembership({ redeem: vi.fn(() => response.promise) });
    const panel = renderPanel({ membership, profile, refreshCurrentUser });
    await user.type(screen.getByRole("textbox", { name: "兑换码" }), "confirmed-read-owner");
    await user.click(screen.getByRole("button", { name: "兑换" }));
    await waitFor(() => expect(membership.redeem).toHaveBeenCalledTimes(1));
    panel.rerender(<AccountMembershipPanel {...panel.props} authorizeRedeem={vi.fn(async () => true)} />);

    await act(async () => response.resolve({ message: "读回确认", type: "credits", value: 1, detail: null }));

    expect(await screen.findByText("兑换已确认；账户资料与积分余额已分别重新读取。")).toBeInTheDocument();
    expect(await screen.findByText("99", { exact: true })).toBeInTheDocument();
    expect(refreshCurrentUser).toHaveBeenCalledTimes(1);
    expect(profile.getCredits).toHaveBeenCalledTimes(1);
    expect(membership.redeem).toHaveBeenCalledTimes(1);
  });

  it("ignores an old session's late 401 after the user scope changes", async () => {
    const userEventApi = userEvent.setup();
    const response = deferred<AccountRedemptionReceipt>();
    const membership = createMembership({ redeem: vi.fn(() => response.promise) });
    const onUnauthorized = vi.fn();
    const first = renderPanel({ membership, onUnauthorized });
    await userEventApi.type(screen.getByRole("textbox", { name: "兑换码" }), "pending-code");
    await userEventApi.click(screen.getByRole("button", { name: "兑换" }));
    await waitFor(() => expect(membership.redeem).toHaveBeenCalledTimes(1));

    const nextUser = { ...baseUser, id: "account-user-2", username: "新账号" };
    first.rerender(<AccountMembershipPanel {...first.props} user={nextUser} sessionGeneration={2} />);
    await act(async () => response.reject(new ApiError("http", "expired token", 401)));

    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(screen.getByText("新账号", { exact: true })).toBeInTheDocument();
    expect(screen.queryByText(/登录状态已失效/)).not.toBeInTheDocument();
    expect(membership.redeem).toHaveBeenCalledTimes(1);
  });
});
