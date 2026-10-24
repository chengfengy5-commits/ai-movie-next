import { useEffect, useMemo, useRef, useState } from "react";
import type {
  Chapter,
  Character,
  Prop,
  Scene,
  StoryboardAsset,
  StoryboardFrame,
} from "../../../shared/api/contracts";
import type { CreatorModel, CreatorPrompt } from "../../../shared/api/creatorSettings";
import { ApiError } from "../../../shared/api/errors";
import type { WorkspaceServices } from "../../../shared/api/services";
import {
  buildStoryboardAssetSaveInput,
  candidateName,
  candidatePayload,
  parseStoryboardAssetAppearances,
  parseStoryboardAssetCandidates,
  parseStoryboardAssetSavePlan,
  type StoryboardAssetAppearance,
  type StoryboardAssetCandidate,
  type StoryboardAssetKind,
  type StoryboardAssetPlanItem,
  type StoryboardAssetSaveInput,
  type StoryboardAssetSavePlan,
  type StoryboardAssetStreamEvent,
} from "../../../shared/api/storyboardAssetGeneration";
import { normalizeApiError } from "../../../shared/api/services";
import { stripFrameOptimizeShotMarkers } from "../frameOptimizeContent";
import { useSeriesDialogFocus } from "../../series/useSeriesDialogFocus";
import "./storyboardAssetExtraction.css";

export interface StoryboardAssetGenerationSnapshot {
  chapter: Chapter;
  assets: StoryboardAsset[];
  characters: Character[];
  scenes: Scene[];
  props: Prop[];
  models: CreatorModel[];
  prompts: CreatorPrompt[];
}

interface CandidateDraft {
  id: number;
  value: StoryboardAssetCandidate;
}

type WorkflowStep = "select" | "edit" | "review" | "unknown";

interface StoryboardAssetExtractionDialogProps {
  services: Pick<WorkspaceServices, "storyboardAssetGeneration">;
  snapshot: StoryboardAssetGenerationSnapshot;
  userId: string;
  seriesId: string;
  lifecycleSignal: AbortSignal;
  isCurrent(): boolean;
  refreshSource(signal: AbortSignal): Promise<StoryboardAssetGenerationSnapshot>;
  executeConfirmedSave(
    input: StoryboardAssetSaveInput,
    plan: StoryboardAssetSavePlan,
    signal: AbortSignal,
    onRequestStarted: () => void,
    onEvent: (event: StoryboardAssetStreamEvent) => void,
  ): Promise<void>;
  onClose(): void;
  onUnauthorized(): void;
}

const kindLabels: Record<StoryboardAssetKind, string> = {
  character: "角色",
  scene: "场景",
  prop: "道具",
};

const promptCategories: Record<StoryboardAssetKind, string> = {
  character: "character-extraction",
  scene: "scene-extraction",
  prop: "prop-extraction",
};

const kindOutputKeys: Record<StoryboardAssetKind, { extracted: string; judged: string }> = {
  character: { extracted: "characters", judged: "merged_characters" },
  scene: { extracted: "scenes", judged: "scenes" },
  prop: { extracted: "props", judged: "props" },
};

const preserveValue = "__preserve_original_binding__";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function frameText(frame: StoryboardFrame): string {
  const value = typeof frame.text === "string"
    ? frame.text
    : typeof frame.original_text === "string" ? frame.original_text : "";
  return stripFrameOptimizeShotMarkers(value).trim();
}

function isOwnedByActor(item: { is_system?: boolean; user_id: string }, userId: string): boolean {
  return item.is_system === true || item.user_id === userId;
}

function rawCandidate(kind: StoryboardAssetKind, name: string): StoryboardAssetCandidate {
  return {
    name: kind === "scene" ? "" : name,
    title: kind === "scene" ? name : "",
    description: "",
    gender: "",
    age: "",
    role: "",
    appearance: "",
    voice_ref: "",
    aliases: [],
    original_name: name,
    existing_id: null,
    is_existing: false,
    is_backfill: false,
    keep: true,
  };
}

function candidatesForKind(snapshot: StoryboardAssetGenerationSnapshot, kind: StoryboardAssetKind): Array<{ id: string; name: string; candidate: StoryboardAssetCandidate }> {
  if (kind === "character") {
    return snapshot.characters.filter((item) => item.series_id === snapshot.chapter.series_id).map((item) => ({
      id: item.id,
      name: item.name,
      candidate: {
        ...rawCandidate(kind, item.name),
        description: item.appearance ?? "",
        gender: item.gender ?? "",
        age: item.age ?? "",
        role: item.role ?? "",
        appearance: item.appearance ?? "",
        voice_ref: item.voice_ref ?? "",
        aliases: [...(item.aliases ?? [])],
        existing_id: item.id,
        is_existing: true,
        is_backfill: true,
      },
    }));
  }
  if (kind === "scene") {
    return snapshot.scenes.filter((item) => item.series_id === snapshot.chapter.series_id).map((item) => ({
      id: item.id,
      name: item.title,
      candidate: {
        ...rawCandidate(kind, item.title),
        description: item.description ?? "",
        aliases: [...(item.aliases ?? [])],
        existing_id: item.id,
        is_existing: true,
        is_backfill: true,
      },
    }));
  }
  return snapshot.props.filter((item) => item.series_id === snapshot.chapter.series_id).map((item) => ({
    id: item.id,
    name: item.name,
    candidate: {
      ...rawCandidate(kind, item.name),
      description: item.description ?? "",
      aliases: [...(item.aliases ?? [])],
      existing_id: item.id,
      is_existing: true,
      is_backfill: true,
    },
  }));
}

function originalBindingNames(
  snapshot: StoryboardAssetGenerationSnapshot,
  kind: StoryboardAssetKind,
  frameIndex: number,
): string[] {
  const frame = snapshot.chapter.content?.[frameIndex];
  if (frame === undefined) return [];
  const key = kind === "character" ? "character" : kind === "scene" ? "scene" : "prop";
  const value = frame[key];
  if (!Array.isArray(value)) return [];
  const byId = new Map(candidatesForKind(snapshot, kind).map((entry) => [entry.id, entry.name]));
  return value.flatMap((id) => {
    if (typeof id !== "string") return [];
    const name = byId.get(id);
    return name === undefined ? [] : [name];
  });
}

function isStoryboardAssetKind(value: string): value is StoryboardAssetKind {
  return value === "character" || value === "scene" || value === "prop";
}

function normalizeJudgedAppearances(
  raw: StoryboardAssetAppearance[],
  kind: StoryboardAssetKind,
  candidates: StoryboardAssetCandidate[],
): { appearances: StoryboardAssetAppearance[]; unmatchedCount: number } {
  const included = candidates.filter((candidate) => candidate.keep);
  const byName = new Map<string, string>();
  for (const candidate of included) {
    const name = candidateName(candidate, kind);
    byName.set(name.trim().toLocaleLowerCase(), name);
    byName.set(candidate.original_name.trim().toLocaleLowerCase(), name);
    for (const alias of candidate.aliases) byName.set(alias.trim().toLocaleLowerCase(), name);
  }
  let unmatchedCount = 0;
  const normalized = raw.map((entry): StoryboardAssetAppearance => {
    if (entry === null) return null;
    if (kind === "scene") {
      if (typeof entry !== "string") throw new ApiError("invalid-response", "场景出场判定格式无效。");
      if (entry.trim() === "") return "";
      const matched = byName.get(entry.trim().toLocaleLowerCase());
      if (matched !== undefined) return matched;
      unmatchedCount += 1;
      return null;
    }
    if (!Array.isArray(entry)) throw new ApiError("invalid-response", "角色或道具出场判定格式无效。");
    const names: string[] = [];
    let hasUnmatchedName = false;
    for (const value of entry) {
      const matched = byName.get(value.trim().toLocaleLowerCase());
      if (matched === undefined) {
        unmatchedCount += 1;
        hasUnmatchedName = true;
      } else if (!names.includes(matched)) {
        names.push(matched);
      }
    }
    return hasUnmatchedName ? null : names;
  });
  return { appearances: normalized, unmatchedCount };
}

function candidateFrameName(candidate: StoryboardAssetCandidate, kind: StoryboardAssetKind): string {
  return candidateName(candidate, kind).trim();
}

function planKey(
  kind: StoryboardAssetKind,
  selectedFrames: number[],
  candidates: CandidateDraft[],
  appearances: StoryboardAssetAppearance[],
): string {
  return JSON.stringify({ kind, selectedFrames, candidates, appearances });
}

function planItemStatus(item: StoryboardAssetPlanItem): string {
  if (item.status === "create") return "拟新建";
  if (item.status === "reuse") return "复用已有素材";
  return "本次不采用";
}

function planItemDescription(item: StoryboardAssetPlanItem): string {
  if (item.status === "reuse") return item.existing_name ? `复用：${item.existing_name}` : "复用当前剧集素材库中的素材";
  return item.reason || (item.status === "create" ? "将保存为新素材" : "不加入本次绑定");
}

export function StoryboardAssetExtractionDialog({
  services,
  snapshot: initialSnapshot,
  userId,
  seriesId,
  lifecycleSignal,
  isCurrent,
  refreshSource,
  executeConfirmedSave,
  onClose,
  onUnauthorized,
}: StoryboardAssetExtractionDialogProps) {
  const generation = services.storyboardAssetGeneration;
  const [snapshot, setSnapshot] = useState(initialSnapshot);
  const [kind, setKind] = useState<StoryboardAssetKind>("character");
  const [modelId, setModelId] = useState(initialSnapshot.models[0]?.id ?? "");
  const [promptId, setPromptId] = useState("");
  const [enableThinking, setEnableThinking] = useState(false);
  const [selectedFrames, setSelectedFrames] = useState<Set<number>>(() => new Set(
    initialSnapshot.chapter.content?.map((_frame, index) => index) ?? [],
  ));
  const [step, setStep] = useState<WorkflowStep>("select");
  const [drafts, setDrafts] = useState<CandidateDraft[]>([]);
  const [appearances, setAppearances] = useState<StoryboardAssetAppearance[]>([]);
  const [plan, setPlan] = useState<StoryboardAssetSavePlan | null>(null);
  const [planSnapshotKey, setPlanSnapshotKey] = useState<string | null>(null);
  const [planConfirmed, setPlanConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);
  const activeController = useRef<AbortController | null>(null);
  const saveRequestStarted = useRef(false);
  const busyRef = useRef(false);
  const mounted = useRef(false);
  const nextDraftId = useRef(1);
  const isCurrentRef = useRef(isCurrent);
  const refreshSourceRef = useRef(refreshSource);
  const executeSaveRef = useRef(executeConfirmedSave);
  const unauthorizedRef = useRef(onUnauthorized);
  const closeRef = useRef(onClose);
  isCurrentRef.current = isCurrent;
  refreshSourceRef.current = refreshSource;
  executeSaveRef.current = executeConfirmedSave;
  unauthorizedRef.current = onUnauthorized;
  closeRef.current = onClose;

  const focus = useSeriesDialogFocus({ active: true, canDismiss: !busy, onDismiss: onClose });
  const frames = snapshot.chapter.content ?? [];
  const selectedFrameList = [...selectedFrames].sort((left, right) => left - right);
  const models = snapshot.models.filter((model) => (
    (model.category === "text" || model.category === "reasoning") && isOwnedByActor(model, userId)
  ));
  const prompts = snapshot.prompts.filter((prompt) => (
    prompt.category === promptCategories[kind] && isOwnedByActor(prompt, userId)
  ));
  const currentPlanKey = planKey(kind, selectedFrameList, drafts, appearances);
  const catalog = useMemo(() => candidatesForKind(snapshot, kind), [snapshot, kind]);
  const keptCandidateNames = drafts
    .filter((draft) => draft.value.keep)
    .map((draft) => candidateFrameName(draft.value, kind));
  const namesAreUsable = keptCandidateNames.length > 0
    && keptCandidateNames.every((name) => name !== "")
    && new Set(keptCandidateNames).size === keptCandidateNames.length;
  const selectedTexts = selectedFrameList.map((index) => {
    const frame = frames[index];
    return frame === undefined ? null : frameText(frame);
  });
  const hasSelectedText = selectedTexts.some((text) => text !== null && text.trim() !== "");
  const candidates = drafts.map((draft) => draft.value);
  const activeCandidates = candidates.filter((candidate) => candidate.keep);

  useEffect(() => {
    mounted.current = true;
    const abortActive = () => activeController.current?.abort();
    lifecycleSignal.addEventListener("abort", abortActive, { once: true });
    return () => {
      mounted.current = false;
      lifecycleSignal.removeEventListener("abort", abortActive);
      activeController.current?.abort();
    };
  }, [lifecycleSignal]);

  useEffect(() => {
    if (snapshot.chapter.series_id !== seriesId) return;
    const allowedModels = snapshot.models.filter((model) => (
      (model.category === "text" || model.category === "reasoning") && isOwnedByActor(model, userId)
    ));
    const allowedPrompts = snapshot.prompts.filter((prompt) => (
      prompt.category === promptCategories[kind] && isOwnedByActor(prompt, userId)
    ));
    setModelId((current) => allowedModels.some((model) => model.id === current) ? current : allowedModels[0]?.id ?? "");
    setPromptId((current) => current === "" || allowedPrompts.some((prompt) => prompt.id === current) ? current : "");
  }, [kind, seriesId, snapshot, userId]);

  function current(): boolean {
    return mounted.current && snapshot.chapter.series_id === seriesId && isCurrentRef.current() && !lifecycleSignal.aborted;
  }

  function invalidatePlan(): void {
    setPlan(null);
    setPlanSnapshotKey(null);
    setPlanConfirmed(false);
  }

  function replaceDrafts(update: (current: CandidateDraft[]) => CandidateDraft[]): void {
    setDrafts((currentDrafts) => update(currentDrafts));
    invalidatePlan();
  }

  async function runStep(
    label: string,
    action: (source: StoryboardAssetGenerationSnapshot, signal: AbortSignal) => Promise<void>,
    options: { finalSave?: boolean } = {},
  ): Promise<void> {
    if (busyRef.current || !current()) return;
    busyRef.current = true;
    setBusy(true);
    setMessage(null);
    setWarning(null);
    setStatus(label);
    if (options.finalSave) saveRequestStarted.current = false;
    const controller = new AbortController();
    activeController.current = controller;
    const abortForLifecycle = () => controller.abort();
    lifecycleSignal.addEventListener("abort", abortForLifecycle, { once: true });
    try {
      const fresh = await refreshSourceRef.current(controller.signal);
      if (!current() || controller.signal.aborted) return;
      setSnapshot(fresh);
      await action(fresh, controller.signal);
    } catch (cause) {
      if (!current() || controller.signal.aborted) return;
      const error = normalizeApiError(cause);
      if (error.status === 401) unauthorizedRef.current();
      if (options.finalSave && saveRequestStarted.current && !isExplicitSaveRejection(error)) {
        setStep("unknown");
        setMessage("保存请求已经发出，但回执或真源核对未能确认。系统已冻结自动重试；请刷新章节与素材库后人工核对。关闭窗口不会撤销可能已提交的保存。" );
      } else if (error.kind !== "aborted") {
        setMessage(error.detail ?? error.message);
      }
    } finally {
      lifecycleSignal.removeEventListener("abort", abortForLifecycle);
      if (activeController.current === controller) activeController.current = null;
      busyRef.current = false;
      if (current()) {
        setBusy(false);
        setStatus(null);
      }
    }
  }

  function captureCandidateCompletion(
    events: StoryboardAssetStreamEvent[],
    action: "extract" | "judge",
  ): Record<string, unknown> {
    const event = events.find((item) => item.type === "complete");
    if (event === undefined || !isRecord(event.data)) {
      throw new ApiError("network", "素材处理流没有完整完成结果。", null);
    }
    const key = action === "extract" ? kindOutputKeys[kind].extracted : kindOutputKeys[kind].judged;
    parseStoryboardAssetCandidates(event.data[key], kind);
    return event.data;
  }

  async function collect(
    stream: AsyncIterable<StoryboardAssetStreamEvent>,
    onEvent?: (event: StoryboardAssetStreamEvent) => void,
  ): Promise<StoryboardAssetStreamEvent[]> {
    const events: StoryboardAssetStreamEvent[] = [];
    for await (const event of stream) {
      if (!current()) throw new ApiError("aborted", "当前章节或登录范围已变化。", null);
      events.push(event);
      onEvent?.(event);
      if (event.type === "status") setStatus(String(event.data.message));
      if (event.type === "warning") setWarning(String(event.data.message));
    }
    return events;
  }

  function validateModelAndPrompt(source: StoryboardAssetGenerationSnapshot): { modelId: string; promptId: string | null } {
    const allowedModels = source.models.filter((model) => (
      (model.category === "text" || model.category === "reasoning") && isOwnedByActor(model, userId)
    ));
    if (!allowedModels.some((model) => model.id === modelId)) {
      throw new ApiError("http", "所选模型已不在当前账号可用列表中，请重新选择。", 409);
    }
    const allowedPrompts = source.prompts.filter((prompt) => (
      prompt.category === promptCategories[kind] && isOwnedByActor(prompt, userId)
    ));
    if (promptId !== "" && !allowedPrompts.some((prompt) => prompt.id === promptId)) {
      throw new ApiError("http", "所选提示词已不在当前账号可用列表中，请重新选择。", 409);
    }
    return { modelId, promptId: promptId || null };
  }

  function selectedSourceTexts(source: StoryboardAssetGenerationSnapshot, indices: number[]): string[] {
    const values = indices.map((index) => {
      const frame = source.chapter.content?.[index];
      if (frame === undefined) throw new ApiError("http", "所选镜头已变化，请重新核对章节。", 409);
      return frameText(frame);
    });
    if (values.length === 0 || !values.some((value) => value.trim() !== "")) {
      throw new ApiError("http", "所选镜头没有可提取的分镜文字。", 422);
    }
    return values;
  }

  async function extractAssets(): Promise<void> {
    await runStep(`正在核对章节并提取${kindLabels[kind]}…`, async (source, signal) => {
      if (generation === undefined) throw new ApiError("http", "当前运行模式没有接入素材提取服务。", null);
      const indices = selectedFrameList;
      selectedSourceTexts(source, indices);
      const selected = validateModelAndPrompt(source);
      const events = await collect(generation.streamExtract(kind, {
        chapter_id: source.chapter.id,
        model_id: selected.modelId,
        prompt_id: selected.promptId,
        enable_thinking: enableThinking,
        frame_indices: indices.length === (source.chapter.content?.length ?? 0) ? null : indices,
      }, signal));
      const complete = captureCandidateCompletion(events, "extract");
      const parsed = parseStoryboardAssetCandidates(complete[kindOutputKeys[kind].extracted], kind);
      const safe = parsed.map((candidate): CandidateDraft => {
        const name = candidateName(candidate, kind);
        return {
          id: nextDraftId.current++,
          value: {
            ...candidate,
            name: kind === "scene" ? "" : name,
            title: kind === "scene" ? name : "",
            original_name: candidate.original_name || name,
            existing_id: null,
            is_existing: false,
            is_backfill: false,
            keep: true,
          },
        };
      });
      setDrafts(safe);
      setAppearances([]);
      setPlan(null);
      setPlanSnapshotKey(null);
      setPlanConfirmed(false);
      setStep("edit");
      setMessage(safe.length === 0 ? "本次没有提取到素材。你可以手工添加候选或复用当前剧集的已有素材。" : `提取到 ${safe.length} 个${kindLabels[kind]}候选，请编辑后继续。`);
    });
  }

  async function judgeFrames(frameIndices: number[], replaceAll: boolean): Promise<void> {
    await runStep("正在核对章节并判断素材逐镜出场…", async (source, signal) => {
      if (generation === undefined) throw new ApiError("http", "当前运行模式没有接入素材提取服务。", null);
      const selected = validateModelAndPrompt(source);
      const indices = [...frameIndices].sort((left, right) => left - right);
      if (indices.length === 0 || indices.some((index) => index < 0 || index >= (source.chapter.content?.length ?? 0))) {
        throw new ApiError("http", "需要判断的镜头已变化，请重新核对。", 409);
      }
      const texts = selectedSourceTexts(source, indices);
      const usable = candidates.filter((candidate) => candidate.keep);
      if (usable.length === 0) throw new ApiError("http", "请至少保留一个素材候选后再判断出场。", 422);
      const inputCandidates = usable.map((candidate) => candidatePayload(candidate, kind));
      const events = await collect(generation.streamJudge(kind, {
        chapter_id: source.chapter.id,
        model_id: selected.modelId,
        prompt_id: selected.promptId,
        enable_thinking: enableThinking,
        skip_merge: true,
        candidates: inputCandidates,
        storyboard_texts: texts,
        frame_numbers: indices.map((index) => index + 1),
      }, signal));
      const complete = captureCandidateCompletion(events, "judge");
      const parsedAppearances = parseStoryboardAssetAppearances(complete.appearances, kind, indices.length);
      const aligned = normalizeJudgedAppearances(parsedAppearances, kind, candidates);
      setAppearances((previous) => {
        const next = replaceAll
          ? new Array<StoryboardAssetAppearance>(selectedFrameList.length).fill(null)
          : previous.length === selectedFrameList.length
            ? [...previous]
            : new Array<StoryboardAssetAppearance>(selectedFrameList.length).fill(null);
        for (let position = 0; position < indices.length; position += 1) {
          const index = indices[position];
          if (index === undefined) continue;
          const slot = selectedFrameList.indexOf(index);
          if (slot < 0) continue;
          next[slot] = aligned.appearances[position] ?? null;
        }
        return next;
      });
      invalidatePlan();
      setStep("review");
      setMessage(aligned.unmatchedCount > 0
        ? `${aligned.unmatchedCount} 项 AI 名称不在当前候选中，已按“保留原绑定”处理；可复用当前剧集素材或手工调整。`
        : `已完成 ${indices.length} 个镜头的出场判断。`);
    });
  }

  function addManualCandidate(): void {
    replaceDrafts((current) => [...current, { id: nextDraftId.current++, value: rawCandidate(kind, "") }]);
    setStep("edit");
    setMessage("请填写候选名称和属性，再判断出场或手工设置镜头绑定。" );
  }

  function addExistingCandidate(id: string): void {
    const entry = catalog.find((item) => item.id === id);
    if (entry === undefined) return;
    replaceDrafts((current) => {
      const existing = current.find((draft) => candidateName(draft.value, kind) === entry.name);
      if (existing !== undefined) {
        return current.map((draft) => draft.id === existing.id ? { ...draft, value: entry.candidate } : draft);
      }
      return [...current, { id: nextDraftId.current++, value: entry.candidate }];
    });
    setStep("edit");
    setMessage(`已加入当前剧集素材库中的${kindLabels[kind]}「${entry.name}」，保存时仅复用该剧集素材。`);
  }

  function updateCandidate(id: number, update: (value: StoryboardAssetCandidate) => StoryboardAssetCandidate): void {
    const previous = drafts.find((draft) => draft.id === id)?.value;
    if (previous === undefined) return;
    const previousName = candidateName(previous, kind);
    replaceDrafts((current) => current.map((draft) => draft.id === id
      ? { ...draft, value: update(draft.value) }
      : draft));
    const nextName = candidateName(update(previous), kind);
    if (nextName !== previousName) {
      setAppearances((current) => current.map((entry) => replaceAppearanceName(entry, kind, previousName, nextName)));
    }
  }

  function removeCandidate(id: number): void {
    const draft = drafts.find((entry) => entry.id === id);
    if (draft === undefined) return;
    const name = candidateName(draft.value, kind);
    replaceDrafts((current) => current.filter((entry) => entry.id !== id));
    setAppearances((current) => current.map((entry) => withoutAppearanceName(entry, kind, name)));
  }

  function setCandidateKept(id: number, keep: boolean): void {
    const draft = drafts.find((entry) => entry.id === id);
    if (draft === undefined) return;
    const name = candidateName(draft.value, kind);
    updateCandidate(id, (candidate) => ({ ...candidate, keep }));
    if (!keep) setAppearances((current) => current.map((entry) => withoutAppearanceName(entry, kind, name)));
  }

  function setAppearanceAt(slot: number, next: StoryboardAssetAppearance): void {
    setAppearances((current) => current.map((entry, index) => index === slot ? next : entry));
    invalidatePlan();
    setStep("review");
  }

  function buildInput(
    dryRun: boolean,
    source: StoryboardAssetGenerationSnapshot,
  ): StoryboardAssetSaveInput {
    if (!namesAreUsable) throw new ApiError("http", "候选名称必须非空且不能重复，请先修正。", 422);
    const candidatesForSave = drafts.map((draft) => draft.value);
    if (appearances.length !== selectedFrameList.length) {
      throw new ApiError("http", "逐镜出场结果与选择镜头数量不一致。", 422);
    }
    if (!Array.isArray(source.chapter.content)) {
      throw new ApiError("http", "当前章节没有可核对的分镜快照，请刷新后重新打开素材流程。", 409);
    }
    if (typeof source.chapter.series_id !== "string" || source.chapter.series_id.trim() === "") {
      throw new ApiError("http", "当前章节没有可核对的剧集归属，请刷新后重新打开素材流程。", 409);
    }
    const includedNames = new Set(candidatesForSave.filter((candidate) => candidate.keep).map((candidate) => candidateName(candidate, kind)));
    const sanitizedAppearances = appearances.map((entry): StoryboardAssetAppearance => {
      if (entry === null) return null;
      if (kind === "scene") return typeof entry === "string" && (entry === "" || includedNames.has(entry)) ? entry : null;
      if (!Array.isArray(entry)) return null;
      return entry.filter((name) => includedNames.has(name));
    });
    return buildStoryboardAssetSaveInput(
      kind,
      source.chapter.id,
      selectedFrameList,
      candidatesForSave,
      sanitizedAppearances,
      dryRun,
      source.chapter.content,
      source.chapter.series_id,
    );
  }

  async function preparePlan(): Promise<void> {
    await runStep("正在保存 dry-run 计划，章节不会写入…", async (source, signal) => {
      if (generation === undefined) throw new ApiError("http", "当前运行模式没有接入素材保存服务。", null);
      validateModelAndPrompt(source);
      const input = buildInput(true, source);
      const events = await collect(generation.streamSave(kind, input, signal));
      const complete = events.find((event) => event.type === "complete")?.data;
      if (!isRecord(complete) || complete.success !== true || complete.dry_run !== true) {
        throw new ApiError("invalid-response", "服务器没有返回可确认的 dry-run 保存计划。", null);
      }
      const parsedPlan = parseStoryboardAssetSavePlan(complete.plan, kind);
      setPlan(parsedPlan);
      setPlanSnapshotKey(currentPlanKey);
      setPlanConfirmed(false);
      setStep("review");
      setMessage("dry-run 计划已生成，尚未保存。请逐镜检查绑定与复用/新建清单。" );
    });
  }

  async function confirmSave(): Promise<void> {
    if (plan === null || !planConfirmed || planSnapshotKey !== currentPlanKey) return;
    await runStep("正在核对锁并保存确认的素材…", async (source, signal) => {
      if (generation === undefined || plan === null) throw new ApiError("http", "素材保存服务不可用。", null);
      const input = buildInput(false, source);
      if (planSnapshotKey !== currentPlanKey) throw new ApiError("http", "确认计划后候选或镜头绑定已变化，请重新生成 dry-run。", 409);
      const onRequestStarted = () => { saveRequestStarted.current = true; };
      await executeSaveRef.current(input, plan, signal, onRequestStarted, (event) => {
        if (event.type === "status" && typeof event.data.message === "string") setStatus(event.data.message);
      });
      if (current()) {
        setStatus("保存已完成并通过服务器真源回读核对。");
        setMessage("素材、章节与逐镜绑定已重新读取并核对。" );
        setStep("review");
      }
      void source;
    }, { finalSave: true });
  }

  function updateSelectedFrame(index: number, checked: boolean): void {
    setSelectedFrames((current) => {
      const next = new Set(current);
      if (checked) next.add(index);
      else next.delete(index);
      return next;
    });
    setDrafts([]);
    setAppearances([]);
    invalidatePlan();
    setStep("select");
  }

  function selectAllFrames(checked: boolean): void {
    setSelectedFrames(checked
      ? new Set(frames.map((_frame, index) => index))
      : new Set());
    setDrafts([]);
    setAppearances([]);
    invalidatePlan();
    setStep("select");
  }

  function changeKind(nextKind: StoryboardAssetKind): void {
    if (nextKind === kind) return;
    setKind(nextKind);
    setPromptId("");
    setDrafts([]);
    setAppearances([]);
    setPlan(null);
    setPlanSnapshotKey(null);
    setPlanConfirmed(false);
    setStep("select");
    setMessage(null);
    setWarning(null);
  }

  function close(): void {
    if (busyRef.current) return;
    closeRef.current();
  }

  const finalPlanCurrent = plan !== null && planSnapshotKey === currentPlanKey;
  const canExtract = generation !== undefined && modelId !== "" && selectedFrameList.length > 0 && hasSelectedText && !busy;
  const canJudge = generation !== undefined && modelId !== "" && selectedFrameList.length > 0
    && hasSelectedText && activeCandidates.length > 0 && !busy;

  return (
    <div className="series-dialog chapter-editing-dialog storyboard-asset-extraction-dialog" ref={(element) => { focus.dialogRef.current = element; }} role="dialog" aria-modal="true" aria-labelledby="storyboard-asset-heading">
      <header className="storyboard-asset-header">
        <div>
          <p className="eyebrow">{snapshot.chapter.title}</p>
          <h2 id="storyboard-asset-heading">提取并确认素材</h2>
        </div>
        <button className="icon-button" type="button" aria-label="关闭素材提取" onClick={close} disabled={busy}>×</button>
      </header>

      {step !== "unknown" && (
        <section className="storyboard-asset-controls" aria-label="提取设置">
          <label>
            素材类别
            <select value={kind} onChange={(event) => {
              const value = event.currentTarget.value;
              if (isStoryboardAssetKind(value)) changeKind(value);
            }} disabled={busy}>
              <option value="character">角色</option>
              <option value="scene">场景</option>
              <option value="prop">道具</option>
            </select>
          </label>
          <label>
            模型
            <select aria-label="模型" value={modelId} onChange={(event) => setModelId(event.currentTarget.value)} disabled={busy || models.length === 0}>
              {models.map((model) => <option key={model.id} value={model.id}>{model.name}{model.category === "reasoning" ? " · 推理" : " · 文本"}</option>)}
            </select>
          </label>
          <label>
            提取提示词（可选）
            <select aria-label="提取提示词" value={promptId} onChange={(event) => setPromptId(event.currentTarget.value)} disabled={busy}>
              <option value="">不使用自定义提示词</option>
              {prompts.map((prompt) => <option key={prompt.id} value={prompt.id}>{prompt.name}{prompt.is_system ? " · 系统" : " · 我的"}</option>)}
            </select>
          </label>
          <label className="storyboard-asset-thinking">
            <input type="checkbox" checked={enableThinking} onChange={(event) => setEnableThinking(event.currentTarget.checked)} disabled={busy} />
            启用思考
          </label>
        </section>
      )}

      {step === "select" && (
        <section className="storyboard-asset-frame-selection">
          <div className="storyboard-asset-section-heading">
            <div><h3>选择镜头</h3><p>未选择的镜头不会发送给判断或保存接口。</p></div>
            <label><input type="checkbox" checked={selectedFrameList.length === frames.length && frames.length > 0} onChange={(event) => selectAllFrames(event.currentTarget.checked)} disabled={busy} />全选</label>
          </div>
          <div className="storyboard-asset-frame-list">
            {frames.map((frame, index) => (
              <label className="storyboard-asset-frame-option" key={index}>
                <input type="checkbox" aria-label={`选择镜头 ${index + 1}`} checked={selectedFrames.has(index)} onChange={(event) => updateSelectedFrame(index, event.currentTarget.checked)} disabled={busy} />
                <span><strong>镜头 {index + 1}</strong><span>{frameText(frame) || "（无文字）"}</span></span>
              </label>
            ))}
          </div>
          {models.length === 0 && <p role="alert">当前账号没有可用的文本或推理模型。</p>}
          {!hasSelectedText && selectedFrameList.length > 0 && <p role="alert">所选镜头没有可提取的文字，请选择包含文字的镜头。</p>}
          <footer className="storyboard-asset-footer">
            <span>{selectedFrameList.length} / {frames.length} 个镜头已选</span>
            <button type="button" className="primary-button" disabled={!canExtract} onClick={() => void extractAssets()}>{busy ? "处理中…" : `提取${kindLabels[kind]}`}</button>
          </footer>
        </section>
      )}

      {step === "edit" && (
        <section className="storyboard-asset-edit-step">
          <div className="storyboard-asset-section-heading">
            <div><h3>编辑{kindLabels[kind]}候选</h3><p>按当前剧集素材库确认复用项；候选名称和属性会进入 dry-run 计划。</p></div>
            <span>{drafts.length} 个候选</span>
          </div>
          <div className="storyboard-asset-candidate-toolbar">
            <button type="button" className="secondary-button" onClick={addManualCandidate} disabled={busy}>添加候选</button>
            <label>
              从当前剧集素材库复用
              <select aria-label={`复用${kindLabels[kind]}`} value="" onChange={(event) => addExistingCandidate(event.currentTarget.value)} disabled={busy || catalog.length === 0}>
                <option value="">选择已有素材</option>
                {catalog.map((item) => <option key={item.id} value={item.id}>{item.name}{item.name.trim() === "" ? "（名称无效）" : ""}</option>)}
              </select>
            </label>
          </div>
          {drafts.length === 0 && <p className="storyboard-asset-empty">暂无候选。可以添加候选或从当前剧集素材库复用。</p>}
          <div className="storyboard-asset-candidates">
            {drafts.map((draft, index) => (
              <CandidateEditor
                key={draft.id}
                candidate={draft.value}
                index={index}
                kind={kind}
                busy={busy}
                onChange={(update) => updateCandidate(draft.id, update)}
                onKeep={(keep) => setCandidateKept(draft.id, keep)}
                onRemove={() => removeCandidate(draft.id)}
              />
            ))}
          </div>
          <footer className="storyboard-asset-footer">
            <button type="button" className="secondary-button" onClick={() => setStep("select")} disabled={busy}>返回镜头选择</button>
            <button type="button" className="primary-button" onClick={() => void judgeFrames(selectedFrameList, true)} disabled={!canJudge}>判断出场并继续</button>
          </footer>
        </section>
      )}

      {(step === "review" || step === "unknown") && (
        <section className="storyboard-asset-review-step">
          {step === "unknown" && (
            <div className="storyboard-asset-unknown" role="alert">
              <strong>保存结果尚未确认</strong>
              <p>{message}</p>
              <button type="button" className="secondary-button" onClick={() => void runStep("正在重新读取章节与素材库…", async (source) => {
                setSnapshot(source);
                setMessage("真源已重新读取。此前保存回执仍未知，请人工核对当前章节和素材库；本窗口不会重发保存请求。" );
              })} disabled={busy}>重新读取真源</button>
            </div>
          )}
          <div className="storyboard-asset-section-heading">
            <div><h3>逐镜出场与绑定</h3><p>场景单选；角色和道具可多选。未判定项为“保留原绑定”，空列表表示明确清空。</p></div>
            <button type="button" className="secondary-button" onClick={() => void judgeFrames(selectedFrameList, true)} disabled={!canJudge || step === "unknown"}>重新判断全部所选镜头</button>
          </div>
          <div className="storyboard-asset-frame-list">
            {selectedFrameList.map((frameIndex, slot) => (
              <AppearanceEditor
                key={`${snapshot.chapter.id}:${frameIndex}`}
                snapshot={snapshot}
                kind={kind}
                frameIndex={frameIndex}
                text={frames[frameIndex] === undefined ? "" : frameText(frames[frameIndex])}
                candidates={drafts}
                appearance={appearances[slot] ?? null}
                busy={busy || step === "unknown"}
                onChange={(entry) => setAppearanceAt(slot, entry)}
                onRejudge={() => void judgeFrames([frameIndex], false)}
              />
            ))}
          </div>

          <div className="storyboard-asset-plan-actions">
            <button type="button" className="secondary-button" onClick={() => setStep("edit")} disabled={busy || step === "unknown"}>返回候选编辑</button>
            <button type="button" className="secondary-button" onClick={() => void preparePlan()} disabled={busy || step === "unknown" || !namesAreUsable || selectedFrameList.length === 0}>
              {plan === null ? "生成 dry-run 保存计划" : "重新生成 dry-run 保存计划"}
            </button>
          </div>

          {plan !== null && step !== "unknown" && (
            <section className="storyboard-asset-plan" aria-label="保存计划">
              <h3>保存计划：{plan.items.length} 个候选</h3>
              <div className="storyboard-asset-plan-items">
                {plan.items.map((item, index) => (
                  <article key={`${item.name}:${index}`}>
                    <strong>{item.name}</strong>
                    <span>{planItemStatus(item)}</span>
                    <p>{planItemDescription(item)}</p>
                    {item.frames.length > 0 && <small>出场镜头：{item.frames.join("、")}</small>}
                  </article>
                ))}
              </div>
              <h4>逐镜绑定预览</h4>
              <ol className="storyboard-asset-plan-frames">
                {selectedFrameList.map((frameIndex, slot) => (
                  <li key={frameIndex}>
                    <strong>镜头 {frameIndex + 1}</strong>
                    <span>{appearanceLabel(appearances[slot] ?? null, kind)}</span>
                  </li>
                ))}
              </ol>
              {!finalPlanCurrent && <p role="alert">候选或镜头绑定已变化，请重新生成 dry-run 计划。</p>}
              <label className="storyboard-asset-confirm">
                <input type="checkbox" checked={planConfirmed} onChange={(event) => setPlanConfirmed(event.currentTarget.checked)} disabled={busy || !finalPlanCurrent} />
                我已核对候选、每个镜头的绑定及复用/新建计划，确认执行保存。
              </label>
              <button type="button" className="primary-button" onClick={() => void confirmSave()} disabled={busy || !planConfirmed || !finalPlanCurrent}>
                明确确认并保存
              </button>
            </section>
          )}
        </section>
      )}

      <footer className="storyboard-asset-dialog-footer" aria-live="polite">
        {status !== null && <span role="status">{status}</span>}
        {warning !== null && <p role="status">{warning}</p>}
        {message !== null && step !== "unknown" && <p role="status">{message}</p>}
      </footer>
    </div>
  );
}

interface CandidateEditorProps {
  candidate: StoryboardAssetCandidate;
  index: number;
  kind: StoryboardAssetKind;
  busy: boolean;
  onChange(update: (candidate: StoryboardAssetCandidate) => StoryboardAssetCandidate): void;
  onKeep(keep: boolean): void;
  onRemove(): void;
}

function CandidateEditor({ candidate, index, kind, busy, onChange, onKeep, onRemove }: CandidateEditorProps) {
  const name = candidateName(candidate, kind);
  const rename = (value: string) => onChange((current) => ({
    ...current,
    name: kind === "scene" ? "" : value,
    title: kind === "scene" ? value : "",
    original_name: current.original_name || name,
  }));
  return (
    <article className="storyboard-asset-candidate">
      <div className="storyboard-asset-candidate-heading">
        <h4>{name || `新候选 ${index + 1}`}</h4>
        <button type="button" className="text-button" onClick={onRemove} disabled={busy}>移除此候选</button>
      </div>
      <label>
        {kind === "scene" ? "场景名称" : kind === "character" ? "角色名称" : "道具名称"}
        <input value={name} onChange={(event) => rename(event.currentTarget.value)} disabled={busy} />
      </label>
      <label>
        描述
        <textarea value={candidate.description} onChange={(event) => onChange((current) => ({ ...current, description: event.currentTarget.value }))} rows={2} disabled={busy} />
      </label>
      {kind === "character" && (
        <div className="storyboard-asset-character-fields">
          <label>性别<input value={candidate.gender} onChange={(event) => onChange((current) => ({ ...current, gender: event.currentTarget.value }))} disabled={busy} /></label>
          <label>年龄<input value={candidate.age} onChange={(event) => onChange((current) => ({ ...current, age: event.currentTarget.value }))} disabled={busy} /></label>
          <label>角色<input value={candidate.role} onChange={(event) => onChange((current) => ({ ...current, role: event.currentTarget.value }))} disabled={busy} /></label>
          <label>外观<input value={candidate.appearance} onChange={(event) => onChange((current) => ({ ...current, appearance: event.currentTarget.value }))} disabled={busy} /></label>
          <label>声音参考<input value={candidate.voice_ref} onChange={(event) => onChange((current) => ({ ...current, voice_ref: event.currentTarget.value }))} disabled={busy} /></label>
        </div>
      )}
      <label>
        别名（用逗号或顿号分隔）
        <input value={candidate.aliases.join("、")} onChange={(event) => onChange((current) => ({
          ...current,
          aliases: event.currentTarget.value.split(/[,，、]/).map((item) => item.trim()).filter(Boolean),
        }))} disabled={busy} />
      </label>
      {candidate.existing_id !== null && <p className="storyboard-asset-reuse-note">复用当前剧集素材：{candidate.existing_id}</p>}
      <label className="storyboard-asset-keep">
        <input type="checkbox" checked={candidate.keep} onChange={(event) => onKeep(event.currentTarget.checked)} disabled={busy} />
        纳入本次素材候选
      </label>
    </article>
  );
}

interface AppearanceEditorProps {
  snapshot: StoryboardAssetGenerationSnapshot;
  kind: StoryboardAssetKind;
  frameIndex: number;
  text: string;
  candidates: CandidateDraft[];
  appearance: StoryboardAssetAppearance;
  busy: boolean;
  onChange(entry: StoryboardAssetAppearance): void;
  onRejudge(): void;
}

function AppearanceEditor({
  snapshot,
  kind,
  frameIndex,
  text,
  candidates,
  appearance,
  busy,
  onChange,
  onRejudge,
}: AppearanceEditorProps) {
  const options = candidates.filter((draft) => draft.value.keep);
  const originalNames = originalBindingNames(snapshot, kind, frameIndex);
  const currentNames = appearance === null
    ? originalNames
    : kind === "scene"
      ? (typeof appearance === "string" && appearance !== "" ? [appearance] : [])
      : Array.isArray(appearance) ? appearance : [];
  const currentLabels = currentNames.length > 0 ? currentNames.join("、") : "无素材绑定";

  if (kind === "scene") {
    const value = appearance === null ? preserveValue : typeof appearance === "string" ? appearance : "";
    return (
      <article className="storyboard-asset-appearance" aria-label={`镜头 ${frameIndex + 1} 出场绑定`}>
        <div><strong>镜头 {frameIndex + 1}</strong><p>{text || "（无文字）"}</p></div>
        <p className="storyboard-asset-current-binding">{appearance === null ? `未判定，保留原绑定：${currentLabels}` : `当前确认：${currentLabels}`}</p>
        <label>
          场景绑定（单选）
          <select aria-label={`镜头 ${frameIndex + 1} 场景绑定`} value={value} onChange={(event) => onChange(event.currentTarget.value === preserveValue ? null : event.currentTarget.value)} disabled={busy}>
            <option value={preserveValue}>未判定，保留原绑定</option>
            <option value="">无场景（明确清空）</option>
            {options.map((draft) => {
              const name = candidateName(draft.value, kind);
              return <option key={draft.id} value={name}>{name}</option>;
            })}
          </select>
        </label>
        <button type="button" className="text-button" onClick={onRejudge} disabled={busy}>单独重判此镜</button>
      </article>
    );
  }

  const values = appearance === null ? [] : Array.isArray(appearance) ? appearance : [appearance];
  return (
    <article className="storyboard-asset-appearance" aria-label={`镜头 ${frameIndex + 1} 出场绑定`}>
      <div><strong>镜头 {frameIndex + 1}</strong><p>{text || "（无文字）"}</p></div>
      <p className="storyboard-asset-current-binding">{appearance === null ? `未判定，保留原绑定：${currentLabels}` : `当前确认：${currentLabels}`}</p>
      {appearance === null && <button type="button" className="text-button" onClick={() => onChange([])} disabled={busy}>手工编辑并清空此镜绑定</button>}
      <fieldset>
        <legend>{kindLabels[kind]}绑定（可多选）</legend>
        {options.length === 0 && <p>没有可选候选，请返回候选编辑添加素材。</p>}
        {options.map((draft) => {
          const name = candidateName(draft.value, kind);
          return (
            <label key={draft.id}>
              <input type="checkbox" checked={values.includes(name)} onChange={(event) => {
                const next = new Set(values);
                if (event.currentTarget.checked) next.add(name);
                else next.delete(name);
                onChange([...next]);
              }} disabled={busy} />
              {name || "未命名候选"}
            </label>
          );
        })}
      </fieldset>
      <button type="button" className="text-button" onClick={onRejudge} disabled={busy}>单独重判此镜</button>
    </article>
  );
}

function replaceAppearanceName(
  appearance: StoryboardAssetAppearance,
  kind: StoryboardAssetKind,
  oldName: string,
  newName: string,
): StoryboardAssetAppearance {
  if (appearance === null) return null;
  if (kind === "scene") return appearance === oldName ? newName : appearance;
  if (!Array.isArray(appearance)) return appearance;
  return appearance.map((name) => name === oldName ? newName : name);
}

function withoutAppearanceName(
  appearance: StoryboardAssetAppearance,
  kind: StoryboardAssetKind,
  name: string,
): StoryboardAssetAppearance {
  if (appearance === null) return null;
  if (kind === "scene") return appearance === name ? "" : appearance;
  if (!Array.isArray(appearance)) return appearance;
  return appearance.filter((item) => item !== name);
}

function appearanceLabel(appearance: StoryboardAssetAppearance, kind: StoryboardAssetKind): string {
  if (appearance === null) return "保留原绑定（AI 未返回）";
  if (kind === "scene") return typeof appearance === "string" && appearance !== "" ? appearance : "无场景";
  return Array.isArray(appearance) && appearance.length > 0 ? appearance.join("、") : "无绑定";
}

function isExplicitSaveRejection(error: ApiError): boolean {
  return error.kind === "http" && error.status !== null && error.status >= 400 && error.status < 500;
}
