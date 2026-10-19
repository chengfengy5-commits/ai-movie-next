import type { AdminSeriesRecord } from "./adminSeriesTypes";

const COLUMNS: ReadonlyArray<{ field: keyof AdminSeriesRecord; label: string }> = [
  { field: "id", label: "剧集 ID" },
  { field: "user_id", label: "用户 ID" },
  { field: "name", label: "名称" },
  { field: "description", label: "简介" },
  { field: "image_url", label: "封面 URL" },
  { field: "created_at", label: "创建时间" },
  { field: "updated_at", label: "更新时间" },
];
const FORMULA_PREFIX = /^[\s\u0000-\u001F\uFEFF]*[=+\-@]/;

function cell(value: string | null): string {
  let text = value ?? "";
  if (FORMULA_PREFIX.test(text)) text = "'" + text;
  return '"' + text.replaceAll('"', '""') + '"';
}

export function serializeAdminSeriesCsv(records: readonly AdminSeriesRecord[]): string {
  const header = COLUMNS.map(({ label }) => cell(label)).join(",");
  const rows = records.map((record) => COLUMNS.map(({ field }) => cell(record[field])).join(","));
  return "\uFEFF" + [header, ...rows].join("\r\n");
}

export function downloadAdminSeriesCsv(csv: string, filename: string): void {
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.style.display = "none";
  try {
    document.body.appendChild(link);
    link.click();
  } finally {
    link.remove();
    URL.revokeObjectURL(url);
  }
}
