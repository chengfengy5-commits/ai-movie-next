import type { AdminTaskRecord } from "./adminTasksTypes";

const COLUMNS: ReadonlyArray<{ field: keyof AdminTaskRecord; label: string; numeric?: boolean }> = [
  { field: "id", label: "任务 ID" },
  { field: "user_id", label: "用户 ID" },
  { field: "type", label: "类型" },
  { field: "status", label: "状态" },
  { field: "credit_cost", label: "消耗积分", numeric: true },
  { field: "message_id", label: "消息 ID" },
  { field: "model_name", label: "模型" },
  { field: "created_at", label: "创建时间" },
  { field: "updated_at", label: "更新时间" },
];

const FORMULA_PREFIX = /^[\s\u0000-\u001F\uFEFF]*[=+\-@]/;

function textCell(value: string | null): string {
  let text = value ?? "";
  if (FORMULA_PREFIX.test(text)) text = "'" + text;
  return '"' + text.replaceAll('"', '""') + '"';
}

export function serializeAdminTasksCsv(records: readonly AdminTaskRecord[]): string {
  const header = COLUMNS.map(({ label }) => textCell(label)).join(",");
  const rows = records.map((record) => COLUMNS.map(({ field, numeric }) => (
    numeric ? String(record[field]) : textCell(record[field] as string | null)
  )).join(","));
  return "\uFEFF" + [header, ...rows].join("\r\n");
}

export function downloadAdminTasksCsv(csv: string, filename: string): void {
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
