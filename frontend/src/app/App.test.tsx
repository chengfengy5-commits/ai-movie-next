// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AccountMembershipServicePort, PublicSiteConfig } from "../shared/api/accountMembership";
import { DEFAULT_SITE_TITLE } from "../features/site-config/PublicSiteConfigProvider";
import { createDemoServices, type WorkspaceServices } from "../shared/api/services";
import { App } from "./App";

describe("App global providers", () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.history.replaceState(null, "", "/admin");
    document.title = DEFAULT_SITE_TITLE;
  });

  afterEach(() => {
    cleanup();
    window.localStorage.clear();
    window.history.replaceState(null, "", "/");
    document.title = DEFAULT_SITE_TITLE;
    delete document.documentElement.dataset.theme;
    document.documentElement.style.colorScheme = "";
  });

  it("mounts the global provider for the admin route and applies the restored mode", async () => {
    window.localStorage.setItem("hao-theme", "dark");
    render(<App services={createDemoServices(window.localStorage)} />);

    expect(screen.getByRole("heading", { name: "管理后台需要本地 API" })).toBeInTheDocument();
    await waitFor(() => expect(document.documentElement).toHaveAttribute("data-theme", "dark"));
    expect(document.documentElement.style.colorScheme).toBe("dark");
  });

  it("applies public site settings before showing the API login entry", async () => {
    window.history.replaceState(null, "", "/");
    const config: PublicSiteConfig = {
      site_title: "公开站点标题",
      site_tagline: "公开站点登录介绍",
      membership_benefits: "会员权益",
      tutorial_url: "https://docs.example.invalid/help",
      purchase_qr_code_url: "",
      purchase_contact_info: "",
      storyboard_cost_multiplier: 1,
      payments_enabled: false,
    };
    const accountMembership: AccountMembershipServicePort = {
      getPublicConfig: vi.fn(async (_signal: AbortSignal) => config),
      listPurchasePackages: vi.fn(async () => []),
      redeem: vi.fn(async () => ({ message: "兑换完成", type: "credits" as const, value: 1, detail: null })),
    };
    const services: WorkspaceServices = {
      ...createDemoServices(window.localStorage),
      mode: "api",
      apiBaseUrl: "http://127.0.0.1:4175",
      accountMembership,
    };

    render(<App services={services} />);

    expect(await screen.findByText(config.site_tagline)).toBeInTheDocument();
    await waitFor(() => expect(document.title).toBe(config.site_title));
    expect(accountMembership.getPublicConfig).toHaveBeenCalledWith(expect.any(AbortSignal));
  });
});
