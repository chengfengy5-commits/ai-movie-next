import { InvalidResponseError } from "./contracts";

export type ContinuousChapterOperationKind =
  | "save-chapter"
  | "create-character"
  | "create-scene"
  | "create-prop";

export type ContinuousChapterOperationCommand =
  | {
      readonly kind: "save-chapter";
      readonly content: readonly Record<string, unknown>[];
      readonly chapter_edit_guard: Record<string, unknown>;
      readonly draft_frame_ids: readonly string[];
    }
  | {
      readonly kind: "create-character" | "create-scene" | "create-prop";
      readonly input: Record<string, unknown>;
      readonly target_draft_id: string;
      readonly source_context: Record<string, unknown>;
      readonly draft_frame_ids: readonly string[];
    };

export interface ContinuousChapterOperationRequest {
  readonly protocol_version: 1;
  readonly editor_session_id: string;
  readonly command: ContinuousChapterOperationCommand;
}

export interface ContinuousChapterOperationReceipt {
  readonly receipt_version: 1;
  readonly operation_id: string;
  readonly owner_id: string;
  readonly editor_session_id: string;
  readonly series_id: string;
  readonly chapter_id: string;
  readonly command_kind: ContinuousChapterOperationKind;
  readonly request_body_sha256: string;
  readonly source_snapshot_digest: string;
  readonly target_draft_id: string | null;
  readonly target_manifest_digest: string;
  readonly outcome: "committed" | "rejected";
  readonly http_status: number;
  readonly result_identity: Record<string, unknown>;
  readonly result: Record<string, unknown>;
  readonly canonical_response_json: string;
  readonly result_digest: string;
  readonly committed_at: string;
}

export interface ContinuousChapterOperationResponse {
  readonly receipt: ContinuousChapterOperationReceipt;
  readonly result?: Record<string, unknown>;
  readonly error?: Record<string, unknown>;
}

export interface ContinuousChapterOperationsPort {
  submit(
    chapterId: string,
    operationId: string,
    bodyJson: string,
    signal: AbortSignal,
  ): Promise<ContinuousChapterOperationResponse>;
  lookup(
    chapterId: string,
    operationId: string,
    signal: AbortSignal,
  ): Promise<{ readonly receipt: ContinuousChapterOperationReceipt }>;
}

export async function continuousSourceSnapshotBinding(requestBodySha256: string): Promise<string> {
  if (!isSha256(requestBodySha256)) throw new InvalidResponseError("连续编辑原请求摘要格式无效。");
  return sha256Utf8(`haoai-continuous-source-v1\u0000${requestBodySha256}`);
}

export async function continuousOperationResultDigest(canonicalResponseJson: string): Promise<string> {
  if (!isWellFormedString(canonicalResponseJson)) throw new InvalidResponseError("连续编辑原操作结果包含无法编码的 UTF-16 字符。");
  return sha256Utf8(canonicalResponseJson);
}

export async function continuousTargetManifestDigest(draftFrameIds: readonly string[]): Promise<string> {
  if (!isUniqueStringArray(draftFrameIds) || draftFrameIds.some((id) => !isWellFormedString(id))) {
    throw new InvalidResponseError("连续编辑原操作镜头清单格式无效。");
  }
  return sha256Utf8(JSON.stringify(draftFrameIds));
}

export async function parseContinuousChapterOperationResponse(
  value: unknown,
  actualHttpStatus: number,
): Promise<ContinuousChapterOperationResponse> {
  assertFiniteJson(value);
  if (!isRecord(value) || !isContinuousChapterOperationReceipt(value.receipt)) {
    throw new InvalidResponseError("连续编辑原操作回执格式无效。");
  }
  const receipt = value.receipt;
  await validateReceipt(receipt);
  if (receipt.http_status !== actualHttpStatus) {
    throw new InvalidResponseError("连续编辑原操作回执状态与实际 HTTP 响应不一致。");
  }
  if (receipt.outcome === "committed") {
    if (actualHttpStatus !== 200 || !isRecord(value.result) || !jsonValuesEqual(value.result, receipt.result)) {
      throw new InvalidResponseError("连续编辑成功响应与原回执结果不一致。");
    }
    if (value.error !== undefined) throw new InvalidResponseError("连续编辑成功响应包含拒绝信息。");
  } else {
    if (actualHttpStatus < 400 || actualHttpStatus >= 500
      || !isRecord(value.error) || !jsonValuesEqual(value.error, receipt.result)) {
      throw new InvalidResponseError("连续编辑拒绝响应与原回执结果不一致。");
    }
    if (value.result !== undefined) throw new InvalidResponseError("连续编辑拒绝响应包含成功结果。");
  }
  return value as unknown as ContinuousChapterOperationResponse;
}

export async function parseContinuousChapterOperationLookup(
  value: unknown,
  actualHttpStatus: number,
): Promise<{ readonly receipt: ContinuousChapterOperationReceipt }> {
  assertFiniteJson(value);
  if (actualHttpStatus !== 200 || !isRecord(value) || !isContinuousChapterOperationReceipt(value.receipt)) {
    throw new InvalidResponseError("连续编辑原操作查询回执格式或 HTTP 状态无效。");
  }
  await validateReceipt(value.receipt);
  return value as { readonly receipt: ContinuousChapterOperationReceipt };
}

async function validateReceipt(receipt: ContinuousChapterOperationReceipt): Promise<void> {
  if (receipt.outcome === "committed" ? receipt.http_status !== 200 : receipt.http_status < 400 || receipt.http_status >= 500) {
    throw new InvalidResponseError("连续编辑原操作结果状态与回执类型不匹配。");
  }
  if (!isValidTimestamp(receipt.committed_at)) {
    throw new InvalidResponseError("连续编辑原操作回执时间无效。");
  }
  const identity = receipt.result_identity;
  if (identity.kind !== receipt.command_kind
    || identity.series_id !== receipt.series_id
    || identity.chapter_id !== receipt.chapter_id
    || identity.manifest_digest !== receipt.target_manifest_digest
    || identity.source_snapshot_digest !== receipt.source_snapshot_digest) {
    throw new InvalidResponseError("连续编辑原操作身份与回执摘要不一致。");
  }
  const draftFrameIds = identity.draft_frame_ids;
  if (!isUniqueStringArray(draftFrameIds)
    || await continuousTargetManifestDigest(draftFrameIds) !== receipt.target_manifest_digest) {
    throw new InvalidResponseError("连续编辑原操作镜头清单与目标摘要不一致。");
  }
  if (await continuousSourceSnapshotBinding(receipt.request_body_sha256) !== receipt.source_snapshot_digest) {
    throw new InvalidResponseError("连续编辑回执的来源绑定与原请求摘要不一致。");
  }
  if (receipt.command_kind === "save-chapter") {
    const frameIds = draftFrameIds;
    const storyboardIds = identity.frame_storyboard_ids;
    if (receipt.target_draft_id !== null || !isUniqueStringArray(frameIds)
      || (receipt.outcome === "committed" && !Array.isArray(storyboardIds))
      || (storyboardIds !== undefined && (!Array.isArray(storyboardIds)
        || storyboardIds.length !== frameIds.length
        || storyboardIds.some((value) => value !== null && !isStringArray(value))))) {
      throw new InvalidResponseError("整章保存回执缺少有效的镜头来源清单。");
    }
    if (receipt.outcome === "committed") {
      if (!Array.isArray(storyboardIds)) throw new InvalidResponseError("整章保存回执缺少镜头分镜身份清单。");
      const content = receipt.result.content;
      if (receipt.result.id !== receipt.chapter_id || receipt.result.series_id !== receipt.series_id
        || !Array.isArray(content) || content.length !== frameIds.length
        || content.some((frame, index) => !isRecord(frame)
          || !jsonValuesEqual(frame.storyboard, storyboardIds[index]))) {
        throw new InvalidResponseError("整章保存回执目标章节、剧集或镜头来源与原操作不一致。");
      }
    }
  } else {
    const expectedType = receipt.command_kind.slice("create-".length);
    if (receipt.target_draft_id === null || identity.target_draft_id !== receipt.target_draft_id
      || identity.asset_type !== expectedType || !draftFrameIds.includes(receipt.target_draft_id)) {
      throw new InvalidResponseError("素材创建回执与原镜头目标不一致。");
    }
    if (receipt.outcome === "committed"
      && (typeof identity.asset_id !== "string" || identity.asset_id === ""
        || receipt.result.id !== identity.asset_id || receipt.result.series_id !== receipt.series_id)) {
      throw new InvalidResponseError("素材创建回执的素材 ID 或所属剧集与原操作不一致。");
    }
  }

  let canonicalResult: unknown;
  try {
    canonicalResult = JSON.parse(receipt.canonical_response_json);
  } catch {
    throw new InvalidResponseError("连续编辑原操作结果字节无法解析。");
  }
  assertFiniteJson(canonicalResult);
  if (!isRecord(canonicalResult) || !jsonValuesEqual(canonicalResult, receipt.result)
    || await continuousOperationResultDigest(receipt.canonical_response_json) !== receipt.result_digest) {
    throw new InvalidResponseError("连续编辑原操作结果字节摘要或解析结果不一致。");
  }
}

function isContinuousChapterOperationReceipt(value: unknown): value is ContinuousChapterOperationReceipt {
  if (!isRecord(value)) return false;
  return value.receipt_version === 1
    && isUuid(value.operation_id)
    && typeof value.owner_id === "string" && value.owner_id !== ""
    && isUuid(value.editor_session_id)
    && typeof value.series_id === "string" && value.series_id !== ""
    && typeof value.chapter_id === "string" && value.chapter_id !== ""
    && isOperationKind(value.command_kind)
    && isSha256(value.request_body_sha256)
    && isSha256(value.source_snapshot_digest)
    && (value.target_draft_id === null || (typeof value.target_draft_id === "string" && value.target_draft_id !== ""))
    && isSha256(value.target_manifest_digest)
    && (value.outcome === "committed" || value.outcome === "rejected")
    && Number.isInteger(value.http_status) && (value.http_status as number) >= 200 && (value.http_status as number) < 500
    && isRecord(value.result_identity)
    && isRecord(value.result)
    && typeof value.canonical_response_json === "string"
    && isSha256(value.result_digest)
    && isString(value.committed_at);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isUuid(value: unknown): value is string {
  return typeof value === "string"
    && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u.test(value);
}

function isSha256(value: unknown): value is string {
  return typeof value === "string" && /^[0-9a-f]{64}$/u.test(value);
}

function isOperationKind(value: unknown): value is ContinuousChapterOperationKind {
  return value === "save-chapter" || value === "create-character"
    || value === "create-scene" || value === "create-prop";
}

function isValidTimestamp(value: string): boolean {
  // The existing database column returns naive UTC datetimes from Python's
  // isoformat(); new browser-side records may include an explicit zone.
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|([+-])(\d{2}):(\d{2}))?$/u.exec(value);
  if (match === null) return false;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const hour = Number(match[4]);
  const minute = Number(match[5]);
  const second = Number(match[6]);
  const leapYear = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const daysInMonth = [31, leapYear ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1];
  const offsetHour = match[8] === undefined ? 0 : Number(match[8]);
  const offsetMinute = match[9] === undefined ? 0 : Number(match[9]);
  return year >= 1 && month >= 1 && month <= 12 && day >= 1 && day <= (daysInMonth ?? 0)
    && hour <= 23 && minute <= 59 && second <= 59
    && offsetHour <= 23 && offsetMinute <= 59
    && Number.isFinite(Date.parse(value));
}

function isUniqueStringArray(value: unknown): value is string[] {
  return isStringArray(value) && value.every((item) => item !== "") && new Set(value).size === value.length;
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isWellFormedString(value: string): boolean {
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code >= 0xd800 && code <= 0xdbff) {
      if (index + 1 >= value.length) return false;
      const next = value.charCodeAt(index + 1);
      if (next < 0xdc00 || next > 0xdfff) return false;
      index += 1;
    } else if (code >= 0xdc00 && code <= 0xdfff) return false;
  }
  return true;
}

function assertFiniteJson(value: unknown, depth = 0): void {
  if (depth > 100) throw new InvalidResponseError("连续编辑原操作结果层级过深。");
  if (value === null || typeof value === "boolean") return;
  if (typeof value === "string") {
    if (!isWellFormedString(value)) throw new InvalidResponseError("连续编辑原操作结果包含无法编码的 UTF-16 字符。");
    return;
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new InvalidResponseError("连续编辑原操作结果包含非有限数字。");
    return;
  }
  if (Array.isArray(value)) {
    for (const item of value) assertFiniteJson(item, depth + 1);
    return;
  }
  if (isRecord(value)) {
    for (const [key, item] of Object.entries(value)) {
      if (!isWellFormedString(key)) throw new InvalidResponseError("连续编辑原操作结果字段包含无法编码的 UTF-16 字符。");
      assertFiniteJson(item, depth + 1);
    }
    return;
  }
  throw new InvalidResponseError("连续编辑原操作结果不是有限 JSON。");
}

function jsonValuesEqual(left: unknown, right: unknown): boolean {
  return stableJson(left) === stableJson(right);
}

function stableJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  if (isRecord(value)) {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableJson(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

async function sha256Utf8(value: string): Promise<string> {
  if (globalThis.crypto?.subtle === undefined) throw new InvalidResponseError("当前浏览器不支持回执摘要核验。");
  const digest = await globalThis.crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function isString(value: unknown): value is string {
  return typeof value === "string";
}
