import {
  QUICK_REFERENCE_FIELDS,
  type QuickReferenceField,
  type QuickReferenceMode,
  type QuickReferenceModeSettings,
  type QuickReferenceSettings,
} from "./quickReference";

const LABELS: Readonly<Record<QuickReferenceField, string>> = {
  storyboard: "故事板图片",
  character: "角色图片",
  scene: "场景图片",
  prop: "道具图片",
  fused: "融合图优先",
  characterVoice: "角色音色与参考音频",
  matchDuration: "按分镜时长匹配视频时长",
};

interface QuickReferenceSettingsPanelProps {
  readonly settings: QuickReferenceSettings;
  readonly disabled: boolean;
  readonly loading: boolean;
  readonly saving: boolean;
  readonly hasEdits: boolean;
  readonly serviceAvailable: boolean;
  readonly message: string | null;
  onChange(mode: QuickReferenceMode, field: QuickReferenceField, checked: boolean): void;
  onSave(): void;
}

function ModeOptions({
  mode,
  settings,
  disabled,
  onChange,
}: {
  readonly mode: QuickReferenceMode;
  readonly settings: QuickReferenceModeSettings;
  readonly disabled: boolean;
  onChange(mode: QuickReferenceMode, field: QuickReferenceField, checked: boolean): void;
}) {
  return (
    <fieldset className="media-generation-batch-type" disabled={disabled}>
      <legend>{mode === "image" ? "普通图片" : "普通视频"}</legend>
      {QUICK_REFERENCE_FIELDS[mode].map((field) => (
        <label key={field}>
          <input checked={settings[field]} onChange={(event) => onChange(mode, field, event.target.checked)} type="checkbox" />
          <span>{LABELS[field]}</span>
        </label>
      ))}
    </fieldset>
  );
}

export function QuickReferenceSettingsPanel({
  settings,
  disabled,
  loading,
  saving,
  hasEdits,
  serviceAvailable,
  message,
  onChange,
  onSave,
}: QuickReferenceSettingsPanelProps) {
  return (
    <details className="media-generation-card media-generation-preferences">
      <summary>快速引用设置</summary>
      <p className="media-generation-note">
        快速引用只在你点击操作时填入当前章节上下文，不会自动提交生成。视频角色音色与角色图片分开控制。
      </p>
      <div className="media-generation-fields">
        <ModeOptions disabled={disabled} mode="image" onChange={onChange} settings={settings.image} />
        <ModeOptions disabled={disabled} mode="video" onChange={onChange} settings={settings.video} />
      </div>
      <div className="media-generation-actions">
        <button className="secondary-button" disabled={disabled || loading || saving || !hasEdits || !serviceAvailable}
          onClick={onSave} type="button">
          {saving ? "保存中…" : "保存快速引用设置"}
        </button>
      </div>
      {loading && <p className="media-generation-note" role="status">正在读取当前账号的快速引用设置…</p>}
      {message !== null && <p className="media-generation-note" role="status">{message}</p>}
      {!serviceAvailable && <p className="media-generation-note" role="status">当前服务尚未接入快速引用设置保存。</p>}
    </details>
  );
}
