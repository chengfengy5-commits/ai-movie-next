import { ApiError } from "../../shared/api/errors";
import type { AdminFetcher } from "./adminApi";
import type {
  AdminUserCreateInput,
  AdminUserListQuery,
  AdminUserPage,
  AdminUserRecord,
  AdminUserUpdateInput,
  AdminUsersApi,
} from "./usersTypes";

type RequestOptions = RequestInit;

function errorDetail(payload: unknown, fallback: string): string {
  if (typeof payload === "object" && payload !== null && "detail" in payload) {
    const detail = payload.detail;
    if (typeof detail === "string" && detail.length > 0) return detail;
  }
  return fallback;
}

async function readError(response: Response): Promise<ApiError> {
  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    // Preserve the HTTP status when the response has no readable JSON body.
  }
  const message = errorDetail(payload, "请求失败：HTTP " + response.status);
  return new ApiError("http", message, response.status, message);
}

async function readJson<T>(response: Response): Promise<T> {
  try {
    return await response.json() as T;
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

export function createAdminUsersApi(
  fetcher: AdminFetcher,
  getToken: () => string | null,
  apiBaseUrl: string,
): AdminUsersApi {
  const endpoint = (path: string) => apiBaseUrl + path;

  async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
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
    return readJson<T>(response);
  }

  return {
    listUsers(query: AdminUserListQuery, signal?: AbortSignal) {
      const params = new URLSearchParams();
      params.set("skip", String(query.skip));
      params.set("limit", String(query.limit));
      params.set("sort_field", query.sort_field);
      params.set("sort_order", query.sort_order);
      if (query.username !== undefined && query.username !== "") params.set("username", query.username);
      if (query.email !== undefined && query.email !== "") params.set("email", query.email);
      if (query.membership_type) params.set("membership_type", query.membership_type);
      if (query.is_superuser !== undefined) params.set("is_superuser", String(query.is_superuser));
      return request<AdminUserPage>(
        endpoint("/admin/users") + "?" + params.toString(),
        signal ? { signal } : {},
      );
    },
    getUser(userId: string, signal?: AbortSignal) {
      return request<AdminUserRecord>(
        endpoint("/admin/users/") + encodeURIComponent(userId),
        signal ? { signal } : {},
      );
    },
    createUser(input: AdminUserCreateInput, signal?: AbortSignal) {
      return request<AdminUserRecord>(endpoint("/admin/users"), {
        method: "POST",
        body: JSON.stringify(input),
        ...(signal ? { signal } : {}),
      });
    },
    updateUser(userId: string, input: AdminUserUpdateInput, signal?: AbortSignal) {
      return request<{ message: string }>(
        endpoint("/admin/users/") + encodeURIComponent(userId),
        {
          method: "PUT",
          body: JSON.stringify(input),
          ...(signal ? { signal } : {}),
        },
      );
    },
  };
}
