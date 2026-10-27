import type { Series } from "../../shared/api/contracts";
import type { CreatorPrompt } from "../../shared/api/creatorSettings";

export type QuickReferenceStyleCategory = "video" | "storyboard" | "character" | "scene" | "prop";

export interface QuickReferenceStyle {
  readonly promptText: string;
  readonly imageUrl: string | null;
}

interface ResolveQuickReferenceStyleInput {
  readonly series: Pick<Series, "style_prompt_id" | "style_prompt">;
  readonly prompts: readonly CreatorPrompt[];
  readonly category: QuickReferenceStyleCategory;
  readonly fallbackCategories?: readonly QuickReferenceStyleCategory[];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

type ParsedStyle =
  | { readonly kind: "text"; readonly text: string }
  | { readonly kind: "object"; readonly value: Record<string, unknown> }
  | { readonly kind: "invalid" };

function parseStyle(raw: string): ParsedStyle {
  const trimmed = raw.trim();
  try {
    const value: unknown = JSON.parse(raw);
    return isRecord(value) ? { kind: "object", value } : { kind: "invalid" };
  } catch {
    if (trimmed.startsWith("[") || !trimmed.startsWith("{")) {
      return trimmed.startsWith("[") ? { kind: "invalid" } : { kind: "text", text: raw.trim() };
    }
    try {
      const repaired = trimmed.replace(/"([^"]*)"/g, (_match, inner: string) => (
        `"${inner.replace(/\n/gu, "\\n").replace(/\r/gu, "")}"`
      ));
      const value: unknown = JSON.parse(repaired);
      return isRecord(value) ? { kind: "object", value } : { kind: "invalid" };
    } catch {
      return { kind: "invalid" };
    }
  }
}

function selectedPromptText(series: ResolveQuickReferenceStyleInput["series"], prompts: readonly CreatorPrompt[]): string {
  if (series.style_prompt_id === null || series.style_prompt_id.trim() === "") return "";
  const selected = prompts.find((prompt) => prompt.id === series.style_prompt_id);
  if (selected !== undefined) return selected.user_prompt?.trim() || selected.system_prompt?.trim() || "";
  return series.style_prompt?.trim() ?? "";
}

function textForCategoryChain(
  value: Record<string, unknown>,
  categories: readonly QuickReferenceStyleCategory[],
): string {
  for (const category of categories) {
    const candidate = value[category];
    if (typeof candidate === "string" && candidate.trim() !== "") return candidate.trim();
  }
  const textEntries = Object.entries(value).filter(([key, candidate]) => (
    typeof candidate === "string" && candidate.trim() !== "" && !/_image$/iu.test(key)
  ));
  const general = textEntries.find(([key]) => /^(general|default|all|base|common|text|style)$/iu.test(key));
  if (general !== undefined) return String(general[1]).trim();
  return textEntries.length === 1 ? String(textEntries[0]?.[1]).trim() : "";
}

function imageForCategoryChain(
  value: Record<string, unknown>,
  categories: readonly QuickReferenceStyleCategory[],
): string | null {
  for (const category of categories) {
    const candidate = value[`${category}_image`];
    if (typeof candidate === "string" && candidate.trim() !== "") return candidate.trim();
  }
  return null;
}

export function resolveQuickReferenceStyle(input: ResolveQuickReferenceStyleInput): QuickReferenceStyle {
  const raw = selectedPromptText(input.series, input.prompts);
  if (raw === "") return { promptText: "", imageUrl: null };

  const parsed = parseStyle(raw);
  if (parsed.kind === "invalid") return { promptText: "", imageUrl: null };
  if (parsed.kind === "text") {
    return { promptText: `【风格提示词】\n${parsed.text}`, imageUrl: null };
  }

  const categories = [input.category, ...(input.fallbackCategories ?? [])];
  const text = textForCategoryChain(parsed.value, categories);
  const imageUrl = imageForCategoryChain(parsed.value, categories);
  return {
    promptText: text === "" ? "" : `【风格提示词】\n${text}`,
    imageUrl,
  };
}
