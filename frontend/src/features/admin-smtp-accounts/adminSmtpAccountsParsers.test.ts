import { describe, expect, it } from "vitest";
import { ApiError } from "../../shared/api/errors";
import { parseAdminSmtpAccount, parseAdminSmtpAccountCreateAck, parseAdminSmtpAccountPage } from "./adminSmtpAccountsParsers";

function account(overrides: Record<string, unknown> = {}) {
  return {
    id: "smtp-1",
    host: "smtp.example.test",
    port: 465,
    user: "sender@example.test",
    password: "synthetic-wire-secret-253",
    daily_sent: 0,
    is_active: false,
    created_at: null,
    updated_at: "2026-10-16T10:00:00",
    ...overrides,
  };
}

describe("SMTP account parsers", () => {
  it("validates the nine-field wire record and drops its password before returning metadata", () => {
    const parsed = parseAdminSmtpAccount(account());

    expect(parsed).toEqual({
      id: "smtp-1", host: "smtp.example.test", port: 465, user: "sender@example.test",
      daily_sent: 0, is_active: false, created_at: null, updated_at: "2026-10-16T10:00:00",
    });
    expect(Object.hasOwn(parsed, "password")).toBe(false);
    expect(JSON.stringify(parsed)).not.toContain("synthetic-wire-secret-253");
  });

  it("rejects malformed or partial account rows and inconsistent full-list totals", () => {
    expect(() => parseAdminSmtpAccount(account({ password: null }))).toThrow(ApiError);
    expect(() => parseAdminSmtpAccount({ ...account(), unexpected: true })).toThrow(ApiError);
    expect(() => parseAdminSmtpAccountPage({ data: [account()], total: 2 })).toThrow(ApiError);
  });

  it("accepts only an exact create acknowledgement tied to the submitted mailbox", () => {
    expect(parseAdminSmtpAccountCreateAck({ id: "created-1", user: "sender@example.test", message: "添加成功" }, "sender@example.test"))
      .toEqual({ id: "created-1", user: "sender@example.test", message: "添加成功" });
    expect(() => parseAdminSmtpAccountCreateAck({ id: "created-1", user: "other@example.test", message: "添加成功" }, "sender@example.test"))
      .toThrow(ApiError);
    expect(() => parseAdminSmtpAccountCreateAck({ id: "created-1", user: "sender@example.test", message: "添加成功", password: "secret" }, "sender@example.test"))
      .toThrow(ApiError);
  });
});
