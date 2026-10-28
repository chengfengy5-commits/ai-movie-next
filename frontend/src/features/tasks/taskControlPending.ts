import { browserStorage, type StorageLike } from "../../shared/api/storage";

export const TASK_CONTROL_PENDING_STORAGE_KEY = "haoai.task-control-pending.v1";

export interface TaskControlPendingScope {
  userId: string;
  apiBaseUrl: string;
  taskId: string;
}

interface StoredTaskControlPendingMarker extends TaskControlPendingScope {}

interface StoredTaskControlPendingMarkers {
  version: 1;
  markers: StoredTaskControlPendingMarker[];
}

export class TaskControlPendingStorageError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "TaskControlPendingStorageError";
  }
}

export function canonicalTaskControlApiBaseUrl(value: string | null): string | null {
  if (typeof value !== "string" || value.trim() === "") return null;
  try {
    const url = new URL(value);
    if ((url.protocol !== "http:" && url.protocol !== "https:")
      || url.username !== "" || url.password !== "" || url.search !== "" || url.hash !== "") {
      return null;
    }
    return url.href.replace(/\/+$/u, "");
  } catch {
    return null;
  }
}

export function createTaskControlPendingScope(
  userId: string,
  apiBaseUrl: string | null,
  taskId: string,
): TaskControlPendingScope | null {
  const canonicalApiBaseUrl = canonicalTaskControlApiBaseUrl(apiBaseUrl);
  if (userId.trim() === "" || canonicalApiBaseUrl === null || taskId.trim() === "") return null;
  return { userId, apiBaseUrl: canonicalApiBaseUrl, taskId };
}

function exactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  return Object.keys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key));
}

function isString(value: unknown): value is string {
  return typeof value === "string" && value.trim() !== "";
}

function sameScope(left: StoredTaskControlPendingMarker, right: TaskControlPendingScope): boolean {
  return left.userId === right.userId
    && left.apiBaseUrl === right.apiBaseUrl
    && left.taskId === right.taskId;
}

function markerIdentity(marker: StoredTaskControlPendingMarker): string {
  return JSON.stringify([marker.userId, marker.apiBaseUrl, marker.taskId]);
}

function resolveStorage(storage?: StorageLike): StorageLike {
  if (storage !== undefined) return storage;
  try {
    return browserStorage();
  } catch {
    throw new TaskControlPendingStorageError("浏览器本地存储当前不可用。");
  }
}

function readStoredMarkers(storage?: StorageLike): StoredTaskControlPendingMarkers {
  const resolved = resolveStorage(storage);
  let raw: string | null;
  try {
    raw = resolved.getItem(TASK_CONTROL_PENDING_STORAGE_KEY);
  } catch {
    throw new TaskControlPendingStorageError("读取本地取消请求记录失败。");
  }
  if (raw === null) return { version: 1, markers: [] };

  let value: unknown;
  try {
    value = JSON.parse(raw) as unknown;
  } catch {
    throw new TaskControlPendingStorageError("本地取消请求记录无法识别，已停止提交操作。");
  }
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new TaskControlPendingStorageError("本地取消请求记录格式无效，已停止提交操作。");
  }
  const envelope = value as Record<string, unknown>;
  if (!exactKeys(envelope, ["version", "markers"]) || envelope.version !== 1 || !Array.isArray(envelope.markers)) {
    throw new TaskControlPendingStorageError("本地取消请求记录版本无法识别，已停止提交操作。");
  }

  const seen = new Set<string>();
  const markers: StoredTaskControlPendingMarker[] = [];
  for (const item of envelope.markers) {
    if (typeof item !== "object" || item === null || Array.isArray(item)) {
      throw new TaskControlPendingStorageError("本地取消请求记录条目无效，已停止提交操作。");
    }
    const marker = item as Record<string, unknown>;
    if (!exactKeys(marker, ["userId", "apiBaseUrl", "taskId"])
      || !isString(marker.userId) || !isString(marker.apiBaseUrl) || !isString(marker.taskId)
      || canonicalTaskControlApiBaseUrl(marker.apiBaseUrl) !== marker.apiBaseUrl) {
      throw new TaskControlPendingStorageError("本地取消请求记录条目格式无效，已停止提交操作。");
    }
    const validated = {
      userId: marker.userId,
      apiBaseUrl: marker.apiBaseUrl,
      taskId: marker.taskId,
    };
    const identity = markerIdentity(validated);
    if (seen.has(identity)) {
      throw new TaskControlPendingStorageError("本地取消请求记录包含重复条目，已停止提交操作。");
    }
    seen.add(identity);
    markers.push(validated);
  }
  return { version: 1, markers };
}

function writeAndVerify(
  storage: StorageLike,
  value: StoredTaskControlPendingMarkers,
): void {
  const serialized = JSON.stringify({
    version: 1,
    markers: [...value.markers].sort((left, right) => markerIdentity(left).localeCompare(markerIdentity(right))),
  });
  try {
    storage.setItem(TASK_CONTROL_PENDING_STORAGE_KEY, serialized);
    if (storage.getItem(TASK_CONTROL_PENDING_STORAGE_KEY) !== serialized) {
      throw new TaskControlPendingStorageError("本地取消请求记录未能回读确认。");
    }
  } catch (cause) {
    if (cause instanceof TaskControlPendingStorageError) throw cause;
    throw new TaskControlPendingStorageError("写入本地取消请求记录失败。");
  }
}

export function hasTaskControlPendingMarker(
  scope: TaskControlPendingScope,
  storage?: StorageLike,
): boolean {
  const envelope = readStoredMarkers(storage);
  return envelope.markers.some((marker) => sameScope(marker, scope));
}

export function writeTaskControlPendingMarker(
  scope: TaskControlPendingScope,
  storage?: StorageLike,
): "stored" | "already_present" {
  const resolved = resolveStorage(storage);
  const envelope = readStoredMarkers(resolved);
  if (envelope.markers.some((marker) => sameScope(marker, scope))) return "already_present";
  writeAndVerify(resolved, { version: 1, markers: [...envelope.markers, scope] });
  return "stored";
}

export function removeTaskControlPendingMarker(
  scope: TaskControlPendingScope,
  storage?: StorageLike,
): boolean {
  const resolved = resolveStorage(storage);
  const envelope = readStoredMarkers(resolved);
  const markers = envelope.markers.filter((marker) => !sameScope(marker, scope));
  if (markers.length === envelope.markers.length) return true;
  if (markers.length === 0) {
    try {
      resolved.removeItem(TASK_CONTROL_PENDING_STORAGE_KEY);
      if (resolved.getItem(TASK_CONTROL_PENDING_STORAGE_KEY) !== null) {
        throw new TaskControlPendingStorageError("本地取消请求记录清理未能回读确认。");
      }
      return true;
    } catch (cause) {
      if (cause instanceof TaskControlPendingStorageError) throw cause;
      throw new TaskControlPendingStorageError("清理本地取消请求记录失败。");
    }
  }
  writeAndVerify(resolved, { version: 1, markers });
  return true;
}
