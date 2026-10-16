import { ApiError } from "../../shared/api/errors";
import { resolveApiBaseUrl } from "../../shared/api/config";
import type { AdminFetcher } from "../admin/adminApi";
import { parseAdminTaskPage } from "./adminTasksParsers";
import { ADMIN_TASK_PAGE_SIZE, ADMIN_TASK_SORT_FIELDS, type AdminTaskListQuery, type AdminTasksApi } from "./adminTasksTypes";

function requestFailure(error: unknown): ApiError {
  if (error instanceof ApiError) return error;
  if ((error instanceof DOMException && error.name === "AbortError")
    || (error instanceof Error && error.name === "AbortError")) return new ApiError("aborted", "请求已取消。");
  if (error instanceof Error && error.name === "TimeoutError") return new ApiError("timeout", "请求超时。");
  return new ApiError("network", "无法连接管理后台服务。");
}

function queryString(query: AdminTaskListQuery): string {
  if (!Number.isSafeInteger(query.skip) || query.skip < 0
    || (query.limit !== ADMIN_TASK_PAGE_SIZE && query.limit !== 1000)
    || !ADMIN_TASK_SORT_FIELDS.includes(query.sort_field)
    || (query.sort_order !== "asc" && query.sort_order !== "desc")) {
    throw new ApiError("invalid-response", "任务列表查询参数无效。");
  }
  const params = new URLSearchParams();
  params.set("skip", String(query.skip));
  params.set("limit", String(query.limit));
  params.set("sort_field", query.sort_field);
  params.set("sort_order", query.sort_order);
  if (query.user_id !== undefined) params.set("user_id", query.user_id);
  if (query.type !== undefined) params.set("type", query.type);
  if (query.status !== undefined) params.set("status", query.status);
  if (query.model_name !== undefined) params.set("model_name", query.model_name);
  return params.toString();
}

export function createAdminTasksApi(
  fetcher: AdminFetcher,
  getToken: () => string | null,
  apiBaseUrl: string,
  pageUrl = globalThis.window?.location.href ?? "http://localhost/",
): AdminTasksApi {
  const baseUrl = resolveApiBaseUrl(apiBaseUrl, pageUrl);

  return {
    async listTasks(query, signal) {
      const token = getToken();
      if (!token) throw new ApiError("http", "未登录", 401, "未登录");
      const headers = new Headers({ Authorization: "Bearer " + token });
      let response: Response;
      try {
        response = await fetcher(baseUrl + "/admin/tasks?" + queryString(query), {
          method: "GET",
          headers,
          ...(signal ? { signal } : {}),
        });
      } catch (error) {
        if (getToken() !== token) throw new ApiError("aborted", "登录会话已变化，旧请求结果已丢弃。");
        throw requestFailure(error);
      }
      if (getToken() !== token) throw new ApiError("aborted", "登录会话已变化，旧请求结果已丢弃。");
      if (response.status !== 200) {
        let detail = "请求失败：HTTP " + response.status;
        try {
          const payload: unknown = await response.json();
          if (typeof payload === "object" && payload !== null && "detail" in payload && typeof payload.detail === "string") {
            detail = payload.detail;
          }
        } catch { /* Preserve the status when the error body is absent or invalid. */ }
        if (getToken() !== token) throw new ApiError("aborted", "登录会话已变化，旧请求结果已丢弃。");
        throw new ApiError(response.ok ? "invalid-response" : "http", detail, response.status, detail);
      }
      let payload: unknown;
      try {
        payload = await response.json();
      } catch {
        if (getToken() !== token) throw new ApiError("aborted", "登录会话已变化，旧请求结果已丢弃。");
        throw new ApiError("invalid-response", "服务返回的数据无法识别。", 200);
      }
      if (getToken() !== token) throw new ApiError("aborted", "登录会话已变化，旧请求结果已丢弃。");
      return parseAdminTaskPage(payload);
    },
  };
}
