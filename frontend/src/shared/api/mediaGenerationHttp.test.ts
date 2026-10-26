import { describe, expect, it, vi } from "vitest";
import { createMediaGenerationHttpPort } from "./mediaGenerationHttp";
import { API_TOKEN_KEY, API_USER_KEY, type StorageLike } from "./storage";
import { createApiServices } from "./services";

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

function authenticatedStorage(): MemoryStorage {
  const storage = new MemoryStorage();
  storage.setItem(API_TOKEN_KEY, "session-token-926");
  storage.setItem(API_USER_KEY, JSON.stringify({ id: "actor-926" }));
  return storage;
}

function runContext() {
  return { actorId: "actor-926", isContextCurrent: () => true };
}

function videoReceipt() {
  return {
    submission_id: "submission-926",
    operation: "video.single",
    task_id: "task-video-926",
    message_id: "message-video-926",
    status: "queued",
    quoted_amount: 12,
    billing_status: "charged",
  };
}

function imageReceipt() {
  return {
    submission_id: "submission-image-936",
    operation: "image.single",
    task_id: "task-image-936",
    message_id: "message-image-936",
    status: "queued",
    quoted_amount: 4,
    billing_status: "charged",
  };
}

describe("media generation HTTP port", () => {
  it("is exposed by the authenticated API services factory", () => {
    const services = createApiServices("http://127.0.0.1:4175/api", {
      storage: authenticatedStorage(),
      fetcher: vi.fn<typeof fetch>(),
    });

    expect(services.mediaGeneration).toBeDefined();
  });

  it("sends video.single with its frozen target and reads the flat original-key receipt", async () => {
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(Response.json(videoReceipt()))
      .mockResolvedValueOnce(Response.json(videoReceipt()));
    const port = createMediaGenerationHttpPort("http://127.0.0.1:4175/api", {
      storage: authenticatedStorage(),
      fetcher,
    });
    const run = port.beginRun(runContext());
    const signal = new AbortController().signal;
    const input = {
      modelId: "model-video-926",
      prompt: "雨夜中的街道缓慢推进",
      target: { kind: "asset_message" as const, chapter_id: "chapter-926", asset_type: "storyboard" as const, asset_id: "frame-926" },
      size: "16:9",
      duration: 5,
      resolution: "720p",
      referenceImages: ["https://example.test/ref.png"],
      referenceAudios: ["https://example.test/ref.wav"],
      referenceVideos: ["https://example.test/ref.mp4"],
      idempotencyKey: "video-original-key-926",
    };

    await expect(run.submitVideo(input, signal)).resolves.toMatchObject({
      operation: "video.single",
      taskId: "task-video-926",
      quotedAmount: 12,
    });
    await expect(run.lookup({ operation: "video.single", idempotencyKey: input.idempotencyKey }, signal))
      .resolves.toMatchObject({ operation: "video.single", taskId: "task-video-926" });

    const [postInput, postInit] = fetcher.mock.calls[0]!;
    const postUrl = new URL(String(postInput));
    expect(postUrl.pathname).toBe("/api/chat/video");
    expect(postInit?.method).toBe("POST");
    expect(new Headers(postInit?.headers).get("Idempotency-Key")).toBe(input.idempotencyKey);
    expect(JSON.parse(String(postInit?.body))).toEqual({
      model_id: "model-video-926",
      prompt: input.prompt,
      target: input.target,
      size: "16:9",
      duration: 5,
      resolution: "720p",
      reference_images: input.referenceImages,
      reference_audios: input.referenceAudios,
      reference_videos: input.referenceVideos,
    });

    const [lookupInput, lookupInit] = fetcher.mock.calls[1]!;
    const lookupUrl = new URL(String(lookupInput));
    expect(lookupUrl.pathname).toBe("/api/chat/submissions/video.single");
    expect(lookupUrl.searchParams.get("idempotency_key")).toBe(input.idempotencyKey);
    expect(lookupInit?.method).toBe("GET");
  });

  it("sends image.single without video-only fields and reads its flat receipt", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(Response.json(imageReceipt()));
    const port = createMediaGenerationHttpPort("http://127.0.0.1:4175/api", {
      storage: authenticatedStorage(),
      fetcher,
    });
    const run = port.beginRun(runContext());
    const input = {
      modelId: "model-image-936",
      prompt: "雨巷分镜的静帧插画",
      target: { kind: "frame_message" as const, chapter_id: "chapter-936", storyboard_asset_id: "frame-936" },
      size: "1024x1024",
      referenceImages: ["https://example.test/first.png", "https://example.test/second.png"],
      idempotencyKey: "image-original-key-936",
    };

    await expect(run.submitImage(input, new AbortController().signal)).resolves.toMatchObject({
      operation: "image.single",
      taskId: "task-image-936",
      quotedAmount: 4,
    });

    const [postInput, postInit] = fetcher.mock.calls[0]!;
    expect(new URL(String(postInput)).pathname).toBe("/api/chat/image");
    expect(postInit?.method).toBe("POST");
    expect(new Headers(postInit?.headers).get("Idempotency-Key")).toBe(input.idempotencyKey);
    expect(JSON.parse(String(postInit?.body))).toEqual({
      model_id: input.modelId,
      prompt: input.prompt,
      target: input.target,
      size: input.size,
      reference_images: input.referenceImages,
    });
  });

  it("looks up an uncertain image.single submission only with its original key", async () => {
    const fetcher = vi.fn<typeof fetch>()
      .mockRejectedValueOnce(new TypeError("offline"))
      .mockResolvedValueOnce(Response.json(imageReceipt()));
    const port = createMediaGenerationHttpPort("http://127.0.0.1:4175/api", {
      storage: authenticatedStorage(),
      fetcher,
    });
    const run = port.beginRun(runContext());
    const input = {
      modelId: "model-image-936",
      prompt: "雾港的独立插画",
      target: { kind: "standalone" as const },
      referenceImages: [],
      idempotencyKey: "image-uncertain-key-936",
    };

    await expect(run.submitImage(input, new AbortController().signal)).rejects.toMatchObject({ kind: "unknown-outcome" });
    await expect(run.lookup({ operation: "image.single", idempotencyKey: input.idempotencyKey }, new AbortController().signal))
      .resolves.toMatchObject({ operation: "image.single", taskId: "task-image-936" });
    expect(fetcher).toHaveBeenCalledTimes(2);
    const [lookupInput, lookupInit] = fetcher.mock.calls[1]!;
    const lookupUrl = new URL(String(lookupInput));
    expect(lookupUrl.pathname).toBe("/api/chat/submissions/image.single");
    expect(lookupUrl.searchParams.get("idempotency_key")).toBe(input.idempotencyKey);
    expect(lookupInit?.method).toBe("GET");
  });

  it("submits one ordered image.batch request and requires the same ordered task receipt on lookup", async () => {
    const expectedTasks = [
      { type: "storyboard", asset_id: "storyboard-926", asset_name: "雨巷转场镜头", message_id: "message-frame", task_id: "task-frame" },
      { type: "character", asset_id: "character-926", asset_name: "林舟", message_id: "message-a", task_id: "task-a" },
      { type: "scene", asset_id: "scene-926", asset_name: "雨巷", message_id: "message-b", task_id: "task-b" },
    ];
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(Response.json({ tasks: expectedTasks }))
      .mockResolvedValueOnce(Response.json({ tasks: expectedTasks }));
    const port = createMediaGenerationHttpPort("http://127.0.0.1:4175/api", {
      storage: authenticatedStorage(),
      fetcher,
    });
    const run = port.beginRun(runContext());
    const signal = new AbortController().signal;
    const input = {
      chapterId: "chapter-926",
      assets: [
        {
          type: "storyboard" as const,
          id: "storyboard-926",
          name: "雨巷转场镜头",
          description: "石板路上的中景转场",
          modelId: "model-image-926",
          promptId: "prompt-926",
          size: "1024x1024",
          referenceImages: ["https://example.test/frame.png"],
        },
        {
          type: "character" as const,
          id: "character-926",
          name: "林舟",
          description: "长风衣，沉着",
          modelId: "model-image-926",
          promptId: "prompt-926",
          size: "1024x1024",
          referenceImages: ["https://example.test/character.png"],
        },
        {
          type: "scene" as const,
          id: "scene-926",
          name: "雨巷",
          description: "石板路，夜雨",
          modelId: "model-image-926",
          promptId: "prompt-926",
          size: "1024x1024",
          referenceImages: ["https://example.test/scene.png"],
        },
      ],
      idempotencyKey: "batch-original-key-926",
    };
    const expected = {
      operation: "image.batch" as const,
      idempotencyKey: input.idempotencyKey,
      batchAssets: input.assets.map(({ type, id }) => ({ type, id })),
    };

    await expect(run.submitBatch(input, signal)).resolves.toMatchObject({
      operation: "image.batch",
      tasks: [{ type: "storyboard", assetId: "storyboard-926" }, { assetId: "character-926" }, { assetId: "scene-926" }],
    });
    await expect(run.lookup(expected, signal)).resolves.toMatchObject({ operation: "image.batch", tasks: [{ taskId: "task-frame" }, { taskId: "task-a" }, { taskId: "task-b" }] });

    expect(fetcher).toHaveBeenCalledTimes(2);
    const [postInput, postInit] = fetcher.mock.calls[0]!;
    expect(new URL(String(postInput)).pathname).toBe("/api/chat/batch-generate-images");
    expect(postInit?.method).toBe("POST");
    expect(new Headers(postInit?.headers).get("Idempotency-Key")).toBe(input.idempotencyKey);
    expect(JSON.parse(String(postInit?.body))).toEqual({
      chapter_id: "chapter-926",
      assets: [
        {
          type: "storyboard", id: "storyboard-926", name: "雨巷转场镜头", description: "石板路上的中景转场",
          model_id: "model-image-926", prompt_id: "prompt-926", size: "1024x1024",
          reference_images: ["https://example.test/frame.png"],
        },
        {
          type: "character", id: "character-926", name: "林舟", description: "长风衣，沉着",
          model_id: "model-image-926", prompt_id: "prompt-926", size: "1024x1024",
          reference_images: ["https://example.test/character.png"],
        },
        {
          type: "scene", id: "scene-926", name: "雨巷", description: "石板路，夜雨",
          model_id: "model-image-926", prompt_id: "prompt-926", size: "1024x1024",
          reference_images: ["https://example.test/scene.png"],
        },
      ],
    });
    const lookupUrl = new URL(String(fetcher.mock.calls[1]![0]));
    expect(lookupUrl.pathname).toBe("/api/chat/submissions/image.batch");
    expect(lookupUrl.searchParams.get("idempotency_key")).toBe(input.idempotencyKey);
  });

  it("classifies a dispatched network failure as unknown so the caller can only look up the original key", async () => {
    const fetcher = vi.fn<typeof fetch>()
      .mockRejectedValueOnce(new TypeError("offline"))
      .mockResolvedValueOnce(Response.json(videoReceipt()));
    const port = createMediaGenerationHttpPort("http://127.0.0.1:4175/api", {
      storage: authenticatedStorage(),
      fetcher,
    });
    const run = port.beginRun(runContext());
    const input = {
      modelId: "model-video-926",
      prompt: "风吹过空巷",
      target: { kind: "standalone" as const },
      referenceImages: [],
      referenceAudios: [],
      referenceVideos: [],
      idempotencyKey: "video-unknown-key-926",
    };
    const signal = new AbortController().signal;

    await expect(run.submitVideo(input, signal)).rejects.toMatchObject({ kind: "unknown-outcome" });
    await expect(run.lookup({ operation: "video.single", idempotencyKey: input.idempotencyKey }, signal))
      .resolves.toMatchObject({ taskId: "task-video-926" });
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(new URL(String(fetcher.mock.calls[1]![0])).searchParams.get("idempotency_key")).toBe(input.idempotencyKey);
    expect(fetcher.mock.calls[1]![1]?.method).toBe("GET");
  });
});
