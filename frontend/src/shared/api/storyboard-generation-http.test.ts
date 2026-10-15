// @vitest-environment node
import { createServer, type Server, type ServerResponse } from "node:http";
import { afterEach, describe, expect, it } from "vitest";
import { createApiServices } from "./services";
import { API_TOKEN_KEY, type StorageLike } from "./storage";

class MemoryStorage implements StorageLike {
  private readonly values = new Map<string, string>();

  getItem(key: string): string | null { return this.values.get(key) ?? null; }
  setItem(key: string, value: string): void { this.values.set(key, value); }
  removeItem(key: string): void { this.values.delete(key); }
}

interface RecordedRequest {
  method: string;
  target: string;
  authorization: string | undefined;
  idempotencyKey: string | undefined;
  body: unknown;
}

interface TestApi {
  baseUrl: string;
  requests: RecordedRequest[];
  finishStream(): void;
  close(): Promise<void>;
}

let activeApi: TestApi | null = null;

async function startTestApi(): Promise<TestApi> {
  const requests: RecordedRequest[] = [];
  let streamResponse: ServerResponse | null = null;
  const server: Server = createServer(async (request, response) => {
    const chunks: Buffer[] = [];
    for await (const chunk of request) chunks.push(Buffer.from(chunk));
    const raw = Buffer.concat(chunks).toString("utf8");
    let body: unknown;
    if (raw !== "") body = JSON.parse(raw) as unknown;
    requests.push({
      method: request.method ?? "",
      target: request.url ?? "",
      authorization: request.headers.authorization,
      idempotencyKey: Array.isArray(request.headers["idempotency-key"])
        ? request.headers["idempotency-key"].join(",")
        : request.headers["idempotency-key"],
      body,
    });
    const json = (value: unknown, status = 200) => {
      response.writeHead(status, { "Content-Type": "application/json" });
      response.end(JSON.stringify(value));
    };
    if (request.url === "/api/models") {
      json([
        {
          id: "model-text", name: "安全模型", category: "text", provider: "test", is_system: true,
          cost_per_call: 6, api_key: "DO_NOT_EXPOSE", base_url: "https://secret.invalid", params: { hidden: true },
        },
        { id: "model-image", name: "图像模型", category: "image", provider: "test", is_system: true, cost_per_call: 3 },
      ]);
      return;
    }
    if (request.url === "/api/prompts") {
      json([
        { id: "scene", name: "场景提示词", category: "scene-segmentation", is_system: true, payload: "PRIVATE_PROMPT" },
        { id: "frame", name: "镜头提示词", category: "storyboard", is_system: true },
      ]);
      return;
    }
    if (request.url === "/api/storyboard/two-pass/quote" && request.method === "POST") {
      json({
        quote_id: "quote-1", operation: "storyboard.two_pass", model_id: "model-text",
        model_name: "安全模型", model_source: "system", unit_price: 6, multiplier: 2,
        quoted_amount: 12, expires_at: "2026-10-15T10:05:00Z",
      });
      return;
    }
    if (request.url === "/api/storyboard/two-pass/stream" && request.method === "POST") {
      if (request.headers["idempotency-key"] === "unauthorized-key") {
        json({ detail: "expired" }, 401);
        return;
      }
      response.writeHead(200, { "Content-Type": "text/event-stream; charset=utf-8" });
      streamResponse = response;
      response.write(`data: ${JSON.stringify({
        type: "receipt", submission_id: "submission-1", operation: "storyboard.two_pass",
        task_id: "task-1", status: "processing", quoted_amount: 12, billing_status: "charged",
      })}\r\n\r\n`);
      if (request.headers["idempotency-key"] !== "uuid-key-2") {
        response.write(`data: ${JSON.stringify({ type: "complete", frames: [{ text: "分镜完成" }] })}\r\n\r\n`);
        response.end();
        streamResponse = null;
      }
      return;
    }
    if (request.url?.startsWith("/api/chat/submissions/storyboard.two_pass?")) {
      json({
        submission_id: "submission-1", operation: "storyboard.two_pass", task_id: "task-1",
        status: "completed", quoted_amount: 12, billing_status: "charged",
      });
      return;
    }
    if (request.url === "/api/chat/tasks?task_id=task-1") {
      json({
        id: "task-1", status: "completed", billing_status: "charged", quoted_amount: 12,
        result: JSON.stringify({ frames: [{ text: "恢复结果" }] }),
      });
      return;
    }
    if (request.url === "/api/series/series-1/export") {
      json({ version: 1, chapters: [{ title: "保留完整frame", frames: [{ text: "x", unknown: true }] }] });
      return;
    }
    if (request.url === "/api/series/import" && request.method === "POST") {
      json({ id: "imported-1", name: "导入剧集", message: "ok" });
      return;
    }
    json({ detail: "not found" }, 404);
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (address === null || typeof address === "string") throw new Error("fixture listener did not bind");
  const api: TestApi = {
    baseUrl: `http://127.0.0.1:${address.port}/api`,
    requests,
    finishStream() {
      if (streamResponse === null) throw new Error("no held stream is active");
      streamResponse.write(`data: ${JSON.stringify({ type: "complete", frames: [{ text: "完成" }] })}\r\n\r\n`);
      streamResponse.end();
      streamResponse = null;
    },
    async close() {
      streamResponse?.end();
      await new Promise<void>((resolve, reject) => {
        server.close((error) => error ? reject(error) : resolve());
      });
    },
  };
  activeApi = api;
  return api;
}

afterEach(async () => {
  const api = activeApi;
  activeApi = null;
  await api?.close();
});

describe("storyboard and exchange API ports", () => {
  it("uses exact safe catalog, quote, receipt stream, recovery and exchange HTTP contracts", async () => {
    const api = await startTestApi();
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "test-bearer");
    const services = createApiServices(api.baseUrl, { storage, fetcher: globalThis.fetch });
    const generation = services.storyboardGeneration;
    expect(generation).toBeDefined();
    const signal = new AbortController().signal;

    const models = await generation!.listModels(signal);
    const prompts = await generation!.listPrompts(signal);
    expect(models).toEqual([{
      id: "model-text", name: "安全模型", category: "text", provider: "test", is_system: true, cost_per_call: 6,
    }]);
    expect(JSON.stringify(models)).not.toContain("DO_NOT_EXPOSE");
    expect(JSON.stringify(prompts)).not.toContain("PRIVATE_PROMPT");
    expect(prompts.map((item) => item.id)).toEqual(["scene", "frame"]);

    const quote = await generation!.quote({
      series_id: "series-1",
      raw_content: "原始剧情",
      model_id: "model-text",
      scene_prompt_id: "scene",
      frame_prompt_id: "frame",
      temperature: 0.7,
      enable_thinking: false,
      skip_frame_generation: false,
    }, signal);
    expect(quote.quote_id).toBe("quote-1");

    const events: string[] = [];
    for await (const event of generation!.stream("quote-1", "uuid-key-1", signal)) events.push(event.type);
    expect(events).toEqual(["receipt", "complete"]);

    const submission = await generation!.getSubmission("uuid-key-1", signal);
    const task = await generation!.getTask(submission.task_id, signal);
    expect(submission.task_id).toBe("task-1");
    expect(task.result).toContain("恢复结果");
    expect(await services.exportSeries!("series-1", signal)).toMatchObject({ version: 1 });
    expect(await services.importSeries!({ name: "导入剧集", data: { version: 1 } }, signal)).toEqual({
      id: "imported-1", name: "导入剧集", message: "ok",
    });

    expect(api.requests.map(({ method, target }) => `${method} ${target}`)).toEqual([
      "GET /api/models",
      "GET /api/prompts",
      "POST /api/storyboard/two-pass/quote",
      "POST /api/storyboard/two-pass/stream",
      "GET /api/chat/submissions/storyboard.two_pass?idempotency_key=uuid-key-1",
      "GET /api/chat/tasks?task_id=task-1",
      "GET /api/series/series-1/export",
      "POST /api/series/import",
    ]);
    for (const request of api.requests) expect(request.authorization).toBe("Bearer test-bearer");
    expect(api.requests[2]?.body).toEqual({
      series_id: "series-1", raw_content: "原始剧情", model_id: "model-text", scene_prompt_id: "scene",
      frame_prompt_id: "frame", temperature: 0.7, enable_thinking: false, skip_frame_generation: false,
    });
    expect(api.requests[3]?.idempotencyKey).toBe("uuid-key-1");
    expect(api.requests[3]?.body).toEqual({ quote_id: "quote-1" });
  });

  it("drops a stream response from an old API session without clearing the new token", async () => {
    const api = await startTestApi();
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "old-token");
    const services = createApiServices(api.baseUrl, { storage, fetcher: globalThis.fetch });
    const generation = services.storyboardGeneration!;
    const controller = new AbortController();
    const iterator = generation.stream("quote-1", "uuid-key-2", controller.signal)[Symbol.asyncIterator]();
    const first = await iterator.next();
    expect(first.value?.type).toBe("receipt");

    storage.setItem(API_TOKEN_KEY, "new-token");
    api.finishStream();
    await expect(iterator.next()).rejects.toMatchObject({ kind: "aborted" });
    expect(storage.getItem(API_TOKEN_KEY)).toBe("new-token");
  });

  it("clears only the current session after a stream 401", async () => {
    const api = await startTestApi();
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "expired-token");
    const services = createApiServices(api.baseUrl, { storage, fetcher: globalThis.fetch });
    const iterator = services.storyboardGeneration!.stream(
      "quote-1",
      "unauthorized-key",
      new AbortController().signal,
    )[Symbol.asyncIterator]();

    await expect(iterator.next()).rejects.toMatchObject({ kind: "http", status: 401 });
    expect(storage.getItem(API_TOKEN_KEY)).toBeNull();
  });

  it("does not clear a replacement token when the old stream returns 401", async () => {
    const api = await startTestApi();
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "old-token");
    const fetcher: typeof fetch = async (input, init) => {
      const response = await globalThis.fetch(input, init);
      if (new URL(String(input)).pathname.endsWith("/storyboard/two-pass/stream")) {
        storage.setItem(API_TOKEN_KEY, "new-token");
      }
      return response;
    };
    const services = createApiServices(api.baseUrl, { storage, fetcher });
    const iterator = services.storyboardGeneration!.stream(
      "quote-1",
      "unauthorized-key",
      new AbortController().signal,
    )[Symbol.asyncIterator]();

    await expect(iterator.next()).rejects.toMatchObject({ kind: "aborted" });
    expect(storage.getItem(API_TOKEN_KEY)).toBe("new-token");
  });
});
