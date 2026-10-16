import type { AdminPurchasePackage, AdminRedemptionCode } from "./adminMembershipTypes";

const FORMULA_PREFIX = /^[\s\u0000-\u001F\uFEFF]*[=+\-@]/;

function cell(value: string | number | boolean | null): string {
  let text = value === null ? "" : String(value);
  if (typeof value === "string" && FORMULA_PREFIX.test(value)) text = "'" + text;
  return '"' + text.replaceAll('"', '""') + '"';
}

export function serializeRedemptionCodesCsv(records: readonly AdminRedemptionCode[]): string {
  const columns: Array<[string, (record: AdminRedemptionCode) => string | number | boolean | null]> = [
    ["ID", (record) => record.id],
    ["兑换码", (record) => record.code],
    ["类型", (record) => record.type],
    ["面值", (record) => record.value],
    ["已使用", (record) => record.is_used],
    ["使用者", (record) => record.used_by],
    ["使用时间", (record) => record.used_at],
    ["创建管理员", (record) => record.created_by],
    ["过期时间", (record) => record.expires_at],
    ["创建时间", (record) => record.created_at],
  ];
  return "\uFEFF" + [
    columns.map(([label]) => cell(label)).join(","),
    ...records.map((record) => columns.map(([, read]) => cell(read(record))).join(",")),
  ].join("\r\n");
}

export function serializePurchasePackagesCsv(records: readonly AdminPurchasePackage[]): string {
  const columns: Array<[string, (record: AdminPurchasePackage) => string | number | boolean | null]> = [
    ["ID", (record) => record.id],
    ["类别", (record) => record.category],
    ["名称", (record) => record.name],
    ["价格（元）", (record) => record.price],
    ["积分", (record) => record.credits],
    ["会员天数", (record) => record.days],
    ["排序", (record) => record.sort_order],
    ["启用", (record) => record.is_active],
    ["创建时间", (record) => record.created_at],
    ["更新时间", (record) => record.updated_at],
  ];
  return "\uFEFF" + [
    columns.map(([label]) => cell(label)).join(","),
    ...records.map((record) => columns.map(([, read]) => cell(read(record))).join(",")),
  ].join("\r\n");
}

export function downloadMembershipCsv(csv: string, filename: string): void {
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
