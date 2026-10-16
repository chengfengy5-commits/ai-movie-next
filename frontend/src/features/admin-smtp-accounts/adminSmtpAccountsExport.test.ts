import { describe, expect, it } from "vitest";
import { serializeAdminSmtpAccountsCsv } from "./adminSmtpAccountsExport";
import type { AdminSmtpAccountRecord } from "./adminSmtpAccountsTypes";

function record(overrides: Partial<AdminSmtpAccountRecord> = {}): AdminSmtpAccountRecord {
  return {
    id: "smtp-1", host: "=SUM(1,1)", port: 465, user: "sender@example.test", daily_sent: 0,
    is_active: false, created_at: null, updated_at: "2026-10-16T00:00:00", ...overrides,
  };
}

describe("SMTP account CSV export", () => {
  it("exports eight metadata columns with formula protection, quoting, BOM, and CRLF while excluding credentials", () => {
    const csv = serializeAdminSmtpAccountsCsv([record({ user: "\uFEFF+formula@example.test", host: "=1+1" })]);
    expect(csv.startsWith("\uFEFF")).toBe(true);
    expect(csv).toContain("\r\n");
    expect(csv.split("\r\n")[0]?.split(",")).toHaveLength(8);
    expect(csv).toContain("'=1+1");
    expect(csv).toContain("'\uFEFF+formula@example.test");
    expect(csv).not.toMatch(/password|hasPassword|synthetic-wire-secret/i);
  });

  it("limits export serialization to 1000 records", () => {
    const csv = serializeAdminSmtpAccountsCsv(Array.from({ length: 1002 }, (_, index) => record({ id: `smtp-${index}` })));
    expect(csv.split("\r\n")).toHaveLength(1001);
  });
});
