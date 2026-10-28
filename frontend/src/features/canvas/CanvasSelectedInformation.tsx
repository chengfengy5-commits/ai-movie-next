import { GenerationHistoryMediaPreview } from "../assets/media/GenerationHistoryMediaPreview";

export interface CanvasInformationMediaPreview {
  key: string;
  assetName: string;
  mediaUrl: string | null;
  mode: "image" | "video";
  unsafeMedia: boolean;
  isCurrent?: () => boolean;
  onUnauthorized?: () => void;
  readMedia?: (url: string, signal: AbortSignal) => Promise<Blob>;
  unavailableReason?: string;
}

export interface CanvasInformationField {
  label: string;
  value: string;
}

export interface CanvasInformationRelation {
  key: string;
  relationship: string;
  label: string;
  onLocate?: () => void;
  locateDisabledReason?: string;
  onEdit?: () => void;
  editLabel?: string;
  editDisabledReason?: string;
}

export interface CanvasSelectedInformationProps {
  title: string;
  kindLabel: string;
  description?: string | null;
  fullText?: string | null;
  fields?: readonly CanvasInformationField[];
  preparationProgress?: number | null;
  relations?: readonly CanvasInformationRelation[];
  mediaPreviews?: readonly CanvasInformationMediaPreview[];
  primaryAction?: {
    label: string;
    disabledReason?: string;
    onActivate?: () => void;
  };
  deleteAction?: {
    label: string;
    disabledReason?: string;
    onDelete: () => void;
  };
}

export function CanvasSelectedInformation({
  title,
  kindLabel,
  description,
  fullText,
  fields = [],
  preparationProgress,
  relations = [],
  mediaPreviews = [],
  primaryAction,
  deleteAction,
}: CanvasSelectedInformationProps) {
  return (
    <section aria-label="选中内容信息" className="canvas-selected-information">
      <div className="canvas-selected-information-main">
        <header className="canvas-selected-information-heading">
          <div>
            <p className="canvas-selected-information-kind">{kindLabel}</p>
            <h3>{title}</h3>
          </div>
          {(primaryAction !== undefined || deleteAction !== undefined) && (
            <div className="canvas-selected-information-actions">
              {primaryAction !== undefined && (
                <div>
                  <button disabled={primaryAction.disabledReason !== undefined || primaryAction.onActivate === undefined}
                    onClick={primaryAction.onActivate} type="button">{primaryAction.label}</button>
                  {primaryAction.disabledReason !== undefined && <span>{primaryAction.disabledReason}</span>}
                </div>
              )}
              {deleteAction !== undefined && (
                <div>
                  <button
                    disabled={deleteAction.disabledReason !== undefined}
                    onClick={deleteAction.onDelete}
                    type="button"
                  >{deleteAction.label}</button>
                  {deleteAction.disabledReason !== undefined && <span>{deleteAction.disabledReason}</span>}
                </div>
              )}
            </div>
          )}
        </header>

        {fullText !== undefined && (
          <div className="canvas-selected-information-text">
            <h4>正文</h4>
            <p>{fullText || "暂无正文"}</p>
          </div>
        )}
        {description !== undefined && (
          <div className="canvas-selected-information-text">
            <h4>描述</h4>
            <p>{description || "暂无描述"}</p>
          </div>
        )}
        {fields.length > 0 && (
          <dl className="canvas-selected-information-fields">
            {fields.map((field) => (
              <div key={field.label}>
                <dt>{field.label}</dt>
                <dd>{field.value}</dd>
              </div>
            ))}
          </dl>
        )}
        {preparationProgress !== null && preparationProgress !== undefined && (
          <div className="canvas-selected-information-progress" aria-label="素材准备进度">
            <div><span>素材准备进度</span><strong>{preparationProgress}%</strong></div>
            <progress max={100} value={preparationProgress}>{preparationProgress}%</progress>
          </div>
        )}
        {mediaPreviews.length > 0 && (
          <div className="canvas-selected-information-media">
            <h4>媒体</h4>
            <div>
              {mediaPreviews.map((media) => (
                <article key={media.key}>
                  <h5>{media.assetName}</h5>
                  {media.unavailableReason !== undefined ? (
                    <p className="canvas-selected-information-media-state" role="status">{media.unavailableReason}</p>
                  ) : media.unsafeMedia ? (
                    <p className="canvas-selected-information-media-state" role="status">媒体地址不符合安全预览要求。</p>
                  ) : media.isCurrent === undefined || media.onUnauthorized === undefined || media.readMedia === undefined ? (
                    <p className="canvas-selected-information-media-state" role="status">当前模式未接入安全媒体预览。</p>
                  ) : (
                    <GenerationHistoryMediaPreview
                      key={media.key}
                      assetName={media.assetName}
                      isCurrent={media.isCurrent}
                      mediaUrl={media.mediaUrl}
                      mode={media.mode}
                      onUnauthorized={media.onUnauthorized}
                      readMedia={media.readMedia}
                      unsafeMedia={media.unsafeMedia}
                    />
                  )}
                </article>
              ))}
            </div>
          </div>
        )}
      </div>

      {relations.length > 0 && (
        <div className="canvas-selected-information-related">
          <h4>关联内容</h4>
          <ul>
            {relations.map((relation) => (
              <li key={relation.key}>
                <span><strong>{relation.relationship}</strong>{relation.label}</span>
                <div>
                  {relation.onLocate !== undefined ? (
                    <button onClick={relation.onLocate} type="button">定位</button>
                  ) : relation.locateDisabledReason !== undefined ? (
                    <span><button disabled type="button">定位</button>{relation.locateDisabledReason}</span>
                  ) : null}
                  {relation.onEdit !== undefined ? (
                    <>
                      <button disabled={relation.editDisabledReason !== undefined}
                        onClick={relation.onEdit} type="button">{relation.editLabel ?? "编辑"}</button>
                      {relation.editDisabledReason !== undefined && <span>{relation.editDisabledReason}</span>}
                    </>
                  ) : relation.editDisabledReason !== undefined ? (
                    <span><button disabled type="button">{relation.editLabel ?? "编辑"}</button>{relation.editDisabledReason}</span>
                  ) : null}
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
