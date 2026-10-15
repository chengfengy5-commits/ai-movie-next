import { useRef, useState } from "react";
import type { SeriesExchangePort } from "../../shared/api/seriesExchange";
import { safeExportFileName } from "./fileImport";

const JSON_IMPORT_MAX_BYTES = 20 * 1024 * 1024;

interface SeriesExportButtonProps {
  seriesId: string;
  seriesName: string;
  port: SeriesExchangePort;
}

function getSeriesName(payload: unknown, fallback: string): string {
  if (typeof payload !== "object" || payload === null || Array.isArray(payload)) {
    return fallback;
  }
  const series = (payload as Record<string, unknown>).series;
  if (typeof series !== "object" || series === null || Array.isArray(series)) {
    return fallback;
  }
  const name = (series as Record<string, unknown>).name;
  return typeof name === "string" && name.trim() !== "" ? name : fallback;
}

export function SeriesExportButton({ seriesId, seriesName, port }: SeriesExportButtonProps) {
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const controllerRef = useRef<AbortController | null>(null);

  async function exportSeries() {
    if (busy) return;
    const controller = new AbortController();
    controllerRef.current = controller;
    setBusy(true);
    setNotice(null);
    let objectUrl: string | null = null;
    try {
      const payload = await port.exportSeries(seriesId, controller.signal);
      const serialized = JSON.stringify(payload, null, 2);
      if (serialized === undefined) {
        throw new Error("服务器没有返回可导出的剧集数据。");
      }
      const blob = new Blob([serialized], { type: "application/json;charset=utf-8" });
      objectUrl = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = safeExportFileName(getSeriesName(payload, seriesName));
      anchor.style.display = "none";
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      setNotice(blob.size > JSON_IMPORT_MAX_BYTES
        ? "导出文件已保存；文件超过 20 MiB，无法通过当前导入入口重新导入。"
        : "剧集 JSON 已导出。请注意，素材中的图片地址不会打包成图片文件。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "导出失败，请重试。");
    } finally {
      if (objectUrl !== null) {
        URL.revokeObjectURL(objectUrl);
      }
      if (controllerRef.current === controller) {
        controllerRef.current = null;
        setBusy(false);
      }
    }
  }

  return (
    <span className="series-export-control">
      <button
        aria-label={"导出剧集 JSON：" + seriesName}
        className="series-exchange-secondary-button"
        disabled={busy}
        onClick={() => void exportSeries()}
        type="button"
      >
        {busy ? "正在导出…" : "导出 JSON"}
      </button>
      {notice !== null && <span className="series-export-notice" role="status">{notice}</span>}
    </span>
  );
}
