import { ApiConfigurationError, resolveApiBaseUrl } from "./config";
import { parseErrorDetail } from "./contracts";
import { ApiError, isAbortError } from "./errors";
import {
  API_TOKEN_KEY,
  browserStorage,
  clearApiSession,
  type StorageLike,
} from "./storage";
import type {
  StoryboardAssetExtractInput,
  StoryboardAssetGenerationServicePort,
  StoryboardAssetJudgeInput,
  StoryboardAssetKind,
  StoryboardAssetSaveInput,
} from "./storyboardAssetGeneration";
import { parseStoryboardAssetSse } from "./storyboardAssetGenerationSse";

export const STORYBOARD_ASSET_STREAM_TIMEOUT_MS = 650_000;

export interface StoryboardAssetGenerationHttpOptions {
  storage?: StorageLike;
  fetcher?: typeof fetch;
  timeoutMs?: number;
  pageUrl?: string;
}

const extractPaths: Record<StoryboardAssetKind, string> = {
  character: "/storyboard/extract-characters-stream",
  scene: "/storyboard/extract-scenes-stream",
  prop: "/storyboard/extract-props-stream",
};

const judgePaths: Record<StoryboardAssetKind, string> = {
  character: "/storyboard/judge-character-appearance-stream",
  scene: "/storyboard/judge-scene-appearance-stream",
  prop: "/storyboard/judge-prop-appearance-stream",
};

const judgeCandidatesKeys: Record<StoryboardAssetKind, string> = {
  character: "characters",
  scene: "scenes",
  prop: "props",
};

const savePaths: Record<StoryboardAssetKind, string> = {
  character: "/storyboard/filter-and-save-characters-stream",
  scene: "/storyboard/filter-and-save-scenes-stream",
  prop: "/storyboard/filter-and-save-props-stream",
};

export function createStoryboardAssetGenerationHttpPort(
  apiBaseUrl: string,
  options: StoryboardAssetGenerationHttpOptions = {},
): StoryboardAssetGenerationServicePort {
  const storage = options.storage ?? browserStorage();
  const fetcher = options.fetcher ?? globalThis.fetch.bind(globalThis);
  const timeoutMs = options.timeoutMs ?? STORYBOARD_ASSET_STREAM_TIMEOUT_MS;
  let resolvedBaseUrl: string | null = null;

  function baseUrl(): string {
    if (resolvedBaseUrl !== null) return resolvedBaseUrl;
    try {
      resolvedBaseUrl = resolveApiBaseUrl(apiBaseUrl, options.pageUrl ?? "http://127.0.0.1/");
      return resolvedBaseUrl;
    } catch (error) {
      if (error instanceof ApiConfigurationError) {
        throw new ApiError("invalid-response", "素材提取 API 地址无效，无法发送请求。");
      }
      throw error;
    }
  }

  async function* stream(
    path: string,
    action: "extract" | "judge" | "save",
    kind: StoryboardAssetKind,
    body: object,
    signal: AbortSignal,
  ) {
    const token = storage.getItem(API_TOKEN_KEY);
    if (token === null || token === "") {
      throw new ApiError("http", "登录状态已失效，请重新登录。", 401);
    }
    if (signal.aborted) throw new ApiError("aborted", "Request was cancelled.");

    const controller = new AbortController();
    let didTimeout = false;
    const timer = setTimeout(() => {
      didTimeout = true;
      controller.abort();
    }, timeoutMs);
    const abortFromCaller = () => controller.abort();
    signal.addEventListener("abort", abortFromCaller, { once: true });
    let reader: ReadableStreamDefaultReader<Uint8Array> | null = null;

    const checkCurrentSession = () => {
      if (signal.aborted || storage.getItem(API_TOKEN_KEY) !== token) {
        throw new ApiError("aborted", "登录范围已变化，素材处理请求已取消。");
      }
      if (didTimeout) throw new ApiError("timeout", "素材处理流超时，结果尚未确认。");
    };

    try {
      let response: Response;
      try {
        response = await fetcher(new URL(`${baseUrl()}${path}`), {
          method: "POST",
          headers: new Headers({
            Accept: "text/event-stream",
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          }),
          body: JSON.stringify(body),
          signal: controller.signal,
          credentials: "omit",
          cache: "no-store",
          redirect: "error",
          referrerPolicy: "no-referrer",
        });
        checkCurrentSession();
      } catch (error) {
        if (error instanceof ApiError) throw error;
        if (signal.aborted) throw new ApiError("aborted", "Request was cancelled.");
        if (didTimeout) throw new ApiError("timeout", "素材处理流超时，结果尚未确认。");
        if (isAbortError(error)) throw new ApiError("aborted", "Request was cancelled.");
        throw new ApiError("network", "无法连接素材处理服务，结果尚未确认。");
      }

      if (response.status !== 200) {
        let detail: string | null = null;
        try {
          detail = parseErrorDetail(await response.json());
        } catch {
          detail = null;
        }
        checkCurrentSession();
        if (response.status === 401 && storage.getItem(API_TOKEN_KEY) === token) clearApiSession(storage);
        throw new ApiError("http", detail ?? errorMessage(response.status), response.status, detail);
      }
      if (response.body === null) throw new ApiError("invalid-response", "素材处理服务没有返回事件流。");

      reader = response.body.getReader();
      for await (const event of parseStoryboardAssetSse(reader, controller.signal, action, kind)) {
        checkCurrentSession();
        yield event;
      }
      checkCurrentSession();
    } catch (error) {
      if (didTimeout) throw new ApiError("timeout", "素材处理流超时，结果尚未确认。");
      if (error instanceof ApiError && error.status === 401) throw error;
      if (signal.aborted || storage.getItem(API_TOKEN_KEY) !== token) {
        throw new ApiError("aborted", "登录范围已变化，素材处理请求已取消。");
      }
      if (error instanceof ApiError) throw error;
      throw new ApiError("network", "素材处理流连接中断，结果尚未确认。");
    } finally {
      clearTimeout(timer);
      signal.removeEventListener("abort", abortFromCaller);
      if (reader !== null) {
        try {
          await reader.cancel();
        } catch {
          // The stream may already be closed or aborted.
        }
        try {
          reader.releaseLock();
        } catch {
          // The reader may have released its lock while the stream was closing.
        }
      }
    }
  }

  return {
    streamExtract(kind: StoryboardAssetKind, input: StoryboardAssetExtractInput, signal: AbortSignal) {
      return stream(extractPaths[kind], "extract", kind, input, signal);
    },
    streamJudge(kind: StoryboardAssetKind, input: StoryboardAssetJudgeInput, signal: AbortSignal) {
      const { candidates, ...rest } = input;
      return stream(judgePaths[kind], "judge", kind, { ...rest, [judgeCandidatesKeys[kind]]: candidates }, signal);
    },
    streamSave(kind: StoryboardAssetKind, input: StoryboardAssetSaveInput, signal: AbortSignal) {
      const candidatesKey = kind === "character" ? "merged_characters" : kind === "scene" ? "scenes" : "props";
      const { candidates, ...rest } = input;
      return stream(savePaths[kind], "save", kind, { ...rest, [candidatesKey]: candidates }, signal);
    },
  };
}

function errorMessage(status: number): string {
  if (status === 401) return "当前登录状态已失效，请重新登录。";
  if (status === 403) return "当前账号没有提取或保存此剧集素材的权限。";
  if (status === 404) return "章节或素材已不存在，请重新读取目录。";
  if (status === 503) return "素材处理服务当前未接线。";
  if (status >= 500) return "素材处理服务暂时不可用，保存结果需要重新核对。";
  return "素材处理请求未完成，请重新读取并核对。";
}
