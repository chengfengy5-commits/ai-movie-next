import { describe, expect, it } from "vitest";
import type { Chapter, StoryboardAsset, StoryboardFrame } from "../../shared/api/contracts";
import {
  buildChapterReorderInput,
  inspectStoryboardReferences,
  isSyncedFrameContentValid,
  sameChapterOrderSnapshot,
  updateStoryboardFrameText,
} from "./chapterEditing";

const seriesId = "series-editing-test";
const chapterId = "chapter-editing-test";

function makeChapter(id: string, order: number, series = seriesId): Chapter {
  return {
    id,
    series_id: series,
    title: id,
    content: [],
    order,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    lock: null,
  };
}

function makeAsset(id: string, frameIndex: number, overrides: Partial<StoryboardAsset> = {}): StoryboardAsset {
  return {
    id,
    series_id: seriesId,
    chapter_id: chapterId,
    frame_index: frameIndex,
    name: `分镜 ${frameIndex + 1}`,
    description: null,
    image_url: null,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    ...overrides,
  };
}

describe("chapter editing helpers", () => {
  it("changes only the target text and preserves unknown nested fields without mutating the input", () => {
    const original = [
      {
        text: "before",
        original_text: "原文",
        storyboard: ["frame-a"],
        character: ["character-a"],
        nested_unknown: { untouched: [1, { value: true }] },
      },
      { text: "other", future_field: { keep: "me" } },
    ] satisfies StoryboardFrame[];
    const originalSnapshot = structuredClone(original);

    const result = updateStoryboardFrameText(original, 1, "after");

    expect(result).toEqual({
      ok: true,
      changed: true,
      content: [
        { ...original[0], text: "after" },
        original[1],
      ],
    });
    expect(original).toEqual(originalSnapshot);
    expect(result.ok && result.content[1]).toBe(original[1]);
  });

  it.each([undefined, null])("treats missing or null frame text as an empty-string no-op (%s)", (text) => {
    const frame: StoryboardFrame = text === undefined ? {} : { text: null } as unknown as StoryboardFrame;
    const content: StoryboardFrame[] = [frame];
    const result = updateStoryboardFrameText(content, 1, "");
    expect(result).toEqual({ ok: true, changed: false, content });
  });

  it("rejects an unrecognized object text value and an invalid frame position", () => {
    expect(updateStoryboardFrameText([{ text: { value: "keep" } }], 1, "replace")).toMatchObject({ ok: false });
    expect(updateStoryboardFrameText([{ text: "keep" }], 2, "replace")).toMatchObject({ ok: false });
  });

  it("maps visual order to descending chapter numbers and rejects invalid chapter identities", () => {
    const chapters = [makeChapter("chapter-a", 1), makeChapter("chapter-b", 2)];
    expect(buildChapterReorderInput(chapters, ["chapter-b", "chapter-a"], seriesId)).toEqual({
      chapters: [{ id: "chapter-b", order: 2 }, { id: "chapter-a", order: 1 }],
    });
    expect(buildChapterReorderInput(chapters, ["chapter-a", "chapter-a"], seriesId)).toBeNull();
    expect(buildChapterReorderInput(chapters, ["chapter-a", "foreign"], seriesId)).toBeNull();
    expect(buildChapterReorderInput([chapters[0]!, makeChapter("foreign", 2, "other-series")], ["chapter-a", "foreign"], seriesId)).toBeNull();
  });

  it("treats a changed directory display sequence as a changed order snapshot", () => {
    const first = makeChapter("chapter-a", 2);
    const second = makeChapter("chapter-b", 1);

    expect(sameChapterOrderSnapshot([first, second], [second, first], seriesId)).toBe(false);
    expect(sameChapterOrderSnapshot([first, second], [{ ...first }, { ...second }], seriesId)).toBe(true);
  });

  it("refuses duplicate and foreign storyboard references before a write", () => {
    const duplicated = [{ storyboard: ["asset-a"] }, { storyboard: ["asset-a"] }];
    expect(inspectStoryboardReferences(duplicated, [makeAsset("asset-a", 0)], seriesId, chapterId))
      .toMatchObject({ ok: false });
    expect(inspectStoryboardReferences(
      [{ storyboard: ["asset-a"] }],
      [makeAsset("asset-a", 0, { chapter_id: "other-chapter" })],
      seriesId,
      chapterId,
    )).toMatchObject({ ok: false });
  });

  it("confirms backend-generated asset refs for a bare text frame only when the fresh asset belongs to that frame", () => {
    const base = [{ text: "before", unknown: { retained: true } }];
    const expected = [{ text: "after", unknown: { retained: true } }];
    const actual = [{ text: "after", unknown: { retained: true }, storyboard: ["generated-asset"] }];

    expect(isSyncedFrameContentValid(
      base, expected, actual, new Set([1]), [makeAsset("generated-asset", 0)],
      seriesId, chapterId, 1, "after",
    )).toBe(true);
    expect(isSyncedFrameContentValid(
      base, expected, [{ text: "after", unknown: { retained: true } }], new Set([1]), [],
      seriesId, chapterId, 1, "after",
    )).toBe(false);
    expect(isSyncedFrameContentValid(
      base, expected, actual, new Set([1]), [makeAsset("generated-asset", 0, { series_id: "foreign-series" })],
      seriesId, chapterId, 1, "after",
    )).toBe(false);
  });
});
