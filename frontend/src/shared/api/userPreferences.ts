import { ApiError } from "./errors";

export type UserPreferences = Readonly<Record<string, unknown>>;

export interface UserPreferencesUpdateReceipt {
  readonly updatedKeys: readonly string[];
}

export interface UserPreferencesPort {
  get(signal: AbortSignal): Promise<UserPreferences>;
  update(preferences: UserPreferences, signal: AbortSignal): Promise<UserPreferencesUpdateReceipt>;
}

function record(value: unknown): Record<string, unknown> | null {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

export function parseUserPreferences(value: unknown): UserPreferences {
  const parsed = record(value);
  if (parsed === null) throw new ApiError("invalid-response", "服务器返回的账户偏好格式无效。", null);
  return Object.freeze({ ...parsed });
}

export function parseUserPreferencesUpdateReceipt(value: unknown): UserPreferencesUpdateReceipt {
  const parsed = record(value);
  const updatedKeys = parsed?.updated_keys;
  if (parsed?.ok !== true || !Array.isArray(updatedKeys)
    || !updatedKeys.every((key) => typeof key === "string")) {
    throw new ApiError("invalid-response", "服务器没有确认账户偏好保存结果。", null);
  }
  return { updatedKeys: Object.freeze([...updatedKeys]) };
}
