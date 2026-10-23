import { useCallback, useEffect, useRef, useState } from "react";
import { CanvasDataError, type CanvasDataPort, type CanvasEnvelope, type CanvasSaveGuard } from "../../shared/api/canvasData";
import type { CanvasDocument } from "./canvasDocument";
import { documentEqual, parseCanvasDocument } from "./canvasDocument";

export type CanvasSaveStatus = "loading" | "ready" | "saving" | "unsupported" | "conflict" | "unknown" | "error";

export interface CanvasDocumentState {
  document: CanvasDocument | null;
  writable: boolean;
  dirty: boolean;
  status: CanvasSaveStatus;
  message: string | null;
  reviewedServer: CanvasEnvelope | null;
  saveNow(): Promise<boolean>;
  reviewServer(): Promise<void>;
  resumeReviewedDraft(): Promise<boolean>;
  adoptReviewedServer(): void;
  edit(document: CanvasDocument): void;
  discard(): void;
  hasPendingWork(): boolean;
  isClean(): boolean;
}

export interface CanvasDocumentOptions {
  port: CanvasDataPort;
  chapterId: string;
  actorId: string;
  sessionGeneration: number;
  seriesId: string;
  serviceIdentity: object;
  onUnauthorized?(): void;
}

export function useCanvasDocument(options: CanvasDocumentOptions): CanvasDocumentState {
  const { port, chapterId, actorId, sessionGeneration, seriesId, serviceIdentity, onUnauthorized } = options;
  const scopeKey = [actorId, sessionGeneration, seriesId, chapterId].join(":");
  const scopeRef = useRef({ key: scopeKey, serviceIdentity, epoch: 0 });
  const dirtySequence = useRef(0);
  const dirtyRef = useRef(false);
  const statusRef = useRef<CanvasSaveStatus>("loading");
  const saveLatestRef = useRef<() => Promise<boolean>>(() => Promise.resolve(false));
  const baseRef = useRef<{ id: string | null; version: number } | null>(null);
  const inFlightRef = useRef<Promise<boolean> | null>(null);
  const followupRequestedRef = useRef(false);
  const saveAbortRef = useRef<AbortController | null>(null);
  const followupTimerRef = useRef<number | null>(null);
  const reviewAbortRef = useRef<AbortController | null>(null);
  const writeBlockedRef = useRef(false);
  const unauthorizedRef = useRef(onUnauthorized);
  unauthorizedRef.current = onUnauthorized;
  const [document, setDocument] = useState<CanvasDocument | null>(null);
  const [writable, setWritable] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [status, setStatus] = useState<CanvasSaveStatus>("loading");
  const [message, setMessage] = useState<string | null>(null);
  const [reviewedServer, setReviewedServer] = useState<CanvasEnvelope | null>(null);
  const documentRef = useRef(document);
  documentRef.current = document;

  scopeRef.current.key = scopeKey;
  scopeRef.current.serviceIdentity = serviceIdentity;

  useEffect(() => {
    const epoch = scopeRef.current.epoch + 1;
    scopeRef.current = { key: scopeKey, serviceIdentity, epoch };
    const controller = new AbortController();
    let active = true;
    inFlightRef.current = null;
    saveAbortRef.current = null;
    reviewAbortRef.current?.abort();
    reviewAbortRef.current = null;
    writeBlockedRef.current = false;
    followupRequestedRef.current = false;
    dirtySequence.current = 0;
    dirtyRef.current = false;
    statusRef.current = "loading";
    if (followupTimerRef.current !== null) window.clearTimeout(followupTimerRef.current);
    baseRef.current = null;
    setDocument(null);
    setWritable(false);
    setDirty(false);
    setStatus("loading");
    setMessage(null);
    setReviewedServer(null);

    void port.get(chapterId, controller.signal).then((envelope) => {
      if (!active || scopeRef.current.epoch !== epoch || scopeRef.current.key !== scopeKey
        || scopeRef.current.serviceIdentity !== serviceIdentity) return;
      const parsed = parseCanvasDocument(envelope.document_json);
      baseRef.current = { id: envelope.id, version: envelope.version };
      setDocument(parsed);
      const canSave = envelope.save_capability === "guarded-v1" && parsed !== null;
      setWritable(canSave);
      setStatus(canSave ? "ready" : "unsupported");
      if (parsed === null) setMessage("画布内容无法安全读取，已保持只读。");
      else if (!canSave) setMessage("当前服务不支持受控画布保存，内容可查看但不会发送保存请求。");
    }).catch((error: unknown) => {
      if (!active || controller.signal.aborted || scopeRef.current.epoch !== epoch
        || scopeRef.current.key !== scopeKey || scopeRef.current.serviceIdentity !== serviceIdentity) return;
      setStatus(error instanceof CanvasDataError && error.kind === "unauthorized" ? "error" : "error");
      statusRef.current = "error";
      setMessage(error instanceof Error ? error.message : "画布读取失败，请重试。");
      if (error instanceof CanvasDataError && error.kind === "unauthorized") unauthorizedRef.current?.();
    });

    return () => {
      active = false;
      controller.abort();
      saveAbortRef.current?.abort();
      reviewAbortRef.current?.abort();
      if (followupTimerRef.current !== null) window.clearTimeout(followupTimerRef.current);
    };
  }, [chapterId, port, scopeKey, serviceIdentity]);

  const edit = useCallback((next: CanvasDocument) => {
    if (!writable || document === null || documentEqual(document, next)) return;
    dirtySequence.current += 1;
    dirtyRef.current = true;
    setDocument(next);
    setDirty(true);
    if (!writeBlockedRef.current) {
      statusRef.current = "ready";
      setStatus("ready");
      setMessage(null);
    }
  }, [document, writable]);

  const saveNow = useCallback(async (): Promise<boolean> => {
    if (writeBlockedRef.current || statusRef.current === "conflict" || statusRef.current === "unknown") return false;
    if (inFlightRef.current !== null) {
      followupRequestedRef.current = true;
      return inFlightRef.current;
    }
    const currentDocument = document;
    const base = baseRef.current;
    if (!writable || currentDocument === null || base === null || !dirty) return !dirty;

    const sequence = dirtySequence.current;
    const epoch = scopeRef.current.epoch;
    const key = scopeRef.current.key;
    const identity = scopeRef.current.serviceIdentity;
    const controller = new AbortController();
    saveAbortRef.current = controller;
    const guard: CanvasSaveGuard = { mode: "guarded-v1", id: base.id, version: base.version };
    setStatus("saving");
    setMessage(null);

    const operation = port.save(chapterId, currentDocument, guard, controller.signal)
      .then((envelope: CanvasEnvelope) => {
        if (scopeRef.current.epoch !== epoch || scopeRef.current.key !== key
          || scopeRef.current.serviceIdentity !== identity) return false;
        baseRef.current = { id: envelope.id, version: envelope.version };
        if (dirtySequence.current === sequence) {
          dirtyRef.current = false;
          setDirty(false);
          setStatus("ready");
          setMessage(null);
        } else {
          setStatus("ready");
        }
        return dirtySequence.current === sequence;
      })
      .catch((error: unknown) => {
        if (scopeRef.current.epoch !== epoch || scopeRef.current.key !== key
          || scopeRef.current.serviceIdentity !== identity) return false;
        if (error instanceof CanvasDataError) {
          if (error.kind === "conflict") {
            writeBlockedRef.current = true;
            statusRef.current = "conflict";
            setStatus("conflict");
            setMessage("画布已被其他成员更新，草稿仍保留。请先核对服务器内容。");
          } else if (error.kind === "unknown") {
            writeBlockedRef.current = true;
            statusRef.current = "unknown";
            setStatus("unknown");
            setMessage("保存结果暂时无法确认，草稿仍保留；请核对服务器状态后再决定下一步。");
          } else {
            statusRef.current = "error";
            setStatus("error");
            setMessage(error.message);
            if (error.kind === "unauthorized") unauthorizedRef.current?.();
          }
        } else {
          writeBlockedRef.current = true;
          statusRef.current = "unknown";
          setStatus("unknown");
          setMessage("保存结果暂时无法确认，草稿仍保留；请核对服务器状态后再决定下一步。");
        }
        return false;
      })
      .finally(() => {
        if (inFlightRef.current === operation) inFlightRef.current = null;
        if (saveAbortRef.current === controller) saveAbortRef.current = null;
        if (dirtySequence.current !== sequence && dirtyRef.current
          && scopeRef.current.epoch === epoch && scopeRef.current.key === key
          && scopeRef.current.serviceIdentity === identity
          && !writeBlockedRef.current
          && statusRef.current !== "conflict" && statusRef.current !== "unknown") {
          if (followupTimerRef.current !== null) window.clearTimeout(followupTimerRef.current);
          const delay = followupRequestedRef.current ? 0 : 2000;
          followupRequestedRef.current = false;
          followupTimerRef.current = window.setTimeout(() => {
            followupTimerRef.current = null;
            void saveLatestRef.current();
          }, delay);
        } else {
          followupRequestedRef.current = false;
        }
      });
    inFlightRef.current = operation;
    return operation;
  }, [chapterId, dirty, document, port, status, writable]);

  saveLatestRef.current = saveNow;

  const reviewServer = useCallback(async (): Promise<void> => {
    const epoch = scopeRef.current.epoch;
    const key = scopeRef.current.key;
    const identity = scopeRef.current.serviceIdentity;
    reviewAbortRef.current?.abort();
    const controller = new AbortController();
    reviewAbortRef.current = controller;
    setMessage("正在只读核对服务器当前内容…");
    try {
      const envelope = await port.get(chapterId, controller.signal);
      if (controller.signal.aborted || scopeRef.current.epoch !== epoch || scopeRef.current.key !== key
        || scopeRef.current.serviceIdentity !== identity) return;
      setReviewedServer(envelope);
      const parsed = parseCanvasDocument(envelope.document_json);
      const currentDocument = documentRef.current;
      const same = parsed !== null && currentDocument !== null && documentEqual(parsed, currentDocument);
      setMessage(same
        ? "服务器当前内容与本地稿相同；这不会确认之前的保存请求。请选择继续保存本地稿或采用服务器内容。"
        : "已读取服务器当前内容；本地稿仍保留，这不会确认之前的保存请求。请选择下一步。 ");
    } catch (error) {
      if (controller.signal.aborted || scopeRef.current.epoch !== epoch || scopeRef.current.key !== key
        || scopeRef.current.serviceIdentity !== identity) return;
      setMessage(error instanceof Error ? error.message : "服务器内容读取失败，本地稿仍保留。");
    } finally {
      if (reviewAbortRef.current === controller) reviewAbortRef.current = null;
    }
  }, [chapterId, document, port]);

  const resumeReviewedDraft = useCallback(async (): Promise<boolean> => {
    const reviewed = reviewedServer;
    if (reviewed === null || reviewed.save_capability !== "guarded-v1" || document === null
      || parseCanvasDocument(reviewed.document_json) === null) return false;
    baseRef.current = { id: reviewed.id, version: reviewed.version };
    writeBlockedRef.current = false;
    dirtySequence.current += 1;
    dirtyRef.current = true;
    setDirty(true);
    setStatus("ready");
    statusRef.current = "ready";
    setMessage("已按你的选择，以刚核对的服务器版本作为新保存基准。");
    setReviewedServer(null);
    return saveLatestRef.current();
  }, [document, reviewedServer]);

  const adoptReviewedServer = useCallback(() => {
    if (reviewedServer === null) return;
    const parsed = parseCanvasDocument(reviewedServer.document_json);
    if (parsed === null) return;
    baseRef.current = { id: reviewedServer.id, version: reviewedServer.version };
    dirtySequence.current += 1;
    dirtyRef.current = false;
    writeBlockedRef.current = false;
    setDocument(parsed);
    setDirty(false);
    setWritable(reviewedServer.save_capability === "guarded-v1");
    setStatus(reviewedServer.save_capability === "guarded-v1" ? "ready" : "unsupported");
    statusRef.current = reviewedServer.save_capability === "guarded-v1" ? "ready" : "unsupported";
    setMessage("已采用服务器当前内容；之前的请求结果仍以服务器状态为准。");
    setReviewedServer(null);
  }, [reviewedServer]);

  useEffect(() => {
    if (!dirty || !writable || status !== "ready") return;
    const timer = window.setTimeout(() => { void saveNow(); }, 2000);
    return () => window.clearTimeout(timer);
  }, [dirty, saveNow, status, writable]);

  useEffect(() => {
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      if (!dirty && status !== "unknown" && status !== "conflict") return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty, status]);

  const discard = useCallback(() => {
    dirtySequence.current += 1;
    dirtyRef.current = false;
    setDirty(false);
    if (status === "conflict" || status === "unknown") {
      setMessage("本地草稿已放弃；之前未知的写入是否提交仍未确认。");
      return;
    }
    setDocument((current) => current);
  }, [status]);

  const hasPendingWork = useCallback((): boolean => (
    dirtyRef.current
    || inFlightRef.current !== null
    || statusRef.current === "unknown"
    || statusRef.current === "conflict"
  ), []);
  const isClean = useCallback((): boolean => !hasPendingWork(), [hasPendingWork]);

  useEffect(() => {
    statusRef.current = status;
  }, [status]);

  return { document, writable, dirty, status, message, reviewedServer, saveNow, reviewServer,
    resumeReviewedDraft, adoptReviewedServer, edit, discard, hasPendingWork, isClean };
}
