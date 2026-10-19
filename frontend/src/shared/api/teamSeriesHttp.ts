import { ApiConfigurationError, resolveApiBaseUrl } from "./config";
import { InvalidResponseError, parseErrorDetail } from "./contracts";
import { ApiError, isAbortError } from "./errors";
import {
  parseCreatedTeamSeries,
  parseTeamSeriesClaimReceipt,
  parseTeamSeriesPage,
  parseTeamSeriesShareReceipt,
  parseTeamSeriesTransferReceipt,
  parseTeamSeriesUnclaimReceipt,
  parseTeamSeriesUnshareReceipt,
  type CreateTeamSeriesInput,
  type TeamSeriesServicePort,
} from "./teamSeries";
import { API_TOKEN_KEY, browserStorage, type StorageLike } from "./storage";

export interface TeamSeriesHttpOptions {
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
  if (status === 403) return "当前账号没有执行此团队剧集操作的权限。";
  if (status === 404) return "团队或剧集已不存在，请刷新后确认。";
  if (status === 503) return "团队剧集服务当前未接线。";
  if (status >= 500) return "团队剧集服务暂时不可用，操作结果需要核对。";
  return "团队剧集操作未完成，请检查输入并重试。";
}

export function createTeamSeriesHttpPort(
  apiBaseUrl: string,
  options: TeamSeriesHttpOptions = {},
): TeamSeriesServicePort {
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
          throw new ApiError("invalid-response", "团队剧集 API 地址无效，无法发送请求。");
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
    method: "GET" | "POST" | "DELETE",
    path: string,
    expectedStatus: 200 | 201,
    signal: AbortSignal,
    body: object | undefined,
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
    const headers = new Headers({ Accept: "application/json", Authorization: `Bearer ${token}` });
    if (body !== undefined) headers.set("Content-Type", "application/json");

    try {
      const response = await fetcher(new URL(`${baseUrl()}${path}`), {
        method,
        headers,
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
        signal: controller.signal,
        credentials: "omit",
        cache: "no-store",
        redirect: "error",
        referrerPolicy: "no-referrer",
      });
      ensureCurrentSession(token, signal);
      if (didTimeout) throw new ApiError("timeout", "团队剧集请求超时，操作结果需要核对。");
      let payload: unknown;
      try {
        payload = await response.json();
      } catch (error) {
        ensureCurrentSession(token, signal);
        if (!response.ok) {
          throw new ApiError("http", statusMessage(response.status, null), response.status);
        }
        if (isAbortError(error) && signal.aborted) throw new ApiError("aborted", "Request was cancelled.");
        if (didTimeout) throw new ApiError("timeout", "团队剧集请求超时，操作结果需要核对。");
        throw new ApiError("invalid-response", "团队剧集服务返回了无法识别的数据。", response.status);
      }
      ensureCurrentSession(token, signal);
      if (didTimeout) throw new ApiError("timeout", "团队剧集请求超时，操作结果需要核对。");

      if (response.status !== expectedStatus) {
        if (response.status >= 400) {
          const detail = parseErrorDetail(payload);
          throw new ApiError("http", statusMessage(response.status, detail), response.status, detail);
        }
        throw new ApiError("invalid-response", "团队剧集服务返回了与本次操作不匹配的状态。", response.status);
      }
      try {
        return parse(payload);
      } catch (error) {
        if (error instanceof ApiError) throw error;
        if (error instanceof InvalidResponseError) {
          throw new ApiError("invalid-response", error.message, response.status);
        }
        throw new ApiError("invalid-response", "团队剧集服务返回的数据与本次操作不匹配。", response.status);
      }
    } catch (error) {
      ensureCurrentSession(token, signal);
      if (error instanceof ApiError) throw error;
      if (didTimeout) throw new ApiError("timeout", "团队剧集请求超时，操作结果需要核对。");
      if (isAbortError(error)) throw new ApiError("aborted", "Request was cancelled.");
      throw new ApiError("network", "无法连接团队剧集服务，操作结果需要核对。");
    } finally {
      clearTimeout(timer);
      signal.removeEventListener("abort", onAbort);
    }
  }

  return {
    listTeamSeries(teamId, page, pageSize, signal) {
      if (!Number.isSafeInteger(page) || !Number.isSafeInteger(pageSize)) {
        throw new ApiError("invalid-response", "团队剧集分页参数无效。");
      }
      return request(
        "GET",
        `/teams/${requiredId(teamId, "团队")}/series?page=${page}&page_size=${pageSize}`,
        200,
        signal,
        undefined,
        (payload) => parseTeamSeriesPage(payload, page, pageSize),
      );
    },
    createTeamSeries(teamId, input: CreateTeamSeriesInput, actorId, signal) {
      if (actorId.length === 0) throw new ApiError("invalid-response", "当前账号信息无效。");
      const body = {
        name: input.name,
        ...(input.description === undefined ? {} : { description: input.description }),
        ...(input.image_url === undefined ? {} : { image_url: input.image_url }),
        ...(input.style_prompt_id === undefined ? {} : { style_prompt_id: input.style_prompt_id }),
        ...(input.claim === undefined ? {} : { claim: input.claim }),
      };
      return request(
        "POST",
        `/teams/${requiredId(teamId, "团队")}/series`,
        201,
        signal,
        body,
        (payload) => parseCreatedTeamSeries(payload, teamId, actorId, input),
      );
    },
    shareSeries(seriesId, teamId, claim, signal) {
      return request(
        "POST",
        `/series/${requiredId(seriesId, "剧集")}/share`,
        200,
        signal,
        { team_id: teamId, claim },
        (payload) => parseTeamSeriesShareReceipt(payload, teamId),
      );
    },
    unshareSeries(seriesId, signal) {
      return request(
        "DELETE",
        `/series/${requiredId(seriesId, "剧集")}/share`,
        200,
        signal,
        undefined,
        parseTeamSeriesUnshareReceipt,
      );
    },
    claimTeamSeries(teamId, seriesId, actorId, signal) {
      if (actorId.length === 0) throw new ApiError("invalid-response", "当前账号信息无效。");
      return request(
        "POST",
        `/teams/${requiredId(teamId, "团队")}/series/${requiredId(seriesId, "剧集")}/claim`,
        200,
        signal,
        undefined,
        (payload) => parseTeamSeriesClaimReceipt(payload, actorId),
      );
    },
    unclaimTeamSeries(teamId, seriesId, signal) {
      return request(
        "DELETE",
        `/teams/${requiredId(teamId, "团队")}/series/${requiredId(seriesId, "剧集")}/claim`,
        200,
        signal,
        undefined,
        parseTeamSeriesUnclaimReceipt,
      );
    },
    transferTeamSeries(teamId, seriesId, userId, signal) {
      return request(
        "POST",
        `/teams/${requiredId(teamId, "团队")}/series/${requiredId(seriesId, "剧集")}/transfer`,
        200,
        signal,
        { user_id: userId },
        (payload) => parseTeamSeriesTransferReceipt(payload, userId),
      );
    },
  };
}
