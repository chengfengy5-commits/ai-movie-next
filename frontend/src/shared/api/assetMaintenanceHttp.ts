import { ApiConfigurationError, resolveApiBaseUrl } from "./config";
import { InvalidResponseError, parseErrorDetail } from "./contracts";
import { ApiError, isAbortError } from "./errors";
import {
  parseAssetDuplicateList,
  parseAssetIgnoredDuplicateList,
  parseAssetOrphanCheckResponse,
  parseMergedAssetReceipt,
  parseSetAssetDuplicateIgnoredReceipt,
  type AssetMaintenanceServicePort,
  type AssetMaintenanceType,
  type MergeAssetsInput,
  type SetAssetDuplicateIgnoredInput,
} from "./assetMaintenance";
import { API_TOKEN_KEY, browserStorage, type StorageLike } from "./storage";

export interface AssetMaintenanceHttpOptions {
  storage?: StorageLike;
  fetcher?: typeof fetch;
  timeoutMs?: number;
  pageUrl?: string;
}

function encodedId(value: string, label: string): string {
  if (value.trim() === "") throw new ApiError("invalid-response", `${label}编号无效。`);
  try {
    return encodeURIComponent(value);
  } catch {
    throw new ApiError("invalid-response", `${label}编号无效。`);
  }
}

function statusMessage(status: number, detail: string | null): string {
  if (detail !== null) return detail;
  if (status === 401) return "当前登录状态已失效，请重新登录。";
  if (status === 403) return "当前账号没有整理此剧集素材的权限。";
  if (status === 404) return "剧集或素材已不存在，请重新读取目录。";
  if (status === 422) return "素材维护请求未通过校验，请重新读取并核对。";
  if (status === 503) return "素材维护服务当前未接线。";
  if (status >= 500) return "素材维护服务暂时不可用，写入结果需要核对。";
  return "素材维护请求未完成，请重新读取并核对。";
}

export function createAssetMaintenanceHttpPort(
  apiBaseUrl: string,
  options: AssetMaintenanceHttpOptions = {},
): AssetMaintenanceServicePort {
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
          throw new ApiError("invalid-response", "素材维护 API 地址无效，无法发送请求。");
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
    method: "GET" | "POST",
    path: string,
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
      if (didTimeout) throw new ApiError("timeout", "素材维护请求超时，操作结果需要核对。");

      let payload: unknown;
      try {
        payload = await response.json();
      } catch (error) {
        ensureCurrentSession(token, signal);
        if (!response.ok) {
          throw new ApiError("http", statusMessage(response.status, null), response.status);
        }
        if (isAbortError(error) && signal.aborted) throw new ApiError("aborted", "Request was cancelled.");
        if (didTimeout) throw new ApiError("timeout", "素材维护请求超时，操作结果需要核对。");
        throw new ApiError("invalid-response", "素材维护服务返回了无法识别的数据。", response.status);
      }
      ensureCurrentSession(token, signal);
      if (didTimeout) throw new ApiError("timeout", "素材维护请求超时，操作结果需要核对。");

      if (response.status !== 200) {
        if (response.status >= 400) {
          const detail = parseErrorDetail(payload);
          throw new ApiError("http", statusMessage(response.status, detail), response.status, detail);
        }
        throw new ApiError("invalid-response", "素材维护服务返回了与本次请求不匹配的状态。", response.status);
      }

      try {
        return parse(payload);
      } catch (error) {
        if (error instanceof ApiError) throw error;
        if (error instanceof InvalidResponseError) {
          throw new ApiError("invalid-response", error.message, response.status);
        }
        throw new ApiError("invalid-response", "素材维护服务返回的数据格式无效。", response.status);
      }
    } catch (error) {
      ensureCurrentSession(token, signal);
      if (error instanceof ApiError) throw error;
      if (didTimeout) throw new ApiError("timeout", "素材维护请求超时，操作结果需要核对。");
      if (isAbortError(error)) throw new ApiError("aborted", "Request was cancelled.");
      throw new ApiError("network", "无法连接素材维护服务，操作结果需要核对。");
    } finally {
      clearTimeout(timer);
      signal.removeEventListener("abort", onAbort);
    }
  }

  function seriesAssetsPath(seriesId: string): string {
    return `/series/${encodedId(seriesId, "剧集")}/assets`;
  }

  return {
    checkOrphans(seriesId, input, signal) {
      return request(
        "POST",
        `${seriesAssetsPath(seriesId)}/orphan-check`,
        signal,
        input,
        parseAssetOrphanCheckResponse,
      );
    },
    listDuplicates(seriesId, type, signal) {
      const query = new URLSearchParams({ type, threshold: "0.82" });
      return request(
        "GET",
        `${seriesAssetsPath(seriesId)}/duplicates?${query.toString()}`,
        signal,
        undefined,
        (payload) => parseAssetDuplicateList(payload, type),
      );
    },
    setDuplicateIgnored(seriesId, input: SetAssetDuplicateIgnoredInput, signal) {
      return request(
        "POST",
        `${seriesAssetsPath(seriesId)}/duplicates/ignore`,
        signal,
        input,
        (payload) => parseSetAssetDuplicateIgnoredReceipt(payload, input.ignored),
      );
    },
    listIgnoredDuplicates(seriesId, type: AssetMaintenanceType, signal) {
      const query = new URLSearchParams({ type });
      return request(
        "GET",
        `${seriesAssetsPath(seriesId)}/duplicates/ignored?${query.toString()}`,
        signal,
        undefined,
        (payload) => parseAssetIgnoredDuplicateList(payload, type),
      );
    },
    mergeAssets(seriesId, input: MergeAssetsInput, signal) {
      return request(
        "POST",
        `${seriesAssetsPath(seriesId)}/merge`,
        signal,
        input,
        (payload) => parseMergedAssetReceipt(payload, input.keep_id),
      );
    },
  };
}
