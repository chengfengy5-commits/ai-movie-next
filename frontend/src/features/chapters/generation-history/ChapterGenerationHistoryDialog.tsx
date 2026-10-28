import { useEffect, useState } from "react";
import { useSeriesDialogFocus } from "../../series/useSeriesDialogFocus";
import {
  GenerationHistoryMediaPreview,
} from "../../assets/media/GenerationHistoryMediaPreview";
import type {
  ChapterGenerationHistoryMessage,
  ChapterGenerationHistoryMode,
  ChapterGenerationHistoryTargetKind,
} from "../../../shared/api/chapterGenerationHistory";

import "./chapterGenerationHistory.css";

export interface ChapterGenerationHistoryEntry {
  readonly message: ChapterGenerationHistoryMessage;
  readonly mediaUrl: string | null;
  readonly unsafeMedia: boolean;
  readonly canAdopt: boolean;
}

export type ChapterGenerationHistoryLoadState =
  | { readonly status: "loading" }
  | { readonly status: "error"; readonly message: string }
  | { readonly status: "ready"; readonly entries: readonly ChapterGenerationHistoryEntry[] };

export interface ChapterGenerationHistoryConfirmation {
  readonly messageId: string;
  readonly currentMediaUrl: string | null;
  readonly alreadyCurrent: boolean;
}

export type ChapterGenerationHistoryNoticeKind = "success" | "error" | "unknown";

export interface ChapterGenerationHistoryNotice {
  readonly kind: ChapterGenerationHistoryNoticeKind;
  readonly message: string;
  readonly canVerify: boolean;
}

interface ChapterGenerationHistoryDialogProps {
  readonly ownerKey: string;
  readonly chapterTitle: string;
  readonly frameIndex: number;
  readonly targetKind: ChapterGenerationHistoryTargetKind;
  readonly mode: ChapterGenerationHistoryMode;
  readonly loadState: ChapterGenerationHistoryLoadState;
  readonly page: number;
  readonly pageCount: number;
  readonly writable: boolean;
  readonly readOnlyReason: string | null;
  readonly confirmation: ChapterGenerationHistoryConfirmation | null;
  readonly busy: boolean;
  readonly notice: ChapterGenerationHistoryNotice | null;
  readonly previewAssetName: string;
  readonly isCurrent: () => boolean;
  readonly readMedia: (url: string, signal: AbortSignal) => Promise<Blob>;
  readonly onUnauthorized: () => void;
  readonly onClose: () => void;
  readonly onTargetKindChange: (targetKind: ChapterGenerationHistoryTargetKind) => void;
  readonly onModeChange: (mode: ChapterGenerationHistoryMode) => void;
  readonly onPageChange: (page: number) => void;
  readonly onPrepareAdoption: (message: ChapterGenerationHistoryMessage) => void;
  readonly onCancelConfirmation: () => void;
  readonly onConfirmAdoption: () => void;
  readonly onVerifyUnknown: () => void;
}

function targetLabel(targetKind: ChapterGenerationHistoryTargetKind): string {
  return targetKind === "frame-preview" ? "镜头预览" : "故事板原图";
}

function modeLabel(mode: ChapterGenerationHistoryMode): string {
  return mode === "image" ? "图片" : "视频";
}

function reasonLabel(reason: string | null): string {
  switch (reason) {
    case "unreadable_chapter": return "章节内容无法安全识别，只能浏览，不能选用历史结果。";
    case "missing_storyboard_id": return "此镜头没有稳定的故事板素材编号，只能浏览。";
    case "duplicate_storyboard_id": return "故事板素材编号重复，只能浏览。";
    case "asset_not_in_chapter": return "关联素材不属于当前章节，只能浏览。";
    case "ambiguous_storyboard_asset": return "故事板素材编号无法唯一对应，只能浏览。";
    case "storyboard_index_mismatch": return "素材与镜头位置不一致，只能浏览。";
    case "invalid_media_value": return "当前媒体字段格式无法核对，只能浏览。";
    default: return reason !== null && reason !== "" ? reason : "当前目标无法核对，只能浏览。";
  }
}

export function ChapterGenerationHistoryDialog({
  ownerKey,
  chapterTitle,
  frameIndex,
  targetKind,
  mode,
  loadState,
  page,
  pageCount,
  writable,
  readOnlyReason,
  confirmation,
  busy,
  notice,
  previewAssetName,
  isCurrent,
  readMedia,
  onUnauthorized,
  onClose,
  onTargetKindChange,
  onModeChange,
  onPageChange,
  onPrepareAdoption,
  onCancelConfirmation,
  onConfirmAdoption,
  onVerifyUnknown,
}: ChapterGenerationHistoryDialogProps) {
  const [activePreviewKey, setActivePreviewKey] = useState<string | null>(null);
  useEffect(() => {
    setActivePreviewKey(null);
  }, [ownerKey, targetKind, mode, page]);
  const focus = useSeriesDialogFocus({ active: true, canDismiss: !busy, onDismiss: onClose });
  const historyLabel = targetKind === "frame-preview" ? "镜头预览历史" : "故事板素材历史";
  const destination = targetLabel(targetKind);
  const selectionFrozen = busy || notice?.kind === "unknown";

  return (
    <div className="series-dialog-backdrop chapter-editing-backdrop chapter-generation-history-backdrop" role="presentation">
      <section
        aria-labelledby="chapter-generation-history-title"
        aria-modal="true"
        className="series-dialog chapter-editing-dialog chapter-generation-history-dialog"
        ref={focus.dialogRef}
        role="dialog"
        tabIndex={-1}
      >
        <header className="chapter-generation-history-heading">
          <div>
            <p className="eyebrow">{chapterTitle || "未命名章节"} · 镜头 {frameIndex + 1}</p>
            <h2 id="chapter-generation-history-title">生成历史</h2>
          </div>
          <button
            aria-label="关闭生成历史"
            className="icon-button"
            disabled={busy}
            onClick={onClose}
            type="button"
          >×</button>
        </header>

        <div className="chapter-generation-history-toolbar">
          <div aria-label="历史来源" className="chapter-generation-history-switch" role="group">
            <button
              aria-pressed={targetKind === "frame-preview"}
              className={targetKind === "frame-preview" ? "active" : ""}
              disabled={selectionFrozen}
              onClick={() => onTargetKindChange("frame-preview")}
              type="button"
            >镜头预览</button>
            <button
              aria-pressed={targetKind === "storyboard-image"}
              className={targetKind === "storyboard-image" ? "active" : ""}
              disabled={selectionFrozen}
              onClick={() => onTargetKindChange("storyboard-image")}
              type="button"
            >故事板素材</button>
          </div>
          <div aria-label="生成类型" className="chapter-generation-history-switch" role="group">
            <button
              aria-pressed={mode === "image"}
              className={mode === "image" ? "active" : ""}
              disabled={selectionFrozen}
              onClick={() => onModeChange("image")}
              type="button"
            >图片</button>
            <button
              aria-pressed={mode === "video"}
              className={mode === "video" ? "active" : ""}
              disabled={selectionFrozen}
              onClick={() => onModeChange("video")}
              type="button"
            >视频</button>
          </div>
        </div>

        <p className="chapter-generation-history-destination">
          选用后只更新{destination}字段；历史浏览不会修改章节或素材。
        </p>

        {notice !== null && (
          <div className={`chapter-generation-history-notice ${notice.kind}`} role={notice.kind === "unknown" ? "alert" : "status"}>
            <p>{notice.message}</p>
            {notice.canVerify && (
              <button className="secondary-button" disabled={busy} onClick={onVerifyUnknown} type="button">
                只读核对当前值
              </button>
            )}
          </div>
        )}

        {!writable && <p className="chapter-generation-history-readonly" role="status">{reasonLabel(readOnlyReason)}</p>}

        <div className="chapter-generation-history-body" aria-label={historyLabel}>
          {loadState.status === "loading" && <p role="status">正在读取{historyLabel}…</p>}
          {loadState.status === "error" && <p className="series-dialog-error" role="alert">{loadState.message}</p>}
          {loadState.status === "ready" && loadState.entries.length === 0 && (
            <p role="status">当前章节没有可显示的{modeLabel(mode)}历史记录。</p>
          )}
          {loadState.status === "ready" && loadState.entries.length > 0 && (
            <>
              <ol className="chapter-generation-history-list">
                {loadState.entries.map((entry) => {
                  const message = entry.message;
                  const selected = confirmation?.messageId === message.id;
                  const canChoose = writable && entry.canAdopt && !selectionFrozen;
                  const previewKey = [ownerKey, targetKind, mode, page, message.id].join("\u0000");
                  const previewIsActive = activePreviewKey === previewKey;
                  return (
                    <li className="chapter-generation-history-item" key={message.id}>
                      <div className="chapter-generation-history-preview">
                        {previewIsActive ? (
                          <GenerationHistoryMediaPreview
                            key={previewKey}
                            assetName={previewAssetName}
                            isCurrent={isCurrent}
                            mediaUrl={entry.mediaUrl}
                            mode={mode}
                            onUnauthorized={onUnauthorized}
                            readMedia={readMedia}
                            unsafeMedia={entry.unsafeMedia}
                          />
                        ) : (
                          <button
                            aria-label={`预览${modeLabel(mode)}历史`}
                            className="secondary-button"
                            disabled={entry.mediaUrl === null || entry.unsafeMedia}
                            onClick={() => setActivePreviewKey(previewKey)}
                            type="button"
                          >预览{modeLabel(mode)}</button>
                        )}
                        {previewIsActive && (
                          <button
                            aria-label={`关闭${modeLabel(mode)}预览`}
                            className="secondary-button"
                            onClick={() => setActivePreviewKey(null)}
                            type="button"
                          >关闭预览</button>
                        )}
                      </div>
                      {message.role === "user" && (
                        <div className="chapter-generation-history-prompt">
                          <span>生成说明</span>
                          <p>{message.content}</p>
                        </div>
                      )}
                      <p className="chapter-generation-history-time">{message.created_at}</p>
                      {message.model_name !== null && <p className="chapter-generation-history-model">{message.model_name}</p>}
                      {message.role === "assistant" && !entry.canAdopt && (
                        <p className="chapter-generation-history-unavailable">此消息没有可安全选用的媒体地址。</p>
                      )}
                      {message.role === "assistant" && entry.canAdopt && !writable && (
                        <p className="chapter-generation-history-unavailable">此目标只能浏览。</p>
                      )}
                      {message.role === "assistant" && (
                        <div className="chapter-generation-history-actions">
                          <button
                            className="secondary-button"
                            disabled={!canChoose || selected}
                            onClick={() => onPrepareAdoption(message)}
                            type="button"
                          >{selected ? "已核对候选" : `选用此${modeLabel(mode)}结果`}</button>
                        </div>
                      )}
                    </li>
                  );
                })}
              </ol>
              {pageCount > 1 && (
                <nav aria-label="生成历史分页" className="chapter-generation-history-pagination">
                  <button className="secondary-button" disabled={page <= 0 || selectionFrozen} onClick={() => onPageChange(page - 1)} type="button">
                    上一页
                  </button>
                  <span>第 {page + 1} / {pageCount} 页</span>
                  <button className="secondary-button" disabled={page + 1 >= pageCount || selectionFrozen} onClick={() => onPageChange(page + 1)} type="button">
                    下一页
                  </button>
                </nav>
              )}
            </>
          )}
        </div>

        {confirmation !== null && (
          <section aria-label="确认选用历史结果" className="chapter-generation-history-confirmation">
            <p>
              {confirmation.alreadyCurrent
                ? `当前${destination}已与此历史结果一致。`
                : `确认将此历史结果写入当前${destination}？`}
            </p>
            <p>系统会在确认后再次核对目标；本次只提交一次。</p>
            <div className="series-dialog-actions">
              <button className="secondary-button" disabled={busy || notice?.kind === "unknown"} onClick={onCancelConfirmation} type="button">取消</button>
              <button className="primary-button" disabled={busy || notice?.kind === "unknown"} onClick={onConfirmAdoption} type="button">
                {busy ? "正在确认…" : confirmation.alreadyCurrent ? "完成核对" : `确认替换${destination}`}
              </button>
            </div>
          </section>
        )}
      </section>
    </div>
  );
}
