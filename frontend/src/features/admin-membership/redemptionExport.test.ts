import { describe, expect, it } from "vitest";
import { serializePurchasePackagesCsv, serializeRedemptionCodesCsv } from "./redemptionExport";
import type { AdminPurchasePackage, AdminRedemptionCode } from "./adminMembershipTypes";

const code: AdminRedemptionCode = {
  id: "row-1", code: " \uFEFF=1+1", type: "credits", value: -2, is_used: false,
  used_by: "\t@formula", used_at: null, created_by: "admin", expires_at: null, created_at: "created",
};

const packageRecord: AdminPurchasePackage = {
  id: "package-1", category: "legacy", name: "comma, quote \" and\nline", price: -5,
  credits: 0, days: null, sort_order: 0, is_active: false, created_at: "created", updated_at: "updated",
};

describe("membership CSV exports", () => {
  it("quotes all text, protects formula prefixes, keeps numeric negatives, BOM, and CRLF", () => {
    const csv = serializeRedemptionCodesCsv([code]);
    expect(csv.startsWith("\uFEFF")).toBe(true);
    expect(csv).toContain('"\u0027 \uFEFF=1+1"');
    expect(csv).toContain('"\u0027\t@formula"');
    expect(csv).toContain('"-2"');
    expect(csv).toContain("\r\n");

    const packages = serializePurchasePackagesCsv([packageRecord]);
    expect(packages).toContain('"comma, quote "" and\nline"');
    expect(packages).toContain('"-5"');
    expect(packages).toContain('"0"');
  });
});
