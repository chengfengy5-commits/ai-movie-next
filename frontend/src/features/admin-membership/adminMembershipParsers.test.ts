import { describe, expect, it } from "vitest";
import { ApiError } from "../../shared/api/errors";
import {
  parseAdminPurchasePackage,
  parseAdminRedemptionCode,
  parseGenerateRedemptionCodesReceipt,
} from "./adminMembershipParsers";

const codeRecord = {
  id: "code-1", code: "SYNTHETIC-ONE", type: "credits", value: 0, is_used: false,
  used_by: null, used_at: null, created_by: "admin-1", expires_at: "legacy date string", created_at: "created",
};

const packageRecord = {
  id: "package-1", category: "legacy-category", name: "Legacy", price: -5,
  credits: 0, days: null, sort_order: 0, is_active: false, created_at: "created", updated_at: "updated",
};

describe("admin membership strict parsers", () => {
  it("preserves nullable, zero, false, negative, legacy category, and date strings", () => {
    expect(parseAdminRedemptionCode({ ...codeRecord, extra_field: "future" })).toEqual(codeRecord);
    expect(parseAdminPurchasePackage(packageRecord)).toEqual(packageRecord);
  });

  it("rejects malformed required record fields", () => {
    expect(() => parseAdminRedemptionCode({ ...codeRecord, is_used: "false" })).toThrow(ApiError);
    expect(() => parseAdminRedemptionCode({ ...codeRecord, value: Number.MAX_SAFE_INTEGER + 1 })).toThrow(ApiError);
    expect(() => parseAdminPurchasePackage({ ...packageRecord, price: 1.2 })).toThrow(ApiError);
    expect(() => parseAdminPurchasePackage({ ...packageRecord, is_active: 0 })).toThrow(ApiError);
  });

  it("accepts a verified first-record receipt and rejects a mismatched count or administrator", () => {
    const payload = { ...codeRecord, _generated_count: 2 };
    expect(parseGenerateRedemptionCodesReceipt(payload, {
      type: "credits", value: 0, count: 2, administratorId: "admin-1",
    })).toEqual({ record: codeRecord, generated_count: 2 });
    expect(() => parseGenerateRedemptionCodesReceipt(payload, {
      type: "credits", value: 0, count: 3, administratorId: "admin-1",
    })).toThrow(/不一致/);
    expect(() => parseGenerateRedemptionCodesReceipt(payload, {
      type: "credits", value: 0, count: 2, administratorId: "admin-2",
    })).toThrow(/不一致/);
  });
});



describe("admin membership empty legacy strings", () => {
  it("preserves empty package name and category strings", () => {
    expect(parseAdminPurchasePackage({ ...packageRecord, name: "", category: "" })).toEqual({
      ...packageRecord, name: "", category: "",
    });
  });
});
