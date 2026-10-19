import { ApiError } from "../../shared/api/errors";
import { resolveApiBaseUrl } from "../../shared/api/config";
import type { AdminFetcher } from "../admin/adminApi";
import { parseAdminSystemConfig, parseAdminSystemConfigPage } from "./adminSystemConfigParsers";
import type { AdminSystemConfig, AdminSystemConfigApi, AdminSystemConfigUpdate } from "./adminSystemConfigTypes";

const INTEGER_DEFAULTS: Partial<Record<keyof AdminSystemConfigUpdate, number>> = {
  team_created_limit: 3,
  team_joined_limit: 3,
  team_member_limit: 20,
  invite_code_ttl_hours: 24,
  chapter_lock_idle_minutes: 15,
};

function integerProjection(value: string | number | boolean): number | null {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  const integer = Math.trunc(value);
  return Number.isSafeInteger(integer) ? integer : null;
}

function workerConcurrencyProjection(value: string | number | boolean): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  if (trimmed === "") return "0";
  if (/^[+-]?\d+$/.test(trimmed)) {
    const negative = trimmed.startsWith("-");
    const digits = trimmed.replace(/^[+-]/, "").replace(/^0+(?=\d)/, "");
    return negative && digits !== "0" ? `-${digits}` : digits;
  }

  // Python int accepts forms such as underscores and non-ASCII digits that this client
  // intentionally does not reinterpret. Leave those responses unconfirmed.
  if (!/[^\x00-\x7f]/.test(trimmed) && !trimmed.includes("_")) return "";
  return null;
}

function expectedUpdateValue(
  field: keyof AdminSystemConfigUpdate,
  value: string | number | boolean,
): string | number | boolean | null {
  if (field === "payments_enabled") return typeof value === "boolean" ? value : null;
  if (field === "storyboard_cost_multiplier") return integerProjection(value);

  const defaultValue = INTEGER_DEFAULTS[field];
  if (defaultValue !== undefined) {
    const integer = integerProjection(value);
    return integer === null ? null : integer || defaultValue;
  }
  if (field === "aistarslab_credit_multiplier") {
    return typeof value === "number" && Number.isFinite(value) ? value || 1 : null;
  }
  if (field === "frontend_url" || field === "allowed_origins" || field === "upload_dir") {
    return typeof value === "string" ? value.trim() : null;
  }
  if (field === "worker_max_concurrency") return workerConcurrencyProjection(value);
  return value;
}

function matchesSubmittedPatch(record: AdminSystemConfig, patch: AdminSystemConfigUpdate): boolean {
  for (const [rawField, submitted] of Object.entries(patch)) {
    if (submitted === undefined) continue;
    const field = rawField as keyof AdminSystemConfigUpdate;
    const expected = expectedUpdateValue(field, submitted);
    if (expected === null || record[field as keyof AdminSystemConfig] !== expected) return false;
  }
  return true;
}

function requestFailure(error: unknown): ApiError {
  if (error instanceof ApiError) return error;
  if ((error instanceof DOMException && error.name === "AbortError")
    || (error instanceof Error && error.name === "AbortError")) {
    return new ApiError("aborted", "请求已取消。");
  }
  if (error instanceof Error && error.name === "TimeoutError") {
    return new ApiError("timeout", "请求超时。");
  }
  return new ApiError("network", "无法连接管理后台服务。");
}

function statusError(response: Response): ApiError {
  const message = "系统配置请求未成功（HTTP " + response.status + "）。";
  return new ApiError(response.ok ? "invalid-response" : "http", message, response.status, message);
}

export function createAdminSystemConfigApi(
  fetcher: AdminFetcher,
  getToken: () => string | null,
  apiBaseUrl: string,
  pageUrl = globalThis.window?.location.href ?? "http://localhost/",
): AdminSystemConfigApi {
  const baseUrl = resolveApiBaseUrl(apiBaseUrl, pageUrl);

  async function request<T>(
    path: string,
    method: "GET" | "PUT",
    body: AdminSystemConfigUpdate | undefined,
    signal: AbortSignal | undefined,
    parse: (payload: unknown) => T,
  ): Promise<T> {
    const token = getToken();
    if (!token) throw new ApiError("http", "未登录", 401, "未登录");

    let response: Response;
    try {
      response = await fetcher(baseUrl + path, {
        method,
        headers: new Headers({ Authorization: "Bearer " + token, ...(body ? { "Content-Type": "application/json" } : {}) }),
        ...(body ? { body: JSON.stringify(body) } : {}),
        ...(signal ? { signal } : {}),
      });
    } catch (error) {
      if (getToken() !== token) throw new ApiError("aborted", "登录会话已变化，旧请求结果已丢弃。");
      throw requestFailure(error);
    }

    if (getToken() !== token) throw new ApiError("aborted", "登录会话已变化，旧请求结果已丢弃。");
    if (response.status !== 200) throw statusError(response);

    let payload: unknown;
    try {
      payload = await response.json();
    } catch {
      if (getToken() !== token) throw new ApiError("aborted", "登录会话已变化，旧请求结果已丢弃。");
      throw new ApiError("invalid-response", "服务返回的数据无法识别。", response.status);
    }
    if (getToken() !== token) throw new ApiError("aborted", "登录会话已变化，旧请求结果已丢弃。");
    return parse(payload);
  }

  return {
    list(signal) {
      return request("/admin/system-config", "GET", undefined, signal, parseAdminSystemConfigPage);
    },
    get(id, signal) {
      return request("/admin/system-config/" + encodeURIComponent(id), "GET", undefined, signal, (payload) => {
        const config = parseAdminSystemConfig(payload);
        if (config.id !== id) {
          throw new ApiError("invalid-response", "系统配置响应 ID 与查询 ID 不一致。");
        }
        return config;
      });
    },
    update(id, patch, signal) {
      return request("/admin/system-config/" + encodeURIComponent(id), "PUT", patch, signal, (payload) => {
        const config = parseAdminSystemConfig(payload);
        if (config.id !== id) {
          throw new ApiError("invalid-response", "系统配置响应 ID 与更新 ID 不一致。");
        }
        if (!matchesSubmittedPatch(config, patch)) {
          throw new ApiError("invalid-response", "服务端返回的配置与本次提交不一致，保存结果待核对。", 200);
        }
        return config;
      });
    },
  };
}
