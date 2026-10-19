import { afterEach, describe, expect, it, vi } from "vitest";
import { downloadAdminSeriesCsv, serializeAdminSeriesCsv } from "./adminSeriesExport";
import type { AdminSeriesRecord } from "./adminSeriesTypes";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function record(overrides: Partial<AdminSeriesRecord> = {}): AdminSeriesRecord {
  return {
    id: "series-1",
    user_id: "writer-1",
    name: "剧集,\"一\"\r\n换行",
    description: null,
    image_url: "\uFEFF =1+1",
    created_at: "2026-10-16T08:00:00",
    updated_at: "2026-10-16T08:30:00",
    ...overrides,
  };
}

describe("admin series CSV export", () => {
  it("writes only the seven named fields with BOM, CRLF, quoting, nulls, and formula protection", () => {
    const csv = serializeAdminSeriesCsv([record()]);
    expect(csv.startsWith("\uFEFF")).toBe(true);
    expect(csv.includes("\r\n")).toBe(true);
    expect(csv).toContain('"剧集,\""一\""\r\n换行"');
    expect(csv).toContain('"series-1","writer-1"');
    expect(csv.slice(1).split("\r\n")[0]).toBe('"剧集 ID","用户 ID","名称","简介","封面 URL","创建时间","更新时间"');
    expect(csv).toContain('"\'\uFEFF =1+1"');
    expect(csv).not.toContain("team_id");
    expect(csv.split("\r\n")).toHaveLength(3);
  });

  it("creates and revokes a temporary download URL", () => {
    const createObjectURL = vi.fn(() => "blob:synthetic-series-export");
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectURL });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revokeObjectURL });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    downloadAdminSeriesCsv("\uFEFF\"id\"\r\n\"series-1\"", "series.csv");

    expect(createObjectURL).toHaveBeenCalledOnce();
    expect(click).toHaveBeenCalledOnce();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:synthetic-series-export");
  });
});
