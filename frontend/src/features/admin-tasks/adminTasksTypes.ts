export type AdminTaskSortField = "type" | "model_name" | "status" | "credit_cost" | "user_id" | "created_at";
export type AdminTaskSortOrder = "asc" | "desc";
export type AdminTaskTypeFilter = "" | "chat" | "image" | "video" | "optimize-frame";
export type AdminTaskStatusFilter = "" | "processing" | "completed" | "failed";

export interface AdminTaskRecord {
  id: string;
  user_id: string;
  type: string;
  status: string;
  credit_cost: number;
  message_id: string | null;
  model_name: string;
  created_at: string | null;
  updated_at: string | null;
}

export interface AdminTaskPage {
  data: AdminTaskRecord[];
  total: number;
}

export interface AdminTaskListQuery {
  skip: number;
  limit: number;
  sort_field: AdminTaskSortField;
  sort_order: AdminTaskSortOrder;
  user_id?: string;
  type?: string;
  status?: string;
  model_name?: string;
}

export interface AdminTaskListState {
  userId: string;
  type: AdminTaskTypeFilter;
  status: AdminTaskStatusFilter;
  modelName: string;
  sortField: AdminTaskSortField;
  sortOrder: AdminTaskSortOrder;
  page: number;
}

export interface AdminTasksApi {
  listTasks(query: AdminTaskListQuery, signal?: AbortSignal): Promise<AdminTaskPage>;
}

export const ADMIN_TASK_PAGE_SIZE = 25;

export const DEFAULT_ADMIN_TASK_LIST_STATE: Readonly<AdminTaskListState> = Object.freeze({
  userId: "",
  type: "",
  status: "",
  modelName: "",
  sortField: "created_at",
  sortOrder: "desc",
  page: 1,
});

export const ADMIN_TASK_SORT_FIELDS: readonly AdminTaskSortField[] = [
  "type", "model_name", "status", "credit_cost", "user_id", "created_at",
];
