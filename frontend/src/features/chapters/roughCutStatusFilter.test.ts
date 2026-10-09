import { describe, expect, it } from "vitest";
import type { PersonalRoughCutFrame } from "../../shared/api/personalRoughCut";
import {
  countRoughCutStatusEntries,
  filterRoughCutStatusEntries,
  indexRoughCutFrames,
  type RoughCutStatusFilter,
} from "./roughCutStatusFilter";

function frame(
  assetId: string,
  included: boolean,
  pending: boolean,
  previewUrl: string | null = null,
): PersonalRoughCutFrame {
  return {
    asset_id: assetId,
    frame_index: 0,
    text: assetId,
    preview_url: previewUrl,
    missing_reason: previewUrl === null ? "当前没有可用视频。" : null,
    included,
    pending,
  };
}

describe("rough-cut status filter", () => {
  it("counts independent inclusion and pending predicates, including overlap", () => {
    const frames = [
      frame("included-pending", true, true),
      frame("included-ready", true, false),
      frame("excluded-pending", false, true),
      frame("excluded-ready", false, false),
    ];
    const counts = countRoughCutStatusEntries(indexRoughCutFrames(frames));

    expect(counts).toEqual({ all: 4, included: 2, excluded: 2, pending: 2 });
    expect(counts.included + counts.excluded).toBe(counts.all);
  });

  it.each([
    ["all", [0, 1, 2, 3]],
    ["included", [0, 1]],
    ["excluded", [2, 3]],
    ["pending", [0, 2]],
  ] as const)("preserves original order, index, and row references for %s", (filter, indexes) => {
    const frames = [
      frame("included-pending", true, true),
      frame("included-ready", true, false),
      frame("excluded-pending", false, true),
      frame("excluded-ready", false, false),
    ];
    const entries = indexRoughCutFrames(frames);
    const visible = filterRoughCutStatusEntries(entries, filter as RoughCutStatusFilter);

    expect(visible.map(({ originalIndex }) => originalIndex)).toEqual(indexes);
    expect(visible.map(({ frame: row }) => row)).toEqual(indexes.map((index) => frames[index]));
    visible.forEach(({ frame: row, originalIndex }) => {
      expect(row).toBe(frames[originalIndex]);
    });
  });

  it("retains empty IDs and rows without video and supports snapshots larger than 500", () => {
    const frames = Array.from({ length: 501 }, (_, index) =>
      frame(index === 0 ? "" : `asset-${index}`, index % 2 === 0, index === 500),
    );
    const entries = indexRoughCutFrames(frames);
    const counts = countRoughCutStatusEntries(entries);
    const pending = filterRoughCutStatusEntries(entries, "pending");

    expect(counts.all).toBe(501);
    expect(entries[0]).toEqual({ frame: frames[0], originalIndex: 0 });
    expect(entries[0]?.frame).toBe(frames[0]);
    expect(entries[0]?.frame.preview_url).toBeNull();
    expect(pending).toEqual([{ frame: frames[500], originalIndex: 500 }]);
    expect(pending[0]?.frame).toBe(frames[500]);
  });

  it("returns no entries for a valid filter with no matches", () => {
    const entries = indexRoughCutFrames([frame("one", true, false)]);

    expect(filterRoughCutStatusEntries(entries, "pending")).toEqual([]);
  });
});
