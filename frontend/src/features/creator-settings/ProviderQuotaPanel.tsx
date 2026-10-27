import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { ApiError } from "../../shared/api/errors";
import type {
  ProviderQuotaAccount,
  ProviderQuotaAccountDirectory,
  ProviderQuotaAccountEditor as ProviderQuotaEditableAccount,
  ProviderQuotaAccountInput,
  ProviderQuotaBalance,
  ProviderQuotaServicePort,
  ProviderQuotaType,
} from "../../shared/api/providerQuota";
import { PROVIDER_QUOTA_TYPES } from "../../shared/api/providerQuota";
import { ProviderQuotaAccountEditor } from "./ProviderQuotaAccountEditor";
import "./providerQuota.css";

type CardState = "loading" | "stale" | "disabled" | "error" | "ready";

interface ProviderQuotaCard {
  account: ProviderQuotaAccount;
  state: CardState;
  balance?: ProviderQuotaBalance;
  message?: string;
}

interface ProviderQuotaPanelProps {
  actorId: string;
  isSuperuser: boolean;
  service?: ProviderQuotaServicePort | undefined;
  onUnauthorized(): void;
  onClose(): void;
  onCloseGuardChange(guard: () => boolean): void;
}

function requestMessage(error: unknown, fallback: string): string {
  if (!(error instanceof ApiError)) return fallback;
  if (error.status === 401) return "登录状态已失效，请重新登录。";
  if (error.status === 403) return "当前账号没有读取供应商额度的权限。";
  if (error.status === 404) return "监控账号已不存在，请刷新列表。";
  if (error.status === 503) return "额度监控服务暂不可用，请稍后重试。";
  if (error.kind === "invalid-response") return "额度服务返回的数据无法识别。";
  return fallback;
}

function formatAmount(value: number | null, unit: string, suffix: string, decimals: number): string {
  if (value === null) return "—";
  return `${unit}${value.toFixed(decimals)}${suffix === "" ? "" : ` ${suffix}`}`;
}

function updatedTime(value: string): string {
  const match = /(\d{1,2}:\d{2})/.exec(value);
  return match ? `${match[1]} 更新` : "";
}

function consoleHost(value: string): string {
  try {
    return new URL(value).host;
  } catch {
    return "供应商后台";
  }
}

function ambiguousWriteFailure(error: unknown): boolean {
  if (!(error instanceof ApiError)) return true;
  return error.kind === "network"
    || error.kind === "timeout"
    || error.kind === "invalid-response"
    || error.status === 408
    || (error.status !== null && error.status >= 500);
}

function accountActionError(error: unknown, action: string): string {
  if (!(error instanceof ApiError)) return `${action}未能确认；请刷新账号列表核实，不会自动重试。`;
  if (error.status === 401) return "登录状态已失效，请重新登录。";
  if (error.status === 403) return `当前账号没有${action}权限。`;
  if (error.status === 404) return "监控账号已不存在，请刷新列表。";
  if (error.status === 503) return "额度监控服务暂不可用。";
  if (error.kind === "http" && error.status !== null && error.status < 500 && error.status !== 408) {
    return `${action}请求未通过校验，请检查填写内容。`;
  }
  return `${action}状态未能确认；请刷新账号列表核实，不会自动重试。`;
}

function matchesAccount(
  account: ProviderQuotaEditableAccount,
  expected: Pick<ProviderQuotaEditableAccount, "id" | "name" | "providerType" | "baseUrl" | "isActive">,
): boolean {
  return account.id === expected.id
    && account.name === expected.name
    && account.providerType === expected.providerType
    && account.baseUrl === expected.baseUrl
    && account.isActive === expected.isActive;
}

function normalizeBaseUrlForReadback(value: string): string {
  let normalized = value.trim().replace(/\/+$/, "");
  if (normalized.endsWith("/v1")) normalized = normalized.slice(0, -3).replace(/\/+$/, "");
  return normalized;
}

function monitorAccount(account: ProviderQuotaEditableAccount): ProviderQuotaAccount {
  return {
    id: account.id,
    name: account.name,
    providerType: account.providerType,
    isActive: account.isActive,
    sortOrder: account.sortOrder,
    consoleUrl: account.consoleUrl,
  };
}

type UnknownMutation =
  | { kind: "create"; message: string; refreshed: boolean }
  | {
      kind: "update";
      id: string;
      expected: Pick<ProviderQuotaEditableAccount, "id" | "name" | "providerType" | "baseUrl" | "isActive">;
      credentialsChanged: boolean;
      message: string;
    }
  | { kind: "delete"; id: string; message: string };

export function ProviderQuotaPanel({ actorId, isSuperuser, service, onUnauthorized, onClose, onCloseGuardChange }: ProviderQuotaPanelProps) {
  const [cards, setCards] = useState<ProviderQuotaCard[]>([]);
  const [editableAccounts, setEditableAccounts] = useState<ProviderQuotaEditableAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [refreshVersion, setRefreshVersion] = useState(0);
  const [editorTarget, setEditorTarget] = useState<ProviderQuotaEditableAccount | null | undefined>(undefined);
  const [deleteTarget, setDeleteTarget] = useState<ProviderQuotaEditableAccount | null>(null);
  const [accountActionPending, setAccountActionPending] = useState(false);
  const [accountActionMessage, setAccountActionMessage] = useState<string | null>(null);
  const [unknownMutation, setUnknownMutation] = useState<UnknownMutation | null>(null);
  const generation = useRef(0);
  const readInProgress = useRef(true);
  const actionLock = useRef(false);
  const actionControllers = useRef(new Set<AbortController>());
  const editorCloseGuard = useRef<() => boolean>(() => true);
  const unknownMutationRef = useRef<UnknownMutation | null>(unknownMutation);
  const directoryRecheckRef = useRef(false);
  const directoryRecheckScopeRef = useRef<{ actorId: string; service: ProviderQuotaServicePort | undefined } | null>(null);
  const scopeRef = useRef({ actorId, isSuperuser, service, onUnauthorized });
  scopeRef.current = { actorId, isSuperuser, service, onUnauthorized };
  unknownMutationRef.current = unknownMutation;

  useLayoutEffect(() => {
    onCloseGuardChange(() => requestPanelClose());
    return () => onCloseGuardChange(() => true);
  }, [accountActionPending, onCloseGuardChange, unknownMutation]);

  useLayoutEffect(() => {
    readInProgress.current = true;
    directoryRecheckRef.current = false;
    directoryRecheckScopeRef.current = null;
    setLoading(true);
    setEditorTarget(undefined);
    setDeleteTarget(null);
    setUnknownMutation(null);
    setAccountActionMessage(null);
  }, [actorId, isSuperuser, service]);

  useEffect(() => {
    if (!isSuperuser || service === undefined) {
      readInProgress.current = false;
      directoryRecheckRef.current = false;
      directoryRecheckScopeRef.current = null;
      setCards([]);
      setEditableAccounts([]);
      setListError(null);
      setLoading(false);
      setEditorTarget(undefined);
      setDeleteTarget(null);
      setUnknownMutation(null);
      setAccountActionMessage(null);
      actionLock.current = false;
      setAccountActionPending(false);
      return;
    }

    const requestGeneration = ++generation.current;
    readInProgress.current = true;
    const preserveUnknownCreate = directoryRecheckRef.current
      && directoryRecheckScopeRef.current?.actorId === actorId
      && directoryRecheckScopeRef.current.service === service
      && unknownMutationRef.current?.kind === "create";
    const controller = new AbortController();
    let active = true;
    let authorizationHandled = false;
    const capturedActorId = actorId;
    const capturedService = service;
    const isCurrent = () => active
      && generation.current === requestGeneration
      && !controller.signal.aborted
      && scopeRef.current.actorId === capturedActorId
      && scopeRef.current.service === capturedService
      && scopeRef.current.isSuperuser;
    const handleAuthorizationFailure = (error: unknown) => {
      if (error instanceof ApiError && error.status === 401 && !authorizationHandled && isCurrent()) {
        authorizationHandled = true;
        scopeRef.current.onUnauthorized();
      }
    };

    setCards([]);
    setEditableAccounts([]);
    setListError(null);
    setLoading(true);
    setEditorTarget(undefined);
    setDeleteTarget(null);
    if (!preserveUnknownCreate) {
      setUnknownMutation(null);
      setAccountActionMessage(null);
    }
    actionLock.current = false;
    setAccountActionPending(false);

    async function load(): Promise<void> {
      try {
        const directory = await capturedService.listAccountDirectory(controller.signal);
        if (!isCurrent()) return;
        const accounts = directory.accounts;
        setEditableAccounts(directory.editableAccounts);
        const initialCards = accounts.map((account): ProviderQuotaCard => ({
          account,
          state: !account.isActive ? "disabled" : preserveUnknownCreate ? "stale" : "loading",
          ...(account.isActive && preserveUnknownCreate
            ? { message: "目录已刷新；额度尚未查询，请手动刷新。" }
            : {}),
        }));
        setCards(initialCards);
        if (preserveUnknownCreate && unknownMutationRef.current?.kind === "create") {
          setUnknownMutation({
            kind: "create",
            message: "目录已刷新；创建结果仍无法按名称或参数关联，请人工核对账号记录。不会自动重试。",
            refreshed: true,
          });
        }
        directoryRecheckRef.current = false;
        directoryRecheckScopeRef.current = null;

        if (preserveUnknownCreate) return;

        await Promise.all(accounts.filter((account) => account.isActive).map(async (account) => {
          try {
            const balance = await capturedService.getBalance(account.id, controller.signal);
            if (!isCurrent()) return;
            const nextCard: ProviderQuotaCard = balance.isActive === false || balance.disabled === true
              ? { account, state: "disabled", balance }
              : balance.configured === false
                ? { account, state: "error", balance, message: "该监控账号未配置可用凭据。" }
                : balance.hasErrors
                  ? { account, state: "error", balance, message: "额度查询异常，请检查该账号配置或稍后重试。" }
                  : balance.balance === null
                    ? { account, state: "error", balance, message: "供应商未返回余额数据。" }
                    : { account, state: "ready", balance };
            setCards((current) => current.map((card) => card.account.id === account.id ? nextCard : card));
          } catch (error) {
            if (!isCurrent()) return;
            handleAuthorizationFailure(error);
            const message = requestMessage(error, "额度查询失败，请稍后重试。");
            setCards((current) => current.map((card) => card.account.id === account.id
              ? { account, state: "error", message }
              : card));
          }
        }));
      } catch (error) {
        if (!isCurrent()) return;
        directoryRecheckRef.current = false;
        directoryRecheckScopeRef.current = null;
        handleAuthorizationFailure(error);
        setCards([]);
        setListError(requestMessage(error, "暂时无法读取供应商监控账号，请稍后重试。"));
        if (preserveUnknownCreate) {
          setUnknownMutation({
            kind: "create",
            message: "目录刷新失败；创建结果仍未知，请稍后重新只读核对。不会自动重试。",
            refreshed: false,
          });
        }
      } finally {
        if (isCurrent()) {
          readInProgress.current = false;
          setLoading(false);
        }
      }
    }

    void load();
    return () => {
      active = false;
      if (generation.current === requestGeneration) generation.current += 1;
      controller.abort();
      for (const actionController of actionControllers.current) actionController.abort();
      actionControllers.current.clear();
      actionLock.current = false;
    };
  }, [actorId, isSuperuser, refreshVersion, service]);

  function applyDirectorySnapshot(directory: ProviderQuotaAccountDirectory): void {
    const previousEditable = editableAccounts;
    setEditableAccounts(directory.editableAccounts);
    setCards((current) => directory.accounts.map((account) => {
      const previousCard = current.find((card) => card.account.id === account.id);
      const previousEditor = previousEditable.find((item) => item.id === account.id);
      const nextEditor = directory.editableAccounts.find((item) => item.id === account.id);
      const unchanged = previousCard !== undefined && previousEditor !== undefined && nextEditor !== undefined
        && previousEditor.name === nextEditor.name
        && previousEditor.providerType === nextEditor.providerType
        && previousEditor.baseUrl === nextEditor.baseUrl
        && previousEditor.isActive === nextEditor.isActive
        && previousEditor.consoleUrl === nextEditor.consoleUrl;
      if (unchanged) return { ...previousCard, account };
      return {
        account,
        state: account.isActive ? "stale" : "disabled",
        ...(account.isActive ? { message: "账号信息已刷新；点击“刷新账号和额度”重新查询余额。" } : {}),
      };
    }));
  }

  function upsertAccount(account: ProviderQuotaEditableAccount): void {
    const summary = monitorAccount(account);
    setEditableAccounts((current) => [...current.filter((item) => item.id !== account.id), account]
      .sort((left, right) => left.sortOrder - right.sortOrder));
    setCards((current) => {
      const next: ProviderQuotaCard = {
        account: summary,
        state: account.isActive ? "stale" : "disabled",
        ...(account.isActive ? { message: "账号信息已保存；点击“刷新账号和额度”重新查询余额。" } : {}),
      };
      const index = current.findIndex((card) => card.account.id === account.id);
      if (index < 0) return [...current, next].sort((left, right) => left.account.sortOrder - right.account.sortOrder);
      return current.map((card) => card.account.id === account.id ? next : card);
    });
  }

  function removeAccount(accountId: string): void {
    setEditableAccounts((current) => current.filter((account) => account.id !== accountId));
    setCards((current) => current.filter((card) => card.account.id !== accountId));
  }

  function beginAction(allowUnknown = false): {
    controller: AbortController;
    capturedService: ProviderQuotaServicePort;
    isCurrent(): boolean;
    finish(): void;
  } | null {
    if (actionLock.current || loading || readInProgress.current || service === undefined || !isSuperuser || (!allowUnknown && unknownMutation !== null)) return null;
    actionLock.current = true;
    setAccountActionPending(true);
    const controller = new AbortController();
    actionControllers.current.add(controller);
    const capturedActorId = actorId;
    const capturedService = service;
    const capturedGeneration = generation.current;
    const isCurrent = () => !controller.signal.aborted
      && generation.current === capturedGeneration
      && scopeRef.current.actorId === capturedActorId
      && scopeRef.current.service === capturedService
      && scopeRef.current.isSuperuser;
    return {
      controller,
      capturedService,
      isCurrent,
      finish() {
        actionControllers.current.delete(controller);
        if (isCurrent()) {
          actionLock.current = false;
          setAccountActionPending(false);
        }
      },
    };
  }

  function handleUnauthorized(error: unknown, isCurrent: () => boolean): void {
    if (error instanceof ApiError && error.status === 401 && isCurrent()) scopeRef.current.onUnauthorized();
  }

  function expectedAccount(accountId: string, input: ProviderQuotaAccountInput) {
    return {
      id: accountId,
      name: input.name,
      providerType: input.providerType,
      baseUrl: normalizeBaseUrlForReadback(input.baseUrl),
      isActive: input.isActive,
    };
  }

  async function saveAccount(input: ProviderQuotaAccountInput): Promise<void> {
    const action = beginAction();
    if (action === null || editorTarget === undefined) return;
    setAccountActionMessage(null);
    const target = editorTarget;
    try {
      if (target === null) {
        try {
          const created = await action.capturedService.createAccount(input, action.controller.signal);
          if (!action.isCurrent()) return;
          if (!matchesAccount(created, expectedAccount(created.id, input))) {
            throw new ApiError("invalid-response", "账号创建回执无法确认。");
          }
          upsertAccount(created);
          setEditorTarget(undefined);
          setAccountActionMessage("账号已创建；余额尚未查询，手动刷新后读取。");
          return;
        } catch (error) {
          if (!action.isCurrent()) return;
          handleUnauthorized(error, action.isCurrent);
          if (!ambiguousWriteFailure(error)) {
            setAccountActionMessage(accountActionError(error, "创建账号"));
            return;
          }
          setEditorTarget(undefined);
          setUnknownMutation({
            kind: "create",
            message: "创建结果未能确认；请先只读刷新账号列表并人工核对。不会按名称猜测或自动重试。",
            refreshed: false,
          });
          return;
        }
      }

      const expected = expectedAccount(target.id, input);
      const credentialsChanged = input.username !== "" || input.password !== "";
      try {
        const updated = await action.capturedService.updateAccount(target.id, input, action.controller.signal);
        if (!action.isCurrent()) return;
        if (!matchesAccount(updated, expected)) {
          throw new ApiError("invalid-response", "账号更新回执与目标不匹配。");
        }
        upsertAccount(updated);
        setEditorTarget(undefined);
        setAccountActionMessage(credentialsChanged
          ? "账号信息已保存；凭据更新请求已返回成功，凭据不会回读或显示。余额尚未刷新。"
          : "账号信息已保存；余额尚未刷新，手动刷新后读取。");
      } catch (error) {
        if (!action.isCurrent()) return;
        handleUnauthorized(error, action.isCurrent);
        if (!ambiguousWriteFailure(error)) {
          setAccountActionMessage(accountActionError(error, "保存账号"));
          return;
        }

        const uncertainMessage = "更新结果未能确认；不会自动重试，正在按原账号 ID 只读核对。";
        setUnknownMutation({ kind: "update", id: target.id, expected, credentialsChanged, message: uncertainMessage });
        try {
          const directory = await action.capturedService.listAccountDirectory(action.controller.signal);
          if (!action.isCurrent()) return;
          applyDirectorySnapshot(directory);
          const readback = directory.editableAccounts.find((account) => account.id === target.id);
          if (readback !== undefined && matchesAccount(readback, expected) && !credentialsChanged) {
            setUnknownMutation(null);
            setEditorTarget(undefined);
            setAccountActionMessage("账号信息已按原 ID 只读回读确认；未自动重试。余额可手动刷新。");
          } else {
            setUnknownMutation({
              kind: "update",
              id: target.id,
              expected,
              credentialsChanged,
              message: readback !== undefined && matchesAccount(readback, expected)
                ? "账号基础字段已回读；凭据不会回读，凭据变更仍未确认。停止核对后可再决定是否操作。"
                : "原账号 ID 的回读尚未确认更新结果。停止核对前不会再次写入。",
            });
          }
        } catch (readError) {
          if (!action.isCurrent()) return;
          handleUnauthorized(readError, action.isCurrent);
          setUnknownMutation({
            kind: "update",
            id: target.id,
            expected,
            credentialsChanged,
            message: "更新状态未知，目录回读也未完成。停止核对前不会再次写入。",
          });
        }
      }
    } finally {
      action.finish();
    }
  }

  async function toggleAccount(account: ProviderQuotaEditableAccount): Promise<void> {
    const action = beginAction();
    if (action === null) return;
    setAccountActionMessage(null);
    const input: ProviderQuotaAccountInput = {
      name: account.name,
      providerType: account.providerType as ProviderQuotaAccountInput["providerType"],
      baseUrl: account.baseUrl,
      username: "",
      password: "",
      isActive: !account.isActive,
    };
    const expected = expectedAccount(account.id, input);
    try {
      try {
        const updated = await action.capturedService.updateAccount(account.id, input, action.controller.signal);
        if (!action.isCurrent()) return;
        if (!matchesAccount(updated, expected)) throw new ApiError("invalid-response", "账号启停回执无法确认。");
        upsertAccount(updated);
        setAccountActionMessage(`已${input.isActive ? "启用" : "停用"}账号；余额尚未刷新。`);
      } catch (error) {
        if (!action.isCurrent()) return;
        handleUnauthorized(error, action.isCurrent);
        if (!ambiguousWriteFailure(error)) {
          setAccountActionMessage(accountActionError(error, "更新账号状态"));
          return;
        }
        setUnknownMutation({
          kind: "update",
          id: account.id,
          expected,
          credentialsChanged: false,
          message: "账号状态更新结果未知；正在按原 ID 只读核对，不会自动重试。",
        });
        try {
          const directory = await action.capturedService.listAccountDirectory(action.controller.signal);
          if (!action.isCurrent()) return;
          applyDirectorySnapshot(directory);
          const readback = directory.editableAccounts.find((item) => item.id === account.id);
          if (readback !== undefined && matchesAccount(readback, expected)) {
            setUnknownMutation(null);
            setAccountActionMessage(`账号已${input.isActive ? "启用" : "停用"}并按原 ID 回读确认。`);
          } else {
            setUnknownMutation({
              kind: "update",
              id: account.id,
              expected,
              credentialsChanged: false,
              message: "账号状态仍未核实。停止核对前不会再次写入。",
            });
          }
        } catch (readError) {
          if (!action.isCurrent()) return;
          handleUnauthorized(readError, action.isCurrent);
          setUnknownMutation({
            kind: "update",
            id: account.id,
            expected,
            credentialsChanged: false,
            message: "账号状态未知，目录回读也未完成。停止核对前不会再次写入。",
          });
        }
      }
    } finally {
      action.finish();
    }
  }

  async function deleteAccount(): Promise<void> {
    const target = deleteTarget;
    if (target === null) return;
    const action = beginAction();
    if (action === null) return;
    setAccountActionMessage(null);
    try {
      try {
        await action.capturedService.deleteAccount(target.id, action.controller.signal);
        if (!action.isCurrent()) return;
        removeAccount(target.id);
        setDeleteTarget(null);
        setAccountActionMessage("账号已删除；未发起余额查询。");
      } catch (error) {
        if (!action.isCurrent()) return;
        handleUnauthorized(error, action.isCurrent);
        if (!ambiguousWriteFailure(error)) {
          setAccountActionMessage(accountActionError(error, "删除账号"));
          return;
        }
        setUnknownMutation({ kind: "delete", id: target.id, message: "删除结果未知；正在按原 ID 只读核对，不会自动重试。" });
        try {
          const directory = await action.capturedService.listAccountDirectory(action.controller.signal);
          if (!action.isCurrent()) return;
          applyDirectorySnapshot(directory);
          const stillExists = directory.editableAccounts.some((account) => account.id === target.id);
          if (!stillExists) {
            setUnknownMutation(null);
            setDeleteTarget(null);
            setAccountActionMessage("账号已从当前目录移除，按原 ID 回读确认删除。未发起余额查询。");
          } else {
            setUnknownMutation({ kind: "delete", id: target.id, message: "账号仍在目录中，删除结果尚未确认。停止核对前不会再次删除。" });
          }
        } catch (readError) {
          if (!action.isCurrent()) return;
          handleUnauthorized(readError, action.isCurrent);
          setUnknownMutation({ kind: "delete", id: target.id, message: "删除状态未知，目录回读也未完成。停止核对前不会再次删除。" });
        }
      }
    } finally {
      action.finish();
    }
  }

  async function refreshUnknownMutation(): Promise<void> {
    const action = beginAction(true);
    if (action === null) return;
    const unknown = unknownMutationRef.current;
    try {
      if (unknown?.kind === "create") {
        directoryRecheckRef.current = true;
        directoryRecheckScopeRef.current = { actorId, service };
        setUnknownMutation({ ...unknown, message: "正在只读刷新账号目录；不会按名称猜测创建结果。", refreshed: false });
        setRefreshVersion((version) => version + 1);
        return;
      }
      if (unknown === null) return;
      const directory = await action.capturedService.listAccountDirectory(action.controller.signal);
      if (!action.isCurrent()) return;
      applyDirectorySnapshot(directory);
      if (unknown.kind === "update") {
        const found = directory.editableAccounts.find((item) => item.id === unknown.id);
        if (found !== undefined && matchesAccount(found, unknown.expected) && !unknown.credentialsChanged) {
          setUnknownMutation(null);
          setEditorTarget(undefined);
          setAccountActionMessage("账号信息已按原 ID 只读回读确认；未自动重试。余额可手动刷新。");
        } else {
          setUnknownMutation({
            ...unknown,
            message: found !== undefined && matchesAccount(found, unknown.expected)
              ? "账号基础字段已回读；凭据不会回读，凭据变更仍未确认。停止核对后可再决定是否操作。"
              : "原账号 ID 的目录回读仍未确认更新结果。停止核对前不会再次写入。",
          });
        }
      } else if (unknown.kind === "delete") {
        const exists = directory.editableAccounts.some((item) => item.id === unknown.id);
        if (!exists) {
          setUnknownMutation(null);
          setDeleteTarget(null);
          setAccountActionMessage("账号已从当前目录移除，按原 ID 回读确认删除。未发起余额查询。");
        } else {
          setUnknownMutation({ ...unknown, message: "账号仍在目录中，删除结果尚未确认。停止核对前不会再次删除。" });
        }
      }
    } catch (error) {
      if (!action.isCurrent()) return;
      handleUnauthorized(error, action.isCurrent);
      setUnknownMutation((current) => current === null ? null : {
        ...current,
        message: "只读目录核对未完成；不会自动重试写入。可稍后重新核对或停止本地跟踪。",
      });
    } finally {
      action.finish();
    }
  }

  function stopTrackingUnknownMutation(): void {
    const current = unknownMutationRef.current;
    if (current === null || (current.kind === "create" && !current.refreshed)) return;
    setUnknownMutation(null);
    setAccountActionMessage(current.kind === "create"
      ? "已停止本地跟踪。请人工核实目录；本次创建可能已生效，后续操作可能产生重复账号。"
      : "已停止本地跟踪。服务器操作可能已生效；请先刷新目录核实后再决定是否操作。"
    );
  }

  function requestPanelClose(): boolean {
    if (actionLock.current || !editorCloseGuard.current()) return false;
    if (unknownMutation !== null) {
      return window.confirm("当前有账号操作结果尚未确认。关闭只会清除本地跟踪，服务器操作可能已经生效。仍要关闭吗？");
    }
    return true;
  }

  function refreshAccountsAndBalances(): void {
    if (actionLock.current || loading || readInProgress.current || unknownMutation !== null || editorTarget !== undefined || deleteTarget !== null) return;
    readInProgress.current = true;
    setLoading(true);
    setRefreshVersion((version) => version + 1);
  }

  const warningCount = cards.filter((card) => card.state === "ready"
    && card.balance !== undefined
    && card.balance.balance !== null
    && card.balance.balance < card.balance.warnBelow).length;
  const errorCount = cards.filter((card) => card.state === "error").length;

  if (!isSuperuser) return null;

  return (
    <section id="creator-provider-quota-panel" className="provider-quota-panel" aria-label="供应商额度监控">
      <header className="provider-quota-panel-header">
        <div>
          <h2>供应商额度监控</h2>
          <p>额度按供应商分别展示，不合并不同计价单位；仅在打开或手动刷新时查询。</p>
        </div>
        <div className="provider-quota-panel-actions">
          <button disabled={loading || service === undefined || accountActionPending || unknownMutation !== null || editorTarget !== undefined || deleteTarget !== null} onClick={refreshAccountsAndBalances} type="button">
            {loading ? "正在查询…" : "刷新账号和额度"}
          </button>
          <button disabled={accountActionPending} onClick={() => { if (requestPanelClose()) onClose(); }} type="button">关闭</button>
        </div>
      </header>

      {service === undefined && <p className="provider-quota-panel-message" role="status">当前运行配置尚未接入额度监控服务。</p>}
      {listError !== null && <p className="provider-quota-panel-error" role="alert">{listError}</p>}
      {accountActionMessage !== null && <p className="provider-quota-panel-message" role="status">{accountActionMessage}</p>}

      {unknownMutation !== null && (
        <div className="provider-quota-panel-unknown" role="alert">
          <p>{unknownMutation.message}</p>
          <div>
            <button disabled={accountActionPending || service === undefined} onClick={() => void refreshUnknownMutation()} type="button">
              重新只读核对
            </button>
            <button
              disabled={accountActionPending || (unknownMutation.kind === "create" && !unknownMutation.refreshed)}
              onClick={stopTrackingUnknownMutation}
              type="button"
            >停止本地跟踪</button>
          </div>
        </div>
      )}

      {service !== undefined && (
        <div className="provider-quota-panel-account-actions">
          <button
            disabled={loading || accountActionPending || unknownMutation !== null || editorTarget !== undefined || deleteTarget !== null}
            onClick={() => { setAccountActionMessage(null); setEditorTarget(null); }}
            type="button"
          >添加账号</button>
          {accountActionPending && <span role="status">正在处理账号操作…</span>}
        </div>
      )}

      {editorTarget !== undefined && service !== undefined && (
        <ProviderQuotaAccountEditor
          account={editorTarget}
          actorId={actorId}
          notice={accountActionMessage}
          onClose={() => setEditorTarget(undefined)}
          onCloseGuardChange={(guard) => { editorCloseGuard.current = guard; }}
          onProbeUnauthorized={onUnauthorized}
          onSave={(input) => void saveAccount(input)}
          pending={accountActionPending}
          service={service}
          writeBlocked={unknownMutation !== null}
        />
      )}

      {deleteTarget !== null && (
        <div className="provider-quota-panel-delete-confirm" role="alertdialog" aria-label="确认删除监控账号">
          <p>确定删除“{deleteTarget.name || "未命名账号"}”吗？此操作会移除该监控账号。</p>
          <button disabled={loading || accountActionPending || unknownMutation !== null} onClick={() => void deleteAccount()} type="button">确认删除</button>
          <button disabled={accountActionPending} onClick={() => setDeleteTarget(null)} type="button">取消</button>
        </div>
      )}

      {service !== undefined && listError === null && (
        <>
          <div className="provider-quota-panel-summary" aria-label="额度状态概览">
            <span>监控账号 {cards.length} 家</span>
            <span className={warningCount > 0 ? "is-warn" : ""}>告警 {loading ? "—" : warningCount}</span>
            <span className={errorCount > 0 ? "is-error" : ""}>异常 {loading ? "—" : errorCount}</span>
          </div>

          {loading && cards.length === 0 && <p className="provider-quota-panel-message" role="status">正在读取监控账号…</p>}
          {!loading && cards.length === 0 && <p className="provider-quota-panel-message">还没有配置供应商监控账号。</p>}

          <div className="provider-quota-panel-grid">
            {cards.map((card) => {
              const balance = card.balance;
              const consoleUrl = balance?.consoleUrl ?? card.account.consoleUrl;
              const isWarning = card.state === "ready" && balance !== undefined
                && balance.balance !== null && balance.balance < balance.warnBelow;
              const stateLabel = card.state === "loading"
                ? "查询中"
                : card.state === "stale"
                  ? "待刷新"
                : card.state === "disabled"
                  ? "已停用"
                  : card.state === "error"
                    ? "异常"
                    : isWarning ? "告警" : "正常";
              const visibleName = balance?.name.trim() || card.account.name.trim() || "未命名供应商";

              return (
                <article className={`provider-quota-card is-${card.state}${isWarning ? " is-warn" : ""}`} key={card.account.id}>
                  <header className="provider-quota-card-header">
                    <div>
                      <strong>{visibleName}</strong>
                      <small>{card.account.providerType}</small>
                    </div>
                    {consoleUrl !== null && (
                      <a href={consoleUrl} rel="noopener noreferrer" target="_blank" title={`打开 ${consoleHost(consoleUrl)}（新窗口）`}>
                        打开后台
                      </a>
                    )}
                  </header>
                  <div className="provider-quota-card-status" role="status">{stateLabel}</div>
                  {card.state === "ready" && balance !== undefined ? (
                    <>
                      <div className="provider-quota-card-main">
                        <strong>{formatAmount(balance.balance, balance.unit, balance.unitSuffix, balance.decimals)}</strong>
                        <span>{balance.balanceLabel}</span>
                      </div>
                      <div className="provider-quota-card-details">
                        {balance.available === false && <span>余额不足，可能无法调用</span>}
                        {balance.used !== null && <span>已用 {formatAmount(balance.used, balance.unit, balance.unitSuffix, balance.decimals)}</span>}
                        {balance.total !== null && <span>总额 {formatAmount(balance.total, balance.unit, balance.unitSuffix, balance.decimals)}</span>}
                        {balance.toppedUp !== null && <span>充值 {formatAmount(balance.toppedUp, balance.unit, balance.unitSuffix, balance.decimals)}</span>}
                        {balance.granted !== null && <span>赠金 {formatAmount(balance.granted, balance.unit, balance.unitSuffix, balance.decimals)}</span>}
                        {balance.credits !== null && <span>{balance.credits.toFixed(0)} 积分</span>}
                        {balance.unit !== "¥" && balance.balanceCny !== null && <span>≈¥{balance.balanceCny.toFixed(2)}</span>}
                        {balance.extraMeta.map((item, index) => (
                          <span key={`${item.label}-${index}`}>{item.label} {item.unit}{item.amount.toFixed(2)}</span>
                        ))}
                      </div>
                    </>
                  ) : card.state === "loading" ? (
                    <div className="provider-quota-card-main"><strong>—</strong><span>正在查询额度…</span></div>
                  ) : card.state === "stale" ? (
                    <div className="provider-quota-card-main"><strong>—</strong><span>{card.message ?? "额度尚未刷新，请手动刷新。"}</span></div>
                  ) : (
                    <div className="provider-quota-card-main">
                      <strong>—</strong>
                      <span>{card.message ?? (card.state === "disabled" ? "该监控账号已停用。" : "余额暂不可用。")}</span>
                    </div>
                  )}
                  {balance !== undefined && updatedTime(balance.updatedAt) !== "" && (
                    <footer>{updatedTime(balance.updatedAt)}</footer>
                  )}
                  {editableAccounts.some((account) => account.id === card.account.id) && (
                    <div className="provider-quota-card-actions">
                      <button
                        disabled={loading || accountActionPending || unknownMutation !== null || editorTarget !== undefined || deleteTarget !== null}
                        onClick={() => {
                          const editable = editableAccounts.find((account) => account.id === card.account.id);
                          if (editable !== undefined) { setAccountActionMessage(null); setEditorTarget(editable); }
                        }}
                        type="button"
                      >编辑</button>
                      <button
                        disabled={loading || accountActionPending || unknownMutation !== null || editorTarget !== undefined || deleteTarget !== null || !PROVIDER_QUOTA_TYPES.includes(card.account.providerType as ProviderQuotaType)}
                        onClick={() => {
                          const editable = editableAccounts.find((account) => account.id === card.account.id);
                          if (editable !== undefined) void toggleAccount(editable);
                        }}
                        type="button"
                      >{card.account.isActive ? "停用" : "启用"}</button>
                      <button
                        disabled={loading || accountActionPending || unknownMutation !== null || editorTarget !== undefined || deleteTarget !== null}
                        onClick={() => setDeleteTarget(editableAccounts.find((account) => account.id === card.account.id) ?? null)}
                        type="button"
                      >删除</button>
                    </div>
                  )}
                </article>
              );
            })}
          </div>
        </>
      )}
    </section>
  );
}
