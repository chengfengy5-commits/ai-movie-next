import type { StylePromptChoice } from "../../shared/api/seriesManagement";
import { stripPythonWhitespace, type TeamSeriesCreateDraft } from "../../shared/api/teamSeries";

interface TeamSeriesCreateFormProps {
  draft: TeamSeriesCreateDraft;
  stylePrompts: StylePromptChoice[];
  stylePromptsLoading: boolean;
  stylePromptsError: string | null;
  disabled: boolean;
  onDraftChange(draft: TeamSeriesCreateDraft): void;
  onSubmit(): void;
}

export function TeamSeriesCreateForm({
  draft,
  stylePrompts,
  stylePromptsLoading,
  stylePromptsError,
  disabled,
  onDraftChange,
  onSubmit,
}: TeamSeriesCreateFormProps) {
  const nameIsValid = stripPythonWhitespace(draft.name).length > 0 && [...draft.name].length <= 255;

  return (
    <form
      aria-label="创建团队剧集"
      className="team-series-create"
      onSubmit={(event) => { event.preventDefault(); onSubmit(); }}
    >
      <h3>创建团队剧集</h3>
      <label htmlFor="team-series-name">名称</label>
      <input
        disabled={disabled}
        id="team-series-name"
        onChange={(event) => onDraftChange({ ...draft, name: event.currentTarget.value })}
        required
        value={draft.name}
      />
      <label htmlFor="team-series-description">简介</label>
      <textarea
        disabled={disabled}
        id="team-series-description"
        onChange={(event) => onDraftChange({ ...draft, description: event.currentTarget.value })}
        value={draft.description ?? ""}
      />
      <label htmlFor="team-series-image-url">封面 URL</label>
      <input
        disabled={disabled}
        id="team-series-image-url"
        onChange={(event) => onDraftChange({ ...draft, image_url: event.currentTarget.value })}
        type="url"
        value={draft.image_url ?? ""}
      />
      <label htmlFor="team-series-style">风格提示词</label>
      <select
        disabled={disabled || stylePromptsLoading}
        id="team-series-style"
        onChange={(event) => {
          const next = { ...draft };
          if (event.currentTarget.value === "") delete next.style_prompt_id;
          else next.style_prompt_id = event.currentTarget.value;
          onDraftChange(next);
        }}
        value={draft.style_prompt_id ?? ""}
      >
        <option value="">不指定风格</option>
        {stylePrompts.map((prompt) => <option key={prompt.id} value={prompt.id}>{prompt.name}</option>)}
      </select>
      {stylePromptsLoading && <p className="team-muted" role="status">正在读取风格提示词…</p>}
      {stylePromptsError !== null && <p className="team-warning" role="status">{stylePromptsError}；仍可使用默认风格创建。</p>}
      <label className="team-series-claim-option">
        <input
          checked={draft.claim}
          disabled={disabled}
          onChange={(event) => onDraftChange({ ...draft, claim: event.currentTarget.checked })}
          type="checkbox"
        />
        创建后由我认领
      </label>
      <button className="primary-button" disabled={disabled || !nameIsValid} type="submit">创建团队剧集</button>
    </form>
  );
}
