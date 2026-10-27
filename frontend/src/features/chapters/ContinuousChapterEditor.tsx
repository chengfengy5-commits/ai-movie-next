import { useRef, useState, type ChangeEvent, type KeyboardEvent } from "react";
import {
  addContinuousDraftFrame,
  deleteContinuousDraftFrame,
  mergeContinuousDraftFrames,
  moveContinuousDraftFrame,
  splitContinuousDraftFrame,
  updateContinuousDraftFrame,
  type ContinuousAssetCatalog,
  type ContinuousChapterDraftSnapshot,
  type ContinuousDraftFrame,
} from "./continuousChapterEditing";
import "./continuousChapterEditor.css";

export type ContinuousAssetKind = "character" | "scene" | "prop";

interface ContinuousChapterEditorProps {
  readonly snapshot: ContinuousChapterDraftSnapshot;
  readonly frames: readonly ContinuousDraftFrame[];
  readonly catalogs: ContinuousAssetCatalog;
  readonly selectedIndex: number;
  readonly busy: boolean;
  readonly unknownWrite: boolean;
  readonly notice: string | null;
  readonly dirty: boolean;
  readonly canUndo: boolean;
  readonly canRedo: boolean;
  onFramesChange(frames: ContinuousDraftFrame[]): void;
  onSelectFrame(index: number): void;
  onSave(): void;
  onUndo(): void;
  onRedo(): void;
  onVerifyUnknown(): void;
  onExit(): void;
  onDiscard(): void;
  onCreateAsset(kind: ContinuousAssetKind, name: string): void;
}

function originalText(frame: ContinuousDraftFrame): { status: "text" | "missing" | "invalid"; text: string } {
  if (frame.sourceIndex === null || frame.originalFrame === null
    || !Object.prototype.hasOwnProperty.call(frame.originalFrame, "original_text")) {
    return { status: "missing", text: "" };
  }
  const value: unknown = frame.originalFrame.original_text;
  return typeof value === "string"
    ? { status: "text", text: value }
    : { status: "invalid", text: "" };
}

export function ContinuousChapterEditor({
  snapshot,
  frames,
  catalogs,
  selectedIndex,
  busy,
  unknownWrite,
  notice,
  dirty,
  canUndo,
  canRedo,
  onFramesChange,
  onSelectFrame,
  onSave,
  onUndo,
  onRedo,
  onVerifyUnknown,
  onExit,
  onDiscard,
  onCreateAsset,
}: ContinuousChapterEditorProps) {
  const selectedFrame = frames[selectedIndex] ?? null;
  const selectedTextArea = useRef<HTMLTextAreaElement | null>(null);
  const [newAssetName, setNewAssetName] = useState("");
  const blocked = busy || unknownWrite;

  function changeFrame(index: number, update: Partial<Pick<ContinuousDraftFrame, "text" | "characterIds" | "sceneIds" | "propIds">>) {
    onFramesChange(updateContinuousDraftFrame(frames, index, update));
  }

  function changeAssetSelection(index: number, kind: ContinuousAssetKind, event: ChangeEvent<HTMLSelectElement>) {
    const ids = Array.from(event.currentTarget.selectedOptions, (option) => option.value);
    if (kind === "character") changeFrame(index, { characterIds: ids });
    if (kind === "scene") changeFrame(index, { sceneIds: ids });
    if (kind === "prop") changeFrame(index, { propIds: ids });
  }

  function createAsset(kind: ContinuousAssetKind) {
    const name = newAssetName.trim();
    if (name === "" || selectedFrame === null || blocked) return;
    onCreateAsset(kind, name);
    setNewAssetName("");
  }

  function handleEditorKeyDown(event: KeyboardEvent<HTMLElement>) {
    if (event.nativeEvent.isComposing || (!event.ctrlKey && !event.metaKey)) return;
    const key = event.key.toLowerCase();
    if (key === "s") {
      event.preventDefault();
      if (!blocked && dirty) onSave();
      return;
    }
    if ((key === "z" && event.shiftKey) || key === "y") {
      if (!blocked && canRedo) {
        event.preventDefault();
        onRedo();
      }
      return;
    }
    if (key === "z" && canUndo && !blocked) {
      event.preventDefault();
      onUndo();
    }
  }

  const textCharacterCount = frames.reduce((total, frame) => total + Array.from(frame.text).length, 0);

  function splitSelectedFrame() {
    const caret = selectedTextArea.current?.selectionStart ?? Math.floor((selectedFrame?.text.length ?? 0) / 2);
    onFramesChange(splitContinuousDraftFrame(frames, selectedIndex, caret, `new:${crypto.randomUUID()}`));
  }

  return (
    <section className="continuous-chapter-editor" aria-label="整章连续编辑" onKeyDown={handleEditorKeyDown}>
      <header className="continuous-chapter-editor-heading">
        <div>
          <p className="eyebrow">整章连续编辑 · {snapshot.chapter.title}</p>
          <h2>逐段整理剧本与镜头素材</h2>
          <p>保存前会重新核对章节、分镜目录和编辑锁。</p>
        </div>
        <div className="continuous-chapter-editor-actions">
          <button className="primary-button" disabled={blocked || !dirty} onClick={onSave} type="button">保存并核对整章</button>
          {unknownWrite && <button className="secondary-button" disabled={busy} onClick={onVerifyUnknown} type="button">重新读取核对结果</button>}
          {!dirty && !unknownWrite && <button className="text-button" disabled={busy} onClick={onExit} type="button">结束连续编辑</button>}
          {dirty && !unknownWrite && <button className="text-button" disabled={busy} onClick={onDiscard} type="button">放弃未保存草稿</button>}
        </div>
      </header>
      {snapshot.lockIdentity !== null && <p className="continuous-chapter-lock" role="note">已核对当前账号持有章节编辑锁。</p>}
      {notice !== null && <p className={unknownWrite ? "continuous-chapter-notice is-unknown" : "continuous-chapter-notice"} role={unknownWrite ? "alert" : "status"}>{notice}</p>}
      {unknownWrite && <p className="continuous-chapter-unknown-help">写入结果尚未确认。草稿和原请求保持冻结，不会自动再次保存；先重新读取确认。</p>}
      <p className="continuous-chapter-summary" aria-live="polite">{frames.length} 个镜头 · {textCharacterCount} 个文字字符</p>
      <div className="continuous-chapter-toolbar">
        <button className="secondary-button" disabled={blocked || !canUndo} onClick={onUndo} type="button">撤销</button>
        <button className="secondary-button" disabled={blocked || !canRedo} onClick={onRedo} type="button">重做</button>
        <button className="secondary-button" disabled={blocked} onClick={() => onFramesChange(addContinuousDraftFrame(frames, selectedIndex, `new:${crypto.randomUUID()}`))} type="button">在当前段后新增</button>
        <button className="secondary-button" disabled={blocked || selectedFrame === null} onClick={splitSelectedFrame} type="button">从光标处分段</button>
        <button className="secondary-button" disabled={blocked || selectedIndex >= frames.length - 1} onClick={() => onFramesChange(mergeContinuousDraftFrames(frames, selectedIndex, "left"))} type="button">与下一段合并（保留本段来源）</button>
        <button className="secondary-button" disabled={blocked || selectedIndex >= frames.length - 1} onClick={() => onFramesChange(mergeContinuousDraftFrames(frames, selectedIndex, "right"))} type="button">与下一段合并（保留下一段来源）</button>
        <button className="secondary-button" disabled={blocked || selectedFrame === null} onClick={() => onFramesChange(deleteContinuousDraftFrame(frames, selectedIndex))} type="button">删除当前段</button>
      </div>
      <div className="continuous-chapter-blocks">
        {frames.map((frame, index) => {
          const selected = index === selectedIndex;
          return (
            <article className={`continuous-chapter-block tone-${index % 8}${selected ? " is-selected" : ""}`} key={frame.draftId}>
              <header>
                <button className="continuous-chapter-block-select" aria-pressed={selected} onClick={() => onSelectFrame(index)} type="button">
                  第 {index + 1} 段{frame.sourceIndex === null ? " · 新增" : ` · 原镜头 ${frame.sourceIndex + 1}`}
                </button>
                <div>
                  <button aria-label={`第 ${index + 1} 段上移`} disabled={blocked || index === 0} onClick={() => onFramesChange(moveContinuousDraftFrame(frames, index, -1))} type="button">↑</button>
                  <button aria-label={`第 ${index + 1} 段下移`} disabled={blocked || index === frames.length - 1} onClick={() => onFramesChange(moveContinuousDraftFrame(frames, index, 1))} type="button">↓</button>
                </div>
              </header>
              <textarea
                aria-label={`第 ${index + 1} 段剧本文字`}
                disabled={blocked}
                onChange={(event) => changeFrame(index, { text: event.currentTarget.value })}
                onFocus={() => onSelectFrame(index)}
                ref={selected ? selectedTextArea : undefined}
                value={frame.text}
              />
              <div className="continuous-chapter-asset-lines">
                <label>角色素材
                  <select aria-label={`第 ${index + 1} 段角色素材`} disabled={blocked} multiple onChange={(event) => changeAssetSelection(index, "character", event)} value={[...frame.characterIds]}>
                    {catalogs.characters.filter((asset) => asset.series_id === snapshot.chapter.series_id).map((asset) => <option key={asset.id} value={asset.id}>{asset.name}</option>)}
                  </select>
                </label>
                <label>场景素材
                  <select aria-label={`第 ${index + 1} 段场景素材`} disabled={blocked} multiple onChange={(event) => changeAssetSelection(index, "scene", event)} value={[...frame.sceneIds]}>
                    {catalogs.scenes.filter((asset) => asset.series_id === snapshot.chapter.series_id).map((asset) => <option key={asset.id} value={asset.id}>{asset.title}</option>)}
                  </select>
                </label>
                <label>道具素材
                  <select aria-label={`第 ${index + 1} 段道具素材`} disabled={blocked} multiple onChange={(event) => changeAssetSelection(index, "prop", event)} value={[...frame.propIds]}>
                    {catalogs.props.filter((asset) => asset.series_id === snapshot.chapter.series_id).map((asset) => <option key={asset.id} value={asset.id}>{asset.name}</option>)}
                  </select>
                </label>
              </div>
              {selected && <p className="continuous-chapter-original">{originalText(frame).status === "text" ? `打开时原文：${originalText(frame).text}` : originalText(frame).status === "missing" ? "该镜头没有原文字段。" : "原文字段无法识别。"}</p>}
            </article>
          );
        })}
      </div>
      <div className="continuous-chapter-create-asset">
        <label htmlFor="continuous-chapter-new-asset">新增素材名称</label>
        <input id="continuous-chapter-new-asset" disabled={blocked || selectedFrame === null} onChange={(event) => setNewAssetName(event.currentTarget.value)} value={newAssetName} />
        <button disabled={blocked || selectedFrame === null || newAssetName.trim() === ""} onClick={() => createAsset("character")} type="button">创建并关联角色</button>
        <button disabled={blocked || selectedFrame === null || newAssetName.trim() === ""} onClick={() => createAsset("scene")} type="button">创建并关联场景</button>
        <button disabled={blocked || selectedFrame === null || newAssetName.trim() === ""} onClick={() => createAsset("prop")} type="button">创建并关联道具</button>
      </div>
    </section>
  );
}
