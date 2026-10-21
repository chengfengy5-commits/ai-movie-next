import { useEffect, useRef, useState } from "react";
import type { AssetLibraryType, Chapter, Character, Prop, Scene } from "../../../shared/api/contracts";
import { AssetReplacementError, toAssetReplacementType, type AssetReplacementRun } from "../../../shared/api/assetReplacement";
import { normalizeApiError, type WorkspaceServices } from "../../../shared/api/services";
import { useSeriesDialogFocus } from "../../series/useSeriesDialogFocus";
import "./assetReplacement.css";
import {
  buildAssetReplacementPlan,
  assetSnapshotMatches,
  chapterContentMatches,
  type AssetReplacementChapterPlan,
  type AssetReplacementPlan,
} from "./assetReplacement";

type AssetRecord = Character | Scene | Prop;

type ChapterResultStatus = "not-submitted" | "not-sent" | "submitting" | "acknowledged" | "verified" | "rejected" | "unknown";
type RecoveryObservation = "expected" | "baseline" | "changed" | "unreadable";

interface ChapterResult {
  status: ChapterResultStatus;
  detail: string;
  observation: RecoveryObservation | null;
}

interface AssetSelectionSnapshot {
  oldAsset: AssetRecord;
  newAsset: AssetRecord;
}

interface ReplacementAttempt {
  id: number;
  run: AssetReplacementRun | null;
  assetSnapshot: AssetSelectionSnapshot | null;
  oldAssetName: string;
  newAssetId: string;
  newAssetName: string;
  selected: AssetReplacementChapterPlan[];
  results: Record<string, ChapterResult>;
}

interface AssetReplacementDialogProps {
  open: boolean;
  epoch: number;
  category: AssetLibraryType;
  asset: AssetRecord;
  seriesId: string;
  seriesName: string;
  canEnter: boolean;
  services: WorkspaceServices;
  isCurrent(epoch: number): boolean;
  onClose(epoch: number): void;
  onBusyChange(epoch: number, busy: boolean): void;
  onDirectoryUpdated(items: AssetRecord[], epoch: number): void;
  onUnauthorized(epoch: number): void;
}

function categoryLabel(category: AssetLibraryType): string {
  switch (category) {
    case "characters": return "角色";
    case "scenes": return "场景";
    case "props": return "道具";
  }
}

function assetName(category: AssetLibraryType, asset: AssetRecord): string {
  const value = category === "scenes"
    ? ("title" in asset && typeof asset.title === "string" ? asset.title : "")
    : ("name" in asset && typeof asset.name === "string" ? asset.name : "");
  return value.trim() === "" ? `未命名${categoryLabel(category)}` : value;
}

function readAssets(
  services: WorkspaceServices,
  category: AssetLibraryType,
  seriesId: string,
  signal: AbortSignal,
): Promise<AssetRecord[]> {
  switch (category) {
    case "characters": return services.listCharacters(seriesId, signal);
    case "scenes": return services.listScenes(seriesId, signal);
    case "props": return services.listProps(seriesId, signal);
  }
}

function hasUniqueAssetId(items: AssetRecord[], assetId: string, seriesId: string): boolean {
  return findUniqueAsset(items, assetId, seriesId) !== null;
}

function findUniqueAsset(items: AssetRecord[], assetId: string, seriesId: string): AssetRecord | null {
  const matches = items.filter((candidate) => candidate.id === assetId);
  const [match] = matches;
  return matches.length === 1 && match?.series_id === seriesId ? match : null;
}

function statusLabel(status: ChapterResultStatus): string {
  switch (status) {
    case "not-submitted": return "未提交";
    case "not-sent": return "未发送";
    case "submitting": return "提交中";
    case "acknowledged": return "服务已确认，待回读";
    case "verified": return "引用已核对";
    case "rejected": return "服务器拒绝";
    case "unknown": return "结果未知";
  }
}

function resultFor(status: ChapterResultStatus, detail: string, observation: RecoveryObservation | null = null): ChapterResult {
  return { status, detail, observation };
}

function copyAssetRecord(asset: AssetRecord): AssetRecord {
  return JSON.parse(JSON.stringify(asset)) as AssetRecord;
}

function newResults(selected: AssetReplacementChapterPlan[]): Record<string, ChapterResult> {
  return Object.fromEntries(selected.map((chapter) => [chapter.chapterId, resultFor("not-submitted", "尚未发送。") ]));
}

function contentText(value: string | null): string {
  return value === null || value.trim() === "" ? "（无文字）" : value;
}

export function AssetReplacementDialog({
  open,
  epoch,
  category,
  asset,
  seriesId,
  seriesName,
  canEnter,
  services,
  isCurrent,
  onClose,
  onBusyChange,
  onDirectoryUpdated,
  onUnauthorized,
}: AssetReplacementDialogProps) {
  const focus = useSeriesDialogFocus({ active: open, canDismiss: true, onDismiss: close });
  const [loading, setLoading] = useState(false);
  const [sourceError, setSourceError] = useState<string | null>(null);
  const [chapters, setChapters] = useState<Chapter[] | null>(null);
  const [directory, setDirectory] = useState<AssetRecord[] | null>(null);
  const [newAssetId, setNewAssetId] = useState("");
  const [plan, setPlan] = useState<AssetReplacementPlan | null>(null);
  const [assetSnapshot, setAssetSnapshot] = useState<AssetSelectionSnapshot | null>(null);
  const [selectedChapterIds, setSelectedChapterIds] = useState<Set<string>>(() => new Set());
  const [confirmationChecked, setConfirmationChecked] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [currentAttempt, setCurrentAttempt] = useState<ReplacementAttempt | null>(null);
  const [attemptHistory, setAttemptHistory] = useState<ReplacementAttempt[]>([]);
  const [recoveryNotice, setRecoveryNotice] = useState<string | null>(null);
  const sourceControllerRef = useRef<AbortController | null>(null);
  const operationControllerRef = useRef<AbortController | null>(null);
  const previewRunRef = useRef<AssetReplacementRun | null>(null);
  const sequenceRef = useRef(0);
  const submittingRef = useRef(false);
  const currentAttemptRef = useRef(currentAttempt);
  currentAttemptRef.current = currentAttempt;
  const previewOldAsset = assetSnapshot?.oldAsset
    ?? findUniqueAsset(directory ?? [], asset.id, seriesId)
    ?? asset;
  const oldAssetName = assetName(category, previewOldAsset);
  const selectedAsset = directory?.find((candidate) => candidate.id === newAssetId) ?? null;
  const candidateCounts = new Map<string, number>();
  for (const candidate of directory ?? []) {
    candidateCounts.set(candidate.id, (candidateCounts.get(candidate.id) ?? 0) + 1);
  }
  const candidates = (directory ?? []).filter((candidate) => candidate.series_id === seriesId
    && candidate.id.trim() !== ""
    && candidate.id !== asset.id
    && candidateCounts.get(candidate.id) === 1);
  const readyChapters = plan?.status === "ready"
    ? plan.chapters.filter((chapter) => chapter.status === "ready")
    : [];
  const selectedPlans = readyChapters.filter((chapter) => selectedChapterIds.has(chapter.chapterId));
  const canConfirm = open
    && canEnter
    && services.assetReplacement !== undefined
    && !loading
    && !submitting
    && selectedAsset !== null
    && assetSnapshot !== null
    && selectedPlans.length > 0
    && confirmationChecked
    && plan?.status === "ready"
    && sourceError === null;

  function close(): void {
    sourceControllerRef.current?.abort();
    operationControllerRef.current?.abort();
    onClose(epoch);
  }

  function isRunCurrent(run: AssetReplacementRun | null): boolean {
    return run === null ? services.assetReplacement === undefined : run.isCurrent();
  }

  function reportUnauthorized(error: unknown, requestEpoch: number, run: AssetReplacementRun | null): void {
    const apiError = normalizeApiError(error);
    if (apiError.status === 401 && run !== null && run.isCurrent() && isCurrent(requestEpoch)) {
      onUnauthorized(requestEpoch);
    }
  }

  async function readSources(
    controller: AbortController,
    requestEpoch: number,
    run: AssetReplacementRun | null,
  ): Promise<{
    chapters: Chapter[];
    directory: AssetRecord[];
  } | null> {
    const [nextChapters, nextDirectory] = await Promise.all([
      services.listChapters(seriesId, controller.signal),
      readAssets(services, category, seriesId, controller.signal),
    ]);
    if (controller.signal.aborted || !isCurrent(requestEpoch) || !isRunCurrent(run)) return null;
    setChapters(nextChapters);
    setDirectory(nextDirectory);
    if (!hasUniqueAssetId(nextDirectory, asset.id, seriesId)) {
      setSourceError("当前旧素材无法在最新目录中唯一核对，不能生成替换预览。");
    } else {
      setSourceError(null);
    }
    onDirectoryUpdated(nextDirectory, requestEpoch);
    return { chapters: nextChapters, directory: nextDirectory };
  }

  async function loadSources(options: {
    preserveSelection?: boolean;
    quiet?: boolean;
    captureNewRun?: boolean;
  } = {}): Promise<void> {
    if (!isCurrent(epoch) || !canEnter) return;
    if (options.captureNewRun === true || previewRunRef.current === null) {
      previewRunRef.current = services.assetReplacement?.beginRun() ?? null;
    }
    const run = previewRunRef.current;
    if (!isRunCurrent(run)) {
      setSourceError("原预览会话已变化，请重新读取并生成新的替换预览。未发送替换请求。");
      return;
    }
    sourceControllerRef.current?.abort();
    const controller = new AbortController();
    sourceControllerRef.current = controller;
    const requestSequence = ++sequenceRef.current;
    if (!options.quiet) setLoading(true);
    setSourceError(null);
    try {
      const result = await readSources(controller, epoch, run);
      if (result === null || requestSequence !== sequenceRef.current || controller.signal.aborted) {
        if (!controller.signal.aborted && requestSequence === sequenceRef.current && !isRunCurrent(run)) {
          setSourceError("原预览会话已变化，请重新读取并生成新的替换预览。未发送替换请求。");
        }
        return;
      }
      if (options.preserveSelection && newAssetId !== "") {
        const oldSnapshot = findUniqueAsset(result.directory, asset.id, seriesId);
        const newSnapshot = findUniqueAsset(result.directory, newAssetId, seriesId);
        if (oldSnapshot === null || newSnapshot === null || newAssetId === asset.id) {
          setNewAssetId("");
          setPlan(null);
          setAssetSnapshot(null);
          setSelectedChapterIds(new Set());
          setConfirmationChecked(false);
          setSourceError("原预览中的新素材已变化，请重新选择并核对。");
          return;
        }
        setAssetSnapshot({ oldAsset: copyAssetRecord(oldSnapshot), newAsset: copyAssetRecord(newSnapshot) });
        setPlan(buildAssetReplacementPlan(result.chapters, seriesId, category, asset.id, newAssetId));
        setSelectedChapterIds(new Set());
        setConfirmationChecked(false);
        return;
      }
      setPlan(null);
      setAssetSnapshot(null);
      setSelectedChapterIds(new Set());
      setConfirmationChecked(false);
    } catch (error) {
      if (controller.signal.aborted || requestSequence !== sequenceRef.current) return;
      reportUnauthorized(error, epoch, run);
      const apiError = normalizeApiError(error);
      setSourceError(!isRunCurrent(run)
        ? "原预览会话已变化，请重新读取并生成新的替换预览。未发送替换请求。"
        : apiError.status === 401
        ? "登录状态已变化，请重新进入素材库后读取。"
        : "章节或素材目录读取失败，未发送替换请求。请重新读取。 ");
    } finally {
      if (requestSequence === sequenceRef.current) setLoading(false);
    }
  }

  useEffect(() => {
    if (!open) {
      sourceControllerRef.current?.abort();
      operationControllerRef.current?.abort();
      return;
    }
    if (!canEnter) {
      setSourceError("当前账号暂不能进入此剧集，未发送替换请求。");
      return;
    }
    if (currentAttemptRef.current === null) void loadSources({ preserveSelection: newAssetId !== "" });
    return () => {
      sourceControllerRef.current?.abort();
      operationControllerRef.current?.abort();
    };
    // Reopening an attempt keeps its per-chapter receipts; a fresh read is explicit.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, epoch, canEnter]);

  useEffect(() => () => {
    sourceControllerRef.current?.abort();
    operationControllerRef.current?.abort();
  }, []);

  function selectNewAsset(id: string): void {
    setNewAssetId(id);
    setSelectedChapterIds(new Set());
    setConfirmationChecked(false);
    setRecoveryNotice(null);
    if (chapters === null || directory === null || id === "") {
      setPlan(null);
      setAssetSnapshot(null);
      return;
    }
    const oldSnapshot = findUniqueAsset(directory, asset.id, seriesId);
    const newSnapshot = findUniqueAsset(directory, id, seriesId);
    if (oldSnapshot === null || newSnapshot === null || id === asset.id) {
      setPlan(null);
      setAssetSnapshot(null);
      setSourceError("所选新素材身份无法唯一核对。");
      return;
    }
    setSourceError(null);
    setAssetSnapshot({ oldAsset: copyAssetRecord(oldSnapshot), newAsset: copyAssetRecord(newSnapshot) });
    setPlan(buildAssetReplacementPlan(chapters, seriesId, category, asset.id, id));
  }

  function toggleChapter(chapterId: string, checked: boolean): void {
    setSelectedChapterIds((previous) => {
      const next = new Set(previous);
      if (checked) next.add(chapterId);
      else next.delete(chapterId);
      return next;
    });
    setConfirmationChecked(false);
  }

  function updateChapterResult(attemptId: number, chapterId: string, result: ChapterResult): void {
    setCurrentAttempt((current) => current?.id === attemptId
      ? { ...current, results: { ...current.results, [chapterId]: result } }
      : current);
  }

  async function confirmReplacement(): Promise<void> {
    if (!canConfirm || submittingRef.current || plan?.status !== "ready" || selectedAsset === null || assetSnapshot === null) return;
    const selected = plan.chapters.filter((chapter) => chapter.status === "ready" && selectedChapterIds.has(chapter.chapterId));
    if (selected.length === 0) return;
    const port = services.assetReplacement;
    if (port === undefined) {
      setSourceError("当前服务未接入素材替换接口，未发送请求。");
      return;
    }
    const run = previewRunRef.current;
    const confirmedAssetSnapshot = assetSnapshot;

    submittingRef.current = true;
    setSubmitting(true);
    onBusyChange(epoch, true);
    setRecoveryNotice(null);
    const attemptId = sequenceRef.current + 1;
    sequenceRef.current = attemptId;
    const attempt: ReplacementAttempt = {
      id: attemptId,
      run,
      assetSnapshot: confirmedAssetSnapshot,
      oldAssetName: assetName(category, confirmedAssetSnapshot.oldAsset),
      newAssetId: selectedAsset.id,
      newAssetName: assetName(category, confirmedAssetSnapshot.newAsset),
      selected,
      results: newResults(selected),
    };
    setCurrentAttempt(attempt);
    const controller = new AbortController();
    operationControllerRef.current = controller;
    const requestEpoch = epoch;

    try {
      if (run === null || !run.isCurrent()) {
        for (const chapter of selected) {
          updateChapterResult(attemptId, chapter.chapterId, resultFor("not-sent", "原预览会话已变化，未发送本章请求；请重新读取并确认。"));
        }
        return;
      }

      for (const chapterPlan of selected) {
        if (controller.signal.aborted || !isCurrent(requestEpoch) || !run.isCurrent()) {
          if (!controller.signal.aborted && isCurrent(requestEpoch)) {
            updateChapterResult(attemptId, chapterPlan.chapterId, resultFor("not-sent", "原预览会话已变化，未发送本章请求；后续章节已停止。"));
          }
          break;
        }
        let freshChapters: Chapter[];
        let freshDirectory: AssetRecord[];
        try {
          [freshChapters, freshDirectory] = await Promise.all([
            services.listChapters(seriesId, controller.signal),
            readAssets(services, category, seriesId, controller.signal),
          ]);
        } catch (error) {
          if (!controller.signal.aborted) {
            reportUnauthorized(error, requestEpoch, run);
            updateChapterResult(attemptId, chapterPlan.chapterId, resultFor(
              "not-sent",
              run.isCurrent()
                ? "发送前重新读取失败，本章未发送；后续章节已停止。"
                : "原预览会话已变化，发送前读取结果未接受；本章未发送，后续章节已停止。",
            ));
          }
          break;
        }
        if (controller.signal.aborted || !isCurrent(requestEpoch) || !run.isCurrent()) {
          if (!controller.signal.aborted && isCurrent(requestEpoch)) {
            updateChapterResult(attemptId, chapterPlan.chapterId, resultFor("not-sent", "原预览会话已变化，发送前读取结果未接受；本章未发送，后续章节已停止。"));
          }
          break;
        }

        const oldAsset = findUniqueAsset(freshDirectory, asset.id, seriesId);
        const newAsset = findUniqueAsset(freshDirectory, attempt.newAssetId, seriesId);
        const chapterMatches = freshChapters.filter((candidate) => candidate.id === chapterPlan.chapterId && candidate.series_id === seriesId);
        if (oldAsset === null || newAsset === null || chapterMatches.length !== 1) {
          updateChapterResult(attemptId, chapterPlan.chapterId, resultFor("not-sent", "素材或章节身份已变化，本章未发送；请重新读取预览。"));
          break;
        }
        if (attempt.assetSnapshot === null
          || !assetSnapshotMatches(oldAsset, attempt.assetSnapshot.oldAsset)
          || !assetSnapshotMatches(newAsset, attempt.assetSnapshot.newAsset)) {
          updateChapterResult(attemptId, chapterPlan.chapterId, resultFor(
            "not-sent",
            "所选素材信息已变化，本章未发送；请重新读取预览并再次明确确认。",
          ));
          break;
        }
        if (!chapterContentMatches(chapterMatches[0], chapterPlan.baselineContent)) {
          updateChapterResult(attemptId, chapterPlan.chapterId, resultFor("not-sent", "本章内容已变化，本章未发送；请重新生成预览并确认。"));
          break;
        }
        if (!run.isCurrent() || !isCurrent(requestEpoch) || controller.signal.aborted) {
          if (!controller.signal.aborted && isCurrent(requestEpoch)) {
            updateChapterResult(attemptId, chapterPlan.chapterId, resultFor("not-sent", "原预览会话已变化，未发送本章请求；后续章节已停止。"));
          }
          break;
        }

        updateChapterResult(attemptId, chapterPlan.chapterId, resultFor("submitting", "单章请求正在发送。"));
        try {
          await run.replaceChapter(chapterPlan.chapterId, {
            old_asset_id: asset.id,
            new_asset_id: attempt.newAssetId,
            asset_type: toAssetReplacementType(category),
          }, controller.signal);
        } catch (error) {
          if (error instanceof AssetReplacementError) {
            const status = error.kind === "not-sent" ? "not-sent" : error.kind === "rejected" ? "rejected" : "unknown";
            updateChapterResult(attemptId, chapterPlan.chapterId, resultFor(status, error.message));
            if (error.status === 401) reportUnauthorized(error, requestEpoch, run);
          } else {
            updateChapterResult(attemptId, chapterPlan.chapterId, resultFor("unknown", "请求中断，章节替换结果尚未确认。"));
          }
          break;
        }

        updateChapterResult(attemptId, chapterPlan.chapterId, resultFor("acknowledged", "服务已确认；正在独立回读章节与素材目录。"));
        if (!run.isCurrent() || !isCurrent(requestEpoch)) {
          updateChapterResult(attemptId, chapterPlan.chapterId, resultFor(
            "acknowledged",
            "服务已确认；原预览会话已变化，未接受回读结果，后续章节已停止。",
            "unreadable",
          ));
          break;
        }
        let rereadChapters: Chapter[];
        let rereadDirectory: AssetRecord[];
        try {
          [rereadChapters, rereadDirectory] = await Promise.all([
            services.listChapters(seriesId, controller.signal),
            readAssets(services, category, seriesId, controller.signal),
          ]);
        } catch (error) {
          if (!controller.signal.aborted) {
            reportUnauthorized(error, requestEpoch, run);
            updateChapterResult(attemptId, chapterPlan.chapterId, resultFor(
              "acknowledged",
              run.isCurrent()
                ? "服务已确认，但独立回读失败；只可重新核对，不会重发请求。"
                : "服务已确认；原预览会话已变化，独立回读未接受，后续章节已停止。",
              "unreadable",
            ));
          }
          break;
        }
        if (controller.signal.aborted || !isCurrent(requestEpoch) || !run.isCurrent()) {
          if (!controller.signal.aborted && isCurrent(requestEpoch)) {
            updateChapterResult(attemptId, chapterPlan.chapterId, resultFor(
              "acknowledged",
              "服务已确认；原预览会话已变化，独立回读未接受，后续章节已停止。",
              "unreadable",
            ));
          }
          break;
        }
        onDirectoryUpdated(rereadDirectory, requestEpoch);
        const rereadChapter = rereadChapters.find((candidate) => candidate.id === chapterPlan.chapterId
          && candidate.series_id === seriesId);
        const rereadNewAssetIsUnique = hasUniqueAssetId(rereadDirectory, attempt.newAssetId, seriesId);
        if (rereadChapter === undefined || !rereadNewAssetIsUnique) {
          updateChapterResult(attemptId, chapterPlan.chapterId, resultFor(
            "acknowledged",
            "服务已确认，但章节或新素材无法唯一核对；后续章节已停止。",
            "unreadable",
          ));
          break;
        }
        if (!chapterContentMatches(rereadChapter, chapterPlan.expectedContent)) {
          updateChapterResult(attemptId, chapterPlan.chapterId, resultFor(
            "acknowledged",
            "服务已确认，但回读内容与预览不符；需要人工核对，后续章节已停止。",
            "changed",
          ));
          break;
        }
        updateChapterResult(attemptId, chapterPlan.chapterId, resultFor(
          "verified",
          "服务已确认，独立回读也与本章预览一致。",
          "expected",
        ));
      }
    } finally {
      onBusyChange(requestEpoch, false);
      if (operationControllerRef.current === controller) operationControllerRef.current = null;
      submittingRef.current = false;
      setSubmitting(false);
    }
  }

  async function recheckUnknown(): Promise<void> {
    const attempt = currentAttemptRef.current;
    if (attempt === null || submittingRef.current || !isCurrent(epoch)) return;
    const run = attempt.run;
    if (run === null || !run.isCurrent()) {
      setRecoveryNotice("原预览会话已变化；保留原结果，不读取新会话数据，也不重发。请新建预览并重新确认。");
      return;
    }
    const rows = attempt.selected.filter((chapter) => {
      const status = attempt.results[chapter.chapterId]?.status;
      return status === "unknown" || status === "acknowledged";
    });
    if (rows.length === 0) return;
    const controller = new AbortController();
    sourceControllerRef.current?.abort();
    sourceControllerRef.current = controller;
    const requestEpoch = epoch;
    setLoading(true);
    setRecoveryNotice("正在只读核对；这一步不会重发替换请求。 ");
    try {
      const [freshChapters, freshDirectory] = await Promise.all([
        services.listChapters(seriesId, controller.signal),
        readAssets(services, category, seriesId, controller.signal),
      ]);
      if (controller.signal.aborted || !isCurrent(requestEpoch) || !run.isCurrent()) {
        if (!controller.signal.aborted && isCurrent(requestEpoch)) {
          setRecoveryNotice("原预览会话已变化；未接受本次只读结果，原逐章状态保留。请新建预览并重新确认。");
        }
        return;
      }
      setChapters(freshChapters);
      setDirectory(freshDirectory);
      onDirectoryUpdated(freshDirectory, requestEpoch);
      for (const chapterPlan of rows) {
        const chapter = freshChapters.find((candidate) => candidate.id === chapterPlan.chapterId
          && candidate.series_id === seriesId);
        const newAssetIsUnique = hasUniqueAssetId(freshDirectory, attempt.newAssetId, seriesId);
        let observation: RecoveryObservation;
        let text: string;
        if (chapter === undefined) {
          observation = "unreadable";
          text = "当前章节无法读取；原请求结果仍按原状态保留。";
        } else if (chapterContentMatches(chapter, chapterPlan.expectedContent)) {
          observation = newAssetIsUnique ? "expected" : "unreadable";
          text = newAssetIsUnique
            ? "当前引用符合预览预期；未知请求仍未获得服务器最终确认。"
            : "章节引用符合预期，但新素材目录无法唯一核对；原请求结果仍未确认。";
        } else if (chapterContentMatches(chapter, chapterPlan.baselineContent)) {
          observation = "baseline";
          text = "当前内容仍符合预览前状态；原请求仍可能稍后完成，不能自动重发。";
        } else {
          observation = "changed";
          text = "当前内容与预览和预期均不同，需要人工核对。";
        }
        const prior = attempt.results[chapterPlan.chapterId];
        if (prior !== undefined) {
          const acknowledgedReadbackMatches = prior.status === "acknowledged" && observation === "expected";
          const acknowledgedDetail = observation === "expected"
            ? "服务已确认，当前章节内容与预期一致；只读回读已完成。"
            : observation === "baseline"
              ? "服务已确认，但当前内容仍符合请求前预览；当前结果与回执不一致，需要人工核对。"
              : observation === "changed"
                ? "服务已确认，但当前内容与预览和预期均不同，需要人工核对。"
                : "服务已确认，但章节或素材无法唯一核对；原回执保留。";
          updateChapterResult(attempt.id, chapterPlan.chapterId, {
            ...prior,
            status: acknowledgedReadbackMatches ? "verified" : prior.status,
            detail: prior.status === "acknowledged" ? acknowledgedDetail : text,
            observation,
          });
        }
      }
      setRecoveryNotice("只读核对已完成。未知请求的状态仍保留，未发送任何新写请求。 ");
    } catch (error) {
      if (!controller.signal.aborted) {
        reportUnauthorized(error, requestEpoch, run);
        setRecoveryNotice(run.isCurrent()
          ? "只读核对失败；原逐章状态已保留，未发送新写请求。 "
          : "原预览会话已变化；未接受只读失败结果，原逐章状态保留。 ");
      }
    } finally {
      if (sourceControllerRef.current === controller) sourceControllerRef.current = null;
      setLoading(false);
    }
  }

  async function startNewPreview(): Promise<void> {
    if (submittingRef.current) return;
    if (currentAttempt !== null) setAttemptHistory((previous) => [...previous, currentAttempt]);
    setCurrentAttempt(null);
    setNewAssetId("");
    setPlan(null);
    setAssetSnapshot(null);
    setSelectedChapterIds(new Set());
    setConfirmationChecked(false);
    setRecoveryNotice(null);
    previewRunRef.current = null;
    await loadSources({ captureNewRun: true });
  }

  if (!open) return null;

  const historyRows = attemptHistory;
  const sourceLabel = `${categoryLabel(category)}「${oldAssetName}」`;

  return (
    <div className="asset-replacement-overlay">
      <section
        aria-labelledby="asset-replacement-heading"
        aria-modal="true"
        className="asset-replacement-dialog"
        ref={focus.dialogRef}
        role="dialog"
        tabIndex={-1}
      >
        <header className="asset-replacement-header">
          <div>
            <p className="eyebrow">{seriesName} · 逐章替换</p>
            <h2 id="asset-replacement-heading">替换章节引用：{oldAssetName}</h2>
          </div>
          <button aria-label="关闭素材替换" className="asset-replacement-close" onClick={focus.dismiss} type="button">×</button>
        </header>

        <div className="asset-replacement-body">
          <p className="asset-replacement-boundary">
            仅替换所选章节的{categoryLabel(category)}引用；每章单独提交并回读，不构成全剧事务。服务可能清理已无引用的旧素材。
          </p>
          {!canEnter && <p className="asset-replacement-error" role="alert">当前账号暂不能进入此剧集，未发送替换请求。</p>}
          {loading && <p className="asset-replacement-progress" role="status">正在读取章节与素材目录…</p>}
          {sourceError !== null && <p className="asset-replacement-error" role="alert">{sourceError}</p>}

          {historyRows.length > 0 && (
            <section aria-label="先前替换记录" className="asset-replacement-history">
              <h3>先前替换记录</h3>
              {historyRows.map((attempt) => (
                <div key={attempt.id}>
                  <strong>{categoryLabel(category)}「{attempt.oldAssetName}」 → {attempt.newAssetName}</strong>
                  <ul>
                    {attempt.selected.map((chapter) => (
                      <li key={chapter.chapterId}>
                        {chapter.title}（{chapter.chapterId}）：{statusLabel(attempt.results[chapter.chapterId]?.status ?? "not-submitted")}
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </section>
          )}

          {currentAttempt === null ? (
            <>
              <label className="asset-replacement-new-asset">
                新{categoryLabel(category)}
                <select
                  aria-label={`新${categoryLabel(category)}`}
                  disabled={loading || directory === null || candidates.length === 0}
                  onChange={(event) => selectNewAsset(event.currentTarget.value)}
                  value={newAssetId}
                >
                  <option value="">请选择同剧同类素材</option>
                  {candidates.map((candidate) => (
                    <option key={candidate.id} value={candidate.id}>
                      {assetName(category, candidate)}（{candidate.id}）
                    </option>
                  ))}
                </select>
              </label>
              {directory !== null && candidates.length === 0 && (
                <p className="asset-replacement-empty" role="status">当前剧集没有可选的同类新素材。</p>
              )}
              {plan?.status === "unreadable" && <p className="asset-replacement-error" role="alert">{plan.reason}</p>}
              {plan?.status === "ready" && (
                <section aria-label="逐章替换预览" className="asset-replacement-preview">
                  <div className="asset-replacement-preview-heading">
                    <div>
                      <h3>替换预览</h3>
                      <p>{sourceLabel} → {selectedAsset === null ? "未选择新素材" : `${categoryLabel(category)}「${assetName(category, selectedAsset)}」`}</p>
                    </div>
                    <div>
                      <button
                        className="secondary-button"
                        disabled={loading || readyChapters.length === 0}
                        onClick={() => setSelectedChapterIds(new Set(readyChapters.map((chapter) => chapter.chapterId)))}
                        type="button"
                      >选中全部可核对章节</button>
                      <button
                        className="secondary-button"
                        disabled={loading}
                        onClick={() => void loadSources({ preserveSelection: true, captureNewRun: true })}
                        type="button"
                      >
                        重新读取预览
                      </button>
                    </div>
                  </div>
                  {plan.chapters.length === 0 ? (
                    <p className="asset-replacement-empty" role="status">最新章节中没有发现旧素材引用。</p>
                  ) : (
                    <div className="asset-replacement-chapters">
                      {plan.chapters.map((chapter) => (
                        <article aria-label={`${chapter.title}替换预览`} className="asset-replacement-chapter" key={chapter.chapterId}>
                          <header>
                            {chapter.status === "ready" ? (
                              <label>
                                <input
                                  aria-label={`选择章节 ${chapter.title}`}
                                  checked={selectedChapterIds.has(chapter.chapterId)}
                                  onChange={(event) => toggleChapter(chapter.chapterId, event.currentTarget.checked)}
                                  type="checkbox"
                                />
                                <strong>{chapter.title}</strong>
                              </label>
                            ) : <strong>{chapter.title} · 无法核对</strong>}
                            <span>章节 ID：{chapter.chapterId}</span>
                          </header>
                          {chapter.status === "unreadable" ? (
                            <p className="asset-replacement-error" role="note">{chapter.reason}</p>
                          ) : (
                            <>
                              <p>预计影响 {chapter.frames.length} 个镜头、{chapter.referenceCount} 处引用。</p>
                              <ol>
                                {chapter.frames.map((frame) => (
                                  <li key={frame.framePosition}>
                                    <strong>第 {frame.framePosition} 镜</strong>（旧引用 {frame.referenceCount} 处）
                                    <p>{contentText(frame.text)}</p>
                                    <small>
                                      预览文字：{contentText(frame.originalText)}；{categoryLabel(category)}引用：
                                      {oldAssetName}（{asset.id}） → {selectedAsset === null ? "未选择" : `${assetName(category, selectedAsset)}（${selectedAsset.id}）`}。
                                    </small>
                                  </li>
                                ))}
                              </ol>
                            </>
                          )}
                        </article>
                      ))}
                    </div>
                  )}
                </section>
              )}
            </>
          ) : (
            <section aria-label="逐章提交结果" className="asset-replacement-results">
              <h3>{categoryLabel(category)}「{currentAttempt.oldAssetName}」 → {currentAttempt.newAssetName}</h3>
              <p>每章按章节 ID 逐个提交；遇到拒绝、未知或回读异常时停止后续章节。</p>
              <ol>
                {currentAttempt.selected.map((chapter) => {
                  const result = currentAttempt.results[chapter.chapterId] ?? resultFor("not-submitted", "尚未发送。");
                  return (
                    <li aria-label={`${chapter.title}提交结果`} key={chapter.chapterId}>
                      <strong>{chapter.title}（{chapter.chapterId}）：{statusLabel(result.status)}</strong>
                      <p>{result.detail}</p>
                    </li>
                  );
                })}
              </ol>
              {currentAttempt.selected.some((chapter) => currentAttempt.results[chapter.chapterId]?.status === "unknown"
                || currentAttempt.results[chapter.chapterId]?.status === "acknowledged") && (
                <button className="secondary-button" disabled={loading || submitting} onClick={() => void recheckUnknown()} type="button">
                  重新核对（只读，不重发）
                </button>
              )}
              {recoveryNotice !== null && <p role="status">{recoveryNotice}</p>}
              <button className="secondary-button" disabled={loading || submitting} onClick={() => void startNewPreview()} type="button">
                新建替换预览
              </button>
            </section>
          )}

          {currentAttempt === null && plan?.status === "ready" && plan.chapters.length > 0 && (
            <label className="asset-replacement-confirm-check">
              <input
                checked={confirmationChecked}
                disabled={loading || submitting || selectedPlans.length === 0}
                onChange={(event) => setConfirmationChecked(event.currentTarget.checked)}
                type="checkbox"
              />
              我已核对所选章节、旧新素材和预计变化，并理解各章可能部分完成。
            </label>
          )}
        </div>

        <footer className="asset-replacement-footer">
          {currentAttempt === null ? (
            <>
              <button className="secondary-button" disabled={submitting} onClick={focus.dismiss} type="button">取消</button>
              <button className="primary-button" disabled={!canConfirm} onClick={() => void confirmReplacement()} type="button">
                确认并逐章替换（{selectedPlans.length} 章）
              </button>
            </>
          ) : (
            <button className="secondary-button" onClick={focus.dismiss} type="button">关闭并保留本次记录</button>
          )}
        </footer>
      </section>
    </div>
  );
}
