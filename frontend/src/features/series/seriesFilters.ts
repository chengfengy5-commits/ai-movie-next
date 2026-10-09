import type { Series, SeriesFilter } from "../../shared/api/contracts";

export const seriesFilters: ReadonlyArray<{ id: SeriesFilter; label: string }> = [
  { id: "all", label: "全部剧集" },
  { id: "mine", label: "我创建的" },
  { id: "team", label: "团队剧集" },
  { id: "claimed", label: "我认领的" },
];

export function filterSeries(
  series: readonly Series[],
  filter: SeriesFilter,
  userId: string,
): Series[] {
  switch (filter) {
    case "mine":
      return series.filter((item) => item.user_id === userId);
    case "team":
      return series.filter((item) => item.team_id !== null && item.team_id.length > 0);
    case "claimed":
      return series.filter((item) => item.claimed_by === userId);
    case "all":
      return [...series];
  }
}

export function visibleSeries(
  series: readonly Series[],
  count: number,
): Series[] {
  return series.slice(0, count);
}

export const SERIES_PAGE_SIZE = 20;
