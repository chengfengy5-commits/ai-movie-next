import { isSafeCoverUrl } from "../../shared/api/config";
import type { StoryboardFrame } from "../../shared/api/contracts";
import type { FusedGenerationMemberType } from "../../shared/api/fusedGeneration";
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
  readonly onFuseMemberType?: (memberType: FusedGenerationMemberType) => void;
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
  onFuseMemberType,
}: FrameResourceColumnProps) {
  const groups = projectFrameResources(frame, frameIndex, chapterId, seriesId, chapterFrames, directories);
  const fusionAvailability = (memberType: FusedGenerationMemberType): { disabled: boolean; reason: string | null } => {
    if (directories.storyboard.status === "loading") return { disabled: true, reason: "正在读取分镜目录。" };
    if (directories.storyboard.status === "error") return { disabled: true, reason: "分镜目录读取失败。" };
    const category = memberType === "character" ? "characters" : "props";
    const group = groups.find((candidate) => candidate.category === category);
    if (group === undefined || group.status === "loading") return { disabled: true, reason: "正在读取素材目录。" };
    if (group.status === "error") return { disabled: true, reason: "素材目录读取失败。" };
    if (group.rows.length === 0) return { disabled: false, reason: "当前镜头未关联素材，可进入后手动选择。" };
    if (group.rows.some((row) => row.state !== "ready" || row.target?.kind !== "asset")) {
      return { disabled: true, reason: "当前镜头的关联素材无法唯一核对。" };
    }
    if (group.rows.some((row) => typeof row.imageUrl !== "string" || row.imageUrl.trim() === "")) {
      return { disabled: true, reason: "当前镜头的关联素材缺少可用图片。" };
    }
    return { disabled: false, reason: null };
  };
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
      {onFuseMemberType !== undefined && (
        <div className="frame-resource-fused-actions" aria-label={`镜头 ${frameIndex + 1} 的融合入口`}>
          {(["character", "prop"] as const).map((memberType) => {
            const availability = fusionAvailability(memberType);
            const label = memberType === "character" ? "融合镜头角色" : "融合镜头道具";
            return (
              <div className="frame-resource-fused-action" key={memberType}>
                <button
                  className="secondary-button"
                  disabled={availability.disabled}
                  onClick={() => onFuseMemberType(memberType)}
                  type="button"
                >
                  {label}
                </button>
                {availability.reason !== null && <small role="note">{availability.reason}</small>}
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
