import { useCallback, useEffect, useRef, useState } from "react";
import type { Chapter } from "../../shared/api/contracts";
import { ApiError, isAbortError } from "../../shared/api/errors";
import type { WorkspaceServices } from "../../shared/api/services";
import type { FrameAssetReferenceAssetTarget } from "./FrameAssetReferencesPanel";
import {
  chapterProductionChecklistFilename,
  collectChapterProductionChecklist,
  formatChapterProductionChecklistText,
  type ChapterChecklistDirectories,
  type ChapterChecklistFrameTarget,
  type ChapterChecklistItem,
  type ChapterChecklistResult,
} from "./chapterProductionChecklist";
import "./chapterProductionChecklist.css";

export interface ChapterProductionChecklistOwner {
  userId: string;
  seriesId: string;
  services: WorkspaceServices;
  chapter: Chapter;
}

export interface ChapterProductionChecklistFrameTarget {
  storyboardAssetId: string;
  isCurrent(): boolean;
}

interface ChapterProductionChecklistPanelProps {
  owner: ChapterProductionChecklistOwner;
  isCurrent(): boolean;
  onClose(): void;
  onNavigateToAsset?(target: FrameAssetReferenceAssetTarget): void;
  onLocateFrame?(target: ChapterProductionChecklistFrameTarget): boolean;
  onUnauthorized(): void;
}

type PanelState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; result: ChapterChecklistResult };

function itemDescription(item: ChapterChecklistItem): string {
  if (item.kind === "asset") {
    const labels = {
      character: "角色",
      scene: "场景",
      prop: "道具",
      storyboard: "故事板素材",
      fused: "融合素材",
    } as const;
    const reasons = {
      "missing-reference": "引用未在当前已读取资产中找到",
      "ambiguous-reference": "资产标识不唯一",
      "missing-image": "缺少图片",
    } as const;
    const name = item.name.replace(/[\u0000-\u001F\u007F-\u009F]/g, " ").replace(/\s+/g, " ").trim();
    const frameLabels = [...new Set(item.frames.map((frame) => frame.frameIndex + 1))]
      .filter((position) => Number.isSafeInteger(position) && position > 0)
      .map((position) => `分镜 ${position}`);
    const related = frameLabels.length > 0 ? `（关联：${frameLabels.join("、")}）` : "";
    return `${labels[item.assetType]}「${name || "未命名素材"}」：${reasons[item.status]}${related}`;
  }
  if (item.kind === "preview" && item.status === "missing") return `分镜 ${item.frame.frameIndex + 1}：预览未配置`;
  if (item.kind === "preview") return `分镜 ${item.frame.frameIndex + 1}：预览链接已配置，但媒体类型未识别`;
  return `分镜 ${item.frame.frameIndex + 1}：尚未配置可识别的视频`;
}

function navigationFrame(item: ChapterChecklistItem): ChapterChecklistFrameTarget | null {
  if (item.kind === "asset") {
    const navigation = item.navigation;
    if (navigation?.kind === "frame") {
      return item.frames.find((frame) => frame.navigable
        && frame.storyboardAssetId === navigation.storyboardAssetId) ?? null;
    }
    return item.frames.find((frame) => frame.navigable && frame.storyboardAssetId !== null) ?? null;
  }
  return item.frame.navigable ? item.frame : null;
}

export function ChapterProductionChecklistPanel({
  owner,
  isCurrent,
  onClose,
  onNavigateToAsset,
  onLocateFrame,
  onUnauthorized,
}: ChapterProductionChecklistPanelProps) {
  const [state, setState] = useState<PanelState>({ status: "loading" });
  const [feedback, setFeedback] = useState<string | null>(null);
  const [manualCopyText, setManualCopyText] = useState("");
  const mounted = useRef(false);
  const stateRef = useRef(state);
  stateRef.current = state;
  const isCurrentRef = useRef(isCurrent);
  isCurrentRef.current = isCurrent;
  const unauthorizedRef = useRef(onUnauthorized);
  unauthorizedRef.current = onUnauthorized;
  const readGeneration = useRef(0);
  const readController = useRef<AbortController | null>(null);
  const outputCleanup = useRef<(() => void) | null>(null);

  const readDirectories = useCallback(async () => {
    readController.current?.abort();
    const controller = new AbortController();
    readController.current = controller;
    const generation = ++readGeneration.current;
    const isOperationCurrent = () => mounted.current
      && !controller.signal.aborted
      && readGeneration.current === generation
      && isCurrentRef.current();
    if (!isOperationCurrent()) return;
    setState({ status: "loading" });
    setFeedback(null);
    setManualCopyText("");

    try {
      const { services, seriesId, chapter } = owner;
      if (services.listFusedAssets === undefined) {
        throw new ApiError("invalid-response", "当前运行模式未接入融合素材目录。", null);
      }
      const [characters, scenes, props, storyboard, fused] = await Promise.all([
        services.listCharacters(seriesId, controller.signal),
        services.listScenes(seriesId, controller.signal),
        services.listProps(seriesId, controller.signal),
        services.listStoryboardAssets(seriesId, chapter.id, controller.signal),
        services.listFusedAssets(seriesId, controller.signal),
      ]);
      if (!isOperationCurrent()) return;
      const directories: ChapterChecklistDirectories = { characters, scenes, props, storyboard, fused };
      setState({ status: "ready", result: collectChapterProductionChecklist(chapter, directories) });
    } catch (error) {
      if (!isOperationCurrent() || isAbortError(error)) return;
      if (error instanceof ApiError && error.status === 401) {
        unauthorizedRef.current();
      }
      setState({
        status: "error",
        message: error instanceof ApiError && error.status === 403
          ? "当前账号无权读取完整资产目录，缺项暂不可确认。"
          : "资产目录读取未完成，缺项暂不可确认。请重试读取。",
      });
    }
  }, [owner]);

  useEffect(() => {
    mounted.current = true;
    void readDirectories();
    return () => {
      mounted.current = false;
      readGeneration.current += 1;
      readController.current?.abort();
      readController.current = null;
      outputCleanup.current?.();
      outputCleanup.current = null;
    };
  }, [readDirectories]);

  const closeOutput = useCallback(() => {
    outputCleanup.current?.();
    outputCleanup.current = null;
  }, []);

  const currentReadyState = state.status === "ready" ? state : null;
  function targetIsCurrent(result: ChapterChecklistResult): boolean {
    return mounted.current && isCurrentRef.current()
      && stateRef.current.status === "ready"
      && stateRef.current.result === result;
  }

  function navigate(item: ChapterChecklistItem, result: ChapterChecklistResult) {
    if (!targetIsCurrent(result)) return;
    if (item.kind === "asset") {
      const navigation = item.navigation;
      if (navigation?.kind === "asset" && onNavigateToAsset !== undefined) {
        const target: FrameAssetReferenceAssetTarget = {
          seriesId: owner.seriesId,
          category: navigation.category,
          assetId: navigation.assetId,
          isCurrent: () => targetIsCurrent(result),
        };
        if (target.isCurrent()) onNavigateToAsset(target);
        return;
      }
    }
    const frame = navigationFrame(item);
    if (frame === null || frame === undefined || frame.storyboardAssetId === null || onLocateFrame === undefined) return;
    const target: ChapterProductionChecklistFrameTarget = {
      storyboardAssetId: frame.storyboardAssetId,
      isCurrent: () => targetIsCurrent(result),
    };
    if (target.isCurrent() && onLocateFrame(target)) {
      setFeedback(`已定位到关联分镜 ${frame.frameIndex + 1}。`);
    }
  }

  async function copyChecklist(result: ChapterChecklistResult) {
    if (!targetIsCurrent(result)) return;
    const text = formatChapterProductionChecklistText(result);
    if (text === null) return;
    try {
      if (typeof navigator === "undefined" || navigator.clipboard?.writeText === undefined) {
        setManualCopyText(text);
        setFeedback("自动复制不可用；可选择下方文本手动复制。");
        return;
      }
      await navigator.clipboard.writeText(text);
      if (targetIsCurrent(result)) setFeedback("清单已复制。");
    } catch {
      if (targetIsCurrent(result)) {
        setManualCopyText(text);
        setFeedback("自动复制未完成；可选择下方文本手动复制。");
      }
    }
  }

  function exportChecklist(result: ChapterChecklistResult) {
    if (!targetIsCurrent(result)) return;
    const text = formatChapterProductionChecklistText(result);
    if (text === null) return;
    closeOutput();
    let anchor: HTMLAnchorElement | null = null;
    let objectUrl: string | null = null;
    let timer = 0;
    let cleaned = false;
    const cleanup = () => {
      if (cleaned) return;
      cleaned = true;
      if (timer !== 0) window.clearTimeout(timer);
      anchor?.remove();
      if (objectUrl !== null) URL.revokeObjectURL(objectUrl);
      if (outputCleanup.current === cleanup) outputCleanup.current = null;
    };
    try {
      objectUrl = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
      anchor = document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = chapterProductionChecklistFilename(owner.chapter.title);
      anchor.hidden = true;
      anchor.setAttribute("aria-hidden", "true");
      anchor.tabIndex = -1;
      document.body.appendChild(anchor);
      outputCleanup.current = cleanup;
      anchor.click();
      timer = window.setTimeout(cleanup, 1000);
      if (targetIsCurrent(result)) setFeedback("已发起 TXT 下载；文件是否落盘由浏览器下载管理决定。");
    } catch {
      cleanup();
      if (targetIsCurrent(result)) setFeedback("无法发起 TXT 下载，请重试。");
    }
  }

  function renderReady(result: ChapterChecklistResult) {
    if (result.state === "unreadable") {
      return <p role="alert">章节分镜数据无法读取，缺项暂不可确认。</p>;
    }
    if (result.state === "empty") {
      return <p role="status">当前章节暂无分镜。</p>;
    }
    if (result.items.length === 0) {
      return <p role="status">当前已读取数据中暂无上述缺项；链接已配置不代表可访问。</p>;
    }
    return (
      <ol className="chapter-production-checklist-items">
        {result.items.map((item) => (
          <li key={item.key}>
            <span>{itemDescription(item)}</span>
            {item.kind === "asset" && item.navigation !== null && (
              <button
                className="text-button"
                onClick={() => navigate(item, result)}
                type="button"
              >{item.navigation.kind === "asset" ? "打开素材" : "定位分镜"}</button>
            )}
            {(item.kind === "preview" || item.kind === "video") && item.frame.navigable && (
              <button className="text-button" onClick={() => navigate(item, result)} type="button">定位分镜</button>
            )}
          </li>
        ))}
      </ol>
    );
  }

  return (
    <section aria-label="本章制作缺项" className="chapter-production-checklist-panel">
      <header>
        <div>
          <h3>本章制作缺项</h3>
          <p>依据当前章节和已读取目录整理，不会修改章节或素材。</p>
        </div>
        <div className="chapter-production-checklist-actions">
          <button className="text-button" disabled={state.status === "loading"} onClick={() => void readDirectories()} type="button">
            重新读取
          </button>
          <button
            className="text-button"
            disabled={currentReadyState === null || currentReadyState.result.state === "unreadable"}
            onClick={() => currentReadyState !== null && void copyChecklist(currentReadyState.result)}
            type="button"
          >复制清单</button>
          <button
            className="text-button"
            disabled={currentReadyState === null || currentReadyState.result.state === "unreadable"}
            onClick={() => currentReadyState !== null && exportChecklist(currentReadyState.result)}
            type="button"
          >导出 TXT</button>
          <button className="text-button" onClick={onClose} type="button">关闭</button>
        </div>
      </header>
      {state.status === "loading" && <p role="status">正在读取章节和资产目录…</p>}
      {state.status === "error" && <p role="alert">{state.message}</p>}
      {state.status === "ready" && renderReady(state.result)}
      {feedback !== null && <p className="chapter-production-checklist-feedback" role="status">{feedback}</p>}
      {manualCopyText !== "" && (
        <label className="chapter-production-checklist-manual-copy">
          清单文本（可手动复制）
          <textarea readOnly value={manualCopyText} />
        </label>
      )}
    </section>
  );
}
