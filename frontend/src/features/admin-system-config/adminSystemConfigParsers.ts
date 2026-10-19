import { ApiError } from "../../shared/api/errors";
import type { AdminSystemConfig, AdminSystemConfigPage } from "./adminSystemConfigTypes";

const CONFIG_FIELDS = [
  "id",
  "muse_token",
  "site_title",
  "site_tagline",
  "storyboard_cost_multiplier",
  "membership_benefits",
  "payments_enabled",
  "tutorial_url",
  "purchase_qr_code_url",
  "purchase_contact_info",
  "team_created_limit",
  "team_joined_limit",
  "team_member_limit",
  "invite_code_ttl_hours",
  "chapter_lock_idle_minutes",
  "aistarslab_credit_multiplier",
  "frontend_url",
  "allowed_origins",
  "upload_dir",
  "worker_max_concurrency",
] as const;

function invalid(message: string): never {
  throw new ApiError("invalid-response", message);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function nullableText(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function safeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value);
}

export function parseAdminSystemConfig(value: unknown): AdminSystemConfig {
  if (!isRecord(value)) return invalid("系统配置响应格式无效。");
  const keys = Object.keys(value);
  if (keys.length !== CONFIG_FIELDS.length || CONFIG_FIELDS.some((field) => !Object.hasOwn(value, field))) {
    return invalid("系统配置响应字段不完整。");
  }

  if (typeof value.id !== "string" || value.id.length === 0
    || !nullableText(value.muse_token)
    || !nullableText(value.site_title)
    || !nullableText(value.site_tagline)
    || !(value.storyboard_cost_multiplier === null || safeInteger(value.storyboard_cost_multiplier))
    || typeof value.membership_benefits !== "string"
    || typeof value.payments_enabled !== "boolean"
    || typeof value.tutorial_url !== "string"
    || typeof value.purchase_qr_code_url !== "string"
    || typeof value.purchase_contact_info !== "string"
    || !safeInteger(value.team_created_limit)
    || !safeInteger(value.team_joined_limit)
    || !safeInteger(value.team_member_limit)
    || !safeInteger(value.invite_code_ttl_hours)
    || !safeInteger(value.chapter_lock_idle_minutes)
    || typeof value.aistarslab_credit_multiplier !== "number"
    || !Number.isFinite(value.aistarslab_credit_multiplier)
    || typeof value.frontend_url !== "string"
    || typeof value.allowed_origins !== "string"
    || typeof value.upload_dir !== "string"
    || typeof value.worker_max_concurrency !== "string") {
    return invalid("系统配置响应字段类型无效。");
  }
  return value as unknown as AdminSystemConfig;
}

export function parseAdminSystemConfigPage(value: unknown): AdminSystemConfigPage {
  if (!isRecord(value) || !Array.isArray(value.data)
    || !safeInteger(value.total) || value.total < 0 || value.data.length > 1
    || value.total !== value.data.length) {
    return invalid("系统配置列表响应格式无效。");
  }
  return { data: value.data.map(parseAdminSystemConfig), total: value.total };
}
