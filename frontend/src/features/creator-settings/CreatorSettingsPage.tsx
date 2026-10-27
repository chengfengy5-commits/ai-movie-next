import { useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { ApiError } from "../../shared/api/errors";
import type {
  CreatorModel,
  CreatorModelCategory,
  CreatorModelConnectionTestInput,
  CreatorModelCreateInput,
  CreatorModelUpdateInput,
  CreatorPrompt,
  CreatorPromptCategory,
  CreatorPromptCreateInput,
  CreatorPromptUpdateInput,
  CreatorSettingsServicePort,
} from "../../shared/api/creatorSettings";
import { parseCreatorModel } from "../../shared/api/creatorSettings";
import { ModelConnectionTest } from "./ModelConnectionTest";
import { ProviderQuotaPanel } from "./ProviderQuotaPanel";
import type { ProviderQuotaServicePort } from "../../shared/api/providerQuota";
import "./creatorSettings.css";

type SettingsTab = "models" | "prompts";
type DraftMode = "create" | "edit" | "copy";
type ModelConversionTarget = "system" | "personal";

interface ModelDraft {
  kind: "model";
  mode: DraftMode;
  id?: string;
  sourceName?: string;
  category: CreatorModelCategory;
  name: string;
  provider: string;
  apiKey: string;
  model: string;
  baseUrl: string;
  params: string;
  paramsBaseline?: string;
  original?: CreatorModel;
}

interface PromptDraft {
  kind: "prompt";
  mode: DraftMode;
  id?: string;
  sourceName?: string;
  category: CreatorPromptCategory;
  name: string;
  systemPrompt: string;
  userPrompt: string;
  original?: CreatorPrompt;
}

type SettingsDraft = ModelDraft | PromptDraft;

type Verification =
  | {
      kind: "entity";
      tab: SettingsTab;
      id: string;
      category: string;
      expected: Record<string, unknown>;
      uncertain: boolean;
      secretChanged?: boolean;
      mutationUnknown?: boolean;
    }
  | { kind: "delete"; tab: SettingsTab; id: string; category: string; uncertain: boolean }
  | { kind: "order"; tab: SettingsTab; category: string; ids: string[]; uncertain: boolean }
  | {
      kind: "model-conversion";
      tab: "models";
      actorId: string;
      category: string;
      sourceId: string;
      newId: string | null;
      target: ModelConversionTarget;
      service: CreatorSettingsServicePort;
      uncertain: boolean;
    }
  | { kind: "create-unknown"; tab: SettingsTab; category: string; name: string };

interface CreatorSettingsPageProps {
  service?: CreatorSettingsServicePort;
  providerQuotaService?: ProviderQuotaServicePort | undefined;
  userId: string;
  isSuperuser?: boolean;
  onUnauthorized(): void;
  onBack(): void;
  onDirtyChange?(dirty: boolean): void;
}

const MODEL_CATEGORIES: Array<{ value: CreatorModelCategory; label: string }> = [
  { value: "text", label: "文本模型" },
  { value: "image", label: "图片模型" },
  { value: "video", label: "视频模型" },
  { value: "reasoning", label: "推理模型" },
];

const PROMPT_CATEGORIES: Array<{ value: CreatorPromptCategory; label: string }> = [
  { value: "storyboard", label: "分镜生成" },
  { value: "chapter-division", label: "章节拆分" },
  { value: "character-extraction", label: "角色提取" },
  { value: "scene-extraction", label: "场景提取" },
  { value: "prop-extraction", label: "道具提取" },
  { value: "character-design", label: "角色设计" },
  { value: "scene-design", label: "场景设计" },
  { value: "prop-design", label: "道具设计" },
  { value: "storyboard-design", label: "分镜设计" },
  { value: "video-design", label: "视频设计" },
  { value: "chat-assistant", label: "创作助手" },
  { value: "frame-optimize", label: "分镜优化" },
  { value: "ai-review", label: "AI复核" },
  { value: "scene-segmentation", label: "场景切分" },
  { value: "style", label: "风格" },
  { value: "fuse", label: "融合" },
];

interface RequestHandle {
  signal: AbortSignal;
  isCurrent(): boolean;
  finish(): void;
}

function canManageModel(item: CreatorModel, userId: string): boolean {
  return item.is_system === false && item.user_id === userId;
}

function canManagePrompt(item: CreatorPrompt, userId: string): boolean {
  return item.is_system === false && item.user_id === userId;
}

function rowsInCurrentScope(nextTab: SettingsTab, rows: CreatorModel[] | CreatorPrompt[], userId: string): CreatorModel[] | CreatorPrompt[] {
  if (nextTab === "models") {
    return (rows as CreatorModel[]).filter((item) => item.is_system || item.user_id === userId);
  }
  return (rows as CreatorPrompt[]).filter((item) => (
    item.is_system === true || (item.is_system === false && item.user_id === userId)
  ));
}

function categoryName(value: string, options: Array<{ value: string; label: string }>): string {
  return options.find((option) => option.value === value)?.label ?? `其他分类 · ${value}`;
}

function optionalValue(value: string): string | null {
  const normalized = value.trim();
  return normalized === "" ? null : normalized;
}

function safeFailureMessage(error: unknown, action: string): string {
  if (error instanceof ApiError) {
    if (error.status === 403) return "服务端拒绝了此操作，当前账号仍保持登录。";
    if (error.status === 401) return "当前登录状态已失效，请重新登录。";
    if (error.kind === "timeout" || error.kind === "network") return `${action}连接中断，结果需要刷新核对。`;
  }
  return `${action}未能确认，已保留当前草稿；请先刷新核对。`;
}

function isCurrentUnauthorized(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}

function isDefinitiveModelConversionFailure(error: unknown): boolean {
  if (!(error instanceof ApiError)) return false;
  if (error.kind === "network" || error.kind === "timeout" || error.kind === "invalid-response" || error.kind === "aborted") {
    return false;
  }
  return error.status !== null && error.status !== 408 && error.status < 500;
}

function modelConversionFailureMessage(error: unknown, target: ModelConversionTarget): string {
  if (error instanceof ApiError && error.status === 403) {
    return "服务端拒绝了此转换，当前账号仍保持登录。";
  }
  const action = target === "system" ? "设为系统" : "转为个人";
  if (error instanceof ApiError && error.status !== null && error.status >= 400 && error.status < 500) {
    return `服务端未接受“${action}”操作（HTTP ${error.status}）；不会自动重试，请刷新列表后再决定。`;
  }
  return `“${action}”操作未完成；不会自动重试，请刷新列表核对。`;
}

function sameValue(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

function validateModelParams(text: string): { value: string | null; error: string | null } {
  if (text.trim() === "") return { value: null, error: null };
  try {
    const parsed: unknown = JSON.parse(text);
    if (parsed === null || Array.isArray(parsed) || typeof parsed !== "object") {
      return { value: null, error: "参数必须是 JSON 对象。" };
    }
    return { value: text, error: null };
  } catch {
    return { value: null, error: "参数必须是合法的 JSON 对象；原有内容仍保留。" };
  }
}

function modelDraftHasChanges(draft: ModelDraft): boolean {
  if (draft.mode !== "edit" || draft.original === undefined) return true;
  const base = draft.original;
  return draft.name !== base.name
    || draft.provider !== base.provider
    || draft.model !== (base.model ?? "")
    || draft.baseUrl !== (base.base_url ?? "")
    || draft.params !== (base.params ?? "")
    || draft.apiKey !== "";
}

function promptDraftHasChanges(draft: PromptDraft): boolean {
  if (draft.mode !== "edit" || draft.original === undefined) return true;
  return draft.name !== draft.original.name
    || draft.systemPrompt !== (draft.original.system_prompt ?? "")
    || draft.userPrompt !== (draft.original.user_prompt ?? "");
}

function draftHasChanges(draft: SettingsDraft | null): boolean {
  if (draft === null) return false;
  return draft.kind === "model" ? modelDraftHasChanges(draft) : promptDraftHasChanges(draft);
}

export function CreatorSettingsPage({
  service,
  providerQuotaService,
  userId,
  isSuperuser = false,
  onUnauthorized,
  onBack,
  onDirtyChange,
}: CreatorSettingsPageProps) {
  const [tab, setTab] = useState<SettingsTab>("models");
  const [modelCategory, setModelCategory] = useState<CreatorModelCategory>("text");
  const [promptCategory, setPromptCategory] = useState<CreatorPromptCategory>("storyboard");
  const [models, setModels] = useState<CreatorModel[]>([]);
  const [prompts, setPrompts] = useState<CreatorPrompt[]>([]);
  const [loading, setLoading] = useState(false);
  const [pending, setPending] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [providerQuotaOpen, setProviderQuotaOpen] = useState(false);
  const [draft, setDraft] = useState<SettingsDraft | null>(null);
  const [modelDraftGeneration, setModelDraftGeneration] = useState(0);
  const [orderDraft, setOrderDraft] = useState<{ tab: SettingsTab; category: string; baseline: string[]; ids: string[] } | null>(null);
  const [orderConflict, setOrderConflict] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<{ tab: SettingsTab; id: string; name: string } | null>(null);
  const [verification, setVerification] = useState<Verification | null>(null);
  const requestGeneration = useRef(0);
  const viewGeneration = useRef(0);
  const editorGeneration = useRef(0);
  const controllers = useRef(new Set<AbortController>());
  const catalogGeneration = useRef<Record<SettingsTab, number>>({ models: 0, prompts: 0 });
  const loadingGeneration = useRef(0);
  const conversionGeneration = useRef(0);
  const pendingLock = useRef(false);
  const tabButtons = useRef<Record<SettingsTab, HTMLButtonElement | null>>({ models: null, prompts: null });
  const propsRef = useRef({ service, userId, isSuperuser, onUnauthorized, onDirtyChange });
  const modelCategoryRef = useRef(modelCategory);
  propsRef.current = { service, userId, isSuperuser, onUnauthorized, onDirtyChange };
  modelCategoryRef.current = modelCategory;

  useLayoutEffect(() => {
    requestGeneration.current += 1;
    viewGeneration.current += 1;
    editorGeneration.current += 1;
    catalogGeneration.current.models += 1;
    catalogGeneration.current.prompts += 1;
    loadingGeneration.current += 1;
    for (const controller of controllers.current) controller.abort();
    controllers.current.clear();
    setModels([]);
    setPrompts([]);
    setTab("models");
    setModelCategory("text");
    setPromptCategory("storyboard");
    setLoading(false);
    setPending(false);
    pendingLock.current = false;
    setListError(null);
    setNotice(null);
    setProviderQuotaOpen(false);
    setDraft(null);
    setModelDraftGeneration((generation) => generation + 1);
    conversionGeneration.current += 1;
    setOrderDraft(null);
    setOrderConflict(false);
    setDeleteTarget(null);
    setVerification(null);
    propsRef.current.onDirtyChange?.(false);
    return () => {
      requestGeneration.current += 1;
      viewGeneration.current += 1;
      editorGeneration.current += 1;
      catalogGeneration.current.models += 1;
      catalogGeneration.current.prompts += 1;
      loadingGeneration.current += 1;
      for (const controller of controllers.current) controller.abort();
      controllers.current.clear();
      propsRef.current.onDirtyChange?.(false);
    };
  }, [isSuperuser, service, userId]);

  const currentModels = useMemo(
    () => models.filter((item) => item.category === modelCategory),
    [models, modelCategory],
  );
  const currentPrompts = useMemo(
    () => prompts.filter((item) => item.category === promptCategory),
    [prompts, promptCategory],
  );
  const orderedModels = useMemo(() => {
    if (orderDraft?.tab !== "models" || orderDraft.category !== modelCategory) return currentModels;
    const byId = new Map(currentModels.map((item) => [item.id, item]));
    return [
      ...orderDraft.ids.map((id) => byId.get(id)).filter((item): item is CreatorModel => item !== undefined),
      ...currentModels.filter((item) => !orderDraft.ids.includes(item.id)),
    ];
  }, [currentModels, orderDraft, modelCategory]);
  const orderedPrompts = useMemo(() => {
    if (orderDraft?.tab !== "prompts" || orderDraft.category !== promptCategory) return currentPrompts;
    const byId = new Map(currentPrompts.map((item) => [item.id, item]));
    return [
      ...orderDraft.ids.map((id) => byId.get(id)).filter((item): item is CreatorPrompt => item !== undefined),
      ...currentPrompts.filter((item) => !orderDraft.ids.includes(item.id)),
    ];
  }, [currentPrompts, orderDraft, promptCategory]);
  const modelCategories = useMemo(() => {
    const known = MODEL_CATEGORIES.map((item) => item.value);
    const observed = models.map((item) => item.category);
    const values = Array.from(new Set([...known, ...observed]));
    return values.map((value) => ({ value, label: categoryName(value, MODEL_CATEGORIES) }));
  }, [models]);
  const promptCategories = useMemo(() => {
    const known = PROMPT_CATEGORIES.map((item) => item.value);
    const observed = prompts.map((item) => item.category);
    const values = Array.from(new Set([...known, ...observed]));
    return values.map((value) => ({ value, label: categoryName(value, PROMPT_CATEGORIES) }));
  }, [prompts]);
  const currentCategory = tab === "models" ? modelCategory : promptCategory;
  const visibleIds = tab === "models"
    ? orderedModels.map((item) => item.id)
    : orderedPrompts.map((item) => item.id);
  const hasUnsavedChanges = draftHasChanges(draft)
    || (orderDraft !== null && !sameValue(orderDraft.ids, orderDraft.baseline));

  useEffect(() => {
    propsRef.current.onDirtyChange?.(hasUnsavedChanges || pending || verification !== null);
  }, [hasUnsavedChanges, onDirtyChange, pending, verification]);

  function beginRequest(editorBound = false): RequestHandle {
    const sessionGeneration = requestGeneration.current;
    const currentViewGeneration = viewGeneration.current;
    const currentEditorGeneration = editorGeneration.current;
    const controller = new AbortController();
    controllers.current.add(controller);
    return {
      signal: controller.signal,
      isCurrent: () => requestGeneration.current === sessionGeneration
        && viewGeneration.current === currentViewGeneration
        && (!editorBound || editorGeneration.current === currentEditorGeneration)
        && !controller.signal.aborted,
      finish: () => {
        controllers.current.delete(controller);
      },
    };
  }

  function invalidateCurrentView(): void {
    viewGeneration.current += 1;
    catalogGeneration.current.models += 1;
    catalogGeneration.current.prompts += 1;
    loadingGeneration.current += 1;
    for (const controller of controllers.current) controller.abort();
    controllers.current.clear();
    setLoading(false);
  }

  function closeDraft(): void {
    editorGeneration.current += 1;
    setModelDraftGeneration((generation) => generation + 1);
    setDraft(null);
  }

  function updateModelDraft(update: (current: ModelDraft) => ModelDraft): void {
    setModelDraftGeneration((generation) => generation + 1);
    setDraft((current) => current?.kind === "model" ? update(current) : current);
  }

  function handleError(error: unknown, request: RequestHandle, action: string): void {
    if (!request.isCurrent()) return;
    if (isCurrentUnauthorized(error)) {
      propsRef.current.onUnauthorized();
      return;
    }
    setNotice(safeFailureMessage(error, action));
  }

  function invalidateCatalog(nextTab: SettingsTab): void {
    catalogGeneration.current[nextTab] += 1;
  }

  function storeList(nextTab: SettingsTab, rows: CreatorModel[] | CreatorPrompt[], category?: string): void {
    if (nextTab === "models") {
      const next = rowsInCurrentScope(nextTab, rows, userId) as CreatorModel[];
      setModels((previous) => category === undefined
        ? next
        : [...previous.filter((item) => item.category !== category), ...next]);
    } else {
      const next = rowsInCurrentScope(nextTab, rows, userId) as CreatorPrompt[];
      setPrompts((previous) => category === undefined
        ? next
        : [...previous.filter((item) => item.category !== category), ...next]);
    }
  }

  async function getList(nextTab: SettingsTab, signal: AbortSignal, category?: string): Promise<CreatorModel[] | CreatorPrompt[]> {
    const currentService = propsRef.current.service;
    if (currentService === undefined) throw new Error("creator settings service unavailable");
    if (nextTab === "models") {
      return currentService.listModels(category, signal);
    }
    return currentService.listPrompts(category, signal);
  }

  async function refreshList(nextTab: SettingsTab, category?: string): Promise<CreatorModel[] | CreatorPrompt[] | null> {
    const currentService = propsRef.current.service;
    if (currentService === undefined) {
      setListError("当前运行配置尚未接入创作者设置服务。");
      return null;
    }
    const request = beginRequest();
    const listVersion = ++catalogGeneration.current[nextTab];
    const loadingVersion = ++loadingGeneration.current;
    setLoading(true);
    setListError(null);
    try {
      const rows = await getList(nextTab, request.signal, category);
      if (!request.isCurrent() || catalogGeneration.current[nextTab] !== listVersion) return null;
      storeList(nextTab, rows, category);
      return rows;
    } catch (error) {
      if (!request.isCurrent() || catalogGeneration.current[nextTab] !== listVersion) return null;
      if (isCurrentUnauthorized(error)) {
        propsRef.current.onUnauthorized();
      } else {
        setListError(safeFailureMessage(error, "读取配置"));
      }
      return null;
    } finally {
      request.finish();
      if (loadingGeneration.current === loadingVersion) setLoading(false);
    }
  }

  useEffect(() => {
    void refreshList(tab);
  }, [tab, currentCategory, service, userId]);

  function confirmDiscard(): boolean {
    if (!hasUnsavedChanges) return true;
    return window.confirm("当前有未保存的配置或排序，离开将丢弃这些草稿。继续吗？");
  }

  function discardDrafts(): void {
    editorGeneration.current += 1;
    setModelDraftGeneration((generation) => generation + 1);
    setDraft(null);
    setOrderDraft(null);
    setOrderConflict(false);
    setDeleteTarget(null);
    setNotice(null);
  }

  function changeTab(nextTab: SettingsTab): boolean {
    if (nextTab === tab) return true;
    if (pendingLock.current || verification !== null || !confirmDiscard()) return false;
    invalidateCurrentView();
    discardDrafts();
    setProviderQuotaOpen(false);
    setTab(nextTab);
    setNotice(null);
    return true;
  }

  function handleTabKeyDown(event: ReactKeyboardEvent<HTMLButtonElement>, currentTabValue: SettingsTab): void {
    const nextTab = event.key === "ArrowRight"
      ? (currentTabValue === "models" ? "prompts" : "models")
      : event.key === "ArrowLeft"
        ? (currentTabValue === "models" ? "prompts" : "models")
        : event.key === "Home"
          ? "models"
          : event.key === "End"
            ? "prompts"
            : null;
    if (nextTab === null) return;
    event.preventDefault();
    if (changeTab(nextTab)) {
      queueMicrotask(() => tabButtons.current[nextTab]?.focus());
    }
  }

  function changeCategory(nextCategory: string): void {
    if (nextCategory === currentCategory || pendingLock.current || verification !== null || !confirmDiscard()) return;
    invalidateCurrentView();
    discardDrafts();
    if (tab === "models") setModelCategory(nextCategory);
    else setPromptCategory(nextCategory);
    setNotice(null);
  }

  function makeModelDraft(category: CreatorModelCategory, mode: "create" | "copy", source?: CreatorModel): ModelDraft {
    return {
      kind: "model",
      mode,
      ...(source === undefined ? {} : { sourceName: source.name }),
      category,
      name: source === undefined ? "" : `${source.name} 副本`,
      provider: source?.provider ?? "",
      apiKey: "",
      model: source?.model ?? "",
      baseUrl: source?.base_url ?? "",
      params: source?.params ?? "",
      ...(source === undefined ? {} : { paramsBaseline: source.params ?? "" }),
    };
  }

  function makePromptDraft(category: CreatorPromptCategory, mode: "create" | "copy", source?: CreatorPrompt): PromptDraft {
    return {
      kind: "prompt",
      mode,
      ...(source === undefined ? {} : { sourceName: source.name }),
      category,
      name: source === undefined ? "" : `${source.name} 副本`,
      systemPrompt: source?.system_prompt ?? "",
      userPrompt: source?.user_prompt ?? "",
    };
  }

  function startCreate(): void {
    if (pendingLock.current || verification !== null || !confirmDiscard()) return;
    editorGeneration.current += 1;
    setModelDraftGeneration((generation) => generation + 1);
    setDeleteTarget(null);
    setVerification(null);
    setNotice(null);
    setOrderDraft(null);
    setOrderConflict(false);
    setDraft(tab === "models"
      ? makeModelDraft(modelCategory, "create")
      : makePromptDraft(promptCategory, "create"));
  }

  function startCopy(item: CreatorModel | CreatorPrompt): void {
    if (pendingLock.current || verification !== null || !confirmDiscard()) return;
    editorGeneration.current += 1;
    setModelDraftGeneration((generation) => generation + 1);
    setDeleteTarget(null);
    setVerification(null);
    setNotice(null);
    setOrderDraft(null);
    setOrderConflict(false);
    if ("provider" in item) {
      setTab("models");
      setModelCategory(item.category);
      setDraft(makeModelDraft(item.category, "copy", item));
    } else {
      setTab("prompts");
      setPromptCategory(item.category);
      setDraft(makePromptDraft(item.category, "copy", item));
    }
  }

  async function startEdit(item: CreatorModel | CreatorPrompt): Promise<void> {
    if (pendingLock.current || verification !== null || !confirmDiscard()) return;
    if ("provider" in item ? !canManageModel(item, userId) : !canManagePrompt(item, userId)) {
      setNotice("系统配置或其他账号的配置为只读内容。");
      return;
    }
    editorGeneration.current += 1;
    setModelDraftGeneration((generation) => generation + 1);
    invalidateCatalog("provider" in item ? "models" : "prompts");
    const request = beginRequest(true);
    setPending(true);
    pendingLock.current = true;
    setNotice(null);
    try {
      const currentService = propsRef.current.service;
      if (currentService === undefined) throw new Error("creator settings service unavailable");
      if ("provider" in item) {
        const fresh = await currentService.getModel(item.id, request.signal);
        if (!request.isCurrent()) return;
        if (fresh.id !== item.id || fresh.category !== item.category || !canManageModel(fresh, userId)) {
          setNotice("模型归属或分类已变化，刷新列表后再试。");
          return;
        }
        setTab("models");
        setModelCategory(fresh.category);
        setDraft({
          kind: "model",
          mode: "edit",
          id: fresh.id,
          category: fresh.category,
          name: fresh.name,
          provider: fresh.provider,
          apiKey: "",
          model: fresh.model ?? "",
          baseUrl: fresh.base_url ?? "",
          params: fresh.params ?? "",
          original: fresh,
        });
      } else {
        const fresh = await currentService.getPrompt(item.id, request.signal);
        if (!request.isCurrent()) return;
        if (fresh.id !== item.id || fresh.category !== item.category || fresh.user_id !== userId || fresh.is_system === true) {
          setNotice("提示词归属或分类已变化，刷新列表后再试。");
          return;
        }
        const freshList = await currentService.listPrompts(fresh.category, request.signal);
        if (!request.isCurrent()) return;
        const listedPrompt = freshList.find((candidate) => candidate.id === item.id);
        if (listedPrompt === undefined || !canManagePrompt(listedPrompt, userId)) {
          setNotice("提示词归属或分类已变化，刷新列表后再试。");
          return;
        }
        storeList("prompts", freshList, fresh.category);
        setTab("prompts");
        setPromptCategory(fresh.category);
        setDraft({
          kind: "prompt",
          mode: "edit",
          id: fresh.id,
          category: fresh.category,
          name: fresh.name,
          systemPrompt: fresh.system_prompt ?? "",
          userPrompt: fresh.user_prompt ?? "",
          original: fresh,
        });
      }
      setOrderDraft(null);
      setDeleteTarget(null);
    } catch (error) {
      handleError(error, request, "打开配置");
    } finally {
      request.finish();
      if (request.isCurrent()) {
        setPending(false);
        pendingLock.current = false;
      }
    }
  }

  function beginOrderDraft(nextTab: SettingsTab, category: string, ids: string[]): void {
    setOrderDraft((current) => {
      if (current?.tab === nextTab && current.category === category) return current;
      setOrderConflict(false);
      return { tab: nextTab, category, baseline: [...ids], ids: [...ids] };
    });
  }

  function moveItem(id: string, direction: -1 | 1): void {
    const ids = visibleIds;
    const index = ids.indexOf(id);
    const target = index + direction;
    if (index < 0 || target < 0 || target >= ids.length) return;
    beginOrderDraft(tab, currentCategory, ids);
    setOrderDraft((current) => {
      if (current === null || current.tab !== tab || current.category !== currentCategory) return current;
      const next = [...current.ids];
      const currentIndex = next.indexOf(id);
      const nextIndex = currentIndex + direction;
      if (currentIndex < 0 || nextIndex < 0 || nextIndex >= next.length) return current;
      const currentId = next[currentIndex];
      const targetId = next[nextIndex];
      if (currentId === undefined || targetId === undefined) return current;
      [next[currentIndex], next[nextIndex]] = [targetId, currentId];
      return { ...current, ids: next };
    });
  }

  function moveToDefault(id: string): void {
    const ids = visibleIds;
    const index = ids.indexOf(id);
    if (index <= 0) return;
    beginOrderDraft(tab, currentCategory, ids);
    setOrderDraft((current) => {
      if (current === null || current.tab !== tab || current.category !== currentCategory) return current;
      const next = [...current.ids];
      const actualIndex = next.indexOf(id);
      if (actualIndex <= 0) return current;
      next.splice(actualIndex, 1);
      next.unshift(id);
      return { ...current, ids: next };
    });
  }

  function entityExpected(
    draftValue: SettingsDraft,
    modelPatch: CreatorModelUpdateInput | null,
    promptPatch: CreatorPromptUpdateInput | null,
  ): Record<string, unknown> {
    if (draftValue.kind === "model") {
      const expected: Record<string, unknown> = {
        category: draftValue.category,
        name: draftValue.name.trim(),
      };
      if (draftValue.mode === "edit" && draftValue.original !== undefined) {
        expected.provider = modelPatch !== null && "provider" in modelPatch
          ? modelPatch.provider
          : draftValue.original.provider;
        expected.model = modelPatch !== null && "model" in modelPatch
          ? modelPatch.model ?? null
          : draftValue.original.model ?? null;
        expected.base_url = modelPatch !== null && "base_url" in modelPatch
          ? modelPatch.base_url ?? null
          : draftValue.original.base_url ?? null;
        expected.params = modelPatch !== null && "params" in modelPatch
          ? modelPatch.params ?? null
          : draftValue.original.params;
      } else {
        expected.provider = draftValue.provider.trim();
        expected.model = optionalValue(draftValue.model);
        expected.base_url = optionalValue(draftValue.baseUrl);
        expected.params = draftValue.mode === "copy" && draftValue.params === (draftValue.paramsBaseline ?? "")
          ? (draftValue.paramsBaseline === "" ? null : draftValue.paramsBaseline ?? null)
          : draftValue.params.trim() === "" ? null : draftValue.params;
      }
      return expected;
    }
    const expected: Record<string, unknown> = {
      category: draftValue.category,
      name: draftValue.name.trim(),
    };
    if (draftValue.mode === "edit" && draftValue.original !== undefined) {
      expected.system_prompt = promptPatch !== null && "system_prompt" in promptPatch
        ? promptPatch.system_prompt ?? null
        : draftValue.original.system_prompt ?? null;
      expected.user_prompt = promptPatch !== null && "user_prompt" in promptPatch
        ? promptPatch.user_prompt ?? null
        : draftValue.original.user_prompt ?? null;
    } else {
      expected.system_prompt = draftValue.systemPrompt;
      expected.user_prompt = draftValue.userPrompt;
    }
    return expected;
  }

  function mergeMutationReceipt(
    tabForEntity: SettingsTab,
    expected: Record<string, unknown>,
    receipt: CreatorModel | CreatorPrompt,
  ): Record<string, unknown> {
    const merged = { ...expected };
    if (tabForEntity === "models" && "provider" in receipt) {
      // The model API fills provider defaults on create and update, so use its
      // non-secret receipt for params before checking the independent GETs.
      merged.params = receipt.params;
    }
    return merged;
  }

  function matchesExpected(
    tabForEntity: SettingsTab,
    entity: CreatorModel | CreatorPrompt,
    expected: Record<string, unknown>,
    listedOwnership = false,
  ): boolean {
    if (entity.category !== expected.category || entity.name !== expected.name || entity.user_id !== userId) return false;
    if (tabForEntity === "models") {
      const model = entity as CreatorModel;
      if (!canManageModel(model, userId)) return false;
      for (const key of ["provider", "model", "base_url", "params"] as const) {
        if (key in expected && !sameValue(model[key] ?? null, expected[key])) return false;
      }
      return true;
    }
    const prompt = entity as CreatorPrompt;
    if (prompt.is_system === true || (prompt.is_system !== false && !listedOwnership)) return false;
    for (const key of ["system_prompt", "user_prompt"] as const) {
      if (key in expected && !sameValue(prompt[key] ?? null, expected[key])) return false;
    }
    return true;
  }

  async function verifyState(target: Verification, request: RequestHandle): Promise<boolean> {
    const currentService = propsRef.current.service;
    if (currentService === undefined) return false;
    if (target.kind === "model-conversion") {
      if (target.actorId !== userId || target.service !== currentService || target.category !== modelCategory) return false;
      const rows = await currentService.listModels(target.category, request.signal);
      if (!request.isCurrent()) return false;
      storeList("models", rows, target.category);
      const sourceStillExists = rows.some((item) => item.id === target.sourceId);
      if (target.newId === null) return false;
      const replacement = rows.find((item) => item.id === target.newId);
      const shouldBeSystem = target.target === "system";
      const expectedOwner = shouldBeSystem ? "system" : target.actorId;
      return !sourceStillExists
        && replacement !== undefined
        && replacement.category === target.category
        && replacement.is_system === shouldBeSystem
        && replacement.user_id === expectedOwner;
    }
    if (target.kind === "create-unknown") {
      const rows = await getList(target.tab, request.signal);
      if (!request.isCurrent()) return false;
      storeList(target.tab, rows);
      const matches = target.tab === "models"
        ? (rows as CreatorModel[]).filter((item) => item.category === target.category && item.name === target.name && item.user_id === userId && item.is_system === false)
        : (rows as CreatorPrompt[]).filter((item) => item.category === target.category && item.name === target.name && item.user_id === userId && item.is_system === false);
      setNotice(matches.length === 0
        ? "已刷新列表，但创建结果仍无法确认；请按列表检查后再决定是否开始新的草稿。"
        : `已刷新列表，找到 ${matches.length} 条同名个人配置；由于没有本次创建的记录 ID，结果仍待你核对。`);
      return false;
    }
    if (target.kind === "delete") {
      const rows = await getList(target.tab, request.signal);
      if (!request.isCurrent()) return false;
      storeList(target.tab, rows);
      const exists = rows.some((item) => item.id === target.id);
      return !exists;
    }
    if (target.kind === "order") {
      const rows = await getList(target.tab, request.signal, target.category);
      if (!request.isCurrent()) return false;
      storeList(target.tab, rows, target.category);
      const actualIds = target.tab === "models"
        ? rowsInCurrentScope("models", rows as CreatorModel[], userId).map((item) => item.id)
        : rowsInCurrentScope("prompts", rows as CreatorPrompt[], userId).map((item) => item.id);
      return sameValue(actualIds, target.ids);
    }
    const entity = target.tab === "models"
      ? await currentService.getModel(target.id, request.signal)
      : await currentService.getPrompt(target.id, request.signal);
    if (!request.isCurrent()) return false;
    const rows = await getList(target.tab, request.signal);
    if (!request.isCurrent()) return false;
    storeList(target.tab, rows);
    const listed = rows.find((item) => item.id === target.id);
    return entity.id === target.id
      && listed !== undefined
      && matchesExpected(target.tab, entity, target.expected, target.tab === "prompts")
      && matchesExpected(target.tab, listed, target.expected);
  }

  async function runVerification(target = verification, allowBusy = false): Promise<void> {
    if (target === null || target === undefined || (pendingLock.current && !allowBusy)) return;
    invalidateCatalog(target.tab);
    const request = beginRequest();
    const isUncertain = target.kind === "create-unknown" || target.uncertain;
    setPending(true);
    pendingLock.current = true;
    setNotice(isUncertain ? "正在只读核对上次操作的状态…" : "正在读取服务器保存结果…");
    try {
      const confirmed = await verifyState(target, request);
      if (!request.isCurrent()) return;
      if (confirmed && target.kind === "entity" && target.mutationUnknown && target.secretChanged) {
        setVerification(target);
        setNotice("非密钥配置已回读，但密钥变更结果无法通过只读信息确认；不会自动重试。请结束核对后再决定是否更换。");
        return;
      }
      if (confirmed) {
        setVerification(null);
        setNotice(target.kind === "order"
          ? "分类顺序已回读确认，第一项现在是此分类默认项。"
          : target.kind === "delete"
            ? "删除结果已回读确认。"
            : target.kind === "model-conversion"
              ? target.target === "system"
                ? "个人模型已按新 ID 转为系统模型，原记录已移除并回读确认。"
                : "系统模型已按新 ID 转为当前个人模型，原记录已移除并回读确认。"
            : target.kind === "entity" && target.secretChanged
              ? "模型非密钥配置已回读确认；密钥变更请求已返回成功，未读取或显示密钥值。"
              : "配置已回读确认。 ");
        if (target.kind === "entity" && draft?.id === target.id) closeDraft();
        setOrderDraft(null);
        setOrderConflict(false);
        return;
      }
      if (target.kind === "create-unknown") return;
      setNotice(target.kind === "model-conversion"
        ? target.newId === null
          ? "已只读刷新模型目录，但无法从未知回执确认新 ID；转换仍可能已生效，不会自动重试。可继续刷新或停止本地核对。"
          : "转换回执中的新模型或原记录状态尚未在目录中匹配；只会只读核对，不会自动重试。"
        : isUncertain
          ? "只读回查尚未匹配目标状态；已保留核对信息，请刷新后人工判断。"
          : "写入已返回成功，但回读结果不一致；请刷新核对，页面不会自动重复写入。");
      setVerification({ ...target, uncertain: true });
    } catch (error) {
      if (!request.isCurrent()) return;
      if (isCurrentUnauthorized(error)) {
        propsRef.current.onUnauthorized();
        return;
      }
      setNotice(target.kind === "model-conversion"
        ? "模型目录只读核对失败；转换状态仍未确认，不会自动重试。可稍后刷新或停止本地核对。"
        : target.kind === "entity" && target.mutationUnknown && target.secretChanged
          ? "密钥变更结果无法通过只读信息确认；不会自动重试，请稍后再次只读核对或结束核对。"
          : isUncertain
            ? safeFailureMessage(error, "只读核对")
            : "写入已返回成功，但刷新失败；保存结果待完成，请只读重试。 ");
      if (target.kind !== "create-unknown") setVerification({ ...target, uncertain: true });
    } finally {
      request.finish();
      if (request.isCurrent()) {
        setPending(false);
        pendingLock.current = false;
      }
    }
  }

  async function convertModel(item: CreatorModel, target: ModelConversionTarget): Promise<void> {
    const actorId = userId;
    const category = modelCategory;
    const sourceId = item.id;
    const currentService = propsRef.current.service;
    const convert = target === "system"
      ? currentService?.promoteModelToSystem
      : currentService?.demoteModelToPersonal;
    const eligibleSource = target === "system"
      ? canManageModel(item, actorId)
      : item.is_system === true;
    if (!isSuperuser || currentService === undefined || convert === undefined || !eligibleSource
      || item.category !== category || pendingLock.current || verification !== null) return;
    if (!confirmDiscard()) return;

    const modelName = item.name.trim() || "未命名模型";
    const title = target === "system" ? `将“${modelName}”设为系统模型？` : `将“${modelName}”转为个人模型？`;
    const description = target === "system"
      ? "此操作会删除原个人模型，并创建一个新 ID 的系统模型；所有用户都能看到它，原排序引用会移除。"
      : "此操作会从系统目录删除原模型，并创建一个新 ID 的当前管理员个人模型；其他用户将不再看到它，原排序引用会移除。";
    if (!window.confirm(`${title}\n\n${description}\n\n确定继续吗？`)) return;

    discardDrafts();
    invalidateCatalog("models");
    const request = beginRequest();
    const attempt = ++conversionGeneration.current;
    const isCurrent = () => request.isCurrent()
      && conversionGeneration.current === attempt
      && propsRef.current.userId === actorId
      && propsRef.current.service === currentService
      && modelCategoryRef.current === category
      && propsRef.current.isSuperuser === true;
    setPending(true);
    pendingLock.current = true;
    setNotice(null);

    const check: Verification = {
      kind: "model-conversion",
      tab: "models",
      actorId,
      category,
      sourceId,
      newId: null,
      target,
      service: currentService,
      uncertain: true,
    };
    try {
      const rawReceipt = await convert.call(currentService, sourceId, request.signal);
      if (!isCurrent()) return;
      const receipt = parseCreatorModel(rawReceipt);
      const expectedSystem = target === "system";
      const expectedOwner = expectedSystem ? "system" : actorId;
      if (receipt.id === sourceId || receipt.category !== category
        || receipt.is_system !== expectedSystem || receipt.user_id !== expectedOwner) {
        throw new Error("model conversion receipt did not match the requested identity");
      }
      const confirmedTarget: Verification = { ...check, newId: receipt.id, uncertain: false };
      setVerification(confirmedTarget);
      setNotice("转换请求已返回，正在只读核对原记录和新记录…");
      request.finish();
      await runVerification(confirmedTarget, true);
    } catch (error) {
      if (!isCurrent()) return;
      if (isCurrentUnauthorized(error)) {
        propsRef.current.onUnauthorized();
        return;
      }
      if (isDefinitiveModelConversionFailure(error)) {
        setNotice(modelConversionFailureMessage(error, target));
        return;
      }
      setVerification(check);
      setNotice("转换结果未知；不会自动重试，正在只读刷新模型目录。核对无法确认新 ID 时，可停止本地核对后再操作。");
      request.finish();
      await runVerification(check, true);
    } finally {
      request.finish();
      if (request.isCurrent()) {
        setPending(false);
        pendingLock.current = false;
      }
    }
  }

  async function saveDraft(): Promise<void> {
    if (pendingLock.current || draft === null || verification !== null) return;
    const currentService = propsRef.current.service;
    if (currentService === undefined) {
      setNotice("当前运行配置尚未接入创作者设置服务。");
      return;
    }
    if (draft.kind === "model" && (draft.name.trim() === "" || draft.provider.trim() === "")) {
      setNotice("请填写模型名称和供应商。 ");
      return;
    }
    if (draft.kind === "prompt" && draft.name.trim() === "") {
      setNotice("请填写提示词名称。 ");
      return;
    }

    let modelPatch: CreatorModelUpdateInput | null = null;
    let promptPatch: CreatorPromptUpdateInput | null = null;
    if (draft.kind === "model") {
      const parsedParams = validateModelParams(draft.params);
      const paramsUnchanged = draft.mode === "edit"
        ? draft.params === (draft.original?.params ?? "")
        : draft.mode === "copy" && draft.params === (draft.paramsBaseline ?? "");
      if (!paramsUnchanged) {
        if (draft.params.trim() !== "" && parsedParams.error !== null) {
          setNotice(parsedParams.error);
          return;
        }
      }
      if (draft.mode === "edit" && draft.original !== undefined) {
        const patch: CreatorModelUpdateInput = {};
        const original = draft.original;
        if (draft.name.trim() !== original.name) patch.name = draft.name.trim();
        if (draft.provider.trim() !== original.provider) patch.provider = draft.provider.trim();
        if (draft.model !== (original.model ?? "")) patch.model = optionalValue(draft.model);
        if (draft.baseUrl !== (original.base_url ?? "")) patch.base_url = optionalValue(draft.baseUrl);
        if (draft.params !== (original.params ?? "")) patch.params = parsedParams.value;
        if (draft.apiKey.trim() !== "") patch.api_key = draft.apiKey;
        if (Object.keys(patch).length === 0) {
          setNotice("没有检测到改动，未发送保存请求。");
          closeDraft();
          return;
        }
        modelPatch = patch;
      }
    } else if (draft.mode === "edit" && draft.original !== undefined) {
      const patch: CreatorPromptUpdateInput = {};
      if (draft.name.trim() !== draft.original.name) patch.name = draft.name.trim();
      if (draft.systemPrompt !== (draft.original.system_prompt ?? "")) patch.system_prompt = draft.systemPrompt;
      if (draft.userPrompt !== (draft.original.user_prompt ?? "")) patch.user_prompt = draft.userPrompt;
      if (Object.keys(patch).length === 0) {
        setNotice("没有检测到改动，未发送保存请求。");
        closeDraft();
        return;
      }
      promptPatch = patch;
    }

    const request = beginRequest(true);
    invalidateCatalog(tab);
    setPending(true);
    pendingLock.current = true;
    setNotice(null);
    const operation = draft.mode === "edit" ? "update" : "create";
    let expected = entityExpected(draft, modelPatch, promptPatch);
    const secretChanged = draft.kind === "model" && draft.apiKey.trim() !== "";
    let responseId: string | null = null;
    try {
      if (draft.kind === "model") {
        if (draft.mode === "edit") {
          if (draft.id === undefined || modelPatch === null) return;
          const response = await currentService.updateModel(draft.id, modelPatch, request.signal);
          responseId = response.id;
          expected = mergeMutationReceipt("models", expected, response);
        } else {
          if (draft.apiKey.trim() === "") {
            setNotice("新建或复制模型需要输入你自己的 API 密钥。 ");
            return;
          }
          const input: CreatorModelCreateInput = {
            category: draft.category,
            name: draft.name.trim(),
            provider: draft.provider.trim(),
            api_key: draft.apiKey,
            model: optionalValue(draft.model),
            base_url: optionalValue(draft.baseUrl),
            params: draft.mode === "copy" && draft.params === (draft.paramsBaseline ?? "")
              ? (draft.paramsBaseline === "" ? null : draft.paramsBaseline ?? null)
              : validateModelParams(draft.params).value,
            is_system: false,
          };
          const response = await currentService.createModel(input, request.signal);
          responseId = response.id;
          expected = mergeMutationReceipt("models", expected, response);
        }
      } else if (draft.mode === "edit") {
        if (draft.id === undefined || promptPatch === null) return;
        const response = await currentService.updatePrompt(draft.id, promptPatch, request.signal);
        responseId = response.id;
        expected = mergeMutationReceipt("prompts", expected, response);
      } else {
        const input: CreatorPromptCreateInput = {
          category: draft.category,
          name: draft.name.trim(),
          system_prompt: draft.systemPrompt,
          user_prompt: draft.userPrompt,
          is_system: false,
        };
        const response = await currentService.createPrompt(input, request.signal);
        responseId = response.id;
        expected = mergeMutationReceipt("prompts", expected, response);
      }
      if (!request.isCurrent()) return;
      if (responseId === null || responseId === "") {
        throw new Error("missing mutation identity");
      }
      closeDraft();
      const check: Verification = {
        kind: "entity",
        tab,
        id: responseId,
        category: draft.category,
        expected,
        uncertain: false,
        secretChanged,
      };
      setVerification(check);
      setNotice("保存请求已完成，正在回读核实…");
      request.finish();
      await runVerification(check, true);
    } catch (error) {
      if (!request.isCurrent()) return;
      if (isCurrentUnauthorized(error)) {
        propsRef.current.onUnauthorized();
        return;
      }
      if (operation === "create") {
        setVerification({
          kind: "create-unknown",
          tab,
          category: draft.category,
          name: draft.name.trim(),
        });
        setNotice("创建结果未知；不会自动重复提交。先只读刷新列表并核对，草稿仍保留。");
        const target: Verification = { kind: "create-unknown", tab, category: draft.category, name: draft.name.trim() };
        request.finish();
        await runVerification(target, true);
      } else if (draft.id !== undefined) {
        const target: Verification = {
          kind: "entity",
          tab,
          id: draft.id,
          category: draft.category,
          expected,
          uncertain: true,
          secretChanged,
          mutationUnknown: true,
        };
        setVerification(target);
        setNotice("更新结果未知；不会自动重复提交。正在只读核对，草稿仍保留。");
        request.finish();
        await runVerification(target, true);
      } else {
        handleError(error, request, "保存配置");
      }
    } finally {
      request.finish();
      if (request.isCurrent()) {
        setPending(false);
        pendingLock.current = false;
      }
    }
  }

  async function saveOrder(): Promise<void> {
    const current = orderDraft;
    const currentService = propsRef.current.service;
    if (current === null || currentService === undefined || pendingLock.current || orderConflict
      || sameValue(current.ids, current.baseline)) return;
    const request = beginRequest();
    invalidateCatalog(current.tab);
    setPending(true);
    pendingLock.current = true;
    setNotice(null);
    try {
      const fresh = await getList(current.tab, request.signal, current.category);
      if (!request.isCurrent()) return;
      const freshIds = current.tab === "models"
        ? rowsInCurrentScope("models", fresh as CreatorModel[], userId).map((item) => item.id)
        : rowsInCurrentScope("prompts", fresh as CreatorPrompt[], userId).map((item) => item.id);
      if (!sameValue(freshIds, current.baseline)) {
        storeList(current.tab, fresh, current.category);
        setNotice("此分类的配置或顺序已变化；保留当前排序草稿，请基于刷新后的列表重新排序。");
        setOrderConflict(true);
        return;
      }
      if (current.tab === "models") {
        await currentService.reorderModels({ category: current.category, model_ids: current.ids }, request.signal);
      } else {
        await currentService.reorderPrompts({ category: current.category, prompt_ids: current.ids }, request.signal);
      }
      if (!request.isCurrent()) return;
      const check: Verification = {
        kind: "order",
        tab: current.tab,
        category: current.category,
        ids: current.ids,
        uncertain: false,
      };
      setVerification(check);
      setNotice("排序请求已完成，正在回读核实…");
      request.finish();
      await runVerification(check, true);
    } catch (error) {
      if (!request.isCurrent()) return;
      if (isCurrentUnauthorized(error)) {
        propsRef.current.onUnauthorized();
        return;
      }
      setVerification({ ...current, kind: "order", uncertain: true });
      setNotice("排序结果未知；不会自动重复提交，正在只读核对。");
      request.finish();
      await runVerification({ kind: "order", tab: current.tab, category: current.category, ids: current.ids, uncertain: true }, true);
    } finally {
      request.finish();
      if (request.isCurrent()) {
        setPending(false);
        pendingLock.current = false;
      }
    }
  }

  async function removeTarget(): Promise<void> {
    if (deleteTarget === null || pendingLock.current) return;
    const target = deleteTarget;
    const request = beginRequest();
    invalidateCatalog(target.tab);
    setPending(true);
    pendingLock.current = true;
    setNotice(null);
    try {
      const currentService = propsRef.current.service;
      if (currentService === undefined) throw new Error("creator settings service unavailable");
      if (target.tab === "models") await currentService.deleteModel(target.id, request.signal);
      else await currentService.deletePrompt(target.id, request.signal);
      if (!request.isCurrent()) return;
      setDeleteTarget(null);
      const check: Verification = { kind: "delete", tab: target.tab, id: target.id, category: currentCategory, uncertain: false };
      setVerification(check);
      setNotice("删除请求已完成，正在回读核实…");
      request.finish();
      await runVerification(check, true);
    } catch (error) {
      if (!request.isCurrent()) return;
      if (isCurrentUnauthorized(error)) {
        propsRef.current.onUnauthorized();
        return;
      }
      const check: Verification = { kind: "delete", tab: target.tab, id: target.id, category: currentCategory, uncertain: true };
      setVerification(check);
      setNotice("删除结果未知；不会自动重复提交，正在只读核对。");
      request.finish();
      await runVerification(check, true);
    } finally {
      request.finish();
      if (request.isCurrent()) {
        setPending(false);
        pendingLock.current = false;
      }
    }
  }

  function requestBack(): void {
    if (pending || verification !== null) {
      if (!window.confirm("当前有操作正在保存或只读核对，离开后将中断页面核对。继续吗？")) return;
    } else if (!confirmDiscard()) {
      return;
    }
    discardDrafts();
    onBack();
  }

  const canUseService = service !== undefined;
  const activeDraft = draft !== null && (draft.kind === "model" ? tab === "models" : tab === "prompts");

  return (
    <section className="creator-settings-page" aria-labelledby="creator-settings-title">
      <div className="creator-settings-header">
        <div>
          <p className="creator-settings-eyebrow">个人配置</p>
          <h1 id="creator-settings-title">创作者设置</h1>
          <p className="creator-settings-description">管理本人使用的模型和提示词。系统配置在此只读，调整顺序即可设置该分类的默认项。</p>
        </div>
        <button className="creator-settings-back" onClick={requestBack} type="button">返回工作空间</button>
      </div>

      <div className="creator-settings-tabs" role="tablist" aria-label="配置类型">
        <button aria-selected={tab === "models"} className={tab === "models" ? "selected" : ""} disabled={pending || verification !== null} onClick={() => changeTab("models")} onKeyDown={(event) => handleTabKeyDown(event, "models")} ref={(element) => { tabButtons.current.models = element; }} role="tab" tabIndex={tab === "models" ? 0 : -1} type="button">模型</button>
        <button aria-selected={tab === "prompts"} className={tab === "prompts" ? "selected" : ""} disabled={pending || verification !== null} onClick={() => changeTab("prompts")} onKeyDown={(event) => handleTabKeyDown(event, "prompts")} ref={(element) => { tabButtons.current.prompts = element; }} role="tab" tabIndex={tab === "prompts" ? 0 : -1} type="button">提示词</button>
      </div>

      {!canUseService && <p className="creator-settings-message" role="status">当前运行配置尚未接入创作者设置服务。</p>}
      {notice !== null && <p className="creator-settings-message" role="status">{notice}</p>}
      {listError !== null && <p className="creator-settings-error" role="alert">{listError}</p>}

      {verification !== null && (
        <div className="creator-settings-verification" role="status">
          <span>{verification.kind === "model-conversion"
            ? "模型转换仍需只读核对；系统不会自动重复转换。"
            : "有一项操作需要只读核对。"}</span>
          <button disabled={pending || !canUseService} onClick={() => void runVerification()} type="button">
            {pending ? "正在核对…" : "刷新核实"}
          </button>
          <button
            disabled={pending}
            onClick={() => {
              const isModelConversion = verification.kind === "model-conversion";
              const confirmation = isModelConversion
                ? "这只会停止本地核对，不会撤销服务端转换；模型可能已经转换成功。请在刷新列表后再决定下一步。继续吗？"
                : "结束本次核对后不会自动重试写入；创建结果未知时会放弃当前草稿。继续吗？";
              if (!window.confirm(confirmation)) return;
              if (verification.kind === "create-unknown") closeDraft();
              setVerification(null);
              setNotice(isModelConversion
                ? "已停止本地核对；服务端转换可能已生效，请先刷新列表并确认状态。"
                : "已结束本次核对；如需继续，请先检查当前列表状态。");
            }}
            type="button"
          >{verification.kind === "model-conversion" ? "停止本地核对" : "结束核对"}</button>
          {verification.kind === "create-unknown" && <small>同名项目无法单独证明是哪次创建，请核对列表记录。</small>}
          {verification.kind === "model-conversion" && (
            <small>转换会删除原 ID 并创建新 ID；无可信回执时不会按名称或供应商猜测新记录。</small>
          )}
        </div>
      )}

      <div className="creator-settings-toolbar">
        <label>
          <span>{tab === "models" ? "模型分类" : "提示词分类"}</span>
          <select aria-label={tab === "models" ? "模型分类" : "提示词分类"} disabled={pending || verification !== null} onChange={(event) => changeCategory(event.target.value)} value={currentCategory}>
            {(tab === "models" ? modelCategories : promptCategories).map((item) => (
              <option key={item.value} value={item.value}>{item.label}</option>
            ))}
          </select>
        </label>
        <div className="creator-settings-toolbar-actions">
          {isSuperuser && tab === "models" && (
            <button
              aria-controls="creator-provider-quota-panel"
              aria-expanded={providerQuotaOpen}
              onClick={() => setProviderQuotaOpen((open) => !open)}
              type="button"
            >
              {providerQuotaOpen ? "收起额度监控" : "供应商额度监控"}
            </button>
          )}
          <button className="creator-settings-primary" disabled={!canUseService || pending || verification !== null} onClick={startCreate} type="button">
            新建{tab === "models" ? "模型" : "提示词"}
          </button>
          <button disabled={pending || verification !== null} onClick={() => void refreshList(tab)} type="button">刷新列表</button>
        </div>
      </div>

      {providerQuotaOpen && isSuperuser && tab === "models" && (
        <ProviderQuotaPanel
          actorId={userId}
          isSuperuser={isSuperuser}
          onClose={() => setProviderQuotaOpen(false)}
          onUnauthorized={onUnauthorized}
          service={providerQuotaService}
        />
      )}

      <div className="creator-settings-content">
        <div className="creator-settings-list" aria-label={tab === "models" ? "模型列表" : "提示词列表"}>
          {loading && <p className="creator-settings-empty" role="status">正在读取配置…</p>}
          {!loading && tab === "models" && currentModels.length === 0 && <p className="creator-settings-empty">此分类还没有配置。</p>}
          {!loading && tab === "prompts" && currentPrompts.length === 0 && <p className="creator-settings-empty">此分类还没有提示词。</p>}
          {tab === "models" && orderedModels.map((item, index) => (
            <article className={`creator-settings-card${item.is_system ? " system" : ""}`} key={item.id}>
              <div className="creator-settings-card-main">
                <strong>{item.name}</strong>
                <span>{item.is_system ? "系统只读" : canManageModel(item, userId) ? "个人配置" : "其他账号"}</span>
                <small>{item.provider}{item.model ? ` · ${item.model}` : ""}{index === 0 ? " · 默认" : ""}</small>
              </div>
              <div className="creator-settings-card-actions">
                <button aria-label={`将${item.name}设为默认`} disabled={index === 0 || pending || verification !== null} onClick={() => moveToDefault(item.id)} type="button">设为默认</button>
                <button aria-label={`${item.name}上移`} disabled={index === 0 || pending || verification !== null} onClick={() => moveItem(item.id, -1)} type="button">上移</button>
                <button aria-label={`${item.name}下移`} disabled={index === orderedModels.length - 1 || pending || verification !== null} onClick={() => moveItem(item.id, 1)} type="button">下移</button>
                <button disabled={pending || verification !== null || !canManageModel(item, userId)} onClick={() => void startEdit(item)} type="button">编辑</button>
                <button disabled={pending || verification !== null || !canUseService} onClick={() => startCopy(item)} type="button">复制</button>
                <button disabled={pending || verification !== null || !canManageModel(item, userId)} onClick={() => setDeleteTarget({ tab: "models", id: item.id, name: item.name })} type="button">删除</button>
                {isSuperuser && canManageModel(item, userId) && (
                  <button
                    disabled={pending || verification !== null || service?.promoteModelToSystem === undefined}
                    onClick={() => void convertModel(item, "system")}
                    type="button"
                  >设为系统</button>
                )}
                {isSuperuser && item.is_system && (
                  <button
                    disabled={pending || verification !== null || service?.demoteModelToPersonal === undefined}
                    onClick={() => void convertModel(item, "personal")}
                    type="button"
                  >转为个人</button>
                )}
              </div>
              {deleteTarget?.tab === "models" && deleteTarget.id === item.id && (
                <div className="creator-settings-inline-confirm" role="group" aria-label={`确认删除${item.name}`}>
                  <span>删除后无法恢复此个人配置。</span>
                  <button disabled={pending} onClick={() => void removeTarget()} type="button">确认删除</button>
                  <button disabled={pending} onClick={() => setDeleteTarget(null)} type="button">取消</button>
                </div>
              )}
            </article>
          ))}
          {tab === "prompts" && orderedPrompts.map((item, index) => (
            <article className={`creator-settings-card${item.is_system ? " system" : ""}`} key={item.id}>
              <div className="creator-settings-card-main">
                <strong>{item.name}</strong>
                <span>{item.is_system ? "系统只读" : canManagePrompt(item, userId) ? "个人配置" : "其他账号"}</span>
                <small>{categoryName(item.category, PROMPT_CATEGORIES)}{index === 0 ? " · 默认" : ""}</small>
              </div>
              <div className="creator-settings-card-actions">
                <button aria-label={`将${item.name}设为默认`} disabled={index === 0 || pending || verification !== null} onClick={() => moveToDefault(item.id)} type="button">设为默认</button>
                <button aria-label={`${item.name}上移`} disabled={index === 0 || pending || verification !== null} onClick={() => moveItem(item.id, -1)} type="button">上移</button>
                <button aria-label={`${item.name}下移`} disabled={index === orderedPrompts.length - 1 || pending || verification !== null} onClick={() => moveItem(item.id, 1)} type="button">下移</button>
                <button disabled={pending || verification !== null || !canManagePrompt(item, userId)} onClick={() => void startEdit(item)} type="button">编辑</button>
                <button disabled={pending || verification !== null || !canUseService} onClick={() => startCopy(item)} type="button">复制</button>
                <button disabled={pending || verification !== null || !canManagePrompt(item, userId)} onClick={() => setDeleteTarget({ tab: "prompts", id: item.id, name: item.name })} type="button">删除</button>
              </div>
              {deleteTarget?.tab === "prompts" && deleteTarget.id === item.id && (
                <div className="creator-settings-inline-confirm" role="group" aria-label={`确认删除${item.name}`}>
                  <span>删除后无法恢复此个人配置。</span>
                  <button disabled={pending} onClick={() => void removeTarget()} type="button">确认删除</button>
                  <button disabled={pending} onClick={() => setDeleteTarget(null)} type="button">取消</button>
                </div>
              )}
            </article>
          ))}
        </div>

        {orderDraft !== null && orderDraft.tab === tab && orderDraft.category === currentCategory && (
          <div className="creator-settings-order-actions">
            <span>{orderConflict ? "服务器列表已变化，排序草稿已保留；请放弃后按新列表重新排序。" : "默认项为当前分类排序第一项；系统条目也可以作为你的默认项。"}</span>
            <button disabled={pending || verification !== null || orderConflict || sameValue(orderDraft.ids, orderDraft.baseline)} onClick={() => void saveOrder()} type="button">保存排序</button>
            <button disabled={pending} onClick={() => { setOrderDraft(null); setOrderConflict(false); }} type="button">放弃排序</button>
          </div>
        )}
      </div>

      {activeDraft && draft !== null && (
        <section className="creator-settings-editor" aria-labelledby="creator-settings-editor-title">
          <div className="creator-settings-editor-heading">
            <div>
              <p className="creator-settings-eyebrow">{draft.mode === "edit" ? "编辑个人配置" : draft.mode === "copy" ? "复制为新个人草稿" : "新建个人配置"}</p>
              <h2 id="creator-settings-editor-title">{draft.kind === "model" ? "模型配置" : "提示词配置"}</h2>
            </div>
            <button disabled={pending} onClick={() => { if (confirmDiscard()) closeDraft(); }} type="button">放弃草稿</button>
          </div>
          {draft.sourceName !== undefined && <p className="creator-settings-hint">来源：{draft.sourceName}。副本会创建新记录，不会覆盖原配置。</p>}
          {draft.kind === "model" ? (
            <div className="creator-settings-fields">
              <label>
                <span>分类</span>
                <select disabled={pending || draft.mode === "edit"} onChange={(event) => updateModelDraft((current) => ({ ...current, category: event.target.value }))} value={draft.category}>
                  {modelCategories.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
                </select>
              </label>
              <label><span>名称</span><input autoComplete="off" disabled={pending} onChange={(event) => updateModelDraft((current) => ({ ...current, name: event.target.value }))} value={draft.name} /></label>
              <label><span>供应商</span><input autoComplete="off" disabled={pending} onChange={(event) => updateModelDraft((current) => ({ ...current, provider: event.target.value }))} value={draft.provider} /></label>
              <label><span>模型标识</span><input autoComplete="off" disabled={pending} onChange={(event) => updateModelDraft((current) => ({ ...current, model: event.target.value }))} value={draft.model} /></label>
              <label><span>服务地址</span><input autoComplete="off" disabled={pending} onChange={(event) => updateModelDraft((current) => ({ ...current, baseUrl: event.target.value }))} value={draft.baseUrl} /></label>
              <label className="creator-settings-field-wide">
                <span>模型参数（JSON 对象）</span>
                <textarea autoComplete="off" disabled={pending} onChange={(event) => updateModelDraft((current) => ({ ...current, params: event.target.value }))} rows={5} value={draft.params} />
              </label>
              <label className="creator-settings-field-wide">
                <span id="creator-settings-api-key-label">API 密钥</span>
                <input aria-describedby={draft.mode === "edit" ? "creator-settings-api-key-help" : undefined} aria-labelledby="creator-settings-api-key-label" autoComplete="new-password" disabled={pending} onChange={(event) => updateModelDraft((current) => ({ ...current, apiKey: event.target.value }))} placeholder={draft.mode === "edit" ? "留空保留已保存的密钥" : "输入你自己的 API 密钥"} type="password" value={draft.apiKey} />
                {draft.mode === "edit" && <small id="creator-settings-api-key-help">留空保留已保存的密钥；如需更换，请输入新密钥。</small>}
              </label>
              <ModelConnectionTest
                draftGeneration={modelDraftGeneration}
                input={{
                  provider: draft.provider,
                  api_key: draft.apiKey,
                  base_url: draft.baseUrl,
                  model: draft.model,
                  category: draft.category,
                } satisfies CreatorModelConnectionTestInput}
                onUnauthorized={onUnauthorized}
                ownerId={userId}
                service={service}
              />
              <p className="creator-settings-hint creator-settings-field-wide">平台积分显示为 0 仅表示平台积分；供应商是否收费取决于你与供应商的配置。</p>
            </div>
          ) : (
            <div className="creator-settings-fields">
              <label>
                <span>分类</span>
                <select disabled={pending || draft.mode === "edit"} onChange={(event) => setDraft((current) => current?.kind === "prompt" ? { ...current, category: event.target.value } : current)} value={draft.category}>
                  {promptCategories.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
                </select>
              </label>
              <label><span>名称</span><input autoComplete="off" disabled={pending} onChange={(event) => setDraft((current) => current?.kind === "prompt" ? { ...current, name: event.target.value } : current)} value={draft.name} /></label>
              <label className="creator-settings-field-wide"><span>系统提示词</span><textarea autoComplete="off" disabled={pending} onChange={(event) => setDraft((current) => current?.kind === "prompt" ? { ...current, systemPrompt: event.target.value } : current)} rows={6} value={draft.systemPrompt} /></label>
              <label className="creator-settings-field-wide"><span>用户提示词</span><textarea autoComplete="off" disabled={pending} onChange={(event) => setDraft((current) => current?.kind === "prompt" ? { ...current, userPrompt: event.target.value } : current)} rows={8} value={draft.userPrompt} /></label>
              <p className="creator-settings-hint creator-settings-field-wide">提示词内容按原文保存；风格与融合分类中的 JSON 文本不会被拆分或重写。</p>
            </div>
          )}
          <div className="creator-settings-editor-actions">
            <button className="creator-settings-primary" disabled={pending || verification !== null} onClick={() => void saveDraft()} type="button">{pending ? "处理中…" : "保存并回读"}</button>
            <button disabled={pending} onClick={() => { if (confirmDiscard()) closeDraft(); }} type="button">取消</button>
          </div>
        </section>
      )}
    </section>
  );
}
