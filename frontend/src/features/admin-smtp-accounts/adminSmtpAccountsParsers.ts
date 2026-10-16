import { ApiError } from "../../shared/api/errors";
import type {
  AdminSmtpAccountAck,
  AdminSmtpAccountCreateAck,
  AdminSmtpAccountPage,
  AdminSmtpAccountRecord,
} from "./adminSmtpAccountsTypes";

const ACCOUNT_FIELDS = ["id", "host", "port", "user", "password", "daily_sent", "is_active", "created_at", "updated_at"] as const;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function parseAdminSmtpAccount(value: unknown): AdminSmtpAccountRecord {
  if (!isRecord(value) || Object.keys(value).length !== ACCOUNT_FIELDS.length
    || ACCOUNT_FIELDS.some((field) => !Object.hasOwn(value, field))) {
    throw new ApiError("invalid-response", "服务返回的 SMTP 账号格式无效。", 200);
  }
  const { password } = value;
  if (typeof value.id !== "string" || value.id.length === 0
    || typeof value.host !== "string"
    || typeof value.port !== "number" || !Number.isSafeInteger(value.port)
    || typeof value.user !== "string"
    || typeof password !== "string"
    || typeof value.daily_sent !== "number" || !Number.isSafeInteger(value.daily_sent)
    || typeof value.is_active !== "boolean"
    || !(typeof value.created_at === "string" || value.created_at === null)
    || !(typeof value.updated_at === "string" || value.updated_at === null)) {
    throw new ApiError("invalid-response", "服务返回的 SMTP 账号字段无效。", 200);
  }

  // Validate the legacy wire secret, then drop it at the adapter boundary.
  void password;
  return {
    id: value.id,
    host: value.host,
    port: value.port,
    user: value.user,
    daily_sent: value.daily_sent,
    is_active: value.is_active,
    created_at: value.created_at,
    updated_at: value.updated_at,
  };
}

export function parseAdminSmtpAccountPage(value: unknown): AdminSmtpAccountPage {
  if (!isRecord(value) || Object.keys(value).length !== 2
    || !Object.hasOwn(value, "data") || !Object.hasOwn(value, "total")
    || !Array.isArray(value.data) || typeof value.total !== "number"
    || !Number.isSafeInteger(value.total) || value.total < 0) {
    throw new ApiError("invalid-response", "服务返回的 SMTP 列表格式无效。", 200);
  }
  const data = value.data.map(parseAdminSmtpAccount);
  if (value.total !== data.length) {
    throw new ApiError("invalid-response", "服务返回的 SMTP 列表数量不一致。", 200);
  }
  return { data, total: value.total };
}

export function parseAdminSmtpAccountCreateAck(value: unknown, submittedUser: string): AdminSmtpAccountCreateAck {
  if (!isRecord(value) || Object.keys(value).length !== 3
    || !Object.hasOwn(value, "id") || !Object.hasOwn(value, "user") || !Object.hasOwn(value, "message")
    || typeof value.id !== "string" || value.id.length === 0 || value.user !== submittedUser
    || value.message !== "添加成功") {
    throw new ApiError("invalid-response", "服务返回的 SMTP 创建确认无法匹配本次提交。", 200);
  }
  return { id: value.id, user: value.user, message: "添加成功" };
}

export function parseAdminSmtpAccountAck(value: unknown, message: AdminSmtpAccountAck["message"]): AdminSmtpAccountAck {
  if (!isRecord(value) || Object.keys(value).length !== 1
    || !Object.hasOwn(value, "message") || value.message !== message) {
    throw new ApiError("invalid-response", "服务返回的 SMTP 操作确认无法匹配本次请求。", 200);
  }
  return { message };
}
