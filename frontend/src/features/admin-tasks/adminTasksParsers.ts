import { ApiError } from "../../shared/api/errors";
import type { AdminTaskPage, AdminTaskRecord } from "./adminTasksTypes";

const RECORD_FIELDS = [
  "id", "user_id", "type", "status", "credit_cost", "message_id", "model_name", "created_at", "updated_at",
] as const;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function parseAdminTaskRecord(value: unknown): AdminTaskRecord {
  if (!isRecord(value) || Object.keys(value).length !== RECORD_FIELDS.length
    || RECORD_FIELDS.some((field) => !Object.hasOwn(value, field))) {
    throw new ApiError("invalid-response", "服务返回的任务记录格式无效。", 200);
  }
  if (typeof value.id !== "string" || value.id.length === 0
    || typeof value.user_id !== "string" || value.user_id.length === 0
    || typeof value.type !== "string"
    || typeof value.status !== "string"
    || typeof value.credit_cost !== "number" || !Number.isSafeInteger(value.credit_cost)
    || !(typeof value.message_id === "string" || value.message_id === null)
    || typeof value.model_name !== "string"
    || !(typeof value.created_at === "string" || value.created_at === null)
    || !(typeof value.updated_at === "string" || value.updated_at === null)) {
    throw new ApiError("invalid-response", "服务返回的任务记录字段无效。", 200);
  }
  return {
    id: value.id,
    user_id: value.user_id,
    type: value.type,
    status: value.status,
    credit_cost: value.credit_cost,
    message_id: value.message_id,
    model_name: value.model_name,
    created_at: value.created_at,
    updated_at: value.updated_at,
  };
}

export function parseAdminTaskPage(value: unknown): AdminTaskPage {
  if (!isRecord(value) || Object.keys(value).length !== 2
    || !Object.hasOwn(value, "data") || !Object.hasOwn(value, "total") || !Array.isArray(value.data)
    || typeof value.total !== "number" || !Number.isSafeInteger(value.total) || value.total < 0) {
    throw new ApiError("invalid-response", "服务返回的任务列表格式无效。", 200);
  }
  return { data: value.data.map(parseAdminTaskRecord), total: value.total };
}
