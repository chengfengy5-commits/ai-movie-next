import { useEffect, useRef, useState } from "react";
import { isSafeCoverUrl } from "../../../shared/api/config";
import type { AssetLibraryType, Character, Prop, Scene } from "../../../shared/api/contracts";
import { ApiError } from "../../../shared/api/errors";
import { normalizeApiError, type WorkspaceServices } from "../../../shared/api/services";
import {
  toAssetMaintenanceType,
  type AssetDuplicateGroup,
  type AssetDuplicateItem,
  type AssetDuplicateList,
  type AssetIgnoredDuplicateList,
  type AssetMaintenanceServicePort,
  type AssetMaintenanceType,
  type AssetOrphanCandidate,
  type MergedAssetReceipt,
  type SetAssetDuplicateIgnoredReceipt,
} from "../../../shared/api/assetMaintenance";
import { useSeriesDialogFocus } from "../../series/useSeriesDialogFocus";
import type { AssetDirectory } from "../management/AssetManagementDialog";

import "./assetMaintenance.css";

type AssetRecord = Character | Scene | Prop;
type MaintenanceTab = "orphans" | "duplicates" | "ignored";

type ReadState<T> =
  | { status: "idle" | "loading" }
  | { status: "ready"; data: T }
  | { status: "error"; message: string };

interface DirectoryReadState<T> {
  directoryEpoch: number;
  state: ReadState<T>;
}

type WriteNotice =
  | { kind: "error"; message: string }
  | { kind: "unknown"; action: string; message: string }
  | { kind: "committed"; message: string; receipt: MergedAssetReceipt | SetAssetDuplicateIgnoredReceipt | null }
  | { kind: "committed-refresh-failed"; message: string; receipt: MergedAssetReceipt | SetAssetDuplicateIgnoredReceipt | null };

interface SelectedItems {
  groupKey: string;
  ids: string[];
  keepId: string | null;
}

type Confirmation =
  | { kind: "ignore"; items: AssetDuplicateItem[] }
  | { kind: "restore"; ids: [string, string]; names: [string, string] }
  | { kind: "merge"; items: AssetDuplicateItem[]; keepId: string };

interface OperationContext {
  ticket: number;
  controller: AbortController;
}

interface AssetMaintenanceDialogProps {
  userId: string;
  category: AssetLibraryType;
  directoryItems: AssetRecord[];
  directoryEpoch: number;
  apiBaseUrl: string | null;
  port: AssetMaintenanceServicePort;
  services: WorkspaceServices;
  seriesId: string;
  seriesName: string;
  isCurrent(): boolean;
  isDirectoryCurrent(items: AssetRecord[], directoryEpoch: number): boolean;
  onClose(): void;
  onUnauthorized(): void;
  onDirectoryUpdated(directory: AssetDirectory, refreshEpoch: number): void;
  onDirectoryNeedsRefresh(): number | null;
  onOrphanActionsLockedChange(locked: boolean): void;
  onLocateAsset(item: AssetRecord, directoryEpoch: number): void;
  onOpenDelete(item: AssetRecord, directoryEpoch: number): void;
}

function categoryLabel(category: AssetLibraryType): string {
  switch (category) {
    case "characters": return "角色";
    case "scenes": return "场景";
    case "props": return "道具";
  }
}

function assetName(category: AssetLibraryType, item: AssetRecord): string {
  const value = category === "scenes"
    ? ("title" in item && typeof item.title === "string" ? item.title : "")
    : ("name" in item && typeof item.name === "string" ? item.name : "");
  return value.trim() === "" ? `未命名${categoryLabel(category)}` : value;
}

function groupKey(group: AssetDuplicateGroup): string {
  return `${group.index}:${group.items.map((item) => item.id).sort().join("|")}`;
}

function isUncertainWrite(error: ApiError): boolean {
  return error.kind === "network"
    || error.kind === "timeout"
    || error.kind === "invalid-response"
    || (error.status !== null && error.status >= 500);
}

function errorText(error: ApiError): string {
  if (error.status === 403) return error.detail ?? "当前账号没有整理此剧集素材的权限。";
  if (error.status === 404) return error.detail ?? "剧集或素材已不存在，请重新读取目录。";
  if (error.status === 409) return error.detail ?? "素材状态已变化，请重新读取并核对后再操作。";
  if (error.status === 422) return error.detail ?? "素材维护请求未通过校验，请重新读取并核对。";
  if (error.kind === "timeout") return "请求超时，写入结果需要核对。";
  if (error.kind === "network") return "无法连接素材维护服务，请检查服务状态。";
  if (error.kind === "invalid-response") return "服务返回的数据无法识别，请重新读取并核对。";
  return error.detail ?? error.message ?? "素材维护请求未完成。";
}

function readDirectory(
  services: WorkspaceServices,
  category: AssetLibraryType,
  seriesId: string,
  signal: AbortSignal,
): Promise<AssetDirectory> {
  switch (category) {
    case "characters":
      return services.listCharacters(seriesId, signal).then((items) => ({ category, items }));
    case "scenes":
      return services.listScenes(seriesId, signal).then((items) => ({ category, items }));
    case "props":
      return services.listProps(seriesId, signal).then((items) => ({ category, items }));
  }
}

function SafeAssetImage({
  apiBaseUrl,
  name,
  url,
}: {
  apiBaseUrl: string | null;
  name: string;
  url: string | null;
}) {
  const [failed, setFailed] = useState(false);
  const safeUrl = apiBaseUrl === null ? null : isSafeCoverUrl(url, apiBaseUrl);
  if (safeUrl === null || failed) {
    return <span className="asset-maintenance-image-placeholder" aria-label={name + "没有可显示的本地图片"} role="img">暂无图片</span>;
  }
  return <img alt={name + "图片"} loading="lazy" onError={() => setFailed(true)} src={safeUrl} />;
}

export function AssetMaintenanceDialog({
  userId,
  category,
  directoryItems,
  directoryEpoch,
  apiBaseUrl,
  port,
  services,
  seriesId,
  seriesName,
  isCurrent,
  isDirectoryCurrent,
  onClose,
  onUnauthorized,
  onDirectoryUpdated,
  onDirectoryNeedsRefresh,
  onOrphanActionsLockedChange,
  onLocateAsset,
  onOpenDelete,
}: AssetMaintenanceDialogProps) {
  const [tab, setTab] = useState<MaintenanceTab>("duplicates");
  const [duplicateState, setDuplicateState] = useState<ReadState<AssetDuplicateList>>({ status: "idle" });
  const [ignoredState, setIgnoredState] = useState<ReadState<AssetIgnoredDuplicateList>>({ status: "idle" });
  const [orphanState, setOrphanState] = useState<DirectoryReadState<AssetRecord[]>>({
    directoryEpoch,
    state: { status: "idle" },
  });
  const [selected, setSelected] = useState<SelectedItems | null>(null);
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const [writeNotice, setWriteNotice] = useState<WriteNotice | null>(null);
  const [operationBusy, setOperationBusy] = useState(false);
  const writeNoticeRef = useRef<WriteNotice | null>(writeNotice);
  const operationBusyRef = useRef(false);
  const orphanActionsLockedRef = useRef(true);
  const operationSequenceRef = useRef(0);
  const operationControllerRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(true);
  const unauthorizedRef = useRef(onUnauthorized);
  unauthorizedRef.current = onUnauthorized;
  const submittingRef = useRef(false);
  const accessLostRef = useRef(false);
  const scopeRef = useRef({ userId, category, port, services, seriesId });
  const label = categoryLabel(category);
  const maintenanceType: AssetMaintenanceType = toAssetMaintenanceType(category);
  const writeFrozen = writeNotice?.kind === "unknown" || writeNotice?.kind === "committed-refresh-failed";
  const writePending = writeNotice?.kind === "committed" && operationBusy;
  const cannotDismiss = operationBusy || writeNotice?.kind === "unknown" || writeNotice?.kind === "committed-refresh-failed";
  const focus = useSeriesDialogFocus({ active: true, canDismiss: !cannotDismiss, onDismiss: onClose });
  const currentOrphanState: ReadState<AssetRecord[]> = orphanState.directoryEpoch === directoryEpoch
    ? orphanState.state
    : { status: "idle" };

  function noticeFreezesWrites(notice: WriteNotice | null): boolean {
    return notice?.kind === "unknown" || notice?.kind === "committed-refresh-failed";
  }

  function setOrphanActionsLocked(locked: boolean): void {
    if (orphanActionsLockedRef.current === locked) return;
    orphanActionsLockedRef.current = locked;
    onOrphanActionsLockedChange(locked);
  }

  function updateWriteNotice(
    update: WriteNotice | null | ((current: WriteNotice | null) => WriteNotice | null),
  ): void {
    const next = typeof update === "function" ? update(writeNoticeRef.current) : update;
    writeNoticeRef.current = next;
    setWriteNotice(next);
    setOrphanActionsLocked(operationBusyRef.current || noticeFreezesWrites(next));
  }

  function scopeIsCurrent(): boolean {
    const initial = scopeRef.current;
    return mountedRef.current
      && isCurrent()
      && userId === initial.userId
      && category === initial.category
      && port === initial.port
      && services === initial.services
      && seriesId === initial.seriesId
      && services.assetMaintenance === port;
  }

  function beginOperation(): OperationContext | null {
    if (operationBusyRef.current || !scopeIsCurrent()) return null;
    operationBusyRef.current = true;
    setOrphanActionsLocked(true);
    setOperationBusy(true);
    operationControllerRef.current?.abort();
    const controller = new AbortController();
    operationControllerRef.current = controller;
    return { ticket: ++operationSequenceRef.current, controller };
  }

  function isCurrentOperation(operation: OperationContext): boolean {
    return scopeIsCurrent()
      && operation.ticket === operationSequenceRef.current
      && !operation.controller.signal.aborted;
  }

  function finishOperation(operation: OperationContext): void {
    if (operation.ticket !== operationSequenceRef.current) return;
    operationBusyRef.current = false;
    if (operationControllerRef.current === operation.controller) operationControllerRef.current = null;
    setOrphanActionsLocked(noticeFreezesWrites(writeNoticeRef.current));
    if (scopeIsCurrent()) setOperationBusy(false);
  }

  function clearSensitiveData(message: string): void {
    accessLostRef.current = true;
    setDuplicateState({ status: "error", message });
    setIgnoredState({ status: "error", message });
    setOrphanState({ directoryEpoch, state: { status: "error", message } });
    setSelected(null);
    setConfirmation(null);
    updateWriteNotice((current) => {
      if (current === null) return null;
      const nextMessage = current.message.includes(message) ? current.message : `${current.message} ${message}`;
      if (current.kind === "committed" || current.kind === "committed-refresh-failed") {
        return { kind: "committed-refresh-failed", message: nextMessage, receipt: null };
      }
      if (current.kind === "unknown") return { ...current, message: nextMessage };
      return { kind: "error", message };
    });
  }

  function handleUnauthorized(error: ApiError): boolean {
    if (error.status !== 401) return false;
    unauthorizedRef.current();
    return true;
  }

  function handleScopeError(error: ApiError): boolean {
    if (error.status !== 403 && error.status !== 404) return false;
    clearSensitiveData("访问状态已变化，请重新读取并确认当前素材目录。");
    return true;
  }

  async function refreshMaintenanceData(
    operation: OperationContext,
    includeDirectory: boolean,
    clearSelection = true,
  ): Promise<boolean> {
    accessLostRef.current = false;
    setDuplicateState({ status: "loading" });
    setIgnoredState({ status: "loading" });
    const refreshEpoch = includeDirectory ? onDirectoryNeedsRefresh() : null;

    const duplicatePromise = Promise.resolve().then(() => port.listDuplicates(seriesId, maintenanceType, operation.controller.signal));
    const ignoredPromise = Promise.resolve().then(() => port.listIgnoredDuplicates(seriesId, maintenanceType, operation.controller.signal));
    const directoryPromise = includeDirectory
      ? Promise.resolve().then(() => readDirectory(services, category, seriesId, operation.controller.signal))
      : Promise.resolve(null);
    const [duplicates, ignored, directory] = await Promise.allSettled([
      duplicatePromise,
      ignoredPromise,
      directoryPromise,
    ]);
    if (!isCurrentOperation(operation)) return false;

    const failures = [duplicates, ignored, directory]
      .filter((result): result is PromiseRejectedResult => result.status === "rejected")
      .map((result) => normalizeApiError(result.reason));
    if (failures.some((error) => error.status === 401)) {
      handleUnauthorized(failures.find((error) => error.status === 401)!);
      clearSensitiveData("登录状态已变化，请重新登录后读取素材。");
      return false;
    }
    const accessError = failures.find((error) => error.status === 403 || error.status === 404);
    if (accessError !== undefined) {
      clearSensitiveData("访问状态已变化，请重新读取并确认当前素材目录。");
      if (includeDirectory) onDirectoryNeedsRefresh();
      return false;
    }

    if (duplicates.status === "fulfilled") {
      setDuplicateState({ status: "ready", data: duplicates.value });
    } else {
      setDuplicateState({ status: "error", message: errorText(normalizeApiError(duplicates.reason)) });
    }
    if (ignored.status === "fulfilled") {
      setIgnoredState({ status: "ready", data: ignored.value });
    } else {
      setIgnoredState({ status: "error", message: errorText(normalizeApiError(ignored.reason)) });
    }
    if (includeDirectory) {
      if (directory.status === "fulfilled" && directory.value !== null && refreshEpoch !== null) {
        onDirectoryUpdated(directory.value, refreshEpoch);
      } else {
        onDirectoryNeedsRefresh();
      }
    }
    if (clearSelection) {
      setSelected(null);
      setConfirmation(null);
    }
    return duplicates.status === "fulfilled"
      && ignored.status === "fulfilled"
      && (!includeDirectory || directory.status === "fulfilled");
  }

  async function loadMaintenanceData(includeDirectory = false): Promise<void> {
    if (writeFrozen) return;
    const operation = beginOperation();
    if (operation === null) return;
    updateWriteNotice((current) => current?.kind === "error" ? null : current);
    try {
      await refreshMaintenanceData(operation, includeDirectory);
      if (!isCurrentOperation(operation)) return;
    } catch (cause: unknown) {
      if (!isCurrentOperation(operation)) return;
      const error = normalizeApiError(cause);
      if (handleUnauthorized(error)) {
        clearSensitiveData("登录状态已变化，请重新登录后读取素材。");
      } else if (handleScopeError(error)) {
        if (includeDirectory) onDirectoryNeedsRefresh();
      } else {
        setDuplicateState({ status: "error", message: errorText(error) });
        setIgnoredState({ status: "error", message: errorText(error) });
        if (includeDirectory) onDirectoryNeedsRefresh();
      }
    } finally {
      finishOperation(operation);
    }
  }

  useEffect(() => {
    mountedRef.current = true;
    void loadMaintenanceData();
    return () => {
      mountedRef.current = false;
      operationBusyRef.current = false;
      operationSequenceRef.current += 1;
      operationControllerRef.current?.abort();
    };
  }, []);

  function updateGroupSelection(group: AssetDuplicateGroup, itemId: string, checked: boolean): void {
    if (operationBusy || writeFrozen) return;
    const key = groupKey(group);
    const baseIds = selected?.groupKey === key ? selected.ids : [];
    const nextIds = checked
      ? Array.from(new Set([...baseIds, itemId]))
      : baseIds.filter((id) => id !== itemId);
    if (nextIds.length > 50) return;
    setSelected({
      groupKey: key,
      ids: nextIds,
      keepId: selected?.groupKey === key && selected.keepId !== null && nextIds.includes(selected.keepId)
        ? selected.keepId
        : null,
    });
    if (writeNotice?.kind === "error") updateWriteNotice(null);
    setConfirmation(null);
  }

  function selectedGroup(group: AssetDuplicateGroup): AssetDuplicateItem[] | null {
    if (selected === null || selected.groupKey !== groupKey(group) || selected.ids.length < 2 || selected.ids.length > 50) {
      return null;
    }
    const ids = new Set(selected.ids);
    const items = group.items.filter((item) => ids.has(item.id));
    return items.length === selected.ids.length && new Set(items.map((item) => item.id)).size === items.length
      ? items
      : null;
  }

  function requestIgnoreConfirmation(group: AssetDuplicateGroup): void {
    const items = selectedGroup(group);
    if (items === null) return;
    setConfirmation({ kind: "ignore", items });
  }

  function requestMergeConfirmation(group: AssetDuplicateGroup): void {
    const items = selectedGroup(group);
    if (items === null || selected?.keepId === null || selected?.keepId === undefined
      || !items.some((item) => item.id === selected.keepId)) return;
    setConfirmation({ kind: "merge", items, keepId: selected.keepId });
  }

  function beginRestoreConfirmation(ids: [string, string], names: [string, string]): void {
    if (operationBusy || writeFrozen) return;
    setConfirmation({ kind: "restore", ids, names });
  }

  async function submitWrite(
    action: string,
    send: (signal: AbortSignal) => Promise<MergedAssetReceipt | SetAssetDuplicateIgnoredReceipt>,
    afterCommit: (operation: OperationContext, receipt: MergedAssetReceipt | SetAssetDuplicateIgnoredReceipt) => Promise<boolean>,
  ): Promise<void> {
    if (submittingRef.current || writeFrozen || !scopeIsCurrent()) return;
    submittingRef.current = true;
    const operation = beginOperation();
    if (operation === null) {
      submittingRef.current = false;
      return;
    }
    updateWriteNotice(null);
    setConfirmation(null);
    let committedReceipt: MergedAssetReceipt | SetAssetDuplicateIgnoredReceipt | null = null;
    try {
      let receipt: MergedAssetReceipt | SetAssetDuplicateIgnoredReceipt;
      try {
        receipt = await send(operation.controller.signal);
      } catch (cause: unknown) {
        if (!isCurrentOperation(operation)) return;
        const error = normalizeApiError(cause);
        if (handleUnauthorized(error)) {
          clearSensitiveData("登录状态已变化，请重新登录后读取素材。");
          return;
        }
        if (handleScopeError(error)) {
          updateWriteNotice({ kind: "error", message: "访问状态已变化，请重新读取后再操作。" });
          return;
        }
        if (error.kind === "aborted") return;
        if (isUncertainWrite(error)) {
          updateWriteNotice({
            kind: "unknown",
            action,
            message: `${action}请求结果未知。计划已保留，请先只重新读取核对，不能自动重发。`,
          });
          return;
        }
        updateWriteNotice({ kind: "error", message: errorText(error) });
        return;
      }

      if (!isCurrentOperation(operation)) return;
      committedReceipt = receipt;
      const refreshed = await afterCommit(operation, receipt);
      if (!isCurrentOperation(operation)) return;
      if (refreshed) {
        updateWriteNotice({
          kind: "committed",
          message: action === "合并"
            ? "合并已提交，素材目录和维护结果已重新读取。"
            : `${action}已提交，疑似重复和已忽略列表已重新读取。`,
          receipt,
        });
        setSelected(null);
      } else {
        updateWriteNotice({
          kind: "committed-refresh-failed",
          message: `${action}已提交，但重新读取失败。可以只重读数据，不能重发本次写入。`,
          receipt: accessLostRef.current ? null : receipt,
        });
      }
    } catch (cause: unknown) {
      if (!isCurrentOperation(operation)) return;
      const error = normalizeApiError(cause);
      if (handleUnauthorized(error)) {
        clearSensitiveData("登录状态已变化，请重新登录后读取素材。");
        updateWriteNotice({ kind: "committed-refresh-failed", message: "写入已提交；登录状态变化导致回读中断。", receipt: null });
      } else if (handleScopeError(error)) {
        updateWriteNotice({ kind: "committed-refresh-failed", message: "写入已提交；访问状态变化导致回读失败。", receipt: null });
      } else {
        updateWriteNotice({ kind: "committed-refresh-failed", message: `${action}已提交，但重新读取失败。`, receipt: committedReceipt });
      }
    } finally {
      submittingRef.current = false;
      finishOperation(operation);
    }
  }

  async function confirmAction(): Promise<void> {
    if (confirmation === null || operationBusy || writeFrozen) return;
    const pending = confirmation;
    const type = maintenanceType;
    if (pending.kind === "ignore") {
      const ids = pending.items.map((item) => item.id);
      if (ids.length < 2 || ids.length > 50 || new Set(ids).size !== ids.length) return;
      await submitWrite("标记不是重复", (signal) => port.setDuplicateIgnored(
        seriesId,
        { type, ids, ignored: true },
        signal,
      ), async (operation) => refreshMaintenanceData(operation, false));
      return;
    }
    if (pending.kind === "restore") {
      await submitWrite("恢复检测", (signal) => port.setDuplicateIgnored(
        seriesId,
        { type, ids: pending.ids, ignored: false },
        signal,
      ), async (operation) => refreshMaintenanceData(operation, false));
      return;
    }

    const keep = pending.items.find((item) => item.id === pending.keepId);
    const mergeIds = pending.items.filter((item) => item.id !== pending.keepId).map((item) => item.id);
    if (keep === undefined || mergeIds.length < 1 || mergeIds.length > 50 || new Set(mergeIds).size !== mergeIds.length) return;
    await submitWrite("合并", (signal) => port.mergeAssets(
      seriesId,
      { type, keep_id: pending.keepId, merge_ids: mergeIds },
      signal,
    ), async (operation) => refreshMaintenanceData(operation, true));
  }

  async function runOrphanCheck(): Promise<void> {
    if (operationBusy || writeFrozen) return;
    const checkedItems = directoryItems;
    const checkedDirectoryEpoch = directoryEpoch;
    const ids = checkedItems.map((item) => item.id);
    const uniqueIds = new Set(ids);
    const directoryIsSafe = checkedItems.every((item) => item.series_id === seriesId
      && typeof item.id === "string" && item.id.trim() !== "") && uniqueIds.size === ids.length;
    if (!directoryIsSafe) {
      setOrphanState({
        directoryEpoch: checkedDirectoryEpoch,
        state: { status: "error", message: "当前目录中存在身份不完整或重复的素材，无法安全检查。" },
      });
      return;
    }
    const operation = beginOperation();
    if (operation === null) return;
    setOrphanState({ directoryEpoch: checkedDirectoryEpoch, state: { status: "loading" } });
    updateWriteNotice((current) => current?.kind === "error" ? null : current);
    try {
      const candidates: AssetOrphanCandidate[] = checkedItems.map((item) => ({ type: maintenanceType, id: item.id }));
      const response = await port.checkOrphans(seriesId, { items: candidates }, operation.controller.signal);
      if (!isCurrentOperation(operation)) return;
      if (!isDirectoryCurrent(checkedItems, checkedDirectoryEpoch)) {
        setOrphanState({
          directoryEpoch: checkedDirectoryEpoch,
          state: { status: "error", message: "目录已更新，旧检查结果不能用于删除。请重新读取后再检查。" },
        });
        return;
      }
      const counts = new Map<string, number>();
      for (const candidate of response.orphans) {
        if (candidate.type === maintenanceType) counts.set(candidate.id, (counts.get(candidate.id) ?? 0) + 1);
      }
      const confirmed = checkedItems.filter((item) => counts.get(item.id) === 1);
      setOrphanState({ directoryEpoch: checkedDirectoryEpoch, state: { status: "ready", data: confirmed } });
      const unmatchedCount = response.orphans.length - confirmed.length;
      if (unmatchedCount > 0) {
        updateWriteNotice({ kind: "error", message: "服务返回了不属于当前目录的候选，已忽略这些结果。" });
      }
    } catch (cause: unknown) {
      if (!isCurrentOperation(operation)) return;
      const error = normalizeApiError(cause);
      if (handleUnauthorized(error)) {
        clearSensitiveData("登录状态已变化，请重新登录后读取素材。");
      } else if (handleScopeError(error)) {
        onDirectoryNeedsRefresh();
      } else if (error.kind !== "aborted") {
        setOrphanState({ directoryEpoch: checkedDirectoryEpoch, state: { status: "error", message: errorText(error) } });
      }
    } finally {
      finishOperation(operation);
    }
  }

  async function reconcileUnknown(): Promise<void> {
    if (writeNotice?.kind !== "unknown" && writeNotice?.kind !== "committed-refresh-failed") return;
    const priorNotice = writeNotice;
    const operation = beginOperation();
    if (operation === null) return;
    try {
      const refreshed = await refreshMaintenanceData(operation, true, priorNotice.kind !== "unknown");
      if (!isCurrentOperation(operation)) return;
      if (priorNotice.kind === "committed-refresh-failed" && refreshed) {
        updateWriteNotice({ kind: "committed", message: "写入已提交，目录和维护数据已重新读取。", receipt: priorNotice.receipt });
      } else if (priorNotice.kind === "unknown") {
        updateWriteNotice({
          ...priorNotice,
          message: refreshed
            ? "数据已重新读取，但无法证明先前请求的写入结果。请检查当前数据；不会自动重发。"
            : "核对读取未完成，先前写入结果仍未知。不会自动重发。",
        });
      }
    } finally {
      finishOperation(operation);
    }
  }

  function abandonLocalReconciliation(): void {
    if (operationBusy || (writeNotice?.kind !== "unknown" && writeNotice?.kind !== "committed-refresh-failed")) return;
    updateWriteNotice(null);
    setSelected(null);
    setConfirmation(null);
  }

  function changeTab(next: MaintenanceTab): void {
    if (operationBusy) return;
    setTab(next);
    setConfirmation(null);
  }

  function locateOrphan(item: AssetRecord): void {
    if (operationBusyRef.current || noticeFreezesWrites(writeNoticeRef.current)) return;
    onLocateAsset(item, directoryEpoch);
  }

  function openOrphanDelete(item: AssetRecord): void {
    if (operationBusyRef.current || noticeFreezesWrites(writeNoticeRef.current)) return;
    onOpenDelete(item, directoryEpoch);
  }

  const orphanItems = currentOrphanState.status === "ready" ? currentOrphanState.data : [];
  const duplicateList = duplicateState.status === "ready" ? duplicateState.data : null;
  const ignoredList = ignoredState.status === "ready" ? ignoredState.data : null;
  const confirmDisabled = operationBusy || writeFrozen;
  const groupSelectionCount = selected?.ids.length ?? 0;

  return (
    <div className="asset-maintenance-backdrop">
      <section
        aria-labelledby="asset-maintenance-title"
        aria-modal="true"
        className="asset-maintenance-dialog"
        ref={focus.dialogRef}
        role="dialog"
        tabIndex={-1}
      >
        <header className="asset-maintenance-header">
          <div>
            <p className="eyebrow">{seriesName} · 素材目录 · {label}</p>
            <h2 id="asset-maintenance-title">整理素材</h2>
          </div>
          <button
            aria-label="关闭素材整理窗口"
            className="asset-maintenance-close"
            disabled={cannotDismiss}
            onClick={focus.dismiss}
            type="button"
          >×</button>
        </header>

        <p className="asset-maintenance-intro">
          孤立项按剧集已保存章节检查。引用次数表示引用出现次数，不代表章节数。每次整理都会由服务端重新检查权限。
        </p>

        <div className="asset-maintenance-tabs" role="tablist" aria-label="素材整理方式">
          <button aria-selected={tab === "orphans"} onClick={() => changeTab("orphans")} role="tab" type="button">孤立项</button>
          <button aria-selected={tab === "duplicates"} onClick={() => changeTab("duplicates")} role="tab" type="button">疑似重复</button>
          <button aria-selected={tab === "ignored"} onClick={() => changeTab("ignored")} role="tab" type="button">
            已忽略{ignoredList === null ? "" : ` (${ignoredList.total})`}
          </button>
        </div>

        {writeNotice !== null && (
          <div className={`asset-maintenance-notice ${writeNotice.kind}`} role={writeNotice.kind === "error" ? "alert" : "status"}>
            <p>{writeNotice.message}</p>
            {writeNotice.kind === "unknown" && (
              <div className="asset-maintenance-actions">
                <button className="secondary-button" disabled={operationBusy} onClick={() => void reconcileUnknown()} type="button">仅重新读取核对</button>
                <button className="secondary-button" disabled={operationBusy} onClick={abandonLocalReconciliation} type="button">放弃本地核对</button>
              </div>
            )}
            {writeNotice.kind === "committed-refresh-failed" && (
              <div className="asset-maintenance-actions">
                <button className="secondary-button" disabled={operationBusy} onClick={() => void reconcileUnknown()} type="button">仅重新读取目录和维护结果</button>
                <button className="secondary-button" disabled={operationBusy} onClick={abandonLocalReconciliation} type="button">关闭回读提示</button>
              </div>
            )}
            {(writeNotice.kind === "committed" || writeNotice.kind === "committed-refresh-failed")
              && writeNotice.receipt !== null && "merged_count" in writeNotice.receipt && (
              <div className="asset-maintenance-receipt">
                <p>{writeNotice.receipt.message}</p>
                <p>保留素材编号：{writeNotice.receipt.keep_id}；合并 {writeNotice.receipt.merged_count} 项；改写 {writeNotice.receipt.redirected_frames} 个镜头引用；更新 {writeNotice.receipt.fused_updated} 个融合成员；新增 {writeNotice.receipt.aliases_added} 个别名。</p>
                {writeNotice.receipt.dropped_images.length > 0 && (
                  <p>未保留图片：{writeNotice.receipt.dropped_images.map((item) => item.name).join("、")}</p>
                )}
                {writeNotice.receipt.aliases.length > 0 && <p>合并后别名：{writeNotice.receipt.aliases.join("、")}</p>}
              </div>
            )}
            {(writeNotice.kind === "committed" || writeNotice.kind === "committed-refresh-failed")
              && writeNotice.receipt !== null && "pairs_total" in writeNotice.receipt && (
              <p>本次涉及组合数：{writeNotice.receipt.pairs_total}。</p>
            )}
          </div>
        )}

        <div className="asset-maintenance-body" role="tabpanel">
          {tab === "orphans" && (
            <section aria-labelledby="asset-maintenance-orphans-heading">
              <div className="asset-maintenance-section-heading">
                <div>
                  <h3 id="asset-maintenance-orphans-heading">检查当前分类的全部素材</h3>
                  <p>检查失败会保留为未知状态，不会当作没有孤立项。</p>
                </div>
                <button className="secondary-button" disabled={operationBusy || writeFrozen} onClick={() => void runOrphanCheck()} type="button">检查孤立项</button>
              </div>
              {currentOrphanState.status === "loading" && <p className="asset-maintenance-state" role="status">正在检查已保存章节引用…</p>}
              {currentOrphanState.status === "error" && <p className="asset-maintenance-error" role="alert">{currentOrphanState.message}</p>}
              {currentOrphanState.status === "ready" && orphanItems.length === 0 && (
                <p className="asset-maintenance-state" role="status">已完成检查，未发现孤立素材。</p>
              )}
              {currentOrphanState.status === "ready" && orphanItems.length > 0 && (
                <>
                  <p className="asset-maintenance-state" role="status">已确认 {orphanItems.length} 项当前目录素材没有被已保存章节引用。检查结果仅代表本次读取时点。</p>
                  <ul className="asset-maintenance-orphan-list">
                    {orphanItems.map((item) => (
                      <li key={item.id}>
                        <span>{assetName(category, item)}</span>
                        <div className="asset-maintenance-actions">
                          <button className="secondary-button" disabled={operationBusy || writeFrozen} onClick={() => locateOrphan(item)} type="button">查看卡片</button>
                          <button className="secondary-button" disabled={operationBusy || writeFrozen} onClick={() => openOrphanDelete(item)} type="button">打开删除确认</button>
                        </div>
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </section>
          )}

          {tab === "duplicates" && (
            <section aria-labelledby="asset-maintenance-duplicates-heading">
              <div className="asset-maintenance-section-heading">
                <div>
                  <h3 id="asset-maintenance-duplicates-heading">服务端识别的疑似重复组</h3>
                  {duplicateList !== null && (
                    <p>当前分类素材总数 {duplicateList.total} 项；疑似重复 {duplicateList.groups.length} 组；已忽略组合 {duplicateList.ignored_total} 对。</p>
                  )}
                </div>
                <button className="secondary-button" disabled={operationBusy || writeFrozen} onClick={() => void loadMaintenanceData()} type="button">重新读取</button>
              </div>
              {duplicateState.status === "loading" && <p className="asset-maintenance-state" role="status">正在读取疑似重复…</p>}
              {duplicateState.status === "error" && <p className="asset-maintenance-error" role="alert">{duplicateState.message}</p>}
              {duplicateState.status === "ready" && duplicateList?.groups.length === 0 && (
                <p className="asset-maintenance-state" role="status">当前分类没有疑似重复组。</p>
              )}
              {duplicateList !== null && duplicateList.groups.length > 0 && (
                <div className="asset-maintenance-groups">
                  {duplicateList.groups.map((group) => {
                    const key = groupKey(group);
                    const groupSelected = selected?.groupKey === key;
                    const groupItems = selectedGroup(group);
                    const canAct = !operationBusy && !writeFrozen && groupItems !== null;
                    return (
                      <article aria-label={`疑似重复组 ${group.index + 1}`} className="asset-maintenance-group" key={key}>
                        <h4>疑似重复组 {group.index + 1}</h4>
                        {group.items.length > 50 && <p className="asset-maintenance-note">此组超过50项，请选择最多50项组成一次明确操作。</p>}
                        {group.items.map((item) => {
                          const selectedItem = groupSelected && selected?.ids.includes(item.id) === true;
                          const canKeep = selectedItem && groupSelected;
                          return (
                            <div className="asset-maintenance-item" key={item.id}>
                              <SafeAssetImage apiBaseUrl={apiBaseUrl} name={item.name} url={item.image_url} />
                              <div className="asset-maintenance-item-copy">
                                <strong>{item.name}</strong>
                                <p>{item.description || "暂无描述"}</p>
                                <p className="asset-maintenance-note">引用 {item.ref_count} 次{item.aliases.length === 0 ? "" : ` · 别名：${item.aliases.join("、")}`}</p>
                                <label className="asset-maintenance-choice">
                                  <input
                                    checked={selectedItem}
                                    disabled={operationBusy || writeFrozen || (!selectedItem && groupSelected && groupSelectionCount >= 50)}
                                    onChange={(event) => updateGroupSelection(group, item.id, event.currentTarget.checked)}
                                    type="checkbox"
                                  />
                                  纳入本次操作
                                </label>
                                <label className="asset-maintenance-choice">
                                  <input
                                    checked={selected?.keepId === item.id && groupSelected}
                                    disabled={operationBusy || writeFrozen || !canKeep}
                                    name="asset-maintenance-keep"
                                    onChange={() => {
                                      setSelected((current) => current?.groupKey === key && current.ids.includes(item.id)
                                        ? { ...current, keepId: item.id }
                                        : current);
                                      setConfirmation(null);
                                    }}
                                    type="radio"
                                  />
                                  保留此项
                                </label>
                              </div>
                            </div>
                          );
                        })}
                        <div className="asset-maintenance-actions">
                          <button className="secondary-button" disabled={!canAct} onClick={() => requestIgnoreConfirmation(group)} type="button">标记所选不是重复</button>
                          <button className="primary-button" disabled={!canAct || selected?.keepId === null} onClick={() => requestMergeConfirmation(group)} type="button">合并所选素材</button>
                        </div>
                      </article>
                    );
                  })}
                </div>
              )}
            </section>
          )}

          {tab === "ignored" && (
            <section aria-labelledby="asset-maintenance-ignored-heading">
              <div className="asset-maintenance-section-heading">
                <div>
                  <h3 id="asset-maintenance-ignored-heading">已忽略的素材组合</h3>
                  {ignoredList !== null && <p>共 {ignoredList.total} 对。恢复后会重新参与重复检测。</p>}
                </div>
                <button className="secondary-button" disabled={operationBusy || writeFrozen} onClick={() => void loadMaintenanceData()} type="button">重新读取</button>
              </div>
              {ignoredState.status === "loading" && <p className="asset-maintenance-state" role="status">正在读取已忽略组合…</p>}
              {ignoredState.status === "error" && <p className="asset-maintenance-error" role="alert">{ignoredState.message}</p>}
              {ignoredList !== null && ignoredList.items.length === 0 && <p className="asset-maintenance-state" role="status">当前没有已忽略组合。</p>}
              {ignoredList !== null && ignoredList.items.length > 0 && (
                <ul className="asset-maintenance-ignored-list">
                  {ignoredList.items.map((item) => (
                    <li key={item.id}>
                      <div className="asset-maintenance-ignored-pair">
                        <SafeAssetImage apiBaseUrl={apiBaseUrl} name={item.name_a} url={item.image_a} />
                        <span>{item.name_a}</span>
                        <span aria-hidden="true">↔</span>
                        <SafeAssetImage apiBaseUrl={apiBaseUrl} name={item.name_b} url={item.image_b} />
                        <span>{item.name_b}</span>
                      </div>
                      <button className="secondary-button" disabled={operationBusy || writeFrozen} onClick={() => beginRestoreConfirmation(
                        [item.asset_id_a, item.asset_id_b], [item.name_a, item.name_b],
                      )} type="button">恢复检测</button>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          )}
        </div>

        {confirmation !== null && (
          <section aria-label="操作确认" className="asset-maintenance-confirmation" role="group">
            {confirmation.kind === "ignore" && (
              <>
                <h3>确认标记为不是重复</h3>
                <p>以下 {confirmation.items.length} 项将组成一组忽略组合，并在后续疑似重复检测中跳过。</p>
                <p>{confirmation.items.map((item) => item.name).join("、")}</p>
              </>
            )}
            {confirmation.kind === "restore" && (
              <>
                <h3>确认恢复检测</h3>
                <p>恢复「{confirmation.names[0]}」与「{confirmation.names[1]}」这对素材，后续会重新参与重复检测。</p>
              </>
            )}
            {confirmation.kind === "merge" && (
              <>
                <h3>确认合并素材</h3>
                <p>保留「{confirmation.items.find((item) => item.id === confirmation.keepId)?.name ?? "所选素材"}」的名称，其余所选素材将删除；全剧引用与融合成员会改指向保留项，名字与别名会合并。</p>
                <p>多张图片不会全部保留；保留项没有图片时，由服务端按自身查询顺序选择一张。其他文字和音频字段不会自动融合。</p>
              </>
            )}
            <div className="asset-maintenance-actions">
              <button className="secondary-button" disabled={confirmDisabled} onClick={() => setConfirmation(null)} type="button">取消</button>
              <button className="primary-button" disabled={confirmDisabled} onClick={() => void confirmAction()} type="button">
                {confirmation.kind === "ignore" ? "确认标记不是重复" : confirmation.kind === "restore" ? "确认恢复检测" : "确认合并这些素材"}
              </button>
            </div>
          </section>
        )}

        <footer className="asset-maintenance-footer">
          <span>{operationBusy || writePending ? "正在处理…" : `当前分类：${label}`}</span>
          <button className="secondary-button" disabled={cannotDismiss} onClick={focus.dismiss} type="button">关闭</button>
        </footer>
      </section>
    </div>
  );
}
