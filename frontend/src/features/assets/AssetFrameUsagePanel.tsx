import { useEffect, useRef, useState } from "react";
import type { AssetLibraryType, Character, Chapter, Prop, Scene } from "../../shared/api/contracts";
import { ApiError } from "../../shared/api/errors";
import { normalizeApiError, type WorkspaceServices } from "../../shared/api/services";
import {
  projectAssetFrameUsage,
  type AssetFrameUsageProjection,
  type AssetFrameUsageText,
} from "./assetFrameUsage";
import { resolveAssetUsageFrameTarget } from "./assetUsageFrameTarget";

interface AssetFrameUsageBaseOwner {
  userId: string;
  services: WorkspaceServices;
  seriesId: string;
  openEpoch: number;
}

type AssetByCategory = {
  characters: Character;
  scenes: Scene;
  props: Prop;
};

type DirectoryByCategory = {
  [Category in AssetLibraryType]: AssetByCategory[Category][];
};

export type AssetFrameUsageOwnerFor<Category extends AssetLibraryType> = AssetFrameUsageBaseOwner & {
  category: Category;
  asset: AssetByCategory[Category];
  directorySnapshot: DirectoryByCategory[Category];
};

export type AssetFrameUsageOwner = {
  [Category in AssetLibraryType]: AssetFrameUsageOwnerFor<Category>;
}[AssetLibraryType];

export type AssetFrameUsageScope = AssetFrameUsageOwner;

export interface AssetFrameUsageChapterTarget {
  chapterId: string;
  seriesId: string;
  isCurrent(): boolean;
}

export interface AssetFrameUsageFrameTarget extends AssetFrameUsageChapterTarget {
  storyboardAssetId: string;
  category: AssetLibraryType;
  assetId: string;
}

export interface AssetFrameUsagePanelProps {
  owner: AssetFrameUsageOwner;
  currentScope: AssetFrameUsageScope;
  assetLabel: string;
  onClose(owner: AssetFrameUsageOwner): void;
  onNavigateToChapter?(target: AssetFrameUsageChapterTarget): void;
  onNavigateToFrame?(target: AssetFrameUsageFrameTarget): void;
  onUnauthorized(): void;
}

type ReadState =
  | { status: "loading" }
  | { status: "error"; error: ApiError }
  | {
      status: "ready";
      projection: AssetFrameUsageProjection;
      chapters: Chapter[];
      requestGeneration: number;
    };

function sameScope(owner: AssetFrameUsageOwner, scope: AssetFrameUsageScope): boolean {
  return owner.userId === scope.userId
    && owner.services === scope.services
    && owner.seriesId === scope.seriesId
    && owner.category === scope.category
    && owner.asset === scope.asset
    && owner.directorySnapshot === scope.directorySnapshot
    && owner.openEpoch === scope.openEpoch;
}

function hasValidOwnerIdentity(owner: AssetFrameUsageOwner): boolean {
  if (owner.asset.series_id !== owner.seriesId || owner.asset.id.trim() === "") {
    return false;
  }
  switch (owner.category) {
    case "characters":
      return owner.directorySnapshot.filter((candidate) => candidate.id === owner.asset.id).length === 1
        && owner.directorySnapshot.some((candidate) => candidate === owner.asset);
    case "scenes":
      return owner.directorySnapshot.filter((candidate) => candidate.id === owner.asset.id).length === 1
        && owner.directorySnapshot.some((candidate) => candidate === owner.asset);
    case "props":
      return owner.directorySnapshot.filter((candidate) => candidate.id === owner.asset.id).length === 1
        && owner.directorySnapshot.some((candidate) => candidate === owner.asset);
  }
}

function errorMessage(error: ApiError): string {
  if (error.status === 403) {
    const detail = error.detail?.toLowerCase() ?? "";
    if (["会员", "membership", "premium", "subscription"].some((word) => detail.includes(word))) {
      return "当前账号暂不可查看关联镜头。";
    }
    return "当前账号暂时无权读取关联镜头。";
  }
  if (error.status === 404) {
    return "当前剧集的章节列表暂不可用。";
  }
  if (error.status === 422 || error.kind === "invalid-response") {
    return "章节列表暂时无法识别，请重试读取。";
  }
  if (error.kind === "timeout") {
    return "读取超时，请重试。";
  }
  if (error.kind === "network") {
    return "无法连接本地服务，请确认服务正在运行。";
  }
  return "暂时无法读取关联镜头，请稍后重试。";
}

function textValue(text: AssetFrameUsageText, kind: "frame" | "original"): string {
  if (text.kind === "value") {
    return text.value;
  }
  if (text.kind === "missing") {
    return kind === "frame" ? "尚未填写镜头文字。" : "尚未填写原文。";
  }
  return kind === "frame" ? "镜头文字暂时无法识别。" : "原文暂时无法识别。";
}

function FrameNavigationAction({
  chapterPosition,
  chapterTitle,
  framePosition,
  createTarget,
  onNavigate,
}: {
  chapterPosition: number;
  chapterTitle: string;
  framePosition: number;
  createTarget(chapterPosition: number, framePosition: number): AssetFrameUsageFrameTarget | null;
  onNavigate(target: AssetFrameUsageFrameTarget): void;
}) {
  const target = createTarget(chapterPosition, framePosition);
  if (target === null) {
    return (
      <p className="asset-frame-usage-warning" role="note">
        镜头身份暂无法核对，不能直接定位。
      </p>
    );
  }
  return (
    <button
      className="asset-frame-usage-frame-link"
      onClick={() => {
        if (target.isCurrent()) {
          onNavigate(target);
        }
      }}
      type="button"
    >
      定位对应镜头：{chapterTitle} · 镜头{framePosition}
    </button>
  );
}

function UsageResults({
  projection,
  createTarget,
  createFrameTarget,
  onNavigateToChapter,
  onNavigateToFrame,
}: {
  projection: AssetFrameUsageProjection;
  createTarget(chapterPosition: number): AssetFrameUsageChapterTarget | null;
  createFrameTarget(chapterPosition: number, framePosition: number): AssetFrameUsageFrameTarget | null;
  onNavigateToChapter?: ((target: AssetFrameUsageChapterTarget) => void) | undefined;
  onNavigateToFrame?: ((target: AssetFrameUsageFrameTarget) => void) | undefined;
}) {
  if (projection.status === "unreadable") {
    return (
      <div className="asset-frame-usage-state is-warning" role="status">
        当前章节快照无法核对。请重新读取章节。
      </div>
    );
  }

  if (projection.status === "empty") {
    return (
      <div className="asset-frame-usage-state" role="status">
        <p>当前章节快照未找到关联镜头。</p>
        {projection.hasUncertainReferences && <p className="asset-frame-usage-warning">部分引用无法核对。</p>}
      </div>
    );
  }

  return (
    <div className="asset-frame-usage-results">
      <p className="asset-frame-usage-summary">
        当前章节快照中找到 {projection.frameCount} 个镜头，共 {projection.referenceCount} 次引用。
      </p>
      {projection.hasUncertainReferences && (
        <p className="asset-frame-usage-warning" role="note">部分引用无法核对，以下仅列出已确认的镜头。</p>
      )}
      <ol className="asset-frame-usage-chapters" aria-label="关联镜头章节列表">
        {projection.chapters.map((chapter) => (
          <li className="asset-frame-usage-chapter" key={chapter.position}>
            <h4>第 {chapter.position} 项 · {chapter.title}</h4>
            {onNavigateToChapter !== undefined && createTarget(chapter.position) !== null && (
              <button
                className="asset-frame-usage-chapter-link"
                onClick={() => {
                  const target = createTarget(chapter.position);
                  if (target?.isCurrent()) {
                    onNavigateToChapter(target);
                  }
                }}
                type="button"
              >
                查看对应章节：{chapter.title}
              </button>
            )}
            <ol className="asset-frame-usage-frames" aria-label={chapter.title + "的关联镜头"}>
              {chapter.frames.map((frame) => (
                <li className="asset-frame-usage-frame" key={frame.framePosition}>
                  <h5>
                    镜头 {frame.framePosition}
                    {frame.referenceCount > 1 && <span> · 引用 {frame.referenceCount} 次</span>}
                  </h5>
                  {onNavigateToFrame !== undefined && (
                    <FrameNavigationAction
                      chapterPosition={chapter.position}
                      chapterTitle={chapter.title}
                      createTarget={createFrameTarget}
                      framePosition={frame.framePosition}
                      onNavigate={onNavigateToFrame}
                    />
                  )}
                  <div className="asset-frame-usage-text-block">
                    <span>镜头文字</span>
                    <p>{textValue(frame.text, "frame")}</p>
                  </div>
                  <div className="asset-frame-usage-text-block">
                    <span>原文</span>
                    <p>{textValue(frame.originalText, "original")}</p>
                  </div>
                </li>
              ))}
            </ol>
          </li>
        ))}
      </ol>
    </div>
  );
}

export function AssetFrameUsagePanel({
  owner,
  currentScope,
  assetLabel,
  onClose,
  onNavigateToChapter,
  onNavigateToFrame,
  onUnauthorized,
}: AssetFrameUsagePanelProps) {
  const boundOwner = useRef(owner).current;
  const currentScopeRef = useRef(currentScope);
  currentScopeRef.current = currentScope;
  const unauthorizedHandlerRef = useRef(onUnauthorized);
  unauthorizedHandlerRef.current = onUnauthorized;
  const [scopeInvalidated, setScopeInvalidated] = useState(false);
  const scopeInvalidatedRef = useRef(false);
  const requestGeneration = useRef(0);
  const [reloadGeneration, setReloadGeneration] = useState(0);
  const [readState, setReadState] = useState<ReadState>({ status: "loading" });
  const readStateRef = useRef(readState);
  readStateRef.current = readState;

  if (
    !scopeInvalidatedRef.current
    && (owner !== boundOwner || !sameScope(boundOwner, currentScopeRef.current) || !hasValidOwnerIdentity(boundOwner))
  ) {
    scopeInvalidatedRef.current = true;
    setScopeInvalidated(true);
  }

  useEffect(() => {
    const generation = ++requestGeneration.current;
    const controller = new AbortController();
    let active = true;
    setReadState({ status: "loading" });

    const isCurrent = () => active
      && !controller.signal.aborted
      && !scopeInvalidatedRef.current
      && requestGeneration.current === generation
      && sameScope(boundOwner, currentScopeRef.current);

    // Deferring the call lets StrictMode's setup/cleanup probe cancel its first setup before any request starts.
    void Promise.resolve().then(() => {
      if (!isCurrent()) {
        return;
      }
      return boundOwner.services.listChapters(boundOwner.seriesId, controller.signal);
    }).then((chapters) => {
      if (!isCurrent() || chapters === undefined) {
        return;
      }
      setReadState({
        status: "ready",
        chapters,
        requestGeneration: generation,
        projection: projectAssetFrameUsage(
          chapters,
          boundOwner.seriesId,
          boundOwner.category,
          boundOwner.asset,
        ),
      });
    }).catch((cause: unknown) => {
      if (!isCurrent()) {
        return;
      }
      const error = normalizeApiError(cause);
      if (error.status === 401) {
        unauthorizedHandlerRef.current();
        return;
      }
      setReadState({ status: "error", error });
    });

    return () => {
      active = false;
      controller.abort();
      if (requestGeneration.current === generation) {
        requestGeneration.current += 1;
      }
    };
  }, [boundOwner, reloadGeneration]);

  function close() {
    if (scopeInvalidatedRef.current) {
      return;
    }
    scopeInvalidatedRef.current = true;
    requestGeneration.current += 1;
    setScopeInvalidated(true);
    onClose(boundOwner);
  }

  function reread() {
    if (scopeInvalidatedRef.current) {
      return;
    }
    requestGeneration.current += 1;
    setReadState({ status: "loading" });
    setReloadGeneration((generation) => generation + 1);
  }

  function createTarget(chapterPosition: number): AssetFrameUsageChapterTarget | null {
    if (readState.status !== "ready" || !Number.isSafeInteger(chapterPosition) || chapterPosition < 1) {
      return null;
    }
    const chapter = readState.chapters[chapterPosition - 1];
    if (
      chapter === undefined
      || typeof chapter.id !== "string"
      || chapter.id.trim() === ""
      || chapter.series_id !== boundOwner.seriesId
      || readState.chapters.filter((candidate) => candidate.id === chapter.id).length !== 1
    ) {
      return null;
    }

    const readyState = readState;
    const readyGeneration = readState.requestGeneration;
    return {
      chapterId: chapter.id,
      seriesId: chapter.series_id,
      isCurrent: () => {
        const activeState = readStateRef.current;
        return !scopeInvalidatedRef.current
          && requestGeneration.current === readyGeneration
          && activeState === readyState
          && activeState.status === "ready"
          && activeState.requestGeneration === readyGeneration
          && sameScope(boundOwner, currentScopeRef.current)
          && hasValidOwnerIdentity(boundOwner);
      },
    };
  }

  function createFrameTarget(
    chapterPosition: number,
    framePosition: number,
  ): AssetFrameUsageFrameTarget | null {
    if (readState.status !== "ready") {
      return null;
    }
    const identity = resolveAssetUsageFrameTarget(
      readState.chapters,
      boundOwner.seriesId,
      chapterPosition,
      framePosition,
      boundOwner.category,
      boundOwner.asset.id,
    );
    if (identity === null) {
      return null;
    }

    const readyState = readState;
    const readyGeneration = readState.requestGeneration;
    return {
      chapterId: identity.chapterId,
      seriesId: identity.seriesId,
      storyboardAssetId: identity.storyboardAssetId,
      category: identity.category,
      assetId: identity.assetId,
      isCurrent: () => {
        const activeState = readStateRef.current;
        return !scopeInvalidatedRef.current
          && requestGeneration.current === readyGeneration
          && activeState === readyState
          && activeState.status === "ready"
          && activeState.requestGeneration === readyGeneration
          && sameScope(boundOwner, currentScopeRef.current)
          && hasValidOwnerIdentity(boundOwner);
      },
    };
  }

  if (scopeInvalidated || scopeInvalidatedRef.current || !sameScope(boundOwner, currentScopeRef.current)) {
    return null;
  }

  return (
    <section className="asset-frame-usage-panel" aria-label="关联镜头">
      <header className="asset-frame-usage-heading">
        <div>
          <p className="eyebrow">当前剧集 · 只读镜头</p>
          <h3>关联镜头</h3>
          <p>{assetLabel}的当前章节快照</p>
        </div>
        <div className="asset-frame-usage-actions">
          <button className="text-button" onClick={reread} type="button">重新读取章节</button>
          <button className="text-button" onClick={close} type="button">关闭</button>
        </div>
      </header>

      {readState.status === "loading" && (
        <p className="asset-frame-usage-state" role="status" aria-live="polite">
          正在读取当前剧集章节…
        </p>
      )}
      {readState.status === "error" && (
        <div className="state-panel error-panel request asset-frame-usage-error" role="alert">
          <div className="state-icon" aria-hidden="true">!</div>
          <div className="state-copy">
            <h4>暂时无法读取关联镜头</h4>
            <p>{errorMessage(readState.error)}</p>
          </div>
          <button className="secondary-button" onClick={reread} type="button">重试读取</button>
        </div>
      )}
      {readState.status === "ready" && (
        <UsageResults
          createTarget={createTarget}
          createFrameTarget={createFrameTarget}
          onNavigateToChapter={onNavigateToChapter}
          onNavigateToFrame={onNavigateToFrame}
          projection={readState.projection}
        />
      )}
    </section>
  );
}
