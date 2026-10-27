import { useEffect, useRef, useState } from "react";
import type { Chapter } from "../../../shared/api/contracts";
import { ApiError } from "../../../shared/api/errors";
import {
  AssetGenerationHistoryError,
  type AssetGenerationHistoryAssetType,
  type AssetGenerationHistoryMessage,
  type AssetGenerationHistoryMode,
  type AssetGenerationHistoryPort,
  type AssetGenerationHistoryRun,
} from "../../../shared/api/assetGenerationHistory";
import { classifyMediaExportUrl, type MediaExportUrlRoute } from "../../../shared/api/mediaExportReader";
import { useSeriesDialogFocus } from "../../series/useSeriesDialogFocus";

import "./assetGenerationHistory.css";

export type AssetGenerationHistoryImageAdoptionInspection =
  | { readonly status: "ready"; readonly imageUrl: string | null }
  | { readonly status: "conflict" }
  | { readonly status: "not-current" };

export type AssetGenerationHistoryImageAdoptionOutcome =
  | "applied"
  | "already-applied"
  | "conflict"
  | "unknown"
  | "not-current";

export interface AssetGenerationHistoryImageAdoptionPort {
  inspect(
    signal: AbortSignal,
    isOperationCurrent: () => boolean,
  ): Promise<AssetGenerationHistoryImageAdoptionInspection>;
  apply(
    input: { readonly expectedImageUrl: string | null; readonly imageUrl: string; readonly signal: AbortSignal },
    isOperationCurrent: () => boolean,
  ): Promise<AssetGenerationHistoryImageAdoptionOutcome>;
}

interface AssetGenerationHistoryDialogProps {
  actorId: string;
  assetId: string;
  assetName: string;
  assetType: AssetGenerationHistoryAssetType;
  apiBaseUrl: string | null;
  listChapters(seriesId: string, signal: AbortSignal): Promise<Chapter[]>;
  onClose(): void;
  onUnauthorized(): void;
  port: AssetGenerationHistoryPort;
  imageAdoptionPort?: AssetGenerationHistoryImageAdoptionPort | undefined;
  seriesId: string;
  seriesName: string;
  isCurrent(): boolean;
}

type ChapterState =
  | { status: "loading" }
  | { status: "ready"; chapters: Chapter[]; rejectedCount: number }
  | { status: "error"; message: string };

interface HistoryEntry {
  readonly message: AssetGenerationHistoryMessage;
  readonly mediaRoute: MediaExportUrlRoute | null;
  readonly unsafeMedia: boolean;
}

type HistoryState =
  | { status: "idle" }
  | { status: "loading"; chapterId: string; mode: AssetGenerationHistoryMode }
  | {
    status: "ready";
    chapterId: string;
    mode: AssetGenerationHistoryMode;
    entries: HistoryEntry[];
    rejectedCount: number;
    run: AssetGenerationHistoryRun;
  }
  | { status: "error"; chapterId: string; mode: AssetGenerationHistoryMode; message: string };

type ActionNotice =
  | { readonly messageId: string; readonly kind: "transferring"; readonly message: string }
  | { readonly messageId: string; readonly kind: "unknown" | "error" | "success"; readonly message: string };

interface ImageAdoptionConfirmation {
  readonly messageId: string;
  readonly imageUrl: string;
  readonly expectedImageUrl: string | null;
}

interface ActiveTransfer {
  readonly messageId: string;
  readonly controller: AbortController;
  readonly run: AssetGenerationHistoryRun;
}

const videoExtensions = new Set(["mp4", "webm", "mov", "avi", "mkv", "m4v", "ogv"]);
const imageExtensions = new Set(["jpg", "jpeg", "png", "gif", "webp", "bmp"]);
const HISTORY_PAGE_SIZE = 10;

function unauthorized(error: unknown): boolean {
  return error instanceof AssetGenerationHistoryError
    ? error.kind === "unauthorized"
    : error instanceof ApiError && error.status === 401;
}

function chapterDirectory(chapters: readonly Chapter[], seriesId: string): { chapters: Chapter[]; rejectedCount: number } {
  const counts = new Map<string, number>();
  for (const chapter of chapters) {
    if (chapter.id.trim() !== "") {
      counts.set(chapter.id, (counts.get(chapter.id) ?? 0) + 1);
    }
  }
  const accepted: Chapter[] = [];
  let rejectedCount = 0;
  for (const chapter of chapters) {
    if (chapter.series_id !== seriesId || chapter.id.trim() === "" || counts.get(chapter.id) !== 1) {
      rejectedCount += 1;
      continue;
    }
    accepted.push(chapter);
  }
  return { chapters: accepted, rejectedCount };
}

function mediaRoute(
  message: AssetGenerationHistoryMessage,
  mode: AssetGenerationHistoryMode,
  apiBaseUrl: string | null,
): { route: MediaExportUrlRoute | null; unsafe: boolean } {
  if (message.role !== "assistant" || !/^(?:https?:\/\/|\/uploads\/)/iu.test(message.content)) {
    return { route: null, unsafe: false };
  }
  if (apiBaseUrl === null) return { route: null, unsafe: true };
  try {
    const route = classifyMediaExportUrl(message.content, apiBaseUrl);
    if (route.kind === "anonymous-public" && new URL(route.url).protocol !== "https:") {
      return { route: null, unsafe: true };
    }
    const parsed = new URL(route.url);
    const extension = parsed.pathname.split(".").at(-1)?.toLowerCase() ?? "";
    if (extension === "svg") return { route: null, unsafe: true };
    const knownMode = videoExtensions.has(extension)
      ? "video"
      : imageExtensions.has(extension) ? "image" : mode;
    if (knownMode !== mode) return { route: null, unsafe: true };
    return { route, unsafe: false };
  } catch {
    return { route: null, unsafe: true };
  }
}

function prepareEntries(
  messages: readonly AssetGenerationHistoryMessage[],
  expected: {
    chapterId: string;
    assetType: AssetGenerationHistoryAssetType;
    assetId: string;
    mode: AssetGenerationHistoryMode;
  },
  apiBaseUrl: string | null,
): { entries: HistoryEntry[]; rejectedCount: number } {
  const counts = new Map<string, number>();
  for (const message of messages) counts.set(message.id, (counts.get(message.id) ?? 0) + 1);

  const entries: HistoryEntry[] = [];
  let rejectedCount = 0;
  for (const message of messages) {
    if (message.id.trim() === ""
      || counts.get(message.id) !== 1
      || message.chapter_id !== expected.chapterId
      || message.asset_type !== expected.assetType
      || message.asset_id !== expected.assetId
      || message.chat_mode !== expected.mode
      || (message.role !== "assistant" && message.role !== "user")) {
      rejectedCount += 1;
      continue;
    }
    const classified = mediaRoute(message, expected.mode, apiBaseUrl);
    if (classified.unsafe) rejectedCount += 1;
    entries.push({ message, mediaRoute: classified.route, unsafeMedia: classified.unsafe });
  }
  return { entries: entries.reverse(), rejectedCount };
}

function mediaPreviewLabel(mode: AssetGenerationHistoryMode): string {
  return mode === "image" ? "图片" : "视频";
}

function displayTime(value: string): string {
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(timestamp);
}

function errorMessage(error: unknown, action: "read" | "write"): string {
  if (error instanceof AssetGenerationHistoryError) {
    switch (error.kind) {
      case "not-sent": return "身份或页面状态已变化，请重新打开历史后操作。";
      case "rejected": return error.status === null
        ? "服务拒绝了当前请求。"
        : `服务拒绝了当前请求（HTTP ${error.status}）。`;
      case "unknown": return action === "write"
        ? "请求已发出但结果未知；已停止自动操作，请只读核对原记录。"
        : "读取结果未知，请重新打开历史核对。";
      case "read-error": return action === "read" ? "无法读取这条历史记录。" : "媒体预览读取失败。";
      case "unauthorized": return "登录状态已失效，请重新登录。";
      case "invalid-response": return "服务返回的数据无法识别，请重新读取。";
    }
  }
  if (error instanceof ApiError) {
    if (error.status === 403) return error.detail ?? "当前账号没有查看此章节历史的权限。";
    if (error.status === 404) return error.detail ?? "章节或素材已不存在，请重新读取。";
    if (error.kind === "timeout") return action === "write"
      ? "请求等待超时，服务端结果未知；已停止自动重试。"
      : "读取超时，请重试。";
    if (error.kind === "network") return action === "write"
      ? "网络中断，服务端结果未知；已停止自动重试。"
      : "网络中断，无法读取历史。";
    return error.detail ?? error.message;
  }
  return action === "write" ? "操作结果未知，未自动重试。" : "无法读取历史，请重试。";
}

function exactCurrentMessage(
  messages: readonly AssetGenerationHistoryMessage[],
  expected: AssetGenerationHistoryMessage,
): AssetGenerationHistoryMessage | null {
  const matches = messages.filter((message) => message.id === expected.id);
  if (matches.length !== 1) return null;
  const message = matches[0];
  if (message === undefined) return null;
  return message.chapter_id === expected.chapter_id
    && message.asset_type === expected.asset_type
    && message.asset_id === expected.asset_id
    && message.chat_mode === expected.chat_mode
    && message.role === expected.role
    && message.content === expected.content
    && message.model_name === expected.model_name
    && message.created_at === expected.created_at
    ? message
    : null;
}

function idStillPresent(messages: readonly AssetGenerationHistoryMessage[], messageId: string): boolean {
  return messages.some((message) => message.id === messageId);
}

function GenerationHistoryMediaPreview({
  assetName,
  entry,
  mode,
  onUnauthorized,
  run,
}: {
  assetName: string;
  entry: HistoryEntry;
  mode: AssetGenerationHistoryMode;
  onUnauthorized(): void;
  run: AssetGenerationHistoryRun;
}) {
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const [failureMessage, setFailureMessage] = useState<string | null>(null);
  const route = entry.mediaRoute;
  const onUnauthorizedRef = useRef(onUnauthorized);
  onUnauthorizedRef.current = onUnauthorized;

  useEffect(() => {
    setFailureMessage(null);
    if (route === null) {
      setObjectUrl(null);
      return;
    }
    const controller = new AbortController();
    let active = true;
    let createdUrl: string | null = null;
    setObjectUrl(null);
    void run.readMedia(route.url, controller.signal).then((blob) => {
      if (!active || controller.signal.aborted || !run.isCurrent()) return;
      createdUrl = URL.createObjectURL(blob);
      setObjectUrl(createdUrl);
    }).catch((error: unknown) => {
      if (!active || controller.signal.aborted || !run.isCurrent()) return;
      const sessionExpired = unauthorized(error);
      if (sessionExpired) onUnauthorizedRef.current();
      setFailureMessage(sessionExpired ? errorMessage(error, "read") : "媒体读取或解码失败。");
    });
    return () => {
      active = false;
      controller.abort();
      if (createdUrl !== null) URL.revokeObjectURL(createdUrl);
    };
  }, [route, run]);

  if (route === null || entry.unsafeMedia) {
    return <p className="asset-generation-history-media-state">媒体地址不符合安全预览要求。</p>;
  }
  const src = objectUrl;
  if (failureMessage !== null) return <p className="asset-generation-history-media-state">{failureMessage}</p>;
  if (src === null) return <p className="asset-generation-history-media-state" role="status">正在读取媒体…</p>;
  if (mode === "video") {
    return (
      <video
        aria-label={assetName + "生成视频"}
        controls
        onError={() => setFailureMessage("媒体读取或解码失败。")}
        playsInline
        preload="metadata"
        src={src}
      />
    );
  }
  return (
    <img
      alt={assetName + "生成图片"}
      loading="lazy"
      onError={() => setFailureMessage("媒体读取或解码失败。")}
      referrerPolicy="no-referrer"
      src={src}
    />
  );
}

export function AssetGenerationHistoryDialog({
  actorId,
  assetId,
  assetName,
  assetType,
  apiBaseUrl,
  listChapters,
  onClose,
  onUnauthorized,
  port,
  imageAdoptionPort,
  seriesId,
  seriesName,
  isCurrent,
}: AssetGenerationHistoryDialogProps) {
  const [chapterState, setChapterState] = useState<ChapterState>({ status: "loading" });
  const [selectedChapterId, setSelectedChapterId] = useState("");
  const [mode, setMode] = useState<AssetGenerationHistoryMode>("image");
  const [chapterRefresh, setChapterRefresh] = useState(0);
  const [historyRefresh, setHistoryRefresh] = useState(0);
  const [historyPage, setHistoryPage] = useState(0);
  const [activePreviewMessageId, setActivePreviewMessageId] = useState<string | null>(null);
  const [historyState, setHistoryState] = useState<HistoryState>({ status: "idle" });
  const [actionNotice, setActionNotice] = useState<ActionNotice | null>(null);
  const [deleteConfirmation, setDeleteConfirmation] = useState<string | null>(null);
  const [imageAdoptionConfirmation, setImageAdoptionConfirmation] = useState<ImageAdoptionConfirmation | null>(null);
  const [busyMessageId, setBusyMessageId] = useState<string | null>(null);
  const parentCurrentRef = useRef(isCurrent);
  parentCurrentRef.current = isCurrent;
  const selectionRef = useRef({ chapterId: selectedChapterId, mode });
  selectionRef.current = { chapterId: selectedChapterId, mode };
  const actionBusyRef = useRef(false);
  const transferRef = useRef<ActiveTransfer | null>(null);
  const actionControllerRef = useRef<AbortController | null>(null);
  const activeControllersRef = useRef(new Set<AbortController>());
  const unauthorizedRef = useRef(onUnauthorized);
  unauthorizedRef.current = onUnauthorized;
  const focus = useSeriesDialogFocus({ active: true, canDismiss: busyMessageId === null, onDismiss: onClose });

  useEffect(() => () => {
    for (const controller of activeControllersRef.current) controller.abort();
    activeControllersRef.current.clear();
    transferRef.current?.controller.abort();
    actionControllerRef.current?.abort();
  }, []);

  function createController(): AbortController {
    const controller = new AbortController();
    activeControllersRef.current.add(controller);
    return controller;
  }

  function releaseController(controller: AbortController): void {
    activeControllersRef.current.delete(controller);
  }

  useEffect(() => {
    const controller = createController();
    let active = true;
    setChapterState({ status: "loading" });
    setSelectedChapterId("");
    void listChapters(seriesId, controller.signal).then((chapters) => {
      if (!active || controller.signal.aborted || !parentCurrentRef.current()) return;
      const directory = chapterDirectory(chapters, seriesId);
      setChapterState({ status: "ready", ...directory });
    }).catch((error: unknown) => {
      if (!active || controller.signal.aborted || !parentCurrentRef.current()) return;
      if (unauthorized(error)) unauthorizedRef.current();
      setChapterState({ status: "error", message: errorMessage(error, "read") });
    });
    return () => {
      active = false;
      controller.abort();
      releaseController(controller);
    };
  }, [chapterRefresh, listChapters, seriesId]);

  useEffect(() => {
    setDeleteConfirmation(null);
    setImageAdoptionConfirmation(null);
    setHistoryPage(0);
    setActivePreviewMessageId(null);
    if (selectedChapterId === "") {
      setHistoryState({ status: "idle" });
      return;
    }
    const selectedChapter = chapterState.status === "ready"
      ? chapterState.chapters.find((chapter) => chapter.id === selectedChapterId)
      : undefined;
    if (selectedChapter === undefined || !parentCurrentRef.current()) {
      setHistoryState({ status: "idle" });
      return;
    }

    let active = true;
    const controller = createController();
    setHistoryState({ status: "loading", chapterId: selectedChapterId, mode });
    const runContext = {
      actorId,
      seriesId,
      chapterId: selectedChapterId,
      assetType,
      assetId,
      mode,
      isContextCurrent: () => active
        && !controller.signal.aborted
        && selectionRef.current.chapterId === selectedChapterId
        && selectionRef.current.mode === mode
        && parentCurrentRef.current(),
    };
    let run: AssetGenerationHistoryRun;
    try {
      run = port.beginRun(runContext);
    } catch (error) {
      setHistoryState({ status: "error", chapterId: selectedChapterId, mode, message: errorMessage(error, "read") });
      if (unauthorized(error)) unauthorizedRef.current();
      return () => { active = false; controller.abort(); releaseController(controller); };
    }

    void run.readMessages(controller.signal).then((messages) => {
      if (!active || controller.signal.aborted || !run.isCurrent()) return;
      const prepared = prepareEntries(messages, {
        chapterId: selectedChapterId,
        assetType,
        assetId,
        mode,
      }, apiBaseUrl);
      setHistoryState({ status: "ready", chapterId: selectedChapterId, mode, ...prepared, run });
    }).catch((error: unknown) => {
      if (!active || controller.signal.aborted || !run.isCurrent()) return;
      if (unauthorized(error)) unauthorizedRef.current();
      setHistoryState({ status: "error", chapterId: selectedChapterId, mode, message: errorMessage(error, "read") });
    });

    return () => {
      active = false;
      controller.abort();
      releaseController(controller);
    };
  }, [actorId, apiBaseUrl, assetId, assetType, chapterState, historyRefresh, mode, port, selectedChapterId, seriesId]);

  const currentHistory = historyState.status === "ready"
    && historyState.chapterId === selectedChapterId
    && historyState.mode === mode
    && historyState.run.isCurrent()
    ? historyState
    : null;
  const currentHistoryError = historyState.status === "error"
    && historyState.chapterId === selectedChapterId
    && historyState.mode === mode
    ? historyState
    : null;
  const currentHistoryLoading = historyState.status === "loading"
    && historyState.chapterId === selectedChapterId
    && historyState.mode === mode;
  const entries = currentHistory?.entries ?? [];
  const mediaCount = entries.filter((entry) => entry.mediaRoute !== null).length;
  const pageCount = Math.max(1, Math.ceil(entries.length / HISTORY_PAGE_SIZE));
  const currentPage = Math.min(historyPage, pageCount - 1);
  const visibleEntries = entries.slice(
    currentPage * HISTORY_PAGE_SIZE,
    (currentPage + 1) * HISTORY_PAGE_SIZE,
  );

  function selectedContextIsCurrent(run: AssetGenerationHistoryRun): boolean {
    return parentCurrentRef.current() && run.isCurrent();
  }

  function matchesLoadedEntry(
    run: AssetGenerationHistoryRun,
    entry: HistoryEntry,
  ): boolean {
    return selectedContextIsCurrent(run)
      && entry.message.chapter_id === selectedChapterId
      && entry.message.asset_type === assetType
      && entry.message.asset_id === assetId
      && entry.message.chat_mode === mode;
  }

  async function readFreshMessages(run: AssetGenerationHistoryRun): Promise<readonly AssetGenerationHistoryMessage[]> {
    const controller = createController();
    try {
      return await run.readMessages(controller.signal);
    } finally {
      releaseController(controller);
    }
  }

  async function verifyOriginal(
    run: AssetGenerationHistoryRun,
    entry: HistoryEntry,
  ): Promise<AssetGenerationHistoryMessage | null> {
    if (!matchesLoadedEntry(run, entry)) return null;
    const messages = await readFreshMessages(run);
    if (!matchesLoadedEntry(run, entry)) return null;
    return exactCurrentMessage(messages, entry.message);
  }

  function beginAction(messageId: string): boolean {
    if (actionBusyRef.current || currentHistory === null) return false;
    actionBusyRef.current = true;
    setBusyMessageId(messageId);
    setActionNotice(null);
    return true;
  }

  function endAction(messageId: string): void {
    actionBusyRef.current = false;
    if (transferRef.current?.messageId === messageId) transferRef.current = null;
    setBusyMessageId((current) => current === messageId ? null : current);
  }

  async function readBack(run: AssetGenerationHistoryRun): Promise<readonly AssetGenerationHistoryMessage[]> {
    return readFreshMessages(run);
  }

  async function transfer(entry: HistoryEntry): Promise<void> {
    if (entry.mediaRoute === null || !beginAction(entry.message.id) || currentHistory === null) return;
    const run = currentHistory.run;
    const messageId = entry.message.id;
    const controller = createController();
    actionControllerRef.current = controller;
    transferRef.current = { messageId, controller, run };
    setActionNotice({ messageId, kind: "transferring", message: "正在等待转存；停止等待不会取消服务器已收到的请求。" });
    try {
      const original = await verifyOriginal(run, entry);
      if (original === null) {
        if (selectedContextIsCurrent(run)) setActionNotice({ messageId, kind: "error", message: "原消息已变化或身份无法唯一核对，未发送转存。" });
        return;
      }
      const transferredUrl = await run.retryOssUpload(original.content, controller.signal);
      if (!matchesLoadedEntry(run, entry)) return;
      if (transferredUrl === original.content) {
        let messages: readonly AssetGenerationHistoryMessage[];
        try {
          messages = await readBack(run);
        } catch (error) {
          if (unauthorized(error)) unauthorizedRef.current();
          setActionNotice({ messageId, kind: "unknown", message: "转存已响应，但原消息独立读取失败；没有改写消息。" });
          return;
        }
        if (!matchesLoadedEntry(run, entry)) return;
        const unchanged = exactCurrentMessage(messages, original);
        setActionNotice({
          messageId,
          kind: unchanged === null ? "unknown" : "success",
          message: unchanged === null ? "转存已响应，但原消息独立读取未能确认。" : "该媒体地址已托管，原消息保持不变。",
        });
        return;
      }

      const beforePut = await verifyOriginal(run, entry);
      if (beforePut === null || beforePut.content !== original.content) {
        if (selectedContextIsCurrent(run)) setActionNotice({ messageId, kind: "error", message: "转存完成，但原消息在写回前已变化；没有覆盖新内容。" });
        return;
      }
      try {
        await run.updateMessageContent(messageId, transferredUrl, controller.signal);
      } catch (error) {
        if (error instanceof AssetGenerationHistoryError && error.kind !== "unknown") throw error;
        if (!matchesLoadedEntry(run, entry)) return;
        let messages: readonly AssetGenerationHistoryMessage[];
        try {
          messages = await readBack(run);
        } catch (readError) {
          if (unauthorized(readError)) unauthorizedRef.current();
          setActionNotice({ messageId, kind: "unknown", message: "消息PUT结果未知，独立读取失败；不会自动再次写入。" });
          return;
        }
        if (!matchesLoadedEntry(run, entry)) return;
        const updated = exactCurrentMessage(messages, { ...original, content: transferredUrl });
        setActionNotice({
          messageId,
          kind: updated === null ? "unknown" : "success",
          message: updated === null ? errorMessage(error, "write") : "已转存，原消息写回已由独立读取确认。",
        });
        if (updated !== null) setHistoryRefresh((value) => value + 1);
        return;
      }

      if (!matchesLoadedEntry(run, entry)) return;
      let messages: readonly AssetGenerationHistoryMessage[];
      try {
        messages = await readBack(run);
      } catch (error) {
        if (unauthorized(error)) unauthorizedRef.current();
        setActionNotice({ messageId, kind: "unknown", message: "服务已响应消息PUT，但独立读取失败；不会自动再次写入。" });
        return;
      }
      if (!matchesLoadedEntry(run, entry)) return;
      const updated = exactCurrentMessage(messages, { ...original, content: transferredUrl });
      if (updated === null) {
        setActionNotice({ messageId, kind: "unknown", message: "服务已响应写回，但独立读取未确认新地址；不会自动再次写入。" });
        return;
      }
      setActionNotice({ messageId, kind: "success", message: "已转存并通过独立读取确认原消息新地址。" });
      setHistoryRefresh((value) => value + 1);
    } catch (error) {
      if (!matchesLoadedEntry(run, entry)) return;
      if (error instanceof AssetGenerationHistoryError && error.kind === "unknown") {
        try {
          const messages = await readBack(run);
          if (!matchesLoadedEntry(run, entry)) return;
          const current = exactCurrentMessage(messages, entry.message);
          setActionNotice({
            messageId,
            kind: "unknown",
            message: current !== null
              ? "转存请求结果未知；原消息仍是原地址，没有自动重发。可停止本地跟踪或重新打开后核对。"
              : "转存请求结果未知且原消息已变化；没有自动写回。请重新打开历史核对。",
          });
        } catch (readError) {
          if (unauthorized(readError)) unauthorizedRef.current();
          setActionNotice({ messageId, kind: "unknown", message: "转存请求结果未知，后续只读核对也未完成；没有自动重发。" });
        }
      } else {
        if (unauthorized(error)) unauthorizedRef.current();
        setActionNotice({ messageId, kind: "error", message: errorMessage(error, "write") });
      }
    } finally {
      endAction(messageId);
      if (actionControllerRef.current === controller) actionControllerRef.current = null;
      releaseController(controller);
    }
  }

  async function deleteEntry(entry: HistoryEntry): Promise<void> {
    if (entry.mediaRoute === null || !beginAction(entry.message.id) || currentHistory === null) return;
    const run = currentHistory.run;
    const messageId = entry.message.id;
    const controller = createController();
    actionControllerRef.current = controller;
    try {
      const original = await verifyOriginal(run, entry);
      if (original === null) {
        if (selectedContextIsCurrent(run)) setActionNotice({ messageId, kind: "error", message: "原消息已变化或身份无法唯一核对，没有发送删除。" });
        return;
      }
      try {
        await run.deleteMessage(messageId, controller.signal);
      } catch (error) {
        const uncertain = error instanceof AssetGenerationHistoryError
          ? error.kind === "unknown" || (error.kind === "rejected" && error.status === 404)
          : error instanceof ApiError && error.status === 404;
        if (!uncertain) throw error;
        if (!matchesLoadedEntry(run, entry)) return;
        let afterUnknown: readonly AssetGenerationHistoryMessage[];
        try {
          afterUnknown = await readBack(run);
        } catch (error) {
          if (unauthorized(error)) unauthorizedRef.current();
          setActionNotice({ messageId, kind: "unknown", message: "删除请求结果未知，独立读取也未完成；不会自动再次删除。" });
          return;
        }
        if (!matchesLoadedEntry(run, entry)) return;
        const absent = !idStillPresent(afterUnknown, messageId);
        setActionNotice({
          messageId,
          kind: absent ? "success" : "unknown",
          message: absent ? "删除已由独立读取确认。" : "删除请求结果未知，记录仍可见；不会自动再次删除。",
        });
        if (absent) setHistoryRefresh((value) => value + 1);
        return;
      }
      if (!matchesLoadedEntry(run, entry)) return;
      let afterDelete: readonly AssetGenerationHistoryMessage[];
      try {
        afterDelete = await readBack(run);
      } catch (error) {
        if (unauthorized(error)) unauthorizedRef.current();
        setActionNotice({ messageId, kind: "unknown", message: "服务已响应删除，但独立读取未完成；不会自动再次删除。" });
        return;
      }
      if (!matchesLoadedEntry(run, entry)) return;
      const absent = !idStillPresent(afterDelete, messageId);
      setActionNotice({
        messageId,
        kind: absent ? "success" : "unknown",
        message: absent ? "删除已由独立读取确认。" : "服务已响应删除，但记录仍可见；没有自动再次删除。",
      });
      if (absent) setHistoryRefresh((value) => value + 1);
    } catch (error) {
      if (!matchesLoadedEntry(run, entry)) return;
      if (unauthorized(error)) unauthorizedRef.current();
      setActionNotice({ messageId, kind: "error", message: errorMessage(error, "write") });
    } finally {
      endAction(messageId);
      if (actionControllerRef.current === controller) actionControllerRef.current = null;
      releaseController(controller);
      setDeleteConfirmation(null);
    }
  }

  async function prepareImageAdoption(entry: HistoryEntry): Promise<void> {
    if (mode !== "image" || entry.mediaRoute === null || imageAdoptionPort === undefined
      || !beginAction(entry.message.id) || currentHistory === null) return;
    const run = currentHistory.run;
    const messageId = entry.message.id;
    const controller = createController();
    actionControllerRef.current = controller;
    try {
      const original = await verifyOriginal(run, entry);
      if (original === null || !matchesLoadedEntry(run, entry)) {
        if (selectedContextIsCurrent(run)) {
          setActionNotice({ messageId, kind: "error", message: "原消息已变化或身份无法唯一核对，未准备替换素材图。" });
        }
        return;
      }
      const inspection = await imageAdoptionPort.inspect(controller.signal, () => matchesLoadedEntry(run, entry));
      if (!matchesLoadedEntry(run, entry) || inspection.status === "not-current") return;
      if (inspection.status === "conflict") {
        setActionNotice({ messageId, kind: "error", message: "当前素材身份或图片已变化，请重新打开历史后操作。" });
        return;
      }
      setImageAdoptionConfirmation({
        messageId,
        imageUrl: original.content,
        expectedImageUrl: inspection.imageUrl,
      });
      setActionNotice({
        messageId,
        kind: "success",
        message: inspection.imageUrl === null
          ? "已核对原生成记录和当前素材。确认后会再次读取并保存这张图片。"
          : "已核对原生成记录和当前素材。确认后会再次读取并替换当前图片。",
      });
    } catch (error) {
      if (!matchesLoadedEntry(run, entry)) return;
      if (unauthorized(error)) unauthorizedRef.current();
      setActionNotice({ messageId, kind: "error", message: errorMessage(error, "read") });
    } finally {
      endAction(messageId);
      if (actionControllerRef.current === controller) actionControllerRef.current = null;
      releaseController(controller);
    }
  }

  async function confirmImageAdoption(entry: HistoryEntry): Promise<void> {
    const confirmation = imageAdoptionConfirmation;
    if (mode !== "image" || entry.mediaRoute === null || imageAdoptionPort === undefined
      || confirmation?.messageId !== entry.message.id
      || !beginAction(entry.message.id) || currentHistory === null) return;
    const run = currentHistory.run;
    const messageId = entry.message.id;
    const controller = createController();
    actionControllerRef.current = controller;
    try {
      const original = await verifyOriginal(run, entry);
      if (original === null || original.content !== confirmation.imageUrl || !matchesLoadedEntry(run, entry)) {
        if (selectedContextIsCurrent(run)) {
          setImageAdoptionConfirmation(null);
          setActionNotice({ messageId, kind: "error", message: "原生成记录已变化，未修改素材图片。" });
        }
        return;
      }
      const outcome = await imageAdoptionPort.apply({
        expectedImageUrl: confirmation.expectedImageUrl,
        imageUrl: original.content,
        signal: controller.signal,
      }, () => matchesLoadedEntry(run, entry));
      if (!matchesLoadedEntry(run, entry) || outcome === "not-current") return;
      setImageAdoptionConfirmation(null);
      switch (outcome) {
        case "applied":
          setActionNotice({ messageId, kind: "success", message: "素材图片已保存，并由独立目录读取确认。" });
          break;
        case "already-applied":
          setActionNotice({ messageId, kind: "success", message: "当前素材图片已是这张图片，目录读取已确认。" });
          break;
        case "conflict":
          setActionNotice({ messageId, kind: "error", message: "当前素材图片已变化，未覆盖新图片。请重新打开历史后确认。" });
          break;
        case "unknown":
          setActionNotice({ messageId, kind: "unknown", message: "保存请求已发出但结果未知；已停止自动操作，请只读核对素材目录。" });
          break;
      }
    } catch (error) {
      if (!matchesLoadedEntry(run, entry)) return;
      if (unauthorized(error)) unauthorizedRef.current();
      setImageAdoptionConfirmation(null);
      setActionNotice({ messageId, kind: "error", message: errorMessage(error, "write") });
    } finally {
      endAction(messageId);
      if (actionControllerRef.current === controller) actionControllerRef.current = null;
      releaseController(controller);
    }
  }

  function stopTransferWait(messageId: string): void {
    const active = transferRef.current;
    if (active?.messageId !== messageId) return;
    active.controller.abort();
    setActionNotice({ messageId, kind: "transferring", message: "已停止本地等待；服务器可能仍在处理，不会自动重发。正在只读核对原记录。" });
  }

  function refreshHistory(): void {
    if (busyMessageId !== null) return;
    setActionNotice(null);
    setImageAdoptionConfirmation(null);
    setHistoryRefresh((value) => value + 1);
  }

  function changeHistoryPage(page: number): void {
    if (busyMessageId !== null || page < 0 || page >= pageCount || page === currentPage) return;
    setActivePreviewMessageId(null);
    setImageAdoptionConfirmation(null);
    setHistoryPage(page);
  }

  const selectedChapter = chapterState.status === "ready"
    ? chapterState.chapters.find((chapter) => chapter.id === selectedChapterId)
    : undefined;
  const canRefreshHistory = selectedChapter !== undefined && busyMessageId === null;

  return (
    <div className="asset-generation-history-overlay">
      <section
        aria-labelledby="asset-generation-history-title"
        aria-modal="true"
        className="asset-generation-history-dialog"
        ref={focus.dialogRef}
        role="dialog"
        tabIndex={-1}
      >
        <header className="asset-generation-history-header">
          <div>
            <p className="eyebrow">素材生成记录</p>
            <h2 id="asset-generation-history-title">{assetName} · 生成历史</h2>
            <p>{seriesName} · {assetType === "character" ? "角色" : assetType === "scene" ? "场景" : "道具"}</p>
          </div>
          <button aria-label="关闭生成历史" className="asset-generation-history-close" onClick={focus.dismiss} type="button">×</button>
        </header>
        <div className="asset-generation-history-toolbar">
          <label>
            章节
            <select
              aria-label="选择历史所属章节"
              disabled={chapterState.status !== "ready" || busyMessageId !== null}
              onChange={(event) => {
                setActionNotice(null);
                setSelectedChapterId(event.currentTarget.value);
              }}
              value={selectedChapterId}
            >
              <option value="">请选择章节</option>
              {chapterState.status === "ready" && chapterState.chapters.map((chapter) => (
                <option key={chapter.id} value={chapter.id}>{chapter.title || `章节 ${chapter.order}`}</option>
              ))}
            </select>
          </label>
          <button
            className="secondary-button"
            disabled={chapterState.status === "loading" || busyMessageId !== null}
            onClick={() => {
              setActionNotice(null);
              setChapterRefresh((value) => value + 1);
            }}
            type="button"
          >
            重新读取章节
          </button>
          <div className="asset-generation-history-mode" role="group" aria-label="历史媒体类型">
            {(["image", "video"] as const).map((value) => (
              <button
                aria-pressed={mode === value}
                className={mode === value ? "active" : ""}
                disabled={busyMessageId !== null}
                key={value}
                onClick={() => {
                  setActionNotice(null);
                  setMode(value);
                }}
                type="button"
              >
                {mediaPreviewLabel(value)}
              </button>
            ))}
          </div>
          <button className="secondary-button" disabled={!canRefreshHistory} onClick={refreshHistory} type="button">重新读取</button>
        </div>
        <div className="asset-generation-history-body">
          {chapterState.status === "loading" && <p className="asset-generation-history-state" role="status">正在读取剧集章节目录…</p>}
          {chapterState.status === "error" && <p className="asset-generation-history-state" role="alert">{chapterState.message}</p>}
          {chapterState.status === "ready" && chapterState.rejectedCount > 0 && (
            <p className="asset-generation-history-state" role="status">已隐藏 {chapterState.rejectedCount} 条重复或不属于当前剧集的章节记录。</p>
          )}
          {chapterState.status === "ready" && chapterState.chapters.length === 0 && (
            <p className="asset-generation-history-state" role="status">当前剧集没有可唯一核对的章节。</p>
          )}
          {selectedChapterId === "" && chapterState.status === "ready" && chapterState.chapters.length > 0 && (
            <p className="asset-generation-history-state" role="status">请选择原章节后读取该素材的生成历史。</p>
          )}
          {selectedChapterId !== "" && currentHistoryLoading && (
            <p className="asset-generation-history-state" role="status">正在读取{mediaPreviewLabel(mode)}历史…</p>
          )}
          {selectedChapterId !== "" && currentHistoryError !== null && (
            <div className="asset-generation-history-state" role="alert">
              <p>{currentHistoryError.message}</p>
              <button className="secondary-button" disabled={!canRefreshHistory} onClick={refreshHistory} type="button">重新读取</button>
            </div>
          )}
          {currentHistory !== null && currentHistory.rejectedCount > 0 && (
            <p className="asset-generation-history-state" role="status">已隐藏 {currentHistory.rejectedCount} 条重复、越界或不安全的历史记录。</p>
          )}
          {currentHistory !== null && mediaCount === 0 && (
            <p className="asset-generation-history-state" role="status">当前章节暂无{mediaPreviewLabel(mode)}记录。</p>
          )}
          {currentHistory !== null && entries.length > 0 && (
            <>
              {pageCount > 1 && (
                <nav aria-label="生成历史分页" className="asset-generation-history-pagination">
                  <button
                    aria-label="上一页历史"
                    className="secondary-button"
                    disabled={currentPage === 0 || busyMessageId !== null}
                    onClick={() => changeHistoryPage(currentPage - 1)}
                    type="button"
                  >
                    上一页
                  </button>
                  <p aria-live="polite" role="status">第 {currentPage + 1} / {pageCount} 页</p>
                  <button
                    aria-label="下一页历史"
                    className="secondary-button"
                    disabled={currentPage + 1 >= pageCount || busyMessageId !== null}
                    onClick={() => changeHistoryPage(currentPage + 1)}
                    type="button"
                  >
                    下一页
                  </button>
                </nav>
              )}
              <ol className="asset-generation-history-list">
                {visibleEntries.map((entry) => {
                  const messageId = entry.message.id;
                  const isBusy = busyMessageId === messageId;
                  const notice = actionNotice?.messageId === messageId ? actionNotice : null;
                  const isMedia = entry.mediaRoute !== null;
                  const previewIsActive = activePreviewMessageId === messageId;
                  return (
                    <li className="asset-generation-history-item" key={messageId}>
                      {isMedia ? (
                        <div className="asset-generation-history-preview">
                          {apiBaseUrl !== null && currentHistory !== null && (
                            previewIsActive ? (
                              <>
                                <GenerationHistoryMediaPreview
                                  assetName={assetName}
                                  entry={entry}
                                  mode={mode}
                                  onUnauthorized={() => unauthorizedRef.current()}
                                  run={currentHistory.run}
                                />
                                <button
                                  aria-label={`关闭${mediaPreviewLabel(mode)}预览`}
                                  className="secondary-button"
                                  onClick={() => setActivePreviewMessageId(null)}
                                  type="button"
                                >
                                  关闭预览
                                </button>
                              </>
                            ) : (
                              <button
                                aria-label={`预览${mediaPreviewLabel(mode)}`}
                                className="secondary-button"
                                onClick={() => setActivePreviewMessageId(messageId)}
                                type="button"
                              >
                                预览{mediaPreviewLabel(mode)}
                              </button>
                            )
                          )}
                        </div>
                      ) : entry.unsafeMedia ? (
                        <p className="asset-generation-history-text-note">媒体地址不符合安全预览要求，已隐藏。</p>
                      ) : (
                        <p className="asset-generation-history-text">{entry.message.content}</p>
                      )}
                      <p className="asset-generation-history-time">{displayTime(entry.message.created_at)}</p>
                      {isMedia && (
                        <div className="asset-generation-history-actions">
                          {mode === "image" && imageAdoptionPort !== undefined && (
                            <button
                              className="secondary-button"
                              disabled={busyMessageId !== null}
                              onClick={() => void prepareImageAdoption(entry)}
                              type="button"
                            >
                              设为{assetType === "character" ? "角色图" : assetType === "scene" ? "场景图" : "道具图"}
                            </button>
                          )}
                          <button
                            className="secondary-button"
                            disabled={busyMessageId !== null}
                            onClick={() => void transfer(entry)}
                            type="button"
                          >
                            {isBusy && notice?.kind === "transferring" ? "转存处理中…" : "转存到 OSS"}
                          </button>
                          {isBusy && notice?.kind === "transferring" && transferRef.current?.messageId === messageId && (
                            <button className="secondary-button" onClick={() => stopTransferWait(messageId)} type="button">停止本地等待</button>
                          )}
                          {deleteConfirmation === messageId ? (
                            <span className="asset-generation-history-confirm">
                              <span>删除这条记录？</span>
                              <button className="danger-button" disabled={busyMessageId !== null} onClick={() => void deleteEntry(entry)} type="button">确认删除</button>
                              <button className="secondary-button" disabled={busyMessageId !== null} onClick={() => setDeleteConfirmation(null)} type="button">取消</button>
                            </span>
                          ) : (
                            <button
                              className="danger-button"
                              disabled={busyMessageId !== null}
                              onClick={() => setDeleteConfirmation(messageId)}
                              type="button"
                            >
                              删除
                            </button>
                          )}
                        </div>
                      )}
                      {imageAdoptionConfirmation?.messageId === messageId && (
                        <div className="asset-generation-history-confirm" role="group" aria-label="确认设置素材图片">
                          <span>
                            {imageAdoptionConfirmation.expectedImageUrl === null
                              ? "将这张图片设为当前素材图。"
                              : "将用这张生成图片替换当前素材图。"}
                          </span>
                          <button
                            className="primary-button"
                            disabled={busyMessageId !== null}
                            onClick={() => void confirmImageAdoption(entry)}
                            type="button"
                          >
                            确认设为{assetType === "character" ? "角色图" : assetType === "scene" ? "场景图" : "道具图"}
                          </button>
                          <button
                            className="secondary-button"
                            disabled={busyMessageId !== null}
                            onClick={() => {
                              setImageAdoptionConfirmation(null);
                              setActionNotice(null);
                            }}
                            type="button"
                          >
                            取消
                          </button>
                        </div>
                      )}
                      {notice !== null && (
                        <div className={`asset-generation-history-notice ${notice.kind}`} role="status">
                          <p>{notice.message}</p>
                          {notice.kind === "unknown" && (
                            <button className="secondary-button" onClick={() => setActionNotice(null)} type="button">停止本地跟踪</button>
                          )}
                        </div>
                      )}
                    </li>
                  );
                })}
              </ol>
            </>
          )}
        </div>
      </section>
    </div>
  );
}
