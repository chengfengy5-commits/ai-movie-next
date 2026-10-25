import { useEffect, useRef, useState, type ChangeEvent } from "react";
import type { Character } from "../../../shared/api/contracts";
import {
  CharacterAudioError,
  type CharacterAudioDirectory,
  type CharacterAudioRun,
  type CharacterAudioSource,
  type CharacterAudioTarget,
  type CharacterAudioUploadReceipt,
} from "../../../shared/api/characterAudio";
import { useSeriesDialogFocus } from "../../series/useSeriesDialogFocus";
import "./characterAudio.css";

export type CharacterAudioAttemptState =
  | "uploading"
  | "uploaded"
  | "upload-not-sent"
  | "upload-rejected"
  | "upload-unknown"
  | "confirming"
  | "saving"
  | "save-not-sent"
  | "save-rejected"
  | "save-unknown"
  | "acknowledged"
  | "verified"
  | "confirmation-stale"
  | "acknowledged-read-failed"
  | "acknowledged-observed-different"
  | "unknown-observed-match"
  | "unknown-observed-different"
  | "unknown-read-failed";

export interface CharacterAudioAttempt {
  readonly id: number;
  readonly runId: number;
  readonly fileName: string;
  readonly receipt: CharacterAudioUploadReceipt | null;
  readonly target: CharacterAudioTarget | null;
  readonly acknowledged: boolean;
  readonly state: CharacterAudioAttemptState;
  readonly message: string;
}

export interface CharacterAudioDialogProps {
  readonly character: Character;
  readonly seriesName: string;
  readonly runId: number;
  readonly run: CharacterAudioRun;
  readonly attempts: CharacterAudioAttempt[];
  isCurrent(): boolean;
  allocateAttemptId(): number;
  onAttemptsChange(attempts: CharacterAudioAttempt[]): void;
  onClose(): void;
  onDirectoryUpdated(directory: CharacterAudioDirectory): void;
  onUnauthorized(): void;
}

type PendingAudio = CharacterAudioUploadReceipt | null | undefined;

interface EntryRead {
  readonly id: number;
  readonly scope: symbol;
  readonly controller: AbortController;
}

function matchingCharacter(directory: CharacterAudioDirectory, character: Character): Character | null {
  if (directory.category !== "characters") return null;
  const matches = directory.items.filter((item) => item.id === character.id);
  if (matches.length !== 1 || matches[0]?.series_id !== character.series_id) return null;
  return matches[0];
}

function sameVoiceAndAudio(left: Character, right: Character): boolean {
  return left.id === right.id
    && left.series_id === right.series_id
    && left.audio_url === right.audio_url
    && left.voice_ref === right.voice_ref;
}

function hasPersistedAudio(character: Character): boolean {
  return typeof character.audio_url === "string" && character.audio_url.trim() !== "";
}

function attemptState(error: unknown, phase: "upload" | "save"): CharacterAudioAttemptState {
  if (error instanceof CharacterAudioError) {
    if (error.kind === "not-sent") return phase === "upload" ? "upload-not-sent" : "save-not-sent";
    if (error.kind === "rejected") return phase === "upload" ? "upload-rejected" : "save-rejected";
    if (error.kind === "unknown") return phase === "upload" ? "upload-unknown" : "save-unknown";
  }
  return phase === "upload" ? "upload-unknown" : "save-unknown";
}

function shouldAllowReadRecovery(state: CharacterAudioAttemptState): boolean {
  return state === "save-unknown"
    || state === "acknowledged-read-failed"
    || state === "acknowledged-observed-different"
    || state === "unknown-observed-match"
    || state === "unknown-observed-different"
    || state === "unknown-read-failed";
}

function isWaitingForReadRecovery(state: CharacterAudioAttemptState): boolean {
  return state === "save-unknown"
    || state === "acknowledged-read-failed"
    || state === "unknown-read-failed";
}

function latestReusableReceipt(
  attempts: CharacterAudioAttempt[],
  runId: number,
  run: CharacterAudioRun,
): CharacterAudioUploadReceipt | null {
  for (const attempt of [...attempts].reverse()) {
    if (attempt.receipt !== null && attempt.runId === runId && run.canSaveAudio(attempt.receipt)) {
      return attempt.receipt;
    }
  }
  return null;
}

function labelForCharacter(character: Character): string {
  return character.name.trim() || "未命名角色";
}

interface CharacterAudioPreviewProps {
  readonly run: CharacterAudioRun;
  readonly source: CharacterAudioSource | CharacterAudioUploadReceipt;
  readonly label: string;
  isCurrent(): boolean;
}

function CharacterAudioPreview({ run, source, label, isCurrent }: CharacterAudioPreviewProps) {
  const [state, setState] = useState<"loading" | "ready" | "failed">("loading");
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const objectUrlRef = useRef<string | null>(null);
  const isCurrentRef = useRef(isCurrent);
  isCurrentRef.current = isCurrent;

  function releasePlayback(): void {
    audioRef.current?.pause();
    audioRef.current?.removeAttribute("src");
    const currentUrl = objectUrlRef.current;
    objectUrlRef.current = null;
    if (currentUrl !== null) URL.revokeObjectURL(currentUrl);
  }

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setState("loading");
    setBlobUrl(null);
    void run.readAudio(source, controller.signal).then((blob) => {
      if (!active || !isCurrentRef.current() || !run.isCurrent()) return;
      if (blob.size <= 0) {
        setState("failed");
        return;
      }
      const nextUrl = URL.createObjectURL(blob);
      objectUrlRef.current = nextUrl;
      setBlobUrl(nextUrl);
      setState("ready");
    }).catch(() => {
      if (!active || !isCurrentRef.current()) return;
      setState("failed");
    });

    return () => {
      active = false;
      controller.abort();
      releasePlayback();
    };
  }, [run, source]);

  function handleAudioError(): void {
    releasePlayback();
    setBlobUrl(null);
    setState("failed");
  }

  return (
    <div className="character-audio-preview">
      {state === "loading" && <p role="status">正在读取音频字节…</p>}
      {state === "failed" && <p role="alert">音频未能加载或当前浏览器不支持此格式。请检查文件格式后重试。</p>}
      {state === "ready" && blobUrl !== null && (
        <>
          <audio aria-label={label} controls onError={handleAudioError} preload="metadata" ref={audioRef} src={blobUrl} />
          <p role="status">音频已加载，请点击试听。</p>
        </>
      )}
    </div>
  );
}

export function CharacterAudioDialog({
  character,
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
}: CharacterAudioDialogProps) {
  const [directoryStatus, setDirectoryStatus] = useState<"loading" | "ready" | "missing" | "failed">("loading");
  const [directoryMessage, setDirectoryMessage] = useState("正在读取当前角色目录…");
  const [currentCharacter, setCurrentCharacter] = useState<Character | null>(null);
  const [currentAudioSource, setCurrentAudioSource] = useState<CharacterAudioSource | null>(null);
  const [pendingAudio, setPendingAudio] = useState<PendingAudio>(undefined);
  const [voiceReference, setVoiceReference] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const operationControllerRef = useRef<AbortController | null>(null);
  const busyRef = useRef(false);
  const attemptsRef = useRef(attempts);
  const entryReadIdRef = useRef(0);
  const entryScopeRef = useRef<symbol | null>(null);
  const entryReadRef = useRef<EntryRead | null>(null);
  const isCurrentRef = useRef(isCurrent);
  const onAttemptsChangeRef = useRef(onAttemptsChange);
  const onDirectoryUpdatedRef = useRef(onDirectoryUpdated);
  const onUnauthorizedRef = useRef(onUnauthorized);
  attemptsRef.current = attempts;
  isCurrentRef.current = isCurrent;
  onAttemptsChangeRef.current = onAttemptsChange;
  onDirectoryUpdatedRef.current = onDirectoryUpdated;
  onUnauthorizedRef.current = onUnauthorized;

  useEffect(() => () => {
    operationControllerRef.current?.abort();
    operationControllerRef.current = null;
  }, []);

  function publishAttempts(nextAttempts: CharacterAudioAttempt[]): void {
    attemptsRef.current = nextAttempts;
    onAttemptsChangeRef.current(nextAttempts);
  }

  function updateAttempt(id: number, update: Partial<CharacterAudioAttempt>): void {
    if (!isCurrentRef.current()) return;
    const nextAttempts = attemptsRef.current.map((attempt) => attempt.id === id ? { ...attempt, ...update } : attempt);
    if (!attemptsRef.current.some((attempt) => attempt.id === id)) return;
    publishAttempts(nextAttempts);
  }

  function reportUnauthorized(error: unknown): void {
    if (error instanceof CharacterAudioError && error.status === 401
      && isCurrentRef.current() && run.isCurrent()) {
      onUnauthorizedRef.current();
    }
  }

  function isCurrentEntryRead(request: EntryRead): boolean {
    return entryReadRef.current === request
      && entryReadIdRef.current === request.id
      && entryScopeRef.current === request.scope;
  }

  async function readEntryDirectory(request: EntryRead): Promise<void> {
    try {
      const directory = await run.readDirectory(request.controller.signal);
      if (!isCurrentEntryRead(request) || !isCurrentRef.current() || !run.isCurrent()) return;
      const freshCharacter = matchingCharacter(directory, character);
      if (freshCharacter === null) {
        setCurrentCharacter(null);
        setCurrentAudioSource(null);
        setDirectoryStatus("missing");
        setDirectoryMessage("当前完整目录中没有唯一且属于本剧集的角色记录，不能继续音频维护。");
        return;
      }
      const source = directory.audioSources.get(freshCharacter.id) ?? null;
      setCurrentCharacter(freshCharacter);
      setCurrentAudioSource(source);
      setVoiceReference(freshCharacter.voice_ref ?? "");
      setPendingAudio(undefined);
      const reusableReceipt = latestReusableReceipt(attemptsRef.current, runId, run);
      if (reusableReceipt !== null) setPendingAudio(reusableReceipt);
      onDirectoryUpdatedRef.current(directory);
      setDirectoryStatus("ready");
      setDirectoryMessage("已读取完整角色目录并确认唯一角色身份；音频与音色草稿尚未保存。");
    } catch (error) {
      if (!isCurrentEntryRead(request) || !isCurrentRef.current()) return;
      reportUnauthorized(error);
      setDirectoryStatus("failed");
      setDirectoryMessage(error instanceof Error ? error.message : "当前角色目录读取失败，尚未开始音频操作。");
    } finally {
      if (entryReadRef.current === request) {
        entryReadRef.current = null;
        if (operationControllerRef.current === request.controller) operationControllerRef.current = null;
        busyRef.current = false;
        setBusy(false);
      }
    }
  }

  function loadEntryDirectory(): void {
    if (busyRef.current || !isCurrentRef.current() || !run.isCurrent()) return;
    const scope = Symbol("character-audio-entry");
    const request: EntryRead = {
      id: ++entryReadIdRef.current,
      scope,
      controller: new AbortController(),
    };
    entryScopeRef.current = scope;
    entryReadRef.current = request;
    operationControllerRef.current = request.controller;
    busyRef.current = true;
    setBusy(true);
    setDirectoryStatus("loading");
    setDirectoryMessage("正在读取当前角色目录…");
    void readEntryDirectory(request);
  }

  useEffect(() => {
    loadEntryDirectory();
    return () => {
      entryScopeRef.current = null;
      entryReadIdRef.current += 1;
      const activeRead = entryReadRef.current;
      entryReadRef.current = null;
      activeRead?.controller.abort();
      if (operationControllerRef.current === activeRead?.controller) operationControllerRef.current = null;
      operationControllerRef.current?.abort();
      operationControllerRef.current = null;
      busyRef.current = false;
    };
  }, [run, runId, character.id, character.series_id]);

  function handleFileChange(event: ChangeEvent<HTMLInputElement>): void {
    const file = event.currentTarget.files?.[0] ?? null;
    setSelectedFile(file);
    event.currentTarget.value = "";
  }

  function setBusyState(value: boolean): void {
    busyRef.current = value;
    setBusy(value);
  }

  async function startUpload(): Promise<void> {
    if (directoryStatus !== "ready" || selectedFile === null || busyRef.current
      || !isCurrentRef.current() || !run.isCurrent()) return;
    const attempt: CharacterAudioAttempt = {
      id: allocateAttemptId(),
      runId,
      fileName: selectedFile.name,
      receipt: null,
      target: null,
      acknowledged: false,
      state: "uploading",
      message: "正在发送一次音频上传；上传期间不会修改角色目录。",
    };
    publishAttempts([...attemptsRef.current, attempt]);
    setBusyState(true);
    const controller = new AbortController();
    operationControllerRef.current = controller;
    try {
      const receipt = await run.uploadAudio(selectedFile, controller.signal);
      if (!isCurrentRef.current() || !run.isCurrent()) return;
      updateAttempt(attempt.id, {
        receipt,
        state: "uploaded",
        message: "服务器已返回本次上传回执；角色目录仍未修改，请明确保存。",
      });
      setPendingAudio(receipt);
      setSelectedFile(null);
    } catch (error) {
      if (!isCurrentRef.current()) return;
      reportUnauthorized(error);
      updateAttempt(attempt.id, {
        state: attemptState(error, "upload"),
        message: error instanceof Error ? error.message : "音频上传结果尚未确认；不会自动重发。",
      });
    } finally {
      operationControllerRef.current = null;
      setBusyState(false);
    }
  }

  function buildTarget(): CharacterAudioTarget | null {
    if (currentCharacter === null) return null;
    const next: { audio?: CharacterAudioUploadReceipt | null; voice_ref?: string } = {};
    const audioChanged = pendingAudio !== undefined
      && (pendingAudio === null ? hasPersistedAudio(currentCharacter) : true);
    const voiceChanged = voiceReference !== (currentCharacter.voice_ref ?? "");
    if (audioChanged) next.audio = pendingAudio;
    if (voiceChanged) next.voice_ref = voiceReference;
    return Object.keys(next).length === 0 ? null : Object.freeze(next);
  }

  async function observeTarget(attempt: CharacterAudioAttempt): Promise<void> {
    if (busyRef.current || attempt.target === null || !isCurrentRef.current() || !run.isCurrent()) return;
    setBusyState(true);
    const controller = new AbortController();
    operationControllerRef.current = controller;
    try {
      const directory = await run.readDirectory(controller.signal);
      if (!isCurrentRef.current() || !run.isCurrent()) return;
      const observed = matchingCharacter(directory, character);
      if (observed === null) {
        setCurrentCharacter(null);
        setCurrentAudioSource(null);
        setDirectoryStatus("missing");
        setDirectoryMessage("最新角色目录中没有唯一且属于本剧集的记录；音频维护已暂停。");
      } else {
        setCurrentCharacter(observed);
        setCurrentAudioSource(directory.audioSources.get(observed.id) ?? null);
        onDirectoryUpdatedRef.current(directory);
      }
      const matches = observed !== null && run.matchesTarget(observed, attempt.target);
      if (attempt.acknowledged) {
        updateAttempt(attempt.id, matches
          ? { state: "verified", message: "独立只读目录读取与已确认的音频、音色变更一致。" }
          : { state: "acknowledged-observed-different", message: "服务器已确认保存，但独立目录读到的内容不同；结果只作为观察记录。" });
      } else {
        updateAttempt(attempt.id, matches
          ? { state: "unknown-observed-match", message: "目录内容与本次目标一致，但原保存回执仍未知；观察结果不等于服务端确认。" }
          : { state: "unknown-observed-different", message: "只读目录未观察到本次目标；原保存回执仍未知，不会自动重放。" });
      }
    } catch (error) {
      if (!isCurrentRef.current()) return;
      reportUnauthorized(error);
      updateAttempt(attempt.id, {
        state: attempt.acknowledged ? "acknowledged-read-failed" : "unknown-read-failed",
        message: error instanceof Error ? error.message : "只读角色目录核对失败。",
      });
    } finally {
      operationControllerRef.current = null;
      setBusyState(false);
    }
  }

  async function saveChanges(): Promise<void> {
    const baseline = currentCharacter;
    const target = buildTarget();
    if (baseline === null || target === null || busyRef.current || !isCurrentRef.current() || !run.isCurrent()) return;
    const receipt = target.audio !== undefined && target.audio !== null ? target.audio : null;
    if (receipt !== null && !run.canSaveAudio(receipt)) return;

    const attempt: CharacterAudioAttempt = {
      id: allocateAttemptId(),
      runId,
      fileName: receipt === null ? "角色音频与音色" : "已上传音频与音色",
      receipt,
      target,
      acknowledged: false,
      state: "confirming",
      message: "正在重新读取完整角色目录；确认前不会发送目录更新。",
    };
    publishAttempts([...attemptsRef.current, attempt]);
    setBusyState(true);
    const controller = new AbortController();
    operationControllerRef.current = controller;
    let acknowledged = false;
    try {
      const freshDirectory = await run.readDirectory(controller.signal);
      if (!isCurrentRef.current() || !run.isCurrent()) return;
      const freshCharacter = matchingCharacter(freshDirectory, character);
      if (freshCharacter === null) {
        setCurrentCharacter(null);
        setCurrentAudioSource(null);
        setDirectoryStatus("missing");
        setDirectoryMessage("最新角色目录中没有唯一且属于本剧集的记录；音频维护已暂停。");
        updateAttempt(attempt.id, {
          state: "confirmation-stale",
          message: "确认前最新角色目录无法唯一核对当前角色；本次没有发送保存。",
        });
        return;
      }
      if (!sameVoiceAndAudio(freshCharacter, baseline)) {
        setCurrentCharacter(freshCharacter);
        setCurrentAudioSource(freshDirectory.audioSources.get(freshCharacter.id) ?? null);
        onDirectoryUpdatedRef.current(freshDirectory);
        updateAttempt(attempt.id, {
          state: "confirmation-stale",
          message: "确认前角色音频或音色已变化；本次没有发送保存。请重新读取并决定如何处理。",
        });
        return;
      }
      setCurrentCharacter(freshCharacter);
      setCurrentAudioSource(freshDirectory.audioSources.get(freshCharacter.id) ?? null);
      onDirectoryUpdatedRef.current(freshDirectory);
      updateAttempt(attempt.id, { state: "saving", message: "角色基线仍一致，正在发送一次仅含已改字段的保存。" });
      await run.save(target, controller.signal);
      if (!isCurrentRef.current() || !run.isCurrent()) return;
      acknowledged = true;
      updateAttempt(attempt.id, {
        acknowledged: true,
        state: "acknowledged",
        message: "服务器已确认保存，正在独立读取角色目录。",
      });
      const observedDirectory = await run.readDirectory(controller.signal);
      if (!isCurrentRef.current() || !run.isCurrent()) return;
      const observedCharacter = matchingCharacter(observedDirectory, character);
      if (observedCharacter === null) {
        setCurrentCharacter(null);
        setCurrentAudioSource(null);
        setDirectoryStatus("missing");
        setDirectoryMessage("服务器已确认保存，但最新角色目录中没有唯一且属于本剧集的记录。");
        updateAttempt(attempt.id, {
          acknowledged: true,
          state: "acknowledged-observed-different",
          message: "服务器已确认保存，但独立目录读取未找到唯一角色；请只读核对。",
        });
      } else {
        onDirectoryUpdatedRef.current(observedDirectory);
        setCurrentCharacter(observedCharacter);
        setCurrentAudioSource(observedDirectory.audioSources.get(observedCharacter.id) ?? null);
        setVoiceReference(observedCharacter.voice_ref ?? "");
        setPendingAudio(undefined);
        updateAttempt(attempt.id, run.matchesTarget(observedCharacter, target)
          ? { acknowledged: true, state: "verified", message: "服务器保存回执与独立角色目录读取一致。" }
          : {
            acknowledged: true,
            state: "acknowledged-observed-different",
            message: "服务器已确认保存，但独立目录读到的内容不同；请只读核对。",
          });
      }
    } catch (error) {
      if (!isCurrentRef.current()) return;
      reportUnauthorized(error);
      if (!acknowledged && error instanceof CharacterAudioError && error.kind === "read-error") {
        updateAttempt(attempt.id, {
          state: "save-not-sent",
          message: "确认前只读目录核对失败，角色更新未发送；重新读取后可再次明确保存。",
        });
      } else {
        updateAttempt(attempt.id, {
          acknowledged,
          state: acknowledged ? "acknowledged-read-failed" : attemptState(error, "save"),
          message: acknowledged
            ? "服务器已确认保存，但独立目录读取失败；可重新执行只读核对。"
            : error instanceof Error ? error.message : "保存结果尚未确认；不会自动重放。",
        });
      }
    } finally {
      operationControllerRef.current = null;
      setBusyState(false);
    }
  }

  function closeDialog(): void {
    operationControllerRef.current?.abort();
    onClose();
  }

  const focus = useSeriesDialogFocus({ active: true, canDismiss: true, onDismiss: closeDialog });
  const visibleCharacter = currentCharacter ?? character;
  const latestRunAttempt = attempts.at(-1) ?? null;
  const target = buildTarget();
  const canSave = directoryStatus === "ready" && !busy && target !== null
    && (latestRunAttempt === null || !isWaitingForReadRecovery(latestRunAttempt.state))
    && (target.audio === undefined || target.audio === null || run.canSaveAudio(target.audio));
  const previewSource = pendingAudio === null
    ? null
    : pendingAudio !== undefined && run.canReadAudio(pendingAudio)
      ? pendingAudio
      : pendingAudio === undefined ? currentAudioSource : null;
  const previewAttempt = previewSource === null ? null : attempts.find((attempt) => attempt.receipt === previewSource) ?? null;
  const previewKey = previewSource === currentAudioSource ? "saved-audio" : `uploaded-audio-${previewAttempt?.id ?? runId}`;

  return (
    <div className="character-audio-overlay" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget && !busyRef.current) focus.dismiss();
    }}>
      <section
        aria-labelledby="character-audio-heading"
        aria-modal="true"
        className="character-audio-dialog"
        ref={focus.dialogRef}
        role="dialog"
      >
        <header className="character-audio-header">
          <div>
            <p className="eyebrow">{seriesName} · 角色音频</p>
            <h2 id="character-audio-heading">音频与音色：{labelForCharacter(visibleCharacter)}</h2>
          </div>
          <button aria-label="关闭音频与音色" className="character-audio-close" onClick={focus.dismiss} type="button">×</button>
        </header>

        <div className="character-audio-body">
          <p className="character-audio-directory-state" role={directoryStatus === "failed" || directoryStatus === "missing" ? "alert" : "status"}>
            {directoryMessage}
          </p>
          {directoryStatus === "failed" && (
            <button className="secondary-button" disabled={busy} onClick={loadEntryDirectory} type="button">
              重新读取当前角色目录
            </button>
          )}

          <section aria-label="角色音频">
            <h3>角色音频</h3>
            {previewSource === null
              ? <p className="character-audio-muted">当前没有待试听的音频。</p>
              : <CharacterAudioPreview
                isCurrent={() => isCurrentRef.current() && run.isCurrent()}
                key={previewKey}
                label={`${labelForCharacter(visibleCharacter)}音频试听`}
                run={run}
                source={previewSource}
              />}
            {directoryStatus === "ready" && currentCharacter !== null && (
              <div className="character-audio-controls">
                <label className="character-audio-file-field">
                  <span>选择音频文件</span>
                  <input
                    accept="audio/mpeg,audio/wav,audio/x-wav,audio/wave,audio/vnd.wave,audio/ogg,audio/mp4,audio/x-m4a,audio/aac,audio/aacp,audio/x-aac,audio/flac,audio/x-flac,audio/x-ms-wma,audio/wma,.mp3,.wav,.ogg,.m4a,.aac,.flac,.wma"
                    disabled={busy}
                    onChange={handleFileChange}
                    type="file"
                  />
                </label>
                {selectedFile !== null && <p className="character-audio-muted">已选：{selectedFile.name}</p>}
                <div className="character-audio-button-row">
                  <button className="secondary-button" disabled={busy || selectedFile === null} onClick={() => void startUpload()} type="button">
                    上传音频
                  </button>
                  {pendingAudio === null ? (
                    <button className="secondary-button" disabled={busy || !hasPersistedAudio(currentCharacter)} onClick={() => setPendingAudio(undefined)} type="button">
                      撤销移除
                    </button>
                  ) : hasPersistedAudio(currentCharacter) ? (
                    <button className="secondary-button" disabled={busy} onClick={() => setPendingAudio(null)} type="button">
                      移除已保存音频
                    </button>
                  ) : pendingAudio !== undefined ? (
                    <button className="secondary-button" disabled={busy} onClick={() => setPendingAudio(undefined)} type="button">
                      清除待保存音频
                    </button>
                  ) : null}
                </div>
                <p className="character-audio-note">支持 MP3、WAV、OGG、M4A、AAC、FLAC、WMA；单文件最大 10 MiB。上传与保存分开确认，移除只更新角色字段，不删除已上传文件。</p>
              </div>
            )}
          </section>

          <section aria-label="声音参考">
            <h3>声音参考</h3>
            <label className="character-audio-voice-field">
              <span>音色描述</span>
              <textarea
                disabled={directoryStatus !== "ready" || busy}
                onChange={(event) => setVoiceReference(event.currentTarget.value)}
                rows={3}
                value={voiceReference}
              />
            </label>
            <p className="character-audio-note">记录角色声音的音高、音色和说话方式。</p>
          </section>

          {latestRunAttempt !== null && (
            <section aria-label="本次操作记录" className="character-audio-attempt">
              <h3>本次操作记录</h3>
              <p role={latestRunAttempt.state.includes("unknown")
                || latestRunAttempt.state.includes("rejected")
                || latestRunAttempt.state.includes("not-sent")
                || latestRunAttempt.state.includes("stale")
                || latestRunAttempt.state.endsWith("failed") ? "alert" : "status"}>
                {latestRunAttempt.message}
              </p>
              {shouldAllowReadRecovery(latestRunAttempt.state) && (
                <button className="secondary-button" disabled={busy} onClick={() => void observeTarget(latestRunAttempt)} type="button">
                  只读核对保存结果
                </button>
              )}
            </section>
          )}

          {attempts.length > 0 && (
            <section aria-label="音频操作历史" className="character-audio-history">
              <h3>音频操作历史</h3>
              <ol>
                {attempts.map((attempt) => (
                  <li key={attempt.id}>
                    <strong>{attempt.fileName}</strong>
                    <span>{attempt.message}</span>
                    {attempt.state === "upload-unknown" && <span>上传结果未知；不会自动重发，原上传文件也不会被删除。</span>}
                  </li>
                ))}
              </ol>
            </section>
          )}
        </div>

        <footer className="character-audio-footer">
          <button className="secondary-button" onClick={focus.dismiss} type="button">关闭</button>
          <button className="primary-button" disabled={!canSave} onClick={() => void saveChanges()} type="button">明确保存音频与音色</button>
        </footer>
      </section>
    </div>
  );
}
