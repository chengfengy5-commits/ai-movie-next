import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { ApiError } from "../../shared/api/errors";
import type { ZhangyugePatServicePort } from "../../shared/api/zhangyugePat";
import "./zhangyugePat.css";

type UnknownPatWrite = "save" | "clear";

interface ZhangyugePatPanelProps {
  actorId: string;
  isSuperuser: boolean;
  service?: ZhangyugePatServicePort | undefined;
  onUnauthorized(): void;
  onClose(): void;
  onCloseGuardChange(guard: () => boolean): void;
}

function safeErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return "登录状态已失效，请重新登录。";
    if (error.status === 403) return "当前账号没有管理员权限。";
    if (error.kind === "network") return "无法连接服务，请检查服务状态后重试。";
    if (error.kind === "timeout") return "请求超时，请先刷新配置状态再决定是否重试。";
    if (error.kind === "invalid-response") return "服务返回的数据无法识别。";
    if (error.status !== null && error.status >= 400 && error.status < 500) {
      return `请求被拒绝（HTTP ${error.status}）。`;
    }
  }
  return "暂时无法完成请求，请刷新配置状态后再试。";
}

function isAmbiguousWriteFailure(error: unknown): boolean {
  if (!(error instanceof ApiError)) return true;
  return error.kind === "network"
    || error.kind === "timeout"
    || error.kind === "invalid-response"
    || error.kind === "aborted"
    || error.status === 408
    || (error.status !== null && error.status >= 500);
}

export function ZhangyugePatPanel({
  actorId,
  isSuperuser,
  service,
  onUnauthorized,
  onClose,
  onCloseGuardChange,
}: ZhangyugePatPanelProps) {
  const [configured, setConfigured] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [pat, setPat] = useState("");
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [unknownWrite, setUnknownWrite] = useState<UnknownPatWrite | null>(null);

  const generation = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const requestLock = useRef(false);
  const unknownWriteRef = useRef<UnknownPatWrite | null>(null);
  const currentScope = useRef({ actorId, isSuperuser, service, onUnauthorized });
  const previousScope = useRef({ actorId, isSuperuser, service });
  currentScope.current = { actorId, isSuperuser, service, onUnauthorized };

  const isCurrent = useCallback((ticket: number, signal: AbortSignal): boolean => (
    generation.current === ticket
    && !signal.aborted
    && currentScope.current.actorId === actorId
    && currentScope.current.isSuperuser === isSuperuser
    && currentScope.current.service === service
  ), [actorId, isSuperuser, service]);

  useLayoutEffect(() => {
    const previous = previousScope.current;
    if (previous.actorId === actorId && previous.isSuperuser === isSuperuser && previous.service === service) return;
    previousScope.current = { actorId, isSuperuser, service };
    generation.current += 1;
    controller.current?.abort();
    controller.current = null;
    requestLock.current = false;
    unknownWriteRef.current = null;
    setConfigured(null);
    setPat("");
    setStatusMessage(null);
    setErrorMessage(null);
    setUnknownWrite(null);
    setBusy(false);
    setLoading(isSuperuser && service !== undefined);
  }, [actorId, isSuperuser, service]);

  const readStatus = useCallback(async (initial = false): Promise<void> => {
    if (!isSuperuser) {
      setLoading(false);
      return;
    }
    if (service === undefined) {
      setLoading(false);
      setErrorMessage("章鱼哥 PAT 配置服务暂不可用。");
      return;
    }
    if (requestLock.current) return;

    requestLock.current = true;
    const ticket = ++generation.current;
    const abortController = new AbortController();
    controller.current = abortController;
    setBusy(true);
    setErrorMessage(null);
    if (initial) setLoading(true);

    try {
      const result = await service.getStatus(abortController.signal);
      if (!isCurrent(ticket, abortController.signal)) return;
      setConfigured(result.configured);
      setStatusMessage(result.configured ? "已检测到可用 PAT。" : "未检测到可用 PAT。");
    } catch (error) {
      if (!isCurrent(ticket, abortController.signal)) return;
      if (error instanceof ApiError && error.status === 401) currentScope.current.onUnauthorized();
      setErrorMessage(safeErrorMessage(error));
    } finally {
      if (isCurrent(ticket, abortController.signal)) {
        requestLock.current = false;
        controller.current = null;
        setBusy(false);
        if (initial) setLoading(false);
      }
    }
  }, [isCurrent, isSuperuser, service]);

  useEffect(() => {
    void readStatus(true);
    return () => {
      generation.current += 1;
      controller.current?.abort();
      controller.current = null;
      requestLock.current = false;
    };
  }, [readStatus]);

  const requestClose = useCallback((): boolean => {
    if (requestLock.current) return false;
    if (unknownWriteRef.current !== null) {
      return window.confirm("PAT 写入结果尚未确认。关闭只会停止本地跟踪，服务端设置可能已经生效。仍要关闭吗？");
    }
    if (pat.trim() !== "") {
      return window.confirm("新 PAT 尚未保存，关闭将清除这份输入。继续吗？");
    }
    return true;
  }, [pat]);

  useLayoutEffect(() => {
    onCloseGuardChange(requestClose);
    return () => onCloseGuardChange(() => true);
  }, [onCloseGuardChange, requestClose]);

  async function writePat(kind: UnknownPatWrite, value: string): Promise<void> {
    if (!isSuperuser || service === undefined || requestLock.current || unknownWriteRef.current !== null) return;
    if (kind === "save" && value.trim() === "") {
      setErrorMessage("请输入新的 PAT；清空配置请使用单独的清空操作。");
      return;
    }

    requestLock.current = true;
    const ticket = ++generation.current;
    const abortController = new AbortController();
    controller.current = abortController;
    setBusy(true);
    setErrorMessage(null);
    setStatusMessage(null);

    try {
      await service.save(value, abortController.signal);
      if (!isCurrent(ticket, abortController.signal)) return;
      unknownWriteRef.current = null;
      setUnknownWrite(null);
      setConfigured(kind === "save");
      setPat("");
      setStatusMessage(kind === "save" ? "PAT 已保存。" : "PAT 已清空。");
    } catch (error) {
      if (!isCurrent(ticket, abortController.signal)) return;
      if (error instanceof ApiError && error.status === 401) currentScope.current.onUnauthorized();
      if (isAmbiguousWriteFailure(error)) {
        unknownWriteRef.current = kind;
        setUnknownWrite(kind);
        setStatusMessage(null);
        setErrorMessage(null);
      } else {
        setErrorMessage(safeErrorMessage(error));
      }
    } finally {
      if (isCurrent(ticket, abortController.signal)) {
        requestLock.current = false;
        controller.current = null;
        setBusy(false);
      }
    }
  }

  function clearPat(): void {
    if (configured !== true || requestLock.current || unknownWriteRef.current !== null) return;
    if (!window.confirm("确认清空已保存的章鱼哥 PAT 吗？此操作会移除当前配置。")) return;
    void writePat("clear", "");
  }

  function stopTracking(): void {
    if (requestLock.current || unknownWriteRef.current === null) return;
    unknownWriteRef.current = null;
    setUnknownWrite(null);
    setPat("");
    setErrorMessage(null);
    setStatusMessage("已停止本地跟踪；服务端 PAT 设置可能已经生效。下一次写入需要重新输入 PAT。");
  }

  return (
    <section
      aria-busy={busy || loading}
      aria-labelledby="creator-zhangyuge-pat-title"
      className="zhangyuge-pat-panel"
      id="creator-zhangyuge-pat-panel"
    >
      <header className="zhangyuge-pat-panel-header">
        <div>
          <h2 id="creator-zhangyuge-pat-title">章鱼哥 PAT 配置</h2>
          <p>只读取是否已配置；新 PAT 仅用于当前显式保存，不会回显或写入本地存储。</p>
        </div>
        <button disabled={busy || loading} onClick={() => { if (requestClose()) onClose(); }} type="button">关闭 PAT 设置</button>
      </header>

      {loading && <p className="zhangyuge-pat-message" role="status">正在读取 PAT 配置状态…</p>}
      {!loading && statusMessage !== null && <p className="zhangyuge-pat-message" role="status">{statusMessage}</p>}
      {errorMessage !== null && <p className="zhangyuge-pat-error" role="alert">{errorMessage}</p>}
      {unknownWrite !== null && (
        <div className="zhangyuge-pat-unknown" role="alert">
          <p>提交结果未确认，不会自动重试。服务端设置可能已经生效；配置布尔值不能证明本次 PAT 内容。</p>
          <button disabled={busy} onClick={stopTracking} type="button">停止本地跟踪</button>
        </div>
      )}

      <label className="zhangyuge-pat-input">
        <span>输入新的 PAT</span>
        <input
          autoComplete="new-password"
          disabled={busy || loading || service === undefined || unknownWrite !== null}
          onChange={(event) => { setPat(event.target.value); setErrorMessage(null); }}
          type="password"
          value={pat}
        />
      </label>

      <div className="zhangyuge-pat-actions">
        <button disabled={busy || loading || service === undefined} onClick={() => void readStatus()} type="button">
          {busy ? "处理中…" : "刷新配置状态"}
        </button>
        <button disabled={busy || loading || service === undefined || unknownWrite !== null} onClick={() => void writePat("save", pat)} type="button">
          保存 PAT
        </button>
        <button disabled={busy || loading || service === undefined || configured !== true || unknownWrite !== null} onClick={clearPat} type="button">
          清空已保存 PAT
        </button>
      </div>
    </section>
  );
}
