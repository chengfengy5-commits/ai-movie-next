// @vitest-environment node
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "./errors";
import {
  isSafeRoughCutMediaReference,
  MAX_ROUGH_CUT_MEDIA_BYTES,
  readRoughCutMedia,
  resolveRoughCutMediaUrl,
} from "./roughCutMedia";

const apiBaseUrl = "http://127.0.0.1:4175/api";
const uploadsUrl = "http://127.0.0.1:4175/uploads/storyboard/clip.mp4";

function responseWithUrl(
  body: BodyInit | null,
  url: string,
  options: {
    status?: number;
    headers?: HeadersInit;
  } = {},
): Response {
  const response = new Response(body, {
    status: options.status ?? 200,
    headers: {
      "Content-Type": "video/mp4",
      ...options.headers,
    },
  });
  Object.defineProperty(response, "url", { value: url });
  return response;
}

function signedLinkResponse(sourceUrl: string, resolvedUrl: string): Response {
  return Response.json({
    items: [{
      url: resolvedUrl,
      filename: "rough-cut-preview.mp4",
      signed: resolvedUrl !== sourceUrl,
    }],
  });
}

describe("rough-cut media reference policy", () => {
  it("keeps canonical uploads protected and allows canonical remote URLs with signed queries", () => {
    expect(resolveRoughCutMediaUrl("/uploads/storyboard/clip.mp4", apiBaseUrl).href).toBe(uploadsUrl);
    expect(isSafeRoughCutMediaReference("https://media.example.invalid/clip.mp4?sig=private", apiBaseUrl))
      .toBe(true);
    expect(() => resolveRoughCutMediaUrl("https://media.example.invalid/clip.mp4?sig=private", apiBaseUrl))
      .toThrow(ApiError);
  });

  it.each([
    "/uploads/clip.mp4?sig=private",
    "/uploads/clip.mp4#fragment",
    "http://127.0.0.1:4175/uploads/clip.mp4?sig=private",
    "http://127.0.0.1:4175/uploads/../private/clip.mp4",
    "http://127.0.0.1:4175/uploads/%2e%2e/private/clip.mp4",
    "http://127.0.0.1:4175/uploads/folder%2fclip.mp4",
    "http://127.0.0.1:4175/uploads/folder%5cclip.mp4",
    "https://media.example.invalid/clip.mp4#fragment",
    "https://user@media.example.invalid/clip.mp4",
    "https://media.example.invalid/a\\b.mp4",
    "file:///uploads/clip.mp4",
    "data:video/mp4;base64,AAAA",
    "blob:https://media.example.invalid/foreign-object-url",
  ])("rejects unsafe or ambiguous media references: %s", async (reference) => {
    expect(isSafeRoughCutMediaReference(reference, apiBaseUrl)).toBe(false);
    await expect(readRoughCutMedia({
      previewUrl: reference,
      apiBaseUrl,
      token: "token",
      fetcher: vi.fn<typeof fetch>(),
      signal: new AbortController().signal,
    })).rejects.toBeInstanceOf(ApiError);
  });
});

describe("readRoughCutMedia", () => {
  it("sends the bearer only to the same-origin protected uploads route", async () => {
    const fetcher = vi.fn<typeof fetch>(async () => responseWithUrl("clip-bytes", uploadsUrl));

    await expect(readRoughCutMedia({
      previewUrl: "/uploads/storyboard/clip.mp4",
      apiBaseUrl,
      token: "private-session-token",
      fetcher,
      signal: new AbortController().signal,
    })).resolves.toMatchObject({ size: 10, type: "video/mp4" });

    const [requestUrl, init] = fetcher.mock.calls[0] ?? [];
    expect(String(requestUrl)).toBe(uploadsUrl);
    expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer private-session-token");
    expect(init).toMatchObject({
      method: "GET",
      credentials: "omit",
      cache: "no-store",
      redirect: "error",
      referrerPolicy: "no-referrer",
    });
  });

  it("resolves a signed remote URL, then reads its Blob without the application bearer", async () => {
    const sourceUrl = "https://media.example.invalid/clip.mp4?sig=source";
    const signedUrl = "https://media.example.invalid/clip.mp4?sig=signed";
    const fetcher = vi.fn<typeof fetch>(async (input) => {
      const url = new URL(String(input));
      if (url.pathname === "/api/sign-download-urls") {
        return signedLinkResponse(sourceUrl, signedUrl);
      }
      return responseWithUrl("remote-bytes", signedUrl);
    });

    const blob = await readRoughCutMedia({
      previewUrl: sourceUrl,
      apiBaseUrl,
      token: "private-session-token",
      fetcher,
      signal: new AbortController().signal,
    });

    expect(blob).toMatchObject({ size: 12, type: "video/mp4" });
    expect(fetcher).toHaveBeenCalledTimes(2);
    const [signerInput, signerInit] = fetcher.mock.calls[0] ?? [];
    expect(String(signerInput)).toBe("http://127.0.0.1:4175/api/sign-download-urls");
    expect(new Headers(signerInit?.headers).get("Authorization")).toBe("Bearer private-session-token");
    expect(JSON.parse(String(signerInit?.body))).toEqual({
      items: [{ url: sourceUrl, filename: "rough-cut-preview.mp4" }],
    });
    expect(signerInit).toMatchObject({ credentials: "omit", redirect: "error", referrerPolicy: "no-referrer" });

    const [mediaInput, mediaInit] = fetcher.mock.calls[1] ?? [];
    expect(String(mediaInput)).toBe(signedUrl);
    expect(new Headers(mediaInit?.headers).has("Authorization")).toBe(false);
    expect(mediaInit).toMatchObject({
      method: "GET",
      mode: "cors",
      credentials: "omit",
      cache: "no-store",
      redirect: "follow",
      referrerPolicy: "no-referrer",
    });
  });

  it("supports the existing identity signer for an already public URL", async () => {
    const sourceUrl = "https://media.example.invalid/clip.mp4?sig=already-signed";
    const fetcher = vi.fn<typeof fetch>(async (input) => {
      const url = new URL(String(input));
      if (url.pathname === "/api/sign-download-urls") {
        return signedLinkResponse(sourceUrl, sourceUrl);
      }
      return responseWithUrl("remote-bytes", sourceUrl);
    });

    await expect(readRoughCutMedia({
      previewUrl: sourceUrl,
      apiBaseUrl,
      token: "token",
      fetcher,
      signal: new AbortController().signal,
    })).resolves.toBeInstanceOf(Blob);
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(String(fetcher.mock.calls[1]?.[0])).toBe(sourceUrl);
  });

  it("maps a remote 401 to a media error without marking the application session unauthorized", async () => {
    const sourceUrl = "https://media.example.invalid/clip.mp4";
    const fetcher = vi.fn<typeof fetch>(async (input) => {
      const url = new URL(String(input));
      if (url.pathname === "/api/sign-download-urls") {
        return signedLinkResponse(sourceUrl, sourceUrl);
      }
      return responseWithUrl("expired", sourceUrl, { status: 401 });
    });

    await expect(readRoughCutMedia({
      previewUrl: sourceUrl,
      apiBaseUrl,
      token: "still-valid-app-session",
      fetcher,
      signal: new AbortController().signal,
    })).rejects.toMatchObject({
      kind: "http",
      status: null,
      message: "远程视频读取失败，请重试或跳过此镜。",
    });
  });

  it.each(["signer", "protected media"] as const)("preserves an API 401 from the %s path", async (source) => {
    const remoteUrl = "https://media.example.invalid/clip.mp4";
    const fetcher = vi.fn<typeof fetch>(async (input) => {
      const url = new URL(String(input));
      if (source === "signer" && url.pathname === "/api/sign-download-urls") {
        return Response.json({ detail: "expired" }, { status: 401 });
      }
      if (source === "protected media") {
        return responseWithUrl("expired", uploadsUrl, { status: 401 });
      }
      return signedLinkResponse(remoteUrl, remoteUrl);
    });

    await expect(readRoughCutMedia({
      previewUrl: source === "signer" ? remoteUrl : "/uploads/storyboard/clip.mp4",
      apiBaseUrl,
      token: "expired-app-session",
      fetcher,
      signal: new AbortController().signal,
    })).rejects.toMatchObject({ kind: "http", status: 401 });
  });

  it("maps failed remote CORS reads to a preview-specific network error", async () => {
    const sourceUrl = "https://media.example.invalid/clip.mp4";
    const fetcher = vi.fn<typeof fetch>(async (input) => {
      const url = new URL(String(input));
      if (url.pathname === "/api/sign-download-urls") {
        return signedLinkResponse(sourceUrl, sourceUrl);
      }
      throw new TypeError("CORS blocked");
    });

    await expect(readRoughCutMedia({
      previewUrl: sourceUrl,
      apiBaseUrl,
      token: "token",
      fetcher,
      signal: new AbortController().signal,
    })).rejects.toMatchObject({
      kind: "network",
      message: "无法连接或读取此镜头的视频，请重试或跳过此镜。",
    });
  });

  it("stops after signing when the captured application session becomes stale", async () => {
    const sourceUrl = "https://media.example.invalid/clip.mp4";
    let resolveSigner!: (response: Response) => void;
    let current = true;
    const fetcher = vi.fn<typeof fetch>(async (input) => {
      const url = new URL(String(input));
      if (url.pathname === "/api/sign-download-urls") {
        return new Promise<Response>((resolve) => { resolveSigner = resolve; });
      }
      return responseWithUrl("remote-bytes", sourceUrl);
    });

    const pending = readRoughCutMedia({
      previewUrl: sourceUrl,
      apiBaseUrl,
      token: "session-before-logout",
      fetcher,
      signal: new AbortController().signal,
      isCurrent: () => current,
    });
    await vi.waitFor(() => expect(resolveSigner).toBeTypeOf("function"));
    current = false;
    resolveSigner(signedLinkResponse(sourceUrl, sourceUrl));

    await expect(pending).rejects.toMatchObject({ kind: "aborted" });
    expect(fetcher).toHaveBeenCalledOnce();
  });

  it("enforces one timeout across signing and the remote stream", async () => {
    vi.useFakeTimers();
    const sourceUrl = "https://media.example.invalid/clip.mp4";
    const mediaSignal = { current: null as AbortSignal | null };
    const fetcher = vi.fn<typeof fetch>((input, init) => {
      const url = new URL(String(input));
      if (url.pathname === "/api/sign-download-urls") {
        return new Promise<Response>((resolve) => {
          setTimeout(() => resolve(signedLinkResponse(sourceUrl, sourceUrl)), 6);
        });
      }
      mediaSignal.current = init?.signal ?? null;
      return new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => {
          reject(new DOMException("Request aborted", "AbortError"));
        }, { once: true });
      });
    });

    try {
      const pending = readRoughCutMedia({
        previewUrl: sourceUrl,
        apiBaseUrl,
        token: "token",
        fetcher,
        signal: new AbortController().signal,
        timeoutMs: 20,
      });
      const pendingRejected = expect(pending).rejects.toMatchObject({
        kind: "timeout",
        message: "粗剪视频读取超时，请重试或跳过此镜。",
      });
      await vi.advanceTimersByTimeAsync(6);
      await vi.waitFor(() => expect(mediaSignal.current).toBeInstanceOf(AbortSignal));
      await vi.advanceTimersByTimeAsync(14);

      await pendingRejected;
      expect(mediaSignal.current?.aborted).toBe(true);
      expect(fetcher).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("rejects a declared body larger than 64 MiB before consuming it", async () => {
    const cancel = vi.fn();
    const body = new ReadableStream<Uint8Array>({ cancel });
    const fetcher = vi.fn<typeof fetch>(async () => responseWithUrl(body, uploadsUrl, {
      headers: { "Content-Length": String(MAX_ROUGH_CUT_MEDIA_BYTES + 1) },
    }));

    await expect(readRoughCutMedia({
      previewUrl: "/uploads/storyboard/large.mp4",
      apiBaseUrl,
      token: "token",
      fetcher,
      signal: new AbortController().signal,
    })).rejects.toMatchObject({ kind: "invalid-response" });
    expect(cancel).toHaveBeenCalledOnce();
  });

  it("enforces the 64 MiB limit while consuming the stream", async () => {
    const cancel = vi.fn();
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new Uint8Array(MAX_ROUGH_CUT_MEDIA_BYTES + 1));
      },
      cancel,
    });
    const fetcher = vi.fn<typeof fetch>(async () => responseWithUrl(body, uploadsUrl));

    await expect(readRoughCutMedia({
      previewUrl: "/uploads/storyboard/streamed-large.mp4",
      apiBaseUrl,
      token: "token",
      fetcher,
      signal: new AbortController().signal,
    })).rejects.toMatchObject({ kind: "invalid-response" });
    expect(cancel).toHaveBeenCalledOnce();
  });

  it("does not issue a request after cancellation", async () => {
    const controller = new AbortController();
    controller.abort();
    const fetcher = vi.fn<typeof fetch>();

    await expect(readRoughCutMedia({
      previewUrl: "/uploads/storyboard/clip.mp4",
      apiBaseUrl,
      token: "token",
      fetcher,
      signal: controller.signal,
    })).rejects.toMatchObject({ kind: "aborted" });
    expect(fetcher).not.toHaveBeenCalled();
  });
});
