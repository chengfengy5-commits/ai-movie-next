import type { AdminUserRecord } from "./usersTypes";

const CSV_COLUMNS: ReadonlyArray<{ field: keyof AdminUserRecord; label: string }> = [
  { field: "id", label: "用户 ID" },
  { field: "username", label: "用户名" },
  { field: "email", label: "邮箱" },
  { field: "is_superuser", label: "超级管理员" },
  { field: "membership_type", label: "会员类型" },
  { field: "membership_expires_at", label: "会员到期时间" },
  { field: "credits", label: "积分" },
  { field: "created_at", label: "注册时间" },
];

const FORMULA_PREFIX = /^[\s\u0000-\u001F\uFEFF]*[=+\-@]/;

function csvCell(value: string | number | boolean | null): string {
  let text = value === null ? "" : String(value);
  if (typeof value === "string" && FORMULA_PREFIX.test(value)) {
    text = "'" + text;
  }
  return '"' + text.replaceAll('"', '""') + '"';
}

export function serializeAdminUsersCsv(records: readonly AdminUserRecord[]): string {
  const header = CSV_COLUMNS.map(({ label }) => csvCell(label)).join(",");
  const rows = records.map((record) => CSV_COLUMNS
    .map(({ field }) => csvCell(record[field]))
    .join(","));
  return "\uFEFF" + [header, ...rows].join("\r\n");
}

export function downloadAdminUsersCsv(csv: string, filename: string): void {
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
