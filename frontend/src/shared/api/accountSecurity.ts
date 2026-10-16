/** Parsed, display-safe subset of the authenticated session response. */
export interface AccountSession {
  id: string;
  deviceName: string;
  ipAddress: string;
  userAgent: string;
  createdAt: string | null;
  lastSeenAt: string | null;
  revokedAt: string | null;
  isActive: boolean;
  isCurrent: boolean;
}

export interface AccountSessionRevocationReceipt {
  message: string;
  session_id: string;
}

export interface AccountPasswordChangeReceipt {
  message: string;
}

export interface AccountSecurityServicePort {
  listSessions(signal: AbortSignal): Promise<AccountSession[]>;
  revokeSession(sessionId: string, signal: AbortSignal): Promise<AccountSessionRevocationReceipt>;
  changePassword(
    input: { old_password: string; new_password: string },
    signal: AbortSignal,
  ): Promise<AccountPasswordChangeReceipt>;
}

export class InvalidAccountSecurityResponseError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "InvalidAccountSecurityResponseError";
  }
}

type RecordValue = Record<string, unknown>;

function record(value: unknown, label: string): RecordValue {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new InvalidAccountSecurityResponseError(`${label}格式无效。`);
  }
  return value as RecordValue;
}

function requiredString(
  value: RecordValue,
  key: string,
  label: string,
  allowEmpty = false,
): string {
  const field = value[key];
  if (typeof field !== "string" || (!allowEmpty && field.trim() === "")) {
    throw new InvalidAccountSecurityResponseError(`${label}中的 ${key} 字段无效。`);
  }
  return field;
}

function requiredNullableString(value: RecordValue, key: string, label: string): string | null {
  const field = value[key];
  if (field !== null && typeof field !== "string") {
    throw new InvalidAccountSecurityResponseError(`${label}中的 ${key} 字段无效。`);
  }
  return field;
}

function requiredBoolean(value: RecordValue, key: string, label: string): boolean {
  const field = value[key];
  if (typeof field !== "boolean") {
    throw new InvalidAccountSecurityResponseError(`${label}中的 ${key} 字段无效。`);
  }
  return field;
}

function parseSession(value: unknown): AccountSession {
  const session = record(value, "登录设备");
  const id = requiredString(session, "id", "登录设备");
  requiredString(session, "jti", "登录设备");

  return {
    id,
    deviceName: requiredString(session, "device_name", "登录设备"),
    ipAddress: requiredString(session, "ip_address", "登录设备"),
    userAgent: requiredString(session, "user_agent", "登录设备", true),
    createdAt: requiredNullableString(session, "created_at", "登录设备"),
    lastSeenAt: requiredNullableString(session, "last_seen_at", "登录设备"),
    revokedAt: requiredNullableString(session, "revoked_at", "登录设备"),
    isActive: requiredBoolean(session, "is_active", "登录设备"),
    isCurrent: requiredBoolean(session, "is_current", "登录设备"),
  };
}

/** The endpoint returns a bare array. Internal JWT ids are validated, then discarded. */
export function parseAccountSessions(value: unknown): AccountSession[] {
  if (!Array.isArray(value)) {
    throw new InvalidAccountSecurityResponseError("登录设备列表格式无效。");
  }

  const ids = new Set<string>();
  return value.map((item) => {
    const session = parseSession(item);
    if (ids.has(session.id)) {
      throw new InvalidAccountSecurityResponseError("登录设备列表包含重复记录。");
    }
    ids.add(session.id);
    return session;
  });
}

export function parseAccountSessionRevocationReceipt(
  value: unknown,
  expectedSessionId: string,
): AccountSessionRevocationReceipt {
  const receipt = record(value, "设备撤销回执");
  const message = requiredString(receipt, "message", "设备撤销回执");
  const sessionId = requiredString(receipt, "session_id", "设备撤销回执");
  if (message !== "设备已下线" || sessionId !== expectedSessionId) {
    throw new InvalidAccountSecurityResponseError("设备撤销回执与本次操作不匹配。");
  }
  return { message, session_id: sessionId };
}

export function parseAccountPasswordChangeReceipt(value: unknown): AccountPasswordChangeReceipt {
  const receipt = record(value, "密码修改回执");
  const message = requiredString(receipt, "message", "密码修改回执");
  if (message !== "密码修改成功，请重新登录") {
    throw new InvalidAccountSecurityResponseError("密码修改回执无法确认本次操作。");
  }
  return { message };
}
