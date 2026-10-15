export interface ImportSeriesInput {
  name?: string;
  description?: string;
  data: unknown;
  team_id?: string;
  claim?: false;
}

export interface ImportSeriesResponse {
  id: string;
  name: string;
  message: string;
}

export interface SeriesExchangePort {
  exportSeries(seriesId: string, signal: AbortSignal): Promise<unknown>;
  importSeries(input: ImportSeriesInput, signal: AbortSignal): Promise<ImportSeriesResponse>;
}

export function parseImportSeriesResponse(value: unknown): ImportSeriesResponse {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error("服务器没有返回有效的剧集导入结果。");
  }
  const record = value as Record<string, unknown>;
  if (typeof record.id !== "string" || record.id.trim() === ""
    || typeof record.name !== "string" || record.name.trim() === ""
    || typeof record.message !== "string") {
    throw new Error("服务器返回的剧集导入结果缺少必要字段。");
  }
  return { id: record.id, name: record.name, message: record.message };
}
