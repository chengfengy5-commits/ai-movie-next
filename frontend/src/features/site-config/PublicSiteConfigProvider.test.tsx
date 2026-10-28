// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AccountMembershipServicePort, PublicSiteConfig } from "../../shared/api/accountMembership";
import { AuthProvider } from "../auth/AuthProvider";
import { AppearanceProvider } from "../appearance/ThemeProvider";
import { createDemoServices, type WorkspaceServices } from "../../shared/api/services";
import { Workspace } from "../../app/Workspace";
import {
  APPLICATION_HOME_HREF,
  DEFAULT_SITE_TAGLINE,
  DEFAULT_SITE_TITLE,
  PublicSiteConfigProvider,
  PublicSiteTutorialLink,
  type PublicSiteConfigSource,
  usePublicSiteConfig,
} from "./PublicSiteConfigProvider";

const publicConfig: PublicSiteConfig = {
  site_title: "HaoAI 测试站",
  site_tagline: "一条来自服务的登录介绍。",
  membership_benefits: "会员权益",
  tutorial_url: "https://docs.example.invalid/guide?from=site",
  purchase_qr_code_url: "",
  purchase_contact_info: "",
  storyboard_cost_multiplier: 1,
  payments_enabled: false,
};

function source(
  getPublicConfig: (signal: AbortSignal) => Promise<PublicSiteConfig>,
  mode: "api" | "demo" = "api",
): PublicSiteConfigSource {
  return { mode, accountMembership: { getPublicConfig } };
}

function PresentationProbe() {
  const presentation = usePublicSiteConfig();
  return (
    <>
      <p>{presentation.siteTagline}</p>
      <PublicSiteTutorialLink />
    </>
  );
}

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  window.history.replaceState(null, "", "/");
  document.title = DEFAULT_SITE_TITLE;
});

describe("public site configuration host", () => {
  it("loads anonymous API settings for the document title, login copy, and tutorial link", async () => {
    const getPublicConfig = vi.fn(async (_signal: AbortSignal) => publicConfig);
    render(
      <PublicSiteConfigProvider services={source(getPublicConfig)}>
        <PresentationProbe />
      </PublicSiteConfigProvider>,
    );

    expect(await screen.findByText(publicConfig.site_tagline)).toBeInTheDocument();
    await waitFor(() => expect(document.title).toBe(publicConfig.site_title));
    const tutorial = screen.getByRole("link", { name: "查看使用教程" });
    expect(tutorial).toHaveAttribute("href", publicConfig.tutorial_url);
    expect(tutorial).toHaveAttribute("target", "_blank");
    expect(tutorial).toHaveAttribute("rel", "noopener noreferrer");
    expect(getPublicConfig).toHaveBeenCalledWith(expect.any(AbortSignal));
  });

  it("keeps the new service owner and ignores a late response from the previous one", async () => {
    let resolveOld: ((value: PublicSiteConfig) => void) | undefined;
    let oldSignal: AbortSignal | undefined;
    const oldService = source((signal) => {
      oldSignal = signal;
      return new Promise((resolve) => { resolveOld = resolve; });
    });
    const newService = source(async () => ({ ...publicConfig, site_title: "新站点", site_tagline: "新标语" }));
    const view = render(
      <PublicSiteConfigProvider services={oldService}>
        <PresentationProbe />
      </PublicSiteConfigProvider>,
    );
    await waitFor(() => expect(oldSignal).toBeInstanceOf(AbortSignal));

    view.rerender(
      <PublicSiteConfigProvider services={newService}>
        <PresentationProbe />
      </PublicSiteConfigProvider>,
    );
    expect(await screen.findByText("新标语")).toBeInTheDocument();
    await waitFor(() => expect(document.title).toBe("新站点"));
    expect(oldSignal?.aborted).toBe(true);

    await act(async () => {
      resolveOld?.({ ...publicConfig, site_title: "旧站点迟到结果", site_tagline: "旧标语" });
    });
    expect(document.title).toBe("新站点");
    expect(screen.getByText("新标语")).toBeInTheDocument();
    expect(screen.queryByText("旧标语")).not.toBeInTheDocument();
  });

  it("uses the default title for a blank value and falls back from an unsafe tutorial URL", async () => {
    const getPublicConfig = vi.fn(async () => ({
      ...publicConfig,
      site_title: "   ",
      site_tagline: "这条标语证明配置响应已处理。",
      tutorial_url: "javascript:alert(1)",
    }));
    render(
      <PublicSiteConfigProvider services={source(getPublicConfig)}>
        <PresentationProbe />
      </PublicSiteConfigProvider>,
    );

    expect(await screen.findByText("这条标语证明配置响应已处理。")).toBeInTheDocument();
    await waitFor(() => expect(document.title).toBe(DEFAULT_SITE_TITLE));
    expect(screen.queryByText(DEFAULT_SITE_TAGLINE)).not.toBeInTheDocument();
    const tutorial = screen.getByRole("link", { name: "查看使用教程" });
    expect(tutorial).toHaveAttribute("href", APPLICATION_HOME_HREF);
    expect(tutorial).not.toHaveAttribute("target");
    expect(tutorial).not.toHaveAttribute("rel");
  });

  it("does not request public settings in explicit demo mode", async () => {
    const getPublicConfig = vi.fn(async () => publicConfig);
    render(
      <PublicSiteConfigProvider services={source(getPublicConfig, "demo")}>
        <PresentationProbe />
      </PublicSiteConfigProvider>,
    );

    expect(screen.getByText(DEFAULT_SITE_TAGLINE)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "查看使用教程" })).toHaveAttribute("href", APPLICATION_HOME_HREF);
    expect(getPublicConfig).not.toHaveBeenCalled();
  });

  it("shows the configured tutorial link in the authenticated workspace sidebar", async () => {
    const user = userEvent.setup();
    window.localStorage.clear();
    const accountMembership: AccountMembershipServicePort = {
      getPublicConfig: vi.fn(async (_signal: AbortSignal) => publicConfig),
      listPurchasePackages: vi.fn(async () => []),
      redeem: vi.fn(async () => ({ message: "兑换完成", type: "credits" as const, value: 1, detail: null })),
    };
    const services: WorkspaceServices = {
      ...createDemoServices(window.localStorage),
      mode: "api",
      apiBaseUrl: "http://127.0.0.1:4175",
      accountMembership,
    };

    render(
      <AppearanceProvider>
        <PublicSiteConfigProvider services={services}>
          <AuthProvider services={services}>
            <Workspace />
          </AuthProvider>
        </PublicSiteConfigProvider>
      </AppearanceProvider>,
    );
    await screen.findByRole("heading", { name: "欢迎回来" });
    await user.type(screen.getByLabelText("账号"), "demo");
    await user.type(screen.getByLabelText("密码"), "demo123");
    await user.click(screen.getByRole("button", { name: "登录工作台" }));

    const tutorial = await screen.findByRole("link", { name: "查看使用教程" });
    expect(tutorial).toHaveAttribute("href", publicConfig.tutorial_url);
    expect(tutorial).toHaveAttribute("target", "_blank");
    expect(tutorial.parentElement).toHaveClass("workspace-appearance");
  });
});
