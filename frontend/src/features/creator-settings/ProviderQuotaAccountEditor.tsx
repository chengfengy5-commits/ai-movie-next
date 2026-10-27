import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { ApiError, isAbortError } from "../../shared/api/errors";
import type {
  ProviderQuotaAccountEditor as ProviderQuotaAccountRecord,
  ProviderQuotaAccountInput,
  ProviderQuotaProbeInput,
  ProviderQuotaServicePort,
  ProviderQuotaType,
} from "../../shared/api/providerQuota";
import { PROVIDER_QUOTA_TYPES } from "../../shared/api/providerQuota";

interface ProviderQuotaAccountEditorProps {
  account: ProviderQuotaAccountRecord | null;
  actorId: string;
  pending: boolean;
  service: ProviderQuotaServicePort;
  onClose(): void;
  onCloseGuardChange(guard: () => boolean): void;
  onProbeUnauthorized(): void;
  onSave(input: ProviderQuotaAccountInput): void;
  notice: string | null;
  writeBlocked: boolean;
}

interface AccountDraft {
  name: string;
  providerType: string;
  baseUrl: string;
  username: string;
  password: string;
  isActive: boolean;
}

const PROVIDER_OPTIONS: Record<ProviderQuotaType, {
  label: string;
  needsUsername: boolean;
  usernameLabel: string;
  secretLabel: string;
  defaultBaseUrl: string;
}> = {
  new_api: {
    label: "new-api 中转站",
    needsUsername: true,
    usernameLabel: "登录账号",
    secretLabel: "登录密码",
    defaultBaseUrl: "",
  },
  deepseek: {
    label: "DeepSeek 官方",
    needsUsername: false,
    usernameLabel: "",
    secretLabel: "API Key",
    defaultBaseUrl: "https://api.deepseek.com",
  },
  aistarslab: {
    label: "AIStarsLab",
    needsUsername: false,
    usernameLabel: "",
    secretLabel: "API Key",
    defaultBaseUrl: "https://api.video.aistarslab.com/openapi",
  },
  aliyun: {
    label: "阿里云（账户余额）",
    needsUsername: true,
    usernameLabel: "AccessKey ID",
    secretLabel: "AccessKey Secret",
    defaultBaseUrl: "https://business.aliyuncs.com",
  },
};

function isProviderType(value: string): value is ProviderQuotaType {
  return (PROVIDER_QUOTA_TYPES as readonly string[]).includes(value);
}

function initialDraft(account: ProviderQuotaAccountRecord | null): AccountDraft {
  if (account === null) {
    return {
      name: "",
      providerType: "new_api",
      baseUrl: "",
      username: "",
      password: "",
      isActive: true,
    };
  }
  return {
    name: account.name,
    providerType: account.providerType,
    baseUrl: account.baseUrl,
    username: "",
    password: "",
    isActive: account.isActive,
  };
}

function sameDraft(left: AccountDraft, right: AccountDraft): boolean {
  return left.name === right.name
    && left.providerType === right.providerType
    && left.baseUrl === right.baseUrl
    && left.username === right.username
    && left.password === right.password
    && left.isActive === right.isActive;
}

function safeProbeFailure(error: unknown): string {
  if (!(error instanceof ApiError)) return "连接测试未能确认；不会自动重试。";
  if (error.status === 401) return "登录状态已失效，请重新登录。";
  if (error.status === 403) return "当前账号没有执行连接测试的权限。";
  if (error.status === 503) return "额度监控服务暂未接线。";
  if (error.kind === "http" && error.status !== null && error.status < 500 && error.status !== 408) {
    return "连接测试请求未通过校验，请检查填写内容。";
  }
  return "连接测试未能确认；不会自动重试。";
}

export function ProviderQuotaAccountEditor({
  account,
  actorId,
  pending,
  service,
  onClose,
  onCloseGuardChange,
  onProbeUnauthorized,
  onSave,
  notice,
  writeBlocked,
}: ProviderQuotaAccountEditorProps) {
  const [draft, setDraft] = useState(() => initialDraft(account));
  const [formError, setFormError] = useState<string | null>(null);
  const [probeMessage, setProbeMessage] = useState<string | null>(null);
  const [probePending, setProbePending] = useState(false);
  const probeGeneration = useRef(0);
  const probeController = useRef<AbortController | null>(null);
  const currentScope = useRef({ actorId, service, onProbeUnauthorized });
  currentScope.current = { actorId, service, onProbeUnauthorized };

  const baseline = initialDraft(account);
  const dirty = !sameDraft(draft, baseline);

  useEffect(() => {
    probeGeneration.current += 1;
    probeController.current?.abort();
    probeController.current = null;
    setDraft(initialDraft(account));
    setFormError(null);
    setProbeMessage(null);
    setProbePending(false);
    return () => {
      probeGeneration.current += 1;
      probeController.current?.abort();
      probeController.current = null;
    };
  }, [account?.id, actorId, service]);

  const requestClose = () => {
    if (pending) return false;
    if (probePending || dirty) {
      const message = probePending
        ? "关闭会停止本地等待，但服务端连接测试可能仍在运行；未保存的字段和凭据也会清除。继续吗？"
        : "当前有未保存的账号设置或凭据，关闭将清除这份草稿。继续吗？";
      if (!window.confirm(message)) return false;
    }
    return true;
  };

  useLayoutEffect(() => {
    onCloseGuardChange(requestClose);
    return () => onCloseGuardChange(() => true);
  }, [dirty, onCloseGuardChange, pending, probePending]);

  function updateDraft(update: Partial<AccountDraft>): void {
    probeGeneration.current += 1;
    setProbeMessage(null);
    setFormError(null);
    setDraft((current) => ({ ...current, ...update }));
  }

  function changeProvider(providerType: string): void {
    const option = isProviderType(providerType) ? PROVIDER_OPTIONS[providerType] : null;
    updateDraft({
      providerType,
      baseUrl: option?.defaultBaseUrl ?? "",
      username: "",
      password: "",
    });
  }

  function inputError(requireFreshCredentials: boolean): string | null {
    if (!isProviderType(draft.providerType)) return "请选择当前支持的供应商类型。";
    if (draft.baseUrl.trim() === "") return "请填写网关地址。";
    const provider = PROVIDER_OPTIONS[draft.providerType];
    const username = draft.username.trim();
    const password = draft.password.trim();
    if (requireFreshCredentials) {
      if (password === "") return `请填写${provider.secretLabel}。`;
      if (provider.needsUsername && username === "") return `请填写${provider.usernameLabel}。`;
    }
    return null;
  }

  function makeInput(): ProviderQuotaAccountInput | null {
    const error = inputError(account === null || account.providerType !== draft.providerType);
    if (error !== null || !isProviderType(draft.providerType)) {
      setFormError(error ?? "请选择当前支持的供应商类型。");
      return null;
    }
    return {
      name: draft.name.trim(),
      providerType: draft.providerType,
      baseUrl: draft.baseUrl.trim(),
      username: draft.username.trim(),
      password: draft.password.trim(),
      isActive: draft.isActive,
    };
  }

  function submit(): void {
    if (pending || probePending) return;
    const input = makeInput();
    if (input === null) return;
    onSave(input);
  }

  async function testConnection(): Promise<void> {
    if (pending || probePending || probeController.current !== null) return;
    const error = inputError(true);
    if (error !== null || !isProviderType(draft.providerType)) {
      setFormError(error ?? "请选择当前支持的供应商类型。");
      return;
    }
    const input: ProviderQuotaProbeInput = {
      providerType: draft.providerType,
      baseUrl: draft.baseUrl.trim(),
      username: draft.username.trim(),
      password: draft.password.trim(),
    };
    const capturedActorId = actorId;
    const capturedService = service;
    const currentGeneration = ++probeGeneration.current;
    const controller = new AbortController();
    probeController.current = controller;
    setFormError(null);
    setProbeMessage(null);
    setProbePending(true);
    try {
      const result = await capturedService.probeConnection(input, controller.signal);
      if (probeGeneration.current !== currentGeneration
        || controller.signal.aborted
        || currentScope.current.actorId !== capturedActorId
        || currentScope.current.service !== capturedService) return;
      setProbeMessage(result.ok && result.balance !== null
        ? `连接成功，可用余额 ${result.unit}${result.balance.toFixed(2)}。`
        : "连接失败，请核对填写的地址和凭据。原始服务错误不会显示。");
    } catch (error) {
      if (probeGeneration.current !== currentGeneration
        || currentScope.current.actorId !== capturedActorId
        || currentScope.current.service !== capturedService) return;
      if (isAbortError(error)) return;
      if (error instanceof ApiError && error.status === 401) currentScope.current.onProbeUnauthorized();
      setProbeMessage(safeProbeFailure(error));
    } finally {
      if (probeGeneration.current === currentGeneration) {
        probeController.current = null;
        setProbePending(false);
      }
    }
  }

  function stopProbe(): void {
    if (!probePending) return;
    probeGeneration.current += 1;
    probeController.current?.abort();
    probeController.current = null;
    setProbePending(false);
    setProbeMessage("已停止本地等待；服务端连接测试可能仍在运行，结果未确认。不会自动重试。");
  }

  const selectedProvider = isProviderType(draft.providerType) ? PROVIDER_OPTIONS[draft.providerType] : null;
  const editorTitle = account === null ? "新建监控账号" : `编辑监控账号：${account.name || "未命名"}`;

  return (
    <div className="provider-quota-editor" role="group" aria-label={editorTitle}>
      <div className="provider-quota-editor-heading">
        <div>
          <h3>{account === null ? "添加监控账号" : "编辑监控账号"}</h3>
          <p>凭据只用于当前操作，不会回显或保存到本地。</p>
        </div>
        <button disabled={pending} onClick={() => { if (requestClose()) onClose(); }} type="button">关闭编辑</button>
      </div>

      <div className="provider-quota-editor-fields">
        <label>
          <span>账号名称</span>
          <input autoComplete="off" disabled={pending || probePending} maxLength={100} onChange={(event) => updateDraft({ name: event.target.value })} value={draft.name} />
        </label>
        <label>
          <span>供应商类型</span>
          <select disabled={pending || probePending} onChange={(event) => changeProvider(event.target.value)} value={draft.providerType}>
            {!isProviderType(draft.providerType) && <option value={draft.providerType}>当前类型暂不支持</option>}
            {PROVIDER_QUOTA_TYPES.map((providerType) => (
              <option key={providerType} value={providerType}>{PROVIDER_OPTIONS[providerType].label}</option>
            ))}
          </select>
        </label>
        <label className="is-wide">
          <span>网关地址</span>
          <input autoComplete="url" disabled={pending || probePending} onChange={(event) => updateDraft({ baseUrl: event.target.value })} value={draft.baseUrl} />
        </label>
        {selectedProvider?.needsUsername && (
          <label>
            <span>{selectedProvider.usernameLabel}</span>
            <input autoComplete="off" disabled={pending || probePending} onChange={(event) => updateDraft({ username: event.target.value })} value={draft.username} />
          </label>
        )}
        <label>
          <span>{selectedProvider?.secretLabel ?? "新凭据"}</span>
          <input autoComplete="new-password" disabled={pending || probePending} onChange={(event) => updateDraft({ password: event.target.value })} type="password" value={draft.password} />
        </label>
        <label className="provider-quota-editor-active">
          <input checked={draft.isActive} disabled={pending || probePending} onChange={(event) => updateDraft({ isActive: event.target.checked })} type="checkbox" />
          <span>启用额度监控</span>
        </label>
      </div>

      {account !== null && (
        <p className="provider-quota-editor-hint">
          已保存凭据不会回显；编辑时只填写需要替换的凭据，留空字段会按服务器规则保留原值。若更换供应商，请重新填写新类型所需凭据。
          {account.hasUsername ? "当前账号含登录账号字段。" : "当前账号不含登录账号字段。"}
        </p>
      )}
      {account === null && <p className="provider-quota-editor-hint">创建账号需要当前供应商类型所需的完整凭据。</p>}
      {formError !== null && <p className="provider-quota-editor-error" role="alert">{formError}</p>}
      {notice !== null && <p className="provider-quota-editor-message" role="status">{notice}</p>}
      {probeMessage !== null && <p className="provider-quota-editor-message" role="status">{probeMessage}</p>}

      <div className="provider-quota-editor-actions">
        <button disabled={pending || probePending || writeBlocked} onClick={() => void testConnection()} type="button">
          {probePending ? "测试中…" : "测试连接"}
        </button>
        {probePending && <button onClick={stopProbe} type="button">停止等待</button>}
        <button className="is-primary" disabled={pending || probePending || writeBlocked} onClick={submit} type="button">
          {pending ? "处理中…" : account === null ? "创建账号" : "保存账号"}
        </button>
        <button disabled={pending} onClick={() => { if (requestClose()) onClose(); }} type="button">取消</button>
      </div>
    </div>
  );
}
