import { describe, expect, it } from "vitest";
import { getDemoAssetLibraryData } from "../assets/demoAssets";
import { demoSeries } from "../series/demoSeries";
import { getDemoChapterData } from "./demoChapters";

describe("demo chapter asset references", () => {
  it("preserves category counts and order while resolving each ID in the same series catalog", () => {
    for (const series of demoSeries) {
      const chapterData = getDemoChapterData(series.id);
      const library = getDemoAssetLibraryData(series.id);
      if (chapterData === null || library === null) {
        throw new Error("Expected demo chapters and assets for every demo series.");
      }

      const frames = chapterData.chapters.flatMap((chapter) => chapter.content ?? []);
      const references = frames.map((frame) => ({
        character: frame["character"],
        scene: frame["scene"],
        prop: frame["prop"],
      }));

      expect(references).toEqual([
        {
          character: [series.id + "-character-linlan"],
          scene: [series.id + "-scene-old-street"],
          prop: [],
        },
        {
          character: [],
          scene: [],
          prop: [series.id + "-shared-material-id"],
        },
        {
          character: [series.id + "-character-linlan", series.id + "-shared-material-id"],
          scene: [series.id + "-shared-material-id"],
          prop: [series.id + "-shared-material-id", series.id + "-prop-key"],
        },
      ]);

      const categories = [
        {
          references: references.map((frame) => frame.character),
          assets: library.characters,
          lengths: [1, 0, 2],
        },
        {
          references: references.map((frame) => frame.scene),
          assets: library.scenes,
          lengths: [1, 0, 1],
        },
        {
          references: references.map((frame) => frame.prop),
          assets: library.props,
          lengths: [0, 1, 2],
        },
      ];

      for (const category of categories) {
        const ids = new Set(category.assets
          .filter((asset) => asset.series_id === series.id)
          .map((asset) => asset.id));
        const referenceLists = category.references.map((value) =>
          Array.isArray(value) ? value.filter((id): id is string => typeof id === "string") : []
        );

        expect(referenceLists.map((items) => items.length)).toEqual(category.lengths);
        for (const items of referenceLists) {
          for (const id of items) {
            expect(ids.has(id)).toBe(true);
          }
        }
      }
    }
  });
});
