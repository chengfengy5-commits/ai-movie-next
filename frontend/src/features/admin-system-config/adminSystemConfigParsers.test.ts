import { describe, expect, it } from "vitest";
import { ApiError } from "../../shared/api/errors";
import { parseAdminSystemConfig, parseAdminSystemConfigPage } from "./adminSystemConfigParsers";

function config(overrides: Record<string, unknown> = {}) {
  return {
    id: "config-1",
    muse_token: null,
    site_title: "",
    site_tagline: "",
    storyboard_cost_multiplier: null,
    membership_benefits: "",
    payments_enabled: false,
    tutorial_url: "",
    purchase_qr_code_url: "",
    purchase_contact_info: "",
    team_created_limit: -3,
    team_joined_limit: 0,
    team_member_limit: 20,
    invite_code_ttl_hours: 24,
    chapter_lock_idle_minutes: 15,
    aistarslab_credit_multiplier: -0.25,
    frontend_url: "",
    allowed_origins: "",
    upload_dir: "",
    worker_max_concurrency: "0",
    ...overrides,
  };
}

describe("admin system config strict parsers", () => {
  it("preserves nullable raw fields, empty text, false, zero, and negative values", () => {
    const parsed = parseAdminSystemConfig(config());
    expect(parsed.muse_token).toBeNull();
    expect(parsed.site_title).toBe("");
    expect(parsed.payments_enabled).toBe(false);
    expect(parsed.team_joined_limit).toBe(0);
    expect(parsed.team_created_limit).toBe(-3);
    expect(parsed.aistarslab_credit_multiplier).toBe(-0.25);
    expect(parseAdminSystemConfigPage({ data: [config()], total: 1 }).total).toBe(1);
  });

  it("accepts only the service's 20-field projection and a zero-or-one row list", () => {
    expect(() => parseAdminSystemConfig({ ...config(), oss_access_key_id: "hidden" }))
      .toThrowError(ApiError);
    expect(() => parseAdminSystemConfigPage({ data: [config(), config({ id: "config-2" })], total: 2 }))
      .toThrowError(ApiError);
    expect(parseAdminSystemConfigPage({ data: [], total: 0 })).toEqual({ data: [], total: 0 });
  });

  it("rejects malformed IDs, nullable numeric writes in reads, and unsafe numeric projections", () => {
    expect(() => parseAdminSystemConfig(config({ id: "" }))).toThrowError(ApiError);
    expect(() => parseAdminSystemConfig(config({ team_member_limit: null }))).toThrowError(ApiError);
    expect(() => parseAdminSystemConfig(config({ storyboard_cost_multiplier: 1.5 }))).toThrowError(ApiError);
    expect(() => parseAdminSystemConfig(config({ team_created_limit: Number.MAX_SAFE_INTEGER + 1 })))
      .toThrowError(ApiError);
  });
});
