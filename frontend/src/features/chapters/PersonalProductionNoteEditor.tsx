import type { PersonalProductionNoteStatus } from "../../shared/api/personalProductionNotes";
import type { PersonalProductionNoteEditCandidate } from "./personal-production/noteEdit";

export interface PersonalProductionNoteEditorDraft {
  status: PersonalProductionNoteStatus | null;
  note: string;
  requiresStatusConfirmation: boolean;
}

interface PersonalProductionNoteEditorProps {
  candidate: PersonalProductionNoteEditCandidate;
  draft: PersonalProductionNoteEditorDraft;
  disabled: boolean;
  onStatusChange(status: PersonalProductionNoteStatus | null): void;
  onNoteChange(note: string): void;
  onSave(): void;
  onCancel(): void;
}

const statusOptions: readonly { value: PersonalProductionNoteStatus; label: string }[] = [
  { value: "unmarked", label: "未标记" },
  { value: "needs_revision", label: "待修" },
  { value: "approved", label: "已认可" },
];

function codePointLength(value: string): number {
  return Array.from(value).length;
}

export function PersonalProductionNoteEditor({
  candidate,
  draft,
  disabled,
  onStatusChange,
  onNoteChange,
  onSave,
  onCancel,
}: PersonalProductionNoteEditorProps) {
  const noteLength = codePointLength(draft.note);
  const noteIsTooLong = noteLength > 2_000;
  const canSave = !disabled
    && !noteIsTooLong
    && !draft.requiresStatusConfirmation
    && draft.status !== null
    && (draft.status !== "approved" || candidate.approvalEligible);

  return (
    <section className="personal-production-note-editor" aria-labelledby={`production-note-editor-${candidate.position}`}>
      <h4 id={`production-note-editor-${candidate.position}`}>编辑镜头 {candidate.position} 的个人记录</h4>
      <label className="personal-production-note-editor-field">
        <span>制作状态</span>
        <select
          aria-label={`镜头 ${candidate.position} 的制作状态`}
          disabled={disabled}
          onChange={(event) => {
            const value = event.currentTarget.value;
            onStatusChange(value === "" ? null : value as PersonalProductionNoteStatus);
          }}
          value={draft.status ?? ""}
        >
          <option value="">请选择状态</option>
          {statusOptions.map((option) => (
            <option
              disabled={option.value === "approved" && !candidate.approvalEligible}
              key={option.value}
              value={option.value}
            >
              {option.label}
            </option>
          ))}
        </select>
      </label>
      {draft.requiresStatusConfirmation && (
        <p className="personal-production-note-editor-hint" role="status">
          已重新读取当前记录，请重新明确选择制作状态后再保存。
        </p>
      )}
      {!draft.requiresStatusConfirmation && draft.status === null && (
        <p className="personal-production-note-editor-hint" role="status">
          请明确选择一种制作状态后再保存。
        </p>
      )}
      {!candidate.approvalEligible && (
        <p className="personal-production-note-editor-hint" role="note">
          当前媒体摘要无法核对，不能将此镜头标记为已认可；备注和待修状态仍可保存。
        </p>
      )}
      <label className="personal-production-note-editor-field">
        <span>文字备注</span>
        <textarea
          aria-label={`镜头 ${candidate.position} 的文字备注`}
          disabled={disabled}
          onChange={(event) => onNoteChange(event.currentTarget.value)}
          value={draft.note}
        />
      </label>
      <p className={noteIsTooLong ? "personal-production-note-editor-count is-invalid" : "personal-production-note-editor-count"}>
        {noteLength}/2000 字
      </p>
      {noteIsTooLong && <p className="personal-production-note-editor-error" role="alert">备注最多 2000 字。</p>}
      <div className="personal-production-note-editor-actions">
        <button className="primary-button" disabled={!canSave} onClick={onSave} type="button">
          保存镜头记录
        </button>
        <button className="secondary-button" disabled={disabled} onClick={onCancel} type="button">
          关闭编辑器
        </button>
      </div>
    </section>
  );
}
