// @vitest-environment node
import { describe, expect, it, vi } from "vitest";
import type { Chapter, StoryboardFrame } from "../../../shared/api/contracts";
import type { PersonalRoughCutSnapshot } from "../../../shared/api/personalRoughCut";
import { captureMediaExportSession } from "../../../shared/api/mediaExportSession";
import {
  buildChapterMediaExportQueue,
  buildSavedRoughCutMediaExportQueue,
  createFailedMediaExportRetryQueue,
  startMediaExport,
} from "./queue";
import { MediaExportSinkError } from "../../../shared/api/mediaExport";
import type { MediaExportQueue, MediaExportQueueItem } from "../../../shared/api/mediaExport";

const apiBaseUrl = "http://127.0.0.1:4175/api";

function createTransport(fetcher: typeof fetch, isCurrent: () => boolean = () => true) {
  return captureMediaExportSession({
    apiBaseUrl,
    token: "signing-token",
    fetcher,
    isCurrent,
  });
}

function chapter(content: StoryboardFrame[]): Chapter {
  return {
    id: "chapter-a",
    series_id: "series-a",
    title: "雨夜/章节",
    content,
    order: 0,
    created_at: "",
    updated_at: "",
    lock: null,
  };
}

function roughCut(frames: PersonalRoughCutSnapshot["frames"]): PersonalRoughCutSnapshot {
  return {
    chapter_id: "chapter-a",
    revision: 7,
    saved: true,
    frames,
    removed_asset_ids: [],
  };
}

function readyItem(index: number, url = `https://cdn.example.invalid/${index}.mp4`): MediaExportQueueItem {
  return {
    id: `run-a:item-${index}`,
    sourceKind: "chapter",
    chapterId: "chapter-a",
    sourceIndex: index,
    frameIndex: index,
    assetId: `asset-${index}`,
    sourceUrl: url,
    filename: `${String(index + 1).padStart(3, "0")}_章节_剧集.mp4`,
    eligibility: "ready",
    state: "queued",
    message: null,
  };
}

describe("media export queue projection", () => {
  it("freezes selected chapter positions and reports exclusions, missing video, and duplicate IDs separately", () => {
    const snapshot = chapter([
      { text: "first", storyboard: ["dup"], preview: "https://cdn.example.invalid/a.mp4" },
      { text: "second", storyboard: ["dup"], preview: "https://cdn.example.invalid/b.mp4" },
      { text: "third", storyboard: ["third"] },
      { text: "fourth", storyboard: ["fourth"], preview: "https://cdn.example.invalid/d.mp4" },
    ]);

    const queue = buildChapterMediaExportQueue({
      runId: "run-a",
      chapter: snapshot,
      seriesName: "剧集?",
      selectedFrameIndexes: [1, 0, 2],
      sourceRevision: "chapter-snapshot-2",
    });

    expect(queue.sourceRevision).toBe("chapter-snapshot-2");
    expect(queue.items.map((item) => item.frameIndex)).toEqual([0, 1, 2, 3]);
    expect(queue.items.map((item) => item.eligibility)).toEqual(["duplicate", "duplicate", "missing", "excluded"]);
    expect(queue.items[0]?.sourceUrl).toBe("https://cdn.example.invalid/a.mp4");
    expect(queue.items[0]?.filename).toBe("1_雨夜_章节_剧集.mp4");
  });

  it("uses only a saved rough-cut snapshot, preserves its order, and excludes pending/excluded/duplicate rows", () => {
    const snapshot = roughCut([
      { asset_id: "video-a", frame_index: 2, text: "A", preview_url: "https://cdn.example.invalid/a.mp4", missing_reason: null, included: true, pending: false },
      { asset_id: "excluded", frame_index: 0, text: "B", preview_url: "https://cdn.example.invalid/b.mp4", missing_reason: null, included: false, pending: false },
      { asset_id: "pending", frame_index: 1, text: "C", preview_url: "https://cdn.example.invalid/c.mp4", missing_reason: null, included: true, pending: true },
      { asset_id: "video-a", frame_index: 3, text: "D", preview_url: "https://cdn.example.invalid/d.mp4", missing_reason: null, included: true, pending: false },
      { asset_id: "missing", frame_index: 4, text: "E", preview_url: null, missing_reason: "当前没有视频", included: true, pending: false },
    ]);

    const queue = buildSavedRoughCutMediaExportQueue({
      runId: "run-b",
      chapter: chapter([]),
      seriesName: "剧集",
      snapshot,
    });

    expect(queue.sourceRevision).toBe(7);
    expect(queue.items.map((item) => item.sourceIndex)).toEqual([0, 1, 2, 3, 4]);
    expect(queue.items.map((item) => item.eligibility)).toEqual([
      "duplicate", "excluded", "pending", "duplicate", "missing",
    ]);
    expect(queue.items[4]?.filename).toMatch(/^粗剪001_分镜005_/u);
    expect(() => buildSavedRoughCutMediaExportQueue({
      runId: "run-c",
      chapter: chapter([]),
      seriesName: "剧集",
      snapshot: { ...snapshot, saved: false },
    })).toThrow();
  });

  it("keeps excluded and pending rough-cut rows from consuming exported filenames", () => {
    const snapshot = roughCut([
      { asset_id: "excluded", frame_index: 0, text: "未纳入项", preview_url: "https://cdn.example.invalid/excluded.mp4", missing_reason: null, included: false, pending: false },
      { asset_id: "ready", frame_index: 0, text: "纳入项", preview_url: "https://cdn.example.invalid/ready.mp4", missing_reason: null, included: true, pending: false },
    ]);

    const queue = buildSavedRoughCutMediaExportQueue({
      runId: "run-filename",
      chapter: chapter([]),
      seriesName: "剧集",
      snapshot,
    });

    expect(queue.items[0]?.eligibility).toBe("excluded");
    expect(queue.items[1]?.eligibility).toBe("ready");
    expect(queue.items[1]?.filename).toBe("粗剪001_分镜001_雨夜_章节_剧集.mp4");
  });

  it("keeps same-URL queue items as separate ordered downloads with distinct names", async () => {
    const items = [readyItem(0, "https://cdn.example.invalid/shared.mp4"), readyItem(1, "https://cdn.example.invalid/shared.mp4")];
    const queue = {
      runId: "run-a",
      sourceKind: "chapter" as const,
      chapterId: "chapter-a",
      seriesId: "series-a",
      chapterName: "章节",
      seriesName: "剧集",
      sourceRevision: "v1",
      items,
    };
    const fetcher = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/api/sign-download-urls")) {
        const body = JSON.parse(String(init?.body)) as { items: Array<{ url: string; filename: string }> };
        return Response.json({ items: body.items.map((item) => ({ ...item, signed: false })) });
      }
      const response = new Response("clip", { headers: { "Content-Type": "video/mp4" } });
      Object.defineProperty(response, "url", { value: url });
      return response;
    });
    const saved: string[] = [];

    const run = startMediaExport(queue, {
      transport: createTransport(fetcher),
      sink: { async save(item) { saved.push(item.filename ?? ""); return { kind: "saved" }; } },
    });
    const result = await run.done;

    expect(result.items.map((item) => item.state)).toEqual(["saved", "saved"]);
    expect(saved).toEqual([items[0]?.filename, items[1]?.filename]);
    expect(fetcher.mock.calls.filter(([input]) => String(input).includes("shared.mp4"))).toHaveLength(2);
  });

  it("emits a persistence receipt only for an actual saved sink result", async () => {
    const items = [readyItem(0), readyItem(1), readyItem(2), readyItem(3)];
    const queue: MediaExportQueue = {
      runId: "run-receipts",
      sourceKind: "chapter",
      chapterId: "chapter-a",
      seriesId: "series-a",
      chapterName: "章节",
      seriesName: "剧集",
      sourceRevision: "chapter-4",
      items,
    };
    const fetcher = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/api/sign-download-urls")) {
        const body = JSON.parse(String(init?.body)) as { items: Array<{ url: string; filename: string }> };
        return Response.json({ items: body.items.map((item) => ({ ...item, signed: false })) });
      }
      const response = new Response("clip", { headers: { "Content-Type": "video/mp4" } });
      Object.defineProperty(response, "url", { value: url });
      return response;
    });
    const onSaved = vi.fn((_item: MediaExportQueueItem) => {
      throw new Error("local storage write failed");
    });
    const result = await startMediaExport(queue, {
      transport: createTransport(fetcher),
      sink: {
        async save(item) {
          if (item.assetId === "asset-1") return { kind: "download-started" };
          if (item.assetId === "asset-2") {
            throw new MediaExportSinkError("unknown", "close", "close unknown");
          }
          if (item.assetId === "asset-3") {
            throw new MediaExportSinkError("failed", "write", "write failed");
          }
          return { kind: "saved" };
        },
      },
      onSaved,
    }).done;

    expect(result.items.map((item) => item.state)).toEqual([
      "saved", "download-started", "unknown", "failed",
    ]);
    expect(onSaved).toHaveBeenCalledOnce();
    expect(onSaved.mock.calls[0]?.[0]).toMatchObject({ assetId: "asset-0", state: "saved" });
  });

  it("marks a post-close uncertainty unknown and never repeats its download", async () => {
    const queue = {
      runId: "run-a",
      sourceKind: "chapter" as const,
      chapterId: "chapter-a",
      seriesId: "series-a",
      chapterName: "章节",
      seriesName: "剧集",
      sourceRevision: "v1",
      items: [readyItem(0)],
    };
    const fetcher = vi.fn<typeof fetch>(async (input, init) => {
      if (String(input).endsWith("/api/sign-download-urls")) {
        const body = JSON.parse(String(init?.body)) as { items: Array<{ url: string; filename: string }> };
        return Response.json({ items: body.items.map((item) => ({ ...item, signed: false })) });
      }
      const response = new Response("clip", { headers: { "Content-Type": "video/mp4" } });
      Object.defineProperty(response, "url", { value: String(input) });
      return response;
    });
    const save = vi.fn(async () => {
      throw new MediaExportSinkError("unknown", "close", "close rejected");
    });

    const result = await startMediaExport(queue, {
      transport: createTransport(fetcher),
      sink: { save },
    }).done;

    expect(result.items[0]?.state).toBe("unknown");
    expect(save).toHaveBeenCalledOnce();
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("retries only explicitly selected known failures and preserves the rest of the run", async () => {
    const previous: MediaExportQueue = {
      runId: "run-a",
      outputDirectoryId: "run-a",
      sourceKind: "chapter",
      chapterId: "chapter-a",
      seriesId: "series-a",
      chapterName: "章节",
      seriesName: "剧集",
      sourceRevision: "snapshot-3",
      items: [
        { ...readyItem(0), state: "saved", filename: "001_章节_剧集.mp4" },
        { ...readyItem(1), state: "download-started", filename: "002_章节_剧集.webm" },
        {
          ...readyItem(2),
          state: "failed",
          filename: "003_章节_剧集.webm",
          message: "写入失败",
          failureStage: "write",
        },
        {
          ...readyItem(3),
          state: "unknown",
          filename: "004_章节_剧集.mp4",
          message: "关闭未知",
          failureStage: "close",
        },
        { ...readyItem(4), state: "stopped", filename: "005_章节_剧集.mp4" },
      ],
    };
    const fetcher = vi.fn<typeof fetch>(async (input, init) => {
      if (String(input).endsWith("/api/sign-download-urls")) {
        const body = JSON.parse(String(init?.body)) as { items: Array<{ url: string; filename: string }> };
        return Response.json({ items: body.items.map((item) => ({ ...item, signed: false })) });
      }
      const response = new Response("clip", { headers: { "Content-Type": "video/webm" } });
      Object.defineProperty(response, "url", { value: String(input) });
      return response;
    });
    const savedNames: string[] = [];
    const options = {
      transport: createTransport(fetcher),
      sink: {
        async save(item: MediaExportQueueItem) {
          savedNames.push(item.filename);
          return { kind: "saved" as const };
        },
      },
    };

    const directRestart = await startMediaExport(previous, options).done;
    expect(directRestart.items.map((item) => item.state)).toEqual([
      "saved", "download-started", "failed", "unknown", "stopped",
    ]);
    expect(directRestart.items[2]?.failureStage).toBe("write");
    expect(directRestart.items[3]?.failureStage).toBe("close");
    expect(fetcher).not.toHaveBeenCalled();
    const staleRestart = await startMediaExport(previous, {
      ...options,
      transport: createTransport(fetcher, () => false),
    }).done;
    expect(staleRestart.items.map((item) => item.state)).toEqual([
      "saved", "download-started", "failed", "unknown", "stopped",
    ]);
    expect(staleRestart.items[3]?.failureStage).toBe("close");
    expect(fetcher).not.toHaveBeenCalled();

    const failedItem = previous.items[2];
    const retry = createFailedMediaExportRetryQueue(previous, {
      itemIds: [failedItem!.id],
      outputDirectoryId: "retry-target-a",
      confirmedNewTarget: true,
    });
    expect(retry.runId).toBe(previous.runId);
    expect(retry.outputDirectoryId).toBe("retry-target-a");
    expect(retry.items.map((item) => item.id)).toEqual(previous.items.map((item) => item.id));
    expect(retry.items.map((item) => item.filename)).toEqual(previous.items.map((item) => item.filename));
    expect(retry.items.map((item) => item.state)).toEqual([
      "saved", "download-started", "queued", "unknown", "stopped",
    ]);
    expect(retry.items[2]?.failureStage).toBeUndefined();
    expect(retry.items[3]?.failureStage).toBe("close");

    const retried = await startMediaExport(retry, options).done;
    expect(retried.items.map((item) => item.state)).toEqual([
      "saved", "download-started", "saved", "unknown", "stopped",
    ]);
    expect(retried.items[3]?.failureStage).toBe("close");
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(JSON.parse(String(fetcher.mock.calls[0]?.[1]?.body)).items).toEqual([{
      url: "https://cdn.example.invalid/2.mp4",
      filename: "003_章节_剧集.webm",
    }]);
    expect(savedNames).toEqual(["003_章节_剧集.webm"]);
    expect(() => createFailedMediaExportRetryQueue(previous, {
      itemIds: [previous.items[3]!.id],
      outputDirectoryId: "retry-target-b",
      confirmedNewTarget: true,
    })).toThrow();
  });

  it("records the corrected MIME target before a sink can fail", async () => {
    const queue: MediaExportQueue = {
      runId: "run-a",
      sourceKind: "chapter",
      chapterId: "chapter-a",
      seriesId: "series-a",
      chapterName: "章节",
      seriesName: "剧集",
      sourceRevision: "v1",
      items: [readyItem(0, "https://cdn.example.invalid/clip.mp4")],
    };
    const fetcher = vi.fn<typeof fetch>(async (input, init) => {
      if (String(input).endsWith("/api/sign-download-urls")) {
        const body = JSON.parse(String(init?.body)) as { items: Array<{ url: string; filename: string }> };
        return Response.json({ items: body.items.map((item) => ({ ...item, signed: false })) });
      }
      const response = new Response("clip", { headers: { "Content-Type": "video/webm" } });
      Object.defineProperty(response, "url", { value: String(input) });
      return response;
    });
    let attemptedFilename = "";
    const result = await startMediaExport(queue, {
      transport: createTransport(fetcher),
      sink: {
        async save(item) {
          attemptedFilename = item.filename;
          throw new MediaExportSinkError("unknown", "close", "close failed");
        },
      },
    }).done;

    expect(attemptedFilename).toMatch(/\.webm$/u);
    expect(result.items[0]).toMatchObject({
      state: "unknown",
      filename: attemptedFilename,
      failureStage: "close",
    });
  });

  it("only reports current application 401s and suppresses old-scope callbacks", async () => {
    const queueFor = (item: MediaExportQueueItem): MediaExportQueue => ({
      runId: "run-a",
      sourceKind: "chapter",
      chapterId: "chapter-a",
      seriesId: "series-a",
      chapterName: "章节",
      seriesName: "剧集",
      sourceRevision: "v1",
      items: [item],
    });
    const authQueue = queueFor(readyItem(0));
    const unauthorized = vi.fn();
    const resolver401 = vi.fn<typeof fetch>(async () => new Response("unauthorized", { status: 401 }));
    const resolverResult = await startMediaExport(authQueue, {
      transport: createTransport(resolver401),
      sink: { async save() { return { kind: "saved" as const }; } },
      onUnauthorized: unauthorized,
    }).done;
    expect(unauthorized).toHaveBeenCalledOnce();
    expect(unauthorized.mock.calls[0]?.[0]).toMatchObject({ status: 401, source: "application" });
    expect(resolverResult.items[0]?.state).toBe("failed");

    const protectedUrl = "http://127.0.0.1:4175/uploads/protected.mp4";
    const protectedQueue = queueFor({ ...readyItem(0, protectedUrl), sourceUrl: protectedUrl });
    const protectedUnauthorized = vi.fn();
    const protectedFetcher = vi.fn<typeof fetch>(async (input, init) => {
      if (String(input).endsWith("/api/sign-download-urls")) {
        const body = JSON.parse(String(init?.body)) as { items: Array<{ url: string; filename: string }> };
        return Response.json({ items: body.items.map((item) => ({ ...item, signed: false })) });
      }
      const response = new Response("expired", { status: 401 });
      Object.defineProperty(response, "url", { value: String(input) });
      return response;
    });
    const protectedResult = await startMediaExport(protectedQueue, {
      transport: createTransport(protectedFetcher),
      sink: { async save() { return { kind: "saved" as const }; } },
      onUnauthorized: protectedUnauthorized,
    }).done;
    expect(protectedUnauthorized).toHaveBeenCalledOnce();
    expect(protectedUnauthorized.mock.calls[0]?.[0]).toMatchObject({ status: 401, source: "application" });
    expect(protectedResult.items[0]?.state).toBe("failed");

    const providerQueue = {
      ...authQueue,
      items: [
        readyItem(0, "https://provider.example.invalid/first.mp4"),
        readyItem(1, "https://provider.example.invalid/second.mp4"),
      ],
    };
    const providerUnauthorized = vi.fn();
    const providerFetcher = vi.fn<typeof fetch>(async (input, init) => {
      if (String(input).endsWith("/api/sign-download-urls")) {
        const body = JSON.parse(String(init?.body)) as { items: Array<{ url: string; filename: string }> };
        return Response.json({ items: body.items.map((item) => ({ ...item, signed: false })) });
      }
      if (String(input).includes("first.mp4")) {
        const response = new Response("provider unauthorized", { status: 401 });
        Object.defineProperty(response, "url", { value: String(input) });
        return response;
      }
      const response = new Response("clip", { headers: { "Content-Type": "video/mp4" } });
      Object.defineProperty(response, "url", { value: String(input) });
      return response;
    });
    const providerResult = await startMediaExport(providerQueue, {
      transport: createTransport(providerFetcher),
      sink: { async save() { return { kind: "saved" as const }; } },
      onUnauthorized: providerUnauthorized,
    }).done;
    expect(providerUnauthorized).not.toHaveBeenCalled();
    expect(providerResult.items.map((item) => item.state)).toEqual(["failed", "saved"]);

    let current = true;
    let releaseProtected: ((response: Response) => void) | undefined;
    const staleUnauthorized = vi.fn();
    const staleOnItem = vi.fn();
    const staleFetcher = vi.fn<typeof fetch>(async (input, init) => {
      if (String(input).endsWith("/api/sign-download-urls")) {
        const body = JSON.parse(String(init?.body)) as { items: Array<{ url: string; filename: string }> };
        return Response.json({ items: body.items.map((item) => ({ ...item, signed: false })) });
      }
      return new Promise<Response>((resolve) => {
        releaseProtected = resolve;
      });
    });
    const staleRun = startMediaExport(protectedQueue, {
      transport: createTransport(staleFetcher, () => current),
      sink: { async save() { return { kind: "saved" as const }; } },
      onUnauthorized: staleUnauthorized,
      onItem: staleOnItem,
    });
    await vi.waitFor(() => expect(releaseProtected).toBeDefined());
    current = false;
    staleOnItem.mockClear();
    const staleResponse = new Response("late unauthorized", { status: 401 });
    Object.defineProperty(staleResponse, "url", { value: protectedUrl });
    releaseProtected?.(staleResponse);
    const staleResult = await staleRun.done;
    expect(staleUnauthorized).not.toHaveBeenCalled();
    expect(staleOnItem).not.toHaveBeenCalled();
    expect(staleResult.items[0]?.state).toBe("stopped");
    expect(staleFetcher).toHaveBeenCalledTimes(2);
  });

  it("stops after the current item without starting the next item", async () => {
    const queue = {
      runId: "run-a",
      sourceKind: "chapter" as const,
      chapterId: "chapter-a",
      seriesId: "series-a",
      chapterName: "章节",
      seriesName: "剧集",
      sourceRevision: "v1",
      items: [readyItem(0), readyItem(1)],
    };
    let releaseRead: (() => void) | undefined;
    const fetcher = vi.fn<typeof fetch>(async (input, init) => {
      if (String(input).endsWith("/api/sign-download-urls")) {
        const body = JSON.parse(String(init?.body)) as { items: Array<{ url: string; filename: string }> };
        return Response.json({ items: body.items.map((item) => ({ ...item, signed: false })) });
      }
      await new Promise<void>((resolve) => { releaseRead = resolve; });
      const response = new Response("clip", { headers: { "Content-Type": "video/mp4" } });
      Object.defineProperty(response, "url", { value: String(input) });
      return response;
    });
    const saved: string[] = [];
    const run = startMediaExport(queue, {
      transport: createTransport(fetcher),
      sink: { async save(item) { saved.push(item.filename ?? ""); return { kind: "saved" }; } },
    });
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    run.stopAfterCurrent();
    releaseRead?.();

    const result = await run.done;
    expect(saved).toHaveLength(1);
    expect(result.items.map((item) => item.state)).toEqual(["saved", "stopped"]);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("does not notify an old scope or start another item after a late sink result", async () => {
    const queue: MediaExportQueue = {
      runId: "run-a",
      sourceKind: "chapter",
      chapterId: "chapter-a",
      seriesId: "series-a",
      chapterName: "章节",
      seriesName: "剧集",
      sourceRevision: "v1",
      items: [readyItem(0), readyItem(1)],
    };
    const fetcher = vi.fn<typeof fetch>(async (input, init) => {
      if (String(input).endsWith("/api/sign-download-urls")) {
        const body = JSON.parse(String(init?.body)) as { items: Array<{ url: string; filename: string }> };
        return Response.json({ items: body.items.map((item) => ({ ...item, signed: false })) });
      }
      const response = new Response("clip", { headers: { "Content-Type": "video/mp4" } });
      Object.defineProperty(response, "url", { value: String(input) });
      return response;
    });
    let current = true;
    let releaseSave: ((result: { kind: "saved" }) => void) | undefined;
    const save = vi.fn(() => new Promise<{ kind: "saved" }>((resolve) => {
      releaseSave = resolve;
    }));
    const onItem = vi.fn();
    const onSaved = vi.fn((_item: MediaExportQueueItem) => undefined);
    const run = startMediaExport(queue, {
      transport: createTransport(fetcher, () => current),
      sink: { save },
      onItem,
      onSaved,
    });
    await vi.waitFor(() => expect(save).toHaveBeenCalledOnce());
    current = false;
    onItem.mockClear();
    releaseSave?.({ kind: "saved" });

    const result = await run.done;
    expect(result.items.map((item) => item.state)).toEqual(["saved", "stopped"]);
    expect(save).toHaveBeenCalledOnce();
    expect(fetcher.mock.calls.map(([input]) => String(input))).toEqual([
      "http://127.0.0.1:4175/api/sign-download-urls",
      "https://cdn.example.invalid/0.mp4",
    ]);
    expect(onItem).not.toHaveBeenCalled();
    expect(onSaved).toHaveBeenCalledOnce();
    expect(onSaved.mock.calls[0]?.[0]).toMatchObject({ assetId: "asset-0", state: "saved" });
  });
});
