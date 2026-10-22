// @vitest-environment node
import { describe, expect, it, vi } from "vitest";
import {
  AssetImageUploadError,
  type AssetImageUploadRunContext,
} from "./assetImageUpload";
import { createAssetImageUploadHttpPort } from "./assetImageUploadHttp";
import type { Character, Prop, Scene } from "./contracts";
import { API_TOKEN_KEY, API_USER_KEY, type StorageLike } from "./storage";

class MemoryStorage implements StorageLike {
  private readonly values = new Map<string, string>();

  getItem(key: string): string | null { return this.values.get(key) ?? null; }
  setItem(key: string, value: string): void { this.values.set(key, value); }
  removeItem(key: string): void { this.values.delete(key); }
}

const seriesId = "series-image-558";
const actorId = "actor-image-558";
const assetId = "asset-image-558";
const uploadedUrl = "http://127.0.0.1:4175/uploads/image-558.png";

function character(imageUrl: string | null = null): Character {
  return {
    id: assetId,
    series_id: seriesId,
    name: "林照",
    gender: "女",
    age: "28",
    role: "摄影师",
    appearance: "短发，背着旧相机。",
    description: "收集城市的旧照片。",
    image_url: imageUrl,
    audio_url: null,
    voice_ref: null,
    aliases: ["阿照"],
    canonical_key: "preserved-character-key",
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-02T00:00:00Z",
  };
}

function scene(imageUrl: string | null = null): Scene {
  return {
    id: assetId,
    series_id: seriesId,
    title: "旧城暗房",
    description: "挂着红色遮光布的暗房。",
    image_url: imageUrl,
    aliases: null,
    canonical_key: "preserved-scene-key",
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-02T00:00:00Z",
  };
}

function prop(imageUrl: string | null = null): Prop {
  return {
    id: assetId,
    series_id: seriesId,
    name: "显影液",
    description: "半瓶显影液。",
    image_url: imageUrl,
    aliases: null,
    canonical_key: "preserved-prop-key",
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-02T00:00:00Z",
  };
}

function createStorage(): MemoryStorage {
  const storage = new MemoryStorage();
  storage.setItem(API_TOKEN_KEY, "captured-token");
  storage.setItem(API_USER_KEY, JSON.stringify({ id: actorId }));
  return storage;
}

function context(overrides: Partial<AssetImageUploadRunContext> = {}): AssetImageUploadRunContext {
  return {
    actorId,
    seriesId,
    category: "characters",
    assetId,
    directoryEpoch: 12,
    portEpoch: 19,
    asset: character("http://127.0.0.1:4175/uploads/current.png"),
    isContextCurrent: () => true,
    ...overrides,
  };
}

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), { status, headers: { "content-type": "application/json" } });
}

function uploadFile(): File {
  return new File([new Uint8Array([1, 2, 3])], "portrait.png", { type: "image/png" });
}

describe("asset image upload HTTP port", () => {
  it("uploads once, explicitly saves only image_url, then reads the directory and owned image", async () => {
    const storage = createStorage();
    const calls: Array<{ url: URL; init: RequestInit }> = [];
    const fetcher: typeof fetch = async (input, init = {}) => {
      const url = input instanceof URL ? input : new URL(input instanceof Request ? input.url : input);
      calls.push({ url, init });
      if (init.method === "POST" && url.pathname === "/api/upload/image") {
        expect(init.body).toBeInstanceOf(FormData);
        expect((init.body as FormData).get("file")).toBeInstanceOf(File);
        return jsonResponse({ url: uploadedUrl });
      }
      if (init.method === "PUT" && url.pathname === `/api/characters/${assetId}`) {
        expect(JSON.parse(String(init.body))).toEqual({ image_url: uploadedUrl });
        return jsonResponse(character(uploadedUrl));
      }
      if (init.method === "GET" && url.pathname === `/api/series/${seriesId}/characters`) {
        return jsonResponse([character(uploadedUrl)]);
      }
      if (init.method === "GET" && url.pathname === "/uploads/image-558.png") {
        return new Response(new Uint8Array([137, 80, 78, 71]), { headers: { "content-type": "image/png" } });
      }
      throw new Error(`Unexpected request ${init.method} ${url.pathname}`);
    };
    const port = createAssetImageUploadHttpPort("http://127.0.0.1:4175/api", { storage, fetcher });
    const run = port.beginRun(context());
    const controller = new AbortController();

    const receipt = await run.uploadImage(uploadFile(), controller.signal);
    expect(run.canSaveImage(receipt)).toBe(true);
    const updated = await run.saveImage(receipt, controller.signal);
    const directory = await run.readDirectory(controller.signal);
    const blob = await run.readImage(receipt, controller.signal);

    expect(updated).toMatchObject({ image_url: uploadedUrl, audio_url: null, canonical_key: "preserved-character-key" });
    expect(directory.items).toEqual([character(uploadedUrl)]);
    expect(directory.imageSources.has(assetId)).toBe(true);
    expect([...new Uint8Array(await blob.arrayBuffer())]).toEqual([137, 80, 78, 71]);
    expect(run.canSaveImage(receipt)).toBe(false);
    expect(calls.map(({ init, url }) => `${init.method} ${url.pathname}`)).toEqual([
      "POST /api/upload/image",
      `PUT /api/characters/${assetId}`,
      `GET /api/series/${seriesId}/characters`,
      "GET /uploads/image-558.png",
    ]);
    expect(calls.every(({ init }) => (init.headers as Record<string, string>).Authorization === "Bearer captured-token")).toBe(true);
  });

  it("classifies a dispatched save with an ambiguous response as unknown and does not retry it", async () => {
    const storage = createStorage();
    const methods: string[] = [];
    const fetcher: typeof fetch = async (_input, init = {}) => {
      methods.push(init.method ?? "GET");
      return init.method === "POST" ? jsonResponse({ url: uploadedUrl }) : jsonResponse({ detail: "not available" }, 503);
    };
    const run = createAssetImageUploadHttpPort("http://127.0.0.1:4175/api", { storage, fetcher })
      .beginRun(context());
    const signal = new AbortController().signal;
    const receipt = await run.uploadImage(uploadFile(), signal);

    await expect(run.saveImage(receipt, signal)).rejects.toMatchObject({ kind: "unknown", status: 503 });
    await expect(run.saveImage(receipt, signal)).rejects.toMatchObject({ kind: "not-sent" });
    expect(methods).toEqual(["POST", "PUT"]);
  });

  it("classifies a directory read invalidated after dispatch as a read error", async () => {
    const storage = createStorage();
    let isCurrent = true;
    let resolveResponse!: (response: Response) => void;
    const pendingResponse = new Promise<Response>((resolve) => { resolveResponse = resolve; });
    const fetcher: typeof fetch = async () => pendingResponse;
    const run = createAssetImageUploadHttpPort("http://127.0.0.1:4175/api", { storage, fetcher })
      .beginRun(context({ isContextCurrent: () => isCurrent }));
    const read = run.readDirectory(new AbortController().signal);

    isCurrent = false;
    resolveResponse(jsonResponse([character()]));

    await expect(read).rejects.toMatchObject({ kind: "read-error" });
  });

  it("rejects invalid files and a replaced session before dispatch", async () => {
    const storage = createStorage();
    const fetcher = vi.fn(async () => jsonResponse({ url: uploadedUrl })) as typeof fetch;
    const port = createAssetImageUploadHttpPort("http://127.0.0.1:4175/api", { storage, fetcher });
    const run = port.beginRun(context());
    const signal = new AbortController().signal;
    const invalidFile = new File([new Uint8Array([1])], "portrait.png", { type: "text/plain" });

    await expect(run.uploadImage(invalidFile, signal)).rejects.toMatchObject({ kind: "not-sent" });
    storage.setItem(API_TOKEN_KEY, "replacement-token");
    await expect(run.uploadImage(uploadFile(), signal)).rejects.toMatchObject({ kind: "not-sent" });
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("refuses to display a successful but empty proxy response", async () => {
    const storage = createStorage();
    const currentSource = context().asset.image_url!;
    const fetcher = vi.fn(async () => new Response(new Uint8Array(), {
      status: 200,
      headers: { "content-type": "image/png" },
    })) as typeof fetch;
    const run = createAssetImageUploadHttpPort("http://127.0.0.1:4175/api", { storage, fetcher })
      .beginRun(context());

    await expect(run.readImage(run.initialImageSource!, new AbortController().signal))
      .rejects.toMatchObject({ kind: "read-error" } satisfies Partial<AssetImageUploadError>);
    expect(currentSource).toContain("/uploads/current.png");
    expect(fetcher).toHaveBeenCalledOnce();
  });

  it("preserves a public upload URL and reads it only through the authenticated image proxy", async () => {
    const storage = createStorage();
    const publicUrl = "https://images.example.invalid/assets/portrait.png?version=7";
    const calls: Array<{ url: URL; init: RequestInit }> = [];
    const fetcher: typeof fetch = async (input, init = {}) => {
      const url = input instanceof URL ? input : new URL(input instanceof Request ? input.url : input);
      calls.push({ url, init });
      if (init.method === "POST") return jsonResponse({ url: publicUrl });
      if (init.method === "PUT") return jsonResponse(character(publicUrl));
      if (init.method === "GET" && url.pathname === "/api/chat/proxy-image") {
        return new Response(new Uint8Array([137, 80, 78, 71]), { headers: { "content-type": "image/png" } });
      }
      throw new Error(`Unexpected direct public image request: ${init.method} ${url.href}`);
    };
    const run = createAssetImageUploadHttpPort("http://127.0.0.1:4175/api", { storage, fetcher })
      .beginRun(context());
    const signal = new AbortController().signal;

    const uploaded = await run.uploadImage(uploadFile(), signal);
    expect(uploaded.url).toBe(publicUrl);
    await run.saveImage(uploaded, signal);
    const image = await run.readImage(uploaded, signal);

    expect(image.type).toBe("image/png");
    expect(calls.map(({ init, url }) => `${init.method} ${url.pathname}`)).toEqual([
      "POST /api/upload/image",
      `PUT /api/characters/${assetId}`,
      "GET /api/chat/proxy-image",
    ]);
    expect(JSON.parse(String(calls[1]?.init.body))).toEqual({ image_url: publicUrl });
    expect(calls[2]?.url.searchParams.get("url")).toBe(publicUrl);
    expect((calls[2]?.init.headers as Record<string, string>).Authorization).toBe("Bearer captured-token");
  });

  it.each([
    { label: "credential-bearing", url: "https://reader:password@images.example.invalid/portrait.png" },
    { label: "private network", url: "http://192.168.1.44/uploads/portrait.png" },
  ])("rejects a $label upload URL without granting it save or preview authority", async ({ url }) => {
    const storage = createStorage();
    const fetcher: typeof fetch = vi.fn(async () => jsonResponse({ url })) as typeof fetch;
    const run = createAssetImageUploadHttpPort("http://127.0.0.1:4175/api", { storage, fetcher })
      .beginRun(context());

    await expect(run.uploadImage(uploadFile(), new AbortController().signal))
      .rejects.toMatchObject({ kind: "unknown" });
    expect(fetcher).toHaveBeenCalledOnce();
  });

  it("uses the category-specific directory and image update endpoints for all three asset types", async () => {
    const cases = [
      { category: "characters" as const, asset: character(), updated: character(uploadedUrl) },
      { category: "scenes" as const, asset: scene(), updated: scene(uploadedUrl) },
      { category: "props" as const, asset: prop(), updated: prop(uploadedUrl) },
    ];
    for (const { category, asset, updated } of cases) {
      const storage = createStorage();
      const calls: Array<{ method: string; pathname: string; body: string }> = [];
      const fetcher: typeof fetch = async (input, init = {}) => {
        const url = input instanceof URL ? input : new URL(input instanceof Request ? input.url : input);
        calls.push({ method: init.method ?? "GET", pathname: url.pathname, body: String(init.body ?? "") });
        if (init.method === "POST") return jsonResponse({ url: uploadedUrl });
        if (init.method === "PUT") return jsonResponse(updated);
        return jsonResponse([{ ...asset, image_url: uploadedUrl }]);
      };
      const run = createAssetImageUploadHttpPort("http://127.0.0.1:4175/api", { storage, fetcher })
        .beginRun(context({ category, asset }));
      const signal = new AbortController().signal;
      const uploaded = await run.uploadImage(uploadFile(), signal);
      await run.saveImage(uploaded, signal);
      const directory = await run.readDirectory(signal);

      expect(directory.category).toBe(category);
      expect(calls.map(({ method, pathname }) => `${method} ${pathname}`)).toEqual([
        "POST /api/upload/image",
        `PUT /api/${category}/${assetId}`,
        `GET /api/series/${seriesId}/${category}`,
      ]);
      expect(JSON.parse(calls[1]!.body)).toEqual({ image_url: uploadedUrl });
    }
  });

  it("does not turn a replaced token into an unauthorized response for the stale run", async () => {
    const storage = createStorage();
    let resolveResponse!: (response: Response) => void;
    const pendingResponse = new Promise<Response>((resolve) => { resolveResponse = resolve; });
    const fetcher: typeof fetch = async () => pendingResponse;
    const run = createAssetImageUploadHttpPort("http://127.0.0.1:4175/api", { storage, fetcher })
      .beginRun(context());
    const upload = run.uploadImage(uploadFile(), new AbortController().signal);

    storage.setItem(API_TOKEN_KEY, "new-token-for-same-actor");
    resolveResponse(jsonResponse({ detail: "Unauthorized" }, 401));
    await expect(upload).rejects.toMatchObject({ kind: "unknown", status: null });
    expect(run.isCurrent()).toBe(false);
  });

  it("classifies malformed directory data after an acknowledged PUT as a read error", async () => {
    const storage = createStorage();
    const fetcher: typeof fetch = async (_input, init = {}) => {
      if (init.method === "POST") return jsonResponse({ url: uploadedUrl });
      if (init.method === "PUT") return jsonResponse(character(uploadedUrl));
      return jsonResponse({ invalid: true });
    };
    const run = createAssetImageUploadHttpPort("http://127.0.0.1:4175/api", { storage, fetcher })
      .beginRun(context());
    const signal = new AbortController().signal;
    const uploaded = await run.uploadImage(uploadFile(), signal);
    await run.saveImage(uploaded, signal);

    await expect(run.readDirectory(signal)).rejects.toMatchObject({ kind: "read-error" });
  });

  it("cancels rejected and stale image response bodies", async () => {
    const storage = createStorage();
    let cancelCount = 0;
    const rejectedBody = new ReadableStream<Uint8Array>({
      cancel() { cancelCount += 1; },
    });
    const rejectedRun = createAssetImageUploadHttpPort("http://127.0.0.1:4175/api", {
      storage,
      fetcher: vi.fn(async () => new Response(rejectedBody, {
        status: 200,
        headers: { "content-type": "text/html" },
      })) as typeof fetch,
    }).beginRun(context());
    await expect(rejectedRun.readImage(rejectedRun.initialImageSource!, new AbortController().signal))
      .rejects.toMatchObject({ kind: "read-error" });
    expect(cancelCount).toBe(1);

    let current = true;
    let resolveResponse!: (response: Response) => void;
    const pendingResponse = new Promise<Response>((resolve) => { resolveResponse = resolve; });
    const staleBody = new ReadableStream<Uint8Array>({
      cancel() { cancelCount += 1; },
    });
    const staleRun = createAssetImageUploadHttpPort("http://127.0.0.1:4175/api", {
      storage,
      fetcher: vi.fn(async () => pendingResponse) as typeof fetch,
    }).beginRun(context({ isContextCurrent: () => current }));
    const read = staleRun.readImage(staleRun.initialImageSource!, new AbortController().signal);
    current = false;
    resolveResponse(new Response(staleBody, { headers: { "content-type": "image/png" } }));

    await expect(read).rejects.toMatchObject({ kind: "read-error" });
    expect(cancelCount).toBe(2);
  });
});
