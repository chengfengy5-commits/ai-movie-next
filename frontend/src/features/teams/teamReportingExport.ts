import type { TeamUsageExport, TeamUsageExportRow } from "../../shared/api/teamReporting";

export const TEAM_REPORTING_SPREADSHEET_MIME = "application/vnd.ms-excel;charset=utf-8;";

function escapeXml(value: string): string {
  const validCharacters = Array.from(value).filter((character) => {
    const codePoint = character.codePointAt(0)!;
    return codePoint === 0x9 || codePoint === 0xa || codePoint === 0xd
      || (codePoint >= 0x20 && codePoint <= 0xd7ff)
      || (codePoint >= 0xe000 && codePoint <= 0xfffd)
      || (codePoint >= 0x10000 && codePoint <= 0x10ffff);
  }).join("");
  return validCharacters
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

function stringCell(styleId: string, value: string | null | undefined): string {
  return `<Cell ss:StyleID="${styleId}"><Data ss:Type="String">${escapeXml(value ?? "")}</Data></Cell>`;
}

function numberCell(styleId: string, value: number): string {
  if (!Number.isFinite(value)) throw new RangeError("导出数值超出有效范围。");
  return `<Cell ss:StyleID="${styleId}"><Data ss:Type="Number">${String(value)}</Data></Cell>`;
}

function headerRow(headers: string[]): string {
  return `<Row ss:Height="22">${headers.map((header) => stringCell("Header", header)).join("")}</Row>`;
}

function addFinite(left: number, right: number): number {
  const sum = left + right;
  if (!Number.isFinite(sum)) throw new RangeError("导出统计值超出有效范围。");
  return sum;
}

function aggregateModels(rows: TeamUsageExportRow[]): Map<string, Map<string, { calls: number; failedCalls: number }>> {
  const byMember = new Map<string, Map<string, { calls: number; failedCalls: number }>>();
  for (const row of rows) {
    let models = byMember.get(row.user_id);
    if (models === undefined) {
      models = new Map();
      byMember.set(row.user_id, models);
    }
    const usage = models.get(row.model_name) ?? { calls: 0, failedCalls: 0 };
    usage.calls = addFinite(usage.calls, row.calls);
    usage.failedCalls = addFinite(usage.failedCalls, row.failed_calls);
    models.set(row.model_name, usage);
  }
  return byMember;
}

function modelSummaryText(
  usage: { calls: number; failedCalls: number } | undefined,
  chapterCount: number,
): string {
  if (usage === undefined || (usage.calls === 0 && usage.failedCalls === 0)) return "";
  const average = chapterCount > 0 ? usage.calls / chapterCount : 0;
  const averageText = Number.isInteger(average) ? String(average) : average.toFixed(1);
  return `成功${usage.calls}次\n平均每章${averageText}次`;
}

function styles(): string {
  return "<Styles>"
    + '<Style ss:ID="Default" ss:Name="Normal"><Font ss:FontName="Microsoft YaHei" ss:Size="11"/></Style>'
    + '<Style ss:ID="Header"><Alignment ss:Horizontal="Center" ss:Vertical="Center"/><Font ss:FontName="Microsoft YaHei" ss:Bold="1" ss:Color="#FFFFFF"/><Interior ss:Color="#2D2D2D" ss:Pattern="Solid"/></Style>'
    + '<Style ss:ID="Text"><Alignment ss:Vertical="Center"/><Borders><Border ss:Position="Bottom" ss:LineStyle="Continuous" ss:Weight="1" ss:Color="#D9D9D9"/></Borders></Style>'
    + '<Style ss:ID="CenterText"><Alignment ss:Horizontal="Center" ss:Vertical="Center"/><Borders><Border ss:Position="Bottom" ss:LineStyle="Continuous" ss:Weight="1" ss:Color="#D9D9D9"/></Borders></Style>'
    + '<Style ss:ID="Num"><Alignment ss:Horizontal="Right" ss:Vertical="Center"/><Borders><Border ss:Position="Bottom" ss:LineStyle="Continuous" ss:Weight="1" ss:Color="#D9D9D9"/></Borders></Style>'
    + '<Style ss:ID="Wrap"><Alignment ss:Horizontal="Center" ss:Vertical="Center" ss:WrapText="1"/><Borders><Border ss:Position="Bottom" ss:LineStyle="Continuous" ss:Weight="1" ss:Color="#D9D9D9"/></Borders></Style>'
    + '<Style ss:ID="AltText"><Alignment ss:Vertical="Center"/><Interior ss:Color="#F5F5F5" ss:Pattern="Solid"/><Borders><Border ss:Position="Bottom" ss:LineStyle="Continuous" ss:Weight="1" ss:Color="#D9D9D9"/></Borders></Style>'
    + '<Style ss:ID="AltCenterText"><Alignment ss:Horizontal="Center" ss:Vertical="Center"/><Interior ss:Color="#F5F5F5" ss:Pattern="Solid"/><Borders><Border ss:Position="Bottom" ss:LineStyle="Continuous" ss:Weight="1" ss:Color="#D9D9D9"/></Borders></Style>'
    + '<Style ss:ID="AltNum"><Alignment ss:Horizontal="Right" ss:Vertical="Center"/><Interior ss:Color="#F5F5F5" ss:Pattern="Solid"/><Borders><Border ss:Position="Bottom" ss:LineStyle="Continuous" ss:Weight="1" ss:Color="#D9D9D9"/></Borders></Style>'
    + '<Style ss:ID="AltWrap"><Alignment ss:Horizontal="Center" ss:Vertical="Center" ss:WrapText="1"/><Interior ss:Color="#F5F5F5" ss:Pattern="Solid"/><Borders><Border ss:Position="Bottom" ss:LineStyle="Continuous" ss:Weight="1" ss:Color="#D9D9D9"/></Borders></Style>'
    + "</Styles>";
}

export function buildTeamReportingSpreadsheetXml(data: TeamUsageExport): string {
  const memberModels = aggregateModels(data.rows);
  const summaryHeaders = ["用户名", "修改章节数", "成功调用总次数", "消耗积分", "失败总次数", ...data.model_order];
  const detailHeaders = ["用户名", "所属剧集", "章节", "模型", "成功调用次数", "消耗积分", "失败次数"];
  const modelColumns = data.model_order.map(() => '<Column ss:Width="40"/>').join("");

  const summaryRows = data.members.map((member, index) => {
    const prefix = index % 2 === 0 ? "" : "Alt";
    const modelValues = data.model_order.map((modelName) => stringCell(
      `${prefix}Wrap`,
      modelSummaryText(memberModels.get(member.user_id)?.get(modelName), member.chapter_count),
    )).join("");
    return `<Row>${stringCell(`${prefix}CenterText`, member.username || member.user_id)}`
      + numberCell(`${prefix}Num`, member.chapter_count)
      + numberCell(`${prefix}Num`, member.calls)
      + numberCell(`${prefix}Num`, member.credits)
      + numberCell(`${prefix}Num`, member.failed_calls)
      + modelValues
      + "</Row>";
  }).join("");

  const detailRows = data.rows.map((row, index) => {
    const prefix = index % 2 === 0 ? "" : "Alt";
    return `<Row>${stringCell(`${prefix}CenterText`, row.username || row.user_id)}`
      + stringCell(`${prefix}Text`, row.series_name)
      + stringCell(`${prefix}Text`, row.chapter_title)
      + stringCell(`${prefix}Text`, row.model_name)
      + numberCell(`${prefix}Num`, row.calls)
      + numberCell(`${prefix}Num`, row.credits)
      + numberCell(`${prefix}Num`, row.failed_calls)
      + "</Row>";
  }).join("");

  return '<?xml version="1.0" encoding="UTF-8"?>\n'
    + '<?mso-application progid="Excel.Sheet"?>\n'
    + '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:x="urn:schemas-microsoft-com:office:excel" xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet" xmlns:html="http://www.w3.org/TR/REC-html40">'
    + styles()
    + '<Worksheet ss:Name="汇总"><Table>'
    + '<Column ss:Width="90"/><Column ss:Width="70"/><Column ss:Width="90"/><Column ss:Width="70"/><Column ss:Width="70"/>'
    + modelColumns
    + headerRow(summaryHeaders)
    + summaryRows
    + '</Table></Worksheet><Worksheet ss:Name="明细"><Table>'
    + '<Column ss:Width="90"/><Column ss:Width="150"/><Column ss:Width="120"/><Column ss:Width="180"/><Column ss:Width="80"/><Column ss:Width="80"/><Column ss:Width="80"/>'
    + headerRow(detailHeaders)
    + detailRows
    + "</Table></Worksheet></Workbook>";
}

export function buildTeamReportingSpreadsheetBlob(data: TeamUsageExport): Blob {
  return new Blob([`\uFEFF${buildTeamReportingSpreadsheetXml(data)}`], {
    type: TEAM_REPORTING_SPREADSHEET_MIME,
  });
}
