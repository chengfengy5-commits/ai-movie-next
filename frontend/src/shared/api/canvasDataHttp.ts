import { API_TOKEN_KEY, API_USER_KEY, clearApiSession, type StorageLike } from "./storage";
import { CanvasDataError, parseCanvasEnvelope, type CanvasDataPort } from "./canvasData";

const MAX_IMAGE_BYTES = 10 * 1024 * 1024;
const IMAGE_TYPES = new Set(["image/avif", "image/bmp", "image/gif", "image/jpeg", "image/png", "image/webp"]);
const IMAGE_READ_TIMEOUT_MS = 15_000;
const CONTROL_OR_SPACE = /[\s\u0000-\u001f\u007f]/u;

export interface CanvasDataHttpOptions {
  storage: StorageLike;
  fetcher: typeof fetch;
}

function currentToken(storage: StorageLike): string {
  const token = storage.getItem(API_TOKEN_KEY);
  if (token === null || token === "") {
    throw new CanvasDataError("unauthorized", "登录状态已失效，请重新登录。", 401);
  }
  return token;
}

function currentActorId(storage: StorageLike): string | null {
  const raw = storage.getItem(API_USER_KEY);
  if (raw === null) return null;
  try {
    const user: unknown = JSON.parse(raw);
    if (user !== null && typeof user === "object" && !Array.isArray(user)
      && "id" in user && typeof user.id === "string" && user.id !== "") {
      return user.id;
    }
  } catch {
    // A write must not be claimed by an actor that the current client cannot identify.
  }
  return null;
}

function ensureCurrent(storage: StorageLike, token: string, signal: AbortSignal): void {
  if (signal.aborted || storage.getItem(API_TOKEN_KEY) !== token) {
    throw new CanvasDataError("rejected", "登录或画布状态已变化，请重新打开画布。");
  }
}

function isSameOriginUpload(imageUrl: string, api: URL): URL | null {
  if (CONTROL_OR_SPACE.test(imageUrl) || imageUrl.includes("\\")) return null;
  let parsed: URL;
  try {
    parsed = new URL(imageUrl, api.origin);
  } catch {
    return null;
  }
  if (parsed.origin !== api.origin || parsed.username !== "" || parsed.password !== ""
    || parsed.search !== "" || parsed.hash !== "" || !parsed.pathname.startsWith("/uploads/")) return null;
  const segments = parsed.pathname.slice(1).split("/");
  if (segments.some((part) => part === "" || part === "." || part === ".." || /%2f|%5c|%25/iu.test(part))) return null;
  return parsed;
}

export function createCanvasDataHttpPort(
  apiBaseUrl: string,
  { storage, fetcher }: CanvasDataHttpOptions,
): CanvasDataPort {
  const api = new URL(apiBaseUrl);
  const negotiatedTokens = new Map<string, string>();

  async function request(
    chapterId: string,
    token: string,
    signal: AbortSignal,
    init: RequestInit,
    query = "",
  ): Promise<Response> {
    ensureCurrent(storage, token, signal);
    let response: Response;
    try {
      response = await fetcher(new URL(apiBaseUrl + "/chapters/" + encodeURIComponent(chapterId) + "/canvas" + query, api.origin), {
        ...init,
        headers: new Headers({
          Accept: "application/json",
          Authorization: `Bearer ${token}`,
          ...(init.body === undefined ? {} : { "Content-Type": "application/json" }),
          ...Object.fromEntries(new Headers(init.headers).entries()),
        }),
        credentials: "omit",
        signal,
      });
    } catch (error) {
      ensureCurrent(storage, token, signal);
      if (error instanceof DOMException && error.name === "AbortError") throw error;
      throw new CanvasDataError("unknown", "画布保存结果暂时无法确认，请保留草稿并核对状态。");
    }
    ensureCurrent(storage, token, signal);
    if (response.status === 401 && storage.getItem(API_TOKEN_KEY) === token) {
      clearApiSession(storage);
      throw new CanvasDataError("unauthorized", "登录状态已失效，请重新登录。", 401);
    }
    if (response.status === 409) throw new CanvasDataError("conflict", "画布已被其他成员更新，草稿仍保留。", 409);
    return response;
  }

  async function readJson(response: Response): Promise<unknown> {
    if (!response.ok) {
      if (response.status >= 500) throw new CanvasDataError("unknown", "画布保存结果暂时无法确认，请保留草稿并核对状态。", response.status);
      throw new CanvasDataError("rejected", "画布读取或保存未被服务器接受。", response.status);
    }
    try {
      return await response.json() as unknown;
    } catch {
      throw new CanvasDataError("unknown", "画布服务返回了无法确认的保存结果。", response.status);
    }
  }

  return {
    async get(chapterId, signal) {
      const token = currentToken(storage);
      const response = await request(chapterId, token, signal, { method: "GET" }, "?save_mode=guarded-v1");
      const envelope = parseCanvasEnvelope(await readJson(response), chapterId);
      ensureCurrent(storage, token, signal);
      if (envelope.save_capability === "guarded-v1") negotiatedTokens.set(chapterId, token);
      else negotiatedTokens.delete(chapterId);
      return envelope;
    },
    async save(chapterId, document, guard, signal) {
      const token = currentToken(storage);
      const actorId = currentActorId(storage);
      if (actorId === null) {
        throw new CanvasDataError("rejected", "当前登录身份无法确认，请重新登录后再保存。");
      }
      if (negotiatedTokens.get(chapterId) !== token) {
        throw new CanvasDataError("unsupported", "当前服务未确认受控画布保存，不会发送更改。");
      }
      const response = await request(chapterId, token, signal, {
        method: "PUT",
        body: JSON.stringify({
          document_json: document,
          version: guard.version,
          save_guard: guard,
        }),
      });
      const envelope = parseCanvasEnvelope(await readJson(response), chapterId);
      const receipt = envelope.save_receipt;
      const expectedVersion = guard.id === null ? 1 : guard.version + 1;
      if (receipt === undefined
        || receipt.mode !== "guarded-v1"
        || receipt.id !== envelope.id
        || receipt.version !== envelope.version
        || envelope.id === null
        || (guard.id !== null && envelope.id !== guard.id)
        || envelope.version !== expectedVersion
        || envelope.updated_by !== actorId
        || envelope.updated_at === null
        || !Number.isFinite(Date.parse(envelope.updated_at))
        || JSON.stringify(envelope.document_json) !== JSON.stringify(document)) {
        throw new CanvasDataError("unknown", "画布保存结果暂时无法确认，请保留草稿并核对状态。");
      }
      ensureCurrent(storage, token, signal);
      if (currentActorId(storage) !== actorId) {
        throw new CanvasDataError("unknown", "登录或画布状态已变化，保存结果暂时无法确认。");
      }
      return envelope;
    },
    async readImage(imageUrl, signal) {
      const token = currentToken(storage);
      const upload = isSameOriginUpload(imageUrl, api);
      let target: URL;
      if (upload !== null) {
        target = upload;
      } else {
        if (CONTROL_OR_SPACE.test(imageUrl) || imageUrl.includes("\\") || /^(?:data|blob|javascript):/iu.test(imageUrl)) {
          throw new CanvasDataError("rejected", "这张图片暂时无法安全读取。");
        }
        let remote: URL;
        try {
          remote = new URL(imageUrl, api.origin);
        } catch {
          throw new CanvasDataError("rejected", "这张图片暂时无法安全读取。");
        }
        if ((remote.protocol !== "http:" && remote.protocol !== "https:") || remote.username || remote.password) {
          throw new CanvasDataError("rejected", "这张图片暂时无法安全读取。");
        }
        target = new URL(`${apiBaseUrl}/chat/proxy-image?${new URLSearchParams({ url: remote.href })}`);
      }
      const controller = new AbortController();
      let timedOut = false;
      const abortFromCaller = () => controller.abort(signal.reason);
      if (signal.aborted) abortFromCaller();
      else signal.addEventListener("abort", abortFromCaller, { once: true });
      const timeout = globalThis.setTimeout(() => {
        timedOut = true;
        controller.abort();
      }, IMAGE_READ_TIMEOUT_MS);
      const ensureWithinDeadline = () => {
        if (timedOut) throw new CanvasDataError("rejected", "图片读取超时，请稍后重试。");
      };
      let response: Response | null = null;
      let reader: ReadableStreamDefaultReader<Uint8Array> | null = null;
      let completed = false;
      const cancelBody = async () => {
        try {
          if (reader !== null) await reader.cancel();
          else await response?.body?.cancel();
        } catch {
          // The fetch implementation may already have closed or cancelled this body.
        }
      };
      const cancelOnAbort = () => { void cancelBody(); };
      controller.signal.addEventListener("abort", cancelOnAbort, { once: true });
      try {
        response = await fetcher(target, {
          method: "GET",
          headers: { Accept: "image/*", Authorization: `Bearer ${token}` },
          credentials: "omit",
          redirect: "error",
          signal: controller.signal,
        });
        ensureCurrent(storage, token, signal);
        ensureWithinDeadline();
        if (response.status === 401 && storage.getItem(API_TOKEN_KEY) === token) {
          await cancelBody();
          clearApiSession(storage);
          throw new CanvasDataError("unauthorized", "登录状态已失效，请重新登录。", 401);
        }
        if (!response.ok) {
          await cancelBody();
          throw new CanvasDataError("rejected", "图片读取失败，请重新核对。", response.status);
        }
        const mime = (response.headers.get("content-type") ?? "").split(";", 1)[0]?.trim().toLowerCase() ?? "";
        const lengthText = response.headers.get("content-length");
        const length = lengthText === null ? null : Number(lengthText);
        if (!IMAGE_TYPES.has(mime) || (length !== null
          && (!Number.isSafeInteger(length) || length < 0 || length > MAX_IMAGE_BYTES))) {
          await cancelBody();
          throw new CanvasDataError("rejected", "服务返回的图片格式或大小不受支持。");
        }
        if (response.body === null) {
          throw new CanvasDataError("rejected", "图片内容为空，请重新核对。");
        }
        reader = response.body.getReader();
        const chunks: Uint8Array[] = [];
        let total = 0;
        while (true) {
          const result = await reader.read();
          ensureCurrent(storage, token, signal);
          ensureWithinDeadline();
          if (result.done) break;
          total += result.value.byteLength;
          if (total > MAX_IMAGE_BYTES) {
            await cancelBody();
            throw new CanvasDataError("rejected", "服务返回的图片格式或大小不受支持。");
          }
          chunks.push(result.value);
        }
        if (total === 0 || (length !== null && total !== length)) {
          throw new CanvasDataError("rejected", "服务返回的图片格式或大小不受支持。");
        }
        ensureCurrent(storage, token, signal);
        ensureWithinDeadline();
        const safeParts: BlobPart[] = chunks.map((chunk) => {
          const copy = new Uint8Array(chunk.byteLength);
          copy.set(chunk);
          return copy;
        });
        completed = true;
        return new Blob(safeParts, { type: mime });
      } catch (error) {
        await cancelBody();
        if (timedOut) throw new CanvasDataError("rejected", "图片读取超时，请稍后重试。");
        if (signal.aborted) throw new DOMException("The operation was aborted.", "AbortError");
        if (error instanceof CanvasDataError) throw error;
        ensureCurrent(storage, token, signal);
        throw new CanvasDataError("rejected", "图片读取失败，请重新核对。");
      } finally {
        globalThis.clearTimeout(timeout);
        signal.removeEventListener("abort", abortFromCaller);
        controller.signal.removeEventListener("abort", cancelOnAbort);
        if (!completed) await cancelBody();
      }
    },
  };
}
