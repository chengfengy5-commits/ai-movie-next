// @vitest-environment node
import { describe, expect, it, vi } from "vitest";
import { demoSeries } from "../../features/series/demoSeries";
import { getDemoChapterData } from "../../features/chapters/demoChapters";
import { InvalidResponseError } from "./contracts";
import type { PersonalProductionResumeUpdate } from "./personalProductionNotes";
import { createApiServices, createDemoServices } from "./services";
import { API_TOKEN_KEY, API_USER_KEY, MOCK_SESSION_KEY, type StorageLike } from "./storage";

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

function resumeSnapshot(chapterId: string, resumeFrameId: string | null, revision = 1) {
  return {
    chapter_id: chapterId,
    revision,
    media_state: "ready",
    frames: [{
      frame_index: 0,
      storyboard_asset_id: "frame/α",
      media_revision: null,
      source_valid: true,
      asset_image_digest: null,
      preview_digest: null,
      invalid_reason: null,
    }],
    frame_notes: {},
    resume_frame_id: resumeFrameId,
  };
}

function update(expectedRevision: number, resumeFrameId: string | null): PersonalProductionResumeUpdate {
  return { expected_revision: expectedRevision, resume_frame_id: resumeFrameId };
}

describe("personal production resume services", () => {
  it("shares demo revision CAS with note writes and keeps state instance-private", async () => {
    const storage = new MemoryStorage();
    const services = createDemoServices(storage);
    const otherInstance = createDemoServices(storage);
    const signal = new AbortController().signal;
    await services.login({ username: "demo", password: "demo123" }, signal);
    const chapterData = getDemoChapterData(demoSeries[0]!.id);
    if (chapterData === null) {
      throw new Error("Expected demo chapter data.");
    }
    const chapter = chapterData.chapters[0]!;
    const original = await services.getPersonalProductionNotes(chapter.id, signal);
    const targetFrame = original.frames.find((frame) => frame.storyboard_asset_id !== null && frame.source_valid);
    if (targetFrame?.storyboard_asset_id === null || targetFrame === undefined) {
      throw new Error("Expected a valid demo frame.");
    }

    const savedResume = await services.savePersonalProductionResume!(
      chapter.id,
      update(original.revision, targetFrame.storyboard_asset_id),
      signal,
    );
    expect(savedResume.revision).toBe(original.revision + 1);
    expect(savedResume.resume_frame_id).toBe(targetFrame.storyboard_asset_id);
    expect(savedResume.frame_notes).toEqual(original.frame_notes);
    await expect(services.savePersonalProductionResume!(
      chapter.id,
      update(original.revision, null),
      signal,
    )).rejects.toMatchObject({ status: 409 });

    const noteWrite = await services.savePersonalProductionNote!(
      chapter.id,
      {
        expected_revision: savedResume.revision,
        frames: [{
          storyboard_asset_id: targetFrame.storyboard_asset_id,
          expected_media_revision: targetFrame.media_revision ?? 1,
          status: "needs_revision",
          note: "与续作标记共享版本",
        }],
      },
      signal,
    );
    expect(noteWrite.revision).toBe(savedResume.revision + 1);
    expect(noteWrite.resume_frame_id).toBe(targetFrame.storyboard_asset_id);

    const clear = await services.savePersonalProductionResume!(
      chapter.id,
      update(noteWrite.revision, null),
      signal,
    );
    expect(clear.revision).toBe(noteWrite.revision + 1);
    expect(clear.resume_frame_id).toBeNull();
    expect(clear.frame_notes.get(targetFrame.storyboard_asset_id)).toMatchObject({
      status: "needs_revision",
      note: "与续作标记共享版本",
    });

    const persisted = await services.getPersonalProductionNotes(chapter.id, signal);
    const isolated = await otherInstance.getPersonalProductionNotes(chapter.id, signal);
    expect(persisted.revision).toBe(clear.revision);
    expect(persisted.resume_frame_id).toBeNull();
    expect(isolated.revision).toBe(original.revision);
    expect(isolated.resume_frame_id).toBe(original.resume_frame_id);
    expect(storage.getItem(MOCK_SESSION_KEY)).toBe("demo-session");
    expect(storage.getItem(API_TOKEN_KEY)).toBeNull();
    expect(storage.getItem(API_USER_KEY)).toBeNull();
  });

  it("sends an exact resume-only PUT on the validated loopback path", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "current-resume-token");
    const chapterId = "chapter/α ?#";
    const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse(resumeSnapshot(chapterId, "frame/α")));
    const services = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher });

    const saved = await services.savePersonalProductionResume!(
      chapterId,
      update(0, "frame/α"),
      new AbortController().signal,
    );

    expect(saved.chapter_id).toBe(chapterId);
    const [input, init] = fetcher.mock.calls[0] ?? [];
    const requestUrl = new URL(String(input));
    expect(requestUrl.pathname).toBe("/api/chapters/chapter%2F%CE%B1%20%3F%23/personal-production-notes");
    expect(requestUrl.search).toBe("");
    expect(requestUrl.hash).toBe("");
    expect(init?.method).toBe("PUT");
    expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer current-resume-token");
    expect(JSON.parse(String(init?.body))).toEqual({
      expected_revision: 0,
      resume_frame_id: "frame/α",
    });
    expect(storage.getItem(API_TOKEN_KEY)).toBe("current-resume-token");
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it.each([
    { expected_revision: -1, resume_frame_id: null },
    { expected_revision: Number.MAX_SAFE_INTEGER, resume_frame_id: null },
    { expected_revision: 0, resume_frame_id: " \t" },
    { expected_revision: 0, resume_frame_id: "🙂".repeat(37) },
    { expected_revision: 0, resume_frame_id: null, user_id: "another-user" },
  ])("rejects invalid resume-only updates without making a request: %o", async (value) => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "current-token");
    const fetcher = vi.fn(async () => jsonResponse(resumeSnapshot("chapter-one", null)));
    const services = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher });

    await expect(services.savePersonalProductionResume!(
      "chapter-one",
      value as unknown as PersonalProductionResumeUpdate,
      new AbortController().signal,
    )).rejects.toBeInstanceOf(InvalidResponseError);
    expect(fetcher).not.toHaveBeenCalled();
    expect(storage.getItem(API_TOKEN_KEY)).toBe("current-token");
  });

  it("rejects remote and non-API direct factory targets before calling fetch", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "must-not-leak");
    const fetcher = vi.fn(async () => jsonResponse(resumeSnapshot("chapter-one", null)));
    const remote = createApiServices("https://api.example.invalid/api", { storage, fetcher });
    const wrongPath = createApiServices("http://127.0.0.1:4175/custom", { storage, fetcher });

    await expect(remote.savePersonalProductionResume!(
      "chapter-one",
      update(0, null),
      new AbortController().signal,
    )).rejects.toMatchObject({ kind: "invalid-response" });
    await expect(wrongPath.savePersonalProductionResume!(
      "chapter-one",
      update(0, null),
      new AbortController().signal,
    )).rejects.toMatchObject({ kind: "invalid-response" });
    expect(fetcher).not.toHaveBeenCalled();
    expect(storage.getItem(API_TOKEN_KEY)).toBe("must-not-leak");
  });

  it("does not accept a late response after the bearer changes", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "old-token");
    let resolveFetch!: (response: Response) => void;
    const fetcher = vi.fn(() => new Promise<Response>((resolve) => { resolveFetch = resolve; }));
    const services = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher });
    const pending = services.savePersonalProductionResume!(
      "chapter-one",
      update(0, null),
      new AbortController().signal,
    );

    storage.setItem(API_TOKEN_KEY, "new-token");
    storage.setItem(API_USER_KEY, "new-user");
    resolveFetch(jsonResponse(resumeSnapshot("chapter-one", null)));

    await expect(pending).rejects.toMatchObject({ kind: "aborted" });
    expect(storage.getItem(API_TOKEN_KEY)).toBe("new-token");
    expect(storage.getItem(API_USER_KEY)).toBe("new-user");
  });

  it("preserves the session after a current HTTP 401", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "current-token");
    storage.setItem(API_USER_KEY, "current-user");
    const services = createApiServices("http://127.0.0.1:4175/api", {
      storage,
      fetcher: vi.fn(async () => jsonResponse({ detail: "expired" }, 401)),
    });

    await expect(services.savePersonalProductionResume!(
      "chapter-one",
      update(0, null),
      new AbortController().signal,
    )).rejects.toMatchObject({ status: 401 });
    expect(storage.getItem(API_TOKEN_KEY)).toBe("current-token");
    expect(storage.getItem(API_USER_KEY)).toBe("current-user");
  });
});
