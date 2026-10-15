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

export function parseTaskReceipt(value: unknown, expectedTaskId: string): TaskReceipt {
  const record = requiredTaskRecord(value, expectedTaskId, "任务结果记录");
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
