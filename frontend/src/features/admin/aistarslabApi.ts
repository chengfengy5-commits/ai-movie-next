import type { AdminFetcher } from "./adminApi";

export type AistarslabModelCategory = "image" | "video";

export interface AistarslabModelChoice {
  model: string;
  label: string;
}

export interface AistarslabLineChoice {
  channel: string;
  title: string;
  isDefault: boolean;
  models: AistarslabModelChoice[];
}

export interface AistarslabBuiltParams {
  channel: string;
  model: string;
  label: string;
  lineTitle: string;
  params: string;
}

export interface AistarslabCreatedSystemModel {
  id: string;
  category: AistarslabModelCategory;
  name: string;
  model: string;
}

export interface AistarslabSyncResult {
  created: number;
  updated: number;
  total: number;
}

export type AistarslabApiErrorKind = "network" | "timeout" | "aborted" | "http" | "invalid-response";

export class AistarslabApiError extends Error {
  constructor(
    readonly kind: AistarslabApiErrorKind,
    readonly status: number | null = null,
  ) {
    super(kind === "http" && status !== null
      ? `AIStarsLab 请求失败（HTTP ${status}）`
      : kind === "timeout"
        ? "AIStarsLab 请求超时。"
        : kind === "aborted"
          ? "AIStarsLab 请求已取消。"
          : kind === "invalid-response"
            ? "AIStarsLab 服务返回的数据无法识别。"
            : "无法连接 AIStarsLab 管理服务。");
    this.name = "AistarslabApiError";
  }
}

export interface AistarslabApi {
  getConfig(signal?: AbortSignal): Promise<{
    imageConfig: AistarslabLineChoice[];
    videoConfig: AistarslabLineChoice[];
  }>;
  buildParams(
    category: AistarslabModelCategory,
    channel: string,
    model: string,
    signal?: AbortSignal,
  ): Promise<AistarslabBuiltParams>;
  addDefault(
    category: AistarslabModelCategory,
    apiKey: string,
    signal?: AbortSignal,
  ): Promise<AistarslabCreatedSystemModel>;
  syncAll(signal?: AbortSignal): Promise<AistarslabSyncResult>;
}

const REQUEST_TIMEOUT_MS = 20_000;

type UnknownRecord = Record<string, unknown>;

function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requiredString(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

function parseLines(value: unknown): AistarslabLineChoice[] | null {
  if (!Array.isArray(value)) return null;
  const result: AistarslabLineChoice[] = [];
  for (const item of value) {
    if (!isRecord(item)) return null;
    const channel = requiredString(item.channel);
    if (channel === null || typeof item.defaultOption !== "boolean" || !Array.isArray(item.models)) return null;
    if (item.title !== undefined && item.title !== null && typeof item.title !== "string") return null;
    const models: AistarslabModelChoice[] = [];
    for (const entry of item.models) {
      if (!isRecord(entry)) return null;
      const model = requiredString(entry.model);
      if (model === null || (entry.label !== undefined && entry.label !== null && typeof entry.label !== "string")) return null;
      models.push({ model, label: requiredString(entry.label) ?? model });
    }
    result.push({
      channel,
      title: typeof item.title === "string" ? item.title : "",
      isDefault: item.defaultOption,
      models,
    });
  }
  return result;
}

function parseConfig(value: unknown): {
  imageConfig: AistarslabLineChoice[];
  videoConfig: AistarslabLineChoice[];
} | null {
  if (!isRecord(value)) return null;
  const imageConfig = parseLines(value.imageConfig);
  const videoConfig = parseLines(value.videoConfig);
  if (imageConfig === null || videoConfig === null) return null;
  return { imageConfig, videoConfig };
}

function parseBuiltParams(value: unknown, channel: string, model: string): AistarslabBuiltParams | null {
  if (!isRecord(value)) return null;
  if (value.channel !== channel || value.model !== model) return null;
  const label = requiredString(value.label);
  const params = requiredString(value.params);
  if (label === null || params === null) return null;
  try {
    const parsed: unknown = JSON.parse(params);
    if (!isRecord(parsed)) return null;
  } catch {
    return null;
  }
  if (value.lineTitle !== undefined && value.lineTitle !== null && typeof value.lineTitle !== "string") return null;
  return {
    channel,
    model,
    label,
    lineTitle: typeof value.lineTitle === "string" ? value.lineTitle : "",
    params,
  };
}

function parseCreatedModel(value: unknown, category: AistarslabModelCategory): AistarslabCreatedSystemModel | null {
  if (!isRecord(value)) return null;
  const id = requiredString(value.id);
  const name = requiredString(value.name);
  const model = requiredString(value.model);
  if (id === null || name === null || model === null) return null;
  if (value.category !== category || value.provider !== "aistarslab" || value.is_system !== true || value.user_id !== "system") {
    return null;
  }
  return { id, category, name, model };
}

function parseSyncResult(value: unknown): AistarslabSyncResult | null {
  if (!isRecord(value)) return null;
  const { created, updated, total } = value;
  const isCount = (count: unknown): count is number => (
    typeof count === "number" && Number.isSafeInteger(count) && count >= 0
  );
  if (!isCount(created) || !isCount(updated) || !isCount(total)) return null;
  if (!Number.isSafeInteger(created + updated) || total !== created + updated) return null;
  return { created, updated, total };
}

export function createAistarslabApi(
  fetcher: AdminFetcher,
  getToken: () => string | null,
  apiBaseUrl = "/api",
): AistarslabApi {
  const endpoint = (path: string) => apiBaseUrl + "/aistarslab" + path;

  async function requestJson(path: string, method: "GET" | "POST", body: unknown, signal?: AbortSignal): Promise<unknown> {
    const controller = new AbortController();
    let timedOut = false;
    const abortFromCaller = () => controller.abort();
    if (signal?.aborted) controller.abort();
    else signal?.addEventListener("abort", abortFromCaller, { once: true });
    const timeout = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, REQUEST_TIMEOUT_MS);

    function awaitRequest<T>(startRequest: () => Promise<T>): Promise<T> {
      if (controller.signal.aborted) {
        return Promise.reject(new AistarslabApiError(timedOut ? "timeout" : "aborted"));
      }
      return new Promise<T>((resolve, reject) => {
        const abort = () => reject(new AistarslabApiError(timedOut ? "timeout" : "aborted"));
        controller.signal.addEventListener("abort", abort, { once: true });
        let request: Promise<T>;
        try {
          request = startRequest();
        } catch (cause) {
          controller.signal.removeEventListener("abort", abort);
          reject(cause);
          return;
        }
        request.then(
          (value) => {
            controller.signal.removeEventListener("abort", abort);
            if (controller.signal.aborted) reject(new AistarslabApiError(timedOut ? "timeout" : "aborted"));
            else resolve(value);
          },
          (cause: unknown) => {
            controller.signal.removeEventListener("abort", abort);
            if (controller.signal.aborted) reject(new AistarslabApiError(timedOut ? "timeout" : "aborted"));
            else reject(cause);
          },
        );
      });
    }

    try {
      const token = getToken();
      if (!token) throw new AistarslabApiError("http", 401);
      const headers = new Headers({ Authorization: "Bearer " + token });
      const init: RequestInit = { method, headers, signal: controller.signal };
      if (body !== undefined) {
        headers.set("Content-Type", "application/json");
        init.body = JSON.stringify(body);
      }
      const response = await awaitRequest(() => fetcher(endpoint(path), init));
      if (response.status !== 200) {
        if (!response.ok) throw new AistarslabApiError("http", response.status);
        throw new AistarslabApiError("invalid-response", response.status);
      }
      try {
        return await awaitRequest(() => response.json() as Promise<unknown>);
      } catch (cause) {
        if (cause instanceof AistarslabApiError) throw cause;
        throw new AistarslabApiError("invalid-response", response.status);
      }
    } catch (error) {
      if (error instanceof AistarslabApiError) throw error;
      if (timedOut) throw new AistarslabApiError("timeout");
      if (signal?.aborted) throw new AistarslabApiError("aborted");
      throw new AistarslabApiError("network");
    } finally {
      clearTimeout(timeout);
      signal?.removeEventListener("abort", abortFromCaller);
    }

  }

  return {
    async getConfig(signal) {
      const parsed = parseConfig(await requestJson("/config", "GET", undefined, signal));
      if (parsed === null) throw new AistarslabApiError("invalid-response", 200);
      return parsed;
    },
    async buildParams(category, channel, model, signal) {
      const path = category === "image" ? "/build-image-params" : "/build-params";
      const parsed = parseBuiltParams(
        await requestJson(path, "POST", { channel, model }, signal),
        channel,
        model,
      );
      if (parsed === null) throw new AistarslabApiError("invalid-response", 200);
      return parsed;
    },
    async addDefault(category, apiKey, signal) {
      const path = category === "image" ? "/add-image-default" : "/add-default";
      const parsed = parseCreatedModel(
        await requestJson(path, "POST", { api_key: apiKey }, signal),
        category,
      );
      if (parsed === null) throw new AistarslabApiError("invalid-response", 200);
      return parsed;
    },
    async syncAll(signal) {
      const parsed = parseSyncResult(await requestJson("/sync-all", "POST", undefined, signal));
      if (parsed === null) throw new AistarslabApiError("invalid-response", 200);
      return parsed;
    },
  };
}
