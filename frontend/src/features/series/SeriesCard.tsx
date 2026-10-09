import { useState } from "react";
import { isSafeCoverUrl } from "../../shared/api/config";
import type { Series } from "../../shared/api/contracts";

interface SeriesCardProps {
  series: Series;
  currentUserId: string;
  apiBaseUrl: string | null;
  onViewChapters(series: Series): void;
  onViewAssets(series: Series): void;
}

function formatDateTime(value: string): string {
  const source = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`;
  const date = new Date(source);
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "short",
    day: "numeric",
  }).format(date);
}

export function SeriesCard({ series, currentUserId, apiBaseUrl, onViewChapters, onViewAssets }: SeriesCardProps) {
  const [imageFailed, setImageFailed] = useState(false);
  const coverUrl = apiBaseUrl === null ? null : isSafeCoverUrl(series.image_url, apiBaseUrl);
  const showImage = coverUrl !== null && !imageFailed;
  const isClaimed = series.claimed_by !== null;

  return (
    <article className="series-card">
      <div className={showImage ? "series-cover has-image" : "series-cover"}>
        {showImage ? (
          <img
            alt=""
            loading="lazy"
            onError={() => setImageFailed(true)}
            src={coverUrl}
          />
        ) : (
          <div className="cover-placeholder" aria-hidden="true">
            <span className="cover-glyph">H</span>
            <span className="cover-caption">HAO AI · SERIES</span>
          </div>
        )}
        {series.team_id !== null && (
          <span className="cover-badge"><span aria-hidden="true">◇</span> 团队共享</span>
        )}
      </div>

      <div className="series-card-body">
        <div className="series-card-title-row">
          <h3>{series.name}</h3>
          {series.can_enter ? (
            <span className="permission-badge permission-available">可以进入</span>
          ) : (
            <span className="permission-badge permission-restricted">当前账号受限</span>
          )}
        </div>
        <p className="series-description">{series.description || "暂无简介"}</p>
        <dl className="series-meta">
          <div>
            <dt>作者</dt>
            <dd>{series.owner_name || "未知作者"}</dd>
          </div>
          <div>
            <dt>团队</dt>
            <dd>{series.team_id !== null ? series.team_name || "团队剧集" : "个人剧集"}</dd>
          </div>
          <div>
            <dt>制作负责人</dt>
            <dd>
              {isClaimed
                ? series.claimed_by === currentUserId
                  ? "我负责"
                  : series.claimed_by_username || "其他创作者"
                : "尚未认领"}
            </dd>
          </div>
        </dl>
        <div className="series-card-footer">
          <time dateTime={series.updated_at}>更新于 {formatDateTime(series.updated_at)}</time>
          <span className={series.can_enter ? "stage-note" : "stage-note restricted"}>
            {series.can_enter ? "制作功能待开放" : "暂时不能进入"}
          </span>
        </div>
        {series.can_enter && (
          <div className="series-card-actions">
            <button
              aria-label={"只读查看章节：" + series.name}
              className="chapter-entry-button"
              onClick={() => onViewChapters(series)}
              type="button"
            >
              只读查看章节 <span aria-hidden="true">→</span>
            </button>
            <button
              aria-label={"只读查看素材库：" + series.name}
              className="asset-entry-button"
              onClick={() => onViewAssets(series)}
              type="button"
            >
              只读查看素材库 <span aria-hidden="true">→</span>
            </button>
          </div>
        )}
      </div>
    </article>
  );
}
