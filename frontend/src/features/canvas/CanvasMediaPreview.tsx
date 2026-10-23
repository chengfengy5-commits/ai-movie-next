import { useEffect, useRef, useState } from "react";
import type { CanvasDataPort } from "../../shared/api/canvasData";

export function CanvasMediaPreview({
  imageUrl,
  port,
  scopeKey,
}: {
  imageUrl: string;
  port: CanvasDataPort;
  scopeKey: string;
}) {
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const ownedObjectUrlRef = useRef<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    let ownedObjectUrl: string | null = null;
    setObjectUrl(null);
    setFailed(false);
    void port.readImage(imageUrl, controller.signal).then((blob) => {
      if (!active || controller.signal.aborted) return;
      const next = URL.createObjectURL(blob);
      ownedObjectUrl = next;
      ownedObjectUrlRef.current = next;
      setObjectUrl(next);
    }).catch(() => {
      if (active && !controller.signal.aborted) setFailed(true);
    });
    return () => {
      active = false;
      controller.abort();
      if (ownedObjectUrl !== null && ownedObjectUrlRef.current === ownedObjectUrl) {
        URL.revokeObjectURL(ownedObjectUrl);
        ownedObjectUrlRef.current = null;
      }
    };
  }, [imageUrl, port, scopeKey]);

  if (objectUrl !== null) return (
    <img
      className="canvas-media-image"
      src={objectUrl}
      alt=""
      onError={() => {
        if (ownedObjectUrlRef.current === objectUrl) {
          URL.revokeObjectURL(objectUrl);
          ownedObjectUrlRef.current = null;
        }
        setObjectUrl(null);
        setFailed(true);
      }}
    />
  );
  return <span className="canvas-media-status">{failed ? "图片暂不可读取" : "正在读取图片…"}</span>;
}
