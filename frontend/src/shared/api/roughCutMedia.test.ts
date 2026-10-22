// @vitest-environment node
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "./errors";
import {
  MAX_ROUGH_CUT_MEDIA_BYTES,
  resolveRoughCutMediaUrl,
  readRoughCutMedia,
} from "./roughCutMedia";

const apiBaseUrl = "http://127.0.0.1:4175/api";

describe("rough-cut media URL policy", () => {
  it.each([
    ["/uploads/storyboard/clip.mp4", "http://127.0.0.1:4175/uploads/storyboard/clip.mp4"],
    ["uploads/storyboard/clip.mp4", "http://127.0.0.1:4175/uploads/storyboard/clip.mp4"],
    ["http://127.0.0.1:4175/uploads/storyboard/clip.mp4", "http://127.0.0.1:4175/uploads/storyboard/clip.mp4"],
  ])("accepts only the configured uploads origin: %s", (reference, expected) => {
    expect(resolveRoughCutMediaUrl(reference, apiBaseUrl).href).toBe(expected);
  });

  it.each([
    "https://media.example.invalid/uploads/clip.mp4",
    "http://localhost:4175/uploads/clip.mp4",
    "http://127.0.0.1:4176/uploads/clip.mp4",
    "http://user@127.0.0.1:4175/uploads/clip.mp4",
    "/uploads/clip.mp4?token=secret",
    "/uploads/clip.mp4#fragment",
    "/uploads/../private/clip.mp4",
    "/uploads/%2e%2e/private/clip.mp4",
    "/uploads/folder%2fclip.mp4",
    "/uploads/folder%5cclip.mp4",
    "/uploads/folder\\clip.mp4",
    "file:///uploads/clip.mp4",
    "data:video/mp4;base64,AAAA",
    "blob:http://127.0.0.1:4175/foreign-object-url",
  ])("rejects unsafe media reference %s", (reference) => {
    expect(() => resolveRoughCutMediaUrl(reference, apiBaseUrl)).toThrow(ApiError);
  });
});

describe("readRoughCutMedia", () => {
  it("sends the bearer only to the same-origin uploads route with strict fetch options", async () => {
    const fetcher = vi.fn<typeof fetch>(async () => new Response("clip-bytes", {
      headers: { "Content-Type": "video/mp4; codecs=avc1" },
    }));

    await expect(readRoughCutMedia({
      previewUrl: "/uploads/storyboard/clip.mp4",
      apiBaseUrl,
      token: "private-session-token",
      fetcher,
      signal: new AbortController().signal,
    })).resolves.toBeInstanceOf(Blob);

    const [requestUrl, init] = fetcher.mock.calls[0] ?? [];
    expect(requestUrl).toBeInstanceOf(URL);
    expect((requestUrl as URL).href).toBe("http://127.0.0.1:4175/uploads/storyboard/clip.mp4");
    expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer private-session-token");
    expect(init).toMatchObject({
      method: "GET",
      credentials: "omit",
      cache: "no-store",
      redirect: "error",
      referrerPolicy: "no-referrer",
    });
    expect(init?.signal).toBeInstanceOf(AbortSignal);
  });

  it.each([
    ["application/octet-stream", 200],
    ["text/html", 200],
    ["video/mp4", 401],
  ])("rejects an invalid media response (%s, %i)", async (contentType, status) => {
    const fetcher = vi.fn<typeof fetch>(async () => new Response("not playable", {
      status,
      headers: { "Content-Type": contentType },
    }));

    await expect(readRoughCutMedia({
      previewUrl: "/uploads/storyboard/clip.mp4",
      apiBaseUrl,
      token: "token",
      fetcher,
      signal: new AbortController().signal,
    })).rejects.toBeInstanceOf(ApiError);
  });

  it("rejects an oversized declared body before reading it", async () => {
    const cancel = vi.fn();
    const body = new ReadableStream<Uint8Array>({
      cancel,
    });
    const fetcher = vi.fn<typeof fetch>(async () => new Response(body, {
      headers: {
        "Content-Type": "video/mp4",
        "Content-Length": String(MAX_ROUGH_CUT_MEDIA_BYTES + 1),
      },
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

  it("enforces the byte limit while consuming the stream", async () => {
    const cancel = vi.fn();
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new Uint8Array(MAX_ROUGH_CUT_MEDIA_BYTES + 1));
      },
      cancel,
    });
    const fetcher = vi.fn<typeof fetch>(async () => new Response(body, {
      headers: { "Content-Type": "video/mp4" },
    }));

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

  it("stops a media request when its bounded timeout expires", async () => {
    const fetcher = vi.fn<typeof fetch>((_input, init) => new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => {
        reject(new DOMException("Request aborted", "AbortError"));
      }, { once: true });
    }));

    await expect(readRoughCutMedia({
      previewUrl: "/uploads/storyboard/slow.mp4",
      apiBaseUrl,
      token: "token",
      fetcher,
      signal: new AbortController().signal,
      timeoutMs: 5,
    })).rejects.toMatchObject({ kind: "timeout" });
    expect(fetcher).toHaveBeenCalledOnce();
  });
});
