import { useEffect, useRef, useState } from "react";
import type { AdminApi, AdminModel } from "./adminTypes";
import {
  AistarslabApiError,
  type AistarslabApi,
  type AistarslabBuiltParams,
  type AistarslabCreatedSystemModel,
  type AistarslabLineChoice,
  type AistarslabModelCategory,
} from "./aistarslabApi";

export interface AistarslabQuickConfigExitNotice {
  kind: "write-in-flight" | "unknown-write" | "created-needs-readback";
  message: string;
}

interface Props {
  api: AistarslabApi | undefined;
  adminApi: AdminApi;
  actorId: string;
  sessionGeneration: number;
  mode: "create" | "edit";
  modelId: string | undefined;
  category: string;
  provider: string;
  apiKey: string;
  draftGeneration: number;
  onUnauthorized(): void;
  onApply(result: AistarslabBuiltParams): void;
  onCreated(): void;
  onWriteActivityChange(active: boolean): void;
  onExitStateChange(owner: symbol, notice: AistarslabQuickConfigExitNotice | null): void;
}

type RequestKind = "config" | "build" | "create" | "readback";

interface RequestOwner {
  controller: AbortController;
  generation: number;
  kind: RequestKind;
  api: AistarslabApi;
  adminApi: AdminApi;
  actorId: string;
  sessionGeneration: number;
  mode: "create" | "edit";
  modelId: string | undefined;
  category: string;
  provider: string;
  draftGeneration: number;
  selectionGeneration: number;
}

interface BuiltSelection {
  value: AistarslabBuiltParams;
  generation: number;
  draftGeneration: number;
  selectionGeneration: number;
  owner: RequestOwner;
}

type CreateState =
  | { kind: "writing" }
  | { kind: "unknown"; message: string }
  | { kind: "readback"; created: AistarslabCreatedSystemModel }
  | { kind: "readback-failed"; created: AistarslabCreatedSystemModel; message: string }
  | null;

function safeFailureMessage(error: unknown, action: string): string {
  if (error instanceof AistarslabApiError) {
    if (error.status === 403) return "权限不足，当前管理员账号不能执行此操作。";
    if (error.status === 401) return "管理员登录已失效，请重新登录。";
    if (error.status !== null) return `${action}失败（HTTP ${error.status}）。`;
    if (error.kind === "timeout") return `${action}请求超时。`;
    if (error.kind === "network") return `${action}时无法连接管理服务。`;
    return `${action}失败，请重试。`;
  }
  if (typeof error === "object" && error !== null && "status" in error) {
    const status = (error as { status?: unknown }).status;
    if (status === 403) return "权限不足，当前管理员账号不能执行此操作。";
    if (status === 401) return "管理员登录已失效，请重新登录。";
    if (typeof status === "number") return `${action}失败（HTTP ${status}）。`;
  }
  return `${action}失败，请重试。`;
}

function isUnknownCreateFailure(error: unknown): boolean {
  if (error instanceof AistarslabApiError) {
    return error.kind === "network"
      || error.kind === "timeout"
      || error.kind === "invalid-response"
      || (error.kind === "http" && error.status !== null && (error.status === 408 || error.status >= 500));
  }
  if (typeof error === "object" && error !== null && "status" in error) {
    const status = (error as { status?: unknown }).status;
    if (typeof status === "number") return status === 408 || status >= 500;
  }
  return true;
}

function modelMatchesCreatedRecord(model: AdminModel, created: AistarslabCreatedSystemModel): boolean {
  return model.id === created.id
    && model.category === created.category
    && model.provider === "aistarslab"
    && model.model === created.model
    && model.name === created.name;
}

function modelLabel(category: string): string {
  return category === "image" ? "图片" : "视频";
}

function categoryLines(category: string, config: {
  imageConfig: AistarslabLineChoice[];
  videoConfig: AistarslabLineChoice[];
}): AistarslabLineChoice[] {
  return category === "image" ? config.imageConfig : config.videoConfig;
}

export function AistarslabQuickConfig(props: Props) {
  const owner = useRef(Symbol("aistarslab-quick-config")).current;
  const propsRef = useRef(props);
  propsRef.current = props;
  const generationRef = useRef(0);
  const selectionGenerationRef = useRef(0);
  const requestRef = useRef<RequestOwner | null>(null);
  const createAttemptedRef = useRef(false);
  const draftGenerationRef = useRef(props.draftGeneration);

  const [lines, setLines] = useState<AistarslabLineChoice[] | null>(null);
  const [selectedChannel, setSelectedChannel] = useState("");
  const [selectedModel, setSelectedModel] = useState("");
  const [built, setBuilt] = useState<BuiltSelection | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [createState, setCreateState] = useState<CreateState>(null);

  const categoryName = modelLabel(props.category);

  function notifyExitState(notice: AistarslabQuickConfigExitNotice | null) {
    propsRef.current.onExitStateChange(owner, notice);
  }

  function setWriteActivity(active: boolean) {
    propsRef.current.onWriteActivityChange(active);
  }

  useEffect(() => {
    const generation = ++generationRef.current;
    requestRef.current?.controller.abort();
    requestRef.current = null;
    selectionGenerationRef.current += 1;
    createAttemptedRef.current = false;
    draftGenerationRef.current = props.draftGeneration;
    setLines(null);
    setSelectedChannel("");
    setSelectedModel("");
    setBuilt(null);
    setBusy(false);
    setMessage(null);
    setError(null);
    setCreateState(null);
    setWriteActivity(false);
    notifyExitState(null);

    return () => {
      if (generationRef.current === generation) generationRef.current += 1;
      const active = requestRef.current;
      if (active?.generation === generation) {
        active.controller.abort();
        requestRef.current = null;
      }
      setWriteActivity(false);
      notifyExitState(null);
    };
  }, [
    props.adminApi,
    props.api,
    props.actorId,
    props.category,
    props.modelId,
    props.mode,
    props.provider,
    props.sessionGeneration,
  ]);

  useEffect(() => {
    if (draftGenerationRef.current === props.draftGeneration) return;
    draftGenerationRef.current = props.draftGeneration;
    setBuilt(null);
    const active = requestRef.current;
    if (!active || active.draftGeneration === props.draftGeneration) return;

    if (active.kind === "create" || active.kind === "readback") {
      active.controller.abort();
      requestRef.current = null;
      createAttemptedRef.current = true;
      setBusy(false);
      setWriteActivity(true);
      setCreateState({ kind: "unknown", message: "草稿在创建请求期间发生变化，创建结果无法与当前表单关联。" });
      notifyExitState({
        kind: "unknown-write",
        message: "AIStarsLab 系统模型创建结果未知。",
      });
      setError("创建结果未知；服务端可能已创建共享模型。不会自动重试，请停止本地跟踪并手动查看模型列表。" );
      return;
    }

    active.controller.abort();
    requestRef.current = null;
    setBusy(false);
    setError("模型草稿已变化，已丢弃旧线路请求结果。请重新拉取并生成参数。");
    setMessage(null);
  }, [props.draftGeneration]);

  function capture(kind: RequestKind, draftGeneration = propsRef.current.draftGeneration): RequestOwner | null {
    const current = propsRef.current;
    if (!current.api || requestRef.current) return null;
    const owner: RequestOwner = {
      controller: new AbortController(),
      generation: generationRef.current,
      kind,
      api: current.api,
      adminApi: current.adminApi,
      actorId: current.actorId,
      sessionGeneration: current.sessionGeneration,
      mode: current.mode,
      modelId: current.modelId,
      category: current.category,
      provider: current.provider,
      draftGeneration,
      selectionGeneration: selectionGenerationRef.current,
    };
    requestRef.current = owner;
    setBusy(true);
    setError(null);
    return owner;
  }

  function isCurrent(owner: RequestOwner, requireDraft = true, requireSelection = false): boolean {
    const current = propsRef.current;
    return requestRef.current === owner
      && !owner.controller.signal.aborted
      && generationRef.current === owner.generation
      && current.api === owner.api
      && current.adminApi === owner.adminApi
      && current.actorId === owner.actorId
      && current.sessionGeneration === owner.sessionGeneration
      && current.mode === owner.mode
      && current.modelId === owner.modelId
      && current.category === owner.category
      && current.provider === owner.provider
      && (!requireDraft || current.draftGeneration === owner.draftGeneration)
      && (!requireSelection || selectionGenerationRef.current === owner.selectionGeneration);
  }

  function finishRequest(owner: RequestOwner) {
    if (requestRef.current !== owner) return;
    requestRef.current = null;
    setBusy(false);
  }

  function handleUnauthorizedIfCurrent(owner: RequestOwner, cause: unknown): boolean {
    const status = cause instanceof AistarslabApiError
      ? cause.status
      : typeof cause === "object" && cause !== null && "status" in cause
        ? (cause as { status?: unknown }).status
        : null;
    if (status === 401 && isCurrent(owner, false)) {
      propsRef.current.onUnauthorized();
      return true;
    }
    return false;
  }

  async function loadConfig() {
    if (!propsRef.current.api || requestRef.current || createAttemptedRef.current) return;
    const owner = capture("config");
    if (!owner) return;
    setMessage("正在读取 AIStarsLab 线路与模型…");
    setLines(null);
    setSelectedChannel("");
    setSelectedModel("");
    setBuilt(null);
    try {
      const config = await owner.api.getConfig(owner.controller.signal);
      if (!isCurrent(owner)) return;
      const nextLines = categoryLines(owner.category, config);
      setLines(nextLines);
      if (nextLines.length === 0) {
        setMessage(null);
        setError(`未读取到可用的${categoryName}线路。`);
      } else {
        setMessage(`已读取 ${nextLines.length} 条${categoryName}线路，请选择线路和模型。`);
      }
    } catch (cause) {
      if (!isCurrent(owner, false) || owner.controller.signal.aborted) return;
      if (handleUnauthorizedIfCurrent(owner, cause)) return;
      setMessage(null);
      setError(safeFailureMessage(cause, "读取线路"));
    } finally {
      finishRequest(owner);
    }
  }

  async function buildSelectedParams(channel: string, model: string) {
    const owner = capture("build");
    if (!owner) return;
    setMessage("正在生成所选模型的能力参数…");
    setBuilt(null);
    try {
      const result = await owner.api.buildParams(owner.category as AistarslabModelCategory, channel, model, owner.controller.signal);
      if (!isCurrent(owner, true, true)) return;
      setBuilt({
        value: result,
        generation: owner.generation,
        draftGeneration: owner.draftGeneration,
        selectionGeneration: owner.selectionGeneration,
        owner,
      });
      setMessage("参数已生成；确认后才会填入当前模型草稿。" );
    } catch (cause) {
      if (!isCurrent(owner, false, true) || owner.controller.signal.aborted) return;
      if (handleUnauthorizedIfCurrent(owner, cause)) return;
      setMessage(null);
      setError(safeFailureMessage(cause, "生成能力参数"));
    } finally {
      finishRequest(owner);
    }
  }

  function changeChannel(channel: string) {
    const active = requestRef.current;
    if (active?.kind === "build") {
      active.controller.abort();
      requestRef.current = null;
      setBusy(false);
    }
    selectionGenerationRef.current += 1;
    setSelectedChannel(channel);
    setSelectedModel("");
    setBuilt(null);
    setMessage(null);
    setError(null);
  }

  function changeModel(model: string) {
    const active = requestRef.current;
    if (active?.kind === "build") {
      active.controller.abort();
      requestRef.current = null;
      setBusy(false);
    } else if (active) {
      return;
    }
    selectionGenerationRef.current += 1;
    setSelectedModel(model);
    setBuilt(null);
    setMessage(null);
    setError(null);
    if (selectedChannel && model) void buildSelectedParams(selectedChannel, model);
  }

  function applyBuiltParams() {
    const current = propsRef.current;
    const candidate = built;
    if (!candidate || busy
      || candidate.generation !== generationRef.current
      || candidate.draftGeneration !== current.draftGeneration
      || candidate.selectionGeneration !== selectionGenerationRef.current
      || candidate.owner.api !== current.api
      || candidate.owner.adminApi !== current.adminApi
      || candidate.owner.actorId !== current.actorId
      || candidate.owner.sessionGeneration !== current.sessionGeneration
      || candidate.owner.mode !== current.mode
      || candidate.owner.modelId !== current.modelId
      || candidate.owner.category !== current.category
      || candidate.owner.provider !== current.provider
      || current.provider !== "aistarslab"
      || (current.category !== "image" && current.category !== "video")) return;
    setBuilt(null);
    current.onApply(candidate.value);
    setMessage(`已填入${modelLabel(current.category)}模型标识和参数；请检查后显式保存。`);
    setError(null);
  }

  function beginExitTracking(notice: AistarslabQuickConfigExitNotice) {
    setWriteActivity(true);
    notifyExitState(notice);
  }

  function clearExitTracking() {
    setWriteActivity(false);
    notifyExitState(null);
  }

  async function verifyCreatedModel(created: AistarslabCreatedSystemModel, owner: RequestOwner): Promise<boolean> {
    const readController = new AbortController();
    const abortFromOwner = () => readController.abort();
    if (owner.controller.signal.aborted) readController.abort();
    else owner.controller.signal.addEventListener("abort", abortFromOwner, { once: true });

    let timedOut = false;
    let timeout: ReturnType<typeof setTimeout> | undefined;
    const timeoutPromise = new Promise<never>((_resolve, reject) => {
      timeout = setTimeout(() => {
        timedOut = true;
        readController.abort();
        reject(new AistarslabApiError("timeout"));
      }, 20_000);
    });
    try {
      const model = await Promise.race([
        owner.adminApi.getModel(created.id, readController.signal),
        timeoutPromise,
      ]);
      return isCurrent(owner, true) && modelMatchesCreatedRecord(model, created);
    } finally {
      if (timeout !== undefined) clearTimeout(timeout);
      owner.controller.signal.removeEventListener("abort", abortFromOwner);
      if (timedOut) readController.abort();
    }
  }

  async function retryReadback() {
    const currentState = createState;
    if (!currentState || currentState.kind !== "readback-failed" || requestRef.current) return;
    const owner = capture("readback");
    if (!owner) return;
    const created = currentState.created;
    setCreateState({ kind: "readback", created });
    setMessage("正在按已返回的系统模型编号重新读取详情…");
    beginExitTracking({ kind: "created-needs-readback", message: "AIStarsLab 默认系统模型已创建，正在按原编号核对详情。" });
    try {
      const matched = await verifyCreatedModel(created, owner);
      if (!isCurrent(owner)) return;
      if (!matched) throw new Error("readback-mismatch");
      setCreateState(null);
      clearExitTracking();
      propsRef.current.onCreated();
    } catch (cause) {
      if (!isCurrent(owner, false) || owner.controller.signal.aborted) return;
      if (handleUnauthorizedIfCurrent(owner, cause)) return;
      const messageText = safeFailureMessage(cause, "读取已创建模型");
      setCreateState({ kind: "readback-failed", created, message: messageText });
      setMessage(null);
      setError(messageText);
      beginExitTracking({ kind: "created-needs-readback", message: "AIStarsLab 默认系统模型已创建，但详情尚未核对。" });
    } finally {
      finishRequest(owner);
    }
  }

  async function createDefault() {
    if (!propsRef.current.api || requestRef.current || createAttemptedRef.current) return;
    const current = propsRef.current;
    const confirmed = window.confirm(
      `这会立即创建一个启用且全站共享的 AIStarsLab 默认${modelLabel(current.category)}系统模型，并追加到系统模型目录。成功后会返回模型列表并丢弃当前未保存草稿；不会使用草稿里的自定义 Base URL。当前密钥输入${current.apiKey.trim() ? "会作为创建凭据" : "为空时服务端会尝试已配置的凭据"}。继续吗？`,
    );
    if (!confirmed) return;

    createAttemptedRef.current = true;
    const owner = capture("create");
    if (!owner) {
      createAttemptedRef.current = false;
      return;
    }
    setCreateState({ kind: "writing" });
    setMessage("正在创建默认系统模型…");
    beginExitTracking({ kind: "write-in-flight", message: "AIStarsLab 默认系统模型创建请求已发出。" });

    let createdRecord: AistarslabCreatedSystemModel | null = null;
    try {
      const created = await owner.api.addDefault(
        current.category as AistarslabModelCategory,
        current.apiKey.trim(),
        owner.controller.signal,
      );
      if (!isCurrent(owner)) return;
      createdRecord = created;
      setCreateState({ kind: "readback", created });
      setMessage("服务已返回新模型编号，正在按该编号读取详情并刷新系统模型目录…");
      const matches = await verifyCreatedModel(created, owner);
      if (!isCurrent(owner)) return;
      if (!matches) throw new Error("readback-mismatch");
      setCreateState(null);
      clearExitTracking();
      propsRef.current.onCreated();
    } catch (cause) {
      if (!isCurrent(owner, false) || owner.controller.signal.aborted) return;
      if (handleUnauthorizedIfCurrent(owner, cause)) return;
      if (createdRecord !== null) {
        const messageText = safeFailureMessage(cause, "读取已创建模型");
        setCreateState({ kind: "readback-failed", created: createdRecord, message: messageText });
        setMessage(null);
        setError(messageText);
        beginExitTracking({ kind: "created-needs-readback", message: "AIStarsLab 默认系统模型已创建，但详情尚未核对。" });
        return;
      }
      if (isUnknownCreateFailure(cause)) {
        const messageText = "创建结果未知；服务端可能已创建共享系统模型。不会自动重试，也不会按模型名称猜测创建结果。";
        setCreateState({ kind: "unknown", message: messageText });
        setMessage(null);
        setError(messageText);
        beginExitTracking({ kind: "unknown-write", message: "AIStarsLab 默认系统模型创建结果未知。" });
      } else {
        createAttemptedRef.current = false;
        setCreateState(null);
        setMessage(null);
        setError(safeFailureMessage(cause, "创建默认系统模型"));
        clearExitTracking();
      }
    } finally {
      finishRequest(owner);
    }
  }

  function stopTrackingAndRefresh() {
    const current = createState;
    if (!current || (current.kind !== "unknown" && current.kind !== "readback-failed")) return;
    const prompt = current.kind === "unknown"
      ? "创建请求结果未知，服务端可能已经创建了系统模型。停止本地跟踪不会撤销服务端操作；返回列表后请自行核对，不会自动重试或按名称认领。仍要停止跟踪并刷新目录吗？"
      : `系统模型“${current.created.name}”已创建，但详情尚未核对。停止核对并返回列表刷新目录吗？`;
    if (!window.confirm(prompt)) return;
    clearExitTracking();
    setCreateState(null);
    propsRef.current.onCreated();
  }

  const selectedLine = lines?.find((line) => line.channel === selectedChannel) ?? null;
  const canUseConfig = lines !== null && !busy && createState === null;
  const canCreate = Boolean(props.api) && !busy && createState === null && !createAttemptedRef.current;
  const categoryForPrompt = props.category === "image" || props.category === "video" ? props.category : null;
  if (!categoryForPrompt || props.provider !== "aistarslab") return null;

  return (
    <section className="admin-aistarslab-quick" aria-label="AIStarsLab 快速配置">
      <div className="admin-aistarslab-heading">
        <div>
          <h2>AIStarsLab 快速配置</h2>
          <p>线路读取与能力参数生成使用服务器已配置的 AIStarsLab 凭据，不会使用下方尚未保存的密钥或 Base URL。</p>
        </div>
        <span>{categoryName}模型</span>
      </div>

      <div className="admin-aistarslab-actions">
        <button className="admin-button admin-button-secondary" type="button" disabled={!props.api || busy || createState !== null} onClick={() => { void loadConfig(); }}>
          {busy && requestRef.current?.kind === "config" ? "读取中…" : "拉取线路/模型"}
        </button>
        <button className="admin-button admin-button-secondary" type="button" disabled={!canCreate} onClick={() => { void createDefault(); }}>
          从默认线路创建系统模型
        </button>
      </div>

      <p className="admin-field-help">创建会立即添加启用的全站共享系统模型。成功后返回模型列表并丢弃当前未保存草稿；当前密钥输入会用于此次创建，留空时由服务端尝试已配置凭据，不使用本表单的 Base URL。</p>

      {lines !== null ? (
        <div className="admin-aistarslab-selection">
          <label className="admin-field">
            <span>AIStarsLab 线路</span>
            <select aria-label="AIStarsLab 线路" value={selectedChannel} disabled={!canUseConfig} onChange={(event) => changeChannel(event.target.value)}>
              <option value="">— 选择线路 —</option>
              {lines.map((line) => (
                <option key={line.channel} value={line.channel}>
                  {(line.title || `线路 ${line.channel}`) + (line.isDefault ? "（默认）" : "")}
                </option>
              ))}
            </select>
          </label>
          <label className="admin-field">
            <span>AIStarsLab 模型</span>
            <select aria-label="AIStarsLab 模型" value={selectedModel} disabled={!canUseConfig || !selectedLine} onChange={(event) => changeModel(event.target.value)}>
              <option value="">— 选择模型 —</option>
              {selectedLine?.models.map((model) => <option key={model.model} value={model.model}>{model.label}</option>)}
            </select>
          </label>
        </div>
      ) : null}

      {built && built.generation === generationRef.current
        && built.draftGeneration === props.draftGeneration
        && built.selectionGeneration === selectionGenerationRef.current
        && built.owner.api === props.api
        && built.owner.adminApi === props.adminApi
        && built.owner.actorId === props.actorId
        && built.owner.sessionGeneration === props.sessionGeneration
        && built.owner.mode === props.mode
        && built.owner.modelId === props.modelId
        && built.owner.category === props.category
        && built.owner.provider === props.provider ? (
          <div className="admin-aistarslab-built">
            <span>{built.value.lineTitle || `线路 ${built.value.channel}`} / {built.value.label}</span>
            <button className="admin-button admin-button-primary" type="button" disabled={busy} onClick={applyBuiltParams}>
              填入当前草稿
            </button>
          </div>
        ) : null}

      {createState?.kind === "unknown" || createState?.kind === "readback-failed" ? (
        <div className="admin-aistarslab-recovery" role="alert">
          {createState.kind === "readback-failed" ? (
            <button className="admin-button admin-button-secondary" type="button" disabled={busy} onClick={() => { void retryReadback(); }}>
              按原模型编号重新核对
            </button>
          ) : null}
          <button className="admin-button admin-button-secondary" type="button" disabled={busy} onClick={stopTrackingAndRefresh}>
            停止本地跟踪并刷新模型目录
          </button>
        </div>
      ) : null}

      {message ? <p className="admin-aistarslab-status" role="status">{message}</p> : null}
      {error ? <p className="admin-form-error" role="alert">{error}</p> : null}
    </section>
  );
}
