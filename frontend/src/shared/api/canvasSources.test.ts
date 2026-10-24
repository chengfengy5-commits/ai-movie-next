import { describe, expect, it, vi } from "vitest";
import {
  CANVAS_SOURCE_CAPABILITY,
  CanvasSourceError,
  createCanvasSourceHttpPort,
  resolveCanvasFrameTarget,
  type CanvasSourceSnapshot,
} from "./canvasSources";
import { API_TOKEN_KEY, API_USER_KEY, type StorageLike } from "./storage";

function memoryStorage(): StorageLike {
  const values = new Map([
    [API_TOKEN_KEY, "canvas-source-token"],
    [API_USER_KEY, JSON.stringify({ id: "actor-a" })],
  ]);
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => { values.set(key, value); },
    removeItem: (key) => { values.delete(key); },
  };
}

function sourcePayload(overrides: Record<string, unknown> = {}) {
  return {
    capability: CANVAS_SOURCE_CAPABILITY,
    series_id: "series-a",
    chapter_id: "chapter-a",
    version: "8be1499c4eb8b1a1f8e20d020cd52796e938726f0a1889cfecc07641d8d28a57",
    requires_lock: false,
    chapter: {
      id: "chapter-a",
      series_id: "series-a",
      title: "Synthetic chapter",
      content: [{ text: "第一镜", storyboard: ["board-a"], custom: "保留" }],
      order: 1,
      created_at: "2026-10-24T12:00:00",
      updated_at: "2026-10-24T12:00:00",
      lock: null,
    },
    storyboard_assets: [{
      id: "board-a",
      series_id: "series-a",
      chapter_id: "chapter-a",
      frame_index: 0,
      name: "分镜 1",
      description: "第一镜",
      image_url: null,
      created_at: "2026-10-24T12:00:00",
      updated_at: "2026-10-24T12:00:00",
    }],
    characters: [],
    scenes: [],
    props: [],
    ...overrides,
  };
}

function frozenSnapshot(): CanvasSourceSnapshot {
  return {
    ...sourcePayload(),
    chapter: {
      ...sourcePayload().chapter,
      content: [{ text: "第一镜", storyboard: ["board-a"], custom: "保留" }],
      lock: null,
    },
    storyboard_assets: sourcePayload().storyboard_assets as CanvasSourceSnapshot["storyboard_assets"],
    characters: [],
    scenes: [],
    props: [],
  } as CanvasSourceSnapshot;
}

describe("canvas source HTTP adapter", () => {
  it.each([
    { storyboard: [], expected: { index: 0, storyboard_id: null } },
    { storyboard: [null, ""], expected: { index: 0, storyboard_id: null } },
    { storyboard: ["", null], expected: { index: 0, storyboard_id: null } },
    { storyboard: [null], expected: { index: 0, storyboard_id: null } },
    { storyboard: [null, "real-id"], expected: null },
    { storyboard: [" "], expected: null },
    { storyboard: [null, "\t"], expected: null },
    { storyboard: null, expected: null },
  ])("resolves the backend null-reference form $storyboard", ({ storyboard, expected }) => {
    const current = {
      ...frozenSnapshot().chapter,
      content: [{ text: "frame", storyboard }],
    } as CanvasSourceSnapshot["chapter"];
    expect(resolveCanvasFrameTarget(current, [], 0)).toEqual(expected);
  });

  it("reads a validated source snapshot without dropping unknown frame fields", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json(sourcePayload()));
    const port = createCanvasSourceHttpPort("http://127.0.0.1:8000/api", {
      storage: memoryStorage(),
      fetcher,
    });

    const snapshot = await port.read("chapter-a", new AbortController().signal);

    const [input, init] = fetcher.mock.calls[0]!;
    expect(new URL(String(input)).pathname).toBe("/api/chapters/chapter-a/canvas-source");
    expect(init?.method).toBe("GET");
    expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer canvas-source-token");
    expect(init?.credentials).toBe("omit");
    expect(snapshot.version).toBe(sourcePayload().version);
    expect(snapshot.chapter.content?.[0]).toMatchObject({ text: "第一镜", custom: "保留" });
    expect(snapshot.requires_lock).toBe(false);
  });

  it("rejects a source snapshot without the server-derived lock requirement", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json(sourcePayload({ requires_lock: null })));
    const port = createCanvasSourceHttpPort("http://127.0.0.1:8000/api", {
      storage: memoryStorage(),
      fetcher,
    });

    await expect(port.read("chapter-a", new AbortController().signal))
      .rejects.toMatchObject({ kind: "unknown" });
  });

  it("sends a frozen version with one request id and validates its acknowledgement", async () => {
    const snapshot = frozenSnapshot();
    const command = { type: "frame.update", target: { index: 0, storyboard_id: "board-a" }, text: "改过的镜头" } as const;
    const fetcher = vi.fn<typeof fetch>(async (_input, init) => {
      const request = JSON.parse(String(init?.body)) as { request_id: string; command: unknown };
      const nextSource = sourcePayload({
        version: "9be1499c4eb8b1a1f8e20d020cd52796e938726f0a1889cfecc07641d8d28a57",
        chapter: {
          ...sourcePayload().chapter,
          content: [{ text: "改过的镜头", storyboard: ["board-a"], custom: "保留" }],
        },
      });
      return Response.json({
        capability: CANVAS_SOURCE_CAPABILITY,
        request_id: request.request_id,
        command_type: "frame.update",
        snapshot: nextSource,
        creation_receipt: null,
      });
    });
    const port = createCanvasSourceHttpPort("http://127.0.0.1:8000/api", {
      storage: memoryStorage(),
      fetcher,
    });

    const result = await port.write("chapter-a", snapshot, command, new AbortController().signal);

    const [input, init] = fetcher.mock.calls[0]!;
    const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
    expect(new URL(String(input)).pathname).toBe("/api/chapters/chapter-a/canvas-source");
    expect(init?.method).toBe("POST");
    expect(body).toMatchObject({
      capability: CANVAS_SOURCE_CAPABILITY,
      expected_series_id: "series-a",
      expected_version: snapshot.version,
      command,
    });
    expect(body.request_id).toMatch(/^[0-9a-f-]{36}$/u);
    expect(result.request_id).toBe(body.request_id);
    expect(result.snapshot.chapter.content?.[0]).toMatchObject({ text: "改过的镜头", custom: "保留" });
  });

  it("accepts the server-created frame ID and passes its opaque receipt only to explicit undo", async () => {
    const snapshot = frozenSnapshot();
    const createdId = "46e667ea-c192-4d6a-a096-be538d3a9762";
    const receiptToken = "opaque-server-creation-proof";
    const insertedPayload = sourcePayload({
      version: "1fc46fa06d43a463b49ac74197c03bd011d52e65bd47d5eb5f4d93e52049fa9c",
      chapter: {
        ...sourcePayload().chapter,
        content: [
          { text: "第一镜", storyboard: ["board-a"], custom: "保留" },
          { text: "新插入镜头", storyboard: [createdId], vendor: { keep: true } },
        ],
      },
      storyboard_assets: [
        ...sourcePayload().storyboard_assets,
        {
          id: createdId,
          series_id: "series-a",
          chapter_id: "chapter-a",
          frame_index: 1,
          name: "分镜 2",
          description: "新插入镜头",
          image_url: null,
          created_at: "2026-10-24T12:00:01",
          updated_at: "2026-10-24T12:00:01",
        },
      ],
    });
    const restoredPayload = sourcePayload({
      version: "d21d0f132c4daf8570c9e3123767c5d32e033c658fd4fb2e121cd7ba99e8568c",
    });
    const fetcher = vi.fn<typeof fetch>(async (_input, init) => {
      const body = JSON.parse(String(init?.body)) as { request_id: string; command: { type: string } };
      return body.command.type === "frame.insert"
        ? Response.json({
          capability: CANVAS_SOURCE_CAPABILITY,
          request_id: body.request_id,
          command_type: "frame.insert",
          snapshot: insertedPayload,
          creation_receipt: {
            token: receiptToken,
            kind: "frame",
            target: { id: createdId, storyboard_id: createdId, index: 1 },
          },
        })
        : Response.json({
          capability: CANVAS_SOURCE_CAPABILITY,
          request_id: body.request_id,
          command_type: "creation.undo",
          snapshot: restoredPayload,
          creation_receipt: null,
        });
    });
    const port = createCanvasSourceHttpPort("http://127.0.0.1:8000/api", {
      storage: memoryStorage(),
      fetcher,
    });

    const inserted = await port.write("chapter-a", snapshot, {
      type: "frame.insert",
      after: { index: 0, storyboard_id: "board-a" },
      frame: { text: "新插入镜头", vendor: { keep: true } },
    }, new AbortController().signal);
    expect(inserted.creation_receipt).toEqual({
      token: receiptToken,
      kind: "frame",
      target: { id: createdId, storyboard_id: createdId, index: 1 },
    });
    expect(inserted.snapshot.chapter.content?.[1]).toMatchObject({
      text: "新插入镜头",
      storyboard: [createdId],
    });

    await port.write("chapter-a", inserted.snapshot, {
      type: "creation.undo",
      receipt: inserted.creation_receipt!.token,
    }, new AbortController().signal);
    const bodies = fetcher.mock.calls.map(([, init]) => JSON.parse(String(init?.body)) as {
      expected_version: string;
      command: { type: string; receipt?: string };
    });
    expect(bodies).toHaveLength(2);
    expect(bodies[1]).toMatchObject({
      expected_version: inserted.snapshot.version,
      command: { type: "creation.undo", receipt: receiptToken },
    });
  });

  it("accepts the unique first storyboard ID when a frame retains extra references", async () => {
    const snapshot = {
      ...frozenSnapshot(),
      chapter: {
        ...frozenSnapshot().chapter,
        content: [{ text: "第一镜", storyboard: ["board-a", "legacy-reference"], videos: [] }],
      },
    } as CanvasSourceSnapshot;
    const next = sourcePayload({
      version: "2fc46fa06d43a463b49ac74197c03bd011d52e65bd47d5eb5f4d93e52049fa9c",
      chapter: {
        ...sourcePayload().chapter,
        content: [{ text: "第一镜", storyboard: ["board-a", "legacy-reference"], videos: ["pending"] }],
      },
    });
    const fetcher = vi.fn<typeof fetch>(async (_input, init) => {
      const body = JSON.parse(String(init?.body)) as { request_id: string };
      return Response.json({
        capability: CANVAS_SOURCE_CAPABILITY,
        request_id: body.request_id,
        command_type: "media.append",
        snapshot: next,
        creation_receipt: {
          token: "append-proof",
          kind: "video",
          target: { storyboard_id: "board-a", index: 0, slot: 0, value: "pending" },
        },
      });
    });
    const port = createCanvasSourceHttpPort("http://127.0.0.1:8000/api", {
      storage: memoryStorage(), fetcher,
    });

    await expect(port.write("chapter-a", snapshot, {
      type: "media.append", target: { index: 0, storyboard_id: "board-a" }, value: "pending",
    }, new AbortController().signal)).resolves.toMatchObject({ creation_receipt: { token: "append-proof" } });
  });

  it("accepts an exact null target even when an orphan storyboard row shares the index", async () => {
    const baseline = sourcePayload({
      chapter: {
        ...sourcePayload().chapter,
        content: [{ text: "无故事板镜头", storyboard: [], videos: [] }],
      },
      storyboard_assets: [{
        id: "orphan-board", series_id: "series-a", chapter_id: "chapter-a", frame_index: 0,
        name: "未绑定素材", description: null, image_url: null,
        created_at: "2026-10-24T12:00:00", updated_at: "2026-10-24T12:00:00",
      }],
    });
    const snapshot = {
      ...frozenSnapshot(),
      chapter: { ...frozenSnapshot().chapter, content: [{ text: "无故事板镜头", storyboard: [], videos: [] }] },
      storyboard_assets: baseline.storyboard_assets as CanvasSourceSnapshot["storyboard_assets"],
    } as CanvasSourceSnapshot;
    const next = sourcePayload({
      version: "3fc46fa06d43a463b49ac74197c03bd011d52e65bd47d5eb5f4d93e52049fa9c",
      chapter: { ...baseline.chapter, content: [{ text: "无故事板镜头", storyboard: [], videos: ["pending"] }] },
      storyboard_assets: baseline.storyboard_assets,
    });
    const fetcher = vi.fn<typeof fetch>(async (_input, init) => {
      const body = JSON.parse(String(init?.body)) as { request_id: string };
      return Response.json({
        capability: CANVAS_SOURCE_CAPABILITY,
        request_id: body.request_id,
        command_type: "media.append",
        snapshot: next,
        creation_receipt: {
          token: "append-orphan-proof",
          kind: "video",
          target: { storyboard_id: null, index: 0, slot: 0, value: "pending" },
        },
      });
    });
    const port = createCanvasSourceHttpPort("http://127.0.0.1:8000/api", {
      storage: memoryStorage(), fetcher,
    });

    await expect(port.write("chapter-a", snapshot, {
      type: "media.append", target: { index: 0, storyboard_id: null }, value: "pending",
    }, new AbortController().signal)).resolves.toMatchObject({ creation_receipt: { token: "append-orphan-proof" } });
  });

  it("does not accept an existing frame ID or a frame inserted at the wrong requested position as new creation proof", async () => {
    const snapshot = frozenSnapshot();
    const existingId = "board-a";
    const fetcher = vi.fn<typeof fetch>(async (_input, init) => {
      const body = JSON.parse(String(init?.body)) as { request_id: string };
      return Response.json({
        capability: CANVAS_SOURCE_CAPABILITY,
        request_id: body.request_id,
        command_type: "frame.insert",
        snapshot: sourcePayload(),
        creation_receipt: {
          token: "not-a-new-frame",
          kind: "frame",
          target: { id: existingId, storyboard_id: existingId, index: 0 },
        },
      });
    });
    const port = createCanvasSourceHttpPort("http://127.0.0.1:8000/api", {
      storage: memoryStorage(), fetcher,
    });

    await expect(port.write("chapter-a", snapshot, {
      type: "frame.insert", after: { index: 0, storyboard_id: "board-a" }, frame: { text: "新镜头" },
    }, new AbortController().signal)).rejects.toMatchObject({ kind: "unknown" });
    expect(fetcher).toHaveBeenCalledOnce();
  });

  it("does not treat an asset ID already present in the frozen snapshot as a new creation", async () => {
    const existing = {
      id: "character-existing",
      series_id: "series-a",
      name: "已有角色",
      description: null,
      image_url: null,
      aliases: null,
      canonical_key: null,
      gender: null,
      age: null,
      role: null,
      appearance: null,
      audio_url: null,
      voice_ref: null,
      created_at: "2026-10-24T12:00:00",
      updated_at: "2026-10-24T12:00:00",
    };
    const baseline = sourcePayload({ characters: [existing] });
    const snapshot = { ...frozenSnapshot(), characters: [existing] } as CanvasSourceSnapshot;
    const fetcher = vi.fn<typeof fetch>(async (_input, init) => {
      const body = JSON.parse(String(init?.body)) as { request_id: string };
      return Response.json({
        capability: CANVAS_SOURCE_CAPABILITY,
        request_id: body.request_id,
        command_type: "asset.create",
        snapshot: baseline,
        creation_receipt: {
          token: "existing-asset-is-not-new",
          kind: "character",
          target: { id: existing.id },
        },
      });
    });
    const port = createCanvasSourceHttpPort("http://127.0.0.1:8000/api", {
      storage: memoryStorage(), fetcher,
    });

    await expect(port.write("chapter-a", snapshot, {
      type: "asset.create", kind: "character", values: { name: "已有角色" },
    }, new AbortController().signal)).rejects.toMatchObject({ kind: "unknown" });
    expect(fetcher).toHaveBeenCalledOnce();
  });

  it("does not accept a new frame receipt at a position different from the frozen insertion intent", async () => {
    const snapshot = frozenSnapshot();
    const insertedId = "new-frame-at-the-wrong-position";
    const wrongPositionSnapshot = sourcePayload({
      version: "5fc46fa06d43a463b49ac74197c03bd011d52e65bd47d5eb5f4d93e52049fa9c",
      chapter: {
        ...sourcePayload().chapter,
        content: [
          { text: "错误位置的新镜头", storyboard: [insertedId] },
          { text: "第一镜", storyboard: ["board-a"], custom: "保留" },
        ],
      },
      storyboard_assets: [
        { ...sourcePayload().storyboard_assets[0], frame_index: 1 },
        {
          ...sourcePayload().storyboard_assets[0],
          id: insertedId,
          frame_index: 0,
          name: "错误位置的新镜头",
        },
      ],
    });
    const fetcher = vi.fn<typeof fetch>(async (_input, init) => {
      const body = JSON.parse(String(init?.body)) as { request_id: string };
      return Response.json({
        capability: CANVAS_SOURCE_CAPABILITY,
        request_id: body.request_id,
        command_type: "frame.insert",
        snapshot: wrongPositionSnapshot,
        creation_receipt: {
          token: "wrong-position-proof",
          kind: "frame",
          target: { id: insertedId, storyboard_id: insertedId, index: 0 },
        },
      });
    });
    const port = createCanvasSourceHttpPort("http://127.0.0.1:8000/api", {
      storage: memoryStorage(), fetcher,
    });

    await expect(port.write("chapter-a", snapshot, {
      type: "frame.insert",
      after: { index: 0, storyboard_id: "board-a" },
      frame: { text: "用户要求插在第一镜之后" },
    }, new AbortController().signal)).rejects.toMatchObject({ kind: "unknown" });
    expect(fetcher).toHaveBeenCalledOnce();
  });

  it("does not accept a correctly positioned frame receipt when the inserted frame differs from the frozen command", async () => {
    const snapshot = frozenSnapshot();
    const insertedId = "new-frame-with-unrequested-content";
    const changedContent = sourcePayload({
      version: "6fc46fa06d43a463b49ac74197c03bd011d52e65bd47d5eb5f4d93e52049fa9c",
      chapter: {
        ...sourcePayload().chapter,
        content: [
          { text: "第一镜", storyboard: ["board-a"], custom: "保留" },
          { text: "服务器返回了不同正文", storyboard: [insertedId], vendor: { keep: true } },
        ],
      },
      storyboard_assets: [
        { ...sourcePayload().storyboard_assets[0], frame_index: 0 },
        {
          ...sourcePayload().storyboard_assets[0],
          id: insertedId,
          frame_index: 1,
          name: "分镜 2",
          description: "服务器返回了不同正文",
        },
      ],
    });
    const fetcher = vi.fn<typeof fetch>(async (_input, init) => {
      const body = JSON.parse(String(init?.body)) as { request_id: string };
      return Response.json({
        capability: CANVAS_SOURCE_CAPABILITY,
        request_id: body.request_id,
        command_type: "frame.insert",
        snapshot: changedContent,
        creation_receipt: {
          token: "content-mismatch-is-not-creation-proof",
          kind: "frame",
          target: { id: insertedId, storyboard_id: insertedId, index: 1 },
        },
      });
    });
    const port = createCanvasSourceHttpPort("http://127.0.0.1:8000/api", {
      storage: memoryStorage(), fetcher,
    });

    await expect(port.write("chapter-a", snapshot, {
      type: "frame.insert",
      after: { index: 0, storyboard_id: "board-a" },
      frame: { text: "用户冻结的正文", vendor: { keep: true } },
    }, new AbortController().signal)).rejects.toMatchObject({ kind: "unknown" });
    expect(fetcher).toHaveBeenCalledOnce();
  });

  it("requires media.append receipts to prove the exact old video length and preserved prefix", async () => {
    const baseline = sourcePayload({
      chapter: {
        ...sourcePayload().chapter,
        content: [{ text: "第一镜", storyboard: ["board-a"], videos: ["same.mp4", "same.mp4"] }],
      },
    });
    const snapshot = {
      ...frozenSnapshot(),
      chapter: { ...frozenSnapshot().chapter, content: baseline.chapter.content },
    } as CanvasSourceSnapshot;
    const next = sourcePayload({
      version: "4fc46fa06d43a463b49ac74197c03bd011d52e65bd47d5eb5f4d93e52049fa9c",
      chapter: {
        ...sourcePayload().chapter,
        content: [{ text: "第一镜", storyboard: ["board-a"], videos: ["same.mp4", "same.mp4", "same.mp4"] }],
      },
    });
    const fetcher = vi.fn<typeof fetch>(async (_input, init) => {
      const body = JSON.parse(String(init?.body)) as { request_id: string };
      return Response.json({
        capability: CANVAS_SOURCE_CAPABILITY,
        request_id: body.request_id,
        command_type: "media.append",
        snapshot: next,
        creation_receipt: {
          token: "wrong-slot-proof",
          kind: "video",
          target: { storyboard_id: "board-a", index: 0, slot: 0, value: "same.mp4" },
        },
      });
    });
    const port = createCanvasSourceHttpPort("http://127.0.0.1:8000/api", {
      storage: memoryStorage(), fetcher,
    });

    await expect(port.write("chapter-a", snapshot, {
      type: "media.append", target: { index: 0, storyboard_id: "board-a" }, value: "same.mp4",
    }, new AbortController().signal)).rejects.toMatchObject({ kind: "unknown" });
    expect(fetcher).toHaveBeenCalledOnce();
  });

  it("never retries a write after an unknown transport result", async () => {
    const fetcher = vi.fn<typeof fetch>().mockRejectedValue(new TypeError("offline"));
    const port = createCanvasSourceHttpPort("http://127.0.0.1:8000/api", {
      storage: memoryStorage(),
      fetcher,
    });

    await expect(port.write("chapter-a", frozenSnapshot(), {
      type: "media.append",
      target: { index: 0, storyboard_id: "board-a" },
      value: "pending",
    }, new AbortController().signal)).rejects.toMatchObject({ kind: "unknown" });
    expect(fetcher).toHaveBeenCalledOnce();
  });

  it("does not fall back when the new source route is unavailable", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 404 }));
    const port = createCanvasSourceHttpPort("http://127.0.0.1:8000/api", {
      storage: memoryStorage(),
      fetcher,
    });

    await expect(port.read("chapter-a", new AbortController().signal))
      .rejects.toBeInstanceOf(CanvasSourceError);
    await expect(port.read("chapter-a", new AbortController().signal))
      .rejects.toMatchObject({ kind: "unsupported" });
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(fetcher.mock.calls.every(([input]) => new URL(String(input)).pathname.endsWith("/canvas-source"))).toBe(true);
  });
});
