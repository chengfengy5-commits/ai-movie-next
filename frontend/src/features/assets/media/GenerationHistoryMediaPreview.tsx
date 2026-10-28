import { useEffect, useRef, useState } from "react";
import { ApiError } from "../../../shared/api/errors";
import { AssetGenerationHistoryError, type AssetGenerationHistoryMode } from "../../../shared/api/assetGenerationHistory";

interface GenerationHistoryMediaPreviewProps {
  readonly assetName: string;
  readonly mediaUrl: string | null;
  readonly unsafeMedia: boolean;
  readonly mode: AssetGenerationHistoryMode;
  readonly isCurrent: () => boolean;
  readonly onUnauthorized: () => void;
  readMedia(url: string, signal: AbortSignal): Promise<Blob>;
}

function isUnauthorized(error: unknown): boolean {
  return error instanceof AssetGenerationHistoryError
    ? error.kind === "unauthorized"
    : error instanceof ApiError && error.status === 401;
}

export function GenerationHistoryMediaPreview({
  assetName,
  mediaUrl,
  unsafeMedia,
  mode,
  isCurrent,
  onUnauthorized,
  readMedia,
}: GenerationHistoryMediaPreviewProps) {
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const [failureMessage, setFailureMessage] = useState<string | null>(null);
  const isCurrentRef = useRef(isCurrent);
  const unauthorizedRef = useRef(onUnauthorized);
  const readMediaRef = useRef(readMedia);
  isCurrentRef.current = isCurrent;
  unauthorizedRef.current = onUnauthorized;
  readMediaRef.current = readMedia;

  useEffect(() => {
    setFailureMessage(null);
    setObjectUrl(null);
    if (mediaUrl === null || unsafeMedia) return;

    const controller = new AbortController();
    let active = true;
    let createdUrl: string | null = null;
    void readMediaRef.current(mediaUrl, controller.signal).then((blob) => {
      if (!active || controller.signal.aborted || !isCurrentRef.current()) return;
      createdUrl = URL.createObjectURL(blob);
      setObjectUrl(createdUrl);
    }).catch((error: unknown) => {
      if (!active || controller.signal.aborted || !isCurrentRef.current()) return;
      const unauthorized = isUnauthorized(error);
      if (unauthorized) unauthorizedRef.current();
      setFailureMessage(unauthorized ? "登录状态已失效，请重新登录。" : "媒体读取或解码失败。");
    });
    return () => {
      active = false;
      controller.abort();
      if (createdUrl !== null) URL.revokeObjectURL(createdUrl);
    };
  }, [mediaUrl, mode, unsafeMedia]);

  if (mediaUrl === null || unsafeMedia) {
    return <p className="asset-generation-history-media-state">媒体地址不符合安全预览要求。</p>;
  }
  if (failureMessage !== null) return <p className="asset-generation-history-media-state">{failureMessage}</p>;
  if (objectUrl === null) {
    return <p className="asset-generation-history-media-state" role="status">正在读取媒体…</p>;
  }
  if (mode === "video") {
    return (
      <video
        aria-label={assetName + "生成视频"}
        controls
        onError={() => setFailureMessage("媒体读取或解码失败。")}
        playsInline
        preload="metadata"
        src={objectUrl}
      />
    );
  }
  return (
    <img
      alt={assetName + "生成图片"}
      loading="lazy"
      onError={() => setFailureMessage("媒体读取或解码失败。")}
      referrerPolicy="no-referrer"
      src={objectUrl}
    />
  );
}
