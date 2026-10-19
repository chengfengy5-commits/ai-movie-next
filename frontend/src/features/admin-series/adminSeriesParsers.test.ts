import { describe, expect, it } from "vitest";
import { ApiError } from "../../shared/api/errors";
import { parseAdminSeriesPage, parseAdminSeriesRecord } from "./adminSeriesParsers";

function record(overrides: Record<string, unknown> = {}) {
  return {
    id: "series-1",
    user_id: "admin-1",
    name: "剧集",
    description: "简介",
    image_url: null,
    created_at: "2026-10-16T08:00:00",
    updated_at: "2026-10-16T08:30:00",
    ...overrides,
  };
}

describe("admin series parsers", () => {
  it("accepts the exact seven-field record, empty names, raw dates, and nullable values", () => {
    expect(parseAdminSeriesRecord(record({ name: "", description: null, image_url: "" }))).toEqual(
      record({ name: "", description: null, image_url: "" }),
    );
  });

  it.each([
    [{ ...record(), id: "" }],
    [{ ...record(), user_id: "" }],
    [{ ...record(), name: null }],
    [{ ...record(), description: 1 }],
    [{ ...record(), image_url: false }],
    [{ ...record(), created_at: 1 }],
    [{ ...record(), extra_relation: [] }],
    [{ ...record(), updated_at: undefined }],
  ])("rejects malformed or expanded records", (value) => {
    expect(() => parseAdminSeriesRecord(value)).toThrow(ApiError);
  });

  it("validates list data and a nonnegative safe total", () => {
    expect(parseAdminSeriesPage({ data: [record()], total: 1 })).toMatchObject({ total: 1 });
    expect(parseAdminSeriesPage({ data: [], total: 0 })).toEqual({ data: [], total: 0 });
    for (const payload of [
      { data: {}, total: 0 },
      { data: [record()], total: -1 },
      { data: [], total: Number.MAX_SAFE_INTEGER + 1 },
      { data: [record()], total: "1" },
      { data: [record()], total: 1, extra: "expanded" },
    ]) {
      expect(() => parseAdminSeriesPage(payload)).toThrow(ApiError);
    }
  });
});
