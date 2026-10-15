import { useEffect, useMemo, useRef, useState, type ChangeEvent } from "react";
import {
  type AssetLibraryType,
  type Character,
  type Prop,
  type Scene,
} from "../../../shared/api/contracts";
import {
  type CharacterCreateInput,
  type CharacterUpdateInput,
  type PropCreateInput,
  type PropUpdateInput,
  type SceneCreateInput,
  type SceneUpdateInput,
} from "../../../shared/api/assetManagement";
import { ApiError } from "../../../shared/api/errors";
import { normalizeApiError, type WorkspaceServices } from "../../../shared/api/services";
import { projectAssetFrameUsage, type AssetFrameUsageProjection } from "../assetFrameUsage";
import { useSeriesDialogFocus } from "../../series/useSeriesDialogFocus";

import "./AssetManagementDialog.css";

export type AssetManagementMode = "create" | "edit" | "delete";

export type AssetDirectory =
  | { category: "characters"; items: Character[] }
  | { category: "scenes"; items: Scene[] }
  | { category: "props"; items: Prop[] };

interface AssetManagementDialogProps {
  mode: AssetManagementMode;
  category: AssetLibraryType;
  item: Character | Scene | Prop | null;
  seriesId: string;
  seriesName: string;
  services: WorkspaceServices;
  isCurrent(): boolean;
  onClose(): void;
  onUnauthorized(): void;
  onDirectoryUpdated(directory: AssetDirectory): void;
  onDirectoryNeedsRefresh(): void;
}

interface FieldDefinition {
  key: "name" | "title" | "gender" | "age" | "role" | "appearance" | "description";
  label: string;
  required?: boolean;
  multiline?: boolean;
}

type FormValues = Record<FieldDefinition["key"], string>;

type WriteState =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "checking" }
  | { kind: "unknown"; message: string }
  | { kind: "saved-refresh-failed"; message: string }
  | { kind: "conflict"; message: string }
  | { kind: "error"; message: string };

type DeletePreview =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; projection: AssetFrameUsageProjection; totalChapterCount: number };

const emptyForm: FormValues = {
  name: "",
  title: "",
  gender: "",
  age: "",
  role: "",
  appearance: "",
  description: "",
};

function fieldsFor(category: AssetLibraryType): FieldDefinition[] {
  if (category === "characters") {
    return [
      { key: "name", label: "角色名称", required: true },
      { key: "gender", label: "性别" },
      { key: "age", label: "年龄" },
      { key: "role", label: "身份", multiline: true },
      { key: "appearance", label: "外观", multiline: true },
      { key: "description", label: "描述", multiline: true },
    ];
  }
  if (category === "scenes") {
    return [
      { key: "title", label: "场景名称", required: true },
      { key: "description", label: "描述", multiline: true },
    ];
  }
  return [
    { key: "name", label: "道具名称", required: true },
    { key: "description", label: "描述", multiline: true },
  ];
}

function categoryLabel(category: AssetLibraryType): string {
  return category === "characters" ? "角色" : category === "scenes" ? "场景" : "道具";
}

function assetLabel(category: AssetLibraryType, item: Character | Scene | Prop | null): string {
  if (item === null) {
    return categoryLabel(category);
  }
  const name = category === "scenes"
    ? ("title" in item ? item.title : "")
    : ("name" in item ? item.name : "");
  return name.trim() === "" ? `未命名${categoryLabel(category)}` : name;
}

function valueFor(item: Character | Scene | Prop | null, key: FieldDefinition["key"]): string {
  if (item === null) {
    return "";
  }
  const value = item[key as keyof typeof item];
  return typeof value === "string" ? value : "";
}

function readDirectory(
  services: WorkspaceServices,
  category: AssetLibraryType,
  seriesId: string,
  signal: AbortSignal,
): Promise<AssetDirectory> {
  switch (category) {
    case "characters":
      return services.listCharacters(seriesId, signal).then((items) => ({ category, items }));
    case "scenes":
      return services.listScenes(seriesId, signal).then((items) => ({ category, items }));
    case "props":
      return services.listProps(seriesId, signal).then((items) => ({ category, items }));
  }
}

function errorMessage(error: ApiError): string {
  if (error.status === 409) {
    return "素材或引用内容已发生变化，请重新读取并核对后再操作。";
  }
  if (error.status === 403) {
    return error.detail ?? "当前账号没有修改这项素材的权限。";
  }
  if (error.status === 404) {
    return "这项素材已不存在或当前剧集不可用，请重新读取目录。";
  }
  if (error.status === 422) {
    return error.detail ?? "提交内容未通过服务端校验，请检查表单内容。";
  }
  if (error.kind === "timeout") {
    return "读取目录超时，请稍后重新读取。";
  }
  if (error.kind === "network") {
    return "暂时无法连接本地服务，请确认服务正在运行后重新读取。";
  }
  if (error.kind === "invalid-response") {
    return "服务返回内容无法识别，请重新读取目录。";
  }
  return error.detail ?? error.message ?? "操作未完成，请重试。";
}

function isUncertainWrite(error: ApiError): boolean {
  return error.kind === "network"
    || error.kind === "timeout"
    || error.kind === "invalid-response"
    || (error.status !== null && error.status >= 500);
}

function changedValues(
  category: AssetLibraryType,
  item: Character | Scene | Prop,
  form: FormValues,
): Record<string, string> {
  const fields = fieldsFor(category);
  const updates: Record<string, string> = {};
  for (const field of fields) {
    const value = field.required ? form[field.key].trim() : form[field.key];
    const baseline = valueFor(item, field.key);
    if (value !== baseline) {
      updates[field.key] = value;
    }
  }
  return updates;
}

function updateMatchesDraft(
  item: Character | Scene | Prop,
  updates: Record<string, string>,
): boolean {
  return Object.entries(updates).every(([key, expected]) => {
    const value = item[key as keyof typeof item];
    return (typeof value === "string" ? value : "") === expected;
  });
}

async function createAsset(
  services: WorkspaceServices,
  category: AssetLibraryType,
  seriesId: string,
  form: FormValues,
  signal: AbortSignal,
): Promise<void> {
  if (category === "characters") {
    const create = services.createCharacter;
    if (create === undefined) throw new ApiError("invalid-response", "当前模式暂不支持创建素材。");
    const input: CharacterCreateInput = {
      name: form.name.trim(),
      gender: form.gender,
      age: form.age,
      role: form.role,
      appearance: form.appearance,
      description: form.description,
    };
    await create.call(services, seriesId, input, signal);
    return;
  }
  if (category === "scenes") {
    const create = services.createScene;
    if (create === undefined) throw new ApiError("invalid-response", "当前模式暂不支持创建素材。");
    const input: SceneCreateInput = { title: form.title.trim(), description: form.description };
    await create.call(services, seriesId, input, signal);
    return;
  }
  const create = services.createProp;
  if (create === undefined) throw new ApiError("invalid-response", "当前模式暂不支持创建素材。");
  const input: PropCreateInput = { name: form.name.trim(), description: form.description };
  await create.call(services, seriesId, input, signal);
}

async function updateAsset(
  services: WorkspaceServices,
  category: AssetLibraryType,
  seriesId: string,
  assetId: string,
  update: Record<string, string>,
  signal: AbortSignal,
): Promise<void> {
  if (category === "characters") {
    const updateCharacter = services.updateCharacter;
    if (updateCharacter === undefined) throw new ApiError("invalid-response", "当前模式暂不支持编辑素材。");
    await updateCharacter.call(services, seriesId, assetId, update as CharacterUpdateInput, signal);
    return;
  }
  if (category === "scenes") {
    const updateScene = services.updateScene;
    if (updateScene === undefined) throw new ApiError("invalid-response", "当前模式暂不支持编辑素材。");
    await updateScene.call(services, seriesId, assetId, update as SceneUpdateInput, signal);
    return;
  }
  const updateProp = services.updateProp;
  if (updateProp === undefined) throw new ApiError("invalid-response", "当前模式暂不支持编辑素材。");
  await updateProp.call(services, seriesId, assetId, update as PropUpdateInput, signal);
}

async function deleteAsset(
  services: WorkspaceServices,
  category: AssetLibraryType,
  assetId: string,
  signal: AbortSignal,
): Promise<void> {
  const remove = category === "characters"
    ? services.deleteCharacter
    : category === "scenes" ? services.deleteScene : services.deleteProp;
  if (remove === undefined) {
    throw new ApiError("invalid-response", "当前模式暂不支持删除素材。");
  }
  await remove.call(services, assetId, signal);
}

function canCreate(services: WorkspaceServices, category: AssetLibraryType): boolean {
  return category === "characters" ? services.createCharacter !== undefined
    : category === "scenes" ? services.createScene !== undefined
      : services.createProp !== undefined;
}

function canUpdate(services: WorkspaceServices, category: AssetLibraryType): boolean {
  return category === "characters" ? services.updateCharacter !== undefined
    : category === "scenes" ? services.updateScene !== undefined
      : services.updateProp !== undefined;
}

function canDelete(services: WorkspaceServices, category: AssetLibraryType): boolean {
  return category === "characters" ? services.deleteCharacter !== undefined
    : category === "scenes" ? services.deleteScene !== undefined
      : services.deleteProp !== undefined;
}

function sameEditableBaseline(
  category: AssetLibraryType,
  baseline: Character | Scene | Prop,
  current: Character | Scene | Prop,
): boolean {
  return fieldsFor(category).every((field) => valueFor(baseline, field.key) === valueFor(current, field.key));
}

export function AssetManagementDialog({
  mode,
  category,
  item,
  seriesId,
  seriesName,
  services,
  isCurrent,
  onClose,
  onUnauthorized,
  onDirectoryUpdated,
  onDirectoryNeedsRefresh,
}: AssetManagementDialogProps) {
  const isCurrentRef = useRef(isCurrent);
  const unauthorizedRef = useRef(onUnauthorized);
  isCurrentRef.current = isCurrent;
  unauthorizedRef.current = onUnauthorized;
  const fields = useMemo(() => fieldsFor(category), [category]);
  const [form, setForm] = useState<FormValues>(() => ({
    ...emptyForm,
    ...Object.fromEntries(fields.map((field) => [field.key, valueFor(item, field.key)])),
  }));
  const [writeState, setWriteState] = useState<WriteState>({ kind: "idle" });
  const [deletePreview, setDeletePreview] = useState<DeletePreview>({ kind: "loading" });
  const operationControllerRef = useRef<AbortController | null>(null);
  const submittingRef = useRef(false);
  const title = mode === "create" ? `新增${categoryLabel(category)}`
    : mode === "edit" ? `编辑${categoryLabel(category)}`
      : `删除${categoryLabel(category)}`;
  const currentAssetLabel = assetLabel(category, item);
  const requiredField = fields.find((field) => field.required);
  const nameIsValid = requiredField === undefined || form[requiredField.key].trim() !== "";
  const update = mode === "edit" && item !== null ? changedValues(category, item, form) : {};
  const canSave = mode === "create"
    ? nameIsValid
    : mode === "edit" && item !== null && nameIsValid && Object.keys(update).length > 0;
  const pending = writeState.kind === "saving" || writeState.kind === "checking";
  const focus = useSeriesDialogFocus({ active: true, canDismiss: !pending, onDismiss: onClose });

  useEffect(() => () => {
    operationControllerRef.current?.abort();
  }, []);

  useEffect(() => {
    if (mode !== "delete" || item === null) {
      return undefined;
    }
    const controller = new AbortController();
    operationControllerRef.current = controller;
    let active = true;
    setDeletePreview({ kind: "loading" });
    void services.listChapters(seriesId, controller.signal).then((chapters) => {
      if (!active || controller.signal.aborted || !isCurrentRef.current()) {
        return;
      }
      const projection = projectAssetFrameUsage(chapters, seriesId, category, item);
      setDeletePreview({ kind: "ready", projection, totalChapterCount: chapters.length });
    }).catch((cause: unknown) => {
      if (!active || controller.signal.aborted || !isCurrentRef.current()) {
        return;
      }
      const error = normalizeApiError(cause);
      if (error.status === 401) {
        unauthorizedRef.current();
        return;
      }
      setDeletePreview({ kind: "error", message: errorMessage(error) });
    });
    return () => {
      active = false;
      controller.abort();
    };
  }, [category, item, mode, seriesId, services]);

  function updateField(event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) {
    const { name, value } = event.currentTarget;
    setForm((current) => ({ ...current, [name]: value }));
    if (writeState.kind === "error") {
      setWriteState({ kind: "idle" });
    }
  }

  function newController(): AbortController {
    operationControllerRef.current?.abort();
    const controller = new AbortController();
    operationControllerRef.current = controller;
    return controller;
  }

  async function refreshDirectory(controller: AbortController, reason: "unknown" | "saved" | "manual") {
    try {
      const directory = await readDirectory(services, category, seriesId, controller.signal);
      if (controller.signal.aborted || !isCurrent()) {
        return;
      }
      onDirectoryUpdated(directory);
      if (reason === "saved") {
        focus.markDismissed();
        onClose();
        return;
      }
      if (reason === "unknown") {
        let message: string;
        if (mode === "create") {
          message = "创建请求结果未知。目录已重新读取，但不能按同名判断新素材身份；草稿已保留，且不会自动再次创建。";
        } else if (mode === "edit" && item !== null) {
          const current = directory.items.find((candidate) => candidate.id === item.id && candidate.series_id === seriesId);
          if (current === undefined) {
            message = "请求结果未知。目录中已找不到此素材 ID；草稿已保留，请核对后再决定下一步。";
          } else if (updateMatchesDraft(current, update)) {
            message = "请求结果未知。按素材 ID 重新读取后，当前字段与草稿一致；草稿已保留。";
          } else {
            message = "请求结果未知。按素材 ID 重新读取后，当前字段与草稿不完全一致；草稿已保留。";
          }
        } else if (mode === "delete" && item !== null) {
          const exists = directory.items.some((candidate) => candidate.id === item.id && candidate.series_id === seriesId);
          message = exists
            ? "请求结果未知。目录核对发现此素材 ID 仍存在；不会自动再次删除。"
            : "请求结果未知。目录 GET 已确认此素材 ID 不再存在；不会再次发送删除请求。";
        } else {
          message = "请求结果未知。目录已重新读取，请按素材 ID 核对当前状态。";
        }
        setWriteState({ kind: "unknown", message });
        return;
      }
      setWriteState({ kind: "idle" });
    } catch (cause: unknown) {
      if (controller.signal.aborted || !isCurrent()) {
        return;
      }
      const error = normalizeApiError(cause);
      if (error.status === 401) {
        onUnauthorized();
        return;
      }
      setWriteState(reason === "saved"
        ? { kind: "saved-refresh-failed", message: `${mode === "delete" ? "已删除" : "已保存"}，目录刷新待完成。${errorMessage(error)}` }
        : { kind: "unknown", message: `写入结果仍未知，目录核对失败。草稿已保留。${errorMessage(error)}` });
    }
  }

  async function recheckDirectory() {
    if (submittingRef.current || !isCurrent()) {
      return;
    }
    const controller = newController();
    submittingRef.current = true;
    const previousState = writeState;
    setWriteState({ kind: "checking" });
    const reason = previousState.kind === "saved-refresh-failed"
      ? "saved"
      : previousState.kind === "unknown" ? "unknown" : "manual";
    await refreshDirectory(controller, reason);
    if (isCurrent()) {
      submittingRef.current = false;
      if (previousState.kind === "conflict") {
        setWriteState({ kind: "conflict", message: "目录已重新读取。请关闭此窗口，再从最新素材卡片打开操作。" });
      } else if (previousState.kind === "error") {
        setWriteState({ kind: "idle" });
      }
    }
  }

  async function save() {
    if (submittingRef.current || !isCurrent() || !canSave
      || writeState.kind === "unknown" || writeState.kind === "saved-refresh-failed"
      || writeState.kind === "conflict" || writeState.kind === "saving" || writeState.kind === "checking") {
      return;
    }
    submittingRef.current = true;
    const controller = newController();
    setWriteState({ kind: "saving" });
    let writeDispatched = false;
    try {
      if (mode === "edit" && item !== null) {
        const fresh = await readDirectory(services, category, seriesId, controller.signal);
        if (controller.signal.aborted || !isCurrent()) {
          return;
        }
        const current = fresh.items.find((candidate) => candidate.id === item.id && candidate.series_id === seriesId);
        onDirectoryUpdated(fresh);
        if (current === undefined || !sameEditableBaseline(category, item, current)) {
          setWriteState({ kind: "conflict", message: "保存前重新读取发现素材已变化。草稿已保留，请关闭后从最新目录重新编辑。" });
          return;
        }
        if (!canUpdate(services, category)) {
          throw new ApiError("invalid-response", "当前模式暂不支持编辑素材。");
        }
        writeDispatched = true;
        await updateAsset(services, category, seriesId, item.id, update, controller.signal);
      } else if (mode === "create") {
        if (!canCreate(services, category)) {
          throw new ApiError("invalid-response", "当前模式暂不支持创建素材。");
        }
        writeDispatched = true;
        await createAsset(services, category, seriesId, form, controller.signal);
      } else {
        return;
      }
      if (controller.signal.aborted || !isCurrent()) {
        return;
      }
      onDirectoryNeedsRefresh();
      setWriteState({ kind: "checking" });
      await refreshDirectory(controller, "saved");
    } catch (cause: unknown) {
      if (controller.signal.aborted || !isCurrent()) {
        return;
      }
      const error = normalizeApiError(cause);
      if (error.status === 401) {
        onUnauthorized();
        return;
      }
      if (error.status === 409) {
        setWriteState({ kind: "conflict", message: errorMessage(error) });
        return;
      }
      if (writeDispatched && isUncertainWrite(error)) {
        onDirectoryNeedsRefresh();
        setWriteState({ kind: "checking" });
        await refreshDirectory(controller, "unknown");
        return;
      }
      setWriteState({ kind: "error", message: errorMessage(error) });
    } finally {
      if (isCurrent()) {
        submittingRef.current = false;
      }
    }
  }

  async function confirmDelete() {
    if (submittingRef.current || !isCurrent() || mode !== "delete" || item === null
      || deletePreview.kind !== "ready" || deletePreview.projection.status === "unreadable"
      || writeState.kind === "unknown" || writeState.kind === "saved-refresh-failed"
      || writeState.kind === "conflict") {
      return;
    }
    submittingRef.current = true;
    const controller = newController();
    setWriteState({ kind: "saving" });
    let writeDispatched = false;
    try {
      if (!canDelete(services, category)) {
        throw new ApiError("invalid-response", "当前模式暂不支持删除素材。");
      }
      writeDispatched = true;
      await deleteAsset(services, category, item.id, controller.signal);
      if (controller.signal.aborted || !isCurrent()) {
        return;
      }
      onDirectoryNeedsRefresh();
      setWriteState({ kind: "checking" });
      await refreshDirectory(controller, "saved");
    } catch (cause: unknown) {
      if (controller.signal.aborted || !isCurrent()) {
        return;
      }
      const error = normalizeApiError(cause);
      if (error.status === 401) {
        onUnauthorized();
        return;
      }
      if (error.status === 409) {
        setWriteState({ kind: "conflict", message: errorMessage(error) });
        return;
      }
      if (writeDispatched && isUncertainWrite(error)) {
        onDirectoryNeedsRefresh();
        setWriteState({ kind: "checking" });
        await refreshDirectory(controller, "unknown");
        return;
      }
      setWriteState({ kind: "error", message: errorMessage(error) });
    } finally {
      if (isCurrent()) {
        submittingRef.current = false;
      }
    }
  }

  function renderField(field: FieldDefinition) {
    const id = `asset-management-${field.key}`;
    const control = field.multiline
      ? <textarea disabled={pending || frozenWrite} id={id} name={field.key} onChange={updateField} rows={field.key === "description" ? 4 : 3} value={form[field.key]} />
      : <input autoComplete="off" disabled={pending || frozenWrite} id={id} name={field.key} onChange={updateField} type="text" value={form[field.key]} />;
    return (
      <label className="asset-management-field" htmlFor={id} key={field.key}>
        <span>{field.label}{field.required ? <b aria-hidden="true"> *</b> : null}</span>
        {control}
      </label>
    );
  }

  const frozenWrite = writeState.kind === "unknown"
    || writeState.kind === "saved-refresh-failed"
    || writeState.kind === "conflict";
  const previewProjection = deletePreview.kind === "ready" ? deletePreview.projection : null;
  const affectedChapterCount = previewProjection?.status === "matches" ? previewProjection.chapters.length : 0;
  const affectedFrameCount = previewProjection?.status === "matches" ? previewProjection.frameCount : 0;
  const affectedReferenceCount = previewProjection?.status === "matches" ? previewProjection.referenceCount : 0;
  const previewIsUncertain = previewProjection?.status === "empty"
    ? previewProjection.hasUncertainReferences
    : previewProjection?.status === "matches" ? previewProjection.hasUncertainReferences : false;

  return (
    <div className="asset-management-backdrop">
      <section
        aria-labelledby="asset-management-title"
        aria-modal="true"
        className="asset-management-dialog"
        ref={focus.dialogRef}
        role="dialog"
        tabIndex={-1}
      >
        <header className="asset-management-header">
          <div>
            <p className="eyebrow">{seriesName} · 素材目录</p>
            <h2 id="asset-management-title">{title}</h2>
          </div>
          <button aria-label="关闭素材窗口" className="asset-management-close" disabled={pending} onClick={focus.dismiss} type="button">×</button>
        </header>

        {mode === "delete" ? (
          <div className="asset-management-delete-copy">
            <p>删除后会从整个剧集及所有章节镜头中移除「{currentAssetLabel}」。</p>
            <p className="asset-management-note">以下为当前影响预览，实际删除以最新章节为准。</p>
            {deletePreview.kind === "loading" && <p role="status">正在重新读取章节以核对影响…</p>}
            {deletePreview.kind === "error" && (
              <div className="asset-management-error" role="alert">
                <p>暂时无法预览删除影响，当前不能确认删除。</p>
                <p>{deletePreview.message}</p>
                <button className="secondary-button" disabled={pending} onClick={() => {
                  const controller = newController();
                  setDeletePreview({ kind: "loading" });
                  void services.listChapters(seriesId, controller.signal).then((chapters) => {
                    if (controller.signal.aborted || !isCurrent()) return;
                    setDeletePreview({
                      kind: "ready",
                      projection: projectAssetFrameUsage(chapters, seriesId, category, item as Character | Scene | Prop),
                      totalChapterCount: chapters.length,
                    });
                  }).catch((cause: unknown) => {
                    if (controller.signal.aborted || !isCurrent()) return;
                    const error = normalizeApiError(cause);
                    if (error.status === 401) onUnauthorized();
                    else setDeletePreview({ kind: "error", message: errorMessage(error) });
                  });
                }} type="button">重新读取章节</button>
              </div>
            )}
            {deletePreview.kind === "ready" && previewProjection?.status === "unreadable" && (
              <p className="asset-management-error" role="alert">章节响应无法安全核对，当前不能删除。</p>
            )}
            {deletePreview.kind === "ready" && previewProjection?.status === "empty" && (
              <p role="status">已读取 {deletePreview.totalChapterCount} 章；未找到可确认的引用。</p>
            )}
            {deletePreview.kind === "ready" && previewProjection?.status === "matches" && (
              <p role="status">已读取 {deletePreview.totalChapterCount} 章；发现 {affectedChapterCount} 章、{affectedFrameCount} 个镜头、{affectedReferenceCount} 次可确认引用。</p>
            )}
            {previewIsUncertain && <p className="asset-management-note" role="note">部分章节引用格式无法识别，上面的数量只包含可确认的引用。</p>}
          </div>
        ) : (
          <div className="asset-management-form">
            {fields.map(renderField)}
            {!nameIsValid && <p className="asset-management-error" role="alert">名称不能为空。</p>}
            {mode === "edit" && Object.keys(update).length === 0 && <p className="asset-management-note" role="status">没有可保存的更改。</p>}
            <p className="asset-management-note">图片、音频、音色引用和别名不会随这些文字字段一起改写。</p>
          </div>
        )}

        {writeState.kind === "error" && <p className="asset-management-error" role="alert">{writeState.message}</p>}
        {writeState.kind === "conflict" && <p className="asset-management-error" role="alert">{writeState.message}</p>}
        {writeState.kind === "unknown" && <p className="asset-management-warning" role="status">{writeState.message}</p>}
        {writeState.kind === "saved-refresh-failed" && <p className="asset-management-warning" role="status">{writeState.message}</p>}
        {pending && <p className="asset-management-pending" role="status">{writeState.kind === "saving" ? "正在提交…" : "正在重新读取目录…"}</p>}

        <footer className="asset-management-footer">
          <button className="secondary-button" disabled={pending} onClick={focus.dismiss} type="button">关闭</button>
          {mode === "delete" && !frozenWrite && (
            <button
              className="danger-button asset-management-submit"
              disabled={pending || deletePreview.kind !== "ready" || deletePreview.projection.status === "unreadable"}
              onClick={() => void confirmDelete()}
              type="button"
            >
              确认删除整个剧集中的素材
            </button>
          )}
          {mode !== "delete" && !frozenWrite && (
            <button
              className="primary-button asset-management-submit"
              disabled={pending || !canSave}
              onClick={() => void save()}
              type="button"
            >
              {mode === "create" ? "创建素材" : "保存更改"}
            </button>
          )}
          {frozenWrite && (
            <button className="secondary-button" disabled={pending} onClick={() => void recheckDirectory()} type="button">
              仅重新读取目录
            </button>
          )}
        </footer>
      </section>
    </div>
  );
}
