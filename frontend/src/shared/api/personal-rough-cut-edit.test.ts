// @vitest-environment node
import { describe, expect, it, vi } from "vitest";
import { demoSeries } from "../../features/series/demoSeries";
import { getDemoChapterData } from "../../features/chapters/demoChapters";
import { InvalidResponseError } from "./contracts";
import {
  parsePersonalRoughCutSaveResponse,
  parsePersonalRoughCutUpdate,
  type PersonalRoughCutSnapshot,
  type PersonalRoughCutUpdate,
} from "./personalRoughCut";
import { createApiServices, createDemoServices } from "./services";
import { API_TOKEN_KEY, API_USER_KEY, MOCK_SESSION_KEY, MOCK_USER_KEY, type StorageLike } from "./storage";

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

function roughCutUpdate(
  expectedRevision: number,
  frames: readonly { asset_id: string; included: boolean }[],
): PersonalRoughCutUpdate {
  return { expected_revision: expectedRevision, frames };
}

function roughCutFrame(
  assetId: string,
  frameIndex: number,
  overrides: Partial<PersonalRoughCutSnapshot["frames"][number]> = {},
): PersonalRoughCutSnapshot["frames"][number] {
  return {
    asset_id: assetId,
    frame_index: frameIndex,
    text: "镜头文字",
    preview_url: null,
    missing_reason: "合成的服务端元数据",
    included: true,
    pending: false,
    ...overrides,
  };
}

function roughCutSnapshot(
  chapterId: string,
  revision: number,
  frames: PersonalRoughCutSnapshot["frames"],
): Record<string, unknown> {
  return {
    chapter_id: chapterId,
    revision,
    saved: true,
    frames,
    removed_asset_ids: [],
  };
}

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const signal = () => new AbortController().signal;

describe("personal rough-cut update contract", () => {
  it("accepts empty chapter updates, exact keys, raw whitespace IDs, and 36 Unicode code points", () => {
    const unicodeId = "😀".repeat(36);
    const parsed = parsePersonalRoughCutUpdate({
      expected_revision: 0,
      frames: [
        { asset_id: " ", included: false },
        { asset_id: unicodeId, included: true },
      ],
    });

    expect(parsed).toEqual({
      expected_revision: 0,
      frames: [
        { asset_id: " ", included: false },
        { asset_id: unicodeId, included: true },
      ],
    });
    expect(parsePersonalRoughCutUpdate({ expected_revision: 0, frames: [] }).frames).toEqual([]);
    expect(() => parsePersonalRoughCutUpdate({
      expected_revision: 0,
      frames: [{ asset_id: "😀".repeat(37), included: true }],
    })).toThrow(InvalidResponseError);
  });

  it.each([
    ["extra top-level key", { expected_revision: 1, frames: [], extra: true }],
    ["extra frame key", { expected_revision: 1, frames: [{ asset_id: "a", included: true, extra: 1 }] }],
    ["duplicate raw ID", { expected_revision: 1, frames: [{ asset_id: "a", included: true }, { asset_id: "a", included: false }] }],
    ["empty raw ID", { expected_revision: 1, frames: [{ asset_id: "", included: true }] }],
    ["non-boolean inclusion", { expected_revision: 1, frames: [{ asset_id: "a", included: 1 }] }],
    ["unsafe next revision", { expected_revision: Number.MAX_SAFE_INTEGER, frames: [] }],
    ["more than 500 frames", { expected_revision: 1, frames: Array.from({ length: 501 }, (_, index) => ({ asset_id: "frame-" + index, included: true })) }],
  ])("rejects %s", (_label, value) => {
    expect(() => parsePersonalRoughCutUpdate(value)).toThrow(InvalidResponseError);
  });

  it("accepts server-maintained metadata while requiring the submitted complete order and inclusion flags", () => {
    const update = roughCutUpdate(8, [
      { asset_id: "frame-b", included: false },
      { asset_id: "frame-a", included: true },
    ]);
    const payload = roughCutSnapshot("chapter-α", 9, [
      roughCutFrame("frame-b", 1, { text: "由服务端维护的 B 文本", included: false }),
      roughCutFrame("frame-a", 0, { preview_url: null, missing_reason: "当前没有视频", included: true }),
    ]);

    expect(parsePersonalRoughCutSaveResponse(payload, "chapter-α", update)).toMatchObject({
      chapter_id: "chapter-α",
      revision: 9,
      saved: true,
      frames: [
        { asset_id: "frame-b", frame_index: 1, included: false, pending: false },
        { asset_id: "frame-a", frame_index: 0, included: true, pending: false },
      ],
      removed_asset_ids: [],
    });
    expect(() => parsePersonalRoughCutSaveResponse(
      roughCutSnapshot("chapter-α", 9, [
        roughCutFrame("frame-a", 0),
        roughCutFrame("frame-b", 1, { included: false }),
      ]),
      "chapter-α",
      update,
    )).toThrow(InvalidResponseError);
    expect(() => parsePersonalRoughCutSaveResponse(
      roughCutSnapshot("chapter-α", 9, [
        roughCutFrame("frame-b", 2, { included: false }),
        roughCutFrame("frame-a", 0),
      ]),
      "chapter-α",
      update,
    )).toThrow(InvalidResponseError);
  });
});

describe("personal rough-cut demo and API services", () => {
  it("saves a full reordered source set atomically, preserves metadata by ID, and keeps service instances isolated", async () => {
    const storage = new MemoryStorage();
    const services = createDemoServices(storage);
    const otherServices = createDemoServices(storage);
    await services.login({ username: "demo", password: "demo123" }, signal());

    const seriesId = demoSeries[0]!.id;
    const data = getDemoChapterData(seriesId);
    if (data === null) {
      throw new Error("Expected demo chapter data.");
    }
    const chapter = data.chapters.find((candidate) => candidate.content !== null && candidate.content.length >= 2);
    if (chapter === undefined) {
      throw new Error("Expected a demo chapter with at least two storyboard frames.");
    }
    const before = await services.getPersonalRoughCut(chapter.id, signal());
    expect(before.frames.length).toBeGreaterThanOrEqual(2);

    const sourceById = new Map(before.frames.map((frame) => [frame.asset_id, frame]));
    const submitted = before.frames.slice().reverse().map((frame, index) => ({
      asset_id: frame.asset_id,
      included: index === 0,
    }));
    const update = roughCutUpdate(before.revision, submitted);
    const saved = await services.savePersonalRoughCut!(chapter.id, update, signal());

    expect(saved.revision).toBe(before.revision + 1);
    expect(saved.saved).toBe(true);
    expect(saved.removed_asset_ids).toEqual([]);
    expect(saved.frames.map(({ asset_id }) => asset_id)).toEqual(submitted.map(({ asset_id }) => asset_id));
    for (let index = 0; index < saved.frames.length; index += 1) {
      const actual = saved.frames[index]!;
      const prior = sourceById.get(actual.asset_id)!;
      expect(actual).toEqual({
        ...prior,
        included: submitted[index]!.included,
        pending: false,
      });
    }

    const reread = await services.getPersonalRoughCut(chapter.id, signal());
    const separateService = await otherServices.getPersonalRoughCut(chapter.id, signal());
    expect(reread).toEqual(saved);
    expect(separateService.revision).toBe(before.revision);
    expect(separateService.frames.map(({ asset_id }) => asset_id)).toEqual(before.frames.map(({ asset_id }) => asset_id));
    await expect(services.savePersonalRoughCut!(chapter.id, update, signal()))
      .rejects.toMatchObject({ status: 409 });
    expect(storage.getItem(MOCK_SESSION_KEY)).toBe("demo-session");
    expect(storage.getItem(MOCK_USER_KEY)).not.toBeNull();
    expect(storage.getItem(API_TOKEN_KEY)).toBeNull();
    expect(storage.getItem(API_USER_KEY)).toBeNull();
  });


  it("sends an exact current-Bearer PUT once and accepts valid server metadata maintenance", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "current-rough-cut-token");
    const chapterId = "chapter/雨夜 ?#";
    const update = roughCutUpdate(4, [
      { asset_id: "frame-b", included: true },
      { asset_id: "frame-a", included: false },
    ]);
    const payload = roughCutSnapshot(chapterId, 5, [
      roughCutFrame("frame-b", 1, { text: "被服务端调整的文本", preview_url: null, included: true }),
      roughCutFrame("frame-a", 0, { missing_reason: "服务端维护说明", included: false }),
    ]);
    const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => jsonResponse(payload));
    const services = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher });

    const saved = await services.savePersonalRoughCut!(chapterId, update, signal());

    expect(saved.chapter_id).toBe(chapterId);
    const [input, init] = fetcher.mock.calls[0] ?? [];
    const requestUrl = new URL(String(input));
    expect(requestUrl.pathname).toBe("/api/chapters/chapter%2F%E9%9B%A8%E5%A4%9C%20%3F%23/rough-cut");
    expect(requestUrl.search).toBe("");
    expect(requestUrl.hash).toBe("");
    expect(init?.method).toBe("PUT");
    expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer current-rough-cut-token");
    expect(JSON.parse(String(init?.body))).toEqual({
      expected_revision: 4,
      frames: [
        { asset_id: "frame-b", included: true },
        { asset_id: "frame-a", included: false },
      ],
    });
    expect(saved.frames.map(({ frame_index }) => frame_index)).toEqual([1, 0]);
  });

  it("validates update input before fetch and drops a completed old-token response", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "old-token");
    let resolveResponse!: (response: Response) => void;
    const fetcher = vi.fn(() => new Promise<Response>((resolve) => { resolveResponse = resolve; }));
    const services = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher });

    await expect(services.savePersonalRoughCut!(
      "chapter-one",
      { expected_revision: 0, frames: [{ asset_id: "x", included: true }, { asset_id: "x", included: false }] },
      signal(),
    )).rejects.toBeInstanceOf(InvalidResponseError);
    expect(fetcher).not.toHaveBeenCalled();

    const pending = services.savePersonalRoughCut!(
      "chapter-one",
      roughCutUpdate(0, [{ asset_id: "frame-one", included: true }]),
      signal(),
    );
    storage.setItem(API_TOKEN_KEY, "new-token");
    storage.setItem(API_USER_KEY, "new-user");
    resolveResponse(jsonResponse(roughCutSnapshot("chapter-one", 1, [roughCutFrame("frame-one", 0)])));
    await expect(pending).rejects.toMatchObject({ kind: "aborted" });
    expect(storage.getItem(API_TOKEN_KEY)).toBe("new-token");
  });
});
