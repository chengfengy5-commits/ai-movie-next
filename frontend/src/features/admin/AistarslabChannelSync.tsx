import { useEffect, useRef, useState } from "react";
import {
  AistarslabApiError,
  type AistarslabApi,
  type AistarslabSyncResult,
} from "./aistarslabApi";

export interface AistarslabChannelSyncExitNotice {
  kind: "write-in-flight" | "unknown-write";
}

interface Props {
  api: AistarslabApi;
  actorId: string;
  sessionGeneration: number;
  viewIdentity: number;
  onUnauthorized(): void;
  onActivityChange(owner: symbol, active: boolean): void;
  onExitStateChange(owner: symbol, notice: AistarslabChannelSyncExitNotice | null): void;
  onModelsChanged(): void;
}

interface SyncAttempt {
  controller: AbortController;
  generation: number;
  api: AistarslabApi;
  actorId: string;
  sessionGeneration: number;
  viewIdentity: number;
}

type SyncPhase = "writing" | "unknown" | null;

function isUnknownFailure(error: unknown): boolean {
  if (error instanceof AistarslabApiError) {
    return error.kind === "network"
      || error.kind === "timeout"
      || error.kind === "aborted"
      || error.kind === "invalid-response"
      || (error.kind === "http" && error.status !== null && (error.status === 408 || error.status >= 500));
  }
  return true;
}

function failureMessage(error: unknown): string {
  if (!(error instanceof AistarslabApiError)) return "同步失败，请检查管理服务后重试。";
  if (error.status === 401) return "管理员登录已失效，请重新登录。";
  if (error.status === 403) return "当前管理员账号没有执行渠道同步的权限。";
  if (error.status !== null) return `同步失败（HTTP ${error.status}）。`;
  if (error.kind === "timeout") return "同步请求超时。";
  if (error.kind === "network") return "无法连接管理服务。";
  return "同步失败，请检查管理服务后重试。";
}

function successMessage(result: AistarslabSyncResult): string {
  return `同步完成：新增 ${result.created} 个，更新 ${result.updated} 个，共 ${result.total} 个。`;
}

export function AistarslabChannelSync({
  api,
  actorId,
  sessionGeneration,
  viewIdentity,
  onUnauthorized,
  onActivityChange,
  onExitStateChange,
  onModelsChanged,
}: Props) {
  const owner = useRef(Symbol("aistarslab-channel-sync")).current;
  const propsRef = useRef({
    api,
    actorId,
    sessionGeneration,
    viewIdentity,
    onUnauthorized,
    onActivityChange,
    onExitStateChange,
    onModelsChanged,
  });
  propsRef.current = {
    api,
    actorId,
    sessionGeneration,
    viewIdentity,
    onUnauthorized,
    onActivityChange,
    onExitStateChange,
    onModelsChanged,
  };

  const generationRef = useRef(0);
  const attemptRef = useRef<SyncAttempt | null>(null);
  const mountedRef = useRef(false);
  const [phase, setPhase] = useState<SyncPhase>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  function isCurrent(attempt: SyncAttempt): boolean {
    const current = propsRef.current;
    return mountedRef.current
      && attemptRef.current === attempt
      && generationRef.current === attempt.generation
      && current.api === attempt.api
      && current.actorId === attempt.actorId
      && current.sessionGeneration === attempt.sessionGeneration
      && current.viewIdentity === attempt.viewIdentity;
  }

  function release(attempt: SyncAttempt): void {
    if (attemptRef.current !== attempt) return;
    attemptRef.current = null;
    propsRef.current.onActivityChange(owner, false);
    propsRef.current.onExitStateChange(owner, null);
    setPhase(null);
  }

  useEffect(() => {
    mountedRef.current = true;
    setPhase(null);
    setMessage(null);
    setError(null);
    propsRef.current.onActivityChange(owner, false);
    return () => {
      mountedRef.current = false;
      generationRef.current += 1;
      const attempt = attemptRef.current;
      attemptRef.current = null;
      attempt?.controller.abort();
      propsRef.current.onExitStateChange(owner, null);
    };
  }, [api, actorId, owner, sessionGeneration, viewIdentity]);

  async function synchronize(): Promise<void> {
    if (attemptRef.current !== null) return;
    if (!window.confirm(
      "将使用服务器已保存的 AIStarsLab 凭据读取图片和视频渠道配置。已有 AIStarsLab 系统模型会更新名称、参数、计费、介绍和线路地址并保留启用状态；新模型会创建为未启用。此操作会修改系统模型列表，是否继续？",
    )) return;

    const current = propsRef.current;
    const attempt: SyncAttempt = {
      controller: new AbortController(),
      generation: ++generationRef.current,
      api: current.api,
      actorId: current.actorId,
      sessionGeneration: current.sessionGeneration,
      viewIdentity: current.viewIdentity,
    };
    attemptRef.current = attempt;
    setPhase("writing");
    setMessage(null);
    setError(null);
    current.onActivityChange(owner, true);
    current.onExitStateChange(owner, { kind: "write-in-flight" });

    try {
      const result = await attempt.api.syncAll(attempt.controller.signal);
      if (!isCurrent(attempt)) return;
      release(attempt);
      setMessage(successMessage(result));
      propsRef.current.onModelsChanged();
    } catch (cause: unknown) {
      if (!isCurrent(attempt)) return;
      if (cause instanceof AistarslabApiError && cause.status === 401) {
        release(attempt);
        propsRef.current.onUnauthorized();
        return;
      }
      if (isUnknownFailure(cause)) {
        setPhase("unknown");
        setMessage("同步结果未知；服务端可能已经处理图片和视频渠道。不会自动重试。刷新目录只显示当前状态，不能证明本次同步成功。");
        propsRef.current.onActivityChange(owner, true);
        propsRef.current.onExitStateChange(owner, { kind: "unknown-write" });
        return;
      }
      release(attempt);
      setError(failureMessage(cause));
    }
  }

  function stopTrackingAndRefresh(): void {
    const attempt = attemptRef.current;
    if (phase !== "unknown" || attempt === null) return;
    attempt.controller.abort();
    release(attempt);
    setError(null);
    setMessage("已停止本地跟踪并刷新模型目录；目录内容不能证明刚才的同步已成功。若仍需同步，请重新发起并确认。");
    propsRef.current.onModelsChanged();
  }

  return (
    <section className="admin-aistarslab-sync" aria-label="AIStarsLab 渠道同步">
      <div className="admin-aistarslab-sync-actions">
        <button
          className="admin-button admin-button-secondary"
          type="button"
          disabled={phase !== null}
          onClick={() => { void synchronize(); }}
        >
          {phase === "writing" ? "正在同步 AIStarsLab 渠道…" : "同步 AIStarsLab 渠道"}
        </button>
        {phase === "unknown" ? (
          <button className="admin-button admin-button-secondary" type="button" onClick={stopTrackingAndRefresh}>
            停止本地跟踪并刷新目录
          </button>
        ) : null}
      </div>
      {message ? <p className="admin-aistarslab-sync-message" role={phase === "unknown" ? "alert" : "status"}>{message}</p> : null}
      {error ? <p className="admin-aistarslab-sync-error" role="alert">{error}</p> : null}
    </section>
  );
}
