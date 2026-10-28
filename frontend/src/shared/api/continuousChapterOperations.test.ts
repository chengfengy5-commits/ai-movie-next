// @vitest-environment node
import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";
import {
  continuousOperationResultDigest,
  continuousSourceSnapshotBinding,
  continuousTargetManifestDigest,
  parseContinuousChapterOperationResponse,
} from "./continuousChapterOperations";

const operationId = "00000000-0000-4000-8000-000000000001";
const editorSessionId = "00000000-0000-4000-8000-000000000002";
const canonicalResponse = JSON.stringify({
  content: [{ storyboard: [] }],
  id: "chapter-a",
  series_id: "series-a",
  unknown: { label: "雪だるま🧊", ratio: 1e-7 },
});

function sha256(value: string): string {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

async function validCommittedResponse() {
  const result = JSON.parse(canonicalResponse) as Record<string, unknown>;
  const sourceBinding = await continuousSourceSnapshotBinding("a".repeat(64));
  const manifestDigest = sha256(JSON.stringify(["source:0"]));
  return {
    receipt: {
      receipt_version: 1,
      operation_id: operationId,
      owner_id: "user-a",
      editor_session_id: editorSessionId,
      series_id: "series-a",
      chapter_id: "chapter-a",
      command_kind: "save-chapter",
      request_body_sha256: "a".repeat(64),
      source_snapshot_digest: sourceBinding,
      target_draft_id: null,
      target_manifest_digest: manifestDigest,
      outcome: "committed",
      http_status: 200,
      result_identity: {
        kind: "save-chapter",
        series_id: "series-a",
        chapter_id: "chapter-a",
        draft_frame_ids: ["source:0"],
        manifest_digest: manifestDigest,
        source_snapshot_digest: sourceBinding,
        frame_storyboard_ids: [[]],
      },
      result,
      canonical_response_json: canonicalResponse,
      result_digest: sha256(canonicalResponse),
      committed_at: "2026-10-28T01:00:00+00:00",
    },
    result,
  };
}

describe("continuous chapter operation receipt protocol", () => {
  it("uses the approved domain-separated source binding bytes", async () => {
    await expect(continuousSourceSnapshotBinding("0".repeat(64))).resolves.toBe(
      "a7f2f0f42d531591f65cf4648baa3811789f950978ec41018c0c1cc8fcfcc948",
    );
  });

  it("hashes the exact compact UTF-8 manifest array and rejects malformed IDs", async () => {
    const ids = ["分镜\n🧊", "source:0"];
    await expect(continuousTargetManifestDigest(ids)).resolves.toBe(sha256(JSON.stringify(ids)));
    await expect(continuousTargetManifestDigest(["bad\ud800"])).rejects.toThrow();
  });

  it("consumes canonical response bytes containing Unicode, exponent numbers, and unknown nested fields", async () => {
    const response = await validCommittedResponse();
    await expect(parseContinuousChapterOperationResponse(response, 200)).resolves.toMatchObject({
      receipt: {
        canonical_response_json: canonicalResponse,
        result_digest: await continuousOperationResultDigest(canonicalResponse),
      },
      result: { unknown: { label: "雪だるま🧊", ratio: 1e-7 } },
    });
  });

  it.each([
    ["committed receipt with a rejected HTTP status", (value: Awaited<ReturnType<typeof validCommittedResponse>>) => {
      value.receipt.http_status = 409;
    }],
    ["wrong target manifest identity", (value: Awaited<ReturnType<typeof validCommittedResponse>>) => {
      value.receipt.result_identity.manifest_digest = "e".repeat(64);
    }],
    ["manifest digest not bound to the frame IDs", (value: Awaited<ReturnType<typeof validCommittedResponse>>) => {
      Object.assign(value.receipt, { target_manifest_digest: "e".repeat(64) });
      value.receipt.result_identity.manifest_digest = "e".repeat(64);
    }],
    ["wrong command kind", (value: Awaited<ReturnType<typeof validCommittedResponse>>) => {
      Object.assign(value.receipt, { command_kind: "create-prop" });
    }],
    ["unexpected save target", (value: Awaited<ReturnType<typeof validCommittedResponse>>) => {
      Object.assign(value.receipt, { target_draft_id: "another-frame" });
    }],
    ["contradictory top-level result", (value: Awaited<ReturnType<typeof validCommittedResponse>>) => {
      value.result = { id: "a different chapter" };
    }],
    ["invalid committed timestamp", (value: Awaited<ReturnType<typeof validCommittedResponse>>) => {
      value.receipt.committed_at = "yesterday";
    }],
    ["out-of-range committed calendar date", (value: Awaited<ReturnType<typeof validCommittedResponse>>) => {
      value.receipt.committed_at = "2026-02-31T00:00:00";
    }],
    ["tampered canonical result bytes", (value: Awaited<ReturnType<typeof validCommittedResponse>>) => {
      value.receipt.canonical_response_json = canonicalResponse + " ";
    }],
    ["canonical bytes that disagree with the persisted result", (value: Awaited<ReturnType<typeof validCommittedResponse>>) => {
      const different = JSON.stringify({ id: "chapter-a", unknown: { label: "雪だるま🧊", ratio: 1e-6 } });
      Object.assign(value.receipt, {
        canonical_response_json: different,
        result_digest: sha256(different),
      });
    }],
    ["foreign result chapter with internally consistent bytes", (value: Awaited<ReturnType<typeof validCommittedResponse>>) => {
      const result = { ...value.receipt.result, id: "chapter-b" };
      const canonical = JSON.stringify(result);
      Object.assign(value.receipt, { result, canonical_response_json: canonical, result_digest: sha256(canonical) });
      Object.assign(value, { result });
    }],
    ["foreign result series with internally consistent bytes", (value: Awaited<ReturnType<typeof validCommittedResponse>>) => {
      const result = { ...value.receipt.result, series_id: "series-b" };
      const canonical = JSON.stringify(result);
      Object.assign(value.receipt, { result, canonical_response_json: canonical, result_digest: sha256(canonical) });
      Object.assign(value, { result });
    }],
  ])("rejects %s", async (_description, mutate) => {
    const response = await validCommittedResponse();
    mutate(response);
    await expect(parseContinuousChapterOperationResponse(response, 200)).rejects.toThrow();
  });

  it("accepts a valid rejected receipt only when its actual HTTP status matches", async () => {
    const canonical = JSON.stringify({ detail: "原来源已变化" });
    const response = await validCommittedResponse();
    Object.assign(response.receipt, {
      command_kind: "create-character",
      target_draft_id: "source:0",
      outcome: "rejected",
      http_status: 409,
      result_identity: {
        kind: "create-character",
        asset_type: "character",
        series_id: "series-a",
        chapter_id: "chapter-a",
        target_draft_id: "source:0",
        draft_frame_ids: ["source:0"],
        manifest_digest: sha256(JSON.stringify(["source:0"])),
      source_snapshot_digest: await continuousSourceSnapshotBinding("a".repeat(64)),
      },
      result: { detail: "原来源已变化" },
      canonical_response_json: canonical,
      result_digest: sha256(canonical),
    });
    delete (response as { result?: unknown }).result;
    Object.assign(response, { error: { detail: "原来源已变化" } });
    await expect(parseContinuousChapterOperationResponse(response, 409)).resolves.toMatchObject({
      receipt: { outcome: "rejected", http_status: 409 },
    });
    await expect(parseContinuousChapterOperationResponse(response, 400)).rejects.toThrow();
  });

  it("requires a committed asset result to retain its original ID and series", async () => {
    const canonical = JSON.stringify({ id: "asset-a", series_id: "series-a" });
    const sourceBinding = await continuousSourceSnapshotBinding("f".repeat(64));
    const manifestDigest = sha256(JSON.stringify(["source:0"]));
    const response = {
      receipt: {
        receipt_version: 1,
        operation_id: "00000000-0000-4000-8000-000000000051",
        owner_id: "user-a",
        editor_session_id: "00000000-0000-4000-8000-000000000052",
        series_id: "series-a",
        chapter_id: "chapter-a",
        command_kind: "create-character",
        request_body_sha256: "f".repeat(64),
        source_snapshot_digest: sourceBinding,
        target_draft_id: "source:0",
        target_manifest_digest: manifestDigest,
        outcome: "committed",
        http_status: 200,
        result_identity: {
          kind: "create-character",
          asset_type: "character",
          series_id: "series-a",
          chapter_id: "chapter-a",
          target_draft_id: "source:0",
          draft_frame_ids: ["source:0"],
          manifest_digest: manifestDigest,
          source_snapshot_digest: sourceBinding,
          asset_id: "asset-a",
        },
        result: { id: "asset-a", series_id: "series-a" },
        canonical_response_json: canonical,
        result_digest: sha256(canonical),
        committed_at: "2026-10-28T01:00:00",
      },
      result: { id: "asset-a", series_id: "series-a" },
    };

    await expect(parseContinuousChapterOperationResponse(response, 200)).resolves.toMatchObject({
      receipt: { outcome: "committed", result_identity: { asset_id: "asset-a" } },
    });
    const foreignResult = { id: "asset-a", series_id: "series-b" };
    const foreignCanonical = JSON.stringify(foreignResult);
    Object.assign(response.receipt, {
      result: foreignResult,
      canonical_response_json: foreignCanonical,
      result_digest: sha256(foreignCanonical),
    });
    Object.assign(response, { result: foreignResult });
    await expect(parseContinuousChapterOperationResponse(response, 200)).rejects.toThrow(/剧集/u);
  });
});
