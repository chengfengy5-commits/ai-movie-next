import { InvalidResponseError } from "./contracts";

export interface TaskReceipt {
  id: string;
  type: string;
  messageId: string | null;
  status: string;
  result: unknown;
  billingStatus: string | null;
  quotedAmount: number | null;
  progress: number | null;
  progressMessage: string | null;
  externalTaskId: string | null;
  externalProvider: string | null;
  createdAt: string | null;
  updatedAt: string | null;
  historyTruth: TaskHistoryTruth;
}

export type TaskHistorySourceStatus = "legacy" | "registered" | "unsupported" | "unknown";
export type TaskHistorySourceConflict = "intent_graph_mismatch" | "multiple_task_submission_intents";
export type TaskWorkerObservationStatus =
  | "active_lease"
  | "expired_lease"
  | "unclaimed"
  | "unknown"
  | "unsupported";
export type TaskProviderObservationStatus =
  | "verified_success"
  | "known_failure"
  | "partial"
  | "unknown"
  | "unsupported";

export interface TaskHistoryProviderUnit {
  operation: string | null;
  graphVersion: string | null;
  status: "verified_success" | "known_failure" | "unknown" | "unsupported";
  unitResultStatus: string;
  stepStatuses: string[];
  submissionStatuses: string[];
  providers: string[];
  providerEvidenceCount: number;
  timing: "timely" | "late" | "mixed" | "unknown";
  timelyEvidenceCount: number;
  lateEvidenceCount: number;
  unknownTimingCount: number;
  observedAt: string | null;
  hasUnverifiedFailedSubmission: boolean;
}

export interface TaskLocalDispositionObservation {
  status: "cancel_requested" | "deadline_elapsed_unsettled" | "policy_stopped" | "local_continuation";
  source: string;
  recordedAt: string | null;
  reason: string | null;
  phase: string | null;
}

export interface TaskHistoryTruth {
  version: 1;
  source: {
    status: TaskHistorySourceStatus;
    source: string;
    unitCount: number | null;
    registeredUnitCount: number;
    operations: string[];
    conflicts: TaskHistorySourceConflict[];
  };
  worker: {
    status: TaskWorkerObservationStatus;
    source: string;
    recoveryStatus: string;
    freshness: "within_lease" | "lease_expired" | "no_active_lease" | "unknown";
    leaseUntil: string | null;
    taskUpdatedAt: string | null;
    evaluatedAt: string;
  };
  provider: {
    status: TaskProviderObservationStatus;
    source: string;
    unitCount: number | null;
    verifiedUnitCount: number;
    knownFailureUnitCount: number;
    unknownUnitCount: number;
    unsupportedUnitCount: number;
    units: TaskHistoryProviderUnit[];
  };
  localDisposition: {
    status: "observed" | "none_recorded" | "unknown" | "unsupported";
    sourceCoverage: string[];
    unitCount: number | null;
    observations: TaskLocalDispositionObservation[];
  };
  billingScope: {
    unitCount: number | null;
    legacyQuoteScope: "first_unit" | "single_unit" | "task_record" | "unknown";
    aggregated: boolean;
  };
}

export interface TaskRequestRecord {
  id: string;
  type: string;
  status: string;
  modelName: string | null;
  creditCost: number | null;
  externalTaskId: string | null;
  externalProvider: string | null;
  createdAt: string | null;
  request: unknown;
}

type JsonRecord = Record<string, unknown>;

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requiredValue(record: JsonRecord, key: string, label: string): unknown {
  if (!Object.prototype.hasOwnProperty.call(record, key)) {
    throw new InvalidResponseError(label + "缺少 " + key + " 字段。");
  }
  return record[key];
}

function requiredString(record: JsonRecord, key: string, label: string): string {
  const value = requiredValue(record, key, label);
  if (typeof value !== "string" || value.length === 0) {
    throw new InvalidResponseError(label + "中的 " + key + " 字段格式无效。");
  }
  return value;
}

function nullableString(record: JsonRecord, key: string, label: string): string | null {
  const value = requiredValue(record, key, label);
  if (value !== null && typeof value !== "string") {
    throw new InvalidResponseError(label + "中的 " + key + " 字段格式无效。");
  }
  return value;
}

function nullableSafeInteger(record: JsonRecord, key: string, label: string): number | null {
  const value = requiredValue(record, key, label);
  if (value !== null && (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0)) {
    throw new InvalidResponseError(label + "中的 " + key + " 字段格式无效。");
  }
  return value;
}

function requiredSafeInteger(record: JsonRecord, key: string, label: string): number {
  const value = requiredValue(record, key, label);
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
    throw new InvalidResponseError(label + "中的 " + key + " 字段格式无效。");
  }
  return value;
}

function requiredTaskRecord(value: unknown, expectedTaskId: string, label: string): JsonRecord {
  if (!isRecord(value)) {
    throw new InvalidResponseError(label + "格式无效。");
  }
  const id = requiredString(value, "id", label);
  if (!expectedTaskId || id !== expectedTaskId) {
    throw new InvalidResponseError(label + "与当前任务不匹配。");
  }
  return value;
}

function stringList(value: unknown, label: string): string[] {
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
    throw new InvalidResponseError(label + "字段格式无效。");
  }
  return value as string[];
}

function booleanValue(record: JsonRecord, key: string, label: string): boolean {
  const value = requiredValue(record, key, label);
  if (typeof value !== "boolean") {
    throw new InvalidResponseError(label + "中的 " + key + " 字段格式无效。");
  }
  return value;
}

function enumValue<T extends string>(
  value: unknown,
  allowed: readonly T[],
  label: string,
): T {
  if (typeof value !== "string" || !allowed.includes(value as T)) {
    throw new InvalidResponseError(label + "字段格式无效。");
  }
  return value as T;
}

export function unsupportedTaskHistoryTruth(): TaskHistoryTruth {
  return {
    version: 1,
    source: {
      status: "unsupported",
      source: "unavailable",
      unitCount: null,
      registeredUnitCount: 0,
      operations: [],
      conflicts: [],
    },
    worker: {
      status: "unsupported",
      source: "unavailable",
      recoveryStatus: "unknown",
      freshness: "unknown",
      leaseUntil: null,
      taskUpdatedAt: null,
      evaluatedAt: "",
    },
    provider: {
      status: "unsupported",
      source: "unavailable",
      unitCount: null,
      verifiedUnitCount: 0,
      knownFailureUnitCount: 0,
      unknownUnitCount: 0,
      unsupportedUnitCount: 0,
      units: [],
    },
    localDisposition: {
      status: "unknown",
      sourceCoverage: [],
      unitCount: null,
      observations: [],
    },
    billingScope: {
      unitCount: null,
      legacyQuoteScope: "unknown",
      aggregated: false,
    },
  };
}

function parseTaskHistoryTruth(value: unknown): TaskHistoryTruth {
  if (!isRecord(value)) {
    throw new InvalidResponseError("任务历史事实记录格式无效。");
  }
  const label = "任务历史事实记录";
  const version = requiredValue(value, "version", label);
  if (typeof version !== "number" || !Number.isSafeInteger(version) || version < 1) {
    throw new InvalidResponseError(label + "版本字段格式无效。");
  }
  if (version !== 1) {
    return unsupportedTaskHistoryTruth();
  }

  const source = requiredValue(value, "source", label);
  const worker = requiredValue(value, "worker", label);
  const provider = requiredValue(value, "provider", label);
  const local = requiredValue(value, "local_disposition", label);
  const billingScope = requiredValue(value, "billing_scope", label);
  if (
    !isRecord(source)
    || !isRecord(worker)
    || !isRecord(provider)
    || !isRecord(local)
    || !isRecord(billingScope)
  ) {
    throw new InvalidResponseError(label + "字段格式无效。");
  }

  const providerUnitsRaw = requiredValue(provider, "units", label);
  if (!Array.isArray(providerUnitsRaw)) {
    throw new InvalidResponseError(label + "中的 units 字段格式无效。");
  }
  const units = providerUnitsRaw.map((item): TaskHistoryProviderUnit => {
    if (!isRecord(item)) throw new InvalidResponseError(label + "中的单元记录格式无效。");
    return {
      operation: nullableString(item, "operation", label),
      graphVersion: nullableString(item, "graph_version", label),
      status: enumValue(requiredValue(item, "status", label), ["verified_success", "known_failure", "unknown", "unsupported"], label),
      unitResultStatus: requiredString(item, "unit_result_status", label),
      stepStatuses: stringList(requiredValue(item, "step_statuses", label), label),
      submissionStatuses: stringList(requiredValue(item, "submission_statuses", label), label),
      providers: stringList(requiredValue(item, "providers", label), label),
      providerEvidenceCount: requiredSafeInteger(item, "provider_evidence_count", label),
      timing: enumValue(requiredValue(item, "timing", label), ["timely", "late", "mixed", "unknown"], label),
      timelyEvidenceCount: requiredSafeInteger(item, "timely_evidence_count", label),
      lateEvidenceCount: requiredSafeInteger(item, "late_evidence_count", label),
      unknownTimingCount: requiredSafeInteger(item, "unknown_timing_count", label),
      observedAt: nullableString(item, "observed_at", label),
      hasUnverifiedFailedSubmission: booleanValue(item, "has_unverified_failed_submission", label),
    };
  });

  const observationsRaw = requiredValue(local, "observations", label);
  if (!Array.isArray(observationsRaw)) {
    throw new InvalidResponseError(label + "中的 observations 字段格式无效。");
  }
  const observations = observationsRaw.map((item): TaskLocalDispositionObservation => {
    if (!isRecord(item)) throw new InvalidResponseError(label + "中的处置记录格式无效。");
    return {
      status: enumValue(
        requiredValue(item, "status", label),
        ["cancel_requested", "deadline_elapsed_unsettled", "policy_stopped", "local_continuation"],
        label,
      ),
      source: requiredString(item, "source", label),
      recordedAt: nullableString(item, "recorded_at", label),
      reason: nullableString(item, "reason", label),
      phase: nullableString(item, "phase", label),
    };
  });

  return {
    version: 1,
    source: {
      status: enumValue(requiredValue(source, "status", label), ["legacy", "registered", "unsupported", "unknown"], label),
      source: requiredString(source, "source", label),
      unitCount: nullableSafeInteger(source, "unit_count", label),
      registeredUnitCount: requiredSafeInteger(source, "registered_unit_count", label),
      operations: stringList(requiredValue(source, "operations", label), label),
      conflicts: stringList(requiredValue(source, "conflicts", label), label).map((conflict) => (
        enumValue(conflict, ["intent_graph_mismatch", "multiple_task_submission_intents"], label)
      )),
    },
    worker: {
      status: enumValue(requiredValue(worker, "status", label), ["active_lease", "expired_lease", "unclaimed", "unknown", "unsupported"], label),
      source: requiredString(worker, "source", label),
      recoveryStatus: requiredString(worker, "recovery_status", label),
      freshness: enumValue(requiredValue(worker, "freshness", label), ["within_lease", "lease_expired", "no_active_lease", "unknown"], label),
      leaseUntil: nullableString(worker, "lease_until", label),
      taskUpdatedAt: nullableString(worker, "task_updated_at", label),
      evaluatedAt: requiredString(worker, "evaluated_at", label),
    },
    provider: {
      status: enumValue(requiredValue(provider, "status", label), ["verified_success", "known_failure", "partial", "unknown", "unsupported"], label),
      source: requiredString(provider, "source", label),
      unitCount: nullableSafeInteger(provider, "unit_count", label),
      verifiedUnitCount: requiredSafeInteger(provider, "verified_unit_count", label),
      knownFailureUnitCount: requiredSafeInteger(provider, "known_failure_unit_count", label),
      unknownUnitCount: requiredSafeInteger(provider, "unknown_unit_count", label),
      unsupportedUnitCount: requiredSafeInteger(provider, "unsupported_unit_count", label),
      units,
    },
    localDisposition: {
      status: enumValue(requiredValue(local, "status", label), ["observed", "none_recorded", "unknown", "unsupported"], label),
      sourceCoverage: stringList(requiredValue(local, "source_coverage", label), label),
      unitCount: nullableSafeInteger(local, "unit_count", label),
      observations,
    },
    billingScope: {
      unitCount: nullableSafeInteger(billingScope, "unit_count", label),
      legacyQuoteScope: enumValue(requiredValue(billingScope, "legacy_quote_scope", label), ["first_unit", "single_unit", "task_record", "unknown"], label),
      aggregated: booleanValue(billingScope, "aggregated", label),
    },
  };
}

export function parseTaskReceipt(value: unknown, expectedTaskId: string): TaskReceipt {
  const record = requiredTaskRecord(value, expectedTaskId, "任务结果记录");
  const rawHistoryTruth = record.history_truth;
  return {
    id: requiredString(record, "id", "任务结果记录"),
    type: requiredString(record, "type", "任务结果记录"),
    messageId: nullableString(record, "message_id", "任务结果记录"),
    status: requiredString(record, "status", "任务结果记录"),
    result: requiredValue(record, "result", "任务结果记录"),
    billingStatus: nullableString(record, "billing_status", "任务结果记录"),
    quotedAmount: nullableSafeInteger(record, "quoted_amount", "任务结果记录"),
    progress: nullableSafeInteger(record, "progress", "任务结果记录"),
    progressMessage: nullableString(record, "progress_message", "任务结果记录"),
    externalTaskId: nullableString(record, "external_task_id", "任务结果记录"),
    externalProvider: nullableString(record, "external_provider", "任务结果记录"),
    createdAt: nullableString(record, "created_at", "任务结果记录"),
    updatedAt: nullableString(record, "updated_at", "任务结果记录"),
    historyTruth: rawHistoryTruth === undefined
      ? unsupportedTaskHistoryTruth()
      : parseTaskHistoryTruth(rawHistoryTruth),
  };
}

export function parseTaskRequestRecord(value: unknown, expectedTaskId: string): TaskRequestRecord {
  const record = requiredTaskRecord(value, expectedTaskId, "任务请求记录");
  return {
    id: requiredString(record, "id", "任务请求记录"),
    type: requiredString(record, "type", "任务请求记录"),
    status: requiredString(record, "status", "任务请求记录"),
    modelName: nullableString(record, "model_name", "任务请求记录"),
    creditCost: nullableSafeInteger(record, "credit_cost", "任务请求记录"),
    externalTaskId: nullableString(record, "external_task_id", "任务请求记录"),
    externalProvider: nullableString(record, "external_provider", "任务请求记录"),
    createdAt: nullableString(record, "created_at", "任务请求记录"),
    request: requiredValue(record, "request", "任务请求记录"),
  };
}

const MAX_DEPTH = 12;
const MAX_NODES = 8_000;
const MAX_COLLECTION_ITEMS = 400;
const MAX_STRING_LENGTH = 450_000;
const MAX_OUTPUT_LENGTH = 500_000;
const TRUNCATION_NOTE = "\n\n[内容因长度或复杂度限制已截断]";

interface ProjectionContext {
  nodes: number;
  truncated: boolean;
  seen: WeakSet<object>;
}

function keySegments(value: string): string[] {
  return value
    .replace(/([a-z0-9])([A-Z])/g, "$1_$2")
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter(Boolean);
}

function isSensitiveKey(value: string): boolean {
  const segments = keySegments(value);
  const compactKey = value.replace(/[^a-z0-9]/gi, "").toLowerCase();
  const sensitiveWords = new Set([
    "authorization",
    "auth",
    "token",
    "password",
    "passwd",
    "secret",
    "credential",
    "credentials",
    "cookie",
    "session",
    "signature",
    "sig",
    "expires",
  ]);
  if (segments.some((segment) => sensitiveWords.has(segment))) {
    return true;
  }
  return compactKey.endsWith("apikey") || (segments.includes("key")
    && segments.some((segment) => ["api", "access", "private", "secret"].includes(segment)));
}

function redactInlineSecrets(text: string): string {
  return text.replace(
    /(["']?)([a-z0-9_.-]{1,80})\1(\s*[:=]\s*)(?:"[^"]*"|'[^']*'|(?:Bearer|Basic)\s+[^\s,;}\]]+|[^\s,;}\]]+)/gi,
    (match, _quote: string, key: string, separator: string) => (
      isSensitiveKey(key) ? separator + "\"[已隐藏]\"" : match
    ),
  );
}

function redactUrlCredentials(text: string): string {
  return text.replace(/https?:\/\/[^\s<>"']+/gi, (matchedUrl) => {
    const trailingPunctuation = matchedUrl.match(/[),.;!?}\]]+$/)?.[0] ?? "";
    const candidate = trailingPunctuation.length === 0
      ? matchedUrl
      : matchedUrl.slice(0, -trailingPunctuation.length);
    try {
      const url = new URL(candidate);
      if (url.username !== "" || url.password !== "") {
        url.username = "";
        url.password = "";
      }

      const queryKeys = Array.from(url.searchParams.keys());
      for (const key of queryKeys) {
        if (isSensitiveKey(key)) {
          url.searchParams.set(key, "[已隐藏]");
        }
      }

      if (url.hash.startsWith("#?")) {
        const hashParams = new URLSearchParams(url.hash.slice(2));
        const hashKeys = Array.from(hashParams.keys());
        let changed = false;
        for (const key of hashKeys) {
          if (isSensitiveKey(key)) {
            hashParams.set(key, "[已隐藏]");
            changed = true;
          }
        }
        if (changed) {
          url.hash = "?" + hashParams.toString();
        }
      }
      return url.toString() + trailingPunctuation;
    } catch {
      const safeCandidate = candidate
        .replace(/(https?:\/\/)[^/@\s]+@/gi, "$1")
        .replace(/([?&#])([^=&#\s]+)=([^&#\s]*)/g, (_pair, separator: string, encodedKey: string, rawValue: string) => {
          let key = encodedKey;
          try {
            key = decodeURIComponent(encodedKey.replace(/\+/g, " "));
          } catch {
            // An invalid encoded key cannot be classified safely; retain the original key.
          }
          return separator + encodedKey + "=" + (isSensitiveKey(key) ? "[已隐藏]" : rawValue);
        });
      return redactInlineSecrets(safeCandidate) + trailingPunctuation;
    }
  });
}

function projectValue(
  value: unknown,
  context: ProjectionContext,
  depth: number,
): unknown {
  context.nodes += 1;
  if (context.nodes > MAX_NODES) {
    context.truncated = true;
    return "[内容过多，已截断]";
  }
  if (depth > MAX_DEPTH) {
    context.truncated = true;
    return "[嵌套过深，已截断]";
  }
  if (typeof value === "string") {
    if (value.length > MAX_STRING_LENGTH) {
      context.truncated = true;
      return redactUrlCredentials(redactInlineSecrets(value.slice(0, MAX_STRING_LENGTH)));
    }
    const parsed = parseJsonString(value);
    if (parsed !== value) {
      return projectValue(parsed, context, depth + 1);
    }
    return redactUrlCredentials(redactInlineSecrets(value));
  }
  if (value === null || typeof value === "boolean" || typeof value === "number") {
    return value;
  }
  if (typeof value !== "object") {
    return String(value);
  }
  if (context.seen.has(value)) {
    context.truncated = true;
    return "[重复引用]";
  }
  context.seen.add(value);

  if (Array.isArray(value)) {
    const projected = value
      .slice(0, MAX_COLLECTION_ITEMS)
      .map((item) => projectValue(item, context, depth + 1));
    if (value.length > MAX_COLLECTION_ITEMS) {
      context.truncated = true;
      projected.push("[其余项目已截断]");
    }
    return projected;
  }

  const entries = Object.entries(value);
  const projected: JsonRecord = {};
  for (const [key, item] of entries.slice(0, MAX_COLLECTION_ITEMS)) {
    projected[key] = isSensitiveKey(key)
      ? "[已隐藏]"
      : projectValue(item, context, depth + 1);
  }
  if (entries.length > MAX_COLLECTION_ITEMS) {
    context.truncated = true;
    projected["[其余字段]"] = "已截断";
  }
  return projected;
}

function parseJsonString(value: string): unknown {
  try {
    return JSON.parse(value) as unknown;
  } catch {
    return value;
  }
}

/** Format untrusted task data as inert text after removing credential-bearing fields and URLs. */
export function safeTaskText(value: unknown): string {
  const context: ProjectionContext = { nodes: 0, truncated: false, seen: new WeakSet<object>() };
  const source = typeof value === "string" ? parseJsonString(value) : value;
  const projected = projectValue(source, context, 0);
  let text = typeof projected === "string"
    ? projected
    : JSON.stringify(projected, null, 2) ?? "";
  if (text.length > MAX_OUTPUT_LENGTH) {
    text = text.slice(0, MAX_OUTPUT_LENGTH);
    context.truncated = true;
  }
  if (context.truncated && !text.endsWith(TRUNCATION_NOTE)) {
    text += TRUNCATION_NOTE;
  }
  return text;
}
