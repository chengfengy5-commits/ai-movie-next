/** Public account configuration and redemption wire contracts. */
export type PurchasePackageCategory = "membership" | "credits";

export interface PublicSiteConfig {
  site_title: string;
  site_tagline: string;
  membership_benefits: string;
  tutorial_url: string;
  purchase_qr_code_url: string;
  purchase_contact_info: string;
  storyboard_cost_multiplier: number;
  payments_enabled: boolean;
}

export interface PurchasePackage {
  id: string;
  category: PurchasePackageCategory;
  name: string;
  price: number;
  credits: number | null;
  days: number | null;
  sort_order: number | null;
}

export interface AccountRedemptionReceipt {
  message: string;
  type: PurchasePackageCategory;
  value: number;
  detail: string | null;
}

export interface AccountMembershipServicePort {
  getPublicConfig(signal: AbortSignal): Promise<PublicSiteConfig>;
  listPurchasePackages(category: PurchasePackageCategory, signal: AbortSignal): Promise<PurchasePackage[]>;
  /** Resolves only after an exact HTTP 200 response has passed receipt parsing. */
  redeem(input: { code: string }, signal: AbortSignal): Promise<AccountRedemptionReceipt>;
}

export class InvalidAccountMembershipResponseError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "InvalidAccountMembershipResponseError";
  }
}

function record(value: unknown, label: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new InvalidAccountMembershipResponseError(`${label}格式无效。`);
  }
  return value as Record<string, unknown>;
}

function requiredString(value: Record<string, unknown>, key: string, label: string): string {
  const field = value[key];
  if (typeof field !== "string") {
    throw new InvalidAccountMembershipResponseError(`${label}中的 ${key} 字段无效。`);
  }
  return field;
}

function nonEmptyString(value: Record<string, unknown>, key: string, label: string): string {
  const field = requiredString(value, key, label);
  if (field.trim() === "") {
    throw new InvalidAccountMembershipResponseError(`${label}中的 ${key} 字段无效。`);
  }
  return field;
}

function requiredInteger(value: Record<string, unknown>, key: string, label: string): number {
  const field = value[key];
  if (typeof field !== "number" || !Number.isSafeInteger(field)) {
    throw new InvalidAccountMembershipResponseError(`${label}中的 ${key} 字段无效。`);
  }
  return field;
}

function nullableInteger(value: Record<string, unknown>, key: string, label: string): number | null {
  const field = value[key];
  if (field === null) return null;
  if (typeof field !== "number" || !Number.isSafeInteger(field)) {
    throw new InvalidAccountMembershipResponseError(`${label}中的 ${key} 字段无效。`);
  }
  return field;
}

function nullableString(value: Record<string, unknown>, key: string, label: string): string | null {
  const field = value[key];
  if (field === null) return null;
  if (typeof field !== "string") {
    throw new InvalidAccountMembershipResponseError(`${label}中的 ${key} 字段无效。`);
  }
  return field;
}

export function parsePublicSiteConfig(value: unknown): PublicSiteConfig {
  const config = record(value, "公开站点配置");
  const paymentsEnabled = config.payments_enabled;
  if (typeof paymentsEnabled !== "boolean") {
    throw new InvalidAccountMembershipResponseError("公开站点配置中的 payments_enabled 字段无效。");
  }
  return {
    site_title: requiredString(config, "site_title", "公开站点配置"),
    site_tagline: requiredString(config, "site_tagline", "公开站点配置"),
    membership_benefits: requiredString(config, "membership_benefits", "公开站点配置"),
    tutorial_url: requiredString(config, "tutorial_url", "公开站点配置"),
    purchase_qr_code_url: requiredString(config, "purchase_qr_code_url", "公开站点配置"),
    purchase_contact_info: requiredString(config, "purchase_contact_info", "公开站点配置"),
    storyboard_cost_multiplier: requiredInteger(config, "storyboard_cost_multiplier", "公开站点配置"),
    payments_enabled: paymentsEnabled,
  };
}

export function parsePurchasePackages(
  value: unknown,
  category: PurchasePackageCategory,
): PurchasePackage[] {
  if (!Array.isArray(value)) {
    throw new InvalidAccountMembershipResponseError("套餐目录格式无效。");
  }
  return value.map((entry) => {
    const item = record(entry, "套餐");
    const itemCategory = requiredString(item, "category", "套餐");
    if (itemCategory !== category) {
      throw new InvalidAccountMembershipResponseError("套餐类别与请求不匹配。");
    }
    return {
      id: requiredString(item, "id", "套餐"),
      category,
      name: requiredString(item, "name", "套餐"),
      price: requiredInteger(item, "price", "套餐"),
      credits: nullableInteger(item, "credits", "套餐"),
      days: nullableInteger(item, "days", "套餐"),
      sort_order: nullableInteger(item, "sort_order", "套餐"),
    };
  });
}

export function parseAccountRedemptionReceipt(value: unknown): AccountRedemptionReceipt {
  const receipt = record(value, "兑换回执");
  const type = requiredString(receipt, "type", "兑换回执");
  if (type !== "membership" && type !== "credits") {
    throw new InvalidAccountMembershipResponseError("兑换回执中的 type 字段无效。");
  }
  return {
    message: nonEmptyString(receipt, "message", "兑换回执"),
    type,
    value: requiredInteger(receipt, "value", "兑换回执"),
    detail: nullableString(receipt, "detail", "兑换回执"),
  };
}
