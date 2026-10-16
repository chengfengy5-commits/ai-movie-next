import { ApiError } from "../../shared/api/errors";
import type {
  AdminMembershipPage,
  AdminPurchasePackage,
  AdminRedemptionCode,
  GenerateRedemptionCodesReceipt,
} from "./adminMembershipTypes";

function invalid(message: string): never {
  throw new ApiError("invalid-response", message);
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requiredIdentifier(value: unknown, field: string): string {
  if (typeof value !== "string" || value.length === 0) invalid("服务返回的数据缺少有效的 " + field + "。");
  return value;
}

function stringValue(value: unknown, field: string): string {
  if (typeof value !== "string") invalid("服务返回的数据包含无效的 " + field + "。");
  return value;
}

function nullableString(value: unknown, field: string): string | null {
  if (value === null) return null;
  if (typeof value !== "string") invalid("服务返回的数据包含无效的 " + field + "。");
  return value;
}

function safeInteger(value: unknown, field: string): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value)) invalid("服务返回的数据包含无效的 " + field + "。");
  return value;
}

function nullableInteger(value: unknown, field: string): number | null {
  if (value === null) return null;
  return safeInteger(value, field);
}

export function parseAdminRedemptionCode(value: unknown): AdminRedemptionCode {
  if (!isObject(value)) invalid("服务返回的兑换码记录格式无效。");
  if (typeof value.is_used !== "boolean") invalid("服务返回的数据包含无效的 is_used。");
  const result: AdminRedemptionCode = {
    id: requiredIdentifier(value.id, "id"),
    code: requiredIdentifier(value.code, "code"),
    type: requiredIdentifier(value.type, "type"),
    value: safeInteger(value.value, "value"),
    is_used: value.is_used,
    used_by: nullableString(value.used_by, "used_by"),
    used_at: nullableString(value.used_at, "used_at"),
    created_by: requiredIdentifier(value.created_by, "created_by"),
    expires_at: nullableString(value.expires_at, "expires_at"),
    created_at: requiredIdentifier(value.created_at, "created_at"),
  };
  return result;
}

export function parseAdminPurchasePackage(value: unknown): AdminPurchasePackage {
  if (!isObject(value)) invalid("服务返回的购买套餐记录格式无效。");
  if (typeof value.is_active !== "boolean") invalid("服务返回的数据包含无效的 is_active。");
  return {
    id: requiredIdentifier(value.id, "id"),
    category: stringValue(value.category, "category"),
    name: stringValue(value.name, "name"),
    price: safeInteger(value.price, "price"),
    credits: nullableInteger(value.credits, "credits"),
    days: nullableInteger(value.days, "days"),
    sort_order: safeInteger(value.sort_order, "sort_order"),
    is_active: value.is_active,
    created_at: requiredIdentifier(value.created_at, "created_at"),
    updated_at: requiredIdentifier(value.updated_at, "updated_at"),
  };
}

function parsePage<T>(value: unknown, parseRecord: (record: unknown) => T, label: string): AdminMembershipPage<T> {
  if (!isObject(value) || !Array.isArray(value.data)) invalid("服务返回的" + label + "列表格式无效。");
  const total = safeInteger(value.total, "total");
  if (total < 0) invalid("服务返回的 total 不能为负数。");
  const data = value.data.map(parseRecord);
  const ids = new Set<string>();
  for (const record of data) {
    const id = (record as { id: string }).id;
    if (ids.has(id)) invalid("服务返回的列表包含重复记录。");
    ids.add(id);
  }
  return { data, total };
}

export function parseAdminRedemptionCodePage(value: unknown): AdminMembershipPage<AdminRedemptionCode> {
  return parsePage(value, parseAdminRedemptionCode, "兑换码");
}

export function parseAdminPurchasePackagePage(value: unknown): AdminMembershipPage<AdminPurchasePackage> {
  return parsePage(value, parseAdminPurchasePackage, "购买套餐");
}

export function parseGenerateRedemptionCodesReceipt(
  value: unknown,
  expected: { type: string; value: number; count: number; administratorId: string },
): GenerateRedemptionCodesReceipt {
  if (!isObject(value)) invalid("服务返回的生成回执格式无效。");
  const record = parseAdminRedemptionCode(value);
  const generatedCount = safeInteger(value._generated_count, "_generated_count");
  if (record.type !== expected.type
    || record.value !== expected.value
    || record.created_by !== expected.administratorId
    || generatedCount !== expected.count) {
    invalid("服务返回的生成回执与本次提交内容不一致，生成结果待核对。");
  }
  return { record, generated_count: generatedCount };
}

export function parseAdminMessage(value: unknown): { message: string } {
  if (!isObject(value)) invalid("服务返回的操作回执格式无效。");
  return { message: requiredIdentifier(value.message, "message") };
}
