import { describe, expect, it } from "vitest";
import type { Chapter, StoryboardAsset, StoryboardFrame } from "../../shared/api/contracts";
import type { PersonalProductionFrameView, PersonalProductionReadout } from "./personal-production/projection";
import { resolveProductionNoteFrameTarget } from "./productionNoteFrameNavigation";

function frame(id: string): StoryboardFrame {
  return { storyboard: [id] };
}

function chapter(frames: StoryboardFrame[] = [frame("shot-a")]): Chapter {
  return {
    id: "chapter-a",
    series_id: "series-a",
    title: "章节 A",
    content: frames,
    order: 1,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-02T00:00:00Z",
    lock: null,
  };
}

function asset(overrides: Partial<StoryboardAsset> = {}): StoryboardAsset {
  return {
    id: "shot-a",
    chapter_id: "chapter-a",
    series_id: "series-a",
    frame_index: 0,
    name: "Shot A",
    description: null,
    image_url: null,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-02T00:00:00Z",
    ...overrides,
  };
}

function row(overrides: Partial<PersonalProductionFrameView> = {}): PersonalProductionFrameView {
  return {
    position: 1,
    status: "unmarked",
    note: "",
    hasSavedNote: false,
    verified: true,
    ...overrides,
  };
}

function readout(
  rows: PersonalProductionFrameView[] = [row()],
  overrides: Partial<PersonalProductionReadout> = {},
): PersonalProductionReadout {
  return {
    revision: 1,
    noSavedRecord: false,
    mediaState: "ready",
    frames: rows,
    orphanNotes: [],
    resumePosition: null,
    resumeIsInvalid: false,
    ...overrides,
  };
}

describe("resolveProductionNoteFrameTarget", () => {
  it.each(["approved", "needs_revision", "needs_reconfirmation", "unmarked"] as const)(
    "resolves a current verified %s row without requiring approval",
    (status) => {
      const currentRow = row({ status });
      expect(resolveProductionNoteFrameTarget(
        chapter(),
        "series-a",
        [asset()],
        readout([currentRow]),
        currentRow,
      )).toEqual({ position: 1, storyboardAssetId: "shot-a", frame: chapter().content?.[0] });
    },
  );

  it("rejects unreadable, unverifiable, unknown, and unverified rows", () => {
    for (const invalidRow of [
      row({ status: "unreadable" }),
      row({ status: "unverifiable" }),
      row({ status: "future-status" as PersonalProductionFrameView["status"] }),
      row({ verified: false }),
    ]) {
      const currentReadout = readout([invalidRow]);
      expect(resolveProductionNoteFrameTarget(
        chapter(), "series-a", [asset()], currentReadout, invalidRow,
      )).toBeNull();
    }
  });

  it("requires the actual unique row, ready snapshot, and a current chapter frame", () => {
    const currentRow = row();
    const currentReadout = readout([currentRow]);
    expect(resolveProductionNoteFrameTarget(
      chapter(), "series-a", [asset()], currentReadout, { ...currentRow },
    )).toBeNull();
    expect(resolveProductionNoteFrameTarget(
      chapter(), "series-a", [asset()], readout([currentRow, currentRow]), currentRow,
    )).toBeNull();
    expect(resolveProductionNoteFrameTarget(
      chapter(), "series-a", [asset()], readout([currentRow], { noSavedRecord: true }), currentRow,
    )).toBeNull();
    expect(resolveProductionNoteFrameTarget(
      chapter(), "series-a", [asset()], readout([currentRow], { mediaState: "mismatch" }), currentRow,
    )).toBeNull();
    expect(resolveProductionNoteFrameTarget(
      chapter(), "series-b", [asset()], currentReadout, currentRow,
    )).toBeNull();
    expect(resolveProductionNoteFrameTarget(
      chapter(), "series-a", [asset()], currentReadout, row({ position: 2 }),
    )).toBeNull();
  });

  it("rejects missing, repeated, foreign, and replaced raw identities", () => {
    const currentRow = row();
    const currentReadout = readout([currentRow]);
    expect(resolveProductionNoteFrameTarget(
      chapter([{}]), "series-a", [asset()], currentReadout, currentRow,
    )).toBeNull();
    expect(resolveProductionNoteFrameTarget(
      chapter([frame("shot-a"), frame("shot-a")]),
      "series-a",
      [asset(), asset({ frame_index: 1 })],
      currentReadout,
      currentRow,
    )).toBeNull();
    expect(resolveProductionNoteFrameTarget(
      chapter(), "series-a", [asset(), asset({ chapter_id: "chapter-b" })], currentReadout, currentRow,
    )).toBeNull();
    expect(resolveProductionNoteFrameTarget(
      chapter(), "series-a", [asset({ series_id: "series-b" })], currentReadout, currentRow,
    )).toBeNull();
    expect(resolveProductionNoteFrameTarget(
      chapter([frame("replacement")]), "series-a", [asset()], currentReadout, currentRow,
    )).toBeNull();
  });
});
