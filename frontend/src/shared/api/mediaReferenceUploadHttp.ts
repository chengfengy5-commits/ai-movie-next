import { ApiConfigurationError, resolveApiBaseUrl } from "./config";
import {
  MediaReferenceUploadError,
  type MediaReferenceKind,
  type MediaReferenceUploadPort,
  type MediaReferenceUploadRunContext,
} from "./mediaReferenceUpload";
import { API_TOKEN_KEY, API_USER_KEY, browserStorage, clearApiSession, type StorageLike } from "./storage";

const DEFAULT_REQUEST_TIMEOUT_MS = 30_000;
const MAX_IMAGE_BYTES = 10 * 1024 * 1024;
const MAX_AUDIO_BYTES = 10 * 1024 * 1024;
const MAX_VIDEO_BYTES = 50 * 1024 * 1024;
const CONTROL_CHARACTERS = /[\u0000-\u001f\u007f-\u009f]/u;
const FILE_TYPES: Readonly<Record<MediaReferenceKind, Readonly<Record<string, ReadonlySet<string>>>>> = {
  image: {
    jpg: new Set(["image/jpeg", "image/pjpeg"]),
    jpeg: new Set(["image/jpeg", "image/pjpeg"]),
    png: new Set(["image/png"]),
    gif: new Set(["image/gif"]),
    webp: new Set(["image/webp"]),
    bmp: new Set(["image/bmp", "image/x-ms-bmp"]),
  },
  audio: {
    mp3: new Set(["audio/mpeg", "audio/mp3", "audio/x-mp3", "audio/mpeg3", "audio/x-mpeg-3"]),
    wav: new Set(["audio/wav", "audio/x-wav", "audio/wave", "audio/vnd.wave"]),
    ogg: new Set(["audio/ogg"]),
    m4a: new Set(["audio/mp4", "audio/x-m4a", "audio/m4a"]),
    aac: new Set(["audio/aac", "audio/aacp", "audio/x-aac"]),
    flac: new Set(["audio/flac", "audio/x-flac"]),
    wma: new Set(["audio/x-ms-wma", "audio/wma"]),
  },
  video: {
    mp4: new Set(["video/mp4"]),
    webm: new Set(["video/webm"]),
    mov: new Set(["video/quicktime"]),
    m4v: new Set(["video/x-m4v", "video/mp4"]),
    avi: new Set(["video/x-msvideo", "video/avi"]),
    mkv: new Set(["video/x-matroska"]),
  },
};

export interface MediaReferenceUploadHttpOptions {
  readonly storage?: StorageLike;
  readonly fetcher?: typeof fetch;
  readonly timeoutMs?: number;
  readonly pageUrl?: string;
}

function fail(
  kind: MediaReferenceUploadError["kind"],
  message: string,
  status: number | null = null,
): never {
  throw new MediaReferenceUploadError(kind, message, status);
}

function isIdentifier(value: unknown): value is string {
  return typeof value === "string" && value.trim() === value && value.length >= 1 && value.length <= 128
    && !CONTROL_CHARACTERS.test(value);
}

function actorIdFromStorage(storage: StorageLike): string | null {
  try {
    const serialized = storage.getItem(API_USER_KEY);
    if (serialized === null) return null;
    const user: unknown = JSON.parse(serialized);
    if (typeof user !== "object" || user === null || Array.isArray(user)) return null;
    const id = (user as Record<string, unknown>).id;
    return isIdentifier(id) ? id : null;
  } catch {
    return null;
  }
}

function validateFile(kind: MediaReferenceKind, file: File): void {
  const limits = { image: MAX_IMAGE_BYTES, audio: MAX_AUDIO_BYTES, video: MAX_VIDEO_BYTES };
  if (file.size <= 0 || file.size > limits[kind]) {
    const max = kind === "video" ? "50 MiB" : "10 MiB";
    fail("not-sent", `文件必须大于 0 字节且不超过 ${max}，未发送上传请求。`);
  }
  const extensionStart = file.name.lastIndexOf(".");
  const extension = extensionStart > 0 ? file.name.slice(extensionStart + 1).toLowerCase() : "";
  const acceptedTypes = FILE_TYPES[kind][extension];
  const mime = file.type.split(";", 1)[0]?.trim().toLowerCase() ?? "";
  if (acceptedTypes === undefined || (mime !== "" && !acceptedTypes.has(mime))) {
    const mediaName = kind === "image" ? "图片" : kind === "audio" ? "音频" : "视频";
    fail("not-sent", `请选择扩展名与${mediaName}类型匹配的文件，未发送上传请求。`);
  }
}

function validUploadUrl(value: unknown): value is string {
  if (typeof value !== "string" || value.trim() !== value || value === "" || CONTROL_CHARACTERS.test(value)) return false;
  try {
    const url = new URL(value);
    return (url.protocol === "http:" || url.protocol === "https:")
      && url.hostname !== ""
      && url.username === ""
      && url.password === ""
      && url.hash === "";
  } catch {
    return false;
  }
}

function hasExactUrl(value: unknown): value is { url: string } {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  const record = value as Record<string, unknown>;
  return Reflect.ownKeys(record).length === 1
    && Object.prototype.hasOwnProperty.call(record, "url")
    && validUploadUrl(record.url);
}

export function createMediaReferenceUploadHttpPort(
  apiBaseUrl: string,
  options: MediaReferenceUploadHttpOptions = {},
): MediaReferenceUploadPort {
  const storage = options.storage ?? browserStorage();
  const fetcher = options.fetcher ?? globalThis.fetch.bind(globalThis);
  const timeoutMs = options.timeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS;
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
    fail("not-sent", "上传请求超时配置无效，未发送请求。");
  }

  let resolvedBaseUrl: string;
  try {
    resolvedBaseUrl = resolveApiBaseUrl(apiBaseUrl, options.pageUrl ?? "http://127.0.0.1/");
  } catch (error) {
    if (error instanceof ApiConfigurationError) fail("not-sent", "上传服务地址无效，未发送请求。");
    throw error;
  }

  return {
    beginRun(context: MediaReferenceUploadRunContext) {
      if (!isIdentifier(context.actorId) || typeof context.isContextCurrent !== "function") {
        fail("not-sent", "当前页面身份无法核对，未创建上传操作。");
      }
      let token: string | null = null;
      try {
        token = storage.getItem(API_TOKEN_KEY);
      } catch {
        // An inaccessible session cannot authorize an upload.
      }
      if (token === null || token.trim() === "" || token.trim() !== token || CONTROL_CHARACTERS.test(token)
        || actorIdFromStorage(storage) !== context.actorId) {
        fail("not-sent", "当前登录身份无法核对，未创建上传操作。");
      }

      const capturedActorId = context.actorId;
      const capturedContextCheck = context.isContextCurrent;
      const contextIsCurrent = capturedContextCheck.bind(context);
      let uploadInFlight = false;
      const isCurrent = () => {
        try {
          return context.actorId === capturedActorId
            && context.isContextCurrent === capturedContextCheck
            && contextIsCurrent()
            && storage.getItem(API_TOKEN_KEY) === token
            && actorIdFromStorage(storage) === capturedActorId;
        } catch {
          return false;
        }
      };

      return {
        isCurrent,
        async upload(kind: MediaReferenceKind, file: File, signal: AbortSignal): Promise<string> {
          if (uploadInFlight) fail("not-sent", "另一个参考素材仍在上传，未发送重复请求。");
          if (kind !== "image" && kind !== "audio" && kind !== "video") {
            fail("not-sent", "参考素材类型无效，未发送上传请求。");
          }
          validateFile(kind, file);
          if (!isCurrent() || signal.aborted) fail("not-sent", "登录或当前生成表单已变化，未发送上传请求。");

          const form = new FormData();
          form.append("file", file, file.name);
          const controller = new AbortController();
          let timedOut = false;
          let dispatched = false;
          let rejectAbort: (reason?: unknown) => void = () => undefined;
          const aborted = new Promise<never>((_resolve, reject) => { rejectAbort = reject; });
          void aborted.catch(() => undefined);
          const rejectOnAbort = () => rejectAbort(controller.signal.reason ?? new DOMException("Request aborted", "AbortError"));
          const abortFromCaller = () => controller.abort(signal.reason);
          signal.addEventListener("abort", abortFromCaller, { once: true });
          if (signal.aborted) abortFromCaller();
          const timeout = globalThis.setTimeout(() => {
            timedOut = true;
            controller.abort(new DOMException("Request timed out", "TimeoutError"));
          }, timeoutMs);
          controller.signal.addEventListener("abort", rejectOnAbort, { once: true });
          uploadInFlight = true;
          try {
            if (!isCurrent() || controller.signal.aborted) {
              fail("not-sent", "登录或当前生成表单已变化，未发送上传请求。");
            }
            dispatched = true;
            const responsePromise = fetcher(new URL(`${resolvedBaseUrl}/upload/${kind}`), {
              method: "POST",
              headers: { Accept: "application/json", Authorization: `Bearer ${token}` },
              body: form,
              signal: controller.signal,
              credentials: "omit",
              cache: "no-store",
              redirect: "error",
              referrerPolicy: "no-referrer",
            });
            void responsePromise.then((lateResponse) => {
              if (controller.signal.aborted) void lateResponse.body?.cancel().catch(() => undefined);
            }, () => undefined);
            const response = await Promise.race([responsePromise, aborted]);
            if (!isCurrent()) fail("unknown", "上传已发送，但当前页面身份已变化，结果未加入参考列表。", response.status);
            if (response.status === 401) {
              try {
                if (storage.getItem(API_TOKEN_KEY) === token) clearApiSession(storage);
              } catch {
                // The caller still receives the authentication failure.
              }
              fail("unauthorized", "登录状态已失效，请重新登录。", response.status);
            }
            if (response.status !== 200) {
              const rejectedStatus = [400, 403, 404, 413, 415, 422].includes(response.status);
              fail(rejectedStatus ? "rejected" : "unknown", "上传服务未能确认本次文件结果。", response.status);
            }
            let payload: unknown;
            try {
              payload = await Promise.race([response.json(), aborted]);
            } catch {
              fail("unknown", "文件已发送，但上传回执无法识别；不会自动重传。", response.status);
            }
            if (!isCurrent()) fail("unknown", "上传已发送，但当前页面身份已变化，结果未加入参考列表。", response.status);
            if (!hasExactUrl(payload)) fail("unknown", "文件已发送，但上传地址无法核对；不会自动重传。", response.status);
            return payload.url;
          } catch (error) {
            if (error instanceof MediaReferenceUploadError) throw error;
            if (signal.aborted) {
              fail(dispatched ? "unknown" : "not-sent", dispatched ? "文件已发送但上传结果未确认；不会自动重传。" : "上传已取消，未发送文件。");
            }
            if (timedOut) fail(dispatched ? "unknown" : "not-sent", dispatched ? "上传超时，结果未确认；不会自动重传。" : "上传超时，未发送文件。");
            fail(dispatched ? "unknown" : "not-sent", dispatched ? "连接中断，上传结果未确认；不会自动重传。" : "连接失败，未发送文件。");
          } finally {
            globalThis.clearTimeout(timeout);
            signal.removeEventListener("abort", abortFromCaller);
            controller.signal.removeEventListener("abort", rejectOnAbort);
            uploadInFlight = false;
          }
        },
      };
    },
  };
}
