import { ApiConfigurationError, resolveApiBaseUrl } from "./config";
import { InvalidResponseError, parseErrorDetail } from "./contracts";
import { ApiError, isAbortError } from "./errors";
import {
  parseTeamMemberTasksPage,
  parseTeamModelUsage,
  parseTeamSeriesUsage,
  parseTeamUsage,
  parseTeamUsageExport,
  type TeamReportingDateRange,
  type TeamReportingServicePort,
} from "./teamReporting";
import { API_TOKEN_KEY, browserStorage, type StorageLike } from "./storage";

export interface TeamReportingHttpOptions {
  storage?: StorageLike;
  fetcher?: typeof fetch;
  timeoutMs?: number;
  pageUrl?: string;
}

function requiredId(value: string, label: string): string {
  if (value.length === 0) throw new ApiError("invalid-response", `${label}编号无效。`);
  try {
    return encodeURIComponent(value);
  } catch {
    throw new ApiError("invalid-response", `${label}编号无效。`);
  }
}

function statusMessage(status: number, detail: string | null): string {
  if (detail !== null) return detail;
  if (status === 401) return "当前登录状态已失效，请重新登录。";
  if (status === 403) return "当前账号没有查看此团队报表的权限。";
  if (status === 404) return "团队、成员或剧集已不存在，请刷新团队资料。";
  if (status === 422) return "报表查询条件无效，请检查日期与分页。";
  if (status === 503) return "团队报表服务当前未接线。";
  if (status >= 500) return "团队报表服务暂时不可用，请重试。";
  return "团队报表请求未完成，请检查查询条件后重试。";
}

function queryString(range: TeamReportingDateRange): string {
  const params = new URLSearchParams();
  if (range.startTime !== undefined) params.set("start_time", range.startTime);
  if (range.endTime !== undefined) params.set("end_time", range.endTime);
  const value = params.toString();
  return value.length === 0 ? "" : `?${value}`;
}

export function createTeamReportingHttpPort(
  apiBaseUrl: string,
  options: TeamReportingHttpOptions = {},
): TeamReportingServicePort {
  const storage = options.storage ?? browserStorage();
  const fetcher = options.fetcher ?? globalThis.fetch.bind(globalThis);
  const timeoutMs = options.timeoutMs ?? 15_000;
  let resolvedBaseUrl: string | null = null;

  function baseUrl(): string {
    if (resolvedBaseUrl === null) {
      try {
        resolvedBaseUrl = resolveApiBaseUrl(apiBaseUrl, options.pageUrl ?? "http://127.0.0.1/");
      } catch (error) {
        if (error instanceof ApiConfigurationError) {
          throw new ApiError("invalid-response", "团队报表 API 地址无效，无法发送请求。");
        }
        throw error;
      }
    }
    return resolvedBaseUrl;
  }

  function ensureCurrentSession(token: string, signal: AbortSignal): void {
    if (signal.aborted || storage.getItem(API_TOKEN_KEY) !== token) {
      throw new ApiError("aborted", "Request was cancelled.");
    }
  }

  async function request<T>(
    path: string,
    signal: AbortSignal,
    parse: (payload: unknown) => T,
  ): Promise<T> {
    const token = storage.getItem(API_TOKEN_KEY);
    if (token === null || token === "") {
      throw new ApiError("http", "当前登录状态已失效，请重新登录。", 401);
    }
    ensureCurrentSession(token, signal);

    const controller = new AbortController();
    const onAbort = () => controller.abort();
    signal.addEventListener("abort", onAbort, { once: true });
    let didTimeout = false;
    const timer = setTimeout(() => {
      didTimeout = true;
      controller.abort();
    }, timeoutMs);

    try {
      const response = await fetcher(new URL(`${baseUrl()}${path}`), {
        method: "GET",
        headers: new Headers({ Accept: "application/json", Authorization: `Bearer ${token}` }),
        signal: controller.signal,
        credentials: "omit",
        cache: "no-store",
        redirect: "error",
        referrerPolicy: "no-referrer",
      });
      ensureCurrentSession(token, signal);
      if (didTimeout) throw new ApiError("timeout", "团队报表请求超时，请重试。");

      let payload: unknown;
      try {
        payload = await response.json();
      } catch (error) {
        ensureCurrentSession(token, signal);
        if (!response.ok) throw new ApiError("http", statusMessage(response.status, null), response.status);
        if (isAbortError(error) && signal.aborted) throw new ApiError("aborted", "Request was cancelled.");
        if (didTimeout) throw new ApiError("timeout", "团队报表请求超时，请重试。");
        throw new ApiError("invalid-response", "团队报表服务返回了无法识别的数据。", response.status);
      }
      ensureCurrentSession(token, signal);
      if (didTimeout) throw new ApiError("timeout", "团队报表请求超时，请重试。");

      if (response.status !== 200) {
        if (response.status >= 400) {
          const detail = parseErrorDetail(payload);
          throw new ApiError("http", statusMessage(response.status, detail), response.status, detail);
        }
        throw new ApiError("invalid-response", "团队报表服务返回了与本次请求不匹配的状态。", response.status);
      }

      try {
        return parse(payload);
      } catch (error) {
        if (error instanceof ApiError) throw error;
        if (error instanceof InvalidResponseError) {
          throw new ApiError("invalid-response", error.message, response.status);
        }
        throw new ApiError("invalid-response", "团队报表服务返回的数据格式无效。", response.status);
      }
    } catch (error) {
      ensureCurrentSession(token, signal);
      if (error instanceof ApiError) throw error;
      if (didTimeout) throw new ApiError("timeout", "团队报表请求超时，请重试。");
      if (isAbortError(error)) throw new ApiError("aborted", "Request was cancelled.");
      throw new ApiError("network", "无法连接团队报表服务，请重试。");
    } finally {
      clearTimeout(timer);
      signal.removeEventListener("abort", onAbort);
    }
  }

  return {
    listMemberTasks(teamId, userId, page, pageSize, signal) {
      if (!Number.isSafeInteger(page) || page < 1 || !Number.isSafeInteger(pageSize) || pageSize < 1) {
        throw new ApiError("invalid-response", "成员任务分页参数无效。");
      }
      return request(
        `/teams/${requiredId(teamId, "团队")}/members/${requiredId(userId, "成员")}/tasks?page=${page}&page_size=${pageSize}`,
        signal,
        parseTeamMemberTasksPage,
      );
    },
    getTeamUsage(teamId, range, signal) {
      return request(`/teams/${requiredId(teamId, "团队")}/usage${queryString(range)}`, signal, parseTeamUsage);
    },
    getSeriesUsage(teamId, seriesId, range, signal) {
      return request(
        `/teams/${requiredId(teamId, "团队")}/series/${requiredId(seriesId, "剧集")}/usage${queryString(range)}`,
        signal,
        parseTeamSeriesUsage,
      );
    },
    getModelUsage(teamId, modelName, range, signal) {
      if (modelName.length === 0) throw new ApiError("invalid-response", "模型名称无效。");
      const params = new URLSearchParams({ model_name: modelName });
      const dateParams = new URLSearchParams(queryString(range).replace(/^\?/, ""));
      for (const [key, value] of dateParams) params.set(key, value);
      return request(`/teams/${requiredId(teamId, "团队")}/usage/model?${params.toString()}`, signal, parseTeamModelUsage);
    },
    exportTeamUsage(teamId, range, signal) {
      return request(`/teams/${requiredId(teamId, "团队")}/usage/export${queryString(range)}`, signal, parseTeamUsageExport);
    },
  };
}
