export interface AdminSystemConfig {
  id: string;
  muse_token: string | null;
  site_title: string | null;
  site_tagline: string | null;
  storyboard_cost_multiplier: number | null;
  membership_benefits: string;
  payments_enabled: boolean;
  tutorial_url: string;
  purchase_qr_code_url: string;
  purchase_contact_info: string;
  team_created_limit: number;
  team_joined_limit: number;
  team_member_limit: number;
  invite_code_ttl_hours: number;
  chapter_lock_idle_minutes: number;
  aistarslab_credit_multiplier: number;
  frontend_url: string;
  allowed_origins: string;
  upload_dir: string;
  worker_max_concurrency: string;
}

export interface AdminSystemConfigUpdate {
  muse_token?: string;
  site_title?: string;
  site_tagline?: string;
  storyboard_cost_multiplier?: number;
  membership_benefits?: string;
  payments_enabled?: boolean;
  tutorial_url?: string;
  purchase_qr_code_url?: string;
  purchase_contact_info?: string;
  team_created_limit?: number;
  team_joined_limit?: number;
  team_member_limit?: number;
  invite_code_ttl_hours?: number;
  chapter_lock_idle_minutes?: number;
  aistarslab_credit_multiplier?: number;
  frontend_url?: string;
  allowed_origins?: string;
  upload_dir?: string;
  worker_max_concurrency?: string;
}

export interface AdminSystemConfigPage {
  data: AdminSystemConfig[];
  total: number;
}

export interface AdminSystemConfigApi {
  list(signal?: AbortSignal): Promise<AdminSystemConfigPage>;
  get(id: string, signal?: AbortSignal): Promise<AdminSystemConfig>;
  update(id: string, patch: AdminSystemConfigUpdate, signal?: AbortSignal): Promise<AdminSystemConfig>;
}

export type AdminSystemConfigExitKind = "dirty-draft" | "write-in-flight" | "unknown-write";

export interface PendingAdminSystemConfigExit {
  kind: AdminSystemConfigExitKind;
  operation: "update";
  configId: string;
  intentId: number;
  message: string;
}

export interface AdminSystemConfigExitSnapshot {
  ownerScope: string;
  viewKey: string;
  administratorId: string;
  configId: string;
  pending: PendingAdminSystemConfigExit | null;
}
