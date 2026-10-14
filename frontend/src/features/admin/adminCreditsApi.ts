import { ApiError } from "../../shared/api/errors";
import type { AdminFetcher } from "./adminApi";
import type {
  AdminCreditLog,
  AdminCreditLogQuery,
  AdminCreditsApi,
  AdminUserCredit,
  AdminUserCreditQuery,
} from "./adminCreditsTypes";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value);
}

function isUserCredit(value: unknown): value is AdminUserCredit {
  return isRecord(value)
    && typeof value.id === "string"
    && typeof value.user_id === "string"
    && isInteger(value.credits)
    && typeof value.created_at === "string"
    && typeof value.updated_at === "string";
}

function isCreditLog(value: unknown): value is AdminCreditLog {
  return isRecord(value)
    && typeof value.id === "string"
    && typeof value.user_id === "string"
    && isInteger(value.amount)
    && isInteger(value.balance_after)
    && typeof value.type === "string"
    && (typeof value.description === "string" || value.description === null)
    && (typeof value.task_id === "string" || value.task_id === null)
    && typeof value.created_at === "string";
}

function parsePage<T>(value: unknown, isItem: (item: unknown) => item is T): { data: T[]; total: number } {
  if (!isRecord(value) || !Array.isArray(value.data) || !isInteger(value.total) || value.total < 0) {
    throw new ApiError("invalid-response", "服务返回的数据无法识别。");
  }
  const data: T[] = [];
  for (const item of value.data) {
    if (!isItem(item)) throw new ApiError("invalid-response", "服务返回的数据无法识别。");
    data.push(item);
  }
  return { data, total: value.total };
}

function requireCredit(value: unknown): AdminUserCredit {
  if (!isUserCredit(value)) throw new ApiError("invalid-response", "服务返回的数据无法识别。");
  return value;
}

function errorDetail(payload: unknown, fallback: string): string {
  if (isRecord(payload) && typeof payload.detail === "string" && payload.detail.length > 0) {
    return payload.detail;
  }
  return fallback;
}

async function readError(response: Response): Promise<ApiError> {
  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    // Keep the HTTP status useful when the response has no readable JSON body.
  }
  const message = errorDetail(payload, "请求失败：HTTP " + response.status);
  return new ApiError("http", message, response.status, message);
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json() as unknown;
  } catch {
    throw new ApiError("invalid-response", "服务返回的数据无法识别。");
  }
}

function requestFailure(error: unknown): ApiError {
  if (error instanceof ApiError) return error;
  if (error instanceof DOMException && error.name === "AbortError") {
    return new ApiError("aborted", "请求已取消。");
  }
  return new ApiError("network", "无法连接管理后台服务。");
}

export function createAdminCreditsApi(
  fetcher: AdminFetcher,
  getToken: () => string | null,
  apiBaseUrl = "/api",
): AdminCreditsApi {
  async function request(path: string, options: RequestInit = {}): Promise<unknown> {
    const headers = new Headers(options.headers);
    if (options.body !== undefined && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    const token = getToken();
    if (!token) throw new ApiError("http", "未登录", 401, "未登录");
    headers.set("Authorization", "Bearer " + token);

    let response: Response;
    try {
      response = await fetcher(path, { ...options, headers });
    } catch (error) {
      throw requestFailure(error);
    }
    if (!response.ok) throw await readError(response);
    return readJson(response);
  }

  function userCreditPath(creditId: string): string {
    return apiBaseUrl + "/admin/user-credits/" + encodeURIComponent(creditId);
  }

  return {
    async listUserCredits(query: AdminUserCreditQuery, signal?: AbortSignal) {
      const params = new URLSearchParams();
      params.set("skip", String(query.skip));
      params.set("limit", String(query.limit));
      params.set("sort_field", query.sort_field);
      params.set("sort_order", query.sort_order);
      const payload = await request(
        apiBaseUrl + "/admin/user-credits?" + params.toString(),
        signal ? { signal } : {},
      );
      return parsePage<AdminUserCredit>(payload, isUserCredit);
    },
    async getUserCredit(creditId: string, signal?: AbortSignal) {
      const payload = await request(userCreditPath(creditId), signal ? { signal } : {});
      return requireCredit(payload);
    },
    async updateUserCredit(creditId: string, credits: number, signal?: AbortSignal) {
      if (!Number.isSafeInteger(credits)) {
        throw new ApiError("invalid-response", "积分必须是有效整数。");
      }
      const payload = await request(userCreditPath(creditId), {
        method: "PUT",
        body: JSON.stringify({ credits }),
        ...(signal ? { signal } : {}),
      });
      return requireCredit(payload);
    },
    async listCreditLogs(query: AdminCreditLogQuery, signal?: AbortSignal) {
      const params = new URLSearchParams();
      params.set("skip", String(query.skip));
      params.set("limit", String(query.limit));
      params.set("sort_field", query.sort_field);
      params.set("sort_order", query.sort_order);
      if (query.user_id !== undefined && query.user_id.trim() !== "") {
        params.set("user_id", query.user_id);
      }
      const payload = await request(
        apiBaseUrl + "/admin/credit-logs?" + params.toString(),
        signal ? { signal } : {},
      );
      return parsePage<AdminCreditLog>(payload, isCreditLog);
    },
  };
}
