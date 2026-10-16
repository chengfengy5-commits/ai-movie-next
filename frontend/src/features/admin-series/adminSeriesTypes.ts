export type AdminSeriesSortField = "id" | "name" | "user_id" | "created_at";
export type AdminSeriesSortOrder = "asc" | "desc";
export type AdminSeriesMode = "list" | "create" | "edit";

export interface AdminSeriesRecord {
  id: string;
  user_id: string;
  name: string;
  description: string | null;
  image_url: string | null;
  created_at: string;
  updated_at: string;
}

export interface AdminSeriesPage {
  data: AdminSeriesRecord[];
  total: number;
}

export interface AdminSeriesListQuery {
  skip: number;
  limit: number;
  sort_field: AdminSeriesSortField;
  sort_order: AdminSeriesSortOrder;
  user_id?: string;
  name?: string;
}

export interface AdminSeriesCreateInput {
  name: string;
  description: string | null;
}

export interface AdminSeriesUpdatePatch {
  name?: string;
  description?: string | null;
  image_url?: string | null;
}

export interface AdminSeriesApi {
  listSeries(query: AdminSeriesListQuery, signal?: AbortSignal): Promise<AdminSeriesPage>;
  getSeries(id: string, signal?: AbortSignal): Promise<AdminSeriesRecord>;
  createSeries(input: AdminSeriesCreateInput, signal?: AbortSignal): Promise<AdminSeriesRecord>;
  updateSeries(id: string, patch: AdminSeriesUpdatePatch, signal?: AbortSignal): Promise<AdminSeriesRecord>;
}

export interface AdminSeriesListState {
  name: string;
  userId: string;
  sortField: AdminSeriesSortField;
  sortOrder: AdminSeriesSortOrder;
  page: number;
}

export const DEFAULT_ADMIN_SERIES_LIST_STATE: Readonly<AdminSeriesListState> = Object.freeze({
  name: "",
  userId: "",
  sortField: "id",
  sortOrder: "asc",
  page: 1,
});

export type AdminSeriesExitKind = "dirty-draft" | "write-in-flight" | "unknown-write";

export interface PendingAdminSeriesExit {
  kind: AdminSeriesExitKind;
  operation: "create" | "update";
  mode: Exclude<AdminSeriesMode, "list">;
  recordId: string | null;
  intentId: number;
  draft: Readonly<{ name: string; description: string; imageUrl: string }>;
  message: string;
}

export interface AdminSeriesExitSnapshot {
  ownerScope: string;
  viewKey: string;
  administratorId: string;
  mode: AdminSeriesMode;
  recordId: string;
  pending: PendingAdminSeriesExit | null;
}
