// @vitest-environment node
import { describe, expect, it } from "vitest";
import { demoSeries } from "../../features/series/demoSeries";
import { InvalidResponseError, type Character, type Prop, type Scene, type StoryboardFrame } from "./contracts";
import { createApiServices, createDemoServices } from "./services";
import { API_TOKEN_KEY, type StorageLike } from "./storage";

class MemoryStorage implements StorageLike {
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

interface CapturedRequest {
  method: string;
  pathname: string;
  body: unknown;
  authorization: string | null;
}

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function characterResponse(overrides: Partial<Character> = {}): Character {
  return {
    id: "character-1",
    series_id: "series-1",
    name: "沈照",
    gender: "女",
    age: "28",
    role: "主角",
    appearance: "短发",
    description: "旧城记者",
    image_url: "https://media.example.invalid/cover.png",
    audio_url: "https://media.example.invalid/voice.mp3",
    voice_ref: "voice-ref-1",
    aliases: ["阿照"],
    canonical_key: "server-key",
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-02T00:00:00Z",
    ...overrides,
  };
}

function sceneResponse(overrides: Partial<Scene> = {}): Scene {
  return {
    id: "scene-1",
    series_id: "series-1",
    title: "旧站台",
    description: "夜雨中的站台",
    image_url: "https://media.example.invalid/scene.png",
    aliases: ["车站"],
    canonical_key: "server-scene-key",
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-02T00:00:00Z",
    ...overrides,
  };
}

function propResponse(overrides: Partial<Prop> = {}): Prop {
  return {
    id: "prop-1",
    series_id: "series-1",
    name: "褪色车票",
    description: "背面写有日期",
    image_url: "https://media.example.invalid/ticket.png",
    aliases: ["旧车票"],
    canonical_key: "server-prop-key",
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-02T00:00:00Z",
    ...overrides,
  };
}

function servicesWithResponses(responses: Response[]) {
  const storage = new MemoryStorage();
  storage.setItem(API_TOKEN_KEY, "fixture-token");
  const requests: CapturedRequest[] = [];
  const fetcher: typeof fetch = async (input, init) => {
    const url = input instanceof URL
      ? input
      : input instanceof Request
        ? new URL(input.url)
        : new URL(input);
    const rawBody = init?.body ?? (input instanceof Request ? input.body : undefined);
    requests.push({
      method: init?.method ?? (input instanceof Request ? input.method : "GET"),
      pathname: `${url.pathname}${url.search}`,
      body: typeof rawBody === "string" ? JSON.parse(rawBody) as unknown : undefined,
      authorization: new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined)).get("Authorization"),
    });
    const next = responses.shift();
    if (next === undefined) {
      throw new Error("No response was prepared for this request.");
    }
    return next;
  };
  return {
    requests,
    storage,
    services: createApiServices("http://127.0.0.1/api", { storage, fetcher }),
  };
}

const signal = () => new AbortController().signal;

describe("asset management API services", () => {
  it("keeps demo-created metadata in the service instance across directory rereads", async () => {
    const storage = new MemoryStorage();
    const services = createDemoServices(storage);
    const controller = new AbortController();
    await services.login({ username: "demo", password: "demo123" }, controller.signal);
    const seriesId = demoSeries[0]!.id;
    const create = services.createCharacter;
    if (create === undefined) throw new Error("Demo asset creation is unavailable.");

    const created = await create(seriesId, { name: "手工新增角色", description: "保留在本次服务会话中" }, controller.signal);
    const reread = await services.listCharacters(seriesId, controller.signal);
    expect(reread.find(({ id }) => id === created.id)).toMatchObject({
      name: "手工新增角色",
      description: "保留在本次服务会话中",
    });
  });

  it.each([
    ["characters", "character", "-character-linlan", "deleteCharacter"],
    ["scenes", "scene", "-scene-old-street", "deleteScene"],
    ["props", "prop", "-prop-key", "deleteProp"],
  ] as const)("removes deleted demo %s references across chapter frames while preserving other data", async (category, field, suffix, method) => {
    const storage = new MemoryStorage();
    const services = createDemoServices(storage);
    const controller = new AbortController();
    await services.login({ username: "demo", password: "demo123" }, controller.signal);
    const seriesId = demoSeries[0]!.id;
    const before = await services.listChapters(seriesId, controller.signal);
    const assetId = seriesId + suffix;
    const deleteAsset = services[method];
    if (deleteAsset === undefined) throw new Error(`Demo ${category} deletion is unavailable.`);

    await deleteAsset(assetId, controller.signal);

    const after = await services.listChapters(seriesId, controller.signal);
    const withoutDeletedReference = (frame: StoryboardFrame) => {
      const references = frame[field];
      return Array.isArray(references) && references.includes(assetId)
        ? { ...frame, [field]: references.filter((reference) => reference !== assetId) }
        : frame;
    };
    expect(after.map(({ id, content }) => ({ id, content }))).toEqual(
      before.map(({ id, content }) => ({ id, content: content?.map(withoutDeletedReference) ?? null })),
    );
    expect(after.flatMap((chapter) => chapter.content ?? []).every((frame) => {
      const references = frame[field];
      return !Array.isArray(references) || !references.includes(assetId);
    })).toBe(true);
    if (category === "characters") {
      const secondChapter = after.find(({ id }) => id === seriesId + "-chapter-letter");
      expect(secondChapter?.content?.[0]?.character).toEqual([seriesId + "-shared-material-id"]);
    }
  });

  it("serves local task receipt and request details in demo mode without inventing billing data", async () => {
    const storage = new MemoryStorage();
    const services = createDemoServices(storage);
    const controller = new AbortController();
    await services.login({ username: "demo", password: "demo123" }, controller.signal);

    const receipt = await services.getTaskReceipt?.("task-demo-01", controller.signal);
    const request = await services.getTaskRequest?.("task-demo-01", controller.signal);

    expect(receipt).toMatchObject({
      id: "task-demo-01",
      type: "image",
      status: "completed",
      billingStatus: null,
      quotedAmount: null,
    });
    expect(request).toMatchObject({
      id: "task-demo-01",
      type: "image",
      creditCost: null,
      request: { prompt: "本地演示记录" },
    });
    await expect(services.getTaskReceipt?.("missing-demo-task", controller.signal)).rejects.toMatchObject({ status: 404 });
    await expect(services.getTaskRequest?.("missing-demo-task", controller.signal)).rejects.toMatchObject({ status: 404 });

    const aborted = new AbortController();
    aborted.abort();
    await expect(services.getTaskReceipt?.("task-demo-01", aborted.signal)).rejects.toMatchObject({ kind: "aborted" });
  });

  it("keeps malformed demo task request data visible as raw text", async () => {
    const storage = new MemoryStorage();
    const services = createDemoServices(storage);
    const controller = new AbortController();
    await services.login({ username: "demo", password: "demo123" }, controller.signal);

    await expect(services.getTaskRequest?.("task-demo-11", controller.signal)).resolves.toMatchObject({
      request: { _raw: "截断的旧请求文本，不会在任务卡片显示。" },
    });
  });

  it("sends the three create requests to their series routes and validates returned identities", async () => {
    const seriesId = "series /?&雪";
    const { requests, services } = servicesWithResponses([
      jsonResponse(characterResponse({ series_id: seriesId }), 201),
      jsonResponse(sceneResponse({ series_id: seriesId }), 201),
      jsonResponse(propResponse({ series_id: seriesId }), 201),
    ]);

    await services.createCharacter?.(seriesId, { name: "沈照", description: "旧城记者" }, signal());
    await services.createScene?.(seriesId, { title: "旧站台" }, signal());
    await services.createProp?.(seriesId, { name: "褪色车票" }, signal());

    expect(requests.map(({ method, pathname, body }) => [method, pathname, body])).toEqual([
      ["POST", "/api/series/series%20%2F%3F%26%E9%9B%AA/characters", { name: "沈照", description: "旧城记者" }],
      ["POST", "/api/series/series%20%2F%3F%26%E9%9B%AA/scenes", { title: "旧站台" }],
      ["POST", "/api/series/series%20%2F%3F%26%E9%9B%AA/props", { name: "褪色车票" }],
    ]);
    expect(requests.every(({ authorization }) => authorization === "Bearer fixture-token")).toBe(true);
  });

  it("sends sparse updates and preserves all response metadata not included in the update", async () => {
    const updatedCharacter = characterResponse({ name: "新名字", description: "" });
    const updatedScene = sceneResponse({ description: "" });
    const updatedProp = propResponse({ name: "新道具名" });
    const { requests, services } = servicesWithResponses([
      jsonResponse(updatedCharacter),
      jsonResponse(updatedScene),
      jsonResponse(updatedProp),
    ]);

    const character = await services.updateCharacter?.("series-1", "character-1", { name: "新名字", description: "" }, signal());
    const scene = await services.updateScene?.("series-1", "scene-1", { description: "" }, signal());
    const prop = await services.updateProp?.("series-1", "prop-1", { name: "新道具名" }, signal());

    expect(requests.map(({ method, pathname, body }) => [method, pathname, body])).toEqual([
      ["PUT", "/api/characters/character-1", { name: "新名字", description: "" }],
      ["PUT", "/api/scenes/scene-1", { description: "" }],
      ["PUT", "/api/props/prop-1", { name: "新道具名" }],
    ]);
    expect(character).toMatchObject({ image_url: updatedCharacter.image_url, audio_url: updatedCharacter.audio_url, voice_ref: updatedCharacter.voice_ref, aliases: ["阿照"] });
    expect(scene).toMatchObject({ image_url: updatedScene.image_url, aliases: ["车站"] });
    expect(prop).toMatchObject({ image_url: updatedProp.image_url, aliases: ["旧车票"] });
  });

  it("deletes by exact id and requires the backend's 204 response", async () => {
    const { requests, services } = servicesWithResponses([
      new Response(null, { status: 204 }),
      new Response(null, { status: 204 }),
      new Response(null, { status: 204 }),
    ]);

    await services.deleteCharacter?.("character-1", signal());
    await services.deleteScene?.("scene-1", signal());
    await services.deleteProp?.("prop-1", signal());

    expect(requests.map(({ method, pathname }) => [method, pathname])).toEqual([
      ["DELETE", "/api/characters/character-1"],
      ["DELETE", "/api/scenes/scene-1"],
      ["DELETE", "/api/props/prop-1"],
    ]);
  });

  it.each([403, 404, 409, 422])("preserves HTTP %s from an update endpoint", async (status) => {
    const { services } = servicesWithResponses([jsonResponse({ detail: "写入被拒绝" }, status)]);

    await expect(services.updateCharacter?.("series-1", "character-1", { description: "new" }, signal()))
      .rejects.toMatchObject({ kind: "http", status });
  });

  it("rejects a successful update response for the wrong series or asset id", async () => {
    const { services } = servicesWithResponses([
      jsonResponse(characterResponse({ series_id: "other-series" })),
      jsonResponse(characterResponse({ id: "other-character" })),
    ]);

    await expect(services.updateCharacter?.("series-1", "character-1", { description: "new" }, signal()))
      .rejects.toThrow(InvalidResponseError);
    await expect(services.updateCharacter?.("series-1", "character-1", { description: "new" }, signal()))
      .rejects.toThrow(InvalidResponseError);
  });

  it("isolates a late 401 after the token changes", async () => {
    let resolveResponse!: (response: Response) => void;
    const pendingResponse = new Promise<Response>((resolve) => { resolveResponse = resolve; });
    const { storage } = servicesWithResponses([]);
    const late401Services = createApiServices("http://127.0.0.1/api", {
      storage,
      fetcher: async () => pendingResponse,
    });
    const pending = late401Services.createCharacter?.("series-1", { name: "新角色" }, signal());
    storage.setItem(API_TOKEN_KEY, "new-session-token");
    resolveResponse(jsonResponse({ detail: "expired" }, 401));

    await expect(pending).rejects.toMatchObject({ kind: "aborted" });
    expect(storage.getItem(API_TOKEN_KEY)).toBe("new-session-token");
  });
});
