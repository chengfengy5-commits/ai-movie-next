import { useLayoutEffect, useRef, useState } from "react";
import { ApiError, isAbortError } from "../../shared/api/errors";
import { normalizeApiError, type WorkspaceServices } from "../../shared/api/services";
import type { MyTaskRecord } from "../../shared/api/myTasks";
import type { TaskControls } from "../../shared/api/taskControls";
import {
  createTaskControlPendingScope,
  hasTaskControlPendingMarker,
  removeTaskControlPendingMarker,
  TaskControlPendingStorageError,
  writeTaskControlPendingMarker,
  type TaskControlPendingScope,
} from "./taskControlPending";

const OBSERVATION_INTERVAL_MS = 3_000;
const MAX_OBSERVATION_FAILURES = 3;
const ACTIVE_TASK_STATUSES = new Set(["queued", "processing", "cancelling", "reconciling"]);
const TERMINAL_TASK_STATUSES = new Set(["completed", "failed", "cancelled"]);
const KNOWN_CANCEL_REJECTIONS = new Set([401, 403, 404]);

export interface UseTaskControlsOptions {
  task: MyTaskRecord;
  userId: string;
  sessionGeneration: number;
  services: WorkspaceServices;
  onUnauthorized(): void;
  onRefreshReceipt(): void | Promise<void>;
  onCancelAttemptMayHaveReachedServer(): void;
}

export interface TaskControlReadState {
  snapshot: TaskControls | null;
  loading: boolean;
  refreshing: boolean;
  error: string | null;
}

export interface TaskControlPendingState {
  loaded: boolean;
  pending: boolean;
  error: string | null;
}

export type TaskControlPhase =
  | "idle"
  | "checking"
  | "sending"
  | "verifying"
  | "unknown"
  | "observing"
  | "paused";

interface TaskControlOwner {
  services: WorkspaceServices;
  userId: string;
  sessionGeneration: number;
  apiBaseUrl: string | null;
  taskId: string;
}

function sameOwner(left: TaskControlOwner, right: TaskControlOwner): boolean {
  return left.services === right.services
    && left.userId === right.userId
    && left.sessionGeneration === right.sessionGeneration
    && left.apiBaseUrl === right.apiBaseUrl
    && left.taskId === right.taskId;
}

function markerScope(owner: TaskControlOwner): TaskControlPendingScope | null {
  return createTaskControlPendingScope(owner.userId, owner.apiBaseUrl, owner.taskId);
}

function storageErrorMessage(cause: unknown): string {
  return cause instanceof TaskControlPendingStorageError
    ? cause.message
    : "读取本地取消请求记录失败。";
}

function isRetryable(error: ApiError): boolean {
  return error.kind === "network" || error.kind === "timeout"
    || (error.status !== null && error.status >= 500);
}

export function useTaskControls({
  task,
  userId,
  sessionGeneration,
  services,
  onUnauthorized,
  onRefreshReceipt,
  onCancelAttemptMayHaveReachedServer,
}: UseTaskControlsOptions) {
  const owner: TaskControlOwner = {
    services,
    userId,
    sessionGeneration,
    apiBaseUrl: services.apiBaseUrl,
    taskId: task.id,
  };
  const ownerRef = useRef(owner);
  ownerRef.current = owner;
  const ownerGenerationRef = useRef(0);
  const onUnauthorizedRef = useRef(onUnauthorized);
  const onRefreshReceiptRef = useRef(onRefreshReceipt);
  const onCancelAttemptRef = useRef(onCancelAttemptMayHaveReachedServer);
  onUnauthorizedRef.current = onUnauthorized;
  onRefreshReceiptRef.current = onRefreshReceipt;
  onCancelAttemptRef.current = onCancelAttemptMayHaveReachedServer;

  const [controls, setControls] = useState<TaskControlReadState>({
    snapshot: null,
    loading: true,
    refreshing: false,
    error: null,
  });
  const [pendingMarker, setPendingMarker] = useState<TaskControlPendingState>({
    loaded: false,
    pending: false,
    error: null,
  });
  const pendingMarkerRef = useRef(pendingMarker);
  pendingMarkerRef.current = pendingMarker;
  const [phase, setPhase] = useState<TaskControlPhase>("idle");
  const phaseRef = useRef(phase);
  phaseRef.current = phase;
  const [documentVisible, setDocumentVisible] = useState(
    () => typeof document === "undefined" || !document.hidden,
  );
  const documentVisibleRef = useRef(documentVisible);
  documentVisibleRef.current = documentVisible;
  const [automaticallyObserving, setAutomaticallyObserving] = useState(false);
  const automaticallyObservingRef = useRef(false);
  const [confirming, setConfirming] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const observationFailuresRef = useRef(0);
  const controlsControllerRef = useRef<AbortController | null>(null);
  const cancelControllerRef = useRef<AbortController | null>(null);
  const observationTimerRef = useRef<number | null>(null);
  const attemptGenerationRef = useRef(0);
  const operationInFlightRef = useRef(false);
  const scheduleObservationRef = useRef<(
    expectedOwner: TaskControlOwner,
    ownerGeneration: number,
    delay: number,
  ) => void>(() => undefined);

  function isCurrent(expectedOwner: TaskControlOwner, ownerGeneration: number): boolean {
    return ownerGenerationRef.current === ownerGeneration && sameOwner(ownerRef.current, expectedOwner);
  }

  function setPending(value: TaskControlPendingState): void {
    pendingMarkerRef.current = value;
    setPendingMarker(value);
  }

  function setAutomaticObservationActive(active: boolean): void {
    if (automaticallyObservingRef.current === active) return;
    automaticallyObservingRef.current = active;
    setAutomaticallyObserving(active);
  }

  function clearObservationTimer(): void {
    if (observationTimerRef.current !== null) {
      window.clearTimeout(observationTimerRef.current);
      observationTimerRef.current = null;
    }
    setAutomaticObservationActive(false);
  }

  function scheduleObservation(expectedOwner: TaskControlOwner, ownerGeneration: number, delay: number): void {
    if (observationTimerRef.current !== null) {
      window.clearTimeout(observationTimerRef.current);
      observationTimerRef.current = null;
    }
    if (!pendingMarkerRef.current.pending || services.getTaskControls === undefined
      || !documentVisibleRef.current || (typeof document !== "undefined" && document.hidden)) {
      setAutomaticObservationActive(false);
      return;
    }
    setAutomaticObservationActive(true);
    observationTimerRef.current = window.setTimeout(() => {
      observationTimerRef.current = null;
      if (isCurrent(expectedOwner, ownerGeneration)
        && pendingMarkerRef.current.pending
        && phaseRef.current !== "paused"
        && documentVisibleRef.current
        && (typeof document === "undefined" || !document.hidden)) {
        void readControls(expectedOwner, ownerGeneration, "automatic", true);
      }
    }, delay);
  }
  scheduleObservationRef.current = scheduleObservation;

  async function readControls(
    expectedOwner: TaskControlOwner,
    ownerGeneration: number,
    source: "initial" | "manual" | "automatic" | "confirm" | "post",
    refreshReceipt: boolean,
  ): Promise<TaskControls | null> {
    if (!isCurrent(expectedOwner, ownerGeneration)) return null;
    const getTaskControls = expectedOwner.services.getTaskControls;
    if (expectedOwner.services.mode !== "api" || getTaskControls === undefined) {
      setControls({ snapshot: null, loading: false, refreshing: false, error: null });
      return null;
    }
    controlsControllerRef.current?.abort();
    const controller = new AbortController();
    controlsControllerRef.current = controller;
    const hasSnapshot = controls.snapshot !== null;
    setControls((previous) => ({
      ...previous,
      loading: !hasSnapshot,
      refreshing: hasSnapshot,
      error: null,
    }));
    if (source === "manual") observationFailuresRef.current = 0;

    try {
      const result = await getTaskControls(expectedOwner.taskId, controller.signal);
      if (!isCurrent(expectedOwner, ownerGeneration) || controller.signal.aborted) return null;
      if (controlsControllerRef.current === controller) controlsControllerRef.current = null;
      setControls({ snapshot: result, loading: false, refreshing: false, error: null });

      let hasPending = pendingMarkerRef.current.pending;
      const scope = markerScope(expectedOwner);
      try {
        if (scope !== null && hasTaskControlPendingMarker(scope)) hasPending = true;
        setPending({ loaded: true, pending: hasPending, error: null });
      } catch (cause) {
        setPending({ loaded: true, pending: hasPending, error: storageErrorMessage(cause) });
      }

      if (hasPending && TERMINAL_TASK_STATUSES.has(result.status) && scope !== null) {
        try {
          removeTaskControlPendingMarker(scope);
          hasPending = false;
          setPending({ loaded: true, pending: false, error: null });
          setMessage("服务器当前任务记录为终态；这不证明取消请求导致了该状态，也不证明上游停止或费用已退还。");
          setPhase("idle");
        } catch (cause) {
          setPending({ loaded: true, pending: true, error: storageErrorMessage(cause) });
        }
      }

      if (refreshReceipt) {
        try {
          await onRefreshReceiptRef.current();
        } catch {
          // Receipt refresh reports its own error and remains independent of controls.
        }
        if (!isCurrent(expectedOwner, ownerGeneration)) return null;
      }

      if (hasPending && ACTIVE_TASK_STATUSES.has(result.status)) {
        setPhase((current) => current === "paused" || current === "unknown" ? current : "observing");
        scheduleObservation(expectedOwner, ownerGeneration, OBSERVATION_INTERVAL_MS);
      } else {
        clearObservationTimer();
      }
      observationFailuresRef.current = 0;
      return result;
    } catch (cause: unknown) {
      if (!isCurrent(expectedOwner, ownerGeneration) || controller.signal.aborted || isAbortError(cause)) return null;
      if (controlsControllerRef.current === controller) controlsControllerRef.current = null;
      const error = normalizeApiError(cause);
      setControls((previous) => ({ ...previous, loading: false, refreshing: false, error: error.message }));
      if (error.status === 401) {
        setPhase("paused");
        clearObservationTimer();
        onUnauthorizedRef.current();
        return null;
      }

      if (refreshReceipt) {
        try {
          await onRefreshReceiptRef.current();
        } catch {
          // Receipt refresh reports its own error and remains independent of controls.
        }
        if (!isCurrent(expectedOwner, ownerGeneration)) return null;
      }

      if ((source === "automatic" || source === "post" || source === "initial")
        && pendingMarkerRef.current.pending) {
        if (!isRetryable(error)) {
          clearObservationTimer();
          if (source !== "initial") {
            setPhase("paused");
            setMessage("自动核对已停止；取消状态仍未确认。可以手动重新核对，不会再次提交取消请求。");
          }
          return null;
        }
        const failures = observationFailuresRef.current + 1;
        observationFailuresRef.current = failures;
        if (failures >= MAX_OBSERVATION_FAILURES) {
          setPhase("paused");
          clearObservationTimer();
          setMessage("自动核对连续失败 3 次，已暂停；可以手动重新核对，不会再次提交取消请求。");
          return null;
        }
        scheduleObservation(expectedOwner, ownerGeneration, OBSERVATION_INTERVAL_MS * (2 ** failures));
      }
      return null;
    }
  }

  useLayoutEffect(() => {
    const ownerGeneration = ownerGenerationRef.current + 1;
    ownerGenerationRef.current = ownerGeneration;
    controlsControllerRef.current?.abort();
    cancelControllerRef.current?.abort();
    controlsControllerRef.current = null;
    cancelControllerRef.current = null;
    clearObservationTimer();
    attemptGenerationRef.current += 1;
    operationInFlightRef.current = false;
    setControls({ snapshot: null, loading: true, refreshing: false, error: null });
    setPending({ loaded: false, pending: false, error: null });
    setPhase("idle");
    setConfirming(false);
    setMessage(null);
    observationFailuresRef.current = 0;

    const scope = markerScope(owner);
    if (scope === null) {
      setPending({ loaded: true, pending: false, error: "当前账号或服务地址不完整，取消操作保持关闭。" });
    } else {
      try {
        setPending({ loaded: true, pending: hasTaskControlPendingMarker(scope), error: null });
      } catch (cause) {
        setPending({ loaded: true, pending: false, error: storageErrorMessage(cause) });
      }
    }
    return () => {
      if (ownerGenerationRef.current === ownerGeneration) ownerGenerationRef.current += 1;
      controlsControllerRef.current?.abort();
      cancelControllerRef.current?.abort();
      controlsControllerRef.current = null;
      cancelControllerRef.current = null;
      clearObservationTimer();
      operationInFlightRef.current = false;
    };
  }, [services, userId, sessionGeneration, task.id, services.apiBaseUrl]);

  useLayoutEffect(() => {
    const handleVisibilityChange = () => {
      const visible = !document.hidden;
      documentVisibleRef.current = visible;
      setDocumentVisible(visible);
      if (!visible) {
        clearObservationTimer();
        return;
      }
      if (pendingMarkerRef.current.pending && phaseRef.current !== "paused") {
        scheduleObservationRef.current(ownerRef.current, ownerGenerationRef.current, OBSERVATION_INTERVAL_MS);
      }
    };
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => document.removeEventListener("visibilitychange", handleVisibilityChange);
  }, []);

  useLayoutEffect(() => {
    const ownerGeneration = ownerGenerationRef.current;
    void readControls(owner, ownerGeneration, "initial", false);
  }, [services, userId, sessionGeneration, task.id, services.apiBaseUrl]);

  function beginConfirmation(): void {
    if (controls.snapshot?.cancel.allowed !== true || pendingMarker.pending || !pendingMarker.loaded
      || pendingMarker.error !== null || services.cancelTask === undefined || phase !== "idle") return;
    setConfirming(true);
    setMessage(null);
  }

  function cancelConfirmation(): void {
    if (phase === "checking") return;
    setConfirming(false);
    setMessage(null);
  }

  async function confirmCancellation(): Promise<void> {
    if (operationInFlightRef.current || !confirming || phase !== "idle") return;
    const expectedOwner = owner;
    const ownerGeneration = ownerGenerationRef.current;
    const scope = markerScope(expectedOwner);
    if (scope === null) {
      setMessage("当前账号或服务地址不完整；没有发送取消请求。");
      return;
    }
    if (services.cancelTask === undefined || services.getTaskControls === undefined) {
      setMessage("当前服务未提供可核对的取消接口；没有发送取消请求。");
      return;
    }

    operationInFlightRef.current = true;
    const attempt = ++attemptGenerationRef.current;
    setPhase("checking");
    setMessage("正在重新核对服务端能力…");
    try {
      let existingMarker: boolean;
      try {
        existingMarker = hasTaskControlPendingMarker(scope);
      } catch (cause) {
        setPending({ loaded: true, pending: pendingMarkerRef.current.pending, error: storageErrorMessage(cause) });
        setConfirming(false);
        setPhase("idle");
        setMessage("无法读取本地待核对记录；没有发送取消请求。");
        return;
      }
      if (existingMarker || pendingMarkerRef.current.pending) {
        setPending({ loaded: true, pending: true, error: null });
        setConfirming(false);
        setPhase("unknown");
        setMessage("已有尚未解决的取消请求；这里只能读取核对，不会再次提交。");
        await readControls(expectedOwner, ownerGeneration, "manual", true);
        return;
      }

      const fresh = await readControls(expectedOwner, ownerGeneration, "confirm", false);
      if (!isCurrent(expectedOwner, ownerGeneration) || attempt !== attemptGenerationRef.current) return;
      if (fresh === null) {
        setConfirming(false);
        setPhase("idle");
        setMessage("无法核对当前取消能力；没有发送取消请求。");
        return;
      }
      if (!fresh.cancel.allowed || fresh.cancel.reason !== "available") {
        setConfirming(false);
        setPhase("idle");
        setMessage("任务能力已变化；取消请求未发送。请查看最新状态。");
        return;
      }

      try {
        const markerResult = writeTaskControlPendingMarker(scope);
        if (markerResult === "already_present") {
          setPending({ loaded: true, pending: true, error: null });
          setConfirming(false);
          setPhase("unknown");
          setMessage("已有尚未解决的取消请求；这里只能读取核对，不会再次提交。");
          return;
        }
      } catch (cause) {
        setPending({ loaded: true, pending: false, error: storageErrorMessage(cause) });
        setConfirming(false);
        setPhase("idle");
        setMessage("无法安全保存本地待核对标记；没有发送取消请求。");
        return;
      }

      setPending({ loaded: true, pending: true, error: null });
      setConfirming(false);
      setPhase("sending");
      setMessage("已保存本地待核对标记，正在发送一次取消请求…");
      const controller = new AbortController();
      cancelControllerRef.current = controller;
      try {
        const acknowledgement = await services.cancelTask(expectedOwner.taskId, controller.signal);
        if (!isCurrent(expectedOwner, ownerGeneration) || controller.signal.aborted
          || attempt !== attemptGenerationRef.current) return;
        if (cancelControllerRef.current === controller) cancelControllerRef.current = null;
        setPhase("verifying");
        setMessage(`取消接口已返回状态记录“${acknowledgement.status}”；正在独立核对任务状态。`);
        onCancelAttemptRef.current();
        await readControls(expectedOwner, ownerGeneration, "post", true);
        if (isCurrent(expectedOwner, ownerGeneration) && attempt === attemptGenerationRef.current) {
          setPhase((current) => current === "paused" || current === "unknown"
            ? current
            : pendingMarkerRef.current.pending ? "observing" : "idle");
        }
      } catch (cause: unknown) {
        if (!isCurrent(expectedOwner, ownerGeneration) || controller.signal.aborted
          || attempt !== attemptGenerationRef.current) return;
        if (cancelControllerRef.current === controller) cancelControllerRef.current = null;
        const error = normalizeApiError(cause);
        if (KNOWN_CANCEL_REJECTIONS.has(error.status ?? -1)) {
          try {
            removeTaskControlPendingMarker(scope);
            setPending({ loaded: true, pending: false, error: null });
          } catch (storageCause) {
            setPending({ loaded: true, pending: true, error: storageErrorMessage(storageCause) });
          }
          setPhase("idle");
          setConfirming(false);
          setMessage(error.status === 401
            ? "登录状态已失效，取消请求被拒绝。请重新登录后读取任务状态。"
            : error.status === 403
              ? "当前账号无权取消此任务；请求被拒绝。"
              : "任务不存在或已不属于当前账号；请求被拒绝。任务记录将保留。");
          if (error.status === 401) onUnauthorizedRef.current();
          return;
        }

        setPhase("unknown");
        setMessage("取消请求结果尚未确认；只读取服务端状态，不会自动再次提交。");
        onCancelAttemptRef.current();
        await readControls(expectedOwner, ownerGeneration, "post", true);
      }
    } finally {
      if (attempt === attemptGenerationRef.current) operationInFlightRef.current = false;
    }
  }

  async function verifyNow(): Promise<void> {
    if (phase === "checking" || phase === "sending" || phase === "verifying") return;
    observationFailuresRef.current = 0;
    setPhase(pendingMarkerRef.current.pending ? "unknown" : "idle");
    setMessage(pendingMarkerRef.current.pending
      ? "正在只读核对；不会再次提交取消请求。"
      : null);
    await readControls(owner, ownerGenerationRef.current, "manual", true);
  }

  const isApiService = services.mode === "api" && services.apiBaseUrl !== null;
  const canSend = isApiService
    && services.getTaskControls !== undefined
    && services.cancelTask !== undefined
    && controls.snapshot?.cancel.allowed === true
    && controls.error === null
    && !controls.refreshing
    && pendingMarker.loaded
    && !pendingMarker.pending
    && pendingMarker.error === null
    && phase === "idle";

  return {
    controls,
    pendingMarker,
    phase,
    automaticallyObserving,
    confirming,
    message,
    canSend,
    isApiService,
    beginConfirmation,
    cancelConfirmation,
    confirmCancellation,
    verifyNow,
  };
}
