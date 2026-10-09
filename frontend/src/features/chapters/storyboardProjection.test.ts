import { describe, expect, it } from "vitest";
import type { Chapter, StoryboardAsset, StoryboardFrame } from "../../shared/api/contracts";
import { projectStoryboardFrames } from "./storyboardProjection";

function makeChapter(content: StoryboardFrame[] | null): Chapter {
  return {
    id: "chapter-current",
    series_id: "series-current",
    title: "第一章",
    content,
    order: 1,
    created_at: "2026-10-01T00:00:00",
    updated_at: "2026-10-02T00:00:00",
    lock: null,
  };
}

function makeAsset(overrides: Partial<StoryboardAsset> = {}): StoryboardAsset {
  return {
    id: "frame-a",
    series_id: "series-current",
    chapter_id: "chapter-current",
    frame_index: 0,
    name: "资产名",
    description: null,
    image_url: "http://127.0.0.1:4175/media/frame-a.svg",
    created_at: "2026-10-01T00:00:00",
    updated_at: "2026-10-02T00:00:00",
    ...overrides,
  };
}

describe("read-only storyboard projection", () => {
  it("maps by the unique same-chapter storyboard id rather than array or frame_index order", () => {
    const chapter = makeChapter([
      { text: "frame B", storyboard: ["frame-b"] },
      { text: "frame A", storyboard: ["frame-a"] },
    ]);
    const views = projectStoryboardFrames(chapter, [
      makeAsset({ id: "frame-a", frame_index: 0 }),
      makeAsset({ id: "frame-b", frame_index: 1, image_url: "http://127.0.0.1:4175/media/frame-b.svg" }),
    ], "http://127.0.0.1:4175/api");

    expect(views.map((view) => view.position)).toEqual([1, 2]);
    expect(views.map((view) => view.imageUrl)).toEqual([
      "http://127.0.0.1:4175/media/frame-b.svg",
      "http://127.0.0.1:4175/media/frame-a.svg",
    ]);
  });

  it("rejects missing, non-string, and repeated storyboard identities without an index fallback", () => {
    const frames: StoryboardFrame[] = [
      { text: "missing" },
      { text: "invalid", storyboard: [17] },
      { text: "duplicate one", storyboard: ["duplicate"] },
      { text: "duplicate two", storyboard: ["duplicate"] },
    ];
    const views = projectStoryboardFrames(makeChapter(frames), [
      makeAsset({ id: "duplicate" }),
      makeAsset({ id: "unused" }),
    ], "http://127.0.0.1:4175/api");

    expect(views.map((view) => view.imageUrl)).toEqual([null, null, null, null]);
    expect(views.every((view) => view.imageStatus === "unavailable")).toBe(true);
  });

  it("rejects duplicate and cross-context assets even when an id matches", () => {
    const chapter = makeChapter([{ text: "frame", storyboard: ["frame-a"] }]);
    const duplicate = makeAsset({ id: "frame-a" });
    const duplicateViews = projectStoryboardFrames(chapter, [duplicate, { ...duplicate }], "http://127.0.0.1:4175/api");
    const crossChapterViews = projectStoryboardFrames(chapter, [
      makeAsset({ id: "frame-a", chapter_id: "another-chapter" }),
    ], "http://127.0.0.1:4175/api");
    const crossSeriesViews = projectStoryboardFrames(chapter, [
      makeAsset({ id: "frame-a", series_id: "another-series" }),
    ], "http://127.0.0.1:4175/api");

    expect(duplicateViews[0]?.imageUrl).toBeNull();
    expect(crossChapterViews[0]?.imageUrl).toBeNull();
    expect(crossSeriesViews[0]?.imageUrl).toBeNull();
  });

  it("hides remote images while preserving text and only reports preview presence", () => {
    const views = projectStoryboardFrames(
      makeChapter([{
        text: "<script>still text</script>",
        original_text: 12,
        storyboard: ["frame-a"],
        preview: "https://remote.example.invalid/video.mp4",
        character: ["character-id", 3, ""],
        scene: ["scene-id"],
        prop: null,
      }]),
      [makeAsset({ id: "frame-a", image_url: "https://remote.example.invalid/image.webp" })],
      "http://127.0.0.1:4175/api",
    );

    expect(views[0]).toMatchObject({
      text: { kind: "value", value: "<script>still text</script>" },
      originalText: { kind: "invalid" },
      referenceCount: 2,
      hasPreview: true,
      imageUrl: null,
      imageStatus: "unavailable",
    });
    expect(JSON.stringify(views)).not.toContain("character-id");
    expect(JSON.stringify(views)).not.toContain("remote.example.invalid");
  });

  it("separates absent originals from missing identity and ignores null content", () => {
    const noImage = projectStoryboardFrames(
      makeChapter([{ text: "frame", storyboard: ["frame-a"] }]),
      [makeAsset({ id: "frame-a", image_url: null })],
      "http://127.0.0.1:4175/api",
    );
    expect(noImage[0]?.imageStatus).toBe("missing");
    expect(projectStoryboardFrames(makeChapter(null), [], "http://127.0.0.1:4175/api")).toEqual([]);
  });
});
