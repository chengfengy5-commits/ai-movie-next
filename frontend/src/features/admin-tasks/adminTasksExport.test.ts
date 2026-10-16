import { describe, expect, it } from "vitest";
import type { AdminTaskRecord } from "./adminTasksTypes";
import { serializeAdminTasksCsv } from "./adminTasksExport";

describe("admin task CSV", () => {
  it("exports all nine allowlisted fields as BOM/CRLF and neutralizes formulas in text only", () => {
    const record: AdminTaskRecord = {
      id: "\uFEFF=2+2",
      user_id: "writer,\"one\"",
      type: "chat",
      status: "-queued",
      credit_cost: 0,
      message_id: null,
      model_name: " @formula",
      created_at: null,
      updated_at: "2026-10-16T08:00:00",
    };
    const csv = serializeAdminTasksCsv([record]);
    expect(csv.startsWith("\uFEFF")).toBe(true);
    expect(csv).toContain("\"'\uFEFF=2+2\"");
    expect(csv).toContain('"writer,""one"""');
    expect(csv).toContain(",0,");
    expect(csv).toContain("\"' @formula\"");
    expect(csv).toContain("\r\n");
    expect(csv.split("\r\n")[0]?.split(",")).toHaveLength(9);
    expect(csv).not.toContain("request_data");
    expect(csv).not.toContain("result");
    expect(csv).not.toContain("password");
  });
});
