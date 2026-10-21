import { ApiConfigurationError, resolveApiBaseUrl } from "./config";
import { InvalidResponseError } from "./contracts";
import {
  AssetReplacementError,
  parseAssetReplacementReceipt,
  type AssetReplacementPort,
  type ReplaceChapterAssetInput,
} from "./assetReplacement";
import { API_TOKEN_KEY, browserStorage, type StorageLike } from "./storage";

export interface AssetReplacementHttpOptions {
  storage?: StorageLike;
  fetcher?: typeof fetch;
  timeoutMs?: number;
  pageUrl?: string;
}

function encodedChapterId(chapterId: string): string {
  if (chapterId.trim() === "") {
    throw new AssetReplacementError("not-sent", "章节身份无效，未发送替换请求。");
  }
  try {
    return encodeURIComponent(chapterId);
  } catch {
    throw new AssetReplacementError("not-sent", "章节身份无效，未发送替换请求。");
  }
}

function validInput(input: ReplaceChapterAssetInput): boolean {
  return input.old_asset_id.trim() !== ""
    && input.new_asset_id.trim() !== ""
    && input.old_asset_id !== input.new_asset_id
    && (input.asset_type === "character" || input.asset_type === "scene" || input.asset_type === "prop");
}

export function createAssetReplacementHttpPort(
  apiBaseUrl: string,
  options: AssetReplacementHttpOptions = {},
): AssetReplacementPort {
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
          throw new AssetReplacementError("not-sent", "素材替换 API 地址无效，未发送请求。");
        }
        throw error;
      }
    }
    return resolvedBaseUrl;
  }

  return {
    beginRun() {
      const token = storage.getItem(API_TOKEN_KEY);
      let inFlight = false;
      const isCurrent = () => token !== null
        && token !== ""
        && storage.getItem(API_TOKEN_KEY) === token;

      return {
        isCurrent,
        async replaceChapter(chapterId, input, signal) {
          if (inFlight) {
            throw new AssetReplacementError("not-sent", "当前章节请求仍在进行，未发送重复请求。");
          }
          if (!isCurrent() || signal.aborted || token === null || token === "") {
            throw new AssetReplacementError("not-sent", "当前登录状态已变化，未发送替换请求。", 401);
          }
          if (!validInput(input)) {
            throw new AssetReplacementError("not-sent", "素材身份无效，未发送替换请求。");
          }

          const encodedId = encodedChapterId(chapterId);
          const requestUrl = new URL(`${baseUrl()}/chapters/${encodedId}/replace-asset`);
          const controller = new AbortController();
          const abortFromCaller = () => controller.abort();
          if (signal.aborted) {
            throw new AssetReplacementError("not-sent", "替换请求已取消，未发送请求。");
          }
          let headers: Headers;
          try {
            headers = new Headers({
              Accept: "application/json",
              Authorization: `Bearer ${token}`,
              "Content-Type": "application/json",
            });
          } catch {
            throw new AssetReplacementError("not-sent", "当前登录凭据格式无效，未发送替换请求。");
          }
          signal.addEventListener("abort", abortFromCaller, { once: true });
          let didTimeout = false;
          let requestDispatched = false;
          const timer = setTimeout(() => {
            didTimeout = true;
            controller.abort();
          }, timeoutMs);

          inFlight = true;
          try {
            if (!isCurrent() || signal.aborted) {
              throw new AssetReplacementError("not-sent", "当前登录状态已变化，未发送替换请求。", 401);
            }
            requestDispatched = true;
            const response = await fetcher(requestUrl, {
              method: "POST",
              headers,
              body: JSON.stringify({
                old_asset_id: input.old_asset_id,
                new_asset_id: input.new_asset_id,
                asset_type: input.asset_type,
              }),
              signal: controller.signal,
              credentials: "omit",
              cache: "no-store",
              redirect: "error",
              referrerPolicy: "no-referrer",
            });

            if ([400, 401, 403, 404, 422].includes(response.status)) {
              throw new AssetReplacementError("rejected", "服务器拒绝了本章节替换请求。", response.status);
            }
            if (response.status !== 200) {
              throw new AssetReplacementError("unknown", "章节替换结果尚未确认，请重新核对。", response.status);
            }
            if (didTimeout) {
              throw new AssetReplacementError("unknown", "章节替换请求超时，结果尚未确认。", response.status);
            }

            let payload: unknown;
            try {
              payload = await response.json();
            } catch {
              throw new AssetReplacementError("unknown", "服务器已响应，但替换回执无法识别。", response.status);
            }
            if (didTimeout) {
              throw new AssetReplacementError("unknown", "章节替换请求超时，结果尚未确认。", response.status);
            }
            if (!isCurrent()) {
              throw new AssetReplacementError("unknown", "登录状态已变化，原章节替换结果尚未确认。", response.status);
            }
            try {
              return parseAssetReplacementReceipt(payload);
            } catch (error) {
              if (error instanceof InvalidResponseError) {
                throw new AssetReplacementError("unknown", "服务器已响应，但替换回执无法识别。", response.status);
              }
              throw error;
            }
          } catch (error) {
            if (error instanceof AssetReplacementError) throw error;
            if (!requestDispatched) {
              throw new AssetReplacementError("not-sent", "替换请求未发送。", null);
            }
            if (didTimeout) {
              throw new AssetReplacementError("unknown", "章节替换请求超时，结果尚未确认。", null);
            }
            throw new AssetReplacementError("unknown", "连接中断，章节替换结果尚未确认。", null);
          } finally {
            inFlight = false;
            clearTimeout(timer);
            signal.removeEventListener("abort", abortFromCaller);
          }
        },
      };
    },
  };
}
