// @vitest-environment node
import { describe, expect, it, vi } from "vitest";
import type { Chapter, StoryboardFrame } from "../../shared/api/contracts";
import type { StorageLike } from "../../shared/api/storage";
import {
  ContinuousChapterJournalError,
  ContinuousChapterJournalConflictError,
  ContinuousChapterJournalLockError,
  ContinuousChapterJournalStorageError,
  MAX_CONTINUOUS_CHAPTER_JOURNAL_BYTES,
  continuousChapterOperationJournalKey,
  continuousChapterOperationRoute,
  continuousTargetManifestDigest,
  createContinuousOperationJournalEnvelope,
  digestContinuousSource,
  freezeContinuousOperationRequest,
  resolveContinuousChapterJournal,
  withContinuousChapterOperationLock,
  writeContinuousChapterJournal,
  readContinuousChapterJournal,
} from "./continuousChapterOperationJournal";
import { buildGuardedChapterUpdate, type ContinuousChapterDraftSnapshot, type ContinuousDraftFrame } from "./continuousChapterEditing";

class MemoryStorage implements StorageLike {
  readonly values = new Map<string, string>();
  failWrites = false;
  getItem(key: string): string | null { return this.values.get(key) ?? null; }
  setItem(key: string, value: string): void {
    if (this.failWrites) throw new Error("synthetic storage write failure");
    this.values.set(key, value);
  }
  removeItem(key: string): void { this.values.delete(key); }
}

const scope = { actorId: "user-a", apiBaseUrl: "https://example.invalid/api", seriesId: "series-a", chapterId: "chapter-a" };
const sourceFrame = { text: "原文", storyboard: [], character: [], scene: [], prop: [], unknown: { ratio: 1e-7, label: "雪だるま🧊" } } as StoryboardFrame;
const editorSessionId = "00000000-0000-4000-8000-000000000002";
const documentId = "00000000-0000-4000-8000-000000000003";

async function makeJournal(operationId: string, chapterId = scope.chapterId) {
  const chapter: Chapter = {
    id: chapterId,
    series_id: scope.seriesId,
    title: "整章恢复测试",
    order: 0,
    content: [sourceFrame],
  } as Chapter;
  const frames: ContinuousDraftFrame[] = [{
    draftId: "source:0",
    sourceIndex: 0,
    storyboardId: null,
    originalFrame: sourceFrame,
    text: "已改",
    characterIds: [],
    sceneIds: [],
    propIds: [],
  }];
  const snapshot: ContinuousChapterDraftSnapshot = {
    chapter,
    frames,
    expectedStoryboards: [],
    lockIdentity: null,
  };
  const update = buildGuardedChapterUpdate(snapshot, frames);
  const frozen = await freezeContinuousOperationRequest({
    protocol_version: 1,
    editor_session_id: editorSessionId,
    command: { kind: "save-chapter", ...update, draft_frame_ids: frames.map((frame) => frame.draftId) },
  });
  const sourceDigest = await digestContinuousSource(frozen.bodySha256);
  const draftFrameIds = frames.map((frame) => frame.draftId);
  return createContinuousOperationJournalEnvelope({
    scope: { ...scope, chapterId },
    editorSessionId,
    documentId,
    authSessionGeneration: 7,
    snapshot,
    frames,
    selectedDraftId: "source:0",
    operationId,
    kind: "save-chapter",
    targetDraftId: null,
    draftFrameIds,
    bodyJson: frozen.bodyJson,
    bodySha256: frozen.bodySha256,
    sourceSnapshotDigest: sourceDigest,
    targetManifestDigest: await continuousTargetManifestDigest(draftFrameIds),
    phase: "captured_not_dispatched",
    route: continuousChapterOperationRoute(chapterId, operationId),
  });
}

const lockManager = {
  async request<T>(_name: string, _options: { mode: "exclusive"; ifAvailable: true }, callback: (lock: Lock | null) => T | Promise<T>): Promise<T> {
    return callback({} as Lock);
  },
};

async function writeUnderLock(
  storage: StorageLike,
  key: string,
  journal: Awaited<ReturnType<typeof makeJournal>>,
  expectedCurrentIntegrity: string | null,
) {
  return withContinuousChapterOperationLock(
    key,
    (lease) => writeContinuousChapterJournal(storage, key, journal, lease, expectedCurrentIntegrity),
    lockManager,
  );
}

describe("continuous chapter operation journal integrity", () => {
  it.each([
    ["different chapter snapshot", (value: Record<string, any>) => { value.snapshot.chapter.id = "chapter-b"; }],
    ["out-of-range source mapping", (value: Record<string, any>) => { value.snapshot.frames[0].sourceIndex = 999; }],
    ["edited draft text", (value: Record<string, any>) => { value.snapshot.frames[0].text = "被改写"; }],
    ["request target manifest", (value: Record<string, any>) => { value.operation.draft_frame_ids = ["other-frame"]; }],
  ])("rejects a journal with %s before restoring its draft", async (_label, mutate) => {
    const storage = new MemoryStorage();
    const key = continuousChapterOperationJournalKey(scope);
    const written = await writeUnderLock(storage, key, await makeJournal("00000000-0000-4000-8000-000000000001"), null);
    const raw = storage.getItem(key);
    expect(raw).not.toBeNull();
    const changed = JSON.parse(raw!) as Record<string, any>;
    mutate(changed);
    storage.setItem(key, JSON.stringify(changed));

    await expect(readContinuousChapterJournal(storage, key)).rejects.toBeInstanceOf(ContinuousChapterJournalError);
    expect(storage.getItem(key)).not.toBeNull();
    expect(written.integrity_sha256).not.toBe("");
  });

  it("does not let stale operation A resolve or remove current operation B", async () => {
    const storage = new MemoryStorage();
    const key = continuousChapterOperationJournalKey(scope);
    const journalA = await writeUnderLock(storage, key, await makeJournal("00000000-0000-4000-8000-000000000001"), null);
    const journalB = await writeUnderLock(
      storage,
      key,
      await makeJournal("00000000-0000-4000-8000-000000000004"),
      journalA.integrity_sha256,
    );

    await expect(withContinuousChapterOperationLock(
      key,
      (lease) => resolveContinuousChapterJournal(storage, key, journalA, lease),
      lockManager,
    )).rejects.toBeInstanceOf(ContinuousChapterJournalError);
    const current = await readContinuousChapterJournal(storage, key);
    expect(current?.operation.operation_id).toBe(journalB.operation.operation_id);
  });

  it("does not replace dispatch-possible operation A with a new operation B even with A's current checksum", async () => {
    const storage = new MemoryStorage();
    const key = continuousChapterOperationJournalKey(scope);
    const dispatchPossibleA = { ...await makeJournal("00000000-0000-4000-8000-000000000005"), phase: "dispatch_possible" as const };
    const writtenA = await writeUnderLock(storage, key, dispatchPossibleA, null);

    await expect(writeUnderLock(
      storage,
      key,
      await makeJournal("00000000-0000-4000-8000-000000000006"),
      writtenA.integrity_sha256,
    )).rejects.toBeInstanceOf(ContinuousChapterJournalConflictError);
    await expect(readContinuousChapterJournal(storage, key)).resolves.toMatchObject({
      phase: "dispatch_possible",
      operation: { operation_id: writtenA.operation.operation_id, request_body_json: writtenA.operation.request_body_json },
    });
  });

  it("rechecks the exact record before set so another observed pending record cannot be overwritten", async () => {
    const storage = new MemoryStorage();
    const otherStorage = new MemoryStorage();
    const key = continuousChapterOperationJournalKey(scope);
    const operationA = await writeUnderLock(storage, key, {
      ...await makeJournal("00000000-0000-4000-8000-000000000007"),
      phase: "dispatch_possible",
    }, null);
    const operationB = await writeUnderLock(otherStorage, key, await makeJournal("00000000-0000-4000-8000-000000000008"), null);
    const operationARetry = {
      ...operationA,
      phase: "unknown" as const,
      observation: { ...operationA.observation, message: "请求结果待确认" },
    };

    await withContinuousChapterOperationLock(key, async (lease) => {
      const write = writeContinuousChapterJournal(storage, key, operationARetry, lease, operationA.integrity_sha256);
      storage.values.set(key, otherStorage.getItem(key)!);
      await expect(write).rejects.toBeInstanceOf(ContinuousChapterJournalConflictError);
    }, lockManager);
    await expect(readContinuousChapterJournal(storage, key)).resolves.toMatchObject({
      operation: { operation_id: operationB.operation.operation_id },
    });
  });

  it("does not mutate after the Web Lock callback returns before asynchronous sealing finishes", async () => {
    const storage = new MemoryStorage();
    const key = continuousChapterOperationJournalKey(scope);
    const dispatchPossible = {
      ...await makeJournal("00000000-0000-4000-8000-000000000009"),
      phase: "dispatch_possible" as const,
    };
    const written = await writeUnderLock(storage, key, dispatchPossible, null);
    const unknown = {
      ...written,
      phase: "unknown" as const,
      observation: { ...written.observation, message: "原请求结果待核对" },
    };
    let pendingWrite: Promise<unknown> | undefined;

    await withContinuousChapterOperationLock(key, async (lease) => {
      pendingWrite = writeContinuousChapterJournal(storage, key, unknown, lease, written.integrity_sha256);
    }, lockManager);
    await expect(pendingWrite).rejects.toBeInstanceOf(ContinuousChapterJournalLockError);
    await expect(readContinuousChapterJournal(storage, key)).resolves.toMatchObject({
      phase: "dispatch_possible",
      operation: { operation_id: written.operation.operation_id },
    });
  });

  it("preserves callback failures after acquiring the chapter lock", async () => {
    const error = new Error("POST timed out after dispatch became possible");
    await expect(withContinuousChapterOperationLock("lock", async () => { throw error; }, lockManager))
      .rejects.toBe(error);
  });

  it("preserves a dispatch-possible record and does not claim no request was sent when a later write fails", async () => {
    const storage = new MemoryStorage();
    const key = continuousChapterOperationJournalKey(scope);
    const captured = await makeJournal("00000000-0000-4000-8000-000000000031");
    const dispatchPossible = { ...captured, phase: "dispatch_possible" as const };
    const written = await writeUnderLock(storage, key, dispatchPossible, null);
    storage.failWrites = true;

    const unknown = {
      ...written,
      phase: "unknown" as const,
      observation: { ...written.observation, message: "响应未确认" },
    };
    const error = await withContinuousChapterOperationLock(
      key,
      (lease) => writeContinuousChapterJournal(storage, key, unknown, lease, written.integrity_sha256),
      lockManager,
    ).catch((value: unknown) => value);

    expect(error).toBeInstanceOf(ContinuousChapterJournalStorageError);
    expect((error as Error).message).not.toContain("未发送");
    storage.failWrites = false;
    await expect(readContinuousChapterJournal(storage, key)).resolves.toMatchObject({ phase: "dispatch_possible" });
  });

  it("preserves a dispatch-possible record and gives unknown wording when an observation exceeds the journal limit", async () => {
    const storage = new MemoryStorage();
    const key = continuousChapterOperationJournalKey(scope);
    const dispatchPossible = {
      ...await makeJournal("00000000-0000-4000-8000-000000000032"),
      phase: "dispatch_possible" as const,
    };
    const written = await writeUnderLock(storage, key, dispatchPossible, null);
    const oversizedUnknown = {
      ...written,
      phase: "unknown" as const,
      observation: { ...written.observation, message: "x".repeat(MAX_CONTINUOUS_CHAPTER_JOURNAL_BYTES + 1) },
    };
    const error = await withContinuousChapterOperationLock(
      key,
      (lease) => writeContinuousChapterJournal(storage, key, oversizedUnknown, lease, written.integrity_sha256),
      lockManager,
    ).catch((value: unknown) => value);

    expect(error).toBeInstanceOf(ContinuousChapterJournalError);
    expect((error as Error).message).toMatch(/可能已经发出|无法安全确认/u);
    expect((error as Error).message).not.toContain("未发送");
    await expect(readContinuousChapterJournal(storage, key)).resolves.toMatchObject({ phase: "dispatch_possible" });
  });

  it("validates the sealed full shape before replacing a valid pending record", async () => {
    const storage = new MemoryStorage();
    const key = continuousChapterOperationJournalKey(scope);
    const current = await writeUnderLock(storage, key, {
      ...await makeJournal("00000000-0000-4000-8000-000000000033"),
      phase: "dispatch_possible",
    }, null);
    const rawBefore = storage.getItem(key);
    const invalidNext = { ...current, phase: "not-a-phase" as never };

    await expect(writeUnderLock(storage, key, invalidNext, current.integrity_sha256))
      .rejects.toBeInstanceOf(ContinuousChapterJournalError);
    expect(storage.getItem(key)).toBe(rawBefore);
  });

  it("fails closed when Web Locks are unavailable", async () => {
    const callback = vi.fn(async () => "sent");
    await expect(withContinuousChapterOperationLock("lock", callback, null))
      .rejects.toBeInstanceOf(ContinuousChapterJournalLockError);
    expect(callback).not.toHaveBeenCalled();
  });
});
