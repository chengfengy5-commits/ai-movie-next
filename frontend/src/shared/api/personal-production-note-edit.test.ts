// @vitest-environment node
import { describe, expect, it, vi } from "vitest";
import { demoSeries } from "../../features/series/demoSeries";
import { getDemoChapterData } from "../../features/chapters/demoChapters";
import type { PersonalProductionNoteUpdate } from "./personalProductionNotes";
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

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function updateFor(
  revision: number,
  frameId: string,
  status: "unmarked" | "needs_revision" | "approved" = "needs_revision",
  expectedMediaRevision = 2,
): PersonalProductionNoteUpdate {
  return {
    expected_revision: revision,
    frames: [{
      storyboard_asset_id: frameId,
      expected_media_revision: expectedMediaRevision,
      status,
      note: "保留雨痕的文字记录",
    }],
  };
}

function noteSnapshot(chapterId: string, frameId: string, revision: number): Record<string, unknown> {
  return {
    chapter_id: chapterId,
    revision,
    media_state: "ready",
    frames: [{
      frame_index: 0,
      storyboard_asset_id: frameId,
      media_revision: 2,
      source_valid: true,
      asset_image_digest: null,
      preview_digest: "a".repeat(64),
      invalid_reason: null,
    }],
    frame_notes: {
      [frameId]: { status: "needs_revision", note: "保留雨痕的文字记录" },
      "retired-frame": { status: "needs_revision", note: "旧镜头记录", unknown: { preserved: true } },
    },
    resume_frame_id: frameId,
  };
}

describe("personal production note save services", () => {
  it("keeps demo CAS updates private to the service instance and preserves other notes and resume", async () => {
    const storage = new MemoryStorage();
    const services = createDemoServices(storage);
    const secondServices = createDemoServices(storage);
    await services.login({ username: "demo", password: "demo123" }, new AbortController().signal);
    const data = getDemoChapterData(demoSeries[0]!.id);
    if (data === null) {
      throw new Error("Expected demo chapter data.");
    }
    const chapter = data.chapters[0]!;
    const targetId = demoSeries[0]!.id + "-frame-letter";
    const original = await services.getPersonalProductionNotes(chapter.id, new AbortController().signal);
    expect(original.revision).toBe(4);

    const update = updateFor(original.revision, targetId, "needs_revision", 1);
    const saved = await services.savePersonalProductionNote!(chapter.id, update, new AbortController().signal);

    expect(saved.revision).toBe(5);
    expect(saved.resume_frame_id).toBe(original.resume_frame_id);
    expect(saved.frame_notes.get(targetId)).toMatchObject({
      status: "needs_revision",
      note: "保留雨痕的文字记录",
    });
    expect(saved.frame_notes.get(demoSeries[0]!.id + "-retired-frame")).toMatchObject({
      note: "旧镜头记录，仅供参考。",
    });
    await expect(services.savePersonalProductionNote!(chapter.id, update, new AbortController().signal))
      .rejects.toMatchObject({ status: 409 });

    const fresh = await services.getPersonalProductionNotes(chapter.id, new AbortController().signal);
    const separateInstance = await secondServices.getPersonalProductionNotes(chapter.id, new AbortController().signal);
    expect(fresh.revision).toBe(5);
    expect(fresh.frame_notes.get(targetId)).toMatchObject({ note: "保留雨痕的文字记录" });
    expect(separateInstance.revision).toBe(4);
    expect(separateInstance.frame_notes.get(targetId)).toMatchObject({ note: "信封封口处的雨痕需要补充表现。" });

    const callerCopy = fresh.frame_notes.get(targetId) as Record<string, unknown>;
    callerCopy.audit = { nested: { changed: true } };
    const afterCallerMutation = await services.getPersonalProductionNotes(chapter.id, new AbortController().signal);
    expect(afterCallerMutation.frame_notes.get(targetId)).not.toHaveProperty("audit");
    expect(storage.getItem(MOCK_SESSION_KEY)).toBe("demo-session");
    expect(storage.getItem(MOCK_USER_KEY)).not.toBeNull();
    expect(storage.getItem(API_TOKEN_KEY)).toBeNull();
    expect(storage.getItem(API_USER_KEY)).toBeNull();
  });

  it("uses a current bearer and exact single-frame PUT to the validated loopback path", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "current-note-token");
    const chapterId = "chapter/α ?#";
    const responseBody = noteSnapshot(chapterId, "frame-one", 1);
    const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => jsonResponse(responseBody));
    const services = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher });
    const update = updateFor(0, "frame-one");

    const result = await services.savePersonalProductionNote!(chapterId, update, new AbortController().signal);

    expect(result.chapter_id).toBe(chapterId);
    const [input, init] = fetcher.mock.calls[0] ?? [];
    const requestUrl = new URL(String(input));
    expect(requestUrl.pathname).toBe("/api/chapters/chapter%2F%CE%B1%20%3F%23/personal-production-notes");
    expect(requestUrl.search).toBe("");
    expect(requestUrl.hash).toBe("");
    expect(init?.method).toBe("PUT");
    expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer current-note-token");
    expect(JSON.parse(String(init?.body))).toEqual({
      expected_revision: 0,
      frames: [{
        storyboard_asset_id: "frame-one",
        expected_media_revision: 2,
        status: "needs_revision",
        note: "保留雨痕的文字记录",
      }],
    });
    expect(storage.getItem(API_TOKEN_KEY)).toBe("current-note-token");
  });

  it("rejects remote and non-/api direct factory targets before calling fetch", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "must-not-leak");
    const fetcher = vi.fn(async () => jsonResponse({}));
    const update = updateFor(0, "frame-one");
    const remote = createApiServices("https://api.example.invalid/api", { storage, fetcher });
    const wrongPath = createApiServices("http://127.0.0.1:4175/custom", { storage, fetcher });

    await expect(remote.savePersonalProductionNote!("chapter-one", update, new AbortController().signal))
      .rejects.toMatchObject({ kind: "invalid-response" });
    await expect(wrongPath.savePersonalProductionNote!("chapter-one", update, new AbortController().signal))
      .rejects.toMatchObject({ kind: "invalid-response" });
    expect(fetcher).not.toHaveBeenCalled();
    expect(storage.getItem(API_TOKEN_KEY)).toBe("must-not-leak");
  });

  it("does not let a completed save response apply after the bearer changes", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "old-token");
    let resolveFetch!: (response: Response) => void;
    const fetcher = vi.fn(() => new Promise<Response>((resolve) => { resolveFetch = resolve; }));
    const services = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher });
    const pending = services.savePersonalProductionNote!(
      "chapter-one",
      updateFor(0, "frame-one"),
      new AbortController().signal,
    );

    storage.setItem(API_TOKEN_KEY, "new-token");
    storage.setItem(API_USER_KEY, "new-user");
    resolveFetch(jsonResponse(noteSnapshot("chapter-one", "frame-one", 1)));

    await expect(pending).rejects.toMatchObject({ kind: "aborted" });
    expect(storage.getItem(API_TOKEN_KEY)).toBe("new-token");
    expect(storage.getItem(API_USER_KEY)).toBe("new-user");
  });

  it("preserves the current session after a current HTTP 401 save response", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "current-token");
    storage.setItem(API_USER_KEY, "current-user");
    const services = createApiServices("http://127.0.0.1:4175/api", {
      storage,
      fetcher: vi.fn(async () => jsonResponse({ detail: "expired" }, 401)),
    });

    await expect(services.savePersonalProductionNote!(
      "chapter-one",
      updateFor(0, "frame-one"),
      new AbortController().signal,
    )).rejects.toMatchObject({ status: 401 });
    expect(storage.getItem(API_TOKEN_KEY)).toBe("current-token");
    expect(storage.getItem(API_USER_KEY)).toBe("current-user");
  });
});
