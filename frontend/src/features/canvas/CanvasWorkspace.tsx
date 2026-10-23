import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent, type WheelEvent } from "react";
import type { Character, Chapter, Prop, Scene, Series, StoryboardAsset } from "../../shared/api/contracts";
import type { CanvasDataPort } from "../../shared/api/canvasData";
import type { WorkspaceServices } from "../../shared/api/services";
import { useSeriesDialogFocus } from "../series/useSeriesDialogFocus";
import { CanvasMediaPreview } from "./CanvasMediaPreview";
import {
  addCanvasItem,
  canvasViewport,
  documentEqual,
  patchCanvasItem,
  patchCanvasViewport,
  patchHiddenRelation,
  patchNodeLayout,
  removeCanvasItem,
  type CanvasDocument,
  type CanvasViewport,
} from "./canvasDocument";
import { buildCanvasGraph, type CanvasGraphNode, type CanvasNodeKind } from "./canvasGraph";
import { EMPTY_CANVAS_HISTORY, pushCanvasHistory, redoCanvasHistory, undoCanvasHistory, type CanvasHistory } from "./canvasHistory";
import { useCanvasDocument } from "./useCanvasDocument";
import "./canvas.css";

interface CanvasSources {
  chapter: Chapter;
  storyboardAssets: StoryboardAsset[];
  characters: Character[];
  scenes: Scene[];
  props: Prop[];
}

interface DragState {
  pointerId: number;
  startX: number;
  startY: number;
  node: CanvasGraphNode | null;
  nodeX: number;
  nodeY: number;
  viewport: CanvasViewport;
  startDocument: CanvasDocument | null;
}

const FILTERS: Array<{ id: "all" | CanvasNodeKind; label: string }> = [
  { id: "all", label: "全部" }, { id: "character", label: "角色" },
  { id: "scene", label: "场景" }, { id: "prop", label: "道具" },
  { id: "frame", label: "分镜" }, { id: "media", label: "故事板构图" },
  { id: "preview", label: "预览" }, { id: "video", label: "视频" },
  { id: "note", label: "便签" }, { id: "card", label: "卡片" },
];

function itemId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : "canvas-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2);
}

function isTextInput(target: EventTarget | null): boolean {
  return target instanceof HTMLElement
    && (target.isContentEditable || target.matches("input, textarea, select, [contenteditable='true']"));
}

function clampZoom(zoom: number): number {
  return Math.min(3, Math.max(0.2, zoom));
}

export function CanvasWorkspace({
  chapter: selectedChapter,
  series,
  services,
  port,
  userId,
  sessionGeneration,
  onClose,
  onUnauthorized,
}: {
  chapter: Chapter;
  series: Series;
  services: WorkspaceServices;
  port: CanvasDataPort;
  userId: string;
  sessionGeneration: number;
  onClose(): void;
  onUnauthorized(): void;
}) {
  const openEpoch = useRef(0);
  if (openEpoch.current === 0) openEpoch.current = Math.floor(Math.random() * Number.MAX_SAFE_INTEGER) || 1;
  const scopeKey = userId + ":" + sessionGeneration + ":" + series.id + ":" + selectedChapter.id + ":" + openEpoch.current;
  const serviceIdentity = useMemo(() => ({ services, port }), [services, port]);
  const documentState = useCanvasDocument({
    port,
    chapterId: selectedChapter.id,
    actorId: userId,
    sessionGeneration,
    seriesId: series.id,
    serviceIdentity,
    onUnauthorized,
  });
  const [sources, setSources] = useState<CanvasSources | null>(null);
  const [sourceError, setSourceError] = useState<string | null>(null);
  const [sourceLoading, setSourceLoading] = useState(true);
  const [history, setHistory] = useState<CanvasHistory>(EMPTY_CANVAS_HISTORY);
  const [filter, setFilter] = useState<"all" | CanvasNodeKind>("all");
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<string | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [connectionSource, setConnectionSource] = useState<string | null>(null);
  const [closePrompt, setClosePrompt] = useState(false);
  const [closeBusy, setCloseBusy] = useState(false);
  const [drag, setDrag] = useState<DragState | null>(null);
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const scopeEpochRef = useRef(0);
  const unauthorizedRef = useRef(onUnauthorized);
  unauthorizedRef.current = onUnauthorized;

  useEffect(() => {
    const epoch = ++scopeEpochRef.current;
    const controller = new AbortController();
    setSourceLoading(true);
    setSourceError(null);
    setSources(null);
    setHistory(EMPTY_CANVAS_HISTORY);
    void Promise.all([
      services.listChapters(series.id, controller.signal),
      services.listStoryboardAssets(series.id, selectedChapter.id, controller.signal),
      services.listCharacters(series.id, controller.signal),
      services.listScenes(series.id, controller.signal),
      services.listProps(series.id, controller.signal),
    ]).then(([chapters, storyboardAssets, characters, scenes, props]) => {
      if (controller.signal.aborted || scopeEpochRef.current !== epoch) return;
      const matches = chapters.filter((candidate) => candidate.id === selectedChapter.id
        && candidate.series_id === series.id);
      if (matches.length !== 1) {
        setSourceError("当前章节无法唯一确认，请重新读取章节后再打开画布。");
        return;
      }
      setSources({ chapter: matches[0]!, storyboardAssets, characters, scenes, props });
    }).catch((error: unknown) => {
      if (controller.signal.aborted || scopeEpochRef.current !== epoch) return;
      if (error instanceof Error && "status" in error && error.status === 401) unauthorizedRef.current();
      setSourceError(error instanceof Error ? error.message : "画布来源读取失败，请重试。");
    }).finally(() => {
      if (!controller.signal.aborted && scopeEpochRef.current === epoch) setSourceLoading(false);
    });
    return () => {
      controller.abort();
      scopeEpochRef.current += 1;
    };
  }, [selectedChapter.id, series.id, services]);

  const displayDocument = documentState.document ?? {};
  const graph = useMemo(() => sources === null
    ? { nodes: [], edges: [] }
    : buildCanvasGraph(
      sources.chapter,
      sources.storyboardAssets,
      sources.characters,
      sources.scenes,
      sources.props,
      displayDocument,
    ), [displayDocument, sources]);
  const visibleNodes = graph.nodes.filter((node) => filter === "all" || node.kind === filter);
  const visibleRefs = new Set(visibleNodes.map((node) => node.ref));
  const visibleEdges = graph.edges.filter((edge) => visibleRefs.has(edge.sourceRef) && visibleRefs.has(edge.targetRef));
  const nodeByRef = new Map(graph.nodes.map((node) => [node.ref, node]));
  const viewport = canvasViewport(displayDocument);
  const closeRef = useRef<() => void>(() => undefined);
  const focus = useSeriesDialogFocus({ active: !closePrompt, canDismiss: true, onDismiss: () => closeRef.current() });
  const closePromptFocus = useSeriesDialogFocus({
    active: closePrompt,
    canDismiss: true,
    onDismiss: () => setClosePrompt(false),
  });

  const applyDocument = useCallback((next: CanvasDocument) => {
    if (!documentState.writable || documentState.document === null || documentEqual(documentState.document, next)) return;
    setHistory((current) => pushCanvasHistory(current, documentState.document!));
    documentState.edit(next);
  }, [documentState]);

  const changeViewport = useCallback((next: CanvasViewport) => {
    if (documentState.document === null) return;
    applyDocument(patchCanvasViewport(documentState.document, next));
  }, [applyDocument, documentState.document]);

  function commitNodePosition(node: CanvasGraphNode, x: number, y: number): void {
    if (documentState.document === null) return;
    if (node.itemCollection !== undefined && node.itemId !== undefined) {
      documentState.edit(patchCanvasItem(documentState.document, node.itemCollection, node.itemId, { x, y }));
    } else {
      documentState.edit(patchNodeLayout(documentState.document, node.ref, { x, y }));
    }
  }

  function beginDrag(event: ReactPointerEvent<HTMLElement>, node: CanvasGraphNode | null): void {
    if (node !== null) {
      event.stopPropagation();
      setSelectedNode(node.ref);
      if (connecting) {
        if (connectionSource === null) setConnectionSource(node.ref);
        else if (connectionSource !== node.ref) {
          const document = documentState.document;
          if (document !== null) {
            const currentEdges = Array.isArray(document.manualEdges) ? document.manualEdges : [];
            const duplicate = currentEdges.some((edge) => edge !== null && typeof edge === "object"
              && "sourceRef" in edge && "targetRef" in edge
              && edge.sourceRef === connectionSource && edge.targetRef === node.ref);
            if (!duplicate) {
              applyDocument(addCanvasItem(document, "manualEdges", {
                id: itemId(), sourceRef: connectionSource, targetRef: node.ref, relation: "manual",
              }));
            }
          }
          setConnectionSource(null);
          setConnecting(false);
        }
        return;
      }
    }
    if (event.button !== 0) return;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    setDrag({
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      node,
      nodeX: node?.x ?? 0,
      nodeY: node?.y ?? 0,
      viewport,
      startDocument: documentState.document,
    });
  }

  function movePointer(event: ReactPointerEvent<HTMLElement>): void {
    if (drag === null || event.pointerId !== drag.pointerId) return;
    const dx = (event.clientX - drag.startX) / (drag.node === null ? 1 : viewport.zoom);
    const dy = (event.clientY - drag.startY) / (drag.node === null ? 1 : viewport.zoom);
    if (drag.node === null) {
      if (documentState.document !== null) {
        documentState.edit(patchCanvasViewport(documentState.document, {
          ...drag.viewport,
          x: drag.viewport.x + dx,
          y: drag.viewport.y + dy,
        }));
      }
    } else if (Math.abs(dx) + Math.abs(dy) >= 2) {
      commitNodePosition(drag.node, drag.nodeX + dx, drag.nodeY + dy);
    }
  }

  function finishDrag(pointerId: number): void {
    if (drag === null || drag.pointerId !== pointerId) return;
    setDrag(null);
    if (drag.startDocument !== null && documentState.document !== null
      && !documentEqual(drag.startDocument, documentState.document)) {
      setHistory((current) => pushCanvasHistory(current, drag.startDocument!));
    }
  }

  function zoomAt(event: WheelEvent<HTMLDivElement>): void {
    event.preventDefault();
    const element = viewportRef.current;
    if (element === null) return;
    const rect = element.getBoundingClientRect();
    const cursorX = event.clientX - rect.left;
    const cursorY = event.clientY - rect.top;
    const nextZoom = clampZoom(viewport.zoom * (event.deltaY < 0 ? 1.12 : 1 / 1.12));
    const worldX = (cursorX - viewport.x) / viewport.zoom;
    const worldY = (cursorY - viewport.y) / viewport.zoom;
    changeViewport({ x: cursorX - worldX * nextZoom, y: cursorY - worldY * nextZoom, zoom: nextZoom });
  }

  function fitContent(): void {
    const element = viewportRef.current;
    if (element === null || visibleNodes.length === 0) return;
    const rect = element.getBoundingClientRect();
    const minX = Math.min(...visibleNodes.map((node) => node.x));
    const minY = Math.min(...visibleNodes.map((node) => node.y));
    const maxX = Math.max(...visibleNodes.map((node) => node.x + node.width));
    const maxY = Math.max(...visibleNodes.map((node) => node.y + node.height));
    const zoom = clampZoom(Math.min((rect.width - 72) / Math.max(1, maxX - minX), (rect.height - 72) / Math.max(1, maxY - minY)));
    changeViewport({
      zoom,
      x: (rect.width - (maxX - minX) * zoom) / 2 - minX * zoom,
      y: (rect.height - (maxY - minY) * zoom) / 2 - minY * zoom,
    });
  }

  function updateText(node: CanvasGraphNode, value: string): void {
    if (documentState.document === null || node.itemCollection === undefined || node.itemId === undefined) return;
    applyDocument(patchCanvasItem(documentState.document, node.itemCollection, node.itemId, {
      [node.itemTextField ?? "text"]: value,
    }));
  }

  function createTextItem(collection: "notes" | "cards"): void {
    if (documentState.document === null) return;
    const centerX = viewportRef.current?.clientWidth ?? 700;
    const centerY = viewportRef.current?.clientHeight ?? 450;
    const id = itemId();
    applyDocument(addCanvasItem(documentState.document, collection, {
      id,
      text: collection === "notes" ? "新便签" : "新文本卡片",
      x: (centerX / 2 - viewport.x) / viewport.zoom,
      y: (centerY / 2 - viewport.y) / viewport.zoom,
      width: 230,
      height: 145,
    }));
    setSelectedNode(collection === "notes" ? id : "card:" + id);
  }

  function removeSelected(): void {
    if (documentState.document === null) return;
    const edge = graph.edges.find((candidate) => candidate.id === selectedEdge && candidate.manual);
    if (edge !== undefined) {
      applyDocument(removeCanvasItem(documentState.document, "manualEdges", edge.id));
      setSelectedEdge(null);
      return;
    }
    const node = graph.nodes.find((candidate) => candidate.ref === selectedNode);
    if (node?.itemCollection !== undefined && node.itemId !== undefined) {
      applyDocument(removeCanvasItem(documentState.document, node.itemCollection, node.itemId));
      setSelectedNode(null);
    }
  }

  function undo(): void {
    if (documentState.document === null) return;
    const result = undoCanvasHistory(history, documentState.document);
    if (result === null) return;
    setHistory(result.history);
    documentState.edit(result.document);
  }

  function redo(): void {
    if (documentState.document === null) return;
    const result = redoCanvasHistory(history, documentState.document);
    if (result === null) return;
    setHistory(result.history);
    documentState.edit(result.document);
  }

  function toggleRelation(relation: string): void {
    if (documentState.document === null) return;
    const hidden = Array.isArray(documentState.document.hiddenRelations)
      ? documentState.document.hiddenRelations
      : [];
    const count = hidden.filter((item) => item === relation).length;
    if (count > 1) return;
    applyDocument(patchHiddenRelation(documentState.document, relation, count === 0));
  }

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (closePrompt || !(event.target instanceof Node) || !focus.dialogRef.current?.contains(event.target)) return;
      const command = event.metaKey || event.ctrlKey;
      const input = isTextInput(event.target);
      if (command && event.key.toLowerCase() === "s") {
        event.preventDefault();
        void documentState.saveNow();
        return;
      }
      if (input) return;
      if (command && event.key.toLowerCase() === "z") {
        event.preventDefault();
        if (event.shiftKey) redo();
        else undo();
      } else if (event.key.toLowerCase() === "f") {
        event.preventDefault();
        fitContent();
      } else if (event.key === "Delete" || event.key === "Backspace") {
        event.preventDefault();
        removeSelected();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  });

  const close = useCallback(() => {
    if (documentState.hasPendingWork()) {
      setClosePrompt(true);
      return;
    }
    focus.markDismissed();
    onClose();
  }, [documentState, focus, onClose]);
  closeRef.current = close;

  async function saveAndClose(): Promise<void> {
    setCloseBusy(true);
    const saved = await documentState.saveNow();
    setCloseBusy(false);
    if (saved) {
      focus.markDismissed();
      onClose();
    }
  }

  function discardAndClose(): void {
    documentState.discard();
    focus.markDismissed();
    onClose();
  }

  const displayedStatus = documentState.status === "loading" || sourceLoading ? "正在读取章节、素材与画布…" :
    documentState.status === "saving" ? "正在保存…" :
      documentState.status === "conflict" ? "存在版本冲突" :
        documentState.status === "unknown" ? "保存结果待核对" :
          documentState.dirty ? "有未保存更改" :
            documentState.status === "unsupported" ? "只读" : "已保存";

  return (
    <div className="canvas-dialog-backdrop">
      <section aria-labelledby="canvas-title" aria-modal="true" className="canvas-dialog" ref={focus.dialogRef} role="dialog" tabIndex={-1}>
        <header className="canvas-header">
          <div>
            <p className="eyebrow">{series.name}</p>
            <h2 id="canvas-title">{sources?.chapter.title ?? selectedChapter.title} · 创作画布</h2>
            <span className="canvas-save-status" role="status">{displayedStatus}</span>
          </div>
          <div className="canvas-header-actions">
            <button className="secondary-button" disabled={!documentState.writable || history.undo.length === 0} onClick={undo} type="button">撤销</button>
            <button className="secondary-button" disabled={!documentState.writable || history.redo.length === 0} onClick={redo} type="button">重做</button>
            <button className="primary-button" disabled={!documentState.dirty || documentState.status === "saving" || documentState.status === "unknown" || documentState.status === "conflict" || !documentState.writable} onClick={() => void documentState.saveNow()} type="button">保存</button>
            <button aria-label="关闭创作画布" className="canvas-close-button" onClick={close} type="button">关闭</button>
          </div>
        </header>

        <div className="canvas-toolbar">
          <div className="canvas-filter-row" aria-label="节点筛选">
            {FILTERS.map((item) => (
              <button
                aria-pressed={filter === item.id}
                className={filter === item.id ? "is-active" : ""}
                key={item.id}
                onClick={() => setFilter(item.id)}
                type="button"
              >{item.label}</button>
            ))}
          </div>
          <div className="canvas-relation-row" aria-label="关系显示">
            {(["uses", "reference", "generates", "manual"] as const).map((relation) => {
              const hidden = Array.isArray(displayDocument.hiddenRelations)
                && displayDocument.hiddenRelations.includes(relation);
              const duplicate = Array.isArray(displayDocument.hiddenRelations)
                && displayDocument.hiddenRelations.filter((item) => item === relation).length > 1;
              return (
                <button
                  aria-pressed={!hidden}
                  disabled={!documentState.writable || duplicate}
                  key={relation}
                  onClick={() => toggleRelation(relation)}
                  type="button"
                >{hidden ? "显示 " : "隐藏 "}{relation} 关系</button>
              );
            })}
          </div>
          <div className="canvas-tool-row">
            <button disabled={!documentState.writable} onClick={() => createTextItem("notes")} type="button">新增便签</button>
            <button disabled={!documentState.writable} onClick={() => createTextItem("cards")} type="button">新增文本卡片</button>
            <button aria-pressed={connecting} disabled={!documentState.writable} onClick={() => {
              setConnecting((value) => !value);
              setConnectionSource(null);
            }} type="button">{connecting ? "取消连线" : "手动连线"}</button>
            <button onClick={() => changeViewport({ ...viewport, zoom: clampZoom(viewport.zoom / 1.15) })} type="button" aria-label="缩小">−</button>
            <span>{Math.round(viewport.zoom * 100)}%</span>
            <button onClick={() => changeViewport({ ...viewport, zoom: clampZoom(viewport.zoom * 1.15) })} type="button" aria-label="放大">＋</button>
            <button onClick={fitContent} type="button">适配内容</button>
            <button disabled={!documentState.writable || (selectedNode === null && selectedEdge === null)} onClick={removeSelected} type="button">删除选中</button>
          </div>
        </div>

        {sourceError !== null && <p className="canvas-error" role="alert">{sourceError}</p>}
        {documentState.message !== null && <p className="canvas-message" role="status">{documentState.message}</p>}
        {(documentState.status === "conflict" || documentState.status === "unknown") && (
          <div className="canvas-message">
            <p>{documentState.status === "unknown"
              ? "保存结果尚未确认。本地草稿已保留，自动保存与快捷键保存已暂停；读取服务器内容不会确认之前的请求结果。"
              : "服务器内容已变化。本地草稿已保留，自动保存与快捷键保存已暂停；请核对后选择下一步。"}</p>
            <button onClick={() => void documentState.reviewServer()} type="button">核对服务器内容</button>
            {documentState.reviewedServer !== null && (
              <>
                <button
                  disabled={documentState.reviewedServer.save_capability !== "guarded-v1"}
                  onClick={() => void documentState.resumeReviewedDraft()}
                  type="button"
                >基于服务器版本继续保存草稿</button>
                <button onClick={() => { documentState.adoptReviewedServer(); setHistory(EMPTY_CANVAS_HISTORY); }} type="button">
                  采用服务器内容并放弃本地草稿
                </button>
              </>
            )}
          </div>
        )}

        <div
          className="canvas-viewport"
          onPointerDown={(event) => beginDrag(event, null)}
          onPointerMove={movePointer}
          onPointerUp={(event) => finishDrag(event.pointerId)}
          onPointerCancel={() => { if (drag !== null) finishDrag(drag.pointerId); }}
          onWheel={zoomAt}
          ref={viewportRef}
          role="region"
          aria-label="画布工作区"
        >
          {sourceLoading || documentState.status === "loading" ? (
            <div className="canvas-empty-state" role="status">正在读取当前章节的真实内容…</div>
          ) : sourceError !== null ? (
            <div className="canvas-empty-state" role="alert">{sourceError}</div>
          ) : (
            <div className="canvas-scene" style={{ transform: "translate(" + viewport.x + "px, " + viewport.y + "px) scale(" + viewport.zoom + ")" }}>
              <svg aria-label="节点关系" className="canvas-edge-layer" height="3000" width="4000">
                {visibleEdges.map((edge) => {
                  const source = nodeByRef.get(edge.sourceRef);
                  const target = nodeByRef.get(edge.targetRef);
                  if (source === undefined || target === undefined) return null;
                  const x1 = source.x + source.width;
                  const y1 = source.y + source.height / 2;
                  const x2 = target.x;
                  const y2 = target.y + target.height / 2;
                  const bend = Math.max(70, Math.abs(x2 - x1) / 2);
                  return (
                    <path
                      className={edge.manual ? "canvas-edge is-manual" : "canvas-edge"}
                      d={"M " + x1 + " " + y1 + " C " + (x1 + bend) + " " + y1 + ", " + (x2 - bend) + " " + y2 + ", " + x2 + " " + y2}
                      key={edge.id}
                      onClick={() => edge.manual && setSelectedEdge(edge.id)}
                      stroke={selectedEdge === edge.id ? "#7c3aed" : undefined}
                    />
                  );
                })}
              </svg>
              {visibleNodes.map((node) => (
                <article
                  aria-label={node.title}
                  className={"canvas-node canvas-node-" + node.kind + (selectedNode === node.ref ? " is-selected" : "") + (connecting ? " is-connectable" : "")}
                  key={node.key}
                  onClick={() => { setSelectedNode(node.ref); setSelectedEdge(null); }}
                  onPointerDown={(event) => beginDrag(event, node)}
                  style={{ left: node.x, top: node.y, width: node.width, minHeight: node.height }}
                  tabIndex={0}
                >
                  <strong className="canvas-node-title">{node.title}</strong>
                  {node.imageUrl !== null && <CanvasMediaPreview imageUrl={node.imageUrl} key={scopeKey + ":" + node.imageUrl} port={port} scopeKey={scopeKey} />}
                  {node.itemCollection !== undefined ? (
                    <textarea
                      aria-label={node.title + "文字"}
                      onChange={(event) => updateText(node, event.currentTarget.value)}
                      onPointerDown={(event) => event.stopPropagation()}
                      value={node.detail}
                    />
                  ) : <p>{node.detail}</p>}
                </article>
              ))}
            </div>
          )}
          {connecting && <div className="canvas-connect-hint" role="status">
            {connectionSource === null ? "选择连线起点，再选择终点。" : "已选起点，请选择终点。"}
          </div>}
        </div>

        {closePrompt && (
          <div className="canvas-confirm-backdrop">
            <section aria-labelledby="canvas-close-title" aria-modal="true" className="canvas-close-confirm"
              ref={closePromptFocus.dialogRef} role="dialog" tabIndex={-1}>
              <h3 id="canvas-close-title">关闭创作画布</h3>
              {documentState.status === "unknown"
                ? <p>保存结果尚未确认。放弃仅清除本地草稿，之前的服务器请求仍可能已经提交。</p>
                : documentState.status === "saving"
                  ? <p>保存请求正在进行。关闭并放弃本地草稿不会撤销已经到达服务器的请求，请在核对状态后再决定。</p>
                : <p>还有未确认的画布编辑。保存完成后再关闭，或继续留在画布中。</p>}
              <div>
                <button
                  disabled={closeBusy || !documentState.writable || documentState.status === "unknown" || documentState.status === "conflict"}
                  onClick={() => void saveAndClose()}
                  type="button"
                >
                  {closeBusy ? "正在等待保存…" : "保存并关闭"}
                </button>
                <button onClick={() => setClosePrompt(false)} type="button">继续编辑</button>
                <button onClick={discardAndClose} type="button">放弃本地草稿并关闭</button>
              </div>
            </section>
          </div>
        )}
      </section>
    </div>
  );
}
