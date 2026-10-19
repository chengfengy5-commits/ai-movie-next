// @vitest-environment node
import { describe, expect, it } from "vitest";
import { parseTeamUsageExport } from "../../shared/api/teamReporting";
import { buildTeamReportingSpreadsheetBlob, buildTeamReportingSpreadsheetXml } from "./teamReportingExport";

describe("team reporting SpreadsheetML export", () => {
  it("writes summary and detail sheets with escaped text, ordered models, and numeric zero/negative values", async () => {
    const data = parseTeamUsageExport({
      rows: [
        {
          user_id: "user-1", username: "=SUM(1&'<name>)", avatar_url: null,
          series_id: "series-1", series_name: "北境 <一> & 二", chapter_id: "chapter-1", chapter_title: "第1章",
          model_name: "model<&>", calls: 0, credits: 0, failed_calls: 2,
        },
        {
          user_id: "user-1", username: "=SUM(1&'<name>)", avatar_url: null,
          series_id: "series-1", series_name: "北境 <一> & 二", chapter_id: "chapter-1", chapter_title: "第1章",
          model_name: "模型乙", calls: 3, credits: -3, failed_calls: 0,
        },
      ],
      members: [{
        user_id: "user-1", username: "=SUM(1&'<name>)", avatar_url: null,
        chapter_count: 1, models: ["model<&>", "模型乙"], calls: 3, credits: -3, failed_calls: 2,
      }],
      model_order: ["模型乙", "model<&>"],
      total_calls: 3,
      total_credits: -3,
      total_failed: 2,
    });

    const xml = buildTeamReportingSpreadsheetXml(data);
    const blob = buildTeamReportingSpreadsheetBlob(data);
    const bytes = new Uint8Array(await blob.arrayBuffer());
    const decoded = new TextDecoder().decode(bytes);

    expect(blob.type).toBe("application/vnd.ms-excel;charset=utf-8;");
    expect(Array.from(bytes.slice(0, 3))).toEqual([0xef, 0xbb, 0xbf]);
    expect(decoded).toBe(xml);
    expect(xml.match(/<Worksheet ss:Name=/g)).toHaveLength(2);
    expect(xml).toContain('<Worksheet ss:Name="汇总">');
    expect(xml).toContain('<Worksheet ss:Name="明细">');
    expect(xml.indexOf(">模型乙</Data>")).toBeLessThan(xml.indexOf(">model&lt;&amp;&gt;</Data>"));
    expect(xml).toContain("=SUM(1&amp;&apos;&lt;name&gt;)");
    expect(xml).not.toContain("=SUM(1&'");
    expect(xml).toContain("北境 &lt;一&gt; &amp; 二");
    expect(xml).toContain("<Data ss:Type=\"Number\">-3</Data>");
    expect(xml).toContain("<Data ss:Type=\"Number\">0</Data>");
    expect(xml).toContain("成功0次\n平均每章0次");
  });

  it("keeps a no-series export valid with two empty sheets and no model columns", () => {
    const empty = parseTeamUsageExport({ rows: [], members: [], total_calls: 0, total_credits: 0, total_failed: 0 });
    const xml = buildTeamReportingSpreadsheetXml(empty);

    expect(empty.model_order).toEqual([]);
    expect(xml.match(/<Worksheet ss:Name=/g)).toHaveLength(2);
    expect(xml).toContain("<Row ss:Height=\"22\">");
    expect(xml).not.toContain('<Column ss:Width="40"/>');
    expect(xml).toContain('<Worksheet ss:Name="汇总"><Table>');
    expect(xml).toContain('<Worksheet ss:Name="明细"><Table>');
  });
});
