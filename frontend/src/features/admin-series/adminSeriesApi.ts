import { ApiError } from "../../shared/api/errors";
import { resolveApiBaseUrl } from "../../shared/api/config";
import type { AdminFetcher } from "../admin/adminApi";
import { parseAdminSeriesPage, parseAdminSeriesRecord } from "./adminSeriesParsers";
import type { AdminSeriesApi, AdminSeriesCreateInput, AdminSeriesListQuery, AdminSeriesRecord, AdminSeriesUpdatePatch } from "./adminSeriesTypes";

function requestFailure(error: unknown): ApiError {
  if (error instanceof ApiError) return error;
  if ((error instanceof DOMException && error.name === "AbortError")
    || (error instanceof Error && error.name === "AbortError")) return new ApiError("aborted", "请求已取消。");
  if (error instanceof Error && error.name === "TimeoutError") return new ApiError("timeout", "请求超时。");
  return new ApiError("network", "无法连接管理后台服务。");
}

function buildQuery(query: AdminSeriesListQuery): string {
  const params = new URLSearchParams();
  params.set("skip", String(query.skip));
  params.set("limit", String(query.limit));
  params.set("sort_field", query.sort_field);
  params.set("sort_order", query.sort_order);
  if (query.user_id !== undefined) params.set("user_id", query.user_id);
  if (query.name !== undefined) params.set("name", query.name);
  return params.toString();
}

export function createAdminSeriesApi(
  fetcher: AdminFetcher,
  getToken: () => string | null,
  getAdministratorId: () => string,
  apiBaseUrl: string,
  pageUrl = globalThis.window?.location.href ?? "http://localhost/",
): AdminSeriesApi {
  const baseUrl = resolveApiBaseUrl(apiBaseUrl, pageUrl);

  async function request<T>(
    path: string,
    method: "GET" | "POST" | "PUT",
    body: unknown,
    signal: AbortSignal | undefined,
    parse: (payload: unknown, administratorId: string) => T,
  ): Promise<T> {
    const token = getToken();
    const administratorId = getAdministratorId();
    if (!token) throw new ApiError("http", "未登录", 401, "未登录");
    const headers = new Headers({ Authorization: "Bearer " + token });
    if (body !== undefined) headers.set("Content-Type", "application/json");
    let response: Response;
    try {
      response = await fetcher(baseUrl + path, {
        method,
        headers,
        ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
        ...(signal ? { signal } : {}),
      });
    } catch (error) {
      throw requestFailure(error);
    }
    if (getToken() !== token || getAdministratorId() !== administratorId) {
      throw new ApiError("aborted", "登录会话已变化，旧请求结果已丢弃。");
    }
    if (response.status !== 200) {
      let detail = "请求失败：HTTP " + response.status;
      try {
        const payload: unknown = await response.json();
        if (typeof payload === "object" && payload !== null && "detail" in payload && typeof payload.detail === "string") detail = payload.detail;
      } catch { /* The status is still useful if the body is empty or malformed. */ }
      if (getToken() !== token || getAdministratorId() !== administratorId) {
        throw new ApiError("aborted", "登录会话已变化，旧请求结果已丢弃。");
      }
      throw new ApiError(response.ok ? "invalid-response" : "http", detail, response.status, detail);
    }
    let payload: unknown;
    try {
      payload = await response.json();
    } catch {
      throw new ApiError("invalid-response", "服务返回的数据无法识别。", 200);
    }
    if (getToken() !== token || getAdministratorId() !== administratorId) {
      throw new ApiError("aborted", "登录会话已变化，旧请求结果已丢弃。");
    }
    return parse(payload, administratorId);
  }

  function parseMatchingRecord(payload: unknown, expectedId?: string): AdminSeriesRecord {
    const record = parseAdminSeriesRecord(payload);
    if (expectedId !== undefined && record.id !== expectedId) {
      throw new ApiError("invalid-response", "服务返回的剧集 ID 与请求 ID 不一致。", 200);
    }
    return record;
  }

  return {
    listSeries(query, signal) {
      return request("/admin/series?" + buildQuery(query), "GET", undefined, signal, (payload) => parseAdminSeriesPage(payload));
    },
    getSeries(id, signal) {
      return request("/admin/series/" + encodeURIComponent(id), "GET", undefined, signal,
        (payload) => parseMatchingRecord(payload, id));
    },
    createSeries(input: AdminSeriesCreateInput, signal) {
      const body = { name: input.name, description: input.description };
      return request("/admin/series", "POST", body, signal, (payload, administratorId) => {
        const record = parseMatchingRecord(payload);
        if (record.user_id !== administratorId || record.name !== body.name || record.description !== body.description) {
          throw new ApiError("invalid-response", "服务返回的剧集与创建提交内容不一致。", 200);
        }
        return record;
      });
    },
    updateSeries(id: string, patch: AdminSeriesUpdatePatch, signal) {
      const body: AdminSeriesUpdatePatch = {};
      if (typeof patch.name === "string") body.name = patch.name;
      if (typeof patch.description === "string" || patch.description === null) body.description = patch.description;
      if (typeof patch.image_url === "string" || patch.image_url === null) body.image_url = patch.image_url;
      return request("/admin/series/" + encodeURIComponent(id), "PUT", body, signal, (payload) => {
        const record = parseMatchingRecord(payload, id);
        if (Object.entries(body).some(([field, value]) => record[field as keyof AdminSeriesRecord] !== value)) {
          throw new ApiError("invalid-response", "服务返回的剧集与更新提交内容不一致。", 200);
        }
        return record;
      });
    },
  };
}
