import { isSafeCoverUrl } from "../../shared/api/config";
import type { StoryboardFrame } from "../../shared/api/contracts";
import { projectFrameResources, type FrameResourceDirectories } from "./frameResourceProjection";
import type { WorkbenchSourceTarget } from "./sourceIntent";

interface FrameResourceColumnProps {
  readonly frame: StoryboardFrame;
  readonly frameIndex: number;
  readonly chapterFrames: readonly StoryboardFrame[];
  readonly chapterId: string;
  readonly seriesId: string;
  readonly apiBaseUrl: string | null;
  readonly directories: FrameResourceDirectories;
  readonly onSelect?: (target: WorkbenchSourceTarget) => void;
}

export function FrameResourceColumn({
  frame,
  frameIndex,
  chapterFrames,
  chapterId,
  seriesId,
  apiBaseUrl,
  directories,
  onSelect,
}: FrameResourceColumnProps) {
  const groups = projectFrameResources(frame, frameIndex, chapterId, seriesId, chapterFrames, directories);
  return (
    <section className="frame-resource-column" aria-label={`镜头 ${frameIndex + 1} 的五类关联素材`}>
      {groups.map((group) => (
        <div className="frame-resource-group" key={group.category}>
          <h4>{group.label}</h4>
          {group.status === "loading" && <p className="frame-resource-state">正在读取目录…</p>}
          {group.status === "error" && <p className="frame-resource-state" role="status">目录读取失败：{group.message}</p>}
          {group.status === "ready" && group.rows.length === 0 && <p className="frame-resource-state">未关联</p>}
          {group.rows.map((row) => {
            const safeImageUrl = apiBaseUrl === null || row.imageUrl === null
              ? null
              : isSafeCoverUrl(row.imageUrl, apiBaseUrl);
            return (
              <div className={`frame-resource-row ${row.state}`} key={row.key}>
                {safeImageUrl !== null && <img alt="" loading="lazy" src={safeImageUrl} />}
                {row.target !== null && onSelect !== undefined ? (
                  <button className="text-button" onClick={() => onSelect(row.target!)} type="button">
                    {row.label}
                  </button>
                ) : (
                  <span>{row.label}</span>
                )}
              </div>
            );
          })}
        </div>
      ))}
    </section>
  );
}
