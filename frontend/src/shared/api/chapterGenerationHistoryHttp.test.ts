import { describe, expect, it, vi } from "vitest";
import {
  API_TOKEN_KEY,
  API_USER_KEY,
  type StorageLike,
} from "./storage";
import {
  ChapterGenerationHistoryError,
  type ChapterGenerationHistoryContext,
  type ChapterGenerationHistoryTargetKind,
  type ChapterGenerationHistoryMode,
} from "./chapterGenerationHistory";
import { createChapterGenerationHistoryHttpPort } from "./chapterGenerationHistoryHttp";

const apiBaseUrl = "http://127.0.0.1:4175/api";
const actorId = "history-user-1200";
const seriesId = "history-series-1200";
const chapterId = "history-chapter-1200";
const storyboardAssetId = "history-storyboard-1200";
const mappingDigest = "a".repeat(64);
const targetDigest = "b".repeat(64);
const messageDigest = "c".repeat(64);

class MemoryStorage implements StorageLike {
  private readonly values = new Map<string, string>();
  getItem(key: string): string | null { return this.values.get(key) ?? null; }
  setItem(key: string, value: string): void { this.values.set(key, value); }
  removeItem(key: string): void { this.values.delete(key); }
}

function createStorage(): MemoryStorage {
  const storage = new MemoryStorage();
  storage.setItem(API_TOKEN_KEY, "history-access-token");
  storage.setItem(API_USER_KEY, JSON.stringify({ id: actorId }));
  return storage;
}

function createContext(
  targetKind: ChapterGenerationHistoryTargetKind = "frame-preview",
  mode: ChapterGenerationHistoryMode = "image",
): ChapterGenerationHistoryContext {
  return {
    actorId,
    seriesId,
    chapterId,
    targetKind,
    storyboardAssetId,
    frameIndex: 0,
    mode,
    isContextCurrent: () => true,
  };
}

function createMessage(context: ChapterGenerationHistoryContext, mediaUrl?: string) {
  const isFrame = context.targetKind === "frame-preview";
  return {
    id: "history-message-1200",
    chapter_id: chapterId,
    frame_index: isFrame ? 0 : null,
    asset_type: isFrame ? null : "storyboard",
    asset_id: isFrame ? null : storyboardAssetId,
    chat_mode: context.mode,
    role: "assistant",
    content: mediaUrl ?? (context.mode === "video"
      ? "https://cdn.example.test/history/result.mp4?signature=kept"
      : "https://cdn.example.test/history/result.png?signature=kept"),
    model_name: "fake-history-model",
    created_at: "2026-10-28T10:00:00Z",
    message_digest: messageDigest,
  };
}

function createProof(mediaUrl: string | null = null) {
  return { mapping_digest: mappingDigest, target_digest: targetDigest, media_url: mediaUrl };
}

function createSnapshot(
  context: ChapterGenerationHistoryContext,
  mediaUrl: string | null = null,
  messages = [createMessage(context)],
) {
  return {
    protocol_version: 1,
    series_id: seriesId,
    chapter_id: chapterId,
    target_kind: context.targetKind,
    storyboard_asset_id: context.storyboardAssetId,
    frame_index: 0,
    chat_mode: context.mode,
    writable: true,
    read_only_reason: null,
    target_proof: createProof(mediaUrl),
    messages,
  };
}

function response(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function createPort(fetcher: typeof fetch, storage = createStorage()) {
  return createChapterGenerationHistoryHttpPort(apiBaseUrl, {
    storage,
    fetcher,
    timeoutMs: 1_000,
  });
}

describe("createChapterGenerationHistoryHttpPort", () => {
  it.each([
    ["frame-preview", "/chat-messages/"],
    ["storyboard-image", "/asset-chat-messages/"],
  ] as const)("transfers, updates, and deletes through the %s message routes", async (targetKind, updatePath) => {
    const context = createContext(targetKind);
    const original = createMessage(context);
    const transferredUrl = "https://storage.example.test/owned/history-result.png?signature=retained";
    const { message_digest: _messageDigest, ...messageResponse } = original;
    const updated = { ...messageResponse, created_at: "2026-10-28T10:00:00", content: transferredUrl };
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(response(createSnapshot(context, null, [original])))
      .mockResolvedValueOnce(response({ url: transferredUrl }))
      .mockResolvedValueOnce(response(updated))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    const run = createPort(fetcher).beginRun(context);
    const signal = new AbortController().signal;

    await run.readSnapshot(signal);
    await expect(run.retryOssUpload(original, signal)).resolves.toBe(transferredUrl);
    await expect(run.updateMessageContent(original, transferredUrl, signal)).resolves.toBeUndefined();
    await expect(run.deleteMessage(original, signal)).resolves.toBeUndefined();

    const transferRequest = fetcher.mock.calls[1]!;
    expect(new URL(String(transferRequest[0])).pathname).toBe("/api/upload/retry-oss");
    expect(JSON.parse(String(transferRequest[1]?.body))).toEqual({ url: original.content });
    const updateRequest = fetcher.mock.calls[2]!;
    expect(new URL(String(updateRequest[0])).pathname).toContain(updatePath);
    expect(updateRequest[1]?.method).toBe("PUT");
    expect(JSON.parse(String(updateRequest[1]?.body))).toEqual({ content: transferredUrl });
    const deleteRequest = fetcher.mock.calls[3]!;
    expect(new URL(String(deleteRequest[0])).pathname).toContain(`${updatePath}single/`);
    expect(deleteRequest[1]?.method).toBe("DELETE");
    expect(deleteRequest[1]?.body).toBeUndefined();
    expect(fetcher).toHaveBeenCalledTimes(4);
  });

  it("accepts the real digest-free naive-UTC PUT response when its UTC instant matches the history GET", async () => {
    const context = createContext();
    const original = createMessage(context);
    const transferredUrl = "https://storage.example.test/owned/history-result.png?signature=retained";
    const { message_digest: _messageDigest, ...messageResponse } = original;
    const updated = { ...messageResponse, created_at: "2026-10-28T10:00:00", content: transferredUrl };
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(response(createSnapshot(context, null, [original])))
      .mockResolvedValueOnce(response(updated));
    const run = createPort(fetcher).beginRun(context);
    const signal = new AbortController().signal;
    await run.readSnapshot(signal);

    await expect(run.updateMessageContent(original, transferredUrl, signal)).resolves.toBeUndefined();
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("treats a mismatched PUT response without a digest as unknown", async () => {
    const context = createContext();
    const original = createMessage(context);
    const transferredUrl = "https://storage.example.test/owned/history-result.png?signature=retained";
    const { message_digest: _messageDigest, ...messageResponse } = original;
    const mismatched = { ...messageResponse, id: "different-message", content: transferredUrl };
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(response(createSnapshot(context, null, [original])))
      .mockResolvedValueOnce(response(mismatched));
    const run = createPort(fetcher).beginRun(context);
    const signal = new AbortController().signal;
    await run.readSnapshot(signal);

    await expect(run.updateMessageContent(original, transferredUrl, signal))
      .rejects.toMatchObject({ kind: "unknown" });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("does not retry an unknown OSS transfer response", async () => {
    const context = createContext();
    const message = createMessage(context);
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(response(createSnapshot(context, null, [message])))
      .mockResolvedValueOnce(response({ detail: "temporary_failure" }, 503));
    const run = createPort(fetcher).beginRun(context);
    const signal = new AbortController().signal;
    await run.readSnapshot(signal);

    await expect(run.retryOssUpload(message, signal)).rejects.toMatchObject({ kind: "unknown", status: 503 });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it.each([
    ["frame-preview", "image"],
    ["frame-preview", "video"],
    ["storyboard-image", "image"],
    ["storyboard-image", "video"],
  ] as const)("reads and adopts the exact %s / %s history tuple", async (targetKind, mode) => {
    const context = createContext(targetKind, mode);
    const candidateUrl = createMessage(context).content;
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(response(createSnapshot(context)))
      .mockResolvedValueOnce(response({
        protocol_version: 1,
        outcome: "applied",
        series_id: seriesId,
        chapter_id: chapterId,
        target_kind: targetKind,
        storyboard_asset_id: storyboardAssetId,
        frame_index: 0,
        target_proof: createProof(candidateUrl),
      }));
    const run = createPort(fetcher).beginRun(context);
    const snapshot = await run.readSnapshot(new AbortController().signal);
    expect(snapshot.messages).toHaveLength(1);
    expect(snapshot.writable).toBe(true);

    const result = await run.adopt({
      messageId: snapshot.messages[0]!.id,
      expectedMessageDigest: snapshot.messages[0]!.message_digest,
      expectedMappingDigest: snapshot.target_proof!.mapping_digest,
      expectedTargetDigest: snapshot.target_proof!.target_digest,
      expectedMediaUrl: snapshot.target_proof!.media_url,
      expectedLockIdentity: null,
    }, new AbortController().signal);

    expect(result.outcome).toBe("applied");
    expect(fetcher).toHaveBeenCalledTimes(2);
    const getUrl = new URL(String(fetcher.mock.calls[0]![0]));
    expect(getUrl.pathname).toBe(`/api/chapters/${chapterId}/generation-history`);
    expect(getUrl.searchParams.get("target_kind")).toBe(targetKind);
    expect(getUrl.searchParams.get("frame_index")).toBe("0");
    expect(getUrl.searchParams.get("chat_mode")).toBe(mode);
    expect(getUrl.searchParams.get("storyboard_asset_id")).toBe(storyboardAssetId);
    expect(fetcher.mock.calls[0]![1]?.method).toBe("GET");
    expect(fetcher.mock.calls[0]![1]?.credentials).toBe("omit");
    expect(fetcher.mock.calls[0]![1]?.redirect).toBe("error");
    const requestInit = fetcher.mock.calls[1]![1];
    expect(requestInit?.method).toBe("POST");
    const body = JSON.parse(String(requestInit?.body)) as Record<string, unknown>;
    expect(body).toEqual({
      protocol_version: 1,
      expected_series_id: seriesId,
      target_kind: targetKind,
      storyboard_asset_id: storyboardAssetId,
      expected_frame_index: 0,
      chat_mode: mode,
      message_id: "history-message-1200",
      expected_message_digest: messageDigest,
      expected_mapping_digest: mappingDigest,
      expected_target_digest: targetDigest,
      expected_media_url: null,
      expected_lock_identity: null,
    });
    expect(JSON.stringify(body)).not.toContain(candidateUrl);
    expect(String(fetcher.mock.calls[0]![0])).not.toContain("history-access-token");
  });

  it("keeps a frame with no stable storyboard ID browse-only", async () => {
    const context = { ...createContext(), storyboardAssetId: null };
    const snapshotValue = {
      ...createSnapshot(context),
      storyboard_asset_id: null,
      writable: false,
      read_only_reason: "missing_storyboard_id",
      target_proof: null,
    };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response(snapshotValue));
    const run = createPort(fetcher).beginRun(context);

    const snapshot = await run.readSnapshot(new AbortController().signal);

    expect(snapshot.writable).toBe(false);
    expect(snapshot.target_proof).toBeNull();
    expect(new URL(String(fetcher.mock.calls[0]![0])).searchParams.has("storyboard_asset_id")).toBe(false);
    await expect(run.adopt({
      messageId: "history-message-1200",
      expectedMessageDigest: messageDigest,
      expectedMappingDigest: mappingDigest,
      expectedTargetDigest: targetDigest,
      expectedMediaUrl: null,
      expectedLockIdentity: null,
    }, new AbortController().signal)).rejects.toMatchObject({ kind: "not-sent" });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("accepts unreadable chapter data as a read-only history response", async () => {
    const context = createContext();
    const payload = {
      ...createSnapshot(context),
      writable: false,
      read_only_reason: "unreadable_chapter",
      target_proof: null,
    };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response(payload));
    const run = createPort(fetcher).beginRun(context);

    const snapshot = await run.readSnapshot(new AbortController().signal);

    expect(snapshot.writable).toBe(false);
    expect(snapshot.read_only_reason).toBe("unreadable_chapter");
    await expect(run.adopt({
      messageId: snapshot.messages[0]!.id,
      expectedMessageDigest: messageDigest,
      expectedMappingDigest: mappingDigest,
      expectedTargetDigest: targetDigest,
      expectedMediaUrl: null,
      expectedLockIdentity: null,
    }, new AbortController().signal)).rejects.toMatchObject({ kind: "not-sent" });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("treats dispatched server errors as unknown and never retries the POST", async () => {
    const context = createContext();
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(response(createSnapshot(context)))
      .mockResolvedValueOnce(response({ detail: "generation_history_unavailable" }, 503));
    const run = createPort(fetcher).beginRun(context);
    const snapshot = await run.readSnapshot(new AbortController().signal);

    await expect(run.adopt({
      messageId: snapshot.messages[0]!.id,
      expectedMessageDigest: messageDigest,
      expectedMappingDigest: mappingDigest,
      expectedTargetDigest: targetDigest,
      expectedMediaUrl: null,
      expectedLockIdentity: null,
    }, new AbortController().signal)).rejects.toMatchObject({ kind: "unknown", status: 503 });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("maps stale target and validation responses without serializing untrusted detail", async () => {
    const context = createContext();
    const conflictFetcher = vi.fn<typeof fetch>().mockResolvedValue(response({ detail: "generation_history_conflict" }, 409));
    const conflictRun = createPort(conflictFetcher).beginRun(context);
    await expect(conflictRun.readSnapshot(new AbortController().signal)).rejects.toMatchObject({ kind: "conflict", status: 409 });

    const validationFetcher = vi.fn<typeof fetch>().mockResolvedValue(response({ detail: [{ loc: ["body"], msg: "invalid" }] }, 422));
    const validationRun = createPort(validationFetcher).beginRun(context);
    await expect(validationRun.readSnapshot(new AbortController().signal)).rejects.toMatchObject({ kind: "read-error", status: 422 });
    expect((await conflictFetcher.mock.results[0]!.value).bodyUsed).toBe(true);
  });

  it("explains the existing storyboard image URL length rejection", async () => {
    const context = createContext("storyboard-image");
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(response(createSnapshot(context)))
      .mockResolvedValueOnce(response({ detail: "storyboard_media_url_too_long" }, 422));
    const run = createPort(fetcher).beginRun(context);
    const snapshot = await run.readSnapshot(new AbortController().signal);

    await expect(run.adopt({
      messageId: snapshot.messages[0]!.id,
      expectedMessageDigest: messageDigest,
      expectedMappingDigest: mappingDigest,
      expectedTargetDigest: targetDigest,
      expectedMediaUrl: null,
      expectedLockIdentity: null,
    }, new AbortController().signal)).rejects.toMatchObject({
      kind: "rejected",
      status: 422,
      message: "该图片地址超过故事板素材字段长度限制，无法写入。",
    });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("invalidates a captured run when the caller's chapter scope changes", async () => {
    const context = createContext();
    const fetcher = vi.fn<typeof fetch>();
    const run = createPort(fetcher).beginRun(context);
    (context as { chapterId: string }).chapterId = "chapter-after-navigation";

    expect(run.isCurrent()).toBe(false);
    await expect(run.readSnapshot(new AbortController().signal)).rejects.toMatchObject({ kind: "not-sent" });
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("does not begin a history run when the stored actor does not match", () => {
    const storage = createStorage();
    storage.setItem(API_USER_KEY, JSON.stringify({ id: "another-user" }));
    const port = createPort(vi.fn<typeof fetch>(), storage);
    expect(() => port.beginRun(createContext())).toThrow(ChapterGenerationHistoryError);
  });
});
