import { useEffect, useRef, useState, type ChangeEvent } from "react";
import type { AssetLibraryType } from "../../../shared/api/contracts";
import {
  AssetImageUploadError,
  type AssetImageDirectory,
  type AssetImageRecord,
  type AssetImageSource,
  type AssetImageUploadReceipt,
  type AssetImageUploadRun,
} from "../../../shared/api/assetImageUpload";
import { useSeriesDialogFocus } from "../../series/useSeriesDialogFocus";
import { AssetImagePreview } from "./AssetImagePreview";
import "./assetImageUpload.css";

export type AssetImageAttemptState =
  | "uploading"
  | "confirming"
  | "uploaded"
  | "saving"
  | "acknowledged"
  | "verified"
  | "upload-not-sent"
  | "save-not-sent"
  | "upload-rejected"
  | "upload-unknown"
  | "save-rejected"
  | "save-unknown"
  | "acknowledged-read-failed"
  | "acknowledged-observed-different"
  | "confirmation-stale"
  | "unknown-observed-match"
  | "unknown-observed-different"
  | "unknown-read-failed";

export interface AssetImageUploadAttempt {
  id: number;
  runId: number;
  file: File;
  receipt: AssetImageUploadReceipt | null;
  state: AssetImageAttemptState;
  message: string;
}

export interface AssetImageUploadDialogProps {
  category: AssetLibraryType;
  asset: AssetImageRecord;
  seriesName: string;
  runId: number;
  run: AssetImageUploadRun;
  attempts: AssetImageUploadAttempt[];
  isCurrent(): boolean;
  allocateAttemptId(): number;
  onAttemptsChange(attempts: AssetImageUploadAttempt[]): void;
  onClose(): void;
  onDirectoryUpdated(directory: AssetImageDirectory): void;
  onUnauthorized(): void;
}

const acceptedImageTypes = "image/jpeg,image/png,image/gif,image/webp,image/bmp";

interface EntryDirectoryRead {
  generation: number;
  scope: symbol;
  controller: AbortController;
}

function categoryLabel(category: AssetLibraryType): string {
  switch (category) {
    case "characters": return "角色";
    case "scenes": return "场景";
    case "props": return "道具";
  }
}

function assetName(category: AssetLibraryType, asset: AssetImageRecord): string {
  const value = category === "scenes"
    ? ("title" in asset ? asset.title : "")
    : ("name" in asset ? asset.name : "");
  return value.trim() || `未命名${categoryLabel(category)}`;
}

function isReadOnlyRecovery(state: AssetImageAttemptState): boolean {
  return state === "save-unknown"
    || state === "acknowledged-read-failed"
    || state === "acknowledged-observed-different"
    || state === "unknown-observed-match"
    || state === "unknown-observed-different"
    || state === "unknown-read-failed";
}

function confirmationMaterial(category: AssetLibraryType, asset: AssetImageRecord): string {
  return JSON.stringify({
    id: asset.id,
    seriesId: asset.series_id,
    imageUrl: asset.image_url,
    displayName: assetName(category, asset),
  });
}

function matchingAsset(directory: AssetImageDirectory, category: AssetLibraryType, asset: AssetImageRecord): AssetImageRecord | null {
  if (directory.category !== category) return null;
  const matches = directory.items.filter((item) => item.id === asset.id);
  if (matches.length !== 1 || matches[0]?.series_id !== asset.series_id) return null;
  return matches[0];
}

function errorState(error: unknown, phase: "upload" | "save"): AssetImageAttemptState {
  if (error instanceof AssetImageUploadError) {
    if (error.kind === "not-sent") return phase === "upload" ? "upload-not-sent" : "save-not-sent";
    if (error.kind === "unknown") return phase === "upload" ? "upload-unknown" : "save-unknown";
    if (error.kind === "rejected") return phase === "upload" ? "upload-rejected" : "save-rejected";
  }
  return phase === "upload" ? "upload-unknown" : "save-unknown";
}

export function AssetImageUploadDialog({
  category,
  asset,
  seriesName,
  runId,
  run,
  attempts,
  isCurrent,
  allocateAttemptId,
  onAttemptsChange,
  onClose,
  onDirectoryUpdated,
  onUnauthorized,
}: AssetImageUploadDialogProps) {
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [directoryStatus, setDirectoryStatus] = useState<"loading" | "ready" | "failed" | "missing">("loading");
  const [directoryMessage, setDirectoryMessage] = useState("");
  const [directoryAsset, setDirectoryAsset] = useState<AssetImageRecord | null>(null);
  const [currentImageSource, setCurrentImageSource] = useState<AssetImageSource | null>(null);
  const operationControllerRef = useRef<AbortController | null>(null);
  const busyRef = useRef(false);
  const attemptsRef = useRef(attempts);
  const plannedMaterialRef = useRef<string | null>(null);
  const entryReadGenerationRef = useRef(0);
  const entryReadScopeRef = useRef<symbol | null>(null);
  const entryReadRequestRef = useRef<EntryDirectoryRead | null>(null);
  const isCurrentRef = useRef(isCurrent);
  const onDirectoryUpdatedRef = useRef(onDirectoryUpdated);
  const onUnauthorizedRef = useRef(onUnauthorized);
  attemptsRef.current = attempts;
  isCurrentRef.current = isCurrent;
  onDirectoryUpdatedRef.current = onDirectoryUpdated;
  onUnauthorizedRef.current = onUnauthorized;
  const latestAttempt = attempts.at(-1) ?? null;

  useEffect(() => () => {
    operationControllerRef.current?.abort();
    operationControllerRef.current = null;
  }, []);

  function updateAttempt(attemptId: number, update: Partial<AssetImageUploadAttempt>): void {
    if (!isCurrent()) return;
    const currentAttempts = attemptsRef.current;
    const nextAttempts = currentAttempts.map((attempt) => attempt.id === attemptId
      ? { ...attempt, ...update }
      : attempt);
    if (!currentAttempts.some((attempt) => attempt.id === attemptId)) return;
    attemptsRef.current = nextAttempts;
    onAttemptsChange(nextAttempts);
  }

  function appendAttempt(attempt: AssetImageUploadAttempt): void {
    if (!isCurrent()) return;
    const nextAttempts = [...attemptsRef.current, attempt];
    attemptsRef.current = nextAttempts;
    onAttemptsChange(nextAttempts);
  }

  function reportUnauthorized(error: unknown): void {
    if (error instanceof AssetImageUploadError && error.status === 401 && isCurrentRef.current() && run.isCurrent()) {
      onUnauthorizedRef.current();
    }
  }

  function isCurrentEntryRead(request: EntryDirectoryRead): boolean {
    return entryReadRequestRef.current === request
      && entryReadGenerationRef.current === request.generation
      && entryReadScopeRef.current === request.scope;
  }

  function loadEntryDirectory(scope = entryReadScopeRef.current): void {
    if (scope === null || busyRef.current) return;
    const request: EntryDirectoryRead = {
      generation: ++entryReadGenerationRef.current,
      scope,
      controller: new AbortController(),
    };
    entryReadRequestRef.current = request;
    busyRef.current = true;
    operationControllerRef.current = request.controller;
    setDirectoryStatus("loading");
    setDirectoryMessage("");
    void readEntryDirectory(request);
  }

  async function readEntryDirectory(request: EntryDirectoryRead): Promise<void> {
    try {
      const directory = await run.readDirectory(request.controller.signal);
      if (!isCurrentEntryRead(request) || !isCurrentRef.current() || !run.isCurrent()) return;
      const currentAsset = matchingAsset(directory, category, asset);
      if (currentAsset === null) {
        setDirectoryStatus("missing");
        setDirectoryMessage("当前目录中没有唯一且属于本剧集的素材记录，不能继续图片操作。");
        return;
      }
      plannedMaterialRef.current = confirmationMaterial(category, currentAsset);
      setDirectoryAsset(currentAsset);
      setCurrentImageSource(directory.imageSources.get(currentAsset.id) ?? null);
      onDirectoryUpdatedRef.current(directory);
      setDirectoryStatus("ready");
      setDirectoryMessage("已读取当前素材目录，图片上传记录已就绪。");
    } catch (error) {
      if (!isCurrentEntryRead(request) || !isCurrentRef.current() || !run.isCurrent()) return;
      reportUnauthorized(error);
      setDirectoryStatus("failed");
      setDirectoryMessage(error instanceof Error ? error.message : "当前素材目录读取失败，尚未开始图片操作。");
    } finally {
      if (entryReadRequestRef.current === request) {
        entryReadRequestRef.current = null;
        if (operationControllerRef.current === request.controller) {
          operationControllerRef.current = null;
        }
        busyRef.current = false;
      }
    }
  }

  useEffect(() => {
    const scope = Symbol("asset-image-upload-dialog-entry");
    entryReadScopeRef.current = scope;
    loadEntryDirectory(scope);
    return () => {
      if (entryReadScopeRef.current !== scope) return;
      entryReadScopeRef.current = null;
      entryReadGenerationRef.current += 1;
      const activeRead = entryReadRequestRef.current;
      if (activeRead?.scope === scope) {
        entryReadRequestRef.current = null;
        if (operationControllerRef.current === activeRead.controller) {
          operationControllerRef.current = null;
        }
        activeRead.controller.abort();
        busyRef.current = false;
        return;
      }
      operationControllerRef.current?.abort();
      operationControllerRef.current = null;
    };
  }, [run, runId, category, asset.id, asset.series_id]);

  function onFileChange(event: ChangeEvent<HTMLInputElement>): void {
    const file = event.currentTarget.files?.[0] ?? null;
    setSelectedFile(file);
    event.currentTarget.value = "";
  }

  async function startUpload(): Promise<void> {
    if (directoryStatus !== "ready" || busyRef.current || selectedFile === null || !isCurrent() || !run.isCurrent()) return;
    busyRef.current = true;
    const attempt: AssetImageUploadAttempt = {
      id: allocateAttemptId(),
      runId,
      file: selectedFile,
      receipt: null,
      state: "uploading",
      message: "正在发送本次图片上传。",
    };
    appendAttempt(attempt);
    const controller = new AbortController();
    operationControllerRef.current = controller;
    try {
      const receipt = await run.uploadImage(attempt.file, controller.signal);
      if (!isCurrentRef.current() || !run.isCurrent()) return;
      updateAttempt(attempt.id, {
        receipt,
        state: "uploaded",
        message: "图片上传已收到服务器回执；尚未修改素材目录。",
      });
      setSelectedFile(null);
    } catch (error) {
      if (!isCurrentRef.current() || !run.isCurrent()) return;
      reportUnauthorized(error);
      updateAttempt(attempt.id, {
        state: errorState(error, "upload"),
        message: error instanceof Error ? error.message : "上传结果尚未确认。",
      });
    } finally {
      operationControllerRef.current = null;
      busyRef.current = false;
    }
  }

  async function observeDirectory(attempt: AssetImageUploadAttempt, afterAck: boolean): Promise<void> {
    if (busyRef.current || !isCurrent() || !run.isCurrent()) return;
    busyRef.current = true;
    const controller = new AbortController();
    operationControllerRef.current = controller;
    try {
      const directory = await run.readDirectory(controller.signal);
      if (!isCurrentRef.current() || !run.isCurrent()) return;
      onDirectoryUpdated(directory);
      const matchingAssets = directory.items.filter((item) => item.id === asset.id);
      const observedExpected = matchingAssets.length === 1 && attempt.receipt !== null
        && matchingAssets[0]?.image_url === attempt.receipt.url;
      if (afterAck) {
        updateAttempt(attempt.id, observedExpected
          ? { state: "verified", message: "更新回执与独立目录读取一致。" }
          : { state: "acknowledged-observed-different", message: "服务器已确认更新，但独立目录读取到的图片地址不同；仅记录观察结果。" });
      } else {
        updateAttempt(attempt.id, observedExpected
          ? { state: "unknown-observed-match", message: "目录读取到本次图片地址，但原更新回执仍未知；观察结果不等于服务器确认。" }
          : { state: "unknown-observed-different", message: "只读目录读取没有确认本次图片地址；原更新回执仍未知。" });
      }
    } catch (error) {
      if (!isCurrentRef.current() || !run.isCurrent()) return;
      reportUnauthorized(error);
      updateAttempt(attempt.id, {
        state: afterAck ? "acknowledged-read-failed" : "unknown-read-failed",
        message: error instanceof Error ? error.message : "只读目录核对失败。",
      });
    } finally {
      operationControllerRef.current = null;
      busyRef.current = false;
    }
  }

  async function confirmSave(attempt: AssetImageUploadAttempt): Promise<void> {
    if (busyRef.current || attempt.receipt === null || attempt.state !== "uploaded"
      || plannedMaterialRef.current === null || !isCurrent() || !run.isCurrent()) return;
    busyRef.current = true;
    updateAttempt(attempt.id, { state: "confirming", message: "正在重新读取素材目录；尚未提交目录更新。" });
    const controller = new AbortController();
    operationControllerRef.current = controller;
    let saveAcknowledged = false;
    try {
      const freshDirectory = await run.readDirectory(controller.signal);
      if (!isCurrentRef.current() || !run.isCurrent()) return;
      const freshAsset = matchingAsset(freshDirectory, category, directoryAsset ?? asset);
      if (freshAsset === null || confirmationMaterial(category, freshAsset) !== plannedMaterialRef.current) {
        updateAttempt(attempt.id, {
          state: "confirmation-stale",
          message: "确认前素材信息已变化；上传回执已保留，本次没有提交目录更新。请关闭并重新开始。",
        });
        return;
      }
      onDirectoryUpdatedRef.current(freshDirectory);
      updateAttempt(attempt.id, { state: "saving", message: "素材信息仍与确认时一致，正在发送一次图片地址更新。" });
      await run.saveImage(attempt.receipt, controller.signal);
      if (!isCurrentRef.current() || !run.isCurrent()) return;
      saveAcknowledged = true;
      updateAttempt(attempt.id, { state: "acknowledged", message: "服务器已确认素材图片更新，正在独立读取目录。" });
      const directory = await run.readDirectory(controller.signal);
      if (!isCurrentRef.current() || !run.isCurrent()) return;
      onDirectoryUpdatedRef.current(directory);
      const matchingAssets = directory.items.filter((item) => item.id === (directoryAsset ?? asset).id);
      if (matchingAssets.length === 1 && matchingAssets[0]?.image_url === attempt.receipt.url) {
        updateAttempt(attempt.id, { state: "verified", message: "更新回执与独立目录读取一致。" });
      } else {
        updateAttempt(attempt.id, {
          state: "acknowledged-observed-different",
          message: "服务器已确认更新，但独立目录读取到的图片地址不同；仅记录观察结果。",
        });
      }
    } catch (error) {
      if (!isCurrentRef.current() || !run.isCurrent()) return;
      reportUnauthorized(error);
      if (saveAcknowledged && error instanceof AssetImageUploadError && error.kind === "read-error") {
        updateAttempt(attempt.id, {
          state: "acknowledged-read-failed",
          message: "服务器已确认更新，但独立目录读取失败；可重新执行只读核对。",
        });
      } else if (!saveAcknowledged && error instanceof AssetImageUploadError && error.kind === "read-error") {
        updateAttempt(attempt.id, {
          state: "uploaded",
          message: "确认前目录只读核对失败，未发送目录更新；上传回执已保留，可重新确认。",
        });
      } else {
        updateAttempt(attempt.id, {
          state: errorState(error, "save"),
          message: error instanceof Error ? error.message : "更新回执尚未确认。",
        });
      }
    } finally {
      operationControllerRef.current = null;
      busyRef.current = false;
    }
  }

  function handleClose(): void {
    operationControllerRef.current?.abort();
    onClose();
  }

  const focus = useSeriesDialogFocus({
    active: true,
    canDismiss: true,
    onDismiss: handleClose,
  });

  const previewAttempt = latestAttempt;
  const currentRunAttempt = previewAttempt?.runId === runId ? previewAttempt : null;
  const uploadedPreview = selectedFile === null
    && currentRunAttempt?.receipt !== null
    && currentRunAttempt?.receipt !== undefined
    && run.canReadImage(currentRunAttempt.receipt)
    ? currentRunAttempt.receipt
    : null;
  const candidateFile = selectedFile ?? (uploadedPreview === null ? (currentRunAttempt?.file ?? null) : null);
  const visibleAsset = directoryAsset ?? asset;
  const canStartUpload = directoryStatus === "ready" && selectedFile !== null && !busyRef.current && isCurrent() && run.isCurrent();

  return (
    <div className="asset-image-upload-overlay" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget && !busyRef.current) focus.dismiss();
    }}>
      <section
        aria-labelledby="asset-image-upload-heading"
        aria-modal="true"
        className="asset-image-upload-dialog"
        ref={focus.dialogRef}
        role="dialog"
      >
        <header className="asset-image-upload-header">
          <div>
            <p className="eyebrow">{seriesName} · {categoryLabel(category)}图片</p>
            <h2 id="asset-image-upload-heading">上传或更换图片：{assetName(category, visibleAsset)}</h2>
          </div>
          <button aria-label="关闭图片上传" className="asset-image-upload-close" onClick={focus.dismiss} type="button">×</button>
        </header>
        <div className="asset-image-upload-body">
          {directoryStatus === "loading" && <p role="status">正在读取当前素材目录…</p>}
          {directoryStatus === "ready" && <p role="status">{directoryMessage}</p>}
          {directoryStatus === "missing" && <p role="status">{directoryMessage}</p>}
          {directoryStatus === "failed" && (
            <div role="status">
              <p>{directoryMessage}</p>
              <button className="secondary-button" disabled={busyRef.current} onClick={() => void loadEntryDirectory()} type="button">
                重新读取当前素材目录
              </button>
            </div>
          )}
          <div className="asset-image-upload-previews">
            <section aria-label="当前图片">
              <h3>当前图片</h3>
              {currentImageSource === null
                ? <p className="asset-image-preview-state">当前素材没有已保存图片。</p>
                : <AssetImagePreview
                  alt={`${assetName(category, visibleAsset)}当前图片`}
                  key={`current:${asset.id}`}
                  run={run}
                  source={currentImageSource}
                />}
            </section>
            <section aria-label="待上传图片">
              <h3>待上传图片</h3>
              {uploadedPreview !== null
                ? <AssetImagePreview
                  alt={`${assetName(category, visibleAsset)}已上传图片`}
                  key={`uploaded:${previewAttempt?.id ?? 0}`}
                  run={run}
                  source={uploadedPreview}
                />
                : candidateFile !== null
                  ? <AssetImagePreview
                    alt={`${assetName(category, visibleAsset)}待上传图片`}
                    file={candidateFile}
                    key={`file:${previewAttempt?.id ?? "selected"}:${candidateFile.name}:${candidateFile.size}`}
                    run={run}
                  />
                  : <p className="asset-image-preview-state">选择图片后可查看预览。</p>}
            </section>
          </div>

          <label className="asset-image-upload-field">
            <span>图片文件</span>
            <input accept={acceptedImageTypes} aria-label="选择图片文件" onChange={onFileChange} type="file" />
            <small>支持 JPEG、PNG、GIF、WebP、BMP；最大 10 MiB。</small>
          </label>
          <button className="secondary-button" disabled={!canStartUpload} onClick={() => void startUpload()} type="button">
            上传所选图片
          </button>

          {attempts.length > 0 && (
            <section aria-label="图片操作记录" className="asset-image-upload-history">
              <h3>本次素材库会话的操作记录</h3>
              <ol>
                {attempts.map((attempt) => (
                  <li key={attempt.id}>
                    <strong>{attempt.file.name}</strong>
                    <span data-state={attempt.state} role="status">{attempt.message}</span>
                    {attempt.state === "uploaded" && attempt.receipt !== null && attempt.runId === runId
                      && run.canSaveImage(attempt.receipt) && (
                      <div className="asset-image-upload-confirmation">
                        <p>上传已完成。点击确认后会对该素材发送一次只修改图片地址的更新，并另行读取目录核对。</p>
                        <button className="primary-button" disabled={busyRef.current} onClick={() => void confirmSave(attempt)} type="button">
                          确认更新素材图片
                        </button>
                      </div>
                    )}
                    {attempt.state === "confirmation-stale" && attempt.receipt !== null && (
                      <span role="note">上传已完成，目录更新未发送；该回执不能按已变化的确认材料提交。</span>
                    )}
                    {attempt.state === "uploaded" && attempt.runId !== runId && (
                      <span role="note">该上传回执来自先前目录读取代次，不能用于当前更新；如需更新请重新上传。</span>
                    )}
                    {isReadOnlyRecovery(attempt.state) && attempt.receipt !== null && (
                      <button className="secondary-button" disabled={busyRef.current} onClick={() => void observeDirectory(
                        attempt,
                        attempt.state === "acknowledged-read-failed" || attempt.state === "acknowledged-observed-different",
                      )} type="button">
                        只读重新核对目录
                      </button>
                    )}
                  </li>
                ))}
              </ol>
            </section>
          )}
          <p className="asset-image-upload-note" role="note">
            关闭窗口或重新读取目录不会清除本次素材库会话的操作记录。未知结果只提供只读核对，不会自动重试。
          </p>
        </div>
        <footer className="asset-image-upload-footer">
          <button className="secondary-button" onClick={focus.dismiss} type="button">完成</button>
        </footer>
      </section>
    </div>
  );
}
