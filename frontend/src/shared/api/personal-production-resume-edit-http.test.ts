// @vitest-environment node
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { InvalidResponseError } from "./contracts";
import type {
  PersonalProductionNoteUpdate,
  PersonalProductionResumeUpdate,
  PersonalProductionSnapshot,
} from "./personalProductionNotes";
import { createApiServices } from "./services";
import { API_TOKEN_KEY, type StorageLike } from "./storage";

const fixturePath = fileURLToPath(new URL("../../../../tools/api-fixture/server.mjs", import.meta.url));
const chapterId = "fixture-series-01-chapter-02";
const chapterOneId = "fixture-series-01-chapter-01";
const unreadableChapterId = "fixture-series-01-chapter-03";
const emptyChapterId = "fixture-series-01-chapter-04";
const notesPath = (id: string) => "/api/chapters/" + id + "/personal-production-notes";

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

interface FixtureProcess {
  baseUrl: string;
  output(): string;
  stop(): Promise<void>;
}

async function startFixture(overrides: Record<string, string> = {}): Promise<FixtureProcess> {
  const inheritedEnvironment = Object.fromEntries(
    Object.entries(process.env).filter(([key]) => !key.startsWith("FIXTURE_")),
  );
  const child = spawn(process.execPath, [fixturePath], {
    env: { ...inheritedEnvironment, ...overrides, FIXTURE_PORT: "0" },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let captured = "";
  child.stdout.setEncoding("utf8");
  child.stderr.setEncoding("utf8");
  child.stdout.on("data", (chunk: string) => { captured += chunk; });
  child.stderr.on("data", (chunk: string) => { captured += chunk; });

  let baseUrl: string;
  try {
    baseUrl = await new Promise<string>((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("Fixture startup timed out: " + captured)), 5_000);
      const onData = (chunk: string) => {
        const match = chunk.match(/http:\/\/127\.0\.0\.1:(\d+)\/api/);
        if (match?.[1]) {
          clearTimeout(timer);
          child.stdout.off("data", onData);
          resolve("http://127.0.0.1:" + match[1] + "/api");
        }
      };
      child.stdout.on("data", onData);
      child.once("error", (error) => {
        clearTimeout(timer);
        reject(error);
      });
      child.once("close", (code) => {
        clearTimeout(timer);
        reject(new Error("Fixture exited before startup (" + code + "): " + captured));
      });
    });
  } catch (error) {
    child.kill("SIGTERM");
    throw error;
  }

  return {
    baseUrl,
    output: () => captured,
    async stop() {
      if (child.exitCode !== null || child.signalCode !== null) {
        return;
      }
      child.kill("SIGTERM");
      await new Promise<void>((resolve) => {
        const timer = setTimeout(resolve, 1_000);
        child.once("close", () => {
          clearTimeout(timer);
          resolve();
        });
      });
    },
  };
}

interface RequestRecord {
  method: string;
  target: string;
  authorization: string | null;
  body: string | null;
}

function recordedFetcher(records: RequestRecord[]): typeof fetch {
  return async (input, init) => {
    const url = input instanceof URL
      ? input
      : input instanceof Request
        ? new URL(input.url)
        : new URL(input);
    const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined));
    records.push({
      method: init?.method ?? (input instanceof Request ? input.method : "GET"),
      target: url.pathname + url.search,
      authorization: headers.get("Authorization"),
      body: typeof init?.body === "string" ? init.body : null,
    });
    return globalThis.fetch(input, init);
  };
}

function createServices(
  baseUrl: string,
  records: RequestRecord[] = [],
  options: { timeoutMs?: number; token?: string } = {},
) {
  const storage = new MemoryStorage();
  storage.setItem(API_TOKEN_KEY, options.token ?? "fixture-demo-token");
  const services = createApiServices(baseUrl, {
    storage,
    fetcher: recordedFetcher(records),
    ...(options.timeoutMs === undefined ? {} : { timeoutMs: options.timeoutMs }),
  });
  return { services, storage };
}

function signal(): AbortSignal {
  return new AbortController().signal;
}

function resumeUpdate(snapshot: PersonalProductionSnapshot, resumeFrameId: string | null): PersonalProductionResumeUpdate {
  return { expected_revision: snapshot.revision, resume_frame_id: resumeFrameId };
}

function parseFixtureRequests(output: string): Array<{ method: string; pathname: string; status: number }> {
  return output.split("\n")
    .filter((line) => line.startsWith("{"))
    .map((line) => JSON.parse(line) as { method: string; pathname: string; status: number });
}

async function rawPut(baseUrl: string, token: string, targetChapterId: string, body: unknown): Promise<Response> {
  return globalThis.fetch(baseUrl + "/chapters/" + encodeURIComponent(targetChapterId) + "/personal-production-notes", {
    method: "PUT",
    headers: { Authorization: "Bearer " + token, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

async function setResume(
  services: ReturnType<typeof createApiServices>,
  targetChapterId: string,
  update: PersonalProductionResumeUpdate,
): Promise<PersonalProductionSnapshot> {
  if (!services.savePersonalProductionResume) {
    throw new Error("API services do not expose resume-position saving.");
  }
  return services.savePersonalProductionResume(targetChapterId, update, signal());
}

async function setNote(
  services: ReturnType<typeof createApiServices>,
  targetChapterId: string,
  update: PersonalProductionNoteUpdate,
): Promise<PersonalProductionSnapshot> {
  if (!services.savePersonalProductionNote) {
    throw new Error("API services do not expose personal-note saving.");
  }
  return services.savePersonalProductionNote(targetChapterId, update, signal());
}

describe("personal production resume API over real HTTP", () => {
  it("sets and clears revision-zero state with current bearer, private snapshots, and exact bodies", async () => {
    const fixture = await startFixture({ FIXTURE_PERSONAL_NOTES_SAVE_INITIAL: "unsaved" });
    const requests: RequestRecord[] = [];
    const first = createServices(fixture.baseUrl, requests);
    const second = createServices(fixture.baseUrl, requests, { token: "fixture-demo-token-02" });

    try {
      const initial = await first.services.getPersonalProductionNotes(chapterId, signal());
      const target = initial.frames[0];
      if (!target?.storyboard_asset_id || !target.source_valid) {
        throw new Error("Opted-in revision-zero chapter did not provide a stable resume target.");
      }
      expect(initial).toMatchObject({ revision: 0, media_state: "ready", resume_frame_id: null });
      expect(initial.frame_notes.size).toBe(0);

      const saved = await setResume(first.services, chapterId, resumeUpdate(initial, target.storyboard_asset_id));
      const reread = await first.services.getPersonalProductionNotes(chapterId, signal());
      const privateSnapshot = await second.services.getPersonalProductionNotes(chapterId, signal());
      expect(saved).toMatchObject({ revision: 1, resume_frame_id: target.storyboard_asset_id });
      expect(saved.frames).toEqual(initial.frames);
      expect(saved.frame_notes).toEqual(initial.frame_notes);
      expect(reread).toEqual(saved);
      expect(privateSnapshot).toMatchObject({ revision: 11, media_state: "ready" });
      expect(privateSnapshot.resume_frame_id).not.toBe(target.storyboard_asset_id);

      const cleared = await setResume(first.services, chapterId, resumeUpdate(saved, null));
      const afterClear = await first.services.getPersonalProductionNotes(chapterId, signal());
      expect(cleared).toMatchObject({ revision: 2, resume_frame_id: null });
      expect(cleared.frames).toEqual(saved.frames);
      expect(cleared.frame_notes).toEqual(saved.frame_notes);
      expect(afterClear).toEqual(cleared);

      expect(requests.filter(({ method }) => method === "PUT").map(({ target: path, authorization, body }) => ({
        path,
        authorization,
        body,
      }))).toEqual([
        {
          path: notesPath(chapterId),
          authorization: "Bearer fixture-demo-token",
          body: JSON.stringify({ expected_revision: 0, resume_frame_id: target.storyboard_asset_id }),
        },
        {
          path: notesPath(chapterId),
          authorization: "Bearer fixture-demo-token",
          body: JSON.stringify({ expected_revision: 1, resume_frame_id: null }),
        },
      ]);
      expect(fixture.output()).not.toContain("demo123");
      expect(fixture.output()).not.toContain("fixture-demo-token");
      expect(fixture.output()).not.toContain(target.storyboard_asset_id);
      expect(parseFixtureRequests(fixture.output()).filter(({ method }) => method === "PUT"))
        .toEqual([
          { method: "PUT", pathname: notesPath(chapterId), status: 200 },
          { method: "PUT", pathname: notesPath(chapterId), status: 200 },
        ]);
    } finally {
      await fixture.stop();
    }
  });

  it("validates exact bodies and personal revision while allowing a double-empty stable source", async () => {
    const fixture = await startFixture({ FIXTURE_PERSONAL_NOTES_MODE: "double-empty" });
    const { services } = createServices(fixture.baseUrl);
    try {
      const before = await services.getPersonalProductionNotes(chapterOneId, signal());
      const target = before.frames[0];
      if (!target?.storyboard_asset_id || !target.source_valid) {
        throw new Error("Double-empty chapter did not provide a stable target.");
      }
      expect(before).toMatchObject({ media_state: "ready" });
      expect(target.asset_image_digest).toBeNull();
      expect(target.preview_digest).toBeNull();

      const updated = await setResume(services, chapterOneId, resumeUpdate(before, target.storyboard_asset_id));
      expect(updated).toMatchObject({
        revision: before.revision + 1,
        resume_frame_id: target.storyboard_asset_id,
      });
      expect(updated.frames[0]).toEqual(target);

      const unknownField = await rawPut(fixture.baseUrl, "fixture-demo-token", chapterOneId, {
        expected_revision: updated.revision,
        resume_frame_id: target.storyboard_asset_id,
        user_id: "other-user",
      });
      expect(unknownField.status).toBe(422);
      const invalidRawId = await rawPut(fixture.baseUrl, "fixture-demo-token", chapterOneId, {
        expected_revision: updated.revision,
        resume_frame_id: "missing-stable-frame",
      });
      expect(invalidRawId.status).toBe(422);
      const staleRevision = await rawPut(fixture.baseUrl, "fixture-demo-token", chapterOneId, {
        expected_revision: before.revision,
        resume_frame_id: null,
      });
      expect(staleRevision.status).toBe(409);

      const after = await services.getPersonalProductionNotes(chapterOneId, signal());
      expect(after).toEqual(updated);
      expect(parseFixtureRequests(fixture.output()).filter(({ method }) => method === "PUT")
        .map(({ status }) => status)).toEqual([200, 422, 422, 409]);
    } finally {
      await fixture.stop();
    }
  });

  it("clears stale positions from unreadable or empty snapshots without requiring a frame", async () => {
    const fixture = await startFixture();
    const { services } = createServices(fixture.baseUrl);
    try {
      const unreadable = await services.getPersonalProductionNotes(unreadableChapterId, signal());
      expect(unreadable.media_state).toBe("unreadable");
      expect(unreadable.resume_frame_id).toBe(unreadableChapterId + "-retired-shot");
      expect(unreadable.frames).toEqual([]);
      const clearedUnreadable = await setResume(services, unreadableChapterId, resumeUpdate(unreadable, null));
      expect(clearedUnreadable).toMatchObject({
        revision: unreadable.revision + 1,
        media_state: "unreadable",
        frames: [],
        resume_frame_id: null,
      });

      const empty = await services.getPersonalProductionNotes(emptyChapterId, signal());
      expect(empty).toMatchObject({ media_state: "empty", resume_frame_id: null, frames: [] });
      const clearedEmpty = await setResume(services, emptyChapterId, resumeUpdate(empty, null));
      expect(clearedEmpty).toMatchObject({
        revision: empty.revision + 1,
        media_state: "empty",
        resume_frame_id: null,
        frames: [],
      });
    } finally {
      await fixture.stop();
    }
  });

  it("returns canonical maintenance changes once while note-only saves retain their normal behavior", async () => {
    const fixture = await startFixture({ FIXTURE_PERSONAL_NOTES_SAVE_MODE: "resume-maintenance-success" });
    const { services } = createServices(fixture.baseUrl);
    try {
      const before = await services.getPersonalProductionNotes(chapterId, signal());
      const target = before.frames[0];
      const untouchedFrame = before.frames[1];
      if (!target?.storyboard_asset_id || !untouchedFrame?.storyboard_asset_id || target.media_revision === null) {
        throw new Error("Maintenance mode requires two identified frames.");
      }
      expect(before.frame_notes.get(target.storyboard_asset_id)).toMatchObject({ status: "approved" });

      const saved = await setResume(services, chapterId, resumeUpdate(before, target.storyboard_asset_id));
      const after = await services.getPersonalProductionNotes(chapterId, signal());
      const maintainedNote = saved.frame_notes.get(target.storyboard_asset_id);
      expect(saved.revision).toBe(before.revision + 1);
      expect(saved.resume_frame_id).toBe(target.storyboard_asset_id);
      expect(saved.frames[0]).toMatchObject({
        frame_index: target.frame_index,
        storyboard_asset_id: target.storyboard_asset_id,
        source_valid: target.source_valid,
      });
      expect(saved.frames[0]?.media_revision).toBe(target.media_revision + 1);
      expect(saved.frames[0]?.asset_image_digest).not.toBe(target.asset_image_digest);
      expect(saved.frames[0]?.preview_digest).not.toBe(target.preview_digest);
      expect(maintainedNote).toMatchObject({ status: "approved", needs_reconfirmation: true });
      expect(maintainedNote).not.toHaveProperty("approved_media_revision");
      expect(saved.frame_notes.get(untouchedFrame.storyboard_asset_id))
        .toEqual(before.frame_notes.get(untouchedFrame.storyboard_asset_id));
      expect(after).toEqual(saved);

      const noteUpdate: PersonalProductionNoteUpdate = {
        expected_revision: saved.revision,
        frames: [{
          storyboard_asset_id: untouchedFrame.storyboard_asset_id,
          expected_media_revision: untouchedFrame.media_revision ?? 1,
          status: "needs_revision",
          note: "resume-maintenance-success 不改变旧备注保存路径。",
        }],
      };
      const noteSaved = await setNote(services, chapterId, noteUpdate);
      expect(noteSaved.revision).toBe(saved.revision + 1);
      expect(noteSaved.frame_notes.get(untouchedFrame.storyboard_asset_id)).toMatchObject({
        status: "needs_revision",
        note: noteUpdate.frames[0].note,
      });
      expect(parseFixtureRequests(fixture.output()).filter(({ method }) => method === "PUT")
        .map(({ status }) => status)).toEqual([200, 200]);
    } finally {
      await fixture.stop();
    }
  });

  it.each([401, 403, 404, 422, 500] as const)("preserves real resume PUT status %s", async (status) => {
    const fixture = await startFixture({ FIXTURE_PERSONAL_NOTES_SAVE_STATUS: String(status) });
    const { services } = createServices(fixture.baseUrl);
    try {
      const current = await services.getPersonalProductionNotes(chapterId, signal());
      const targetId = current.frames[0]?.storyboard_asset_id;
      if (!targetId) {
        throw new Error("Fixture lacks a stable target.");
      }
      await expect(setResume(services, chapterId, resumeUpdate(current, targetId)))
        .rejects.toMatchObject({ kind: "http", status });
      expect(parseFixtureRequests(fixture.output()).some(({ method, status: loggedStatus }) => (
        method === "PUT" && loggedStatus === status
      ))).toBe(true);
    } finally {
      await fixture.stop();
    }
  });

  it("keeps applied canonical state visible after an invalid response and times out without retry", async () => {
    const invalidFixture = await startFixture({ FIXTURE_PERSONAL_NOTES_SAVE_MODE: "applied-invalid-structure" });
    const invalidServices = createServices(invalidFixture.baseUrl, [], { timeoutMs: 200 });
    try {
      const before = await invalidServices.services.getPersonalProductionNotes(chapterId, signal());
      const targetId = before.frames[0]?.storyboard_asset_id;
      if (!targetId) {
        throw new Error("Fixture lacks a stable target.");
      }
      await expect(setResume(invalidServices.services, chapterId, resumeUpdate(before, targetId)))
        .rejects.toBeInstanceOf(InvalidResponseError);
      const after = await invalidServices.services.getPersonalProductionNotes(chapterId, signal());
      expect(after.revision).toBe(before.revision + 1);
      expect(after.resume_frame_id).toBe(targetId);
      expect(parseFixtureRequests(invalidFixture.output()).filter(({ method }) => method === "PUT"))
        .toHaveLength(1);
    } finally {
      await invalidFixture.stop();
    }

    const timeoutFixture = await startFixture({ FIXTURE_PERSONAL_NOTES_SAVE_MODE: "timeout" });
    const timeoutRequests: RequestRecord[] = [];
    const timeoutServices = createServices(timeoutFixture.baseUrl, timeoutRequests, { timeoutMs: 40 });
    try {
      const before = await timeoutServices.services.getPersonalProductionNotes(chapterId, signal());
      const targetId = before.frames[0]?.storyboard_asset_id;
      if (!targetId) {
        throw new Error("Fixture lacks a stable target.");
      }
      await expect(setResume(timeoutServices.services, chapterId, resumeUpdate(before, targetId)))
        .rejects.toMatchObject({ kind: "timeout" });
      expect(timeoutRequests.filter(({ method }) => method === "PUT")).toHaveLength(1);
      expect(parseFixtureRequests(timeoutFixture.output()).filter(({ method }) => method === "PUT"))
        .toEqual([{ method: "PUT", pathname: notesPath(chapterId), status: 0 }]);
    } finally {
      await timeoutFixture.stop();
    }
  });
});
