// @vitest-environment node
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { InvalidResponseError } from "./contracts";
import type { PersonalProductionSnapshot, PersonalProductionNoteUpdate } from "./personalProductionNotes";
import { createApiServices } from "./services";
import { API_TOKEN_KEY, type StorageLike } from "./storage";

const fixturePath = fileURLToPath(new URL("../../../../tools/api-fixture/server.mjs", import.meta.url));
const chapterId = "fixture-series-01-chapter-02";
const notesPath = `/api/chapters/${chapterId}/personal-production-notes`;

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
      const timer = setTimeout(() => reject(new Error(`Fixture startup timed out: ${captured}`)), 5_000);
      const onData = (chunk: string) => {
        const match = chunk.match(/http:\/\/127\.0\.0\.1:(\d+)\/api/);
        if (match?.[1]) {
          clearTimeout(timer);
          child.stdout.off("data", onData);
          resolve(`http://127.0.0.1:${match[1]}/api`);
        }
      };
      child.stdout.on("data", onData);
      child.once("error", (error) => {
        clearTimeout(timer);
        reject(error);
      });
      child.once("close", (code) => {
        clearTimeout(timer);
        reject(new Error(`Fixture exited before startup (${code}): ${captured}`));
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
      target: `${url.pathname}${url.search}`,
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

async function save(
  services: ReturnType<typeof createApiServices>,
  targetChapterId: string,
  update: PersonalProductionNoteUpdate,
  requestSignal = signal(),
): Promise<PersonalProductionSnapshot> {
  if (!services.savePersonalProductionNote) {
    throw new Error("API services do not expose personal-note saving.");
  }
  return services.savePersonalProductionNote(targetChapterId, update, requestSignal);
}

function updateFor(
  snapshot: PersonalProductionSnapshot,
  options: {
    frameIndex?: number;
    expectedRevision?: number;
    expectedMediaRevision?: number;
    status?: PersonalProductionNoteUpdate["frames"][0]["status"];
    note?: string;
  } = {},
): PersonalProductionNoteUpdate {
  const frame = snapshot.frames[options.frameIndex ?? 0];
  if (!frame?.storyboard_asset_id || frame.media_revision === null) {
    throw new Error("Fixture snapshot does not provide an editable frame.");
  }
  return {
    expected_revision: options.expectedRevision ?? snapshot.revision,
    frames: [{
      storyboard_asset_id: frame.storyboard_asset_id,
      expected_media_revision: options.expectedMediaRevision ?? frame.media_revision,
      status: options.status ?? "needs_revision",
      note: options.note ?? "本地测试备注🙂",
    }],
  };
}

interface FixtureRequestLog {
  method: string;
  pathname: string;
  status: number;
}

function parseFixtureRequests(output: string): FixtureRequestLog[] {
  return output.split("\n")
    .filter((line) => line.startsWith("{"))
    .map((line) => JSON.parse(line) as FixtureRequestLog);
}

function noteText(value: unknown): string | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return null;
  }
  const note = (value as Record<string, unknown>).note;
  return typeof note === "string" ? note : null;
}

async function rawPut(
  baseUrl: string,
  token: string,
  targetChapterId: string,
  body: unknown,
  suffix = "",
): Promise<Response> {
  return globalThis.fetch(
    `${baseUrl}/chapters/${encodeURIComponent(targetChapterId)}/personal-production-notes${suffix}`,
    {
      method: "PUT",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
    },
  );
}

describe("personal production note save API over real HTTP", () => {
  it("uses the thirteenth business method and preserves a full existing snapshot", async () => {
    const fixture = await startFixture();
    const requests: RequestRecord[] = [];
    const { services } = createServices(fixture.baseUrl, requests);
    const note = "Unicode 原文不裁剪：🙂 é";

    try {
      const signedIn = await services.login({ username: "demo", password: "demo123" }, signal());
      const restored = await services.restore(signal());
      const teams = await services.listMyTeams(signal());
      const series = await services.listSeries(signal());
      const selectedSeries = series[0];
      if (!selectedSeries) {
        throw new Error("Fixture did not return a series.");
      }
      const chapters = await services.listChapters(selectedSeries.id, signal());
      const selectedChapter = chapters.find(({ id }) => id === chapterId);
      if (!selectedChapter) {
        throw new Error("Fixture did not return the save chapter.");
      }
      await services.listStoryboardAssets(selectedSeries.id, chapterId, signal());
      await services.listCharacters(selectedSeries.id, signal());
      await services.listScenes(selectedSeries.id, signal());
      await services.listProps(selectedSeries.id, signal());
      await services.getPersonalRoughCut(chapterId, signal());
      await services.listMyTasks(1, signal());
      const original = await services.getPersonalProductionNotes(chapterId, signal());
      const targetId = original.frames[0]?.storyboard_asset_id;
      const preservedId = original.frames[1]?.storyboard_asset_id;
      if (!targetId || !preservedId) {
        throw new Error("Fixture did not return both frame identities.");
      }
      const expectedUpdate = updateFor(original, { note, status: "needs_revision" });

      const saved = await save(services, chapterId, expectedUpdate);
      const reread = await services.getPersonalProductionNotes(chapterId, signal());

      expect(signedIn.id).toBe("demo-user");
      expect(restored?.id).toBe("demo-user");
      expect(teams.length).toBeGreaterThan(0);
      expect(saved.revision).toBe(original.revision + 1);
      expect(saved.chapter_id).toBe(chapterId);
      expect(saved.resume_frame_id).toBe(original.resume_frame_id);
      expect(saved.frames).toEqual(original.frames);
      expect(saved.frame_notes.get(targetId)).toMatchObject({
        status: "needs_revision",
        note,
        approved_media_revision: null,
        needs_reconfirmation: false,
      });
      expect(saved.frame_notes.get(preservedId)).toEqual(original.frame_notes.get(preservedId));
      expect(saved.frame_notes.get(`${chapterId}-orphan-shot`))
        .toEqual(original.frame_notes.get(`${chapterId}-orphan-shot`));
      expect(saved.frame_notes.get("__proto__")).toEqual(original.frame_notes.get("__proto__"));
      expect(reread.revision).toBe(saved.revision);
      expect(reread.frame_notes.get(targetId)).toEqual(saved.frame_notes.get(targetId));

      const put = requests.find(({ method }) => method === "PUT");
      expect(put).toMatchObject({
        target: notesPath,
        authorization: "Bearer fixture-demo-token",
      });
      expect(put?.body).not.toBeNull();
      expect(JSON.parse(put?.body ?? "null")).toEqual(expectedUpdate);
      expect(Object.keys(JSON.parse(put?.body ?? "{}") as object).sort()).toEqual([
        "expected_revision",
        "frames",
      ]);
      expect(requests.filter(({ method }) => method === "PUT")).toHaveLength(1);

      const methods = new Set(requests.map(({ method, target }) => `${method} ${target.split("?")[0]}`));
      expect(methods).toEqual(new Set([
        "POST /api/auth/login",
        "GET /api/auth/me",
        "GET /api/teams/my",
        "GET /api/series",
        `GET /api/series/${selectedSeries.id}/chapters`,
        `GET /api/series/${selectedSeries.id}/storyboard-assets`,
        `GET /api/series/${selectedSeries.id}/characters`,
        `GET /api/series/${selectedSeries.id}/scenes`,
        `GET /api/series/${selectedSeries.id}/props`,
        `GET /api/chapters/${chapterId}/rough-cut`,
        "GET /api/chat/tasks/list",
        `GET ${notesPath}`,
        `PUT ${notesPath}`,
      ]));
      expect(requests.filter(({ method }) => method !== "POST")
        .every(({ authorization }) => authorization === "Bearer fixture-demo-token")).toBe(true);
      expect(fixture.output()).not.toContain(note);
      expect(fixture.output()).not.toContain("demo123");
      expect(fixture.output()).not.toContain("fixture-demo-token");
      expect(parseFixtureRequests(fixture.output()).filter(({ method }) => method === "PUT"))
        .toEqual([{ method: "PUT", pathname: notesPath, status: 200 }]);
    } finally {
      await fixture.stop();
    }
  });

  it("creates an editable revision-zero snapshot only for the opted-in account and keeps notes private", async () => {
    const fixture = await startFixture({ FIXTURE_PERSONAL_NOTES_SAVE_INITIAL: "unsaved" });
    const requests: RequestRecord[] = [];
    const first = createServices(fixture.baseUrl, requests);
    const second = createServices(fixture.baseUrl, requests, { token: "fixture-demo-token-02" });
    const note = "初次建立个人记录🙂";

    try {
      const initial = await first.services.getPersonalProductionNotes(chapterId, signal());
      expect(initial).toMatchObject({ revision: 0, media_state: "ready", frame_notes: new Map(), resume_frame_id: null });
      expect(initial.frames).toHaveLength(2);
      expect(initial.frames.every(({ source_valid, media_revision }) => source_valid && media_revision !== null))
        .toBe(true);

      const update = updateFor(initial, { frameIndex: 1, status: "unmarked", note });
      const saved = await save(first.services, chapterId, update);
      const firstReread = await first.services.getPersonalProductionNotes(chapterId, signal());
      const secondSnapshot = await second.services.getPersonalProductionNotes(chapterId, signal());
      const targetId = update.frames[0].storyboard_asset_id;

      expect(saved.revision).toBe(1);
      expect(saved.resume_frame_id).toBeNull();
      expect(saved.frames).toEqual(initial.frames);
      expect(saved.frame_notes.get(targetId)).toMatchObject({ status: "unmarked", note });
      expect(firstReread.revision).toBe(1);
      expect(firstReread.frame_notes.get(targetId)).toEqual(saved.frame_notes.get(targetId));
      expect(secondSnapshot).toMatchObject({ revision: 11, media_state: "ready" });
      expect(secondSnapshot.frame_notes.get(targetId)).toMatchObject({
        note: "第二账号自己的已核对记录。",
        status: "approved",
      });
      expect(noteText(secondSnapshot.frame_notes.get(targetId))).not.toBe(note);
      expect(requests.filter(({ method, target }) => method === "PUT" && target === notesPath))
        .toHaveLength(1);
      expect(requests.filter(({ method, target }) => method === "GET" && target === notesPath)
        .map(({ authorization }) => authorization)).toEqual([
        "Bearer fixture-demo-token",
        "Bearer fixture-demo-token",
        "Bearer fixture-demo-token-02",
      ]);
      expect(fixture.output()).not.toContain(note);
      expect(fixture.output()).not.toContain("fixture-demo-token");
    } finally {
      await fixture.stop();
    }
  });

  it.each([401, 403, 404, 422, 500] as const)("preserves real HTTP save status %s", async (status) => {
    const fixture = await startFixture({ FIXTURE_PERSONAL_NOTES_SAVE_STATUS: String(status) });
    const { services } = createServices(fixture.baseUrl);
    try {
      const current = await services.getPersonalProductionNotes(chapterId, signal());
      await expect(save(services, chapterId, updateFor(current)))
        .rejects.toMatchObject({ kind: "http", status });
      expect(parseFixtureRequests(fixture.output()).some((record) => record.method === "PUT" && record.status === status))
        .toBe(true);
    } finally {
      await fixture.stop();
    }
  });

  it.each(["revision-conflict", "media-conflict"] as const)("returns a real %s without applying a second write", async (mode) => {
    const fixture = await startFixture({ FIXTURE_PERSONAL_NOTES_SAVE_MODE: mode });
    const requests: RequestRecord[] = [];
    const { services } = createServices(fixture.baseUrl, requests);
    try {
      const current = await services.getPersonalProductionNotes(chapterId, signal());
      const update = updateFor(current, mode === "media-conflict"
        ? { expectedMediaRevision: (current.frames[0]?.media_revision ?? 1) + 1 }
        : {});
      await expect(save(services, chapterId, update)).rejects.toMatchObject({ kind: "http", status: 409 });
      const reread = await services.getPersonalProductionNotes(chapterId, signal());
      expect(reread.revision).toBe(current.revision);
      expect(requests.filter(({ method }) => method === "PUT")).toHaveLength(1);
    } finally {
      await fixture.stop();
    }
  });

  it("returns 422 for a missing stable frame identity without mutating the record", async () => {
    const fixture = await startFixture();
    const requests: RequestRecord[] = [];
    const { services } = createServices(fixture.baseUrl, requests);
    try {
      const current = await services.getPersonalProductionNotes(chapterId, signal());
      const update = updateFor(current);
      const requestedFrame = update.frames[0];
      if (requestedFrame === undefined) {
        throw new Error("Expected one requested frame.");
      }
      const missingIdentityUpdate = {
        ...update,
        frames: [{ ...requestedFrame, storyboard_asset_id: "missing-stable-frame" }] as const,
      };

      await expect(save(services, chapterId, missingIdentityUpdate)).rejects.toMatchObject({
        kind: "http",
        status: 422,
      });
      const staleRevisionUpdate = {
        ...update,
        expected_revision: current.revision + 1,
      };
      await expect(save(services, chapterId, staleRevisionUpdate)).rejects.toMatchObject({
        kind: "http",
        status: 409,
      });
      const reread = await services.getPersonalProductionNotes(chapterId, signal());
      expect(reread).toEqual(current);
      expect(requests.filter(({ method, target }) => method === "PUT" && target === notesPath))
        .toHaveLength(2);
      const loggedSaves = parseFixtureRequests(fixture.output()).filter((record) => (
        record.method === "PUT" && record.pathname === notesPath
      ));
      expect(loggedSaves.map(({ status }) => status)).toEqual([422, 409]);
    } finally {
      await fixture.stop();
    }
  });

  it("retains a separate in-memory maintenance commit when returning 409", async () => {
    const fixture = await startFixture({ FIXTURE_PERSONAL_NOTES_SAVE_MODE: "maintenance-conflict" });
    const { services } = createServices(fixture.baseUrl);
    try {
      const current = await services.getPersonalProductionNotes(chapterId, signal());
      const targetId = current.frames[0]?.storyboard_asset_id;
      const maintainedId = current.frames[1]?.storyboard_asset_id;
      if (!targetId || !maintainedId) {
        throw new Error("Fixture did not provide two frame records for maintenance conflict.");
      }

      await expect(save(services, chapterId, updateFor(current)))
        .rejects.toMatchObject({ kind: "http", status: 409 });
      const after = await services.getPersonalProductionNotes(chapterId, signal());

      expect(after.revision).toBe(current.revision + 1);
      expect(after.frame_notes.get(targetId)).toEqual(current.frame_notes.get(targetId));
      expect(after.frame_notes.get(maintainedId)).toMatchObject({
        status: "unmarked",
        approved_media_revision: null,
        needs_reconfirmation: true,
      });
      expect(noteText(after.frame_notes.get(maintainedId)))
        .toBe(noteText(current.frame_notes.get(maintainedId)));
    } finally {
      await fixture.stop();
    }
  });

  it("rejects non-exact wire bodies and an approved double-empty frame without mutation", async () => {
    const fixture = await startFixture();
    try {
      const current = await globalThis.fetch(`${fixture.baseUrl}/chapters/${chapterId}/personal-production-notes`, {
        headers: { Authorization: "Bearer fixture-demo-token" },
      });
      const snapshot = await current.json() as PersonalProductionSnapshot;
      const targetId = snapshot.frames[0]?.storyboard_asset_id;
      const mediaRevision = snapshot.frames[0]?.media_revision;
      if (!targetId || mediaRevision === null || mediaRevision === undefined) {
        throw new Error("Fixture did not provide a writable target frame.");
      }
      const invalidBody = {
        expected_revision: snapshot.revision,
        frames: [{
          storyboard_asset_id: targetId,
          expected_media_revision: mediaRevision,
          status: "needs_revision",
          note: "raw request",
        }],
        resume_frame_id: targetId,
      };
      const invalidResponse = await rawPut(fixture.baseUrl, "fixture-demo-token", chapterId, invalidBody);
      expect(invalidResponse.status).toBe(422);

      const doubleEmptyFixture = await startFixture({ FIXTURE_PERSONAL_NOTES_MODE: "double-empty" });
      try {
        const emptyChapterId = "fixture-series-01-chapter-01";
        const beforeResponse = await globalThis.fetch(
          `${doubleEmptyFixture.baseUrl}/chapters/${emptyChapterId}/personal-production-notes`,
          { headers: { Authorization: "Bearer fixture-demo-token" } },
        );
        const before = await beforeResponse.json() as PersonalProductionSnapshot;
        const emptyFrame = before.frames[0];
        if (!emptyFrame?.storyboard_asset_id || emptyFrame.media_revision === null) {
          throw new Error("Double-empty fixture did not provide a stable editable frame.");
        }
        expect(emptyFrame.asset_image_digest).toBeNull();
        expect(emptyFrame.preview_digest).toBeNull();

        const rejected = await rawPut(doubleEmptyFixture.baseUrl, "fixture-demo-token", emptyChapterId, {
          expected_revision: before.revision,
          frames: [{
            storyboard_asset_id: emptyFrame.storyboard_asset_id,
            expected_media_revision: emptyFrame.media_revision,
            status: "approved",
            note: "不应产生认可",
          }],
        });
        expect(rejected.status).toBe(422);
        const afterResponse = await globalThis.fetch(
          `${doubleEmptyFixture.baseUrl}/chapters/${emptyChapterId}/personal-production-notes`,
          { headers: { Authorization: "Bearer fixture-demo-token" } },
        );
        const after = await afterResponse.json() as PersonalProductionSnapshot;
        expect(after).toEqual(before);
      } finally {
        await doubleEmptyFixture.stop();
      }
    } finally {
      await fixture.stop();
    }
  });

  it.each(["applied-invalid-json", "applied-invalid-structure"] as const)(
    "reports $mode as uncertain while GET confirms the applied revision",
    async (mode) => {
      const fixture = await startFixture({ FIXTURE_PERSONAL_NOTES_SAVE_MODE: mode });
      const requests: RequestRecord[] = [];
      const { services } = createServices(fixture.baseUrl, requests);
      try {
        const initial = await services.getPersonalProductionNotes(chapterId, signal());
        const update = updateFor(initial, { note: `已应用但${mode}🙂` });
        const pendingSave = save(services, chapterId, update);
        if (mode === "applied-invalid-structure") {
          await expect(pendingSave).rejects.toBeInstanceOf(InvalidResponseError);
        } else {
          await expect(pendingSave).rejects.toMatchObject({ kind: "invalid-response" });
        }

        const confirmed = await services.getPersonalProductionNotes(chapterId, signal());
        expect(confirmed.revision).toBe(initial.revision + 1);
        expect(noteText(confirmed.frame_notes.get(update.frames[0].storyboard_asset_id)))
          .toBe(update.frames[0].note);
        expect(requests.filter(({ method }) => method === "PUT")).toHaveLength(1);
        expect(fixture.output()).not.toContain(update.frames[0].note);
      } finally {
        await fixture.stop();
      }
    },
  );

  it.each(["timeout", "body-timeout"] as const)("logs and applies a real save before the %s reply is lost", async (mode) => {
    const fixture = await startFixture({ FIXTURE_PERSONAL_NOTES_SAVE_MODE: mode });
    const { services } = createServices(fixture.baseUrl, [], { timeoutMs: 80 });
    try {
      const initial = await services.getPersonalProductionNotes(chapterId, signal());
      const update = updateFor(initial, { note: `结果未知${mode}🙂` });
      await expect(save(services, chapterId, update)).rejects.toMatchObject({ kind: "timeout" });

      const confirmed = await services.getPersonalProductionNotes(chapterId, signal());
      expect(confirmed.revision).toBe(initial.revision + 1);
      expect(noteText(confirmed.frame_notes.get(update.frames[0].storyboard_asset_id)))
        .toBe(update.frames[0].note);
      const putRecord = parseFixtureRequests(fixture.output()).find(({ method }) => method === "PUT");
      expect(putRecord).toMatchObject({ method: "PUT", pathname: notesPath });
      expect(putRecord?.status).toBe(mode === "timeout" ? 0 : 200);
      expect(fixture.output()).not.toContain(update.frames[0].note);
    } finally {
      await fixture.stop();
    }
  });

  it("applies a delayed write despite client cancellation without logging request data", async () => {
    const fixture = await startFixture({ FIXTURE_PERSONAL_NOTES_SAVE_DELAY_MS: "120" });
    const { services } = createServices(fixture.baseUrl, [], { timeoutMs: 35 });
    try {
      const initial = await services.getPersonalProductionNotes(chapterId, signal());
      const update = updateFor(initial, { note: "客户端已取消🙂" });
      await expect(save(services, chapterId, update)).rejects.toMatchObject({ kind: "timeout" });
      await new Promise((resolve) => setTimeout(resolve, 150));
      const confirmed = await services.getPersonalProductionNotes(chapterId, signal());
      expect(confirmed.revision).toBe(initial.revision + 1);
      expect(noteText(confirmed.frame_notes.get(update.frames[0].storyboard_asset_id)))
        .toBe(update.frames[0].note);
      expect(fixture.output()).not.toContain(update.frames[0].note);
    } finally {
      await fixture.stop();
    }
  });

  it("encodes the chapter path once and rejects an unauthenticated or query-selected PUT", async () => {
    const fixture = await startFixture();
    const requests: RequestRecord[] = [];
    const { services } = createServices(fixture.baseUrl, requests);
    const update: PersonalProductionNoteUpdate = {
      expected_revision: 0,
      frames: [{
        storyboard_asset_id: "x",
        expected_media_revision: 1,
        status: "needs_revision",
        note: "x",
      }],
    };
    try {
      await expect(save(services, "chapter /?&雪#", update))
        .rejects.toMatchObject({ kind: "http", status: 404 });
      expect(requests[0]?.target).toBe(
        "/api/chapters/chapter%20%2F%3F%26%E9%9B%AA%23/personal-production-notes",
      );

      const unauthenticated = await rawPut(fixture.baseUrl, "", chapterId, update);
      expect(unauthenticated.status).toBe(401);
      const query = await rawPut(fixture.baseUrl, "fixture-demo-token", chapterId, update, "?user_id=demo-user-02");
      expect(query.status).toBe(422);
      for (const invalidChapterId of ["", ".", ".."] as const) {
        await expect(save(services, invalidChapterId, update))
          .rejects.toMatchObject({ kind: "invalid-response" });
      }
      expect(requests).toHaveLength(1);
    } finally {
      await fixture.stop();
    }
  });

  it("advertises PUT in CORS preflight without adding a control route", async () => {
    const fixture = await startFixture();
    try {
      const response = await globalThis.fetch(`${fixture.baseUrl}/chapters/${chapterId}/personal-production-notes`, {
        method: "OPTIONS",
        headers: {
          Origin: "http://localhost:5174",
          "Access-Control-Request-Method": "PUT",
          "Access-Control-Request-Headers": "authorization,content-type",
        },
      });
      expect(response.status).toBe(204);
      expect(response.headers.get("Access-Control-Allow-Methods")).toContain("PUT");
      const unexpected = await globalThis.fetch(`${fixture.baseUrl}/__fixture__/save-mode`, {
        headers: { Authorization: "Bearer fixture-demo-token" },
      });
      expect(unexpected.status).toBe(404);
    } finally {
      await fixture.stop();
    }
  });
});
