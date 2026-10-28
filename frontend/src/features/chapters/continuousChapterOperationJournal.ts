import type { Chapter, StoryboardAsset } from "../../shared/api/contracts";
import {
  continuousSourceSnapshotBinding,
  continuousTargetManifestDigest,
  parseContinuousChapterOperationLookup,
} from "../../shared/api/continuousChapterOperations";
import type { ContinuousChapterOperationKind, ContinuousChapterOperationReceipt } from "../../shared/api/continuousChapterOperations";
import type { StorageLike } from "../../shared/api/storage";
import {
  buildGuardedChapterUpdate,
  type ContinuousChapterDraftSnapshot,
  type ContinuousDraftFrame,
} from "./continuousChapterEditing";

export const CONTINUOUS_CHAPTER_OPERATION_JOURNAL_VERSION = 1 as const;
export const MAX_CONTINUOUS_CHAPTER_JOURNAL_BYTES = 2_000_000;
export { continuousTargetManifestDigest };

export type ContinuousChapterOperationPhase =
  | "captured_not_dispatched"
  | "dispatch_possible"
  | "unknown"
  | "acknowledged_pending_receipt"
  | "receipt_committed_pending_target_read"
  | "receipt_rejected"
  | "resolved_reviewed";

export interface ContinuousChapterOperationEnvelope {
  readonly schema_version: typeof CONTINUOUS_CHAPTER_OPERATION_JOURNAL_VERSION;
  readonly integrity_sha256: string;
  readonly origin: {
    readonly actor_id: string;
    readonly api_base_url: string;
    readonly series_id: string;
    readonly chapter_id: string;
    readonly editor_session_id: string;
    readonly document_id: string;
    readonly auth_session_generation: number;
  };
  readonly created_at: string;
  readonly phase: ContinuousChapterOperationPhase;
  readonly snapshot: {
    readonly chapter: Chapter;
    readonly frames: readonly ContinuousDraftFrame[];
    readonly expected_storyboards: readonly StoryboardAsset[];
    readonly lock_identity: ContinuousChapterDraftSnapshot["lockIdentity"];
  };
  readonly selected_draft_id: string | null;
  readonly operation: {
    readonly operation_id: string;
    readonly kind: ContinuousChapterOperationKind;
    readonly target_draft_id: string | null;
    readonly draft_frame_ids: readonly string[];
    readonly request_body_json: string;
    readonly request_body_sha256: string;
    readonly source_snapshot_digest: string;
    readonly target_manifest_digest: string;
    readonly route: string;
  };
  readonly observation: {
    readonly message: string | null;
    readonly receipt: ContinuousChapterOperationReceipt | null;
    readonly result: Record<string, unknown> | null;
    readonly target_status: "unchecked" | "matches" | "changed" | "unavailable";
  };
}

export type NewContinuousChapterOperationEnvelope = Omit<ContinuousChapterOperationEnvelope, "integrity_sha256"> & {
  readonly integrity_sha256?: string;
};

export interface ContinuousChapterOperationScope {
  readonly actorId: string;
  readonly apiBaseUrl: string;
  readonly seriesId: string;
  readonly chapterId: string;
}

export interface ContinuousChapterOperationReceiptIdentity extends Pick<
  ContinuousChapterOperationReceipt,
  | "operation_id"
  | "owner_id"
  | "editor_session_id"
  | "series_id"
  | "chapter_id"
  | "command_kind"
  | "request_body_sha256"
  | "source_snapshot_digest"
  | "target_draft_id"
  | "target_manifest_digest"
  | "outcome"
  | "result_identity"
> {}

export interface ContinuousChapterOperationLockLease {
  readonly storageKey: string;
  assertActive(): void;
}

export class ContinuousChapterJournalError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ContinuousChapterJournalError";
  }
}

export class ContinuousChapterJournalStorageError extends ContinuousChapterJournalError {
  constructor(message = "本地原操作记录不可用；当前原操作状态无法安全确认，请先核对记录后再继续。") {
    super(message);
    this.name = "ContinuousChapterJournalStorageError";
  }
}

export class ContinuousChapterJournalLockError extends ContinuousChapterJournalError {
  constructor(message = "当前浏览器无法确认连续编辑的跨标签互斥，操作未发送。") {
    super(message);
    this.name = "ContinuousChapterJournalLockError";
  }
}

export class ContinuousChapterJournalConflictError extends ContinuousChapterJournalError {
  constructor(message = "本章节的待确认原操作已变化；当前记录保持原样，未覆盖或清除。") {
    super(message);
    this.name = "ContinuousChapterJournalConflictError";
  }
}

export interface ContinuousChapterLockManager {
  request<T>(
    name: string,
    options: { readonly mode: "exclusive"; readonly ifAvailable: true },
    callback: (lock: Lock | null) => T | Promise<T>,
  ): Promise<T>;
}

export function continuousChapterOperationJournalKey(scope: ContinuousChapterOperationScope): string {
  const apiBaseUrl = normalizeApiBaseUrl(scope.apiBaseUrl);
  if (scope.actorId.trim() === "" || scope.seriesId.trim() === "" || scope.chapterId.trim() === "" || apiBaseUrl === null) {
    throw new ContinuousChapterJournalError("当前账号、接口或章节身份不完整，无法安全记录原操作。");
  }
  assertWellFormedString(scope.actorId);
  assertWellFormedString(scope.seriesId);
  assertWellFormedString(scope.chapterId);
  return [
    "haoai:continuous-chapter-pending:v1",
    scope.actorId,
    apiBaseUrl,
    scope.seriesId,
    scope.chapterId,
  ].map(encodeURIComponent).join(":");
}

export function normalizeApiBaseUrl(value: string): string | null {
  try {
    const parsed = new URL(value);
    if ((parsed.protocol !== "http:" && parsed.protocol !== "https:")
      || parsed.username !== "" || parsed.password !== "" || parsed.search !== "" || parsed.hash !== "") return null;
    parsed.pathname = parsed.pathname.replace(/\/+$/u, "");
    return parsed.toString().replace(/\/$/u, "");
  } catch {
    return null;
  }
}

export function continuousChapterOperationRoute(chapterId: string, operationId: string): string {
  return `/chapters/${encodeURIComponent(chapterId)}/continuous-edit/operations/${encodeURIComponent(operationId)}`;
}

export function continuousChapterOperationLockName(storageKey: string): string {
  return `haoai:continuous-chapter-lock:${storageKey}`;
}

export async function withContinuousChapterOperationLock<T>(
  storageKey: string,
  callback: (lease: ContinuousChapterOperationLockLease) => Promise<T>,
  lockManager: ContinuousChapterLockManager | null = browserLockManager(),
): Promise<T> {
  if (lockManager === null) throw new ContinuousChapterJournalLockError();
  let callbackStarted = false;
  let active = false;
  const lease: ContinuousChapterOperationLockLease = {
    storageKey,
    assertActive() {
      if (!active) throw new ContinuousChapterJournalLockError("章节操作锁已释放，未修改本地原操作记录。");
    },
  };
  try {
    return await lockManager.request(
      continuousChapterOperationLockName(storageKey),
      { mode: "exclusive", ifAvailable: true },
      async (lock) => {
        if (lock === null) throw new ContinuousChapterJournalLockError("另一个标签页正在处理本章节操作，请稍后再核对。");
        active = true;
        callbackStarted = true;
        return callback(lease);
      },
    );
  } catch (error) {
    if (callbackStarted) throw error;
    if (error instanceof ContinuousChapterJournalError) throw error;
    throw new ContinuousChapterJournalLockError();
  } finally {
    active = false;
  }
}

function browserLockManager(): ContinuousChapterLockManager | null {
  try {
    if (typeof navigator === "undefined" || navigator.locks === undefined) return null;
    return navigator.locks;
  } catch {
    return null;
  }
}

function assertFiniteJson(value: unknown, seen = new Set<object>(), depth = 0): void {
  if (depth > 100) throw new ContinuousChapterJournalError("请求数据层级过深，未发送连续编辑操作。");
  if (value === null || typeof value === "boolean") return;
  if (typeof value === "string") {
    assertWellFormedString(value);
    return;
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new ContinuousChapterJournalError("请求包含无法安全保存的数字，未发送连续编辑操作。");
    return;
  }
  if (typeof value !== "object") throw new ContinuousChapterJournalError("请求包含无法安全保存的数据，未发送连续编辑操作。");
  if (seen.has(value)) throw new ContinuousChapterJournalError("请求包含循环引用，未发送连续编辑操作。");
  seen.add(value);
  if (Array.isArray(value)) {
    value.forEach((item) => assertFiniteJson(item, seen, depth + 1));
  } else {
    const prototype = Object.getPrototypeOf(value);
    if (prototype !== Object.prototype && prototype !== null) {
      throw new ContinuousChapterJournalError("请求包含非普通 JSON 对象，未发送连续编辑操作。");
    }
    for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
      assertWellFormedString(key);
      assertFiniteJson(item, seen, depth + 1);
    }
  }
  seen.delete(value);
}

function assertWellFormedString(value: string): void {
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code >= 0xd800 && code <= 0xdbff) {
      if (index + 1 >= value.length) throw new ContinuousChapterJournalError("标识包含无法编码的 UTF-16 字符，未发送连续编辑操作。");
      const next = value.charCodeAt(index + 1);
      if (next < 0xdc00 || next > 0xdfff) throw new ContinuousChapterJournalError("标识包含无法编码的 UTF-16 字符，未发送连续编辑操作。");
      index += 1;
    } else if (code >= 0xdc00 && code <= 0xdfff) {
      throw new ContinuousChapterJournalError("标识包含无法编码的 UTF-16 字符，未发送连续编辑操作。");
    }
  }
}

function stableJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  if (typeof value === "object" && value !== null) {
    const entries = Object.entries(value as Record<string, unknown>).sort(([left], [right]) => compareCodePoints(left, right));
    return `{${entries.map(([key, item]) => `${JSON.stringify(key)}:${stableJson(item)}`).join(",")}}`;
  }
  const serialized = JSON.stringify(value);
  if (serialized === undefined) throw new ContinuousChapterJournalError("请求无法稳定序列化。");
  return serialized;
}

function compareCodePoints(left: string, right: string): number {
  const leftPoints = Array.from(left, (character) => character.codePointAt(0) ?? 0);
  const rightPoints = Array.from(right, (character) => character.codePointAt(0) ?? 0);
  const sharedLength = Math.min(leftPoints.length, rightPoints.length);
  for (let index = 0; index < sharedLength; index += 1) {
    const leftPoint = leftPoints[index] ?? 0;
    const rightPoint = rightPoints[index] ?? 0;
    if (leftPoint !== rightPoint) return leftPoint - rightPoint;
  }
  return leftPoints.length - rightPoints.length;
}

async function sha256(value: string): Promise<string> {
  if (globalThis.crypto?.subtle === undefined) throw new ContinuousChapterJournalError("当前浏览器不支持安全摘要，操作未发送。");
  const digest = await globalThis.crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function freezeContinuousOperationRequest(body: unknown): Promise<{
  readonly bodyJson: string;
  readonly bodySha256: string;
}> {
  assertFiniteJson(body);
  const bodyJson = JSON.stringify(body);
  if (typeof bodyJson !== "string") throw new ContinuousChapterJournalError("原请求无法转换为 JSON，操作未发送。");
  if (new TextEncoder().encode(bodyJson).byteLength > MAX_CONTINUOUS_CHAPTER_JOURNAL_BYTES) {
    throw new ContinuousChapterJournalError("原稿与请求超过本地记录容量，操作未发送；未截断内容。");
  }
  const roundTrip = JSON.parse(bodyJson) as unknown;
  assertFiniteJson(roundTrip);
  return { bodyJson, bodySha256: await sha256(bodyJson) };
}

export async function digestContinuousSource(requestBodySha256: string): Promise<string> {
  return continuousSourceSnapshotBinding(requestBodySha256);
}

export async function readContinuousChapterJournal(
  storage: StorageLike,
  key: string,
): Promise<ContinuousChapterOperationEnvelope | null> {
  const raw = readJournalStorageValue(storage, key);
  return readContinuousChapterJournalValue(raw);
}

function readJournalStorageValue(storage: StorageLike, key: string): string | null {
  try {
    return storage.getItem(key);
  } catch {
    throw new ContinuousChapterJournalStorageError();
  }
}

async function readContinuousChapterJournalValue(raw: string | null): Promise<ContinuousChapterOperationEnvelope | null> {
  if (raw === null) return null;
  if (new TextEncoder().encode(raw).byteLength > MAX_CONTINUOUS_CHAPTER_JOURNAL_BYTES) {
    throw new ContinuousChapterJournalError("本地原操作记录超过安全读取上限；记录已保留，未启动新操作。");
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    throw new ContinuousChapterJournalError("本地原操作记录格式损坏；记录已保留，未启动新操作。");
  }
  const journal = validateJournalShape(parsed);
  const { integrity_sha256: _integrity, ...integrityBody } = journal;
  if (await sha256(stableJson(integrityBody)) !== journal.integrity_sha256) {
    throw new ContinuousChapterJournalError("本地原稿或操作状态完整性校验失败；记录已保留，未启动新操作。");
  }
  await validateJournalContents(journal);
  return journal;
}

export function continuousChapterJournalMatchesScope(
  journal: ContinuousChapterOperationEnvelope,
  scope: ContinuousChapterOperationScope,
): boolean {
  return journal.origin.actor_id === scope.actorId
    && journal.origin.api_base_url === (normalizeApiBaseUrl(scope.apiBaseUrl) ?? "")
    && journal.origin.series_id === scope.seriesId
    && journal.origin.chapter_id === scope.chapterId;
}

export function continuousOperationReceiptMatchesJournal(
  receipt: ContinuousChapterOperationReceiptIdentity,
  journal: ContinuousChapterOperationEnvelope,
  actorId: string,
): boolean {
  if (!(receipt.operation_id === journal.operation.operation_id
    && receipt.owner_id === actorId
    && receipt.owner_id === journal.origin.actor_id
    && receipt.editor_session_id === journal.origin.editor_session_id
    && receipt.series_id === journal.origin.series_id
    && receipt.chapter_id === journal.origin.chapter_id
    && receipt.command_kind === journal.operation.kind
    && receipt.request_body_sha256 === journal.operation.request_body_sha256
    && receipt.source_snapshot_digest === journal.operation.source_snapshot_digest
    && receipt.target_draft_id === journal.operation.target_draft_id
    && receipt.target_manifest_digest === journal.operation.target_manifest_digest)) return false;
  const identity = receipt.result_identity;
  if (journal.operation.kind === "save-chapter") {
    return Array.isArray(identity.draft_frame_ids)
      && jsonValuesEqual(identity.draft_frame_ids, journal.operation.draft_frame_ids)
      && (receipt.outcome === "rejected" || Array.isArray(identity.frame_storyboard_ids));
  }
  return identity.asset_type === journal.operation.kind.slice("create-".length)
    && identity.target_draft_id === journal.operation.target_draft_id
    && Array.isArray(identity.draft_frame_ids)
    && jsonValuesEqual(identity.draft_frame_ids, journal.operation.draft_frame_ids);
}

export async function writeContinuousChapterJournal(
  storage: StorageLike,
  key: string,
  journal: NewContinuousChapterOperationEnvelope | ContinuousChapterOperationEnvelope,
  lease: ContinuousChapterOperationLockLease,
  expectedCurrentIntegrity: string | null,
): Promise<ContinuousChapterOperationEnvelope> {
  assertLeaseForKey(lease, key);
  const observedRaw = readJournalStorageValue(storage, key);
  const current = await readContinuousChapterJournalValue(observedRaw);
  lease.assertActive();
  if (expectedCurrentIntegrity === null) {
    if (current !== null) throw new ContinuousChapterJournalConflictError();
  } else if (current === null || current.integrity_sha256 !== expectedCurrentIntegrity) {
    throw new ContinuousChapterJournalConflictError();
  }
  if (current !== null && current.phase !== "captured_not_dispatched"
    && !sameOriginalOperation(current, journal)) {
    throw new ContinuousChapterJournalConflictError("原操作可能已发出；必须保留并更新原操作记录，不能替换为新请求。");
  }

  let sealed: ContinuousChapterOperationEnvelope;
  let serialized: string;
  try {
    assertFiniteJson(journal);
    const { integrity_sha256: _ignored, ...integrityBody } = journal;
    sealed = {
      ...integrityBody,
      integrity_sha256: await sha256(stableJson(integrityBody)),
    };
    validateJournalShape(sealed);
    await validateJournalContents(sealed);
    serialized = JSON.stringify(sealed);
    if (new TextEncoder().encode(serialized).byteLength > MAX_CONTINUOUS_CHAPTER_JOURNAL_BYTES) {
      throw journalStorageCapacityError(current, journal.phase);
    }
  } catch (error) {
    if ((current !== null && current.phase !== "captured_not_dispatched")
      || journal.phase !== "captured_not_dispatched") {
      throw postDispatchJournalUpdateError(error);
    }
    throw error;
  }
  lease.assertActive();
  // Web Locks serialize cooperating tabs; this reread only rejects stale owners.
  if (readJournalStorageValue(storage, key) !== observedRaw) {
    throw new ContinuousChapterJournalConflictError("本地原操作记录在核验期间发生变化；当前记录保持原样。");
  }
  lease.assertActive();
  try {
    storage.setItem(key, serialized);
    if (storage.getItem(key) !== serialized) throw new Error("journal readback mismatch");
  } catch {
    throw new ContinuousChapterJournalStorageError();
  }
  const verified = await readContinuousChapterJournal(storage, key);
  if (verified?.integrity_sha256 !== sealed.integrity_sha256
    || verified.operation.operation_id !== sealed.operation.operation_id
    || verified.operation.request_body_json !== sealed.operation.request_body_json) {
    throw new ContinuousChapterJournalStorageError("原操作记录回读不一致；本次请求可能已经发出，请先核对原操作。");
  }
  return verified;
}

export async function resolveContinuousChapterJournal(
  storage: StorageLike,
  key: string,
  journal: ContinuousChapterOperationEnvelope,
  lease: ContinuousChapterOperationLockLease,
): Promise<void> {
  assertLeaseForKey(lease, key);
  const current = await readContinuousChapterJournal(storage, key);
  if (current === null || current.integrity_sha256 !== journal.integrity_sha256) {
    throw new ContinuousChapterJournalConflictError();
  }
  if (!isJournalTerminalAndReviewed(current)) {
    throw new ContinuousChapterJournalError("只有原回执与当前目标均已核对后，才能解除本章写入冻结。");
  }

  const tombstone: ContinuousChapterOperationEnvelope = {
    ...current,
    phase: "resolved_reviewed",
  };
  const written = await writeContinuousChapterJournal(storage, key, tombstone, lease, current.integrity_sha256);
  const tombstoneRaw = JSON.stringify(written);
  const currentRaw = readJournalStorageValue(storage, key);
  if (currentRaw !== tombstoneRaw) throw new ContinuousChapterJournalConflictError();
  lease.assertActive();
  if (readJournalStorageValue(storage, key) !== tombstoneRaw) throw new ContinuousChapterJournalConflictError();
  lease.assertActive();
  try {
    storage.removeItem(key);
    if (storage.getItem(key) !== null) throw new Error("journal remove readback mismatch");
  } catch {
    throw new ContinuousChapterJournalStorageError("无法清除已审阅的原操作记录；原记录仍保留。");
  }
}

export function createContinuousOperationJournalEnvelope(input: {
  readonly scope: ContinuousChapterOperationScope;
  readonly editorSessionId: string;
  readonly documentId: string;
  readonly authSessionGeneration: number;
  readonly snapshot: ContinuousChapterDraftSnapshot;
  readonly frames: readonly ContinuousDraftFrame[];
  readonly selectedDraftId: string | null;
  readonly operationId: string;
  readonly kind: ContinuousChapterOperationKind;
  readonly targetDraftId: string | null;
  readonly draftFrameIds: readonly string[];
  readonly bodyJson: string;
  readonly bodySha256: string;
  readonly sourceSnapshotDigest: string;
  readonly targetManifestDigest: string;
  readonly phase: ContinuousChapterOperationPhase;
  readonly route: string;
}): NewContinuousChapterOperationEnvelope {
  return {
    schema_version: CONTINUOUS_CHAPTER_OPERATION_JOURNAL_VERSION,
    integrity_sha256: "",
    origin: {
      actor_id: input.scope.actorId,
      api_base_url: normalizeApiBaseUrl(input.scope.apiBaseUrl) ?? "",
      series_id: input.scope.seriesId,
      chapter_id: input.scope.chapterId,
      editor_session_id: input.editorSessionId,
      document_id: input.documentId,
      auth_session_generation: input.authSessionGeneration,
    },
    created_at: new Date().toISOString(),
    phase: input.phase,
    snapshot: {
      chapter: input.snapshot.chapter,
      frames: input.frames,
      expected_storyboards: input.snapshot.expectedStoryboards,
      lock_identity: input.snapshot.lockIdentity,
    },
    selected_draft_id: input.selectedDraftId,
    operation: {
      operation_id: input.operationId,
      kind: input.kind,
      target_draft_id: input.targetDraftId,
      draft_frame_ids: input.draftFrameIds,
      request_body_json: input.bodyJson,
      request_body_sha256: input.bodySha256,
      source_snapshot_digest: input.sourceSnapshotDigest,
      target_manifest_digest: input.targetManifestDigest,
      route: input.route,
    },
    observation: { message: null, receipt: null, result: null, target_status: "unchecked" },
  };
}

function validateJournalShape(value: unknown): ContinuousChapterOperationEnvelope {
  assertFiniteJson(value);
  if (!isRecord(value) || value.schema_version !== CONTINUOUS_CHAPTER_OPERATION_JOURNAL_VERSION
    || !isSha256(value.integrity_sha256) || !isRecord(value.origin) || !isRecord(value.snapshot)
    || !isRecord(value.operation) || !isRecord(value.observation)) throw invalidJournal();
  const origin = value.origin;
  const snapshot = value.snapshot;
  const operation = value.operation;
  const observation = value.observation;
  if (!isString(origin.actor_id) || origin.actor_id === "" || !isString(origin.api_base_url)
    || normalizeApiBaseUrl(origin.api_base_url) !== origin.api_base_url
    || !isString(origin.series_id) || origin.series_id === "" || !isString(origin.chapter_id) || origin.chapter_id === ""
    || !isUuid(origin.editor_session_id) || !isUuid(origin.document_id)
    || !Number.isSafeInteger(origin.auth_session_generation) || (origin.auth_session_generation as number) < 0
    || !isValidTimestamp(value.created_at) || !isOperationPhase(value.phase)
    || !isRecord(snapshot.chapter) || !Array.isArray(snapshot.frames) || !Array.isArray(snapshot.expected_storyboards)
    || !(snapshot.lock_identity === null || isPlainRecord(snapshot.lock_identity))
    || !isString(operation.operation_id) || !isUuid(operation.operation_id) || !isOperationKind(operation.kind)
    || !(operation.target_draft_id === null || (isString(operation.target_draft_id) && operation.target_draft_id !== ""))
    || !Array.isArray(operation.draft_frame_ids) || operation.draft_frame_ids.some((item) => !isString(item))
    || !isString(operation.request_body_json) || !isSha256(operation.request_body_sha256)
    || !isSha256(operation.source_snapshot_digest) || !isSha256(operation.target_manifest_digest)
    || !isString(operation.route) || !(value.selected_draft_id === null || isString(value.selected_draft_id))
    || !(observation.message === null || isString(observation.message))
    || !(observation.receipt === null || isPlainRecord(observation.receipt))
    || !(observation.result === null || isPlainRecord(observation.result))
    || !isTargetStatus(observation.target_status)) throw invalidJournal();
  return value as unknown as ContinuousChapterOperationEnvelope;
}

async function validateJournalContents(journal: ContinuousChapterOperationEnvelope): Promise<void> {
  const { origin, snapshot, operation, observation } = journal;
  if (snapshot.chapter.id !== origin.chapter_id || snapshot.chapter.series_id !== origin.series_id
    || !Number.isSafeInteger(snapshot.chapter.order)
    || !(snapshot.chapter.content === null || (Array.isArray(snapshot.chapter.content)
      && snapshot.chapter.content.every(isPlainRecord)))) throw invalidJournal("本地原章节快照与章节身份不一致；记录已保留。");
  const sourceContent = snapshot.chapter.content ?? [];
  const seenFrameIds = new Set<string>();
  const seenSourceIndexes = new Set<number>();
  const storyboardIds = new Set<string>();
  for (const asset of snapshot.expected_storyboards) {
    if (!isPlainRecord(asset) || !isString(asset.id) || asset.id === ""
      || asset.series_id !== origin.series_id || asset.chapter_id !== origin.chapter_id
      || !Number.isSafeInteger(asset.frame_index) || (asset.frame_index as number) < 0
      || storyboardIds.has(asset.id)) throw invalidJournal("本地分镜基线身份不唯一；记录已保留。");
    assertWellFormedString(asset.id);
    storyboardIds.add(asset.id);
  }
  for (const frame of snapshot.frames) {
    if (!isPlainRecord(frame) || !isString(frame.draftId) || frame.draftId === ""
      || !(frame.sourceIndex === null || Number.isSafeInteger(frame.sourceIndex))
      || !(frame.storyboardId === null || (isString(frame.storyboardId) && frame.storyboardId !== ""))
      || !(frame.originalFrame === null || isPlainRecord(frame.originalFrame)) || !isString(frame.text)
      || !isStringArray(frame.characterIds) || !isStringArray(frame.sceneIds) || !isStringArray(frame.propIds)
      || seenFrameIds.has(frame.draftId)) throw invalidJournal("本地原稿分段字段或 ID 无法完整核对；记录已保留。");
    assertWellFormedString(frame.draftId);
    seenFrameIds.add(frame.draftId);
    if (frame.sourceIndex === null) {
      if (frame.originalFrame !== null || frame.storyboardId !== null) throw invalidJournal("新增分段误带原镜头身份；记录已保留。");
    } else {
      const sourceIndex = frame.sourceIndex as number;
      if (sourceIndex < 0 || sourceIndex >= sourceContent.length || seenSourceIndexes.has(sourceIndex)
        || frame.originalFrame === null || !jsonValuesEqual(frame.originalFrame, sourceContent[sourceIndex])) {
        throw invalidJournal("原稿分段来源映射与完整原镜头快照不一致；记录已保留。");
      }
      seenSourceIndexes.add(sourceIndex);
      const originalStoryboardId = readOriginalStoryboardId(sourceContent[sourceIndex]);
      if (originalStoryboardId !== frame.storyboardId) throw invalidJournal("原稿分段分镜身份与原镜头不一致；记录已保留。");
    }
    if (frame.storyboardId !== null && !storyboardIds.has(frame.storyboardId)) throw invalidJournal("原稿引用的分镜不在已记录目录基线中；记录已保留。");
  }
  if (operation.draft_frame_ids.length !== snapshot.frames.length
    || operation.draft_frame_ids.some((id, index) => id !== snapshot.frames[index]?.draftId)
    || new Set(operation.draft_frame_ids).size !== operation.draft_frame_ids.length
    || (journal.selected_draft_id !== null && !seenFrameIds.has(journal.selected_draft_id))) {
    throw invalidJournal("原请求分段清单与完整原稿不一致；记录已保留。");
  }
  if (snapshot.lock_identity !== null
    && (!isString(snapshot.lock_identity.user_id) || snapshot.lock_identity.user_id !== origin.actor_id
      || !isString(snapshot.lock_identity.acquired_at))) throw invalidJournal("原编辑锁身份与操作账号不一致；记录已保留。");

  const expectedRoute = continuousChapterOperationRoute(origin.chapter_id, operation.operation_id);
  if (operation.route !== expectedRoute) throw invalidJournal("原操作路由与绑定章节或 UUID 不一致；记录已保留。");
  const bodySha = await sha256(operation.request_body_json);
  if (bodySha !== operation.request_body_sha256
    || await continuousSourceSnapshotBinding(bodySha) !== operation.source_snapshot_digest
    || await continuousTargetManifestDigest(operation.draft_frame_ids) !== operation.target_manifest_digest) {
    throw invalidJournal("本地原请求摘要或来源绑定不匹配；记录已保留，未启动新操作。");
  }
  let body: unknown;
  try {
    body = JSON.parse(operation.request_body_json);
  } catch {
    throw invalidJournal("本地原请求 JSON 无法解析；记录已保留。");
  }
  if (!isPlainRecord(body) || body.protocol_version !== 1 || body.editor_session_id !== origin.editor_session_id
    || !isPlainRecord(body.command) || body.command.kind !== operation.kind) throw invalidJournal("原请求协议、会话或操作类型与记录不一致；记录已保留。");
  const command = body.command;
  if (!isStringArray(command.draft_frame_ids)
    || !jsonValuesEqual(command.draft_frame_ids, operation.draft_frame_ids)) throw invalidJournal("原请求分段清单与记录不一致；记录已保留。");

  const built = buildGuardedChapterUpdate({
    chapter: snapshot.chapter,
    frames: snapshot.frames,
    expectedStoryboards: snapshot.expected_storyboards,
    lockIdentity: snapshot.lock_identity,
  }, snapshot.frames);
  if (operation.kind === "save-chapter") {
    if (operation.target_draft_id !== null || !isRecord(command.chapter_edit_guard)
      || !Array.isArray(command.content)
      || !jsonValuesEqual(command.content, built.content)
      || !jsonValuesEqual(command.chapter_edit_guard, built.chapter_edit_guard)) {
      throw invalidJournal("整章保存请求与原稿来源映射不一致；记录已保留。");
    }
  } else {
    if (operation.target_draft_id === null || operation.target_draft_id !== command.target_draft_id
      || journal.selected_draft_id !== operation.target_draft_id
      || !seenFrameIds.has(operation.target_draft_id) || !isPlainRecord(command.input)
      || !isRecord(command.source_context)
      || !jsonValuesEqual(command.source_context, built.chapter_edit_guard)) {
      throw invalidJournal("素材创建请求与原稿目标或来源快照不一致；记录已保留。");
    }
  }

  if (observation.receipt !== null) {
    const receipt = observation.receipt as unknown as ContinuousChapterOperationReceipt;
    await parseContinuousChapterOperationLookup({ receipt }, 200);
    if (!continuousOperationReceiptMatchesJournal(receipt, journal, origin.actor_id)) {
      throw invalidJournal("服务器回执与冻结的原操作身份不一致；记录已保留。");
    }
    if (observation.result !== null && !jsonValuesEqual(observation.result, receipt.result)) {
      throw invalidJournal("本地原操作观察结果与服务器回执不一致；记录已保留。");
    }
    if (journal.phase === "receipt_committed_pending_target_read" && receipt.outcome !== "committed") {
      throw invalidJournal("本地成功回执阶段与服务器回执不一致；记录已保留。");
    }
    if (journal.phase === "receipt_rejected" && receipt.outcome !== "rejected") {
      throw invalidJournal("本地拒绝回执阶段与服务器回执不一致；记录已保留。");
    }
    if (journal.phase === "resolved_reviewed" && (!isJournalTerminalAndReviewed(journal))) {
      throw invalidJournal("本地结案状态缺少终态回执或目标核对；记录已保留。");
    }
  } else if (observation.result !== null || observation.target_status !== "unchecked"
    || journal.phase === "acknowledged_pending_receipt" || journal.phase === "receipt_committed_pending_target_read"
    || journal.phase === "receipt_rejected" || journal.phase === "resolved_reviewed") {
    throw invalidJournal("本地原操作观察缺少可核验的服务器回执；记录已保留。");
  }
}

function readOriginalStoryboardId(frame: unknown): string | null {
  if (!isPlainRecord(frame)) return null;
  const storyboard = frame.storyboard;
  if (storyboard === undefined || (Array.isArray(storyboard) && storyboard.length === 0)) return null;
  if (Array.isArray(storyboard) && storyboard.length === 1 && typeof storyboard[0] === "string") return storyboard[0];
  throw invalidJournal("原镜头分镜来源无法唯一核对；记录已保留。");
}

function isJournalTerminalAndReviewed(journal: ContinuousChapterOperationEnvelope): boolean {
  const receipt = journal.observation.receipt;
  return receipt !== null
    && continuousOperationReceiptMatchesJournal(receipt, journal, journal.origin.actor_id)
    && journal.observation.target_status === "matches"
    && (journal.phase === "receipt_committed_pending_target_read" || journal.phase === "receipt_rejected" || journal.phase === "resolved_reviewed");
}

function assertLeaseForKey(lease: ContinuousChapterOperationLockLease, key: string): void {
  lease.assertActive();
  if (lease.storageKey !== key) throw new ContinuousChapterJournalLockError("本地操作锁不属于当前章节记录，未修改原记录。");
}

function sameOriginalOperation(
  current: ContinuousChapterOperationEnvelope,
  next: NewContinuousChapterOperationEnvelope | ContinuousChapterOperationEnvelope,
): boolean {
  return current.schema_version === next.schema_version
    && current.created_at === next.created_at
    && current.selected_draft_id === next.selected_draft_id
    && stableJson(current.origin) === stableJson(next.origin)
    && stableJson(current.snapshot) === stableJson(next.snapshot)
    && stableJson(current.operation) === stableJson(next.operation);
}

function journalStorageCapacityError(
  current: ContinuousChapterOperationEnvelope | null,
  nextPhase: ContinuousChapterOperationPhase,
): ContinuousChapterJournalStorageError {
  if ((current !== null && current.phase !== "captured_not_dispatched")
    || nextPhase !== "captured_not_dispatched") {
    return new ContinuousChapterJournalStorageError("本地原操作记录超过安全容量；原操作状态无法安全确认，记录保持原样。");
  }
  return new ContinuousChapterJournalStorageError("原稿与请求超过本地记录容量；操作未发送，内容未截断。");
}

function postDispatchJournalUpdateError(_error: unknown): ContinuousChapterJournalError {
  return new ContinuousChapterJournalError("原操作记录更新未完成；已有记录保持原样，原请求可能已经发出，请先核对原操作。");
}

function invalidJournal(message = "本地原操作记录字段无法完整核对；记录已保留，未启动新操作。"): ContinuousChapterJournalError {
  return new ContinuousChapterJournalError(message);
}

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return isPlainRecord(value);
}

function jsonValuesEqual(left: unknown, right: unknown): boolean {
  return stableJson(left) === stableJson(right);
}

function isString(value: unknown): value is string {
  return typeof value === "string";
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isUuid(value: unknown): value is string {
  return typeof value === "string"
    && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u.test(value);
}

function isSha256(value: unknown): value is string {
  return typeof value === "string" && /^[0-9a-f]{64}$/u.test(value);
}

function isOperationKind(value: unknown): value is ContinuousChapterOperationKind {
  return value === "save-chapter" || value === "create-character" || value === "create-scene" || value === "create-prop";
}

function isOperationPhase(value: unknown): value is ContinuousChapterOperationPhase {
  return value === "captured_not_dispatched" || value === "dispatch_possible" || value === "unknown"
    || value === "acknowledged_pending_receipt" || value === "receipt_committed_pending_target_read"
    || value === "receipt_rejected" || value === "resolved_reviewed";
}

function isTargetStatus(value: unknown): value is ContinuousChapterOperationEnvelope["observation"]["target_status"] {
  return value === "unchecked" || value === "matches" || value === "changed" || value === "unavailable";
}

function isValidTimestamp(value: unknown): value is string {
  return typeof value === "string"
    && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?$/u.test(value)
    && Number.isFinite(Date.parse(value));
}
