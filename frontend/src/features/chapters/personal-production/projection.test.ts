// @vitest-environment node
import { describe, expect, it, vi } from "vitest";
import type { Chapter, StoryboardAsset } from "../../../shared/api/contracts";
import {
  parsePersonalProductionSnapshot,
  type PersonalProductionFrameSnapshot,
} from "../../../shared/api/personalProductionNotes";
import {
  capturePersonalProductionMedia,
  projectPersonalProductionSnapshot,
} from "./projection";
import { digestPersonalProductionMediaIdentity } from "./mediaIdentity";

const chapterId = "chapter-projection";
const seriesId = "series-projection";
const previewUrl = "https://cdn.example.invalid/preview?X-Amz-Signature=temporary&version=2";
const imageUrl = "https://cdn.example.invalid/original?signature=temporary";

function chapter(frames: Chapter["content"] = [{ storyboard: ["frame-a"], preview: previewUrl }]): Chapter {
  return {
    id: chapterId,
    series_id: seriesId,
    title: "投影测试",
    content: frames,
    order: 1,
    created_at: "2026-10-01T00:00:00",
    updated_at: "2026-10-02T00:00:00",
    lock: null,
  };
}

function asset(id = "frame-a", overrides: Partial<StoryboardAsset> = {}): StoryboardAsset {
  return {
    id,
    series_id: seriesId,
    chapter_id: chapterId,
    frame_index: 7,
    name: "不可展示",
    description: null,
    image_url: imageUrl,
    created_at: "2026-10-01T00:00:00",
    updated_at: "2026-10-02T00:00:00",
    ...overrides,
  };
}

function serverFrame(overrides: Partial<PersonalProductionFrameSnapshot> = {}): PersonalProductionFrameSnapshot {
  return {
    frame_index: 0,
    storyboard_asset_id: "frame-a",
    media_revision: 2,
    source_valid: true,
    asset_image_digest: "0".repeat(64),
    preview_digest: "0".repeat(64),
    invalid_reason: null,
    ...overrides,
  };
}

function snapshot(overrides: Record<string, unknown> = {}) {
  return parsePersonalProductionSnapshot({
    chapter_id: chapterId,
    revision: 3,
    media_state: "ready",
    frames: [serverFrame()],
    frame_notes: {},
    resume_frame_id: null,
    ...overrides,
  }, chapterId);
}

async function localCapture(overrides: { chapter?: Chapter; assets?: StoryboardAsset[] } = {}) {
  return capturePersonalProductionMedia(
    overrides.chapter ?? chapter(),
    overrides.assets ?? [asset()],
  );
}

describe("personal production media snapshot projection", () => {
  it("hashes remote URL identities as local strings without fetching or rendering them", async () => {
    const captured = await localCapture();
    expect(captured.state).toBe("ready");
    expect(captured.frames[0]).toMatchObject({
      assetImageUrl: imageUrl,
      previewUrl,
      identityValid: true,
    });
    expect(captured.frames[0]?.assetImageDigest).toBe(
      await digestPersonalProductionMediaIdentity(imageUrl),
    );
    expect(captured.frames[0]?.previewDigest).toBe(
      await digestPersonalProductionMediaIdentity(previewUrl),
    );
  });

  it("refuses missing, repeated and cross-chapter identities without an index fallback", async () => {
    const missing = await localCapture({ chapter: chapter([{ storyboard: [], preview: previewUrl }]) });
    expect(missing.frames[0]?.identityValid).toBe(false);

    const duplicateChapter = chapter([
      { storyboard: ["frame-a"] },
      { storyboard: ["frame-a"] },
    ]);
    const repeated = await localCapture({ chapter: duplicateChapter, assets: [asset()] });
    expect(repeated.frames.map((frame) => frame.identityValid)).toEqual([false, false]);

    const crossChapter = await localCapture({ assets: [asset("frame-a", { chapter_id: "chapter-other" })] });
    expect(crossChapter.frames[0]?.identityValid).toBe(false);
  });

  it("treats duplicate source assets as ambiguous even when one matches the chapter", async () => {
    const captured = await localCapture({ assets: [asset(), asset("frame-a", { chapter_id: "chapter-other" })] });
    expect(captured.frames[0]?.identityValid).toBe(false);
  });

  it("distinguishes empty and unreadable local media states", async () => {
    expect(await localCapture({ chapter: chapter([]) })).toMatchObject({ state: "empty", frames: [] });
    expect(await localCapture({ chapter: chapter(null) })).toMatchObject({ state: "unreadable", frames: [] });
  });

  it("surfaces digest failures without treating source URLs as unreadable data", async () => {
    const failingDigest = vi.fn(async () => { throw new Error("subtle unavailable"); });
    const captured = await capturePersonalProductionMedia(chapter(), [asset()], failingDigest);
    expect(captured.frames[0]).toMatchObject({
      identityValid: true,
      digestAvailable: false,
      assetImageDigest: null,
      previewDigest: null,
    });
    const view = projectPersonalProductionSnapshot(chapter(), captured, snapshot({
      frame_notes: { "frame-a": { status: "approved", note: "旧认可", approved_media_revision: 2 } },
    }));
    expect(view.frames[0]).toMatchObject({ status: "unverifiable", verified: false, note: "旧认可" });
  });

  it("treats an absent note as unmarked but a present null entry as unreadable", async () => {
    const currentChapter = chapter();
    const captured = await localCapture({ chapter: currentChapter });
    const local = captured.frames[0]!;
    const currentSnapshot = (frameNotes: Record<string, unknown>) => snapshot({
      frames: [serverFrame({
        asset_image_digest: local.assetImageDigest,
        preview_digest: local.previewDigest,
      })],
      frame_notes: frameNotes,
    });
    expect(projectPersonalProductionSnapshot(currentChapter, captured, currentSnapshot({}))
      .frames[0]).toMatchObject({ status: "unmarked", hasSavedNote: false, verified: true });
    expect(projectPersonalProductionSnapshot(currentChapter, captured, currentSnapshot({ "frame-a": null }))
      .frames[0]).toMatchObject({ status: "unreadable", hasSavedNote: true, verified: true });
  });

  it("requires a positive media revision and treats a stable identity at another position as a structure mismatch", async () => {
    const currentChapter = chapter([
      { storyboard: ["frame-a"], preview: previewUrl },
      { storyboard: ["frame-b"], preview: null },
    ]);
    const captured = await localCapture({
      chapter: currentChapter,
      assets: [asset("frame-a"), asset("frame-b", { image_url: null })],
    });
    const first = captured.frames[0]!;
    const second = captured.frames[1]!;
    const noRevision = projectPersonalProductionSnapshot(currentChapter, captured, snapshot({
      frames: [serverFrame({ media_revision: null, asset_image_digest: first.assetImageDigest, preview_digest: first.previewDigest }),
        serverFrame({ frame_index: 1, storyboard_asset_id: "frame-b", asset_image_digest: null, preview_digest: null })],
      frame_notes: { "frame-a": { status: "approved", approved_media_revision: 2 } },
    }));
    expect(noRevision.frames[0]?.status).toBe("unverifiable");

    const swappedPositions = projectPersonalProductionSnapshot(currentChapter, captured, snapshot({
      frames: [
        serverFrame({ storyboard_asset_id: "frame-b", asset_image_digest: null, preview_digest: null }),
        serverFrame({ frame_index: 1, storyboard_asset_id: "frame-a", asset_image_digest: first.assetImageDigest, preview_digest: first.previewDigest }),
      ],
      frame_notes: {
        "frame-a": { status: "approved", approved_media_revision: 2 },
        "frame-b": { status: "approved", approved_media_revision: 2 },
      },
    }));
    expect(swappedPositions.mediaState).toBe("mismatch");
    expect(swappedPositions.frames.map((frame) => frame.status)).not.toContain("approved");
    expect(second.storyboardAssetId).toBe("frame-b");
  });

  it("keeps replaced or duplicated server identities out of current notes and surfaces them as orphans", async () => {
    const currentChapter = chapter();
    const captured = await localCapture({ chapter: currentChapter });
    const replaced = projectPersonalProductionSnapshot(currentChapter, captured, snapshot({
      frames: [serverFrame({ storyboard_asset_id: "frame-b" })],
      frame_notes: { "frame-a": { status: "needs_revision", note: "不可绑定的旧备注" } },
    }));
    expect(replaced.mediaState).toBe("mismatch");
    expect(replaced.frames[0]).toMatchObject({ status: "unmarked", hasSavedNote: false });
    expect(replaced.orphanNotes).toHaveLength(1);
    expect(replaced.orphanNotes[0]?.projection).toMatchObject({
      kind: "readable",
      value: { status: "needs_revision", note: "不可绑定的旧备注" },
    });

    const twoFrames = chapter([
      { storyboard: ["frame-a"] },
      { storyboard: ["frame-b"] },
    ]);
    const twoFrameCapture = await localCapture({
      chapter: twoFrames,
      assets: [asset("frame-a"), asset("frame-b")],
    });
    const duplicated = projectPersonalProductionSnapshot(twoFrames, twoFrameCapture, snapshot({
      frames: [
        serverFrame(),
        serverFrame({ frame_index: 1, storyboard_asset_id: "frame-a" }),
      ],
      frame_notes: {
        "frame-a": { status: "needs_revision", note: "重复的服务端ID" },
        "frame-b": { status: "unmarked", note: "服务端快照中缺失" },
      },
    }));
    expect(duplicated.mediaState).toBe("mismatch");
    expect(duplicated.frames.map((frame) => frame.hasSavedNote)).toEqual([false, false]);
    expect(duplicated.orphanNotes).toHaveLength(2);
  });

  it("keeps a structurally matched note associated when its media source is invalid", async () => {
    const currentChapter = chapter();
    const captured = await localCapture({ chapter: currentChapter });
    const local = captured.frames[0]!;
    const result = projectPersonalProductionSnapshot(currentChapter, captured, snapshot({
      frames: [serverFrame({
        source_valid: false,
        asset_image_digest: local.assetImageDigest,
        preview_digest: local.previewDigest,
        invalid_reason: "source_changed",
      })],
      frame_notes: { "frame-a": { status: "needs_revision", note: "仍属于该镜头" } },
    }));
    expect(result.mediaState).toBe("ready");
    expect(result.frames[0]).toMatchObject({
      status: "needs_revision",
      note: "仍属于该镜头",
      hasSavedNote: true,
      verified: false,
    });
    expect(result.orphanNotes).toHaveLength(0);
  });

  it("requires stable identity, source validity and both local digests before showing an approval", async () => {
    const currentChapter = chapter();
    const captured = await localCapture({ chapter: currentChapter });
    const frame = captured.frames[0]!;
    const matched = serverFrame({
      asset_image_digest: frame.assetImageDigest,
      preview_digest: frame.previewDigest,
    });
    const snapshotWithNote = (saved: unknown) => snapshot({
      frames: [matched],
      frame_notes: { "frame-a": saved },
      resume_frame_id: "frame-a",
    });
    const valid = projectPersonalProductionSnapshot(currentChapter, captured, snapshotWithNote({
      status: "approved",
      approved_media_revision: 2,
    }));
    expect(valid.frames[0]).toMatchObject({ status: "approved", verified: true });
    expect(valid.resumePosition).toBe(1);

    const mismatch = projectPersonalProductionSnapshot(currentChapter, captured, snapshot({
      frames: [{ ...matched, asset_image_digest: "f".repeat(64) }],
      frame_notes: { "frame-a": { status: "approved", approved_media_revision: 2 } },
    }));
    expect(mismatch.frames[0]?.status).toBe("unverifiable");

    const staleVersion = projectPersonalProductionSnapshot(currentChapter, captured, snapshot({
      frames: [matched],
      frame_notes: { "frame-a": { status: "approved", approved_media_revision: 1 } },
      resume_frame_id: "frame-a",
    }));
    expect(staleVersion.frames[0]?.status).toBe("needs_reconfirmation");
    expect(staleVersion).toMatchObject({ resumePosition: 1, resumeIsInvalid: false });
  });

  it("does not revive approval after A-to-B-to-A when media revision advances", async () => {
    const captured = await localCapture();
    const local = captured.frames[0]!;
    const readout = projectPersonalProductionSnapshot(chapter(), captured, snapshot({
      frames: [serverFrame({
        media_revision: 4,
        asset_image_digest: local.assetImageDigest,
        preview_digest: local.previewDigest,
      })],
      frame_notes: { "frame-a": { status: "approved", approved_media_revision: 2 } },
    }));
    expect(readout.frames[0]?.status).toBe("needs_reconfirmation");
  });

  it("keeps empty-media approvals pending and old or malformed notes outside current counts", async () => {
    const noMediaChapter = chapter([{ storyboard: ["frame-a"], preview: null }]);
    const captured = await localCapture({ chapter: noMediaChapter, assets: [asset("frame-a", { image_url: null })] });
    const emptyMedia = projectPersonalProductionSnapshot(noMediaChapter, captured, snapshot({
      frames: [serverFrame({ asset_image_digest: null, preview_digest: null })],
      frame_notes: {
        "frame-a": { status: "approved", approved_media_revision: 2 },
        orphan: { status: "unknown", note: "不可信状态" },
      },
    }));
    expect(emptyMedia.frames[0]?.status).toBe("needs_reconfirmation");
    expect(emptyMedia.orphanNotes).toHaveLength(1);
    expect(emptyMedia.orphanNotes[0]?.projection.kind).toBe("unreadable");

    const currentChapter = chapter();
    const verifiedCapture = await localCapture();
    const local = verifiedCapture.frames[0]!;
    const good = snapshot({
      frames: [serverFrame({
        asset_image_digest: local.assetImageDigest,
        preview_digest: local.previewDigest,
      })],
      frame_notes: {
        "frame-a": { status: "needs_revision" },
        former: { note: "仅旧记录" },
      },
    });
    const view = projectPersonalProductionSnapshot(currentChapter, verifiedCapture, good);
    expect(view.frames[0]).toMatchObject({ status: "needs_revision", note: "" });
    expect(view.orphanNotes[0]?.projection.kind).toBe("readable");
  });

  it("detects frame count, position and media-state mismatches and invalid resume targets", async () => {
    const currentChapter = chapter([
      { storyboard: ["frame-a"], preview: previewUrl },
      { storyboard: ["frame-b"], preview: null },
    ]);
    const captured = await localCapture({
      chapter: currentChapter,
      assets: [asset("frame-a"), asset("frame-b", { image_url: null })],
    });
    const mismatch = projectPersonalProductionSnapshot(currentChapter, captured, snapshot({
      frames: [serverFrame()],
      resume_frame_id: "missing-frame",
    }));
    expect(mismatch.mediaState).toBe("mismatch");
    expect(mismatch.resumeIsInvalid).toBe(true);
  });
});
