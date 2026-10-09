import type { PersonalProductionFrameView, CurrentNoteStatus } from "./personal-production/projection";

export type ProductionNoteStatusFilter = "all" | CurrentNoteStatus;

export interface ProductionNoteStatusFilterOption {
  value: ProductionNoteStatusFilter;
  label: string;
}

export const productionNoteStatusFilterOptions: readonly ProductionNoteStatusFilterOption[] = [
  { value: "all", label: "全部" },
  { value: "needs_revision", label: "待修" },
  { value: "needs_reconfirmation", label: "待重新确认" },
  { value: "unmarked", label: "未标记" },
  { value: "approved", label: "已认可" },
  { value: "unverifiable", label: "当前媒体暂不可核对" },
  { value: "unreadable", label: "记录不可识别" },
];

export type ProductionNoteStatusFilterCounts = Record<ProductionNoteStatusFilter, number>;

export function countProductionNoteStatuses(
  frames: readonly PersonalProductionFrameView[],
): ProductionNoteStatusFilterCounts {
  const counts: ProductionNoteStatusFilterCounts = {
    all: frames.length,
    needs_revision: 0,
    needs_reconfirmation: 0,
    unmarked: 0,
    approved: 0,
    unverifiable: 0,
    unreadable: 0,
  };

  for (const frame of frames) {
    counts[frame.status] += 1;
  }

  return counts;
}

export function filterProductionNoteFrames(
  frames: readonly PersonalProductionFrameView[],
  selected: ProductionNoteStatusFilter,
): readonly PersonalProductionFrameView[] {
  if (selected === "all") {
    return frames;
  }

  return frames.filter((frame) => frame.status === selected);
}
