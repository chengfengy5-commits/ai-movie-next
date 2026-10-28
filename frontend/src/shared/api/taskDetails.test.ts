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

function rawHistoryTruth(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    version: 1,
    source: {
      status: "registered",
      source: "billing_units.operation_and_step_graph_version",
      unit_count: 2,
      registered_unit_count: 2,
      operations: ["image.single", "video.single"],
      conflicts: [],
    },
    worker: {
      status: "expired_lease",
      source: "ai_tasks.worker_lease_and_recovery_status",
      recovery_status: "reconciling",
      freshness: "lease_expired",
      lease_until: "2026-10-15T07:00:00+00:00",
      task_updated_at: "2026-10-15T07:01:00+00:00",
      evaluated_at: "2026-10-15T08:00:00+00:00",
    },
    provider: {
      status: "partial",
      source: "external_submissions_and_linked_result_evidence",
      unit_count: 2,
      verified_unit_count: 1,
      known_failure_unit_count: 0,
      unknown_unit_count: 1,
      unsupported_unit_count: 0,
      units: [
        {
          operation: "image.single",
          graph_version: "image-single-v1",
          status: "unknown",
          unit_result_status: "unknown",
          step_statuses: ["failed"],
          submission_statuses: ["failed"],
          providers: ["openai"],
          provider_evidence_count: 0,
          timing: "unknown",
          timely_evidence_count: 0,
          late_evidence_count: 0,
          unknown_timing_count: 0,
          observed_at: null,
          has_unverified_failed_submission: true,
        },
        {
          operation: "video.single",
          graph_version: "video-single-v1",
          status: "verified_success",
          unit_result_status: "succeeded",
          step_statuses: ["result_observed"],
          submission_statuses: ["completed"],
          providers: ["aistarslab"],
          provider_evidence_count: 1,
          timing: "mixed",
          timely_evidence_count: 1,
          late_evidence_count: 1,
          unknown_timing_count: 0,
          observed_at: "2026-10-15T08:00:00+00:00",
          has_unverified_failed_submission: false,
        },
      ],
    },
    local_disposition: {
      status: "observed",
      source_coverage: ["billing_units"],
      unit_count: 2,
      observations: [
        {
          status: "deadline_elapsed_unsettled",
          source: "billing_units.deadline_at_and_result_status",
          recorded_at: "2026-10-15T07:00:00+00:00",
          reason: null,
          phase: null,
        },
      ],
    },
    billing_scope: {
      unit_count: 2,
      legacy_quote_scope: "first_unit",
      aggregated: false,
    },
    ...overrides,
  };
}

function rawHistoryTruthWithInvalidEvidenceCount(): Record<string, unknown> {
  const truth = rawHistoryTruth();
  const provider = truth.provider as Record<string, unknown>;
  const units = provider.units as Array<Record<string, unknown>>;
  return {
    ...truth,
    provider: {
      ...provider,
      units: units.map((unit, index) => (
        index === 0 ? { ...unit, provider_evidence_count: null } : unit
      )),
    },
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
    const receipt = parseTaskReceipt(rawReceipt(), "task-17");
    expect(receipt).toMatchObject({
      id: "task-17",
      result: "{\"frames\":[{\"text\":\"完整镜头全文\"}]}",
      billingStatus: null,
      quotedAmount: null,
      progress: 100,
    });
    expect(receipt.historyTruth).toMatchObject({
      source: { status: "unsupported" },
      provider: { status: "unsupported" },
    });
  });

  it("parses independent source, worker, provider and local facts without aggregating fees", () => {
    const receipt = parseTaskReceipt(rawReceipt({
      quoted_amount: 0,
      billing_status: "unbilled",
      history_truth: rawHistoryTruth(),
    }), "task-17");

    expect(receipt.quotedAmount).toBe(0);
    expect(receipt.historyTruth).toMatchObject({
      source: { status: "registered", unitCount: 2 },
      worker: { status: "expired_lease", recoveryStatus: "reconciling" },
      provider: {
        status: "partial",
        units: [
          { status: "unknown", hasUnverifiedFailedSubmission: true },
          {
            status: "verified_success",
            timing: "mixed",
            timelyEvidenceCount: 1,
            lateEvidenceCount: 1,
          },
        ],
      },
      localDisposition: { status: "observed" },
      billingScope: { legacyQuoteScope: "first_unit", aggregated: false },
    });
  });

  it("keeps the legacy receipt readable when the history truth version is newer", () => {
    const receipt = parseTaskReceipt(rawReceipt({ history_truth: { version: 2 } }), "task-17");

    expect(receipt.status).toBe("completed");
    expect(receipt.historyTruth.source.status).toBe("unsupported");
  });

  it.each([
    rawHistoryTruth({ source: { status: "failed" } }),
    rawHistoryTruthWithInvalidEvidenceCount(),
  ])("rejects malformed history truth records", (history_truth) => {
    expect(() => parseTaskReceipt(rawReceipt({ history_truth }), "task-17"))
      .toThrow(InvalidResponseError);
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
