// @vitest-environment node
import { describe, expect, it, vi } from "vitest";
import { MEDIA_EXPORT_MAX_BYTES, MEDIA_EXPORT_TIMEOUT_MS } from "./mediaExport";
import {
  classifyMediaExportUrl,
  readAnonymousMediaExport,
  readProtectedMediaExport,
  resolveMediaExportDownloadLinks,
} from "./mediaExportReader";

const apiBaseUrl = "http://127.0.0.1:4175/api";
const uploadsUrl = "http://127.0.0.1:4175/uploads/storyboard/clip.mp4";

function mediaResponse(
  body: BodyInit | null,
  options: {
    contentType?: string;
    contentLength?: string;
    contentEncoding?: string;
    type?: ResponseType;
    url: string;
    status?: number;
  },
): Response {
  const response = new Response(body, {
    status: options.status ?? 200,
    headers: {
      "Content-Type": options.contentType ?? "video/mp4",
      ...(options.contentLength === undefined ? {} : { "Content-Length": options.contentLength }),
      ...(options.contentEncoding === undefined ? {} : { "Content-Encoding": options.contentEncoding }),
    },
  });
  Object.defineProperty(response, "url", { value: options.url });
  if (options.type !== undefined) {
    Object.defineProperty(response, "type", { value: options.type });
  }
  return response;
}

describe("media export source routing", () => {
  it("uses the protected route only for strict same-origin uploads paths", () => {
    expect(classifyMediaExportUrl("/uploads/storyboard/clip.mp4", apiBaseUrl)).toEqual({
      kind: "protected-uploads",
      url: uploadsUrl,
    });
    expect(classifyMediaExportUrl("https://cdn.example.invalid/video.mp4?sig=abc", apiBaseUrl)).toEqual({
      kind: "anonymous-public",
      url: "https://cdn.example.invalid/video.mp4?sig=abc",
    });
    expect(() => classifyMediaExportUrl("/uploads/clip.mp4?sig=abc", apiBaseUrl)).toThrow();
    expect(() => classifyMediaExportUrl("http://user@cdn.example.invalid/clip.mp4", apiBaseUrl)).toThrow();
    expect(() => classifyMediaExportUrl("https://cdn.example.invalid/clip.mp4#fragment", apiBaseUrl)).toThrow();
    expect(() => classifyMediaExportUrl("https://cdn.example.invalid/a\\b.mp4", apiBaseUrl)).toThrow();
  });

  it("sends Bearer only to protected uploads with redirects disabled", async () => {
    const fetcher = vi.fn<typeof fetch>(async () => mediaResponse("clip", {
      url: uploadsUrl,
      contentType: "application/octet-stream",
    }));

    const result = await readProtectedMediaExport({
      mediaUrl: uploadsUrl,
      apiBaseUrl,
      token: "private-token",
      fetcher,
      signal: new AbortController().signal,
    });

    expect(result.blob.size).toBe(4);
    expect(result.extension).toBe("mp4");
    const [, init] = fetcher.mock.calls[0] ?? [];
    expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer private-token");
    expect(init).toMatchObject({
      credentials: "omit",
      cache: "no-store",
      redirect: "error",
      referrerPolicy: "no-referrer",
    });
  });

  it("reads public media anonymously, follows redirects, and validates the final URL", async () => {
    const fetcher = vi.fn<typeof fetch>(async () => mediaResponse("clip", {
      url: "https://cdn2.example.invalid/final.mp4?signature=private",
      contentType: "video/mp4; codecs=avc1",
    }));

    await expect(readAnonymousMediaExport({
      mediaUrl: "https://cdn.example.invalid/start.mp4?signature=private",
      apiBaseUrl,
      fetcher,
      signal: new AbortController().signal,
    })).resolves.toMatchObject({ extension: "mp4", mimeType: "video/mp4" });

    const [, init] = fetcher.mock.calls[0] ?? [];
    expect(new Headers(init?.headers).has("Authorization")).toBe(false);
    expect(init).toMatchObject({
      mode: "cors",
      credentials: "omit",
      cache: "no-store",
      redirect: "follow",
      referrerPolicy: "no-referrer",
    });
  });

  it("enforces the 200 MiB budget on declared and streamed bytes and rejects empty/non-video bodies", async () => {
    const signal = new AbortController().signal;
    const oversized = vi.fn<typeof fetch>(async () => mediaResponse("", {
      url: uploadsUrl,
      contentLength: String(MEDIA_EXPORT_MAX_BYTES + 1),
    }));
    await expect(readProtectedMediaExport({ mediaUrl: uploadsUrl, apiBaseUrl, token: "t", fetcher: oversized, signal }))
      .rejects.toMatchObject({ kind: "invalid-response" });

    const empty = vi.fn<typeof fetch>(async () => mediaResponse(new Uint8Array(), { url: uploadsUrl }));
    await expect(readProtectedMediaExport({ mediaUrl: uploadsUrl, apiBaseUrl, token: "t", fetcher: empty, signal }))
      .rejects.toMatchObject({ kind: "invalid-response" });

    const html = vi.fn<typeof fetch>(async () => mediaResponse("<html>", {
      url: uploadsUrl,
      contentType: "text/html",
    }));
    await expect(readProtectedMediaExport({ mediaUrl: uploadsUrl, apiBaseUrl, token: "t", fetcher: html, signal }))
      .rejects.toMatchObject({ kind: "invalid-response" });

    const streamedOverflow = vi.fn<typeof fetch>(async () => mediaResponse("12345", {
      url: uploadsUrl,
    }));
    await expect(readProtectedMediaExport({
      mediaUrl: uploadsUrl,
      apiBaseUrl,
      token: "t",
      fetcher: streamedOverflow,
      signal,
      maxBytes: 4,
    })).rejects.toMatchObject({ kind: "invalid-response" });

    expect(MEDIA_EXPORT_TIMEOUT_MS).toBe(600_000);
  });

  it("matches video MIME to the saved extension and checks comparable content lengths", async () => {
    const mismatch = vi.fn<typeof fetch>(async () => mediaResponse("clip", {
      url: uploadsUrl,
      contentType: "video/webm",
    }));
    await expect(readProtectedMediaExport({
      mediaUrl: uploadsUrl,
      apiBaseUrl,
      token: "t",
      fetcher: mismatch,
      signal: new AbortController().signal,
    })).resolves.toMatchObject({ mimeType: "video/webm", extension: "webm" });

    const shortBody = vi.fn<typeof fetch>(async () => mediaResponse("clip", {
      url: uploadsUrl,
      contentLength: "6",
    }));
    await expect(readProtectedMediaExport({
      mediaUrl: uploadsUrl,
      apiBaseUrl,
      token: "t",
      fetcher: shortBody,
      signal: new AbortController().signal,
    })).rejects.toMatchObject({ kind: "invalid-response" });

    const longBody = vi.fn<typeof fetch>(async () => mediaResponse("clip", {
      url: uploadsUrl,
      contentLength: "2",
    }));
    await expect(readProtectedMediaExport({
      mediaUrl: uploadsUrl,
      apiBaseUrl,
      token: "t",
      fetcher: longBody,
      signal: new AbortController().signal,
    })).rejects.toMatchObject({ kind: "invalid-response" });

    const encodedCors = vi.fn<typeof fetch>(async () => mediaResponse("clip", {
      url: "https://cdn.example.invalid/clip.mp4",
      contentType: "video/mp4",
      contentLength: "2",
      contentEncoding: "gzip",
      type: "cors",
    }));
    await expect(readAnonymousMediaExport({
      mediaUrl: "https://cdn.example.invalid/clip.mp4",
      apiBaseUrl,
      fetcher: encodedCors,
      signal: new AbortController().signal,
    })).resolves.toMatchObject({ blob: { size: 4 } });

    const unknownOctetStream = vi.fn<typeof fetch>(async () => mediaResponse("clip", {
      url: "https://cdn.example.invalid/clip.bin",
      contentType: "application/octet-stream",
    }));
    await expect(readAnonymousMediaExport({
      mediaUrl: "https://cdn.example.invalid/clip.bin",
      apiBaseUrl,
      fetcher: unknownOctetStream,
      signal: new AbortController().signal,
    })).rejects.toMatchObject({ kind: "invalid-response" });
  });

  it("times out and externally cancels stalled media streams", async () => {
    vi.useFakeTimers();
    try {
      let timeoutCancelled = false;
      const timeoutBody = new ReadableStream<Uint8Array>({
        pull() {},
        cancel() { timeoutCancelled = true; },
      });
      const timeoutFetch = vi.fn<typeof fetch>(async () => mediaResponse(timeoutBody, {
        url: uploadsUrl,
      }));
      const timedRead = readProtectedMediaExport({
        mediaUrl: uploadsUrl,
        apiBaseUrl,
        token: "t",
        fetcher: timeoutFetch,
        signal: new AbortController().signal,
        timeoutMs: 10,
      });
      const timedReadRejected = expect(timedRead).rejects.toMatchObject({ kind: "timeout" });
      await Promise.resolve();
      await Promise.resolve();
      await vi.advanceTimersByTimeAsync(10);
      await timedReadRejected;
      expect(timeoutCancelled).toBe(true);
      const releasedTimeoutReader = timeoutBody.getReader();
      releasedTimeoutReader.releaseLock();
      expect(vi.getTimerCount()).toBe(0);

      let externalCancelled = false;
      const externalBody = new ReadableStream<Uint8Array>({
        pull() {},
        cancel() { externalCancelled = true; },
      });
      const external = new AbortController();
      const externalFetch = vi.fn<typeof fetch>(async () => mediaResponse(externalBody, {
        url: uploadsUrl,
      }));
      const externalRead = readProtectedMediaExport({
        mediaUrl: uploadsUrl,
        apiBaseUrl,
        token: "t",
        fetcher: externalFetch,
        signal: external.signal,
      });
      const externalReadRejected = expect(externalRead).rejects.toMatchObject({ kind: "aborted" });
      await Promise.resolve();
      await Promise.resolve();
      external.abort();
      await externalReadRejected;
      expect(externalCancelled).toBe(true);
      const releasedExternalReader = externalBody.getReader();
      releasedExternalReader.releaseLock();
      expect(vi.getTimerCount()).toBe(0);
    } finally {
      vi.useRealTimers();
    }
  });

  it("signs in exact ordered batches of at most 500 and rejects reordered or malformed receipts", async () => {
    const inputs = Array.from({ length: 501 }, (_, index) => ({
      url: `https://cdn.example.invalid/${index}.mp4`,
      filename: `clip-${index}.mp4`,
    }));
    const fetcher = vi.fn<typeof fetch>(async (_input, init) => {
      const body = JSON.parse(String(init?.body)) as { items: typeof inputs };
      return Response.json({
        items: body.items.map((item) => ({ ...item, signed: false })),
      });
    });

    const results = await resolveMediaExportDownloadLinks({
      items: inputs,
      apiBaseUrl,
      token: "signing-token",
      fetcher,
      signal: new AbortController().signal,
    });

    expect(results).toHaveLength(501);
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(JSON.parse(String(fetcher.mock.calls[0]?.[1]?.body)).items).toHaveLength(500);
    expect(JSON.parse(String(fetcher.mock.calls[1]?.[1]?.body)).items).toHaveLength(1);
    expect(results[500]?.filename).toBe("clip-500.mp4");

    const reordered = vi.fn<typeof fetch>(async () => Response.json({
      items: [
        { ...inputs[1], signed: false },
        { ...inputs[0], signed: false },
      ],
    }));
    await expect(resolveMediaExportDownloadLinks({
      items: inputs.slice(0, 2),
      apiBaseUrl,
      token: "signing-token",
      fetcher: reordered,
      signal: new AbortController().signal,
    })).rejects.toMatchObject({ kind: "invalid-response" });
  });

  it("does not automatically replay a failed signing request", async () => {
    const fetcher = vi.fn<typeof fetch>(async () => new Response("unavailable", { status: 503 }));
    await expect(resolveMediaExportDownloadLinks({
      items: [{ url: uploadsUrl, filename: "clip.mp4" }],
      apiBaseUrl,
      token: "signing-token",
      fetcher,
      signal: new AbortController().signal,
    })).rejects.toMatchObject({ kind: "http" });
    expect(fetcher).toHaveBeenCalledOnce();

    const unauthorized = vi.fn<typeof fetch>(async () => new Response("expired", { status: 401 }));
    await expect(resolveMediaExportDownloadLinks({
      items: [{ url: uploadsUrl, filename: "clip.mp4" }],
      apiBaseUrl,
      token: "signing-token",
      fetcher: unauthorized,
      signal: new AbortController().signal,
    })).rejects.toMatchObject({ kind: "http", status: 401, source: "application" });
    expect(unauthorized).toHaveBeenCalledOnce();
  });

  it("bounds the response body independently of the per-batch deadline", async () => {
    let cancelled = false;
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode("12345"));
      },
      cancel() { cancelled = true; },
    });
    const fetcher = vi.fn<typeof fetch>(async () => new Response(body));
    await expect(resolveMediaExportDownloadLinks({
      items: [{ url: "https://cdn.example.invalid/clip.mp4", filename: "clip.mp4" }],
      apiBaseUrl,
      token: "signing-token",
      fetcher,
      signal: new AbortController().signal,
      maxResponseBytes: 4,
    })).rejects.toMatchObject({ kind: "invalid-response" });
    expect(cancelled).toBe(true);
    const releasedReader = body.getReader();
    releasedReader.releaseLock();
    expect(fetcher).toHaveBeenCalledOnce();
  });

  it("bounds link request/body time, cancels response readers, and stops before the next batch", async () => {
    vi.useFakeTimers();
    try {
      const requestAbort = new AbortController();
      const stalledRequest = vi.fn<typeof fetch>((_input, init) => new Promise<Response>((_resolve, reject) => {
        const requestSignal = init?.signal as AbortSignal;
        requestSignal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), {
          once: true,
        });
      }));
      const requestPending = resolveMediaExportDownloadLinks({
        items: [{ url: "https://cdn.example.invalid/clip.mp4", filename: "clip.mp4" }],
        apiBaseUrl,
        token: "signing-token",
        fetcher: stalledRequest,
        signal: requestAbort.signal,
        timeoutMs: 10,
      });
      const requestRejected = expect(requestPending).rejects.toMatchObject({ kind: "timeout" });
      await vi.advanceTimersByTimeAsync(10);
      await requestRejected;
      expect(stalledRequest).toHaveBeenCalledOnce();
      expect(vi.getTimerCount()).toBe(0);

      let bodyCancelled = false;
      const stalledBody = new ReadableStream<Uint8Array>({
        pull() {},
        cancel() { bodyCancelled = true; },
      });
      const firstBatchOnly = vi.fn<typeof fetch>(async () => new Response(stalledBody, {
        headers: { "Content-Type": "application/json" },
      }));
      const bodyPending = resolveMediaExportDownloadLinks({
        items: Array.from({ length: 501 }, (_, index) => ({
          url: `https://cdn.example.invalid/${index}.mp4`,
          filename: `clip-${index}.mp4`,
        })),
        apiBaseUrl,
        token: "signing-token",
        fetcher: firstBatchOnly,
        signal: new AbortController().signal,
        timeoutMs: 10,
      });
      const bodyRejected = expect(bodyPending).rejects.toMatchObject({ kind: "timeout" });
      await Promise.resolve();
      await Promise.resolve();
      await vi.advanceTimersByTimeAsync(10);
      await bodyRejected;
      expect(bodyCancelled).toBe(true);
      const releasedBodyReader = stalledBody.getReader();
      releasedBodyReader.releaseLock();
      expect(firstBatchOnly).toHaveBeenCalledOnce();
      expect(vi.getTimerCount()).toBe(0);

      let externalBodyCancelled = false;
      const externallyStalledBody = new ReadableStream<Uint8Array>({
        pull() {},
        cancel() { externalBodyCancelled = true; },
      });
      const external = new AbortController();
      const externalFetcher = vi.fn<typeof fetch>(async () => new Response(externallyStalledBody));
      const externalPending = resolveMediaExportDownloadLinks({
        items: [{ url: "https://cdn.example.invalid/clip.mp4", filename: "clip.mp4" }],
        apiBaseUrl,
        token: "signing-token",
        fetcher: externalFetcher,
        signal: external.signal,
        timeoutMs: 10,
      });
      const externalRejected = expect(externalPending).rejects.toMatchObject({ kind: "aborted" });
      await Promise.resolve();
      await Promise.resolve();
      external.abort();
      await externalRejected;
      expect(externalBodyCancelled).toBe(true);
      const releasedExternalBodyReader = externallyStalledBody.getReader();
      releasedExternalBodyReader.releaseLock();
      expect(vi.getTimerCount()).toBe(0);
    } finally {
      vi.useRealTimers();
    }
  });
});
