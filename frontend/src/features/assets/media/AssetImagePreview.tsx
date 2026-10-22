import { useEffect, useRef, useState } from "react";
import {
  type AssetImageSource,
  type AssetImageUploadReceipt,
  type AssetImageUploadRun,
} from "../../../shared/api/assetImageUpload";

type PreviewSource = AssetImageSource | AssetImageUploadReceipt;

interface AssetImagePreviewProps {
  alt: string;
  run: AssetImageUploadRun;
  source?: PreviewSource | null;
  file?: File | null;
}

type PreviewState =
  | { kind: "loading" }
  | { kind: "ready"; url: string; request: PreviewRequest }
  | { kind: "error"; message: string };

interface PreviewRequest {
  id: number;
  cancelled: boolean;
  node: HTMLImageElement | null;
  run: AssetImageUploadRun;
  source: PreviewSource | null;
  file: File | null;
  objectUrl: string | null;
}

function releaseObjectUrl(request: PreviewRequest): void {
  if (request.objectUrl === null) return;
  const url = request.objectUrl;
  request.objectUrl = null;
  URL.revokeObjectURL(url);
}

export function AssetImagePreview({ alt, run, source = null, file = null }: AssetImagePreviewProps) {
  const [preview, setPreview] = useState<PreviewState>({ kind: "loading" });
  const [visibleImageId, setVisibleImageId] = useState<number | null>(null);
  const activeRef = useRef<PreviewRequest | null>(null);
  const requestIdRef = useRef(0);

  useEffect(() => {
    const request: PreviewRequest = {
      id: ++requestIdRef.current,
      cancelled: false,
      node: null,
      run,
      source,
      file,
      objectUrl: null,
    };
    activeRef.current = request;
    const controller = new AbortController();
    setPreview({ kind: "loading" });

    const resolvePreview = async () => {
      try {
        const blob = file ?? (source === null ? null : await run.readImage(source, controller.signal));
        if (blob === null) {
          throw new Error("没有可读取的图片来源。");
        }
        if (request.cancelled || controller.signal.aborted) return;
        if (!run.isCurrent()) {
          setPreview({ kind: "error", message: "登录或目录状态已变化，图片未显示。" });
          return;
        }
        const objectUrl = URL.createObjectURL(blob);
        request.objectUrl = objectUrl;
        setPreview({ kind: "ready", url: objectUrl, request });
      } catch (error) {
        if (request.cancelled || controller.signal.aborted) return;
        releaseObjectUrl(request);
        setPreview({
          kind: "error",
          message: error instanceof Error ? error.message : "图片读取失败。",
        });
      }
    };

    void resolvePreview();
    return () => {
      request.cancelled = true;
      controller.abort();
      if (activeRef.current === request) activeRef.current = null;
      releaseObjectUrl(request);
    };
  }, [file, run, source]);

  const matchingPreview = preview.kind === "ready"
    && preview.request.run === run
    && preview.request.source === source
    && preview.request.file === file
    && run.isCurrent()
    ? preview
    : null;

  if (preview.kind === "loading") {
    return <p className="asset-image-preview-state" role="status">正在读取图片…</p>;
  }
  if (preview.kind === "error") {
    return <p className="asset-image-preview-state" role="status">{preview.message}</p>;
  }
  if (matchingPreview === null) {
    return <p className="asset-image-preview-state" role="status">正在读取图片…</p>;
  }

  return (
    <div className="asset-image-preview-frame">
      <img
        alt={alt}
        className={visibleImageId === matchingPreview.request.id ? "asset-image-preview-image loaded" : "asset-image-preview-image"}
        key={matchingPreview.request.id}
        onError={(event) => {
          const request = matchingPreview.request;
          if (activeRef.current !== request || request.cancelled || request.node !== event.currentTarget) return;
          releaseObjectUrl(request);
          setPreview({ kind: "error", message: "图片无法解码或显示。" });
        }}
        onLoad={(event) => {
          const request = matchingPreview.request;
          if (activeRef.current !== request || request.cancelled || request.node !== event.currentTarget) return;
          if (!run.isCurrent()) {
            releaseObjectUrl(request);
            setPreview({ kind: "error", message: "登录或目录状态已变化，图片未显示。" });
            return;
          }
          if (event.currentTarget.naturalWidth <= 0 || event.currentTarget.naturalHeight <= 0) {
            releaseObjectUrl(request);
            setPreview({ kind: "error", message: "图片没有可显示的像素。" });
          } else {
            setVisibleImageId(request.id);
          }
        }}
        ref={(node) => {
          const request = matchingPreview.request;
          if (activeRef.current === request && !request.cancelled) request.node = node;
        }}
        src={matchingPreview.url}
      />
    </div>
  );
}
