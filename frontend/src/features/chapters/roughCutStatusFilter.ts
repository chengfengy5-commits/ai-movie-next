import type { PersonalRoughCutFrame } from "../../shared/api/personalRoughCut";

export type RoughCutStatusFilter = "all" | "included" | "excluded" | "pending";

export interface IndexedRoughCutFrame {
  readonly frame: PersonalRoughCutFrame;
  readonly originalIndex: number;
}

export interface RoughCutStatusCounts {
  all: number;
  included: number;
  excluded: number;
  pending: number;
}

export function indexRoughCutFrames(
  frames: readonly PersonalRoughCutFrame[],
): readonly IndexedRoughCutFrame[] {
  return frames.map((frame, originalIndex) => ({ frame, originalIndex }));
}

export function countRoughCutStatusEntries(
  entries: readonly IndexedRoughCutFrame[],
): RoughCutStatusCounts {
  return entries.reduce<RoughCutStatusCounts>((counts, { frame }) => {
    counts.all += 1;
    if (frame.included) {
      counts.included += 1;
    } else {
      counts.excluded += 1;
    }
    if (frame.pending) {
      counts.pending += 1;
    }
    return counts;
  }, { all: 0, included: 0, excluded: 0, pending: 0 });
}

export function filterRoughCutStatusEntries(
  entries: readonly IndexedRoughCutFrame[],
  filter: RoughCutStatusFilter,
): readonly IndexedRoughCutFrame[] {
  switch (filter) {
    case "all":
      return entries;
    case "included":
      return entries.filter(({ frame }) => frame.included);
    case "excluded":
      return entries.filter(({ frame }) => !frame.included);
    case "pending":
      return entries.filter(({ frame }) => frame.pending);
  }
}
