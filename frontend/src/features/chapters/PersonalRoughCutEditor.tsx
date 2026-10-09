import type { PersonalRoughCutFrame } from "../../shared/api/personalRoughCut";
import type { PersonalRoughCutUpdateFrame } from "../../shared/api/personalRoughCut";

interface PersonalRoughCutEditorProps {
  frames: readonly PersonalRoughCutFrame[];
  draft: readonly PersonalRoughCutUpdateFrame[];
  disabled: boolean;
  saveDisabled: boolean;
  onMove(index: number, direction: -1 | 1): void;
  onIncludedChange(index: number, included: boolean): void;
  onSave(): void;
  onCancel(): void;
}

export function PersonalRoughCutEditor({
  frames,
  draft,
  disabled,
  saveDisabled,
  onMove,
  onIncludedChange,
  onSave,
  onCancel,
}: PersonalRoughCutEditorProps) {
  return (
    <section className="personal-rough-cut-editor" aria-label="编辑粗剪编排">
      <div className="personal-rough-cut-editor-heading">
        <div>
          <h4>编辑粗剪编排</h4>
          <p>只调整本人的草稿顺序与纳入状态，不更改章节正文或媒体。</p>
        </div>
        <div className="personal-rough-cut-editor-actions">
          <button className="secondary-button" disabled={disabled} onClick={onCancel} type="button">
            取消编辑
          </button>
          <button className="primary-button" disabled={saveDisabled} onClick={onSave} type="button">
            保存粗剪编排
          </button>
        </div>
      </div>
      <div className="personal-rough-cut-editor-groups" aria-label="粗剪编排草稿">
        {[
          { included: true, label: "已纳入镜头" },
          { included: false, label: "已排除镜头" },
        ].map((group) => (
          <section className="personal-rough-cut-editor-group" key={group.label}>
            <h5>{group.label}</h5>
            <ol className="personal-rough-cut-editor-list" aria-label={group.label}>
              {draft.flatMap((draftFrame, index) => {
                if (draftFrame.included !== group.included) {
                  return [];
                }
                const frame = frames.find((candidate) => candidate.asset_id === draftFrame.asset_id);
                if (frame === undefined) {
                  return [];
                }
                const sourcePosition = frame.frame_index + 1;
                const canMoveUp = draft.slice(0, index).some((entry) => entry.included === draftFrame.included);
                const canMoveDown = draft.slice(index + 1).some((entry) => entry.included === draftFrame.included);
                return [(
                  <li className="personal-rough-cut-editor-row" key={`${draftFrame.asset_id}-${index}`}>
                    <div className="personal-rough-cut-editor-row-main">
                      <strong>镜头 {sourcePosition}</strong>
                      <span>{frame.text === "" ? "未提供正文" : frame.text}</span>
                      <span className="personal-rough-cut-editor-state">
                        {draftFrame.included ? "已纳入草稿" : "已排除"}
                      </span>
                      {frame.pending && <span className="personal-rough-cut-editor-state">待安排</span>}
                    </div>
                    <div className="personal-rough-cut-editor-row-actions">
                      <button
                        aria-label={`上移镜头 ${sourcePosition}`}
                        className="text-button"
                        disabled={disabled || !canMoveUp}
                        onClick={() => onMove(index, -1)}
                        type="button"
                      >
                        上移
                      </button>
                      <button
                        aria-label={`下移镜头 ${sourcePosition}`}
                        className="text-button"
                        disabled={disabled || !canMoveDown}
                        onClick={() => onMove(index, 1)}
                        type="button"
                      >
                        下移
                      </button>
                      <button
                        aria-label={`${draftFrame.included ? "排除" : "纳入"}粗剪：镜头 ${sourcePosition}`}
                        aria-pressed={draftFrame.included}
                        className="secondary-button"
                        disabled={disabled}
                        onClick={() => onIncludedChange(index, !draftFrame.included)}
                        type="button"
                      >
                        {draftFrame.included ? "排除" : "纳入"}
                      </button>
                    </div>
                  </li>
                )];
              })}
            </ol>
          </section>
        ))}
      </div>
    </section>
  );
}
