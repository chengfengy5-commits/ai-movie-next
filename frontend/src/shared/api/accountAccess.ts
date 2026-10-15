import { InvalidResponseError, parseUser, type User } from "./contracts";

export interface RegisterAccountInput {
  username: string;
  email: string;
  password: string;
  email_code: string;
}

export interface SendEmailCodeInput {
  email: string;
  username?: string;
}

export interface VerifyEmailCodeInput {
  email: string;
  code: string;
}

export interface ForgotPasswordInput {
  username: string;
  email: string;
}

export interface ResetPasswordInput {
  token: string;
  new_password: string;
}

export interface AccountActionReceipt {
  message: string;
}

export interface EmailCodeReceipt extends AccountActionReceipt {
  expireInSeconds: number;
}

export interface AccountAccessService {
  register(input: RegisterAccountInput, signal: AbortSignal): Promise<User>;
  sendEmailCode(input: SendEmailCodeInput, signal: AbortSignal): Promise<EmailCodeReceipt>;
  verifyEmailCode(input: VerifyEmailCodeInput, signal: AbortSignal): Promise<AccountActionReceipt>;
  requestPasswordReset(input: ForgotPasswordInput, signal: AbortSignal): Promise<AccountActionReceipt>;
  resetPassword(input: ResetPasswordInput, signal: AbortSignal): Promise<AccountActionReceipt>;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function parseAccountActionReceipt(value: unknown): AccountActionReceipt {
  if (!isRecord(value) || typeof value.message !== "string" || value.message.trim() === "") {
    throw new InvalidResponseError("认证服务没有返回有效的操作结果。");
  }
  return { message: value.message };
}

export function parseEmailCodeReceipt(value: unknown): EmailCodeReceipt {
  const receipt = parseAccountActionReceipt(value);
  const expireInSeconds = isRecord(value) ? value.expire_in : undefined;
  if (typeof expireInSeconds !== "number" || !Number.isSafeInteger(expireInSeconds) || expireInSeconds <= 0) {
    throw new InvalidResponseError("认证服务没有返回有效的验证码有效期。");
  }
  return { ...receipt, expireInSeconds };
}

export function parseRegisteredUser(value: unknown): User {
  return parseUser(value);
}
