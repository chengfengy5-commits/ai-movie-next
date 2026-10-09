import { describe, expect, it } from "vitest";
import { getDemoChapterData } from "./demoChapters";
import { getDemoPersonalRoughCut } from "./demoPersonalRoughCut";
import { demoSeries } from "../series/demoSeries";

describe("demo personal rough-cut projection", () => {
  it("treats null chapter content as the same empty source as an empty array", () => {
    const data = getDemoChapterData(demoSeries[0]!.id);
    if (data === null) {
      throw new Error("Expected demo chapter data.");
    }
    const chapter = data.chapters[1]!;
    const assets = data.assetsByChapter[chapter.id] ?? [];

    const nullContent = getDemoPersonalRoughCut({ ...chapter, content: null }, assets);
    const emptyContent = getDemoPersonalRoughCut({ ...chapter, content: [] }, assets);

    expect(nullContent).toEqual({
      chapter_id: chapter.id,
      revision: 0,
      saved: false,
      frames: [],
      removed_asset_ids: [],
    });
    expect(nullContent).toEqual(emptyContent);
  });
});
