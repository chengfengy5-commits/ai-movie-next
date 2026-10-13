import { ApiError } from "../../shared/api/errors";
import type {
  AdminApi,
  AdminLoginResponse,
  AdminModel,
  AdminModelPage,
  AdminStats,
} from "./adminTypes";

export type AdminFetcher = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

type RequestOptions = RequestInit & { authenticated?: boolean };

function errorDetail(payload: unknown, fallback: string): string {
  if (typeof payload === "object" && payload !== null && "detail" in payload) {
    const detail = payload.detail;
    if (typeof detail === "string" && detail.length > 0) {
      return detail;
    }
  }
  return fallback;
}

async function readError(response: Response): Promise<ApiError> {
  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    // Keep the HTTP status useful when a server error has no JSON body.
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
  if (error instanceof ApiError) {
    return error;
  }
  if (error instanceof DOMException && error.name === "AbortError") {
    return new ApiError("aborted", "请求已取消。");
  }
  return new ApiError("network", "无法连接管理后台服务。");
}

export function createAdminApi(
  fetcher: AdminFetcher,
  getToken: () => string | null,
  apiBaseUrl = "/api",
): AdminApi {
  const endpoint = (path: string) => apiBaseUrl + path;

  async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    const headers = new Headers(options.headers);
    if (options.body !== undefined && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    if (options.authenticated !== false) {
      const token = getToken();
      if (!token) {
        throw new ApiError("http", "未登录", 401, "未登录");
      }
      headers.set("Authorization", "Bearer " + token);
    }

    const init: RequestInit = { ...options };
    delete (init as RequestOptions).authenticated;
    let response: Response;
    try {
      response = await fetcher(path, { ...init, headers });
    } catch (error) {
      throw requestFailure(error);
    }
    if (!response.ok) {
      throw await readError(response);
    }
    return readJson<T>(response);
  }

  return {
    login(username, password, signal) {
      return request<AdminLoginResponse>(endpoint("/auth/login"), {
        method: "POST",
        body: JSON.stringify({ username, password }),
        ...(signal ? { signal } : {}),
        authenticated: false,
      }).then((result) => {
        if (!result.user || result.user.is_superuser !== true) {
          throw new ApiError("http", "权限不足，仅管理员可登录后台。", 403, "权限不足，仅管理员可登录后台。");
        }
        if (typeof result.access_token !== "string" || result.access_token.length === 0) {
          throw new ApiError("invalid-response", "登录响应缺少有效的访问令牌。");
        }
        return result;
      });
    },
    getStats(signal) {
      return request<AdminStats>(endpoint("/admin/stats"), signal ? { signal } : {});
    },
    listModels(query, signal) {
      const params = new URLSearchParams();
      params.set("skip", String(query.skip));
      params.set("limit", String(query.limit));
      if (query.category) params.set("category", query.category);
      if (query.provider) params.set("provider", query.provider);
      if (query.is_active !== undefined) params.set("is_active", String(query.is_active));
      return request<AdminModelPage>(
        endpoint("/admin/system-models") + "?" + params.toString(),
        signal ? { signal } : {},
      );
    },
    getModel(id, signal) {
      return request<AdminModel>(
        endpoint("/admin/system-models/") + encodeURIComponent(id),
        signal ? { signal } : {},
      );
    },
    createModel(input, signal) {
      return request<AdminModel>(endpoint("/admin/system-models"), {
        method: "POST",
        body: JSON.stringify(input),
        ...(signal ? { signal } : {}),
      });
    },
    updateModel(id, input, signal) {
      return request<AdminModel>(endpoint("/admin/system-models/") + encodeURIComponent(id), {
        method: "PUT",
        body: JSON.stringify(input),
        ...(signal ? { signal } : {}),
      });
    },
    async deleteModel(id, signal) {
      const result = await request<{ message: string }>(
        endpoint("/admin/system-models/") + encodeURIComponent(id),
        signal ? { method: "DELETE", signal } : { method: "DELETE" },
      );
      return { ...result, id };
    },
  };
}
