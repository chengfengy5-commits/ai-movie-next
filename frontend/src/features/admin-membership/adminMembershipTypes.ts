export type RedemptionCodeType = "membership" | "credits";
export type AdminMembershipSortOrder = "asc" | "desc";

export interface AdminRedemptionCode {
  id: string;
  code: string;
  type: string;
  value: number;
  is_used: boolean;
  used_by: string | null;
  used_at: string | null;
  created_by: string;
  expires_at: string | null;
  created_at: string;
}

export interface AdminPurchasePackage {
  id: string;
  category: string;
  name: string;
  price: number;
  credits: number | null;
  days: number | null;
  sort_order: number;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export type RedemptionSortField =
  | "id" | "code" | "type" | "value" | "is_used" | "used_by"
  | "used_at" | "created_by" | "expires_at" | "created_at";

export type PackageSortField =
  | "id" | "category" | "name" | "price" | "credits" | "days"
  | "sort_order" | "is_active" | "created_at" | "updated_at";

export interface AdminMembershipPage<T> {
  data: T[];
  total: number;
}

export interface RedemptionCodeQuery {
  skip: number;
  limit: number;
  sort_field: RedemptionSortField;
  sort_order: AdminMembershipSortOrder;
  code?: string;
  type?: string;
  is_used?: boolean;
  value?: number;
}

export interface PackageQuery {
  skip: number;
  limit: number;
  sort_field: PackageSortField;
  sort_order: AdminMembershipSortOrder;
}

export interface GenerateRedemptionCodesInput {
  type: RedemptionCodeType;
  value: number;
  count: number;
  expires_at?: string | null;
}

export interface GenerateRedemptionCodesReceipt {
  record: AdminRedemptionCode;
  generated_count: number;
}

export interface PurchasePackageInput {
  category: string;
  name: string;
  price: number;
  credits: number | null;
  days: number | null;
  sort_order: number;
  is_active: boolean;
}

export type PurchasePackageUpdate = Partial<PurchasePackageInput>;

export interface AdminMembershipApi {
  listRedemptionCodes(query: RedemptionCodeQuery, signal?: AbortSignal): Promise<AdminMembershipPage<AdminRedemptionCode>>;
  getRedemptionCode(id: string, signal?: AbortSignal): Promise<AdminRedemptionCode>;
  generateRedemptionCodes(input: GenerateRedemptionCodesInput, administratorId: string, signal?: AbortSignal): Promise<GenerateRedemptionCodesReceipt>;
  deleteRedemptionCode(id: string, signal?: AbortSignal): Promise<{ message: string }>;
  listPurchasePackages(query: PackageQuery, signal?: AbortSignal): Promise<AdminMembershipPage<AdminPurchasePackage>>;
  getPurchasePackage(id: string, signal?: AbortSignal): Promise<AdminPurchasePackage>;
  createPurchasePackage(input: PurchasePackageInput, signal?: AbortSignal): Promise<AdminPurchasePackage>;
  updatePurchasePackage(id: string, input: PurchasePackageUpdate, signal?: AbortSignal): Promise<AdminPurchasePackage>;
  deletePurchasePackage(id: string, signal?: AbortSignal): Promise<{ message: string }>;
}

export type MembershipWriteOperation = "create" | "update" | "delete";

export interface PendingMembershipExit {
  kind: "dirty-draft" | "write-in-flight" | "unknown-write";
  resource: "redemption-codes" | "purchase-packages";
  operation: MembershipWriteOperation;
  recordId?: string;
  intentId: number;
  draft: Readonly<Record<string, string | boolean>>;
  message: string;
}

export interface AdminMembershipExitSnapshot {
  ownerScope: string;
  viewKey: string;
  administratorId: string;
  pending: PendingMembershipExit | null;
}
