import { describe, expect, it } from "vitest";
import {
  InvalidAccountMembershipResponseError,
  parseAccountRedemptionReceipt,
  parsePublicSiteConfig,
  parsePurchasePackages,
} from "./accountMembership";

const config = {
  site_title: "站点",
  site_tagline: "介绍",
  membership_benefits: "权益一\n权益二",
  tutorial_url: "",
  purchase_qr_code_url: "",
  purchase_contact_info: "",
  storyboard_cost_multiplier: 1,
  payments_enabled: false,
};

describe("account membership wire contracts", () => {
  it("preserves public configuration text and the explicit payment switch", () => {
    expect(parsePublicSiteConfig(config)).toEqual(config);
  });

  it("rejects incomplete public configuration instead of guessing a default", () => {
    const { payments_enabled: _paymentsEnabled, ...incomplete } = config;
    expect(() => parsePublicSiteConfig(incomplete)).toThrow(InvalidAccountMembershipResponseError);
  });

  it("preserves server order, yuan prices, and nullable package fields", () => {
    const packages = [
      { id: "last", category: "credits", name: "点数包", price: 29, credits: 300, days: null, sort_order: 9 },
      { id: "first", category: "credits", name: "基础包", price: 10, credits: null, days: null, sort_order: 1 },
    ];
    expect(parsePurchasePackages(packages, "credits")).toEqual(packages);
  });

  it("accepts an empty directory and rejects a category mismatch", () => {
    expect(parsePurchasePackages([], "membership")).toEqual([]);
    expect(() => parsePurchasePackages([
      { id: "p1", category: "credits", name: "点数包", price: 10, credits: 10, days: null, sort_order: 0 },
    ], "membership")).toThrow(InvalidAccountMembershipResponseError);
  });

  it.each([
    { id: "p1", category: "credits", name: "点数包", price: 10.5, credits: 10, days: null, sort_order: 0 },
    { id: "p1", category: "credits", name: "点数包", price: 10, credits: Number.MAX_SAFE_INTEGER + 1, days: null, sort_order: 0 },
    { id: "p1", category: "credits", name: "点数包", price: 10, credits: 10, days: null },
  ])("rejects malformed package fields", (item) => {
    expect(() => parsePurchasePackages([item], "credits")).toThrow(InvalidAccountMembershipResponseError);
  });

  it("accepts only a complete, typed redemption receipt and preserves nullable detail", () => {
    expect(parseAccountRedemptionReceipt({
      message: "兑换成功",
      type: "credits",
      value: 0,
      detail: null,
      ignored: "field",
    })).toEqual({ message: "兑换成功", type: "credits", value: 0, detail: null });
  });

  it.each([
    { message: " ", type: "credits", value: 1, detail: null },
    { message: "完成", type: "membership", value: 1.5, detail: null },
    { message: "完成", type: "unknown", value: 1, detail: null },
    { message: "完成", type: "credits", value: 1 },
    { message: "完成", type: "credits", value: Number.MAX_SAFE_INTEGER + 1, detail: null },
  ])("rejects a receipt that cannot confirm this redemption", (receipt) => {
    expect(() => parseAccountRedemptionReceipt(receipt)).toThrow(InvalidAccountMembershipResponseError);
  });
});
