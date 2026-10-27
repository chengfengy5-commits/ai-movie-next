import type { FusedGenerationMemberType } from "../../shared/api/fusedGeneration";
import type { CreatorModel, CreatorPrompt } from "../../shared/api/creatorSettings";

export interface FusedGenerationPreference {
  readonly modelId: string;
  readonly promptId: string;
  readonly size: string;
}

function safeKeyPart(value: string): string {
  return encodeURIComponent(value.trim());
}

export function fusedGenerationPreferenceKey(
  userId: string,
  apiBaseUrl: string,
  memberType: FusedGenerationMemberType,
): string | null {
  if (userId.trim() === "" || apiBaseUrl.trim() === "") return null;
  return `haoai:fused-generation:v1:${safeKeyPart(userId)}:${safeKeyPart(apiBaseUrl)}:${memberType}`;
}

export function readFusedGenerationPreference(
  storage: Pick<Storage, "getItem">,
  key: string | null,
): FusedGenerationPreference | null {
  return readFusedGenerationPreferenceResult(storage, key).value;
}

export function readFusedGenerationPreferenceResult(
  storage: Pick<Storage, "getItem">,
  key: string | null,
): { readonly value: FusedGenerationPreference | null; readonly available: boolean } {
  if (key === null) return { value: null, available: true };
  try {
    const raw = storage.getItem(key);
    if (raw === null) return { value: null, available: true };
    const value: unknown = JSON.parse(raw);
    if (value === null || typeof value !== "object" || Array.isArray(value)) return { value: null, available: true };
    const record = value as Record<string, unknown>;
    if (typeof record.modelId !== "string" || typeof record.promptId !== "string"
      || typeof record.size !== "string") return { value: null, available: true };
    return { value: { modelId: record.modelId, promptId: record.promptId, size: record.size }, available: true };
  } catch {
    return { value: null, available: false };
  }
}

export function writeFusedGenerationPreference(
  storage: Pick<Storage, "setItem">,
  key: string | null,
  preference: FusedGenerationPreference,
): boolean {
  if (key === null) return false;
  try {
    storage.setItem(key, JSON.stringify(preference));
    return true;
  } catch {
    return false;
  }
}

export interface FusedImageSizeOptions {
  readonly values: readonly string[];
  readonly defaultValue: string;
}

function record(value: unknown): Record<string, unknown> | null {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

export function fusedImageSizeOptions(model: CreatorModel | null): FusedImageSizeOptions {
  const fallback = { values: ["1024x1024"], defaultValue: "1024x1024" };
  if (model?.params === null || model?.params === undefined || model.params === "") return fallback;
  let root: Record<string, unknown> | null = null;
  try {
    root = record(JSON.parse(model.params) as unknown);
  } catch {
    return fallback;
  }
  const config = record(root?.image_size);
  if (config === null || config.enabled === false || config.enabled === null || config.enabled === undefined) {
    return fallback;
  }
  if (config.enabled !== true || !Array.isArray(config.options)) return fallback;
  const values = [...new Set(config.options.filter((value): value is string => (
    typeof value === "string" && value.trim() !== "" && value.length <= 64
  )))];
  if (values.length === 0) return fallback;
  const defaultValue = typeof config.default === "string" && values.includes(config.default)
    ? config.default
    : values[0]!;
  return { values, defaultValue };
}

function selectedPromptPart(raw: string | null | undefined, memberType: FusedGenerationMemberType): string | null {
  if (typeof raw !== "string" || raw === "") return null;
  const trimmed = raw.trim();
  if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
    try {
      const parsed = record(JSON.parse(trimmed) as unknown);
      const value = parsed?.[memberType];
      return typeof value === "string" && value.trim() !== "" ? value : null;
    } catch {
      return null;
    }
  }
  return raw;
}

export function resolveFusedPrompt(
  prompt: CreatorPrompt | null,
  memberType: FusedGenerationMemberType,
): { readonly system: string | null; readonly user: string | null } {
  if (prompt === null || prompt.category !== "fuse") return { system: null, user: null };
  const system = typeof prompt.system_prompt === "string" && prompt.system_prompt !== ""
    ? prompt.system_prompt
    : null;
  return { system, user: selectedPromptPart(prompt.user_prompt, memberType) };
}

export function safeFusedImageUrl(value: string, apiBaseUrl: string | null): string | null {
  if (value.trim() !== value || value === "" || /[\u0000-\u0020\\]/u.test(value) || apiBaseUrl === null) {
    return null;
  }
  try {
    const api = new URL(apiBaseUrl);
    const image = new URL(value, api.origin + "/");
    if (image.username !== "" || image.password !== "" || image.hash !== ""
      || (image.protocol !== "http:" && image.protocol !== "https:")) return null;
    if (image.origin === api.origin) return image.href;
    return image.protocol === "https:" && /^https:\/\//iu.test(value) ? image.href : null;
  } catch {
    return null;
  }
}
