import { describe, expect, it } from "vitest";
import type { Chapter, StoryboardAsset } from "../../../shared/api/contracts";
import {
  InvalidResponseError,
} from "../../../shared/api/contracts";
import {
  parsePersonalProductionResumeUpdate,
  parsePersonalProductionSnapshot,
  type PersonalProductionSnapshot,
} from "../../../shared/api/personalProductionNotes";
import type {
  CapturedPersonalProductionMedia,
  PersonalProductionReadout,
} from "./projection";
import {
  canClearPersonalProductionResume,
  createPersonalProductionResumeUpdate,
  resolvePersonalProductionResumeCandidates,
  validatePersonalProductionResumeSaveResponse,
} from "./resumeEdit";

function createFixture(options: {
  revision?: number;
  mediaState?: "ready" | "empty" | "unreadable";
  resumeFrameId?: string | null;
  sourceValid?: boolean;
  verified?: boolean;
  localDigest?: string | null;
  serverDigest?: string | null;
} = {}) {
  const chapter: Chapter = {
    id: "chapter-resume",
    series_id: "series-resume",
    title: "续作测试",
    content: [
      { storyboard: ["frame-one"], preview: null },
      { storyboard: ["frame-two"], preview: null },
    ],
    order: 1,
    created_at: "2026-10-01T00:00:00",
    updated_at: "2026-10-01T00:00:00",
    lock: null,
  };
  const assets: StoryboardAsset[] = chapter.content!.map((_, index) => ({
    id: index === 0 ? "frame-one" : "frame-two",
    series_id: chapter.series_id,
    chapter_id: chapter.id,
    frame_index: index,
    name: "素材",
    description: null,
    image_url: null,
    created_at: "2026-10-01T00:00:00",
    updated_at: "2026-10-01T00:00:00",
  }));
  const mediaState = options.mediaState ?? "ready";
  const frames = mediaState === "ready"
    ? [
        {
          frame_index: 0,
          storyboard_asset_id: "frame-one",
          media_revision: null,
          source_valid: options.sourceValid ?? true,
          asset_image_digest: options.serverDigest ?? null,
          preview_digest: null,
          invalid_reason: null,
        },
        {
          frame_index: 1,
          storyboard_asset_id: "frame-two",
          media_revision: null,
          source_valid: true,
          asset_image_digest: null,
          preview_digest: null,
          invalid_reason: null,
        },
      ]
    : [];
  const snapshot = parsePersonalProductionSnapshot({
    chapter_id: chapter.id,
    revision: options.revision ?? 0,
    media_state: mediaState,
    frames,
    frame_notes: {},
    resume_frame_id: options.resumeFrameId ?? null,
  }, chapter.id);
  const captured: CapturedPersonalProductionMedia = {
    state: mediaState,
    frames: mediaState === "ready"
      ? [
          {
            frameIndex: 0,
            candidateId: "frame-one",
            storyboardAssetId: "frame-one",
            identityValid: true,
            assetImageUrl: null,
            previewUrl: null,
            assetImageDigest: options.localDigest ?? null,
            previewDigest: null,
            digestAvailable: true,
          },
          {
            frameIndex: 1,
            candidateId: "frame-two",
            storyboardAssetId: "frame-two",
            identityValid: true,
            assetImageUrl: null,
            previewUrl: null,
            assetImageDigest: null,
            previewDigest: null,
            digestAvailable: true,
          },
        ]
      : [],
  };
  const readout: PersonalProductionReadout = {
    revision: snapshot.revision,
    noSavedRecord: snapshot.revision === 0,
    mediaState: mediaState === "ready" ? "ready" : mediaState,
    frames: mediaState === "ready"
      ? [
          { position: 1, status: "unverifiable", note: "", hasSavedNote: false, verified: options.verified ?? false },
          { position: 2, status: "unmarked", note: "", hasSavedNote: false, verified: false },
        ]
      : [],
    orphanNotes: [],
    resumePosition: null,
    resumeIsInvalid: options.resumeFrameId !== null && options.resumeFrameId !== undefined,
  };
  return { chapter, assets, captured, snapshot, readout };
}

function candidatesFor(fixture: ReturnType<typeof createFixture>) {
  return resolvePersonalProductionResumeCandidates({
    ...fixture,
    seriesId: fixture.chapter.series_id,
    mediaSnapshotAvailable: true,
  });
}

describe("personal production resume update contract", () => {
  it("requires exactly two own fields, a safely incrementable revision, and a raw nonblank id", () => {
    const rawId = "  frame-one  ";
    expect(parsePersonalProductionResumeUpdate({
      expected_revision: 0,
      resume_frame_id: rawId,
    })).toEqual({ expected_revision: 0, resume_frame_id: rawId });
    expect(parsePersonalProductionResumeUpdate({
      expected_revision: 4,
      resume_frame_id: null,
    })).toEqual({ expected_revision: 4, resume_frame_id: null });

    for (const value of [
      { expected_revision: 0 },
      { expected_revision: 0, resume_frame_id: null, frames: [] },
      { expected_revision: Number.MAX_SAFE_INTEGER, resume_frame_id: null },
      { expected_revision: -1, resume_frame_id: null },
      { expected_revision: 0, resume_frame_id: "   " },
      { expected_revision: 0, resume_frame_id: "🟦".repeat(37) },
    ]) {
      expect(() => parsePersonalProductionResumeUpdate(value)).toThrow(InvalidResponseError);
    }
    const inherited = Object.assign(Object.create({ resume_frame_id: null }) as object, {
      expected_revision: 0,
    });
    expect(() => parsePersonalProductionResumeUpdate(inherited)).toThrow(InvalidResponseError);
  });
});

describe("personal production resume identity", () => {
  it("allows revision-zero, no-note, digest-mismatched and double-empty targets without approval", () => {
    const fixture = createFixture({
      revision: 0,
      verified: false,
      localDigest: "a".repeat(64),
      serverDigest: "b".repeat(64),
    });
    const candidates = candidatesFor(fixture);

    expect(candidates.map((candidate) => candidate.storyboardAssetId)).toEqual(["frame-one", "frame-two"]);
    expect(candidates[0]?.row.verified).toBe(false);
    expect(createPersonalProductionResumeUpdate(fixture.snapshot, candidates[0] ?? null))
      .toEqual({ expected_revision: 0, resume_frame_id: "frame-one" });
  });

  it("counts all chapter, asset and server identities before accepting ownership", () => {
    const fixture = createFixture();
    fixture.assets.push({ ...fixture.assets[0]!, chapter_id: "foreign", series_id: "other" });
    expect(candidatesFor(fixture)).toHaveLength(1);
    const duplicatedChapter: Chapter = {
      ...fixture.chapter,
      content: [
        fixture.chapter.content![0]!,
        { ...fixture.chapter.content![1]!, storyboard: ["frame-one"] },
      ],
    };
    expect(candidatesFor({ ...fixture, chapter: duplicatedChapter })).toEqual([]);
    const duplicateServer: PersonalProductionSnapshot = {
      ...fixture.snapshot,
      frames: [
        ...fixture.snapshot.frames,
        { ...fixture.snapshot.frames[0]!, frame_index: 2 },
      ],
    };
    expect(resolvePersonalProductionResumeCandidates({
      ...fixture,
      snapshot: duplicateServer,
      readout: { ...fixture.readout, mediaState: "mismatch" },
      seriesId: fixture.chapter.series_id,
      mediaSnapshotAvailable: true,
    })).toEqual([]);
  });

  it("keeps a valid target when duplicate non-target source identities map to null", () => {
    const fixture = createFixture();
    const duplicateId = "duplicate-local-frame";
    const chapter: Chapter = {
      ...fixture.chapter,
      content: [
        fixture.chapter.content![0]!,
        { storyboard: [duplicateId], preview: null },
        { storyboard: [duplicateId], preview: null },
      ],
    };
    const duplicateAsset = { ...fixture.assets[0]!, id: duplicateId };
    const assets = [
      fixture.assets[0]!,
      { ...duplicateAsset, frame_index: 1 },
      { ...duplicateAsset, frame_index: 2 },
    ];
    const captured: CapturedPersonalProductionMedia = {
      state: "ready",
      frames: [
        fixture.captured.frames[0]!,
        {
          frameIndex: 1,
          candidateId: duplicateId,
          storyboardAssetId: null,
          identityValid: false,
          assetImageUrl: null,
          previewUrl: null,
          assetImageDigest: null,
          previewDigest: null,
          digestAvailable: false,
        },
        {
          frameIndex: 2,
          candidateId: duplicateId,
          storyboardAssetId: null,
          identityValid: false,
          assetImageUrl: null,
          previewUrl: null,
          assetImageDigest: null,
          previewDigest: null,
          digestAvailable: false,
        },
      ],
    };
    const snapshot: PersonalProductionSnapshot = {
      ...fixture.snapshot,
      frames: [
        { ...fixture.snapshot.frames[0]!, frame_index: 0 },
        {
          ...fixture.snapshot.frames[1]!,
          frame_index: 1,
          storyboard_asset_id: null,
          source_valid: false,
        },
        {
          ...fixture.snapshot.frames[1]!,
          frame_index: 2,
          storyboard_asset_id: null,
          source_valid: false,
        },
      ],
    };
    const readout: PersonalProductionReadout = {
      ...fixture.readout,
      frames: [1, 2, 3].map((position) => ({
        position,
        status: "unmarked" as const,
        note: "",
        hasSavedNote: false,
        verified: false,
      })),
    };

    const input = {
      chapter,
      seriesId: chapter.series_id,
      assets,
      captured,
      snapshot,
      readout,
      mediaSnapshotAvailable: true,
    };
    const candidates = resolvePersonalProductionResumeCandidates(input);
    expect(candidates.map((candidate) => candidate.storyboardAssetId)).toEqual(["frame-one"]);

    const replacedSource: Chapter = {
      ...chapter,
      content: [
        { storyboard: ["replacement-frame"], preview: null },
        ...chapter.content!.slice(1),
      ],
    };
    expect(resolvePersonalProductionResumeCandidates({ ...input, chapter: replacedSource })).toEqual([]);

    const misalignedSnapshot: PersonalProductionSnapshot = {
      ...snapshot,
      frames: [
        { ...snapshot.frames[0]!, storyboard_asset_id: "replacement-frame" },
        ...snapshot.frames.slice(1),
      ],
    };
    expect(resolvePersonalProductionResumeCandidates({ ...input, snapshot: misalignedSnapshot })).toEqual([]);
  });

  it("rejects a source-invalid target and requires an available media snapshot", () => {
    const invalid = createFixture({ sourceValid: false });
    expect(candidatesFor(invalid).map((candidate) => candidate.storyboardAssetId)).toEqual(["frame-two"]);
    expect(resolvePersonalProductionResumeCandidates({
      ...invalid,
      seriesId: invalid.chapter.series_id,
      mediaSnapshotAvailable: false,
    })).toEqual([]);
  });

  it.each(["empty", "unreadable"] as const)(
    "allows clearing a non-null old target from a current %s canonical read",
    (mediaState) => {
      const fixture = createFixture({ mediaState, revision: 5, resumeFrameId: "retired-frame" });
      expect(canClearPersonalProductionResume(fixture.chapter.id, fixture.snapshot)).toBe(true);
      expect(createPersonalProductionResumeUpdate(fixture.snapshot, null)).toEqual({
        expected_revision: 5,
        resume_frame_id: null,
      });
    },
  );

  it("does not offer an unchanged position or already-empty clear as a write", () => {
    const fixture = createFixture({ resumeFrameId: "frame-one" });
    const candidate = candidatesFor(fixture)[0]!;
    expect(candidate.storyboardAssetId).toBe(fixture.snapshot.resume_frame_id);
    expect(canClearPersonalProductionResume(fixture.chapter.id, {
      ...fixture.snapshot,
      resume_frame_id: null,
    })).toBe(false);
  });

  it("accepts a whole canonical response with maintenance changes for a set", () => {
    const fixture = createFixture();
    const candidate = candidatesFor(fixture)[0]!;
    const update = createPersonalProductionResumeUpdate(fixture.snapshot, candidate);
    const response = {
      chapter_id: fixture.chapter.id,
      revision: 1,
      media_state: "ready",
      frames: fixture.snapshot.frames.map((frame) => ({
        ...frame,
        asset_image_digest: frame.frame_index === 0 ? "c".repeat(64) : frame.asset_image_digest,
      })),
      frame_notes: new Map([["unrelated", { status: "approved", note: "维护后的记录" }]]),
      resume_frame_id: "frame-one",
    };
    const saved = validatePersonalProductionResumeSaveResponse({
      chapterId: fixture.chapter.id,
      update,
      candidate,
      value: response,
    });
    expect(saved.frame_notes.get("unrelated")).toEqual({ status: "approved", note: "维护后的记录" });
    expect(saved.frames[0]?.asset_image_digest).toBe("c".repeat(64));
  });

  it("accepts clear without requiring a remaining media frame", () => {
    const fixture = createFixture({ revision: 7, resumeFrameId: "old-frame" });
    const update = createPersonalProductionResumeUpdate(fixture.snapshot, null);
    const cleared = validatePersonalProductionResumeSaveResponse({
      chapterId: fixture.chapter.id,
      update,
      candidate: null,
      value: {
        chapter_id: fixture.chapter.id,
        revision: 8,
        media_state: "unreadable",
        frames: [],
        frame_notes: {},
        resume_frame_id: null,
      },
    });
    expect(cleared.media_state).toBe("unreadable");
    expect(cleared.resume_frame_id).toBeNull();
  });

  const invalidSetResponses: Array<[string, {
    chapter_id?: string;
    revision?: number;
    resume_frame_id?: string;
    duplicate?: boolean;
    source_valid?: boolean;
  }]> = [
    ["wrong chapter", { chapter_id: "other" }],
    ["wrong revision", { revision: 2 }],
    ["wrong resume id", { resume_frame_id: "frame-two" }],
    ["duplicate set target", { duplicate: true }],
    ["invalid set target", { source_valid: false }],
  ];
  it.each(invalidSetResponses)("rejects a set response with %s", (_label, overrides) => {
    const fixture = createFixture();
    const candidate = candidatesFor(fixture)[0]!;
    const update = createPersonalProductionResumeUpdate(fixture.snapshot, candidate);
    const responseFrames = fixture.snapshot.frames.map((frame) => ({ ...frame }));
    if (overrides.duplicate) {
      responseFrames.push({ ...responseFrames[0]!, frame_index: responseFrames.length });
    }
    if (overrides.source_valid === false) {
      responseFrames[0] = { ...responseFrames[0]!, source_valid: false };
    }
    expect(() => validatePersonalProductionResumeSaveResponse({
      chapterId: fixture.chapter.id,
      update,
      candidate,
      value: {
        chapter_id: overrides.chapter_id ?? fixture.chapter.id,
        revision: overrides.revision ?? 1,
        media_state: "ready",
        frames: responseFrames,
        frame_notes: {},
        resume_frame_id: overrides.resume_frame_id ?? "frame-one",
      },
    })).toThrow(InvalidResponseError);
  });
});
