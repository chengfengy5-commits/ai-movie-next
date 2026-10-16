import { describe, expect, it } from "vitest";
import {
  InvalidAccountSecurityResponseError,
  parseAccountPasswordChangeReceipt,
  parseAccountSessionRevocationReceipt,
  parseAccountSessions,
} from "./accountSecurity";

function session(overrides: Record<string, unknown> = {}) {
  return {
    id: "session-1",
    jti: "internal-jwt-id",
    device_name: "桌面浏览器",
    ip_address: "127.0.0.1",
    user_agent: "Example browser",
    created_at: "2026-10-01T09:00:00",
    last_seen_at: "2026-10-01T10:00:00+08:00",
    revoked_at: null,
    is_active: true,
    is_current: false,
    ...overrides,
  };
}

describe("account security response parsers", () => {
  it("parses a bare ordered array and projects only display fields", () => {
    const result = parseAccountSessions([
      session({ user_agent: "", created_at: null, internal_note: "discard me" }),
      session({ id: "session-2", is_current: true, last_seen_at: null }),
    ]);

    expect(result).toEqual([
      {
        id: "session-1",
        deviceName: "桌面浏览器",
        ipAddress: "127.0.0.1",
        userAgent: "",
        createdAt: null,
        lastSeenAt: "2026-10-01T10:00:00+08:00",
        revokedAt: null,
        isActive: true,
        isCurrent: false,
      },
      {
        id: "session-2",
        deviceName: "桌面浏览器",
        ipAddress: "127.0.0.1",
        userAgent: "Example browser",
        createdAt: "2026-10-01T09:00:00",
        lastSeenAt: null,
        revokedAt: null,
        isActive: true,
        isCurrent: true,
      },
    ]);
    expect(result[0]).not.toHaveProperty("jti");
    expect(result[0]).not.toHaveProperty("internal_note");
  });

  it("accepts an empty list and rejects wrapped, malformed, or ambiguous session data", () => {
    expect(parseAccountSessions([])).toEqual([]);
    expect(() => parseAccountSessions({ data: [] })).toThrow(InvalidAccountSecurityResponseError);
    expect(() => parseAccountSessions([session({ is_current: "false" })]))
      .toThrow(InvalidAccountSecurityResponseError);
    expect(() => parseAccountSessions([session({ last_seen_at: undefined })]))
      .toThrow(InvalidAccountSecurityResponseError);
    expect(() => parseAccountSessions([session(), session()]))
      .toThrow(InvalidAccountSecurityResponseError);
  });

  it("requires an exact target id and confirmed server message for a revoke receipt", () => {
    expect(parseAccountSessionRevocationReceipt(
      { message: "设备已下线", session_id: "session-1", extra: true },
      "session-1",
    )).toEqual({ message: "设备已下线", session_id: "session-1" });
    expect(() => parseAccountSessionRevocationReceipt(
      { message: "设备已下线", session_id: "another-session" },
      "session-1",
    )).toThrow(InvalidAccountSecurityResponseError);
    expect(() => parseAccountSessionRevocationReceipt(
      { message: "请求已受理", session_id: "session-1" },
      "session-1",
    )).toThrow(InvalidAccountSecurityResponseError);
  });

  it("accepts only the known password-change confirmation", () => {
    expect(parseAccountPasswordChangeReceipt({ message: "密码修改成功，请重新登录" }))
      .toEqual({ message: "密码修改成功，请重新登录" });
    expect(() => parseAccountPasswordChangeReceipt({ message: "已受理" }))
      .toThrow(InvalidAccountSecurityResponseError);
  });
});
