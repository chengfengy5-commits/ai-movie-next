import { describe, expect, it } from "vitest";
import type { Chapter, StoryboardAsset } from "../../../shared/api/contracts";
import {
  parsePersonalProductionNoteUpdate,
  type PersonalProductionSnapshot,
} from "../../../shared/api/personalProductionNotes";
import type { CapturedPersonalProductionMedia, PersonalProductionReadout } from "./projection";
import {
  createPersonalProductionNoteUpdate,
  resolvePersonalProductionNoteEditCandidate,
  validatePersonalProductionNoteSaveResponse,
} from "./noteEdit";

const digest = "a".repeat(64);
const otherDigest = "b".repeat(64);

function createFixture(options: {
  revision?: number;
  frameCount?: number;
  localDigest?: string | null;
  localPreviewDigest?: string | null;
  serverDigest?: string | null;
  serverPreviewDigest?: string | null;
  sourceValid?: boolean;
  mediaRevision?: number | null;
  note?: unknown;
  saved?: boolean;
  status?: PersonalProductionReadout["frames"][number]["status"];
  verified?: boolean;
} = {}) {
  const frameCount = options.frameCount ?? 1;
  const chapter: Chapter = {
    id: "chapter-one",
    series_id: "series-one",
    title: "第一章",
    content: Array.from({ length: frameCount }, (_, index) => ({
      storyboard: [index === 0 ? "frame-one" : `frame-${index + 1}`],
      preview: "preview-one",
    })),
    order: 1,
    created_at: "2026-10-01T00:00:00",
    updated_at: "2026-10-01T00:00:00",
    lock: null,
  };
  const assets: StoryboardAsset[] = Array.from({ length: frameCount }, (_, index) => ({
    id: index === 0 ? "frame-one" : `frame-${index + 1}`,
    series_id: chapter.series_id,
    chapter_id: chapter.id,
    frame_index: index,
    name: `镜头 ${index + 1}`,
    description: null,
    image_url: "https://media.example.invalid/frame.png",
    created_at: "2026-10-01T00:00:00",
    updated_at: "2026-10-01T00:00:00",
  }));
  const captured: CapturedPersonalProductionMedia = {
    state: "ready",
    frames: Array.from({ length: frameCount }, (_, index) => ({
      frameIndex: index,
      candidateId: index === 0 ? "frame-one" : `frame-${index + 1}`,
      storyboardAssetId: index === 0 ? "frame-one" : `frame-${index + 1}`,
      identityValid: true,
      assetImageUrl: "https://media.example.invalid/frame.png",
      previewUrl: "preview-one",
      assetImageDigest: options.localDigest === undefined ? digest : options.localDigest,
      previewDigest: options.localPreviewDigest === undefined ? digest : options.localPreviewDigest,
      digestAvailable: true,
    })),
  };
  const snapshot: PersonalProductionSnapshot = {
    chapter_id: chapter.id,
    revision: options.revision ?? 3,
    media_state: "ready",
    frames: Array.from({ length: frameCount }, (_, index) => ({
      frame_index: index,
      storyboard_asset_id: index === 0 ? "frame-one" : `frame-${index + 1}`,
      media_revision: index === 0
        ? options.mediaRevision === undefined ? 2 : options.mediaRevision
        : 1,
      source_valid: options.sourceValid ?? true,
      asset_image_digest: options.serverDigest === undefined ? digest : options.serverDigest,
      preview_digest: options.serverPreviewDigest === undefined ? digest : options.serverPreviewDigest,
      invalid_reason: null,
    })),
    frame_notes: new Map<string, unknown>(options.saved === false ? [] : [
      ["frame-one", options.note ?? {
        status: options.status === "needs_reconfirmation" ? "approved" : options.status ?? "needs_revision",
        note: "已有备注",
        ...(options.status === "needs_reconfirmation"
          ? { approved_media_revision: 1, needs_reconfirmation: true }
          : {}),
      }],
      ["retired-frame", { status: "needs_revision", note: "保留的旧记录", extra: { version: 4 } }],
    ]),
    resume_frame_id: "frame-one",
  };
  const saved = options.saved ?? true;
  const readout: PersonalProductionReadout = {
    revision: snapshot.revision,
    noSavedRecord: !saved && snapshot.revision === 0,
    mediaState: "ready",
    frames: Array.from({ length: frameCount }, (_, index) => ({
      position: index + 1,
      status: index === 0 ? options.status ?? "needs_revision" : "unmarked",
      note: index === 0 ? (options.saved === false ? "" : "已有备注") : "",
      hasSavedNote: index === 0 ? saved : false,
      verified: index === 0 ? options.verified ?? true : true,
    })),
    orphanNotes: [],
    resumePosition: 1,
    resumeIsInvalid: false,
  };
  return { chapter, assets, captured, snapshot, readout };
}

function candidateFor(options: Parameters<typeof createFixture>[0] = {}) {
  const fixture = createFixture(options);
  const candidate = resolvePersonalProductionNoteEditCandidate({
    ...fixture,
    row: fixture.readout.frames[0]!,
    mediaSnapshotAvailable: true,
  });
  return { ...fixture, candidate };
}

describe("personal production note edit projection", () => {
  it("allows an explicit first note at revision zero without changing the read-only projection", () => {
    const { candidate } = candidateFor({ revision: 0, saved: false, status: "unmarked" });

    expect(candidate).not.toBeNull();
    expect(candidate?.initialStatus).toBe("unmarked");
    expect(candidate?.approvalEligible).toBe(true);
    expect(createPersonalProductionNoteUpdate(candidate!, "needs_revision", "新备注")).toEqual({
      expected_revision: 0,
      frames: [{
        storyboard_asset_id: "frame-one",
        expected_media_revision: 2,
        status: "needs_revision",
        note: "新备注",
      }],
    });
  });

  it("requires an explicit choice for an expired approval while still allowing reapproval", () => {
    const { candidate } = candidateFor({ status: "needs_reconfirmation" });

    expect(candidate?.approvalEligible).toBe(true);
    expect(candidate?.initialStatus).toBeNull();
    expect(createPersonalProductionNoteUpdate(candidate!, "approved", "重新核对").frames[0]?.status)
      .toBe("approved");
  });

  it("allows ordinary notes when digests differ but blocks approval", () => {
    const { candidate } = candidateFor({
      status: "needs_revision",
      verified: false,
      localDigest: otherDigest,
    });

    expect(candidate?.approvalEligible).toBe(false);
    expect(createPersonalProductionNoteUpdate(candidate!, "needs_revision", "保留文字").frames[0]?.note)
      .toBe("保留文字");
    expect(() => createPersonalProductionNoteUpdate(candidate!, "approved", "不能认可"))
      .toThrow("不能保存为认可状态");
  });

  it("does not allow approval when both captured media digests are empty", () => {
    const { candidate } = candidateFor({
      localDigest: null,
      localPreviewDigest: null,
      serverDigest: null,
      serverPreviewDigest: null,
    });

    expect(candidate?.approvalEligible).toBe(false);
    expect(() => createPersonalProductionNoteUpdate(candidate!, "approved", "不能认可"))
      .toThrow("不能保存为认可状态");
    expect(createPersonalProductionNoteUpdate(candidate!, "needs_revision", "仍可保存备注").frames[0]?.note)
      .toBe("仍可保存备注");
  });

  it.each(["asset_image_digest", "preview_digest"] as const)(
    "rejects an approved response whose %s changes at the same media revision",
    (changedDigest) => {
      const { candidate } = candidateFor();
      const update = createPersonalProductionNoteUpdate(candidate!, "approved", "重新核对");
      const original = candidate!.snapshot;
      const response: PersonalProductionSnapshot = {
        ...original,
        revision: original.revision + 1,
        frames: original.frames.map((frame) => frame.frame_index === candidate!.frameIndex
          ? { ...frame, [changedDigest]: otherDigest }
          : { ...frame }),
        frame_notes: new Map([
          ...original.frame_notes,
          [candidate!.storyboardAssetId, {
            status: "approved",
            note: "重新核对",
            approved_media_revision: candidate!.expectedMediaRevision,
            needs_reconfirmation: false,
          }],
        ]),
      };

      expect(response.frames[candidate!.frameIndex]?.media_revision).toBe(candidate!.expectedMediaRevision);
      expect(() => validatePersonalProductionNoteSaveResponse({ candidate: candidate!, update, value: response }))
        .toThrow("无法与保存前媒体核对结果一致");
    },
  );

  it("counts the complete asset response before accepting same-chapter ownership", () => {
    const fixture = createFixture();
    fixture.assets.push({ ...fixture.assets[0]!, series_id: "another-series", chapter_id: "another-chapter" });

    const candidate = resolvePersonalProductionNoteEditCandidate({
      ...fixture,
      row: fixture.readout.frames[0]!,
      mediaSnapshotAvailable: true,
    });

    expect(candidate).toBeNull();
  });

  it.each([
    { label: "source_valid=false", sourceValid: false },
    { label: "missing media revision", mediaRevision: null },
    { label: "unreadable raw note", note: { status: "unknown", note: "坏记录" }, status: "unreadable" as const },
  ])("does not expose an editor candidate for $label", (options) => {
    const { candidate } = candidateFor(options);

    expect(candidate).toBeNull();
  });

  it("rejects duplicate raw identities in the chapter even when capture claims a unique row", () => {
    const fixture = createFixture({ frameCount: 2 });
    fixture.chapter.content![1] = { storyboard: ["frame-one"], preview: "preview-one" };
    fixture.captured.frames[1] = { ...fixture.captured.frames[1]!, candidateId: "frame-one", storyboardAssetId: "frame-one" };
    fixture.snapshot.frames[1] = { ...fixture.snapshot.frames[1]!, storyboard_asset_id: "frame-one" };

    const candidate = resolvePersonalProductionNoteEditCandidate({
      ...fixture,
      row: fixture.readout.frames[0]!,
      mediaSnapshotAvailable: true,
    });

    expect(candidate).toBeNull();
  });

  it("validates a parsed Map snapshot response and preserves other note records", () => {
    const { candidate, snapshot } = candidateFor({ status: "needs_revision" });
    const update = createPersonalProductionNoteUpdate(candidate!, "needs_revision", "更新备注");
    const response: PersonalProductionSnapshot = {
      ...snapshot,
      revision: snapshot.revision + 1,
      frames: snapshot.frames.map((frame) => ({ ...frame })),
      frame_notes: new Map([
        ["frame-one", { status: "needs_revision", note: "更新备注", audit: { source: "server" } }],
        ["retired-frame", { status: "needs_revision", note: "旧记录", extra: { version: 4 } }],
      ]),
    };

    const next = validatePersonalProductionNoteSaveResponse({ candidate: candidate!, update, value: response });

    expect(next.frame_notes.get("frame-one")).toEqual({
      status: "needs_revision",
      note: "更新备注",
      audit: { source: "server" },
    });
    expect(next.frame_notes.get("retired-frame")).toEqual({
      status: "needs_revision",
      note: "旧记录",
      extra: { version: 4 },
    });
  });

  it.each(["chapter", "revision", "target", "status", "note", "approval metadata"] as const)(
    "rejects a successful response with incorrect %s",
    (failure) => {
      const { candidate } = candidateFor();
      const update = createPersonalProductionNoteUpdate(
        candidate!,
        failure === "approval metadata" ? "approved" : "needs_revision",
        "本次备注",
      );
      const original = candidate!.snapshot;
      const nextNotes = new Map(original.frame_notes);
      const savedStatus = update.frames[0].status;
      nextNotes.set(candidate!.storyboardAssetId, {
        status: failure === "status" ? "unmarked" : savedStatus,
        note: failure === "note" ? "另一段文字" : update.frames[0].note,
        approved_media_revision: failure === "approval metadata"
          ? candidate!.expectedMediaRevision + 1
          : savedStatus === "approved" ? candidate!.expectedMediaRevision : null,
        needs_reconfirmation: false,
      });
      const response: PersonalProductionSnapshot = {
        ...original,
        chapter_id: failure === "chapter" ? "other-chapter" : original.chapter_id,
        revision: original.revision + (failure === "revision" ? 2 : 1),
        frames: original.frames.map((frame) => frame.frame_index === candidate!.frameIndex
          ? { ...frame, storyboard_asset_id: failure === "target" ? "other-frame" : frame.storyboard_asset_id }
          : { ...frame }),
        frame_notes: nextNotes,
      };

      expect(() => validatePersonalProductionNoteSaveResponse({ candidate: candidate!, update, value: response }))
        .toThrow();
    },
  );

  it("validates own request keys and counts supplementary Unicode code points", () => {
    const note = "🙂".repeat(2_000);
    const valid = {
      expected_revision: 0,
      frames: [{
        storyboard_asset_id: "frame-one",
        expected_media_revision: 1,
        status: "unmarked",
        note,
      }],
    };
    expect(parsePersonalProductionNoteUpdate(valid).frames[0]?.note).toBe(note);
    expect(() => parsePersonalProductionNoteUpdate({ ...valid, extra: true })).toThrow();
    expect(() => parsePersonalProductionNoteUpdate({
      ...valid,
      frames: [{ ...valid.frames[0], note: note + "🙂" }],
    })).toThrow();
  });
});
