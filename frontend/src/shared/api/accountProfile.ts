/** Narrow self-service account endpoints consumed by the account overview page. */
export interface AccountCredits {
  credits: number;
}

export interface AccountProfileUpdateInput {
  bio: string;
}

/** The profile PUT response is intentionally partial; it is never a full User. */
export interface AccountProfileReceipt {
  id: string;
  username: string;
  avatar_url: string | null;
  bio: string | null;
}

export interface AccountProfileServicePort {
  getCredits(signal: AbortSignal): Promise<AccountCredits>;
  updateProfile(input: AccountProfileUpdateInput, signal: AbortSignal): Promise<AccountProfileReceipt>;
}

export class InvalidAccountProfileResponseError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "InvalidAccountProfileResponseError";
  }
}

function record(value: unknown, label: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new InvalidAccountProfileResponseError(`${label}格式无效。`);
  }
  return value as Record<string, unknown>;
}

function requiredString(value: Record<string, unknown>, key: string): string {
  const field = value[key];
  if (typeof field !== "string" || field.trim() === "") {
    throw new InvalidAccountProfileResponseError(`账户资料中的 ${key} 字段无效。`);
  }
  return field;
}

function nullableString(value: Record<string, unknown>, key: string): string | null {
  const field = value[key];
  if (field !== null && typeof field !== "string") {
    throw new InvalidAccountProfileResponseError(`账户资料中的 ${key} 字段无效。`);
  }
  return field;
}

export function parseAccountCredits(value: unknown): AccountCredits {
  const snapshot = record(value, "积分余额");
  const credits = snapshot.credits;
  if (typeof credits !== "number" || !Number.isSafeInteger(credits)) {
    throw new InvalidAccountProfileResponseError("积分余额格式无效。");
  }
  return { credits };
}

export function parseAccountProfileReceipt(value: unknown): AccountProfileReceipt {
  const receipt = record(value, "账户资料回执");
  return {
    id: requiredString(receipt, "id"),
    username: requiredString(receipt, "username"),
    avatar_url: nullableString(receipt, "avatar_url"),
    bio: nullableString(receipt, "bio"),
  };
}
