import { ApiError } from "../../shared/api/errors";
import type { AdminFetcher } from "./adminApi";
import type {
  SystemPrompt,
  SystemPromptPage,
  SystemPromptsApi,
} from "./systemPromptsTypes";

type RequestOptions = RequestInit & { authenticated?: boolean };

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
    // Keep the HTTP status useful when the server sends no JSON body.
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

export function createSystemPromptsApi(
  fetcher: AdminFetcher,
  getToken: () => string | null,
  apiBaseUrl = "/api",
): SystemPromptsApi {
  const endpoint = (path: string) => apiBaseUrl + path;

  async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    const headers = new Headers(options.headers);
    if (options.body !== undefined && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    if (options.authenticated !== false) {
      const token = getToken();
      if (!token) throw new ApiError("http", "未登录", 401, "未登录");
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
    if (!response.ok) throw await readError(response);
    return readJson<T>(response);
  }

  function promptPath(id: string): string {
    return endpoint("/admin/system-prompts/") + encodeURIComponent(id);
  }

  return {
    list(query, signal) {
      const params = new URLSearchParams();
      params.set("skip", String(query.skip));
      params.set("limit", String(query.limit));
      if (query.category) params.set("category", query.category);
      if (query.is_active !== undefined) params.set("is_active", String(query.is_active));
      return request<SystemPromptPage>(
        endpoint("/admin/system-prompts") + "?" + params.toString(),
        signal ? { signal } : {},
      );
    },
    get(id, signal) {
      return request<SystemPrompt>(promptPath(id), signal ? { signal } : {});
    },
    create(input, signal) {
      return request<SystemPrompt>(endpoint("/admin/system-prompts"), {
        method: "POST",
        body: JSON.stringify(input),
        ...(signal ? { signal } : {}),
      });
    },
    update(id, input, signal) {
      return request<SystemPrompt>(promptPath(id), {
        method: "PUT",
        body: JSON.stringify(input),
        ...(signal ? { signal } : {}),
      });
    },
    delete(id, signal) {
      return request<{ message: string }>(
        promptPath(id),
        signal ? { method: "DELETE", signal } : { method: "DELETE" },
      );
    },
  };
}
