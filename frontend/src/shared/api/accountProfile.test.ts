import { describe, expect, it } from "vitest";
import {
  InvalidAccountProfileResponseError,
  parseAccountCredits,
  parseAccountProfileReceipt,
} from "./accountProfile";

describe("account profile wire contracts", () => {
  it.each([0, 125, -7])("preserves a real integer credit balance (%s)", (credits) => {
    expect(parseAccountCredits({ credits, ignored: "field" })).toEqual({ credits });
  });

  it.each([1.5, Number.NaN, Number.POSITIVE_INFINITY, Number.MAX_SAFE_INTEGER + 1, "12", null])(
    "rejects an invalid credit balance (%s)",
    (credits) => {
      expect(() => parseAccountCredits({ credits })).toThrow(InvalidAccountProfileResponseError);
    },
  );

  it("preserves an empty bio in the partial profile receipt", () => {
    expect(parseAccountProfileReceipt({
      id: "user-1",
      username: "账户用户",
      avatar_url: null,
      bio: "",
      is_superuser: true,
    })).toEqual({
      id: "user-1",
      username: "账户用户",
      avatar_url: null,
      bio: "",
    });
  });

  it.each([
    { id: "user-1", username: "账户用户", avatar_url: null },
    { id: "user-1", username: "账户用户", avatar_url: 8, bio: null },
    { id: " ", username: "账户用户", avatar_url: null, bio: null },
  ])("rejects an incomplete or malformed profile receipt", (receipt) => {
    expect(() => parseAccountProfileReceipt(receipt)).toThrow(InvalidAccountProfileResponseError);
  });
});
