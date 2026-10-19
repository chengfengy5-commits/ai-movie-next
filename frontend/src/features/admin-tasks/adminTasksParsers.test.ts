import { describe, expect, it } from "vitest";
import { parseAdminTaskPage, parseAdminTaskRecord } from "./adminTasksParsers";

function task(overrides: Record<string, unknown> = {}) {
  return {
    id: "task-1",
    user_id: "writer-1",
    type: "future-task-type",
    status: "queued-for-review",
    credit_cost: 0,
    message_id: null,
    model_name: "",
    created_at: null,
    updated_at: "2026-10-16T08:00:00",
    ...overrides,
  };
}

describe("admin task parsers", () => {
  it("keeps unknown string statuses, zero cost, empty model, and nullable dates", () => {
    expect(parseAdminTaskRecord(task())).toEqual(task());
    expect(parseAdminTaskPage({ data: [task()], total: 1 })).toEqual({ data: [task()], total: 1 });
  });

  it.each([
    [task({ credit_cost: "0" })],
    [task({ credit_cost: 1.5 })],
    [task({ type: null })],
    [task({ status: 1 })],
    [task({ message_id: 0 })],
    [task({ created_at: 0 })],
    [task({ extra: true })],
  ])("rejects a task row outside the strict nine-field DTO", (value) => {
    expect(() => parseAdminTaskRecord(value)).toThrow();
  });

  it.each([
    [{ data: [], total: -1 }],
    [{ data: [], total: 1.2 }],
    [{ data: [task()], total: 1, hidden: [] }],
    [{ data: [task({ credit_cost: null })], total: 1 }],
  ])("rejects malformed page envelopes and rows", (value) => {
    expect(() => parseAdminTaskPage(value)).toThrow();
  });
});
