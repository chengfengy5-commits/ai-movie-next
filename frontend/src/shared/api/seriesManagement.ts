import { InvalidResponseError } from "./contracts";

export interface CreateSeriesInput {
  name: string;
  description: string;
  image_url: string | null;
  style_prompt_id: string | null;
}

export interface UpdateSeriesInput {
  name?: string;
  description?: string;
  image_url?: string;
  style_prompt_id?: string;
}

export interface StylePromptChoice {
  id: string;
  name: string;
  is_system: boolean;
  user_id: string | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function parseStylePromptChoices(value: unknown): StylePromptChoice[] {
  if (!Array.isArray(value)) {
    throw new InvalidResponseError("服务器返回的风格提示词列表格式无效。");
  }
  return value.map((entry) => {
    if (!isRecord(entry) || typeof entry.id !== "string" || entry.id.length === 0
      || typeof entry.name !== "string" || entry.name.length === 0
      || (entry.category !== undefined && entry.category !== "style")
      || typeof entry.is_system !== "boolean"
      || (entry.user_id !== undefined && entry.user_id !== null && typeof entry.user_id !== "string")) {
      throw new InvalidResponseError("风格提示词列表中包含格式无效的项目。");
    }
    return {
      id: entry.id,
      name: entry.name,
      is_system: entry.is_system === true,
      user_id: typeof entry.user_id === "string" ? entry.user_id : null,
    };
  });
}

export function parseUploadedSeriesCover(value: unknown): string {
  if (!isRecord(value) || typeof value.url !== "string" || value.url.trim() === "") {
    throw new InvalidResponseError("服务器没有返回有效的封面地址。");
  }
  return value.url.trim();
}
