import { ApiConfigurationError, resolveApiBaseUrl } from "./config";
import {
  parseUpdatedCharacter,
  parseUpdatedProp,
  parseUpdatedScene,
} from "./assetManagement";
import {
  AssetImageUploadError,
  type AssetImageDirectory,
  type AssetImageRecord,
  type AssetImageSource,
  type AssetImageUploadPort,
  type AssetImageUploadReceipt,
  type AssetImageUploadRun,
} from "./assetImageUpload";
import {
  parseCharacterList,
  parsePropList,
  parseSceneList,
  type AssetLibraryType,
  type Character,
  type Prop,
  type Scene,
} from "./contracts";
import { API_TOKEN_KEY, API_USER_KEY, browserStorage, type StorageLike } from "./storage";

const MAX_IMAGE_BYTES = 10 * 1024 * 1024;
const IMAGE_MIME_TYPES = new Set([
  "image/jpeg",
  "image/png",
  "image/gif",
  "image/webp",
  "image/bmp",
]);
const IMAGE_EXTENSIONS_BY_MIME: Readonly<Record<string, ReadonlySet<string>>> = {
  "image/jpeg": new Set(["jpg", "jpeg"]),
  "image/png": new Set(["png"]),
  "image/gif": new Set(["gif"]),
  "image/webp": new Set(["webp"]),
  "image/bmp": new Set(["bmp"]),
};

export interface AssetImageUploadHttpOptions {
  storage?: StorageLike;
  fetcher?: typeof fetch;
  timeoutMs?: number;
  pageUrl?: string;
}

function fail(kind: AssetImageUploadError["kind"], message: string, status: number | null = null): never {
  throw new AssetImageUploadError(kind, message, status);
}

function actorIdFromStorage(storage: StorageLike): string | null {
  const serialized = storage.getItem(API_USER_KEY);
  if (serialized === null) return null;
  try {
    const user: unknown = JSON.parse(serialized);
    if (typeof user !== "object" || user === null || Array.isArray(user)) return null;
    const id = (user as Record<string, unknown>).id;
    return typeof id === "string" && id.trim() !== "" ? id : null;
  } catch {
    return null;
  }
}

function validateImageFile(file: File): void {
  if (file.size <= 0 || file.size > MAX_IMAGE_BYTES) {
    fail("not-sent", "图片必须大于 0 字节且不超过 10 MiB，未发送上传请求。");
  }
  const mime = file.type.toLowerCase();
  if (!IMAGE_MIME_TYPES.has(mime)) {
    fail("not-sent", "请选择 JPEG、PNG、GIF、WebP 或 BMP 图片，未发送上传请求。");
  }
  const extension = file.name.split(".").at(-1)?.toLowerCase() ?? "";
  if (!IMAGE_EXTENSIONS_BY_MIME[mime]?.has(extension)) {
    fail("not-sent", "图片扩展名与文件类型不匹配，未发送上传请求。");
  }
}

function categoryPath(category: AssetLibraryType): string {
  return category;
}

function parseDirectory(category: AssetLibraryType, value: unknown, seriesId: string): AssetImageRecord[] {
  switch (category) {
    case "characters": return parseCharacterList(value, seriesId);
    case "scenes": return parseSceneList(value, seriesId);
    case "props": return parsePropList(value, seriesId);
  }
}

function parseUpdatedAsset(
  category: AssetLibraryType,
  value: unknown,
  seriesId: string,
  assetId: string,
): AssetImageRecord {
  switch (category) {
    case "characters": return parseUpdatedCharacter(value, seriesId, assetId);
    case "scenes": return parseUpdatedScene(value, seriesId, assetId);
    case "props": return parseUpdatedProp(value, seriesId, assetId);
  }
}

function directoryFor(
  category: AssetLibraryType,
  items: AssetImageRecord[],
  createSource: (url: string) => AssetImageSource,
): AssetImageDirectory {
  const imageSources = new Map<string, AssetImageSource>();
  for (const item of items) {
    if (typeof item.image_url === "string" && item.image_url.trim() !== "") {
      imageSources.set(item.id, createSource(item.image_url));
    }
  }
  switch (category) {
    case "characters": return { category, items: items as Character[], imageSources };
    case "scenes": return { category, items: items as Scene[], imageSources };
    case "props": return { category, items: items as Prop[], imageSources };
  }
}

function validPersistedUrl(value: string, apiOrigin: string, apiBaseUrl: string): URL | null {
  try {
    const url = new URL(value, apiBaseUrl);
    if ((url.protocol !== "http:" && url.protocol !== "https:") || url.username !== "" || url.password !== "") {
      return null;
    }
    if (url.origin === apiOrigin && url.pathname.startsWith("/uploads/") && url.pathname.length > "/uploads/".length) {
      return url;
    }
    if (isLocalHostname(url.hostname)) {
      return null;
    }
    return url;
  } catch {
    return null;
  }
}

function isLocalHostname(hostname: string): boolean {
  const normalized = hostname.toLowerCase().replace(/\.$/, "");
  if (normalized === "localhost" || normalized.endsWith(".localhost")
    || normalized.endsWith(".local") || normalized.endsWith(".internal")) {
    return true;
  }
  if (/^\d{1,3}(?:\.\d{1,3}){3}$/.test(normalized)) {
    const octets = normalized.split(".").map(Number);
    const [first, second] = octets;
    return first === 0 || first === 10 || first === 127
      || (first === 169 && second === 254)
      || (first === 172 && second !== undefined && second >= 16 && second <= 31)
      || (first === 192 && second === 168);
  }
  if (normalized.startsWith("[") && normalized.endsWith("]")) {
    const address = normalized.slice(1, -1);
    return address === "::" || address === "::1"
      || address.startsWith("fc") || address.startsWith("fd") || address.startsWith("fe80:")
      || address.startsWith("::ffff:");
  }
  return false;
}

export function createAssetImageUploadHttpPort(
  apiBaseUrl: string,
  options: AssetImageUploadHttpOptions = {},
): AssetImageUploadPort {
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
          throw new AssetImageUploadError("not-sent", "图片服务地址无效，未发送请求。");
        }
        throw error;
      }
    }
    return resolvedBaseUrl;
  }

  return {
    beginRun(context): AssetImageUploadRun {
      const token = storage.getItem(API_TOKEN_KEY);
      const actorId = actorIdFromStorage(storage);
      if (token === null || token.trim() === "" || actorId !== context.actorId) {
        fail("not-sent", "当前登录用户无法核对，未创建图片操作。");
      }
      if (context.seriesId.trim() === "" || context.assetId.trim() === ""
        || context.asset.id !== context.assetId || context.asset.series_id !== context.seriesId) {
        fail("not-sent", "素材身份无法核对，未创建图片操作。");
      }

      const apiUrl = baseUrl();
      const apiOrigin = new URL(apiUrl).origin;
      const sourceUrls = new WeakMap<object, string>();
      const sourceKinds = new WeakMap<object, "persisted" | "uploaded">();
      const uploadUrls = new WeakMap<object, string>();
      const saveAttempted = new WeakSet<object>();
      let writeInFlight = false;

      function assertCurrent(kind: "not-sent" | "unknown" | "read-error", wasDispatched = false): void {
        let current = false;
        try {
          current = context.isContextCurrent()
            && storage.getItem(API_TOKEN_KEY) === token
            && actorIdFromStorage(storage) === actorId;
        } catch {
          current = false;
        }
        if (!current) {
          fail(wasDispatched && kind === "not-sent" ? "unknown" : kind, "当前账号或目录状态已变化，图片操作结果无法继续确认。");
        }
      }

      function makeSource(url: string, kind: "persisted" | "uploaded"): AssetImageSource {
        const source: AssetImageSource = Object.freeze({ kind: "asset-image-source" });
        sourceUrls.set(source, url);
        sourceKinds.set(source, kind);
        return source;
      }

      function makeReceipt(url: string): AssetImageUploadReceipt {
        const receipt: AssetImageUploadReceipt = Object.freeze({ kind: "asset-image-upload-receipt", url });
        uploadUrls.set(receipt, url);
        sourceUrls.set(receipt, url);
        sourceKinds.set(receipt, "uploaded");
        return receipt;
      }

      function newRequest(signal: AbortSignal, stage: "upload" | "save" | "directory" | "image") {
        assertCurrent("not-sent");
        if (signal.aborted) {
          fail("not-sent", "图片操作已取消，未发送请求。");
        }
        const controller = new AbortController();
        let didTimeout = false;
        const abortFromCaller = () => controller.abort();
        signal.addEventListener("abort", abortFromCaller, { once: true });
        const timer = setTimeout(() => {
          didTimeout = true;
          controller.abort();
        }, timeoutMs);
        const cleanup = () => {
          clearTimeout(timer);
          signal.removeEventListener("abort", abortFromCaller);
        };
        const check = (wasDispatched: boolean) => {
          if (signal.aborted) {
            fail(wasDispatched && (stage === "upload" || stage === "save") ? "unknown" : "read-error", "图片操作已取消，结果需要重新核对。");
          }
          if (didTimeout) {
            fail(wasDispatched && (stage === "upload" || stage === "save") ? "unknown" : "read-error", "图片操作超时，结果需要重新核对。");
          }
          assertCurrent(stage === "image" || stage === "directory" ? "read-error" : "not-sent", wasDispatched);
        };
        return { controller, didTimeout: () => didTimeout, cleanup, check };
      }

      async function fetchResponse(
        url: URL,
        init: RequestInit,
        stage: "upload" | "save" | "directory" | "image",
      ): Promise<{ response: Response; request: ReturnType<typeof newRequest> }> {
        const request = newRequest(init.signal instanceof AbortSignal ? init.signal : new AbortController().signal, stage);
        let dispatched = false;
        let response: Response | null = null;
        try {
          request.check(false);
          dispatched = true;
          response = await fetcher(url, {
            ...init,
            signal: request.controller.signal,
            credentials: "omit",
            cache: "no-store",
            redirect: "error",
            referrerPolicy: "no-referrer",
          });
          request.check(dispatched);
          return { response, request };
        } catch (error) {
          if (response?.body !== null && response?.body !== undefined && !response.bodyUsed) {
            try { await response.body.cancel(); } catch { /* The stream may already be closed. */ }
          }
          request.cleanup();
          if (error instanceof AssetImageUploadError) throw error;
          if (stage === "upload" || stage === "save") {
            fail(dispatched ? "unknown" : "not-sent", request.didTimeout() ? "图片请求超时，结果尚未确认。" : "连接中断，图片请求结果尚未确认。");
          }
          fail("read-error", request.didTimeout() ? "图片读取超时，请重新核对。" : "图片读取失败，请重试只读检查。");
        }
      }

      async function readJsonResponse(
        url: URL,
        init: RequestInit,
        stage: "upload" | "save" | "directory",
      ): Promise<unknown> {
        const { response, request } = await fetchResponse(url, init, stage);
        try {
          if (response.status !== 200) {
            if (stage === "upload" || stage === "save") {
              if ([400, 401, 403, 404, 413, 415, 422].includes(response.status)) {
                fail("rejected", "服务器拒绝了图片操作。", response.status);
              }
              fail("unknown", "服务器响应状态无法确认图片操作结果。", response.status);
            }
            fail("read-error", "目录读取失败，请重新核对。", response.status);
          }
          let value: unknown;
          try {
            value = await response.json();
          } catch {
            if (stage === "upload" || stage === "save") {
              fail("unknown", "服务器已响应，但图片操作回执无法识别。", response.status);
            }
            fail("read-error", "目录响应无法识别，请重新核对。", response.status);
          }
          request.check(true);
          return value;
        } finally {
          if (response.body !== null && !response.bodyUsed) {
            try { await response.body.cancel(); } catch { /* The stream may already be closed. */ }
          }
          request.cleanup();
        }
      }

      const initialImageSource = typeof context.asset.image_url === "string" && context.asset.image_url.trim() !== ""
        ? makeSource(context.asset.image_url, "persisted")
        : null;

      const run: AssetImageUploadRun = {
        isCurrent: () => {
          try {
            return context.isContextCurrent()
              && storage.getItem(API_TOKEN_KEY) === token
              && actorIdFromStorage(storage) === actorId;
          } catch {
            return false;
          }
        },
        canSaveImage: (receipt) => uploadUrls.has(receipt) && !saveAttempted.has(receipt),
        canReadImage: (source) => sourceUrls.has(source),
        initialImageSource,
        async uploadImage(file, signal) {
          assertCurrent("not-sent");
          if (writeInFlight) fail("not-sent", "另一个图片写入仍在进行，未发送重复请求。");
          validateImageFile(file);
          const form = new FormData();
          form.append("file", file, file.name);
          writeInFlight = true;
          try {
            const payload = await readJsonResponse(
              new URL(`${apiUrl}/upload/image`),
              {
                method: "POST",
                headers: { Accept: "application/json", Authorization: `Bearer ${token}` },
                body: form,
                signal,
              },
              "upload",
            );
            if (typeof payload !== "object" || payload === null || Array.isArray(payload)
              || typeof (payload as Record<string, unknown>).url !== "string") {
              fail("unknown", "服务器已响应，但图片上传地址无法识别。", 200);
            }
            const rawUrl = (payload as Record<string, unknown>).url;
            if (typeof rawUrl !== "string") {
              fail("unknown", "服务器已响应，但图片上传地址无法识别。", 200);
            }
            const canonicalUrl = validPersistedUrl(rawUrl, apiOrigin, apiUrl);
            const isOwnedUpload = canonicalUrl !== null && canonicalUrl.origin === apiOrigin
              && canonicalUrl.pathname.startsWith("/uploads/")
              && canonicalUrl.pathname.length > "/uploads/".length;
            if (canonicalUrl === null || (!isOwnedUpload && isLocalHostname(canonicalUrl.hostname))) {
              fail("unknown", "服务器已响应，但上传回执不是有效的公开图片地址。", 200);
            }
            return makeReceipt(rawUrl);
          } finally {
            writeInFlight = false;
          }
        },
        async saveImage(receipt, signal) {
          assertCurrent("not-sent");
          if (writeInFlight) fail("not-sent", "另一个图片写入仍在进行，未发送重复请求。");
          const uploadedUrl = uploadUrls.get(receipt);
          if (uploadedUrl === undefined || saveAttempted.has(receipt)) {
            fail("not-sent", "图片上传回执无效或已经提交，未发送重复更新。");
          }
          const encodedAssetId = encodeURIComponent(context.assetId);
          const endpoint = new URL(`${apiUrl}/${categoryPath(context.category)}/${encodedAssetId}`);
          writeInFlight = true;
          try {
            const payload = await readJsonResponse(endpoint, {
              method: "PUT",
              headers: {
                Accept: "application/json",
                Authorization: `Bearer ${token}`,
                "Content-Type": "application/json",
              },
              body: JSON.stringify({ image_url: uploadedUrl }),
              signal,
            }, "save");
            saveAttempted.add(receipt);
            const updated = parseUpdatedAsset(context.category, payload, context.seriesId, context.assetId);
            if (updated.image_url !== uploadedUrl) {
              fail("unknown", "服务器返回了素材回执，但图片地址与本次上传不一致。", 200);
            }
            assertCurrent("unknown", true);
            return updated;
          } catch (error) {
            if (!(error instanceof AssetImageUploadError) || error.kind !== "not-sent") {
              saveAttempted.add(receipt);
            }
            throw error;
          } finally {
            writeInFlight = false;
          }
        },
        async readDirectory(signal) {
          const encodedSeriesId = encodeURIComponent(context.seriesId);
          const payload = await readJsonResponse(
            new URL(`${apiUrl}/series/${encodedSeriesId}/${categoryPath(context.category)}`),
            {
              method: "GET",
              headers: { Accept: "application/json", Authorization: `Bearer ${token}` },
              signal,
            },
            "directory",
          );
          let items: AssetImageRecord[];
          try {
            items = parseDirectory(context.category, payload, context.seriesId);
          } catch {
            fail("read-error", "目录响应格式无效，请重新核对。", 200);
          }
          if (!run.isCurrent()) fail("read-error", "登录或目录状态已变化，目录结果未接受。");
          return directoryFor(context.category, items, (url) => makeSource(url, "persisted"));
        },
        async readImage(source, signal) {
          assertCurrent("read-error");
          const rawUrl = sourceUrls.get(source);
          const sourceKind = sourceKinds.get(source);
          if (rawUrl === undefined || sourceKind === undefined) {
            fail("read-error", "图片来源不属于当前操作，拒绝读取。");
          }
          if (sourceKind === "uploaded" && uploadUrls.has(source as AssetImageUploadReceipt)) {
            // Upload receipts are minted by this exact run and may be read directly from /uploads/.
          } else if (sourceKind !== "persisted") {
            fail("read-error", "图片来源不属于当前目录或上传回执，拒绝读取。");
          }
          const persistedUrl = validPersistedUrl(rawUrl, apiOrigin, apiUrl);
          if (persistedUrl === null) fail("read-error", "图片地址不是可安全读取的 HTTP 图片地址。");
          const isOwnedUpload = persistedUrl.origin === apiOrigin
            && persistedUrl.pathname.startsWith("/uploads/")
            && persistedUrl.pathname.length > "/uploads/".length;
          const proxyTarget = /^https?:\/\//i.test(rawUrl) ? rawUrl : persistedUrl.href;
          const requestUrl = isOwnedUpload
            ? persistedUrl
            : new URL(`${apiUrl}/chat/proxy-image?${new URLSearchParams({ url: proxyTarget }).toString()}`);
          const { response, request } = await fetchResponse(requestUrl, {
            method: "GET",
            headers: { Accept: "image/*", Authorization: `Bearer ${token}` },
            signal,
          }, "image");
          let reader: ReadableStreamDefaultReader<Uint8Array> | null = null;
          let streamDone = false;
          try {
            if (response.status !== 200) fail("read-error", "图片读取失败，请重新核对。", response.status);
            const mime = response.headers.get("content-type")?.split(";", 1)[0]?.trim().toLowerCase() ?? "";
            if (!IMAGE_MIME_TYPES.has(mime)) fail("read-error", "服务返回的内容不是受支持的图片类型。");
            const lengthHeader = response.headers.get("content-length");
            if (lengthHeader !== null && Number(lengthHeader) > MAX_IMAGE_BYTES) {
              fail("read-error", "图片超过 10 MiB，未显示。");
            }
            if (response.body === null) fail("read-error", "图片响应没有内容。");
            reader = response.body.getReader();
            const chunks: Uint8Array[] = [];
            let totalBytes = 0;
            while (true) {
              request.check(true);
              const chunk = await reader.read();
              if (chunk.done) {
                streamDone = true;
                break;
              }
              totalBytes += chunk.value.byteLength;
              if (totalBytes > MAX_IMAGE_BYTES) fail("read-error", "图片超过 10 MiB，未显示。");
              if (chunk.value.byteLength > 0) chunks.push(chunk.value);
            }
            if (totalBytes === 0) fail("read-error", "服务返回了空图片内容，未显示。");
            request.check(true);
            const blobParts = chunks.map((chunk) => chunk.slice().buffer as ArrayBuffer);
            return new Blob(blobParts, { type: mime });
          } catch (error) {
            if (error instanceof AssetImageUploadError) throw error;
            fail("read-error", request.didTimeout() ? "图片读取超时，请重新核对。" : "图片读取失败，请重新核对。");
          } finally {
            if (reader !== null && !streamDone) {
              try { await reader.cancel(); } catch { /* The stream may already be aborted. */ }
            } else if (reader === null && response.body !== null && !response.bodyUsed) {
              try { await response.body.cancel(); } catch { /* The stream may already be closed. */ }
            }
            request.cleanup();
          }
        },
      };

      return run;
    },
  };
}
