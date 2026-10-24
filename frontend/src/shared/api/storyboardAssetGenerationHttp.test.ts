import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import type { AddressInfo } from "node:net";
import { describe, expect, it, vi } from "vitest";
import storyboardAssetWire from "../../testing/fixtures/storyboardAssetWire.json";
import { ApiError } from "./errors";
import { createStoryboardAssetGenerationHttpPort } from "./storyboardAssetGenerationHttp";
import { API_TOKEN_KEY, API_USER_KEY, type StorageLike } from "./storage";
import type { StoryboardAssetKind, StoryboardAssetStreamEvent } from "./storyboardAssetGeneration";

class TestStorage implements StorageLike {
  private readonly values = new Map<string, string>();

  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }

  removeItem(key: string): void {
    this.values.delete(key);
  }
}

async function requestBody(request: IncomingMessage): Promise<Record<string, unknown>> {
  const chunks: Buffer[] = [];
  for await (const chunk of request) chunks.push(Buffer.from(chunk));
  const value: unknown = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new Error("request body must be an object");
  return value as Record<string, unknown>;
}

async function withLoopbackServer<T>(
  handler: (request: IncomingMessage, response: ServerResponse) => Promise<void>,
  run: (baseUrl: string) => Promise<T>,
): Promise<T> {
  const server = createServer((request, response) => { void handler(request, response); });
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => resolve());
  });
  const address = server.address();
  if (address === null || typeof address === "string") throw new Error("loopback server did not bind a TCP port");
  const { port } = address as AddressInfo;
  try {
    return await run(`http://127.0.0.1:${port}/api`);
  } finally {
    await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
  }
}

function eventStream(events: Array<Record<string, unknown>>): Buffer {
  return Buffer.from(events.map((event) => `data: ${JSON.stringify(event)}\r\n\r\n`).join(""), "utf8");
}

async function writeEvents(response: ServerResponse, events: Array<Record<string, unknown>>): Promise<void> {
  response.writeHead(200, { "content-type": "text/event-stream; charset=utf-8", "cache-control": "no-cache" });
  const bytes = eventStream(events);
  for (let offset = 0; offset < bytes.length; offset += 7) {
    response.write(bytes.subarray(offset, offset + 7));
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
  response.end();
}

async function collect(stream: AsyncIterable<StoryboardAssetStreamEvent>): Promise<StoryboardAssetStreamEvent[]> {
  const events: StoryboardAssetStreamEvent[] = [];
  for await (const event of stream) events.push(event);
  return events;
}

function judgeCompleteFor(kind: StoryboardAssetKind): Record<string, unknown> {
  if (kind === "character") return storyboardAssetWire.producer.judge_complete.character;
  if (kind === "scene") return storyboardAssetWire.producer.judge_complete.scene;
  return storyboardAssetWire.producer.judge_complete.prop;
}

describe("storyboard asset generation HTTP", () => {
  it("keeps the default request budget beyond the backend ten-minute runtime", async () => {
    const storage = new TestStorage();
    storage.setItem(API_TOKEN_KEY, "local-test-token");
    const fetcher = vi.fn(async () => ({
      status: 200,
      body: new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(eventStream([
            { type: "start", message: "开始提取角色..." },
            storyboardAssetWire.producer.extract_complete.character,
            { type: "done" },
          ]));
          controller.close();
        },
      }),
    } as Response));
    const timerSpy = vi.spyOn(globalThis, "setTimeout");

    try {
      const port = createStoryboardAssetGenerationHttpPort("http://127.0.0.1:4175/api", { storage, fetcher });
      const events = await collect(port.streamExtract("character", {
        chapter_id: "chapter-1", model_id: "model-1", prompt_id: null, enable_thinking: false, frame_indices: null,
      }, new AbortController().signal));

      expect(events.at(-1)?.type).toBe("done");
      expect(timerSpy.mock.calls.some(([, delay]) => Number(delay) === 650_000)).toBe(true);
      expect(fetcher).toHaveBeenCalledTimes(1);
    } finally {
      timerSpy.mockRestore();
    }
  });

  it("honors short injected timeouts and never retries an aborted request", async () => {
    const storage = new TestStorage();
    storage.setItem(API_TOKEN_KEY, "local-test-token");
    const fetcher = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
      const signal = init?.signal;
      if (signal === undefined || signal === null) {
        reject(new Error("expected a request abort signal"));
        return;
      }
      if (signal.aborted) {
        reject(new DOMException("Aborted", "AbortError"));
        return;
      }
      signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
    }));
    vi.useFakeTimers();
    try {
      const port = createStoryboardAssetGenerationHttpPort("http://127.0.0.1:4175/api", {
        storage,
        fetcher,
        timeoutMs: 5,
      });
      const pending = collect(port.streamExtract("character", {
        chapter_id: "chapter-1", model_id: "model-1", prompt_id: null, enable_thinking: false, frame_indices: null,
      }, new AbortController().signal));
      const timedOut = expect(pending).rejects.toMatchObject({ kind: "timeout" });
      await vi.advanceTimersByTimeAsync(5);

      await timedOut;
      expect(fetcher).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("posts actual three-kind endpoint contracts to an owned loopback server", async () => {
    const storage = new TestStorage();
    storage.setItem(API_TOKEN_KEY, "local-test-token");
    const requests: Array<{ url: string; authorization: string | undefined; body: Record<string, unknown> }> = [];

    await withLoopbackServer(async (request, response) => {
      const body = await requestBody(request);
      requests.push({ url: request.url ?? "", authorization: request.headers.authorization, body });
      if (request.url === "/api/storyboard/extract-characters-stream") {
        await writeEvents(response, [
          { type: "start", message: "开始提取角色..." },
          { type: "status", message: "正在解析角色数据..." },
          storyboardAssetWire.producer.extract_complete.character,
          { type: "done" },
        ]);
      } else if (request.url === "/api/storyboard/judge-scene-appearance-stream") {
        await writeEvents(response, [
          { type: "start", message: "开始判断场景出场..." },
          storyboardAssetWire.producer.judge_complete.scene,
          { type: "done" },
        ]);
      } else if (request.url === "/api/storyboard/filter-and-save-scenes-stream") {
        const plan = storyboardAssetWire.save_plan;
        await writeEvents(response, [
          { type: "start", message: "开始保存场景..." },
          { type: "plan", ...plan },
          { type: "complete", success: true, dry_run: true, plan, message: "已生成待确认清单" },
          { type: "done" },
        ]);
      } else {
        response.writeHead(404, { "content-type": "application/json" }).end(JSON.stringify({ detail: "unexpected route" }));
      }
    }, async (baseUrl) => {
      const port = createStoryboardAssetGenerationHttpPort(baseUrl, { storage, timeoutMs: 5_000 });
      const signal = new AbortController().signal;
      const extracted = await collect(port.streamExtract("character", {
        chapter_id: "chapter-1", model_id: "model-1", prompt_id: null, enable_thinking: true, frame_indices: [0, 2],
      }, signal));
      const judged = await collect(port.streamJudge("scene", {
        chapter_id: "chapter-1", model_id: "model-1", prompt_id: "", enable_thinking: false, skip_merge: true,
        candidates: [{ title: "夜色书房" }], storyboard_texts: ["她推门走进书房"], frame_numbers: [2],
      }, signal));
      const saved = await collect(port.streamSave("scene", {
        chapter_id: "chapter-1", target_frames: [1], appearances: ["夜色书房"], dry_run: true,
        candidates: [{ title: "夜色书房", is_existing: false, is_backfill: false }],
      }, signal));

      expect(extracted.at(-1)?.type).toBe("done");
      expect(judged.at(-1)?.type).toBe("done");
      expect(saved.at(-1)?.type).toBe("done");
      expect(requests.map((request) => request.url)).toEqual([
        "/api/storyboard/extract-characters-stream",
        "/api/storyboard/judge-scene-appearance-stream",
        "/api/storyboard/filter-and-save-scenes-stream",
      ]);
      expect(requests.every((request) => request.authorization === "Bearer local-test-token")).toBe(true);
      expect(requests[0]?.body).toEqual({ chapter_id: "chapter-1", model_id: "model-1", prompt_id: null, enable_thinking: true, frame_indices: [0, 2] });
      expect(requests[1]?.body).toEqual({
        chapter_id: "chapter-1", model_id: "model-1", prompt_id: "", enable_thinking: false, skip_merge: true,
        scenes: [{ title: "夜色书房" }], storyboard_texts: ["她推门走进书房"], frame_numbers: [2],
      });
      expect(requests[2]?.body).toEqual({
        chapter_id: "chapter-1", target_frames: [1], appearances: ["夜色书房"], dry_run: true,
        scenes: [{ title: "夜色书房", is_existing: false, is_backfill: false }],
      });
    });
  });

  it("sends kind-specific judge request bodies matching the shared backend parser fixture", async () => {
    const storage = new TestStorage();
    storage.setItem(API_TOKEN_KEY, "local-test-token");
    const requests: Array<{ url: string; body: Record<string, unknown> }> = [];

    await withLoopbackServer(async (request, response) => {
      const body = await requestBody(request);
      requests.push({ url: request.url ?? "", body });
      const contract = storyboardAssetWire.judge_requests.find((entry) => (
        request.url?.endsWith(`judge-${entry.kind === "character" ? "character" : entry.kind}-appearance-stream`)
      ));
      if (contract === undefined) {
        response.writeHead(404, { "content-type": "application/json" }).end(JSON.stringify({ detail: "unexpected route" }));
        return;
      }
      const kind = contract.kind;
      if (kind !== "character" && kind !== "scene" && kind !== "prop") {
        response.writeHead(400).end();
        return;
      }
      await writeEvents(response, [
        { type: "start", message: "开始判断素材出场..." },
        judgeCompleteFor(kind),
        { type: "done" },
      ]);
    }, async (baseUrl) => {
      const port = createStoryboardAssetGenerationHttpPort(baseUrl, { storage, timeoutMs: 5_000 });
      for (const contract of storyboardAssetWire.judge_requests) {
        const kind = contract.kind;
        if (kind !== "character" && kind !== "scene" && kind !== "prop") throw new Error("invalid fixture kind");
        await collect(port.streamJudge(kind, contract.input, new AbortController().signal));
      }

      expect(requests.map(({ body }) => body)).toEqual(
        storyboardAssetWire.judge_requests.map(({ wire }) => wire),
      );
    });
  });

  it("does not clear a newer login when an old session receives a late 401 and never retries", async () => {
    const storage = new TestStorage();
    storage.setItem(API_TOKEN_KEY, "old-token");
    storage.setItem(API_USER_KEY, "old-user");
    let requestCount = 0;
    let finishResponse: (() => void) | null = null;
    let markRequestSeen: (() => void) | null = null;
    const requestSeen = new Promise<void>((resolve) => { markRequestSeen = resolve; });

    await withLoopbackServer(async (_request, response) => {
      requestCount += 1;
      markRequestSeen?.();
      await new Promise<void>((resolve) => { finishResponse = resolve; });
      response.writeHead(401, { "content-type": "application/json" }).end(JSON.stringify({ detail: "expired" }));
    }, async (baseUrl) => {
      const port = createStoryboardAssetGenerationHttpPort(baseUrl, { storage, timeoutMs: 5_000 });
      const pending = collect(port.streamExtract("prop", {
        chapter_id: "chapter-1", model_id: "model-1", prompt_id: null, enable_thinking: false, frame_indices: null,
      }, new AbortController().signal));
      await requestSeen;
      storage.setItem(API_TOKEN_KEY, "new-token");
      storage.setItem(API_USER_KEY, "new-user");
      finishResponse?.();
      await expect(pending).rejects.toMatchObject({ kind: "aborted" });
      expect(storage.getItem(API_TOKEN_KEY)).toBe("new-token");
      expect(storage.getItem(API_USER_KEY)).toBe("new-user");
      expect(requestCount).toBe(1);
    });
  });

  it("clears only a current session on HTTP 401", async () => {
    const storage = new TestStorage();
    storage.setItem(API_TOKEN_KEY, "current-token");
    storage.setItem(API_USER_KEY, "current-user");
    await withLoopbackServer(async (_request, response) => {
      response.writeHead(401, { "content-type": "application/json" }).end(JSON.stringify({ detail: "expired" }));
    }, async (baseUrl) => {
      const port = createStoryboardAssetGenerationHttpPort(baseUrl, { storage, timeoutMs: 5_000 });
      const pending = collect(port.streamExtract("prop", {
        chapter_id: "chapter-1", model_id: "model-1", prompt_id: null, enable_thinking: false, frame_indices: null,
      }, new AbortController().signal));
      await expect(pending).rejects.toMatchObject({ status: 401 });
      expect(storage.getItem(API_TOKEN_KEY)).toBeNull();
      expect(storage.getItem(API_USER_KEY)).toBeNull();
    });
  });

  it("aborts a live response stream instead of claiming the request can be undone", async () => {
    const storage = new TestStorage();
    storage.setItem(API_TOKEN_KEY, "token");
    let responseClosed: (() => void) | null = null;
    const closed = new Promise<void>((resolve) => { responseClosed = resolve; });
    await withLoopbackServer(async (_request, response) => {
      response.on("close", () => responseClosed?.());
      response.writeHead(200, { "content-type": "text/event-stream" });
      response.write("data: {\"type\":\"start\",\"message\":\"开始\"}\n\n");
    }, async (baseUrl) => {
      const port = createStoryboardAssetGenerationHttpPort(baseUrl, { storage, timeoutMs: 5_000 });
      const controller = new AbortController();
      const iterator = port.streamExtract("prop", {
        chapter_id: "chapter-1", model_id: "model-1", prompt_id: null, enable_thinking: false, frame_indices: null,
      }, controller.signal)[Symbol.asyncIterator]();
      const first = await iterator.next();
      expect(first.value?.type).toBe("start");
      controller.abort();
      await expect(iterator.next()).rejects.toBeInstanceOf(ApiError);
      await closed;
    });
  });
});
