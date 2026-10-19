import type { AdminSmtpAccountRecord } from "./adminSmtpAccountsTypes";

const COLUMNS: ReadonlyArray<{ field: keyof AdminSmtpAccountRecord; label: string; numeric?: boolean }> = [
  { field: "id", label: "账号 ID" },
  { field: "user", label: "邮箱" },
  { field: "host", label: "SMTP 主机" },
  { field: "port", label: "端口", numeric: true },
  { field: "daily_sent", label: "今日发送量", numeric: true },
  { field: "is_active", label: "已启用" },
  { field: "created_at", label: "创建时间" },
  { field: "updated_at", label: "更新时间" },
];
const FORMULA_PREFIX = /^[\s\u0000-\u001F\uFEFF]*[=+\-@]/;

function textCell(value: string | null): string {
  let text = value ?? "";
  if (FORMULA_PREFIX.test(text)) text = "'" + text;
  return '"' + text.replaceAll('"', '""') + '"';
}

export function serializeAdminSmtpAccountsCsv(records: readonly AdminSmtpAccountRecord[]): string {
  const header = COLUMNS.map(({ label }) => textCell(label)).join(",");
  const rows = records.slice(0, 1000).map((record) => COLUMNS.map(({ field, numeric }) => {
    const value = record[field];
    if (numeric && typeof value === "number") return String(value);
    if (field === "is_active") return textCell(value ? "是" : "否");
    return textCell(typeof value === "string" ? value : null);
  }).join(","));
  return "\uFEFF" + [header, ...rows].join("\r\n");
}

export function downloadAdminSmtpAccountsCsv(csv: string, filename: string): void {
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
