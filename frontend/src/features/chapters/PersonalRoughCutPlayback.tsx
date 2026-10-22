import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "../../shared/api/errors";
import {
  isSafeRoughCutMediaReference,
} from "../../shared/api/roughCutMedia";
import type {
  PersonalRoughCutPlaybackReader,
  WorkspaceServices,
} from "../../shared/api/services";
import type {
  PersonalRoughCutFrame,
  PersonalRoughCutSnapshot,
} from "../../shared/api/personalRoughCut";
import "./personalRoughCutPlayback.css";

export interface PersonalRoughCutPlaybackQueue {
  frames: PersonalRoughCutFrame[];
  excludedCount: number;
  pendingCount: number;
  missingCount: number;
  unsupportedCount: number;
  duplicateCount: number;
  invalidIdentityCount: number;
}

export function buildPersonalRoughCutPlaybackQueue(
  snapshot: PersonalRoughCutSnapshot,
  apiBaseUrl: string | null,
): PersonalRoughCutPlaybackQueue {
  const idCounts = new Map<string, number>();
  for (const frame of snapshot.frames) {
    if (frame.asset_id.length > 0) {
      idCounts.set(frame.asset_id, (idCounts.get(frame.asset_id) ?? 0) + 1);
    }
  }

  const queue: PersonalRoughCutFrame[] = [];
  let excludedCount = 0;
  let pendingCount = 0;
  let missingCount = 0;
  let unsupportedCount = 0;
  let duplicateCount = 0;
  let invalidIdentityCount = 0;

  if (!snapshot.saved) {
    return {
      frames: queue,
      excludedCount,
      pendingCount,
      missingCount,
      unsupportedCount,
      duplicateCount,
      invalidIdentityCount,
    };
  }

  for (const frame of snapshot.frames) {
    if (!frame.included) {
      excludedCount += 1;
    }
    if (frame.pending) {
      pendingCount += 1;
    }
    if (!frame.included || frame.pending) {
      continue;
    }

    if (frame.asset_id.length === 0 || frame.asset_id.trim() !== frame.asset_id) {
      invalidIdentityCount += 1;
      continue;
    }
    if ((idCounts.get(frame.asset_id) ?? 0) !== 1) {
      duplicateCount += 1;
      continue;
    }
    if (frame.missing_reason !== null || frame.preview_url === null) {
      missingCount += 1;
      continue;
    }
    if (apiBaseUrl === null || !isSafeRoughCutMediaReference(frame.preview_url, apiBaseUrl)) {
      unsupportedCount += 1;
      continue;
    }
    queue.push(frame);
  }

  return {
    frames: queue,
    excludedCount,
    pendingCount,
    missingCount,
    unsupportedCount,
    duplicateCount,
    invalidIdentityCount,
  };
}

interface PlaybackState {
  status: "idle" | "loading" | "ready" | "playing" | "paused" | "error" | "ended";
  index: number | null;
  attempt: number | null;
  message: string | null;
  continueRequired: boolean;
  currentTime: number;
  duration: number;
}

interface PlaybackAttemptContext {
  attempt: number;
  controller: AbortController;
  reader: PersonalRoughCutPlaybackReader;
  snapshot: PersonalRoughCutSnapshot;
  requestGeneration: number;
  index: number;
  mediaElement: HTMLVideoElement | null;
  objectUrl: string | null;
  sourceInstalled: boolean;
}

interface PersonalRoughCutPlaybackProps {
  chapterId: string;
  userId: string;
  services: WorkspaceServices;
  snapshot: PersonalRoughCutSnapshot;
  requestGeneration: number;
  reader: PersonalRoughCutPlaybackReader | null;
  enabled: boolean;
  onUnauthorized(): void;
  registerStop(stop: (() => void) | null): void;
}

const IDLE_STATE: PlaybackState = {
  status: "idle",
  index: null,
  attempt: null,
  message: null,
  continueRequired: false,
  currentTime: 0,
  duration: 0,
};

function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) {
    return "0:00";
  }
  const wholeSeconds = Math.floor(seconds);
  const minutes = Math.floor(wholeSeconds / 60);
  return `${minutes}:${String(wholeSeconds % 60).padStart(2, "0")}`;
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.message;
  }
  return "无法读取或播放此镜头的视频，请重试或跳过此镜。";
}

export function PersonalRoughCutPlayback({
  chapterId,
  userId,
  services,
  snapshot,
  requestGeneration,
  reader,
  enabled,
  onUnauthorized,
  registerStop,
}: PersonalRoughCutPlaybackProps) {
  const queue = useMemo(
    () => buildPersonalRoughCutPlaybackQueue(snapshot, services.apiBaseUrl),
    [snapshot, services.apiBaseUrl],
  );
  const propsRef = useRef({
    chapterId,
    userId,
    services,
    snapshot,
    requestGeneration,
    reader,
    enabled,
    onUnauthorized,
    queue,
  });
  propsRef.current = {
    chapterId,
    userId,
    services,
    snapshot,
    requestGeneration,
    reader,
    enabled,
    onUnauthorized,
    queue,
  };

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const objectUrlRef = useRef<string | null>(null);
  const attemptRef = useRef(0);
  const attemptContextRef = useRef<PlaybackAttemptContext | null>(null);
  const endedAttemptRef = useRef<number | null>(null);
  const mountedRef = useRef(true);
  const [playback, setPlayback] = useState<PlaybackState>(IDLE_STATE);

  const releaseActiveMedia = useCallback((context = attemptContextRef.current) => {
    const video = context?.mediaElement ?? videoRef.current;
    if (video !== null) {
      video.pause();
      video.removeAttribute("src");
      video.load();
    }

    const controller = controllerRef.current;
    controllerRef.current = null;
    controller?.abort();

    const objectUrl = context?.objectUrl ?? objectUrlRef.current;
    objectUrlRef.current = null;
    if (objectUrl !== null) {
      URL.revokeObjectURL(objectUrl);
    }
  }, []);

  const invalidateAttempt = useCallback(() => {
    attemptRef.current += 1;
    const context = attemptContextRef.current;
    attemptContextRef.current = null;
    endedAttemptRef.current = null;
    releaseActiveMedia(context);
  }, [releaseActiveMedia]);

  const stopPlayback = useCallback(() => {
    invalidateAttempt();
    if (mountedRef.current) {
      setPlayback(IDLE_STATE);
    }
  }, [invalidateAttempt]);

  useEffect(() => {
    mountedRef.current = true;
    registerStop(stopPlayback);
    return () => {
      registerStop(null);
      mountedRef.current = false;
      invalidateAttempt();
    };
  }, [invalidateAttempt, registerStop, stopPlayback]);

  useEffect(() => {
    setPlayback(IDLE_STATE);
    return () => invalidateAttempt();
  }, [chapterId, invalidateAttempt, reader, requestGeneration, services, snapshot, userId]);

  useEffect(() => {
    if (!enabled) {
      stopPlayback();
    }
  }, [enabled, stopPlayback]);

  const attachVideo = useCallback((video: HTMLVideoElement | null) => {
    videoRef.current = video;
    if (video !== null) {
      const context = attemptContextRef.current;
      if (context !== null && context.attempt === attemptRef.current) {
        context.mediaElement = video;
      }
    }
  }, []);

  function isCurrentAttempt(
    attempt: number,
    controller: AbortController,
    readerAtStart: PersonalRoughCutPlaybackReader,
    snapshotAtStart: PersonalRoughCutSnapshot,
    generationAtStart: number,
  ): boolean {
    const latest = propsRef.current;
    const context = attemptContextRef.current;
    return mountedRef.current
      && attemptRef.current === attempt
      && context?.attempt === attempt
      && context.controller === controller
      && context.reader === readerAtStart
      && context.snapshot === snapshotAtStart
      && context.requestGeneration === generationAtStart
      && !controller.signal.aborted
      && latest.enabled
      && latest.reader === readerAtStart
      && readerAtStart.isCurrent()
      && latest.snapshot === snapshotAtStart
      && latest.requestGeneration === generationAtStart;
  }

  function ownsMediaAttempt(attempt: number | null, video: HTMLVideoElement): boolean {
    const context = attemptContextRef.current;
    return attempt !== null
      && context !== null
      && context.attempt === attempt
      && context.mediaElement === video
      && context.sourceInstalled
      && context.objectUrl !== null;
  }

  function stopForStaleSession(message: string) {
    invalidateAttempt();
    if (mountedRef.current) {
      setPlayback({ ...IDLE_STATE, status: "error", message });
    }
  }

  async function playCurrent(
    attempt: number,
    controller: AbortController,
    readerAtStart: PersonalRoughCutPlaybackReader,
    snapshotAtStart: PersonalRoughCutSnapshot,
    generationAtStart: number,
  ) {
    const context = attemptContextRef.current;
    const video = context?.mediaElement ?? null;
    if (
      video === null
      || context?.sourceInstalled !== true
      || !isCurrentAttempt(attempt, controller, readerAtStart, snapshotAtStart, generationAtStart)
    ) {
      return;
    }
    try {
      await video.play();
      if (isCurrentAttempt(attempt, controller, readerAtStart, snapshotAtStart, generationAtStart)) {
        setPlayback((current) => current.attempt === attempt
          ? { ...current, status: "playing", continueRequired: false, message: null }
          : current);
      }
    } catch {
      if (isCurrentAttempt(attempt, controller, readerAtStart, snapshotAtStart, generationAtStart)) {
        setPlayback((current) => current.attempt === attempt
          ? {
              ...current,
              status: "paused",
              message: "浏览器尚未允许自动播放，请点击“继续播放”。",
              continueRequired: true,
            }
          : current);
      }
    }
  }

  async function startAt(index: number) {
    const latest = propsRef.current;
    const frame = latest.queue.frames[index];
    const currentReader = latest.reader;
    if (
      !latest.enabled
      || !latest.snapshot.saved
      || frame?.preview_url === null
      || frame === undefined
      || currentReader === null
    ) {
      return;
    }
    if (!currentReader.isCurrent()) {
      stopForStaleSession("登录状态已变化，试看已停止。请重新读取粗剪后再试。" );
      return;
    }

    invalidateAttempt();
    const attempt = attemptRef.current;
    const controller = new AbortController();
    controllerRef.current = controller;
    const snapshotAtStart = latest.snapshot;
    const generationAtStart = latest.requestGeneration;
    attemptContextRef.current = {
      attempt,
      controller,
      reader: currentReader,
      snapshot: snapshotAtStart,
      requestGeneration: generationAtStart,
      index,
      mediaElement: null,
      objectUrl: null,
      sourceInstalled: false,
    };
    setPlayback({
      status: "loading",
      index,
      attempt,
      message: "正在读取当前镜头的视频…",
      continueRequired: false,
      currentTime: 0,
      duration: 0,
    });

    try {
      const blob = await currentReader.read(frame.preview_url, controller.signal);
      if (!isCurrentAttempt(attempt, controller, currentReader, snapshotAtStart, generationAtStart)) {
        if (mountedRef.current && attemptRef.current === attempt && !controller.signal.aborted && !currentReader.isCurrent()) {
          stopForStaleSession("登录状态已变化，试看已停止。请重新读取粗剪后再试。");
        }
        return;
      }

      const activeContext = attemptContextRef.current;
      const video = activeContext?.mediaElement ?? null;
      if (activeContext === null || video === null) {
        return;
      }
      const objectUrl = URL.createObjectURL(blob);
      if (!isCurrentAttempt(attempt, controller, currentReader, snapshotAtStart, generationAtStart)) {
        URL.revokeObjectURL(objectUrl);
        return;
      }
      activeContext.objectUrl = objectUrl;
      objectUrlRef.current = objectUrl;
      video.src = objectUrl;
      activeContext.sourceInstalled = true;
      video.load();
      setPlayback({
        status: "ready",
        index,
        attempt,
        message: null,
        continueRequired: false,
        currentTime: 0,
        duration: 0,
      });
      await playCurrent(attempt, controller, currentReader, snapshotAtStart, generationAtStart);
    } catch (error) {
      if (!isCurrentAttempt(attempt, controller, currentReader, snapshotAtStart, generationAtStart)) {
        if (mountedRef.current && attemptRef.current === attempt && !controller.signal.aborted && !currentReader.isCurrent()) {
          stopForStaleSession("登录状态已变化，试看已停止。请重新读取粗剪后再试。");
        }
        return;
      }
      const message = errorMessage(error);
      if (error instanceof ApiError && error.status === 401 && currentReader.isCurrent()) {
        propsRef.current.onUnauthorized();
      }
      invalidateAttempt();
      setPlayback({
        status: "error",
        index,
        attempt: null,
        message,
        continueRequired: false,
        currentTime: 0,
        duration: 0,
      });
    }
  }

  function retryCurrent() {
    const index = playback.index;
    if (index !== null) {
      void startAt(index);
    }
  }

  function skipCurrent() {
    const index = playback.index;
    if (index === null) {
      return;
    }
    const nextIndex = index + 1;
    if (nextIndex < propsRef.current.queue.frames.length) {
      void startAt(nextIndex);
      return;
    }
    invalidateAttempt();
    setPlayback({ ...IDLE_STATE, status: "ended", message: "已跳过最后一镜，试看结束。" });
  }

  function handleEnded(attempt: number | null, video: HTMLVideoElement) {
    const context = attemptContextRef.current;
    if (
      attempt === null
      || context === null
      || attempt !== context.attempt
      || attempt !== attemptRef.current
      || !ownsMediaAttempt(attempt, video)
    ) {
      return;
    }
    if (!isCurrentAttempt(
      context.attempt,
      context.controller,
      context.reader,
      context.snapshot,
      context.requestGeneration,
    )) {
      if (!context.reader.isCurrent()) {
        stopForStaleSession("登录状态已变化，试看已停止。请重新读取粗剪后再试。");
      } else {
        invalidateAttempt();
        setPlayback(IDLE_STATE);
      }
      return;
    }
    if (!context.reader.isCurrent()) {
      stopForStaleSession("登录状态已变化，试看已停止。请重新读取粗剪后再试。");
      return;
    }
    if (endedAttemptRef.current === attempt) {
      return;
    }
    endedAttemptRef.current = attempt;
    const index = context.index;
    if (index + 1 < propsRef.current.queue.frames.length) {
      void startAt(index + 1);
      return;
    }
    setPlayback((current) => current.attempt === attempt
      ? { ...current, status: "ended", message: "已到达保存粗剪的最后一镜。", continueRequired: false }
      : current);
  }

  function continuePlayback() {
    const current = playback;
    const context = attemptContextRef.current;
    if (current.attempt === null || context === null || current.attempt !== context.attempt) {
      return;
    }
    if (!context.reader.isCurrent()) {
      stopForStaleSession("登录状态已变化，试看已停止。请重新读取粗剪后再试。");
      return;
    }
    void playCurrent(
      context.attempt,
      context.controller,
      context.reader,
      context.snapshot,
      context.requestGeneration,
    );
  }

  const playbackAvailable = snapshot.saved && reader !== null && queue.frames.length > 0;
  const currentFrame = playback.index === null ? null : queue.frames[playback.index] ?? null;

  return (
    <section className="personal-rough-cut-playback" aria-label="粗剪连续试看">
      <div className="personal-rough-cut-playback-heading">
        <div>
          <h4>保存顺序连续试看</h4>
          <p>仅使用已保存草稿的纳入视频和本机已鉴权读取；过滤器与未保存编辑不会改变播放顺序。</p>
        </div>
        <button
          className="secondary-button"
          disabled={!playbackAvailable || !enabled}
          onClick={() => { void startAt(0); }}
          type="button"
        >
          从头试看
        </button>
      </div>

      {!snapshot.saved ? (
        <p className="personal-rough-cut-playback-note" role="status">
          当前是未保存的初始投影。先保存编排，再按服务端确认的顺序试看。
        </p>
      ) : reader === null ? (
        <p className="personal-rough-cut-playback-note" role="status">
          当前服务未提供已鉴权的视频读取，无法开始试看。
        </p>
      ) : !enabled ? (
        <p className="personal-rough-cut-playback-note" role="status">
          编辑或保存期间，试看已停止。
        </p>
      ) : queue.frames.length === 0 ? (
        <p className="personal-rough-cut-playback-note" role="status">
          当前保存草稿中没有身份有效、纳入且可安全读取的视频。
        </p>
      ) : null}

      <p className="personal-rough-cut-playback-counts" aria-live="polite">
        可试看 {queue.frames.length} 项 · 已排除 {queue.excludedCount} · 待安排 {queue.pendingCount}
        {` · 缺片 ${queue.missingCount} · 不支持引用 ${queue.unsupportedCount}`}
        {` · 重复素材 ${queue.duplicateCount} · 无效身份 ${queue.invalidIdentityCount}`}
      </p>

      {playbackAvailable && (
        <>
          <div className="personal-rough-cut-playback-media">
            <video
              aria-label="当前镜头视频预览"
              controls
              key={playback.attempt ?? "idle"}
              playsInline
              preload="metadata"
              ref={attachVideo}
              onDurationChange={(event) => {
                if (!ownsMediaAttempt(playback.attempt, event.currentTarget)) {
                  return;
                }
                setPlayback((current) => current.attempt === playback.attempt
                  ? { ...current, duration: event.currentTarget.duration }
                  : current);
              }}
              onEnded={(event) => handleEnded(playback.attempt, event.currentTarget)}
              onError={(event) => {
                if (!ownsMediaAttempt(playback.attempt, event.currentTarget)) {
                  return;
                }
                if (!attemptContextRef.current?.reader.isCurrent()) {
                  stopForStaleSession("登录状态已变化，试看已停止。请重新读取粗剪后再试。");
                  return;
                }
                const index = playback.index;
                if (index === null) {
                  return;
                }
                invalidateAttempt();
                setPlayback({
                  status: "error",
                  index,
                  attempt: null,
                  message: "浏览器无法解码此镜头的视频，请重试或跳过此镜。",
                  continueRequired: false,
                  currentTime: 0,
                  duration: 0,
                });
              }}
              onPause={(event) => {
                if (ownsMediaAttempt(playback.attempt, event.currentTarget)) {
                  const context = attemptContextRef.current;
                  if (context !== null && !context.reader.isCurrent()) {
                    stopForStaleSession("登录状态已变化，试看已停止。请重新读取粗剪后再试。");
                    return;
                  }
                  setPlayback((current) => current.attempt === playback.attempt && current.status !== "error"
                    ? { ...current, status: "paused" }
                    : current);
                }
              }}
              onPlay={(event) => {
                if (!ownsMediaAttempt(playback.attempt, event.currentTarget)) {
                  return;
                }
                const context = attemptContextRef.current;
                if (playback.attempt !== null && context?.attempt === playback.attempt) {
                  if (!isCurrentAttempt(
                    context.attempt,
                    context.controller,
                    context.reader,
                    context.snapshot,
                    context.requestGeneration,
                  )) {
                    if (!context.reader.isCurrent()) {
                      stopForStaleSession("登录状态已变化，试看已停止。请重新读取粗剪后再试。");
                    }
                    return;
                  }
                  if (!context.reader.isCurrent()) {
                    stopForStaleSession("登录状态已变化，试看已停止。请重新读取粗剪后再试。");
                    return;
                  }
                  setPlayback((current) => current.attempt === playback.attempt
                    ? { ...current, status: "playing" }
                    : current);
                }
              }}
              onTimeUpdate={(event) => {
                if (!ownsMediaAttempt(playback.attempt, event.currentTarget)) {
                  return;
                }
                const currentReader = propsRef.current.reader;
                if (currentReader !== null && !currentReader.isCurrent()) {
                  stopForStaleSession("登录状态已变化，试看已停止。请重新读取粗剪后再试。");
                  return;
                }
                setPlayback((current) => current.attempt === playback.attempt
                  ? { ...current, currentTime: event.currentTarget.currentTime }
                  : current);
              }}
            />
            <p className="personal-rough-cut-playback-progress" aria-live="polite">
              {currentFrame === null
                ? "尚未选择镜头"
                : `当前第 ${playback.index! + 1}/${queue.frames.length} 项 · 章节第 ${currentFrame.frame_index + 1} 镜 · ${formatTime(playback.currentTime)} / ${formatTime(playback.duration)}`}
            </p>
          </div>

          {playback.message !== null && (
            <p
              className={playback.status === "error" ? "personal-rough-cut-playback-error" : "personal-rough-cut-playback-note"}
              role={playback.status === "error" ? "alert" : "status"}
              aria-live="polite"
            >
              {playback.message}
            </p>
          )}

          {playback.status === "loading" && <p role="status">正在读取视频…</p>}
          {playback.status === "paused" && playback.continueRequired && (
            <button className="secondary-button" onClick={continuePlayback} type="button">
              继续播放
            </button>
          )}
          {playback.status === "error" && (
            <div className="personal-rough-cut-playback-actions">
              <button className="secondary-button" onClick={retryCurrent} type="button">重试此镜</button>
              <button className="text-button" onClick={skipCurrent} type="button">跳过并继续</button>
            </div>
          )}
          {playback.status !== "idle" && playback.status !== "error" && playback.status !== "ended" && (
            <button className="text-button" onClick={stopPlayback} type="button">停止试看</button>
          )}

          <ol className="personal-rough-cut-playback-queue" aria-label="保存顺序播放队列">
            {queue.frames.map((frame, index) => (
              <li key={`${frame.asset_id}-${index}`}>
                <span>第 {index + 1} 项 · 章节第 {frame.frame_index + 1} 镜</span>
                <button
                  className="text-button"
                  disabled={!enabled}
                  onClick={() => { void startAt(index); }}
                  type="button"
                >
                  从此镜试看
                </button>
              </li>
            ))}
          </ol>
        </>
      )}
    </section>
  );
}
