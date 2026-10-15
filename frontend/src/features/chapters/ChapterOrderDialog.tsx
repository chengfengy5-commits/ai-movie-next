import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import type { Chapter } from "../../shared/api/contracts";
import { normalizeApiError } from "../../shared/api/services";
import { useSeriesDialogFocus } from "../series/useSeriesDialogFocus";
import { buildChapterReorderInput } from "./chapterEditing";

interface ChapterOrderDialogProps {
  chapters: Chapter[];
  seriesId: string;
  onClose(): void;
  onSave(orderedIds: string[], signal: AbortSignal): Promise<void>;
}

export function ChapterOrderDialog({ chapters, seriesId, onClose, onSave }: ChapterOrderDialogProps) {
  const [orderedIds, setOrderedIds] = useState(() => chapters.map((chapter) => chapter.id));
  const [activeId, setActiveId] = useState<string | null>(chapters[0]?.id ?? null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const hasReceivedDirectoryUpdate = useRef(false);
  const focus = useSeriesDialogFocus({ active: true, canDismiss: true, onDismiss: close });

  useEffect(() => () => controllerRef.current?.abort(), []);

  useEffect(() => {
    const isDirectoryRefresh = hasReceivedDirectoryUpdate.current;
    hasReceivedDirectoryUpdate.current = true;
    const availableIds = new Set(chapters.map((chapter) => chapter.id));
    setOrderedIds((current) => {
      const retained = current.filter((id) => availableIds.has(id));
      const retainedIds = new Set(retained);
      return [...retained, ...chapters.map((chapter) => chapter.id).filter((id) => !retainedIds.has(id))];
    });
    setActiveId((current) => current !== null && availableIds.has(current) ? current : chapters[0]?.id ?? null);
    if (isDirectoryRefresh) {
      setMessage("已重新读取目录；保留仍存在章节的草稿顺序。再次保存前会重新核对。");
    }
  }, [chapters]);

  function close(): void {
    controllerRef.current?.abort();
    controllerRef.current = null;
    focus.markDismissed();
    onClose();
  }

  function move(id: string, direction: -1 | 1): void {
    const index = orderedIds.indexOf(id);
    const nextIndex = index + direction;
    if (index < 0 || nextIndex < 0 || nextIndex >= orderedIds.length || saving) return;
    const next = [...orderedIds];
    [next[index], next[nextIndex]] = [next[nextIndex]!, next[index]!];
    setOrderedIds(next);
    setActiveId(id);
    setMessage(null);
  }

  function onRowKeyDown(event: KeyboardEvent<HTMLLIElement>, id: string): void {
    if (event.altKey && event.key === "ArrowUp") {
      event.preventDefault();
      move(id, -1);
    } else if (event.altKey && event.key === "ArrowDown") {
      event.preventDefault();
      move(id, 1);
    }
  }

  async function submit(): Promise<void> {
    if (saving) return;
    const input = buildChapterReorderInput(chapters, orderedIds, seriesId);
    if (input === null) {
      setMessage("章节目录身份重复或已变化，无法安全保存排序。请重新读取目录。" );
      return;
    }
    const controller = new AbortController();
    controllerRef.current = controller;
    setSaving(true);
    setMessage(null);
    try {
      await onSave(orderedIds, controller.signal);
      if (!controller.signal.aborted) {
        focus.markDismissed();
        onClose();
      }
    } catch (cause) {
      if (!controller.signal.aborted) {
        const error = normalizeApiError(cause);
        setMessage(error.detail ?? error.message);
      }
    } finally {
      if (controllerRef.current === controller) {
        controllerRef.current = null;
        setSaving(false);
      }
    }
  }

  const chapterById = new Map(chapters.map((chapter) => [chapter.id, chapter]));
  return (
    <div className="series-dialog-backdrop chapter-editing-backdrop" role="presentation">
      <section
        aria-labelledby="chapter-order-title"
        aria-modal="true"
        className="series-dialog chapter-editing-dialog chapter-order-dialog"
        ref={focus.dialogRef}
        role="dialog"
        tabIndex={-1}
      >
        <div className="series-dialog-heading">
          <div>
            <p className="eyebrow">章节管理</p>
            <h2 id="chapter-order-title">调整章节顺序</h2>
          </div>
          <button aria-label="关闭章节排序" className="icon-button" onClick={close} type="button">×</button>
        </div>
        <p className="chapter-editing-help">使用上下按钮移动章节，也可聚焦章节后按 Alt + ↑ 或 Alt + ↓。</p>
        <ol className="chapter-order-list" aria-label="新的章节顺序">
          {orderedIds.map((id, index) => {
            const chapter = chapterById.get(id);
            return (
              <li
                aria-current={activeId === id ? "true" : undefined}
                className={activeId === id ? "chapter-order-item active" : "chapter-order-item"}
                key={id}
                onFocus={() => setActiveId(id)}
                onKeyDown={(event) => onRowKeyDown(event, id)}
                tabIndex={0}
              >
                <span className="chapter-order-position">{String(index + 1).padStart(2, "0")}</span>
                <strong>{chapter?.title.trim() || "未命名章节"}</strong>
                <div className="chapter-order-controls">
                  <button aria-label={`将${chapter?.title || "章节"}上移`} disabled={index === 0 || saving} onClick={() => move(id, -1)} type="button">上移</button>
                  <button aria-label={`将${chapter?.title || "章节"}下移`} disabled={index === orderedIds.length - 1 || saving} onClick={() => move(id, 1)} type="button">下移</button>
                </div>
              </li>
            );
          })}
        </ol>
        {message !== null && <p className="series-dialog-error" role="alert">{message}</p>}
        <div className="series-dialog-actions">
          <button className="secondary-button" onClick={close} type="button">取消</button>
          <button className="primary-button" disabled={saving || orderedIds.length < 2} onClick={() => void submit()} type="button">
            {saving ? "正在核对并保存…" : "保存全部顺序"}
          </button>
        </div>
      </section>
    </div>
  );
}
