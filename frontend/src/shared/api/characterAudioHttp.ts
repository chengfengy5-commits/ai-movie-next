import { ApiConfigurationError, resolveApiBaseUrl } from "./config";
import { parseCharacterList, type Character } from "./contracts";
import { parseUpdatedCharacter } from "./assetManagement";
import {
  CharacterAudioError,
  type CharacterAudioDirectory,
  type CharacterAudioPort,
  type CharacterAudioRun,
  type CharacterAudioSource,
  type CharacterAudioTarget,
  type CharacterAudioUploadReceipt,
} from "./characterAudio";
import { API_TOKEN_KEY, API_USER_KEY, browserStorage, type StorageLike } from "./storage";

const MAX_AUDIO_BYTES = 10 * 1024 * 1024;
const AUDIO_MIME_TYPES_BY_EXTENSION: Readonly<Record<string, ReadonlySet<string>>> = {
  mp3: new Set(["audio/mpeg", "audio/mp3", "audio/x-mp3", "audio/mpeg3", "audio/x-mpeg-3"]),
  wav: new Set(["audio/wav", "audio/x-wav", "audio/wave", "audio/vnd.wave"]),
  ogg: new Set(["audio/ogg"]),
  m4a: new Set(["audio/mp4", "audio/x-m4a", "audio/m4a"]),
  aac: new Set(["audio/aac", "audio/aacp", "audio/x-aac"]),
  flac: new Set(["audio/flac", "audio/x-flac"]),
  wma: new Set(["audio/x-ms-wma", "audio/wma"]),
};
const AUDIO_MIME_TYPES = new Set(Object.values(AUDIO_MIME_TYPES_BY_EXTENSION).flatMap((types) => [...types]));
const receiptUrls = new WeakMap<object, string>();

export interface CharacterAudioHttpOptions {
  readonly storage?: StorageLike;
  readonly fetcher?: typeof fetch;
  readonly timeoutMs?: number;
  readonly pageUrl?: string;
}

interface SafeAudioUrl {
  readonly url: URL;
  readonly ownedUpload: boolean;
}

interface RequestControl {
  readonly controller: AbortController;
  didTimeout(): boolean;
  cleanup(): void;
  check(dispatched: boolean): void;
}

function fail(kind: CharacterAudioError["kind"], message: string, status: number | null = null): never {
  throw new CharacterAudioError(kind, message, status);
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

function validateAudioFile(file: File): void {
  if (file.size <= 0 || file.size > MAX_AUDIO_BYTES) {
    fail("not-sent", "音频文件必须大于 0 字节且不超过 10 MiB，未发送上传请求。");
  }
  const extension = file.name.split(".").at(-1)?.toLowerCase() ?? "";
  const mime = file.type.split(";", 1)[0]?.trim().toLowerCase() ?? "";
  const acceptedMimeTypes = AUDIO_MIME_TYPES_BY_EXTENSION[extension];
  if (acceptedMimeTypes === undefined
    || (mime !== "" && (!mime.startsWith("audio/") || !acceptedMimeTypes.has(mime)))) {
    fail("not-sent", "请选择扩展名与音频类型匹配的 MP3、WAV、OGG、M4A、AAC、FLAC 或 WMA 文件，未发送上传请求。");
  }
}

function isLocalHostname(hostname: string): boolean {
  const normalized = hostname.toLowerCase().replace(/\.$/, "");
  if (normalized === "localhost" || normalized.endsWith(".localhost")
    || normalized.endsWith(".local") || normalized.endsWith(".internal")) {
    return true;
  }
  if (/^\d{1,3}(?:\.\d{1,3}){3}$/.test(normalized)) {
    const [first, second] = normalized.split(".").map(Number);
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

function isUploadPath(pathname: string): boolean {
  if (!pathname.startsWith("/uploads/") || pathname.length <= "/uploads/".length
    || /%(?:2f|5c|2e)/i.test(pathname)) {
    return false;
  }
  return pathname.slice("/uploads/".length).split("/").every((segment) => segment !== "" && segment !== "." && segment !== "..");
}

function safeAudioUrl(value: string, apiOrigin: string, apiBaseUrl: string): SafeAudioUrl | null {
  if (value.trim() === "") return null;
  let url: URL;
  try {
    url = new URL(value, apiBaseUrl);
  } catch {
    return null;
  }
  if ((url.protocol !== "http:" && url.protocol !== "https:") || url.username !== "" || url.password !== "") {
    return null;
  }
  if (url.origin === apiOrigin && isUploadPath(url.pathname)) return { url, ownedUpload: true };
  if (!/^https?:\/\//i.test(value) || isLocalHostname(url.hostname)) return null;
  return { url, ownedUpload: false };
}

function makeDirectory(
  items: Character[],
  createSource: (url: string) => CharacterAudioSource,
): CharacterAudioDirectory {
  const audioSources = new Map<string, CharacterAudioSource>();
  for (const character of items) {
    if (typeof character.audio_url === "string" && character.audio_url.trim() !== "") {
      audioSources.set(character.id, createSource(character.audio_url));
    }
  }
  return { category: "characters", items, audioSources };
}

function targetHasAudio(target: CharacterAudioTarget): boolean {
  return Object.prototype.hasOwnProperty.call(target, "audio");
}

function targetHasVoiceReference(target: CharacterAudioTarget): boolean {
  return Object.prototype.hasOwnProperty.call(target, "voice_ref");
}

export function createCharacterAudioHttpPort(
  apiBaseUrl: string,
  options: CharacterAudioHttpOptions = {},
): CharacterAudioPort {
  const storage = options.storage ?? browserStorage();
  const fetcher = options.fetcher ?? globalThis.fetch.bind(globalThis);
  const timeoutMs = options.timeoutMs ?? 15_000;
  let resolvedBaseUrl: string | null = null;

  function baseUrl(): string {
    if (resolvedBaseUrl !== null) return resolvedBaseUrl;
    try {
      resolvedBaseUrl = resolveApiBaseUrl(apiBaseUrl, options.pageUrl ?? "http://127.0.0.1/");
      return resolvedBaseUrl;
    } catch (error) {
      if (error instanceof ApiConfigurationError) {
        fail("not-sent", "音频服务地址无效，未发送请求。");
      }
      throw error;
    }
  }

  return {
    beginRun(context): CharacterAudioRun {
      const token = storage.getItem(API_TOKEN_KEY);
      const actorId = actorIdFromStorage(storage);
      if (token === null || token.trim() === "" || actorId !== context.actorId) {
        fail("not-sent", "当前登录用户无法核对，未创建音频操作。");
      }
      if (context.seriesId.trim() === "" || context.characterId.trim() === ""
        || context.character.id !== context.characterId || context.character.series_id !== context.seriesId) {
        fail("not-sent", "角色身份无法核对，未创建音频操作。");
      }

      const apiUrl = baseUrl();
      const apiOrigin = new URL(apiUrl).origin;
      const localUrls = new WeakMap<object, string>();
      const uploadUrls = new WeakMap<object, string>();
      const attemptedTargets = new WeakSet<object>();
      const attemptedReceipts = new WeakSet<object>();
      let writeInFlight = false;

      function assertCurrent(kind: CharacterAudioError["kind"], dispatched = false): void {
        let current = false;
        try {
          current = context.isContextCurrent()
            && storage.getItem(API_TOKEN_KEY) === token
            && actorIdFromStorage(storage) === actorId;
        } catch {
          current = false;
        }
        if (!current) {
          fail(dispatched && kind === "not-sent" ? "unknown" : kind, "当前账号或角色目录状态已变化，音频操作结果无法继续确认。");
        }
      }

      function makeSource(url: string): CharacterAudioSource {
        const source: CharacterAudioSource = Object.freeze({ kind: "character-audio-source" });
        localUrls.set(source, url);
        return source;
      }

      function makeReceipt(url: string): CharacterAudioUploadReceipt {
        const receipt: CharacterAudioUploadReceipt = Object.freeze({ kind: "character-audio-upload-receipt" });
        uploadUrls.set(receipt, url);
        localUrls.set(receipt, url);
        receiptUrls.set(receipt, url);
        return receipt;
      }

      function newRequest(signal: AbortSignal, stage: "upload" | "save" | "directory" | "audio"): RequestControl {
        assertCurrent(stage === "directory" || stage === "audio" ? "read-error" : "not-sent");
        if (signal.aborted) fail("not-sent", "音频操作已取消，未发送请求。");
        const controller = new AbortController();
        let didTimeout = false;
        const abortFromCaller = () => controller.abort();
        signal.addEventListener("abort", abortFromCaller, { once: true });
        const timer = setTimeout(() => {
          didTimeout = true;
          controller.abort();
        }, timeoutMs);
        return {
          controller,
          didTimeout: () => didTimeout,
          cleanup: () => {
            clearTimeout(timer);
            signal.removeEventListener("abort", abortFromCaller);
          },
          check: (dispatched) => {
            if (signal.aborted) {
              fail(dispatched && (stage === "upload" || stage === "save") ? "unknown" : "read-error", "音频操作已取消，结果需要重新核对。");
            }
            if (didTimeout) {
              fail(dispatched && (stage === "upload" || stage === "save") ? "unknown" : "read-error", "音频操作超时，结果需要重新核对。");
            }
            assertCurrent(stage === "directory" || stage === "audio" ? "read-error" : "not-sent", dispatched);
          },
        };
      }

      async function fetchResponse(
        url: URL,
        init: RequestInit,
        stage: "upload" | "save" | "directory" | "audio",
      ): Promise<{ response: Response; request: RequestControl }> {
        const signal = init.signal instanceof AbortSignal ? init.signal : new AbortController().signal;
        const request = newRequest(signal, stage);
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
            try { await response.body.cancel(); } catch { /* The response stream may already be closed. */ }
          }
          request.cleanup();
          if (error instanceof CharacterAudioError) throw error;
          if (stage === "upload" || stage === "save") {
            fail(dispatched ? "unknown" : "not-sent", request.didTimeout() ? "音频请求超时，结果尚未确认。" : "连接中断，音频请求结果尚未确认。");
          }
          fail("read-error", request.didTimeout() ? "音频读取超时，请重新核对。" : "音频读取失败，请重试只读检查。");
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
            if (stage === "directory") fail("read-error", "角色目录读取失败，请重新核对。", response.status);
            if ([400, 401, 403, 404, 413, 415, 422].includes(response.status)) {
              fail("rejected", "服务器拒绝了音频操作。", response.status);
            }
            fail("unknown", "服务器响应状态无法确认音频操作结果。", response.status);
          }
          let payload: unknown;
          try {
            payload = await response.json();
          } catch {
            fail(stage === "directory" ? "read-error" : "unknown", "服务器已响应，但音频操作回执无法识别。", 200);
          }
          request.check(true);
          return payload;
        } finally {
          if (response.body !== null && !response.bodyUsed) {
            try { await response.body.cancel(); } catch { /* The response stream may already be closed. */ }
          }
          request.cleanup();
        }
      }

      function targetAudioUrl(target: CharacterAudioTarget): string | null | undefined {
        if (!targetHasAudio(target)) return undefined;
        if (target.audio === null) return null;
        if (target.audio === undefined) return undefined;
        return receiptUrls.get(target.audio);
      }

      const run: CharacterAudioRun = {
        isCurrent: () => {
          try {
            return context.isContextCurrent()
              && storage.getItem(API_TOKEN_KEY) === token
              && actorIdFromStorage(storage) === actorId;
          } catch {
            return false;
          }
        },
        canSaveAudio: (receipt) => uploadUrls.has(receipt) && !attemptedReceipts.has(receipt),
        canReadAudio: (source) => localUrls.has(source),
        async uploadAudio(file, signal) {
          assertCurrent("not-sent");
          if (writeInFlight) fail("not-sent", "另一个音频写入仍在进行，未发送重复请求。");
          validateAudioFile(file);
          const form = new FormData();
          form.append("file", file, file.name);
          writeInFlight = true;
          try {
            const payload = await readJsonResponse(
              new URL(`${apiUrl}/upload/audio`),
              { method: "POST", headers: { Accept: "application/json", Authorization: `Bearer ${token}` }, body: form, signal },
              "upload",
            );
            if (typeof payload !== "object" || payload === null || Array.isArray(payload)
              || typeof (payload as Record<string, unknown>).url !== "string") {
              fail("unknown", "服务器已响应，但音频上传回执无法识别。", 200);
            }
            const rawUrl = (payload as Record<string, unknown>).url as string;
            if (safeAudioUrl(rawUrl, apiOrigin, apiUrl) === null) {
              fail("unknown", "服务器已响应，但上传回执不是安全的音频地址。", 200);
            }
            return makeReceipt(rawUrl);
          } finally {
            writeInFlight = false;
          }
        },
        async save(target, signal) {
          assertCurrent("not-sent");
          if (writeInFlight) fail("not-sent", "另一个音频写入仍在进行，未发送重复请求。");
          if (attemptedTargets.has(target)) fail("not-sent", "本次音频更新已经发送，不能自动重放。");
          const hasAudio = targetHasAudio(target);
          const hasVoiceReference = targetHasVoiceReference(target);
          if (!hasAudio && !hasVoiceReference) fail("not-sent", "没有待保存的音频或音色变化，未发送请求。");
          if (hasAudio && target.audio !== null && (target.audio === undefined || !uploadUrls.has(target.audio))) {
            fail("not-sent", "音频上传回执不属于当前操作，未发送目录更新。");
          }
          if (target.audio !== null && target.audio !== undefined && attemptedReceipts.has(target.audio)) {
            fail("not-sent", "本次音频回执已经提交，不能自动重放。");
          }
          if (hasVoiceReference && typeof target.voice_ref !== "string") {
            fail("not-sent", "音色参考文本无效，未发送目录更新。");
          }
          const body: Record<string, unknown> = {};
          if (hasAudio) body.audio_url = target.audio === null ? null : uploadUrls.get(target.audio as CharacterAudioUploadReceipt);
          if (hasVoiceReference) body.voice_ref = target.voice_ref;
          const endpoint = new URL(`${apiUrl}/characters/${encodeURIComponent(context.characterId)}`);
          attemptedTargets.add(target);
          if (target.audio !== null && target.audio !== undefined) attemptedReceipts.add(target.audio);
          writeInFlight = true;
          try {
            const payload = await readJsonResponse(endpoint, {
              method: "PUT",
              headers: { Accept: "application/json", Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
              body: JSON.stringify(body),
              signal,
            }, "save");
            let updated: Character;
            try {
              updated = parseUpdatedCharacter(payload, context.seriesId, context.characterId);
            } catch {
              fail("unknown", "服务器已响应，但角色更新回执无法识别。", 200);
            }
            requestCheckAfterWrite();
            return updated;
          } catch (error) {
            if (error instanceof CharacterAudioError && error.kind === "not-sent") {
              attemptedTargets.delete(target);
              if (target.audio !== null && target.audio !== undefined) attemptedReceipts.delete(target.audio);
            }
            throw error;
          } finally {
            writeInFlight = false;
          }
        },
        async readDirectory(signal) {
          const endpoint = new URL(`${apiUrl}/series/${encodeURIComponent(context.seriesId)}/characters`);
          const payload = await readJsonResponse(endpoint, {
            method: "GET", headers: { Accept: "application/json", Authorization: `Bearer ${token}` }, signal,
          }, "directory");
          let items: Character[];
          try {
            items = parseCharacterList(payload, context.seriesId);
          } catch {
            fail("read-error", "角色目录响应格式无效，请重新核对。", 200);
          }
          if (!run.isCurrent()) fail("read-error", "登录或角色目录状态已变化，目录结果未接受。");
          return makeDirectory(items, makeSource);
        },
        async readAudio(source, signal) {
          assertCurrent("read-error");
          const rawUrl = localUrls.get(source);
          if (rawUrl === undefined) fail("read-error", "音频来源不属于当前目录或上传回执，拒绝读取。");
          const safe = safeAudioUrl(rawUrl, apiOrigin, apiUrl);
          if (safe === null) fail("read-error", "音频地址不是安全的 HTTP 音频地址。");
          const headers = new Headers({ Accept: "audio/*" });
          if (safe.ownedUpload) headers.set("Authorization", `Bearer ${token}`);
          const { response, request } = await fetchResponse(safe.url, { method: "GET", headers, signal }, "audio");
          let reader: ReadableStreamDefaultReader<Uint8Array> | null = null;
          let streamDone = false;
          try {
            if (response.status !== 200) fail("read-error", "音频读取失败，请重新核对。", response.status);
            const mime = response.headers.get("content-type")?.split(";", 1)[0]?.trim().toLowerCase() ?? "";
            if (!AUDIO_MIME_TYPES.has(mime)) fail("read-error", "服务返回的内容不是受支持的音频类型。");
            const lengthHeader = response.headers.get("content-length");
            if (lengthHeader !== null && Number(lengthHeader) > MAX_AUDIO_BYTES) {
              fail("read-error", "音频超过 10 MiB，未提供试听。");
            }
            if (response.body === null) fail("read-error", "音频响应没有内容。");
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
              if (totalBytes > MAX_AUDIO_BYTES) fail("read-error", "音频超过 10 MiB，未提供试听。");
              if (chunk.value.byteLength > 0) chunks.push(chunk.value);
            }
            if (totalBytes === 0) fail("read-error", "服务返回了空音频内容，未提供试听。");
            request.check(true);
            return new Blob(chunks.map((chunk) => chunk.slice().buffer as ArrayBuffer), { type: mime });
          } catch (error) {
            if (error instanceof CharacterAudioError) throw error;
            fail("read-error", request.didTimeout() ? "音频读取超时，请重新核对。" : "音频读取失败，请重新核对。");
          } finally {
            if (reader !== null && !streamDone) {
              try { await reader.cancel(); } catch { /* The response stream may already be aborted. */ }
            } else if (reader === null && response.body !== null && !response.bodyUsed) {
              try { await response.body.cancel(); } catch { /* The response stream may already be closed. */ }
            }
            request.cleanup();
          }
        },
        matchesTarget(character, target) {
          if (character.id !== context.characterId || character.series_id !== context.seriesId) return false;
          if (targetHasAudio(target)) {
            const expectedAudio = target.audio === null ? null : targetAudioUrl(target);
            if (expectedAudio === undefined || character.audio_url !== expectedAudio) return false;
          }
          if (targetHasVoiceReference(target) && character.voice_ref !== target.voice_ref) return false;
          return true;
        },
      };

      function requestCheckAfterWrite(): void {
        assertCurrent("unknown", true);
      }

      return run;
    },
  };
}
