import { useEffect, useRef, useState } from "react";
import type { Chapter, Series } from "../../shared/api/contracts";
import { ApiError, isAbortError } from "../../shared/api/errors";
import { normalizeApiError, type WorkspaceServices } from "../../shared/api/services";
import type { ImportSeriesInput, SeriesExchangePort } from "../../shared/api/seriesExchange";
import {
  getFileErrorMessage,
  isUnknownWriteOutcome,
  prepareMarkdownFiles,
  readJsonSeriesExport,
  readMarkdownChapterFiles,
  SeriesExchangeFileError,
  type JsonSeriesExportV1,
  type MarkdownChapterFile,
} from "./fileImport";
import "./seriesExchange.css";

type ItemStatus = "ready" | "importing" | "saved" | "failed" | "unknown";
type ImportMode = "json" | "markdown";
type MarkdownTargetMode = "new" | "existing";

interface MarkdownItem extends MarkdownChapterFile {
  status: ItemStatus;
  chapterId: string | null;
  error: string | null;
}

interface JsonSelection {
  fileName: string;
  data: JsonSeriesExportV1;
}

interface ImportResult {
  seriesId: string;
  name: string;
  chapterIds: string[];
  kind: ImportMode;
}

interface SeriesExchangeDialogProps {
  active: boolean;
  open: boolean;
  services: WorkspaceServices;
  port: SeriesExchangePort;
  series: readonly Series[];
  userId: string;
  onClose(): void;
  onUnauthorized(): void;
  onImported?(result: ImportResult): void;
}

interface ActiveOperation {
  controller: AbortController;
  generation: number;
  phase: "read" | "json-write" | "series-create" | "chapter-write";
  itemKey?: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function updateMarkdownItem(
  items: MarkdownItem[],
  key: string,
  update: Partial<MarkdownItem>,
): MarkdownItem[] {
  return items.map((item) => item.key === key ? { ...item, ...update } : item);
}

function getExportedSeriesName(data: JsonSeriesExportV1): string {
  return isRecord(data.series) && typeof data.series.name === "string"
    ? data.series.name
    : "导入的剧集";
}

function hasValidId(value: unknown): value is { id: string; name: string } {
  return isRecord(value)
    && typeof value.id === "string"
    && value.id.trim() !== ""
    && typeof value.name === "string"
    && value.name.trim() !== "";
}

function getFrameText(chapter: Chapter): string | null {
  if (!Array.isArray(chapter.content) || chapter.content.length !== 1) {
    return null;
  }
  const frame = chapter.content[0];
  return isRecord(frame) && typeof frame.text === "string" ? frame.text : null;
}

function matchingNewChapters(
  chapters: readonly Chapter[],
  baselineIds: ReadonlySet<string>,
  item: MarkdownItem,
): Chapter[] {
  return chapters.filter((chapter) => !baselineIds.has(chapter.id)
    && chapter.title === item.title
    && getFrameText(chapter) === item.text);
}

function descriptionForUnknown(error: unknown): string {
  const normalized = normalizeApiError(error);
  if (normalized.status !== null && normalized.status >= 500) {
    return "服务器响应异常，正在核对保存结果。";
  }
  if (normalized.kind === "invalid-response") {
    return "服务器回执无法识别，正在核对保存结果。";
  }
  return "连接中断，正在核对保存结果。";
}

function requestErrorMessage(error: unknown, fallback: string): string {
  const normalized = normalizeApiError(error);
  if (normalized.status === 401) return "登录状态已失效，请重新登录后再试。";
  if (normalized.status === 403) return normalized.detail ?? "当前账号没有执行此操作的权限。";
  if (normalized.kind === "network") return "无法连接本地服务，请确认服务正在运行后重试。";
  if (normalized.kind === "timeout") return "请求超时，请稍后重试。";
  if (normalized.kind === "invalid-response") return "服务返回的数据格式无法识别，请检查服务状态后重试。";
  return normalized.detail ?? normalized.message ?? fallback;
}

function newEmptyState() {
  return {
    mode: null as ImportMode | null,
    jsonSelection: null as JsonSelection | null,
    jsonStatus: "ready" as ItemStatus,
    jsonError: null as string | null,
    markdownItems: [] as MarkdownItem[],
    markdownTargetMode: null as MarkdownTargetMode | null,
    selectedSeriesId: "",
    newSeriesName: "",
    targetSeriesId: null as string | null,
    targetSeriesName: null as string | null,
    baselineChapterIds: [] as string[],
    baselineChapterIdsCaptured: false,
    seriesBaselineIds: [] as string[],
    seriesBaselineIdsCaptured: false,
    seriesCreationStatus: "ready" as ItemStatus,
    seriesCreationError: null as string | null,
    notice: null as string | null,
  };
}

export function SeriesExchangeDialog({
  active,
  open,
  services,
  port,
  series,
  userId,
  onClose,
  onUnauthorized,
  onImported,
}: SeriesExchangeDialogProps) {
  const [state, setState] = useState(newEmptyState);
  const [busy, setBusy] = useState(false);
  const operationRef = useRef<ActiveOperation | null>(null);
  const generationRef = useRef(0);
  const scopeRef = useRef({ userId, services, active, open });
  const appliedScopeRef = useRef({ userId, services });
  scopeRef.current = { userId, services, active, open };
  const visibleSeries = series.filter((item) => item.can_enter);
  function beginOperation(phase: ActiveOperation["phase"]): ActiveOperation {
    const current = operationRef.current;
    current?.controller.abort();
    const operation: ActiveOperation = {
      controller: new AbortController(),
      generation: ++generationRef.current,
      phase,
    };
    operationRef.current = operation;
    setBusy(true);
    return operation;
  }

  function isCurrent(operation: ActiveOperation): boolean {
    return operationRef.current === operation
      && operation.generation === generationRef.current
      && !operation.controller.signal.aborted
      && scopeRef.current.userId === userId
      && scopeRef.current.services === services
      && scopeRef.current.active
      && scopeRef.current.open;
  }

  function finishOperation(operation: ActiveOperation): void {
    if (operationRef.current === operation) {
      operationRef.current = null;
      setBusy(false);
    }
  }

  function stopCurrentOperation(message: string): void {
    const operation = operationRef.current;
    if (operation === null) return;
    operationRef.current = null;
    generationRef.current += 1;
    operation.controller.abort();
    setBusy(false);
    if (operation.phase === "json-write") {
      setState((current) => ({ ...current, jsonStatus: "unknown", jsonError: message, notice: message }));
    } else if (operation.phase === "chapter-write" && operation.itemKey !== undefined) {
      const key = operation.itemKey;
      setState((current) => ({
        ...current,
        markdownItems: updateMarkdownItem(current.markdownItems, key, {
          status: "unknown",
          error: message,
        }),
        notice: message,
      }));
    } else if (operation.phase === "series-create") {
      setState((current) => ({
        ...current,
        seriesCreationStatus: "unknown",
        seriesCreationError: message,
        notice: message,
      }));
    }
  }

  useEffect(() => {
    const previous = appliedScopeRef.current;
    const changed = previous.userId !== userId || previous.services !== services;
    appliedScopeRef.current = { userId, services };
    if (changed) {
      stopCurrentOperation("账号或服务范围已切换；已提交结果需要重新核对。");
      setState(newEmptyState());
    } else if (!active || !open) {
      stopCurrentOperation("导入已停止；当前文件结果需要核对后再继续。");
    }
  }, [active, open, services, userId]);

  useEffect(() => () => {
    const operation = operationRef.current;
    operationRef.current = null;
    generationRef.current += 1;
    operation?.controller.abort();
  }, []);

  async function handleFileSelection(mode: ImportMode, files: FileList | null) {
    if (files === null || files.length === 0) return;
    const operation = beginOperation("read");
    setState({
      ...newEmptyState(),
      mode,
      markdownTargetMode: null,
      notice: null,
    });
    try {
      const selectedNames = Array.from(files, (file) => file.name.toLowerCase());
      if (selectedNames.some((name) => name.endsWith(".json"))
        && selectedNames.some((name) => name.endsWith(".md"))) {
        throw new SeriesExchangeFileError("JSON 和 Markdown 不能混合导入，请一次只选择一种格式。");
      }
      if (mode === "json") {
        if (files.length !== 1) {
          throw new SeriesExchangeFileError("JSON 导入只能选择一个文件。");
        }
        const file = files[0]!;
        const data = await readJsonSeriesExport(file);
        if (!isCurrent(operation)) return;
        setState((current) => ({
          ...current,
          mode,
          jsonSelection: { fileName: file.name, data },
          jsonStatus: "ready",
          jsonError: null,
          notice: null,
        }));
      } else {
        const prepared = prepareMarkdownFiles(files);
        const chapters = await readMarkdownChapterFiles(prepared);
        if (!isCurrent(operation)) return;
        setState((current) => ({
          ...current,
          mode,
          markdownTargetMode: null,
          newSeriesName: chapters[0]?.title ?? "",
          markdownItems: chapters.map((item) => ({
            ...item,
            status: "ready",
            chapterId: null,
            error: null,
          })),
          targetSeriesId: null,
          targetSeriesName: null,
          baselineChapterIds: [],
          seriesCreationStatus: "ready",
          seriesCreationError: null,
          notice: null,
        }));
      }
    } catch (error) {
      if (!isCurrent(operation)) return;
      setState((current) => ({
        ...current,
        mode,
        jsonSelection: mode === "json" ? null : current.jsonSelection,
        jsonError: mode === "json" ? getFileErrorMessage(error) : current.jsonError,
        markdownItems: mode === "markdown" ? [] : current.markdownItems,
        notice: getFileErrorMessage(error),
      }));
    } finally {
      finishOperation(operation);
    }
  }

  function moveMarkdownItem(key: string, offset: -1 | 1) {
    setState((current) => {
      const index = current.markdownItems.findIndex((item) => item.key === key);
      const nextIndex = index + offset;
      if (index < 0 || nextIndex < 0 || nextIndex >= current.markdownItems.length
        || current.markdownItems.some((item) => item.status !== "ready")) {
        return current;
      }
      const next = [...current.markdownItems];
      [next[index], next[nextIndex]] = [next[nextIndex]!, next[index]!];
      return { ...current, markdownItems: next };
    });
  }

  async function listSeriesForReconciliation(signal: AbortSignal): Promise<Series[]> {
    return services.listSeries(signal);
  }

  async function reconcileJsonImport(operation: ActiveOperation): Promise<void> {
    // A new same-name row cannot prove which request created it. Until the full
    // imported payload can be matched to a server export, keep this outcome unknown.
    await listSeriesForReconciliation(operation.controller.signal);
  }

  async function importJson() {
    if (state.jsonSelection === null || busy || state.jsonStatus === "saved" || state.jsonStatus === "unknown") {
      return;
    }
    const { data } = state.jsonSelection;
    const targetName = state.newSeriesName.trim() || getExportedSeriesName(data);
    const operation = beginOperation("json-write");
    setState((current) => ({ ...current, jsonStatus: "importing", jsonError: null, notice: null }));
    try {
      const input: ImportSeriesInput = { name: targetName, data };
      operation.phase = "json-write";
      let imported: unknown;
      try {
        imported = await port.importSeries(input, operation.controller.signal);
      } catch (error) {
        if (!isCurrent(operation)) return;
        const normalized = normalizeApiError(error);
        if (normalized.status === 401) onUnauthorized();
        if (isUnknownWriteOutcome(error)) {
          setState((current) => ({
            ...current,
            jsonStatus: "unknown",
            jsonError: descriptionForUnknown(error),
            notice: descriptionForUnknown(error),
          }));
          await reconcileJsonImport(operation).catch((reconcileError) => {
            if (isCurrent(operation) && normalizeApiError(reconcileError).status === 401) onUnauthorized();
          });
          if (!isCurrent(operation)) return;
          return;
        }
        setState((current) => ({
          ...current,
          jsonStatus: "failed",
          jsonError: requestErrorMessage(error, "JSON 导入失败，可确认后重试。"),
          notice: requestErrorMessage(error, "JSON 导入失败，可确认后重试。"),
        }));
        return;
      }

      if (!isCurrent(operation)) return;
      if (!hasValidId(imported)) {
        setState((current) => ({
          ...current,
          jsonStatus: "unknown",
          jsonError: "服务器回执无法识别，正在核对保存结果。",
          notice: "服务器回执无法识别，正在核对保存结果。",
        }));
        await reconcileJsonImport(operation).catch((reconcileError) => {
          if (isCurrent(operation) && normalizeApiError(reconcileError).status === 401) onUnauthorized();
        });
        if (!isCurrent(operation)) return;
        return;
      }
      setState((current) => ({ ...current, jsonStatus: "saved", jsonError: null, notice: "已导入剧集《" + imported.name + "》。" }));
      onImported?.({ seriesId: imported.id, name: imported.name, chapterIds: [], kind: "json" });
    } catch (error) {
      if (!isCurrent(operation)) return;
      const normalized = normalizeApiError(error);
      if (normalized.status === 401) onUnauthorized();
      setState((current) => ({
        ...current,
        jsonStatus: "failed",
          jsonError: requestErrorMessage(error, "JSON 导入失败，可确认后重试。"),
          notice: requestErrorMessage(error, "JSON 导入失败，可确认后重试。"),
      }));
    } finally {
      finishOperation(operation);
    }
  }

  async function ensureMarkdownTarget(operation: ActiveOperation): Promise<{ id: string; name: string } | null> {
    let targetId = state.targetSeriesId;
    let targetName = state.targetSeriesName;
    const targetMode = state.markdownTargetMode;

    if (targetId !== null && targetName !== null) {
      if (targetMode === "existing" && !visibleSeries.some((item) => item.id === targetId)) {
        setState((current) => ({ ...current, notice: "所选剧集当前不可进入，请重新选择。" }));
        return null;
      }
      return { id: targetId, name: targetName };
    }

    if (targetMode === "existing") {
      const selected = visibleSeries.find((item) => item.id === state.selectedSeriesId);
      if (selected === undefined) {
        setState((current) => ({ ...current, notice: "请先选择一个可进入的剧集。" }));
        return null;
      }
      targetId = selected.id;
      targetName = selected.name;
      setState((current) => ({ ...current, targetSeriesId: targetId, targetSeriesName: targetName }));
      return { id: targetId, name: targetName };
    }

    if (targetMode !== "new" || services.createSeries === undefined) {
      setState((current) => ({ ...current, notice: "当前服务不支持创建剧集。" }));
      return null;
    }
    const name = state.newSeriesName.trim();
    if (name === "") {
      setState((current) => ({ ...current, notice: "请填写新剧集名称。" }));
      return null;
    }

    if (state.seriesCreationStatus === "unknown") {
      await listSeriesForReconciliation(operation.controller.signal);
      if (!isCurrent(operation)) return null;
      setState((current) => ({
        ...current,
        notice: "新剧集创建结果仍无法确认来源；不会选取同名剧集或继续导入章节。请在剧集列表核实后重新选择目标。",
      }));
      return null;
    }

    let baseline = new Set(state.seriesBaselineIds);
    if (!state.seriesBaselineIdsCaptured) {
      const before = await listSeriesForReconciliation(operation.controller.signal);
      if (!isCurrent(operation)) return null;
      baseline = new Set(before.map((item) => item.id));
      setState((current) => ({ ...current, seriesBaselineIds: [...baseline], seriesBaselineIdsCaptured: true }));
    }
    operation.phase = "series-create";
    setState((current) => ({ ...current, seriesCreationStatus: "importing", seriesCreationError: null }));
    try {
      const created = await services.createSeries({
        name,
        description: "从 Markdown 导入",
        image_url: null,
        style_prompt_id: null,
      }, operation.controller.signal);
      if (!isCurrent(operation)) return null;
      if (!hasValidId(created)) {
        throw new ApiError("invalid-response", "创建剧集回执格式无效。");
      }
      setState((current) => ({
        ...current,
        targetSeriesId: created.id,
        targetSeriesName: created.name,
        seriesCreationStatus: "saved",
        seriesCreationError: null,
        notice: null,
      }));
      operation.phase = "read";
      onImported?.({ seriesId: created.id, name: created.name, chapterIds: [], kind: "markdown" });
      return { id: created.id, name: created.name };
    } catch (error) {
      if (!isCurrent(operation)) return null;
      const normalized = normalizeApiError(error);
      if (normalized.status === 401) onUnauthorized();
      if (isUnknownWriteOutcome(error)) {
        setState((current) => ({
          ...current,
          seriesCreationStatus: "unknown",
          seriesCreationError: descriptionForUnknown(error),
          seriesBaselineIds: [...baseline],
          seriesBaselineIdsCaptured: true,
          notice: descriptionForUnknown(error),
        }));
      } else {
        setState((current) => ({
          ...current,
          seriesCreationStatus: "failed",
          seriesCreationError: requestErrorMessage(error, "新剧集创建失败，可确认后重试。"),
          notice: requestErrorMessage(error, "新剧集创建失败，可确认后重试。"),
        }));
      }
      return null;
    }
  }

  async function reconcileUnknownChapters(
    operation: ActiveOperation,
    target: { id: string; name: string },
    initialIds: Set<string>,
    items: MarkdownItem[],
  ): Promise<{ ids: Set<string>; items: MarkdownItem[]; unresolved: boolean }> {
    const unknownItems = items.filter((item) => item.status === "unknown");
    if (unknownItems.length === 0) return { ids: initialIds, items, unresolved: false };
    let currentItems = items;
    const chapters = await services.listChapters(target.id, operation.controller.signal);
    if (!isCurrent(operation)) return { ids: initialIds, items, unresolved: true };
    const nextIds = new Set(initialIds);
    let unresolved = false;
    for (const item of unknownItems) {
      const matches = matchingNewChapters(chapters, nextIds, item);
      if (matches.length !== 1) {
        unresolved = true;
        continue;
      }
      const chapter = matches[0]!;
      nextIds.add(chapter.id);
      currentItems = updateMarkdownItem(currentItems, item.key, {
        status: "saved",
        chapterId: chapter.id,
        error: null,
      });
      onImported?.({
        seriesId: target.id,
        name: target.name,
        chapterIds: [chapter.id],
        kind: "markdown",
      });
    }
    const resolvedItems = currentItems.filter((item) => item.status === "saved" && item.chapterId !== null);
    setState((current) => {
      let nextItems = current.markdownItems;
      for (const item of resolvedItems) {
        nextItems = updateMarkdownItem(nextItems, item.key, {
          status: "saved",
          chapterId: item.chapterId,
          error: null,
        });
      }
      return {
        ...current,
        markdownItems: nextItems,
        baselineChapterIds: [...nextIds],
        baselineChapterIdsCaptured: true,
        notice: unresolved ? "部分文件的保存结果仍无法唯一确认；不会自动重发，请先检查章节列表。" : current.notice,
      };
    });
    return { ids: nextIds, items: currentItems, unresolved };
  }

  async function runMarkdownImport() {
    if (busy || state.markdownItems.length === 0 || services.createChapter === undefined) {
      return;
    }
    const operation = beginOperation("read");
    setState((current) => ({ ...current, notice: null }));
    try {
      const target = await ensureMarkdownTarget(operation);
      if (!isCurrent(operation) || target === null) return;

      let baselineIds = new Set(state.baselineChapterIds);
      const currentChapters = await services.listChapters(target.id, operation.controller.signal);
      if (!isCurrent(operation)) return;
      if (!state.baselineChapterIdsCaptured) {
        baselineIds = new Set(currentChapters.map((chapter) => chapter.id));
        setState((current) => ({ ...current, baselineChapterIds: [...baselineIds], baselineChapterIdsCaptured: true }));
      } else {
        currentChapters.forEach((chapter) => {
          if (state.markdownItems.some((item) => item.status === "saved" && item.chapterId === chapter.id)) {
            baselineIds.add(chapter.id);
          }
        });
      }

      let workingItems = state.markdownItems;
      const reconciled = await reconcileUnknownChapters(operation, target, baselineIds, workingItems);
      if (!isCurrent(operation)) return;
      baselineIds = reconciled.ids;
      workingItems = reconciled.items;
      if (reconciled.unresolved) return;

      const createQueue = [...workingItems].reverse().filter((item) => item.status === "ready" || item.status === "failed");
      if (createQueue.length === 0) {
        setState((current) => ({ ...current, notice: "所选文件均已保存；没有重复提交。" }));
        return;
      }

      for (const item of createQueue) {
        if (!isCurrent(operation)) return;
        operation.phase = "chapter-write";
        operation.itemKey = item.key;
        setState((current) => ({
          ...current,
          markdownItems: updateMarkdownItem(current.markdownItems, item.key, {
            status: "importing",
            error: null,
          }),
          notice: null,
        }));
        try {
          const saved = await services.createChapter(target.id, {
            title: item.title,
            content: [{ text: item.text }],
            order: 0,
          }, operation.controller.signal);
          if (!isCurrent(operation)) return;
          if (!isRecord(saved) || typeof saved.id !== "string" || saved.id.trim() === "") {
            throw new ApiError("invalid-response", "服务器没有返回章节编号。");
          }
          baselineIds.add(saved.id);
          setState((current) => ({
            ...current,
            markdownItems: updateMarkdownItem(current.markdownItems, item.key, {
              status: "saved",
              chapterId: saved.id,
              error: null,
            }),
            baselineChapterIds: [...baselineIds],
            baselineChapterIdsCaptured: true,
            notice: null,
          }));
          onImported?.({
            seriesId: target.id,
            name: target.name,
            chapterIds: [saved.id],
            kind: "markdown",
          });
        } catch (error) {
          if (!isCurrent(operation)) return;
          const normalized = normalizeApiError(error);
          if (normalized.status === 401) onUnauthorized();
          const unknown = isUnknownWriteOutcome(error);
          const message = unknown ? descriptionForUnknown(error) : normalized.detail ?? normalized.message;
          setState((current) => ({
            ...current,
            markdownItems: updateMarkdownItem(current.markdownItems, item.key, {
              status: unknown ? "unknown" : "failed",
              error: message,
            }),
            notice: unknown ? message : "《" + item.title + "》未保存；已暂停后续文件。" + message,
          }));
          if (unknown) {
            const verified = await reconcileUnknownChapters(operation, target, baselineIds, [
              { ...item, status: "unknown", error: message, chapterId: null },
            ]).catch((reconcileError) => {
              if (isCurrent(operation) && normalizeApiError(reconcileError).status === 401) onUnauthorized();
              return null;
            });
            if (!isCurrent(operation)) return;
            if (verified !== null && !verified.unresolved) {
              baselineIds = verified.ids;
            } else {
              return;
            }
          } else {
            operation.phase = "read";
            return;
          }
        }
      }

      if (!isCurrent(operation)) return;
      operation.phase = "read";
      try {
        await services.listChapters(target.id, operation.controller.signal);
        if (!isCurrent(operation)) return;
        setState((current) => ({ ...current, notice: "导入结果已保存，章节目录已刷新。" }));
      } catch (error) {
        if (!isCurrent(operation)) return;
        const normalized = normalizeApiError(error);
        if (normalized.status === 401) onUnauthorized();
        setState((current) => ({ ...current, notice: "导入结果已保存，但章节目录刷新失败；不会重复提交文件。" }));
      }
    } catch (error) {
      if (!isCurrent(operation) || isAbortError(error)) return;
      const normalized = normalizeApiError(error);
      if (normalized.status === 401) onUnauthorized();
      setState((current) => ({
        ...current,
        notice: requestErrorMessage(error, "无法读取现有章节，已暂停导入。"),
      }));
    } finally {
      finishOperation(operation);
    }
  }

  function closeDialog() {
    stopCurrentOperation("导入已停止；当前文件结果需要核对后再继续。");
    onClose();
  }

  if (!open || !active) {
    return null;
  }

  const canUseMarkdown = services.createChapter !== undefined;
  const hasSavedMarkdown = state.markdownItems.some((item) => item.status === "saved");
  const allMarkdownSaved = state.markdownItems.length > 0 && state.markdownItems.every((item) => item.status === "saved");
  const mayReorder = !busy && state.markdownItems.every((item) => item.status === "ready");
  const hasRetryableMarkdown = state.markdownItems.some((item) => item.status === "ready" || item.status === "failed");
  const hasUnknownMarkdown = state.markdownItems.some((item) => item.status === "unknown");
  const targetCreationUnknown = state.seriesCreationStatus === "unknown";
  const hasUnknownWrite = hasUnknownMarkdown || targetCreationUnknown;
  const hasPersistedResult = state.jsonStatus === "saved"
    || state.jsonStatus === "unknown"
    || targetCreationUnknown
    || hasSavedMarkdown
    || state.targetSeriesId !== null;

  return (
    <div className="series-exchange-backdrop" role="presentation">
      <section
        aria-labelledby="series-exchange-title"
        aria-modal="true"
        className="series-exchange-dialog"
        role="dialog"
      >
        <header className="series-exchange-header">
          <div>
            <p className="eyebrow">剧集数据交换</p>
            <h2 id="series-exchange-title">导入剧集或章节</h2>
          </div>
          <button aria-label="关闭导入导出窗口" className="series-exchange-close" onClick={closeDialog} type="button">
            ×
          </button>
        </header>

        <div className="series-exchange-body">
          <div className="series-exchange-format-tabs" role="group" aria-label="导入格式">
            <button
              aria-pressed={state.mode === "json"}
              className={state.mode === "json" ? "active" : ""}
              disabled={busy || hasPersistedResult}
              onClick={() => setState((current) => ({ ...current, mode: "json", notice: null }))}
              type="button"
            >
              JSON 剧集
            </button>
            <button
              aria-pressed={state.mode === "markdown"}
              className={state.mode === "markdown" ? "active" : ""}
              disabled={busy || hasPersistedResult || !canUseMarkdown}
              onClick={() => setState((current) => ({ ...current, mode: "markdown", markdownTargetMode: "new", notice: null }))}
              type="button"
            >
              Markdown 章节
            </button>
          </div>

          {state.mode === null && (
            <div className="series-exchange-choice">
              <p>JSON 会导入为新剧集；多个 Markdown 文件会各自创建一个章节。</p>
              <div>
                <button className="series-exchange-primary-button" onClick={() => setState((current) => ({ ...current, mode: "json" }))} type="button">
                  选择 JSON 文件
                </button>
                <button
                  className="series-exchange-secondary-button"
                  disabled={!canUseMarkdown}
                  onClick={() => setState((current) => ({ ...current, mode: "markdown", markdownTargetMode: "new" }))}
                  type="button"
                >
                  选择 Markdown 文件
                </button>
              </div>
            </div>
          )}

          {state.mode === "json" && (
            <div className="series-exchange-section">
              <p>导入单个版本 1 JSON 文件并创建新剧集。图片等媒体仅保留原地址，不会下载或打包。</p>
              <label className="series-exchange-file-picker">
                <span>选择 JSON 文件（最大 20 MiB）</span>
                <input
                  accept=".json,application/json"
                  disabled={busy || state.jsonStatus === "saved" || state.jsonStatus === "unknown" || hasSavedMarkdown || state.targetSeriesId !== null}
                  onChange={(event) => void handleFileSelection("json", event.currentTarget.files)}
                  type="file"
                />
              </label>
              {state.jsonSelection !== null && (
                <>
                  <p className="series-exchange-selected-file">已选择：{state.jsonSelection.fileName}</p>
                  <label className="series-exchange-field">
                    新剧集名称（可选）
                    <input
                      disabled={busy || state.jsonStatus === "saved" || state.jsonStatus === "unknown"}
                      onChange={(event) => {
                        const value = event.currentTarget.value;
                        setState((current) => ({ ...current, newSeriesName: value }));
                      }}
                      placeholder={getExportedSeriesName(state.jsonSelection.data)}
                      value={state.newSeriesName}
                    />
                  </label>
                  <div className="series-exchange-actions">
                    <button
                      className="series-exchange-primary-button"
                      disabled={busy || state.jsonStatus === "saved" || state.jsonStatus === "unknown"}
                      onClick={() => void importJson()}
                      type="button"
                    >
                      {state.jsonStatus === "importing" ? "正在导入…" : state.jsonStatus === "saved" ? "已导入" : "导入为新剧集"}
                    </button>
                  </div>
                  {state.jsonStatus === "saved" && <p className="series-exchange-success">已确认新剧集编号，可从剧集列表打开。</p>}
                  {state.jsonStatus === "saved" && (
                    <button className="series-exchange-secondary-button" onClick={() => setState(newEmptyState())} type="button">
                      开始新的导入批次
                    </button>
                  )}
                  {state.jsonStatus === "failed" && (
                    <button className="series-exchange-secondary-button" disabled={busy} onClick={() => void importJson()} type="button">
                      重试已确认失败的导入
                    </button>
                  )}
                  {state.jsonStatus === "unknown" && <p className="series-exchange-warning">结果仍待核对；请先检查剧集列表，不会自动重发。</p>}
                  {state.jsonError !== null && <p className="series-exchange-error" role="alert">{state.jsonError}</p>}
                </>
              )}
            </div>
          )}

          {state.mode === "markdown" && (
            <div className="series-exchange-section">
              <p>每个 Markdown 文件会成为一个章节，文件名作为标题，全文作为单个分镜文本。</p>
              <p className="series-exchange-hint">最多 50 个文件；每个不超过 2 MiB、总量不超过 20 MiB。导入顺序按下方队列，现有章节相对顺序会保留。</p>
              <label className="series-exchange-file-picker">
                <span>选择 Markdown 文件</span>
                <input
                  accept=".md,text/markdown"
                  disabled={busy || hasPersistedResult || hasUnknownWrite}
                  multiple
                  onChange={(event) => void handleFileSelection("markdown", event.currentTarget.files)}
                  type="file"
                />
              </label>
              {state.markdownItems.length > 0 && (
                <>
                  <fieldset className="series-exchange-target">
                    <legend>导入到哪里</legend>
                    <label>
                      <input
                        checked={state.markdownTargetMode === "new"}
                        disabled={busy || state.targetSeriesId !== null || services.createSeries === undefined}
                        name="markdown-target"
                        onChange={() => setState((current) => ({
                          ...current,
                          markdownTargetMode: "new",
                          selectedSeriesId: "",
                          targetSeriesId: null,
                          targetSeriesName: null,
                          baselineChapterIds: [],
                          baselineChapterIdsCaptured: false,
                          seriesBaselineIds: [],
                          seriesBaselineIdsCaptured: false,
                          seriesCreationStatus: "ready",
                          seriesCreationError: null,
                        }))}
                        type="radio"
                      />
                      新建剧集
                    </label>
                    <label>
                      <input
                        checked={state.markdownTargetMode === "existing"}
                        disabled={busy || state.targetSeriesId !== null}
                        name="markdown-target"
                        onChange={() => setState((current) => ({
                          ...current,
                          markdownTargetMode: "existing",
                          targetSeriesId: null,
                          targetSeriesName: null,
                          baselineChapterIds: [],
                          baselineChapterIdsCaptured: false,
                          seriesBaselineIds: [],
                          seriesBaselineIdsCaptured: false,
                          seriesCreationStatus: "ready",
                          seriesCreationError: null,
                        }))}
                        type="radio"
                      />
                      已有剧集
                    </label>
                  </fieldset>
                  {state.markdownTargetMode === "new" && (
                    <label className="series-exchange-field">
                      新剧集名称
                      <input
                        disabled={busy || state.targetSeriesId !== null}
                        onChange={(event) => {
                          const value = event.currentTarget.value;
                          setState((current) => ({ ...current, newSeriesName: value }));
                        }}
                        value={state.newSeriesName}
                      />
                    </label>
                  )}
                  {state.markdownTargetMode === "existing" && (
                    <label className="series-exchange-field">
                      选择可进入的剧集
                      <select
                        disabled={busy || state.targetSeriesId !== null}
                        onChange={(event) => {
                          const value = event.currentTarget.value;
                          setState((current) => ({ ...current, selectedSeriesId: value }));
                        }}
                        value={state.selectedSeriesId}
                      >
                        <option value="">请选择</option>
                        {visibleSeries.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                      </select>
                    </label>
                  )}
                  <ol aria-label="Markdown 章节导入顺序" className="series-exchange-queue">
                    {state.markdownItems.map((item, index) => (
                      <li key={item.key}>
                        <div className="series-exchange-queue-main">
                          <span className="series-exchange-order">{index + 1}</span>
                          <div>
                            <strong>{item.title || "未命名章节"}</strong>
                            <span>{item.fileName} · {formatByteSize(new TextEncoder().encode(item.text).length)}</span>
                            {item.chapterId !== null && <span>章节编号：{item.chapterId}</span>}
                            {item.error !== null && <span className="series-exchange-item-error">{item.error}</span>}
                          </div>
                        </div>
                        <div className="series-exchange-queue-actions">
                          <button aria-label={"上移 " + item.title} disabled={!mayReorder || index === 0} onClick={() => moveMarkdownItem(item.key, -1)} type="button">↑</button>
                          <button aria-label={"下移 " + item.title} disabled={!mayReorder || index === state.markdownItems.length - 1} onClick={() => moveMarkdownItem(item.key, 1)} type="button">↓</button>
                          <span className={"series-exchange-status " + item.status}>
                            {statusLabel(item.status)}
                          </span>
                        </div>
                      </li>
                    ))}
                  </ol>
                  {targetCreationUnknown && (
                    <p className="series-exchange-warning">新剧集创建结果待核对。继续操作时只查询剧集列表，不会再次创建。</p>
                  )}
                  {state.seriesCreationStatus === "failed" && state.seriesCreationError !== null && (
                    <p className="series-exchange-error" role="alert">新剧集创建失败：{state.seriesCreationError}</p>
                  )}
                  <div className="series-exchange-actions">
                    <button
                      className="series-exchange-primary-button"
                      disabled={busy || (!hasRetryableMarkdown && !hasUnknownWrite)}
                      onClick={() => void runMarkdownImport()}
                      type="button"
                    >
                      {busy ? "正在导入…" : hasUnknownWrite ? "核对结果并继续" : state.markdownItems.some((item) => item.status === "failed") ? "继续导入 / 重试失败项" : "开始导入章节"}
                    </button>
                  </div>
                  {hasUnknownWrite && (
                    <p className="series-exchange-warning">有文件结果仍待核对；点击“继续导入”只会读取章节列表，无法唯一确认时会停止，不会重发。</p>
                  )}
                  {allMarkdownSaved && (
                    <button className="series-exchange-secondary-button" onClick={() => setState(newEmptyState())} type="button">
                      开始新的导入批次
                    </button>
                  )}
                </>
              )}
            </div>
          )}

          {state.notice !== null && (
            <p className="series-exchange-notice" role="status" aria-live="polite">{state.notice}</p>
          )}
        </div>

        <footer className="series-exchange-footer">
          <span>JSON 仅支持版本 1；导出文件包含媒体地址，不包含图片文件。</span>
          <button className="series-exchange-secondary-button" onClick={closeDialog} type="button">关闭</button>
        </footer>
      </section>
    </div>
  );
}

function formatByteSize(bytes: number): string {
  return bytes < 1024 * 1024
    ? Math.ceil(bytes / 1024) + " KiB"
    : (bytes / (1024 * 1024)).toFixed(1) + " MiB";
}

function statusLabel(status: ItemStatus): string {
  switch (status) {
    case "ready": return "待导入";
    case "importing": return "导入中";
    case "saved": return "已保存";
    case "failed": return "失败，可重试";
    case "unknown": return "结果待核对";
  }
}
