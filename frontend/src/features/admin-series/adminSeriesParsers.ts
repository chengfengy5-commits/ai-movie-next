import { ApiError } from "../../shared/api/errors";
import type { AdminSeriesPage, AdminSeriesRecord } from "./adminSeriesTypes";

const RECORD_FIELDS = ["id", "user_id", "name", "description", "image_url", "created_at", "updated_at"] as const;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function parseAdminSeriesRecord(value: unknown): AdminSeriesRecord {
  if (!isRecord(value) || Object.keys(value).length !== RECORD_FIELDS.length
    || RECORD_FIELDS.some((field) => !Object.hasOwn(value, field))) {
    throw new ApiError("invalid-response", "服务返回的剧集记录格式无效。", 200);
  }
  if (typeof value.id !== "string" || value.id.length === 0
    || typeof value.user_id !== "string" || value.user_id.length === 0
    || typeof value.name !== "string"
    || !(typeof value.description === "string" || value.description === null)
    || !(typeof value.image_url === "string" || value.image_url === null)
    || typeof value.created_at !== "string" || typeof value.updated_at !== "string") {
    throw new ApiError("invalid-response", "服务返回的剧集记录字段无效。", 200);
  }
  return {
    id: value.id,
    user_id: value.user_id,
    name: value.name,
    description: value.description,
    image_url: value.image_url,
    created_at: value.created_at,
    updated_at: value.updated_at,
  };
}

export function parseAdminSeriesPage(value: unknown): AdminSeriesPage {
  if (!isRecord(value) || Object.keys(value).length !== 2
    || !Object.hasOwn(value, "data") || !Object.hasOwn(value, "total") || !Array.isArray(value.data)
    || typeof value.total !== "number" || !Number.isSafeInteger(value.total) || value.total < 0) {
    throw new ApiError("invalid-response", "服务返回的剧集列表格式无效。", 200);
  }
  return { data: value.data.map(parseAdminSeriesRecord), total: value.total };
}
