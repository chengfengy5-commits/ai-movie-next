import { ApiError } from "../../shared/api/errors";

export const JSON_IMPORT_MAX_BYTES = 20 * 1024 * 1024;
export const MARKDOWN_FILE_MAX_BYTES = 2 * 1024 * 1024;
export const MARKDOWN_IMPORT_MAX_FILES = 50;
export const MARKDOWN_IMPORT_MAX_TOTAL_BYTES = 20 * 1024 * 1024;

export interface JsonSeriesExportV1 {
  version: 1;
  [key: string]: unknown;
}

export interface MarkdownChapterFile {
  key: string;
  fileName: string;
  title: string;
  text: string;
}

export class SeriesExchangeFileError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "SeriesExchangeFileError";
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function validateObjectArray(value: unknown, label: string): void {
  if (!Array.isArray(value) || value.some((entry) => !isRecord(entry))) {
    throw new SeriesExchangeFileError(label + "必须是对象数组。");
  }
}

export function validateSeriesExportV1(value: unknown): JsonSeriesExportV1 {
  if (!isRecord(value) || value.version !== 1) {
    throw new SeriesExchangeFileError("只支持版本号为 1 的剧集 JSON 文件。");
  }
  if (value.series !== undefined && !isRecord(value.series)) {
    throw new SeriesExchangeFileError("剧集信息格式无效。");
  }
  if (!Array.isArray(value.chapters)) {
    throw new SeriesExchangeFileError("章节列表必须是数组。");
  }
  value.chapters.forEach((chapter, index) => {
    if (!isRecord(chapter) || !Array.isArray(chapter.frames)) {
      throw new SeriesExchangeFileError("第 " + (index + 1) + " 个章节的分镜列表格式无效。");
    }
    validateObjectArray(chapter.frames, "第 " + (index + 1) + " 个章节的分镜列表");
  });
  for (const key of ["characters", "scenes", "props", "storyboard_assets"] as const) {
    if (value[key] !== undefined) {
      validateObjectArray(value[key], key + " 列表");
    }
  }

  // The input came from JSON.parse, so this copy retains every JSON field without
  // carrying references into UI state or assigning untrusted keys to a config object.
  return JSON.parse(JSON.stringify(value)) as JsonSeriesExportV1;
}

function fileExtension(fileName: string): string {
  const dot = fileName.lastIndexOf(".");
  return dot < 0 ? "" : fileName.slice(dot).toLowerCase();
}

function assertJsonFile(file: File): void {
  if (fileExtension(file.name) !== ".json") {
    throw new SeriesExchangeFileError("请选择一个 .json 文件。");
  }
  if (file.size > JSON_IMPORT_MAX_BYTES) {
    throw new SeriesExchangeFileError("JSON 文件不能超过 20 MiB；文件尚未读取。");
  }
}

export async function readJsonSeriesExport(file: File): Promise<JsonSeriesExportV1> {
  assertJsonFile(file);
  let source: string;
  try {
    source = await file.text();
  } catch {
    throw new SeriesExchangeFileError("无法读取所选 JSON 文件。");
  }
  try {
    return validateSeriesExportV1(JSON.parse(source.replace(/^\uFEFF/, "")) as unknown);
  } catch (error) {
    if (error instanceof SeriesExchangeFileError) {
      throw error;
    }
    throw new SeriesExchangeFileError("JSON 文件内容无法解析。");
  }
}

function parseChineseNumber(value: string): bigint | null {
  const digitValues: Record<string, number> = {
    零: 0, 〇: 0, 一: 1, 二: 2, 两: 2, 三: 3, 四: 4, 五: 5,
    六: 6, 七: 7, 八: 8, 九: 9,
  };
  let section = 0;
  let total = 0;
  let number = 0;
  for (const character of value) {
    if (character in digitValues) {
      number = digitValues[character]!;
      continue;
    }
    if (character === "十" || character === "百" || character === "千") {
      const unit = character === "十" ? 10 : character === "百" ? 100 : 1000;
      section += (number || (unit === 10 ? 1 : 0)) * unit;
      number = 0;
      continue;
    }
    if (character === "万") {
      section += number;
      total += (section || 1) * 10000;
      section = 0;
      number = 0;
      continue;
    }
    return null;
  }
  return BigInt(total + section + number);
}

interface NaturalPart {
  numeric: bigint | null;
  text: string;
}

const CHINESE_NUMERALS = "零〇一二两三四五六七八九十百千万";
const NATURAL_COLLATOR = new Intl.Collator("zh-CN", { numeric: true, sensitivity: "base" });

function naturalParts(value: string): NaturalPart[] {
  const parts: NaturalPart[] = [];
  const matcher = new RegExp("[0-9]+|[" + CHINESE_NUMERALS + "]+", "g");
  let offset = 0;
  for (const match of value.matchAll(matcher)) {
    const index = match.index;
    if (index > offset) {
      parts.push({ numeric: null, text: value.slice(offset, index) });
    }
    const text = match[0];
    const numeric = /^[0-9]+$/.test(text) ? BigInt(text) : parseChineseNumber(text);
    parts.push({ numeric, text });
    offset = index + text.length;
  }
  if (offset < value.length) {
    parts.push({ numeric: null, text: value.slice(offset) });
  }
  return parts;
}

export function compareNaturalFileNames(left: string, right: string): number {
  const leftParts = naturalParts(left);
  const rightParts = naturalParts(right);
  const length = Math.min(leftParts.length, rightParts.length);
  for (let index = 0; index < length; index += 1) {
    const a = leftParts[index]!;
    const b = rightParts[index]!;
    if (a.numeric !== null && b.numeric !== null) {
      if (a.numeric < b.numeric) return -1;
      if (a.numeric > b.numeric) return 1;
      continue;
    }
    const order = NATURAL_COLLATOR.compare(a.text, b.text);
    if (order !== 0) return order;
  }
  return leftParts.length - rightParts.length || NATURAL_COLLATOR.compare(left, right);
}

export function prepareMarkdownFiles(files: FileList | readonly File[]): File[] {
  if (files.length === 0) {
    throw new SeriesExchangeFileError("请选择至少一个 .md 文件。");
  }
  if (files.length > MARKDOWN_IMPORT_MAX_FILES) {
    throw new SeriesExchangeFileError("一次最多导入 50 个 Markdown 文件；文件尚未读取。");
  }
  if (Array.from(files).some((file) => fileExtension(file.name) !== ".md")) {
    throw new SeriesExchangeFileError("JSON 和 Markdown 不能混合导入，请一次只选择一种格式。");
  }
  if (Array.from(files).some((file) => file.size > MARKDOWN_FILE_MAX_BYTES)) {
    throw new SeriesExchangeFileError("每个 Markdown 文件不能超过 2 MiB；文件尚未读取。");
  }
  const totalBytes = Array.from(files).reduce((sum, file) => sum + file.size, 0);
  if (totalBytes > MARKDOWN_IMPORT_MAX_TOTAL_BYTES) {
    throw new SeriesExchangeFileError("Markdown 文件总量不能超过 20 MiB；文件尚未读取。");
  }
  return Array.from(files).sort((left, right) => compareNaturalFileNames(left.name, right.name));
}

function titleFromFileName(fileName: string): string {
  return fileName.replace(/\.md$/i, "");
}

export async function readMarkdownChapterFiles(files: readonly File[]): Promise<MarkdownChapterFile[]> {
  const prepared = prepareMarkdownFiles(files);
  const chapters: MarkdownChapterFile[] = [];
  for (const file of prepared) {
    let text: string;
    try {
      text = await file.text();
    } catch {
      throw new SeriesExchangeFileError("无法读取文件《" + file.name + "》。");
    }
    chapters.push({
      key: file.name + ":" + file.size + ":" + chapters.length,
      fileName: file.name,
      title: titleFromFileName(file.name),
      text,
    });
  }
  return chapters;
}

export function isUnknownWriteOutcome(error: unknown): boolean {
  if (!(error instanceof ApiError)) {
    return true;
  }
  return error.kind === "network"
    || error.kind === "timeout"
    || error.kind === "invalid-response"
    || (error.status !== null && error.status >= 500);
}

export function getFileErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "文件处理失败，请重试。";
}

export function safeExportFileName(value: unknown): string {
  const source = typeof value === "string" ? value : "";
  const safe = source
    .replace(/[\\/:*?"<>|\u0000-\u001f\u007f]/g, "_")
    .trim()
    .replace(/[. ]+$/g, "");
  return (safe || "haoai-series-export") + ".json";
}
