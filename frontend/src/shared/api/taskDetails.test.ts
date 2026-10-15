import { describe, expect, it } from "vitest";
import { InvalidResponseError } from "./contracts";
import {
  parseTaskReceipt,
  parseTaskRequestRecord,
  safeTaskText,
} from "./taskDetails";

function rawReceipt(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: "task-17",
    type: "storyboard.two_pass",
    message_id: null,
    status: "completed",
    result: "{\"frames\":[{\"text\":\"完整镜头全文\"}]}",
    billing_status: null,
    quoted_amount: null,
    progress: 100,
    progress_message: null,
    external_task_id: null,
    external_provider: null,
    created_at: "2026-10-15T08:00:00Z",
    updated_at: "2026-10-15T08:01:00Z",
    ...overrides,
  };
}

function rawRequest(request: unknown): Record<string, unknown> {
  return {
    id: "task-17",
    type: "storyboard.two_pass",
    status: "completed",
    model_name: null,
    credit_cost: 0,
    external_task_id: null,
    external_provider: null,
    created_at: "2026-10-15T08:00:00Z",
    request,
  };
}

describe("task detail parsing", () => {
  it("accepts nullable legacy billing fields and maps the full receipt", () => {
    expect(parseTaskReceipt(rawReceipt(), "task-17")).toMatchObject({
      id: "task-17",
      result: "{\"frames\":[{\"text\":\"完整镜头全文\"}]}",
      billingStatus: null,
      quotedAmount: null,
      progress: 100,
    });
  });

  it.each([
    [{ frames: ["镜头"] }],
    [["镜头"]],
    ["历史纯文本"],
    [12],
    [true],
    [null],
    [{ _raw: "{invalid legacy payload" }],
  ])("preserves an arbitrary historical request payload", (payload) => {
    expect(parseTaskRequestRecord(rawRequest(payload), "task-17").request).toEqual(payload);
  });

  it("rejects a response that belongs to another task", () => {
    expect(() => parseTaskReceipt(rawReceipt({ id: "another-task" }), "task-17"))
      .toThrow(InvalidResponseError);
    expect(() => parseTaskRequestRecord(rawRequest(null), "another-task"))
      .toThrow(InvalidResponseError);
  });
});

describe("safe task text", () => {
  it("hides sensitive fields, credentials, and signed URL query values", () => {
    const text = safeTaskText({
      request: {
        api_key: "private-api-key",
        nested: { Authorization: "Bearer private-bearer" },
        prompt: "Watch https://user:private-password@cdn.example.test/out.mp4?X-Amz-Credential=private-credential&X-Amz-Signature=private-signature&X-Amz-Expires=900",
      },
    });

    expect(text).toContain("[已隐藏]");
    expect(text).toContain("cdn.example.test/out.mp4");
    expect(text).not.toMatch(/private-api-key|private-bearer|private-password|private-credential|private-signature/);
    expect(safeTaskText("https://user:private-password@[bad/out.mp4?api%5Fkey=encoded"))
      .not.toMatch(/private-password|encoded/);
    expect(safeTaskText("https://cdn.example.test/out.mp4?api%5Fkey=private-encoded-key"))
      .not.toContain("private-encoded-key");
  });

  it("redacts credentials in nested JSON and malformed legacy text while keeping ordinary prose", () => {
    const text = safeTaskText({
      _raw: 'broken payload Authorization: Bearer private-bearer, apiKey="private-api-key"',
      nested: '{"token":"private-token","dialogue":"The token falls from the sky."}',
    });

    expect(text).not.toMatch(/private-bearer|private-api-key|private-token/);
    expect(text).toContain("The token falls from the sky.");
    expect(text).toContain("[已隐藏]");
  });

  it("keeps markup inert and bounds large or deeply nested values", () => {
    const markup = safeTaskText("<img src=x onerror=alert(1)>");
    expect(markup).toBe("<img src=x onerror=alert(1)>");
    expect(safeTaskText({ veryLarge: "x".repeat(600_000) })).toContain("内容因长度或复杂度限制已截断");
    const deepValue = Array.from({ length: 14 }, () => "deeper")
      .reduceRight((value: unknown) => ({ deeper: value }), "x");
    expect(safeTaskText(deepValue)).toContain("嵌套过深，已截断");
  });
});
