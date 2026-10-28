// @vitest-environment jsdom
import { StrictMode } from "react";
import { act, cleanup, createEvent, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Chapter, Series } from "../../shared/api/contracts";
import { CanvasDataError, type CanvasDataPort } from "../../shared/api/canvasData";
import type {
  ChapterGenerationHistoryContext,
  ChapterGenerationHistoryPort,
  ChapterGenerationHistoryRun,
} from "../../shared/api/chapterGenerationHistory";
import { ApiError } from "../../shared/api/errors";
import type { WorkspaceServices } from "../../shared/api/services";
import {
  CANVAS_SOURCE_CAPABILITY,
  type CanvasSourcePort,
  type CanvasSourceSnapshot,
  type CanvasSourceWriteReceipt,
} from "../../shared/api/canvasSources";
import type { CanvasDocument } from "./canvasDocument";
import { CanvasMediaPreview } from "./CanvasMediaPreview";
import { CanvasWorkspace, canvasFramePreparationProgress } from "./CanvasWorkspace";

const chapter: Chapter = {
  id: "chapter-a",
  series_id: "series-a",
  title: "第一章",
  content: [{ text: "门前的来信", character: ["character-a"], scene: [], prop: [], storyboard: [], preview: null, videos: [] }],
  order: 1,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  lock: null,
};
const series = { id: "series-a", name: "雾中来信", can_enter: true } as Series;
const document: CanvasDocument = {
  nodeLayouts: {},
  notes: [],
  cards: [],
  manualEdges: [],
  hiddenRelations: [],
  viewport: { x: 0, y: 0, zoom: 1 },
  unknown: { preserve: true },
};

const sourceSnapshot: CanvasSourceSnapshot = {
  capability: CANVAS_SOURCE_CAPABILITY,
  series_id: series.id,
  chapter_id: chapter.id,
  version: "8be1499c4eb8b1a1f8e20d020cd52796e938726f0a1889cfecc07641d8d28a57",
  requires_lock: false,
  chapter,
  storyboard_assets: [],
  characters: [{
    id: "character-a", series_id: "series-a", name: "林澜", description: "当前角色", image_url: null,
    aliases: null, canonical_key: null, gender: null, age: null, role: null, appearance: null,
    audio_url: null, voice_ref: null, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
  }],
  scenes: [],
  props: [],
};

function mediaSnapshot(version: string, preview: string): CanvasSourceSnapshot {
  const mediaChapter: Chapter = {
    ...chapter,
    content: [{
      text: "含重复视频 URL 的镜头",
      character: [],
      scene: [],
      prop: [],
      storyboard: ["board-a"],
      preview,
      videos: ["same.mp4", "same.mp4"],
      future_frame_field: { preserve: true },
    }],
  };
  return {
    ...sourceSnapshot,
    version,
    chapter: mediaChapter,
    storyboard_assets: [{
      id: "board-a",
      series_id: "series-a",
      chapter_id: "chapter-a",
      frame_index: 0,
      name: "分镜 1",
      description: null,
      image_url: null,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    }],
  };
}

type WorkspaceServiceOverrides = {
  [Key in keyof WorkspaceServices]?: WorkspaceServices[Key] | undefined;
};

function setup(
  save?: CanvasDataPort["save"],
  strictMode = false,
  initialDocument = document,
  sourcePort?: CanvasSourcePort,
  serviceOverrides: WorkspaceServiceOverrides = {},
  writable = true,
) {
  let persistedDocument = initialDocument;
  const port: CanvasDataPort = {
    get: vi.fn(async () => ({
      id: "canvas-a",
      chapter_id: chapter.id,
      version: 2,
      document_json: persistedDocument,
      updated_by: "actor-a",
      updated_at: "2026-01-01T00:00:00Z",
      ...(writable ? { save_capability: "guarded-v1" as const } : {}),
    })),
    save: save ?? vi.fn(async (_chapterId, nextDocument, guard) => {
      persistedDocument = nextDocument;
      return {
      id: guard.id ?? "canvas-a",
      chapter_id: chapter.id,
      version: guard.id === null ? 1 : guard.version + 1,
      document_json: nextDocument,
      updated_by: "actor-a",
      updated_at: "2026-01-02T00:00:00Z",
      save_receipt: {
        mode: "guarded-v1" as const,
        id: guard.id ?? "canvas-a",
        version: guard.id === null ? 1 : guard.version + 1,
      },
      };
    }),
    readImage: vi.fn(async () => {
      throw new Error("Image data is not part of this source test fixture.");
    }),
  };
  const resolvedSourcePort = sourcePort ?? {
    read: vi.fn(async () => sourceSnapshot),
    write: vi.fn(),
  } as unknown as CanvasSourcePort;
  const services = {
    canvasSource: resolvedSourcePort,
    listChapters: vi.fn(async () => [chapter]),
    listStoryboardAssets: vi.fn(async () => []),
    listCharacters: vi.fn(async () => [{
      id: "character-a", series_id: "series-a", name: "林澜", description: "当前角色", image_url: null,
    }]),
    listScenes: vi.fn(async () => []),
    listProps: vi.fn(async () => []),
    ...serviceOverrides,
  } as unknown as WorkspaceServices;
  const onClose = vi.fn();
  const onUnauthorized = vi.fn();
  const onSourceChanged = vi.fn();
  const canvas = (
    <CanvasWorkspace
      chapter={chapter}
      onClose={onClose}
      onUnauthorized={onUnauthorized}
      onSourceChanged={onSourceChanged}
      port={port}
      series={series}
      services={services}
      sessionGeneration={4}
      userId="actor-a"
    />
  );
  const view = render(strictMode ? <StrictMode>{canvas}</StrictMode> : canvas);
  return {
    port, services, onClose, onUnauthorized, onSourceChanged,
    rerender: view.rerender, unmount: view.unmount, container: view.container,
  };
}

function workspaceElement(
  services: WorkspaceServices,
  options: {
    port: CanvasDataPort;
    onClose(): void;
    onUnauthorized(): void;
    onSourceChanged(chapterId: string): void;
    userId?: string;
    sessionGeneration?: number;
  },
) {
  return (
    <CanvasWorkspace
      chapter={chapter}
      onClose={options.onClose}
      onUnauthorized={options.onUnauthorized}
      onSourceChanged={options.onSourceChanged}
      port={options.port}
      series={series}
      services={services}
      sessionGeneration={options.sessionGeneration ?? 4}
      userId={options.userId ?? "actor-a"}
    />
  );
}

function pointerUpOn(target: Element | null, viewport: HTMLElement, pointerId: number): void {
  const descriptor = Object.getOwnPropertyDescriptor(document, "elementFromPoint");
  Object.defineProperty(document, "elementFromPoint", {
    configurable: true,
    value: () => target,
  });
  try {
    fireEvent.pointerUp(target instanceof HTMLElement ? target : viewport, { pointerId, clientX: 0, clientY: 0 });
  } finally {
    if (descriptor === undefined) Reflect.deleteProperty(document, "elementFromPoint");
    else Object.defineProperty(document, "elementFromPoint", descriptor);
  }
}

function clickCanvasEdge(target: Element, viewport: HTMLElement, pointerId: number): void {
  fireEvent.pointerDown(target, { pointerId, button: 0, clientX: 0, clientY: 0 });
  fireEvent.pointerUp(viewport, { pointerId, button: 0, clientX: 0, clientY: 0 });
  fireEvent.click(target);
}

function stubChapterGenerationHistoryMediaReader(readMedia: ChapterGenerationHistoryRun["readMedia"]) {
  const captured = { context: null as ChapterGenerationHistoryContext | null };
  const run = { isCurrent: () => true, readMedia } as unknown as ChapterGenerationHistoryRun;
  const port = {
    beginRun: vi.fn((context: ChapterGenerationHistoryContext) => {
      captured.context = context;
      return run;
    }),
  } as unknown as ChapterGenerationHistoryPort;
  return { captured, port };
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("CanvasWorkspace", () => {
  it("connects nodes by port drag in world coordinates and uses one history entry and the existing save port", async () => {
    const initialDocument: CanvasDocument = {
      ...document,
      viewport: { x: -120, y: -55, zoom: 1.5 },
    };
    const workspace = setup(undefined, false, initialDocument);
    await screen.findByLabelText("林澜");
    const viewport = screen.getByRole("region", { name: "画布工作区" });
    vi.spyOn(viewport, "getBoundingClientRect").mockReturnValue({
      x: 70, y: 90, left: 70, top: 90, right: 870, bottom: 690, width: 800, height: 600,
      toJSON: () => ({}),
    });
    const sourceNode = screen.getByLabelText("林澜");
    const sourcePort = screen.getByRole("button", { name: "从林澜右侧端口拖动连线" });
    const targetPort = screen.getByRole("button", { name: "从分镜 1左侧端口拖动连线" });
    const originalSceneTransform = workspace.container.querySelector(".canvas-scene")?.getAttribute("style");

    fireEvent.pointerDown(sourcePort, { pointerId: 31, button: 0, clientX: 385, clientY: 200 });
    const temporaryPath = workspace.container.querySelector(".canvas-edge-temporary")?.getAttribute("d");
    expect(temporaryPath).toContain("M 290 110");
    fireEvent.pointerMove(viewport, { pointerId: 31, clientX: 800, clientY: 250 });
    expect(workspace.container.querySelector(".canvas-edge-temporary")?.getAttribute("d")).not.toBe(temporaryPath);
    pointerUpOn(targetPort, viewport, 31);

    await waitFor(() => expect(workspace.container.querySelectorAll("path.is-manual")).toHaveLength(1));
    expect(workspace.container.querySelector(".canvas-edge-temporary")).toBeNull();
    expect((sourceNode as HTMLElement).style.left).toBe("40px");
    expect((sourceNode as HTMLElement).style.top).toBe("50px");
    expect(workspace.container.querySelector(".canvas-scene")?.getAttribute("style")).toBe(originalSceneTransform);

    const undo = screen.getByRole("button", { name: "撤销" });
    const redo = screen.getByRole("button", { name: "重做" });
    fireEvent.click(undo);
    await waitFor(() => expect(workspace.container.querySelectorAll("path.is-manual")).toHaveLength(0));
    expect(undo).toBeDisabled();
    fireEvent.click(redo);
    await waitFor(() => expect(workspace.container.querySelectorAll("path.is-manual")).toHaveLength(1));
    fireEvent.click(screen.getByRole("button", { name: "保存布局" }));
    await waitFor(() => expect(workspace.port.save).toHaveBeenCalledOnce());
    const savedDocument = vi.mocked(workspace.port.save).mock.calls[0]?.[1];
    expect(savedDocument?.manualEdges).toEqual([expect.objectContaining({
      sourceRef: "character:character-a",
      targetRef: `frame-index:${sourceSnapshot.version}:0`,
      relation: "manual",
    })]);
    expect(savedDocument?.unknown).toEqual({ preserve: true });
  });

  it("cancels blank, self, duplicate, and pointer-cancelled port gestures while allowing the reverse direction", async () => {
    const targetRef = `frame-index:${sourceSnapshot.version}:0`;
    const workspace = setup(undefined, false, {
      ...document,
      manualEdges: [{ id: "existing", sourceRef: "character:character-a", targetRef, relation: "manual", future: true }],
    });
    await screen.findByLabelText("林澜");
    const viewport = screen.getByRole("region", { name: "画布工作区" });
    const characterRight = screen.getByRole("button", { name: "从林澜右侧端口拖动连线" });
    const characterLeft = screen.getByRole("button", { name: "从林澜左侧端口拖动连线" });
    const frameRight = screen.getByRole("button", { name: "从分镜 1右侧端口拖动连线" });
    const frameLeft = screen.getByRole("button", { name: "从分镜 1左侧端口拖动连线" });

    fireEvent.pointerDown(characterRight, { pointerId: 41, button: 0, clientX: 0, clientY: 0 });
    pointerUpOn(null, viewport, 41);
    fireEvent.pointerDown(characterRight, { pointerId: 42, button: 0, clientX: 0, clientY: 0 });
    pointerUpOn(characterLeft, viewport, 42);
    fireEvent.pointerDown(characterRight, { pointerId: 43, button: 0, clientX: 0, clientY: 0 });
    pointerUpOn(frameLeft, viewport, 43);
    expect(workspace.container.querySelectorAll("path.is-manual")).toHaveLength(1);
    expect(workspace.container.querySelector(".canvas-edge-temporary")).toBeNull();
    expect(screen.getByRole("button", { name: "撤销" })).toBeDisabled();

    fireEvent.pointerDown(characterRight, { pointerId: 44, button: 0, clientX: 0, clientY: 0 });
    fireEvent.pointerCancel(viewport, { pointerId: 44 });
    expect(workspace.container.querySelector(".canvas-edge-temporary")).toBeNull();
    expect(workspace.container.querySelectorAll("path.is-manual")).toHaveLength(1);

    fireEvent.pointerDown(characterRight, { pointerId: 45, button: 0, clientX: 0, clientY: 0 });
    const lostCapture = new Event("lostpointercapture", { bubbles: true });
    Object.defineProperty(lostCapture, "pointerId", { value: 45 });
    fireEvent(viewport, lostCapture);
    expect(workspace.container.querySelector(".canvas-edge-temporary")).toBeNull();
    expect(workspace.container.querySelectorAll("path.is-manual")).toHaveLength(1);

    fireEvent.pointerDown(frameRight, { pointerId: 46, button: 0, clientX: 0, clientY: 0 });
    pointerUpOn(characterLeft, viewport, 46);
    await waitFor(() => expect(workspace.container.querySelectorAll("path.is-manual")).toHaveLength(2));
    fireEvent.click(screen.getByRole("button", { name: "保存布局" }));
    await waitFor(() => expect(workspace.port.save).toHaveBeenCalledOnce());
    const savedDocument = vi.mocked(workspace.port.save).mock.calls[0]?.[1];
    expect(savedDocument?.manualEdges).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: "existing", future: true, sourceRef: "character:character-a", targetRef }),
      expect.objectContaining({ sourceRef: targetRef, targetRef: "character:character-a", relation: "manual" }),
    ]));
  });

  it("disables port writes for read-only documents and cancels an active drag after scope changes", async () => {
    const readOnly = setup(undefined, false, document, undefined, {}, false);
    await screen.findByLabelText("林澜");
    const readOnlyPort = screen.getByRole("button", { name: "从林澜右侧端口拖动连线" });
    expect(readOnlyPort).toBeDisabled();
    expect(readOnly.container.querySelector(".canvas-edge-temporary")).toBeNull();
    expect(readOnly.port.save).not.toHaveBeenCalled();
    readOnly.unmount();

    const workspace = setup();
    await screen.findByLabelText("林澜");
    const viewport = screen.getByRole("region", { name: "画布工作区" });
    const sourcePort = screen.getByRole("button", { name: "从林澜右侧端口拖动连线" });
    fireEvent.pointerDown(sourcePort, { pointerId: 46, button: 0, clientX: 0, clientY: 0 });
    expect(workspace.container.querySelector(".canvas-edge-temporary")).not.toBeNull();
    workspace.rerender(workspaceElement(workspace.services, {
      port: workspace.port,
      onClose: workspace.onClose,
      onUnauthorized: workspace.onUnauthorized,
      onSourceChanged: workspace.onSourceChanged,
      userId: "actor-b",
      sessionGeneration: 5,
    }));
    await waitFor(() => expect(workspace.container.querySelector(".canvas-edge-temporary")).toBeNull());
    pointerUpOn(screen.getByRole("button", { name: "从分镜 1左侧端口拖动连线" }), viewport, 46);
    expect(workspace.container.querySelectorAll("path.is-manual")).toHaveLength(0);
    expect(workspace.port.save).not.toHaveBeenCalled();
  });

  it("cancels a port drag when the existing canvas writer becomes uncertain", async () => {
    const save = vi.fn<CanvasDataPort["save"]>().mockRejectedValue(new CanvasDataError("unknown", "unknown"));
    const workspace = setup(save);
    await screen.findByLabelText("林澜");
    const viewport = screen.getByRole("region", { name: "画布工作区" });
    const character = screen.getByLabelText("林澜");
    fireEvent.pointerDown(character, { pointerId: 47, button: 0, clientX: 0, clientY: 0 });
    fireEvent.pointerMove(viewport, { pointerId: 47, clientX: 80, clientY: 40 });
    fireEvent.pointerUp(viewport, { pointerId: 47 });
    await waitFor(() => expect((character as HTMLElement).style.left).toBe("120px"));

    fireEvent.pointerDown(screen.getByRole("button", { name: "从林澜右侧端口拖动连线" }), {
      pointerId: 48, button: 0, clientX: 0, clientY: 0,
    });
    expect(workspace.container.querySelector(".canvas-edge-temporary")).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "保存布局" }));
    await screen.findByText(/保存结果尚未确认/);
    expect(workspace.container.querySelector(".canvas-edge-temporary")).toBeNull();
    pointerUpOn(screen.getByRole("button", { name: "从分镜 1左侧端口拖动连线" }), viewport, 48);
    expect(workspace.container.querySelectorAll("path.is-manual")).toHaveLength(0);
    expect(save).toHaveBeenCalledOnce();
  });

  it("shows selected frame information and navigates to a related asset", async () => {
    setup();
    await screen.findByText("门前的来信");

    fireEvent.click(screen.getByLabelText("分镜 1"));
    const information = screen.getByRole("region", { name: "选中内容信息" });
    expect(within(information).getByText("门前的来信")).toBeTruthy();
    expect(within(information).getByRole("progressbar").getAttribute("value")).toBe("30");
    expect(within(information).getByText("所属章节")).toBeTruthy();
    expect(within(information).getByText("林澜")).toBeTruthy();

    fireEvent.click(within(information).getByRole("button", { name: "定位" }));
    expect(screen.getByRole("article", { name: "林澜" }).className).toContain("is-selected");
    expect(screen.getByRole("region", { name: "选中内容信息" })
      .querySelector(".canvas-selected-information-kind")?.textContent).toBe("角色素材");
  });

  it("replaces a selected node with a manually clicked edge", async () => {
    const targetRef = `frame-index:${sourceSnapshot.version}:0`;
    const initialDocument: CanvasDocument = {
      ...document,
      manualEdges: [{ id: "manual-edge-a", sourceRef: "character:character-a", targetRef, relation: "manual" }],
    };
    const { container, port } = setup(undefined, false, initialDocument);
    await screen.findByLabelText("林澜");

    const character = screen.getByLabelText("林澜");
    const viewport = screen.getByRole("region", { name: "画布工作区" });
    const edge = container.querySelector("path.is-manual");
    expect(edge).not.toBeNull();
    fireEvent.click(character);
    expect(character.classList).toContain("is-selected");

    clickCanvasEdge(edge!, viewport, 71);

    expect(character.classList).not.toContain("is-selected");
    expect(edge?.getAttribute("stroke")).toBe("#7c3aed");
    expect(screen.getByRole("button", { name: "撤销" })).toBeDisabled();
    expect(port.save).not.toHaveBeenCalled();
  });

  it("clears a selected edge on node pointerdown before a drag can suppress click", async () => {
    const targetRef = `frame-index:${sourceSnapshot.version}:0`;
    const initialDocument: CanvasDocument = {
      ...document,
      manualEdges: [{ id: "manual-edge-a", sourceRef: "character:character-a", targetRef, relation: "manual" }],
    };
    const { container, port } = setup(undefined, false, initialDocument);
    await screen.findByLabelText("林澜");

    const character = screen.getByLabelText("林澜");
    const viewport = screen.getByRole("region", { name: "画布工作区" });
    const edge = container.querySelector("path.is-manual");
    expect(edge).not.toBeNull();
    clickCanvasEdge(edge!, viewport, 72);
    expect(edge?.getAttribute("stroke")).toBe("#7c3aed");

    fireEvent.pointerDown(character, { pointerId: 73, button: 0, clientX: 0, clientY: 0 });
    expect(character.classList).toContain("is-selected");
    expect(edge?.getAttribute("stroke")).not.toBe("#7c3aed");
    fireEvent.pointerUp(viewport, { pointerId: 73, button: 0, clientX: 0, clientY: 0 });

    expect(screen.getByRole("button", { name: "撤销" })).toBeDisabled();
    expect(port.save).not.toHaveBeenCalled();
  });

  it("clears selection on left blank panning and preserves it on right-button down", async () => {
    const targetRef = `frame-index:${sourceSnapshot.version}:0`;
    const initialDocument: CanvasDocument = {
      ...document,
      manualEdges: [{ id: "manual-edge-a", sourceRef: "character:character-a", targetRef, relation: "manual" }],
    };
    const { container, port } = setup(undefined, false, initialDocument);
    await screen.findByLabelText("林澜");

    const character = screen.getByLabelText("林澜");
    const viewport = screen.getByRole("region", { name: "画布工作区" });
    const edge = container.querySelector("path.is-manual");
    expect(edge).not.toBeNull();
    fireEvent.click(character);
    fireEvent.pointerDown(viewport, { pointerId: 74, button: 2, clientX: 0, clientY: 0 });
    expect(character.classList).toContain("is-selected");
    fireEvent.pointerUp(viewport, { pointerId: 74, button: 2, clientX: 0, clientY: 0 });
    fireEvent.pointerDown(viewport, { pointerId: 75, button: 0, clientX: 0, clientY: 0 });
    expect(character.classList).not.toContain("is-selected");
    fireEvent.pointerUp(viewport, { pointerId: 75, button: 0, clientX: 0, clientY: 0 });

    clickCanvasEdge(edge!, viewport, 76);
    expect(edge?.getAttribute("stroke")).toBe("#7c3aed");
    fireEvent.pointerDown(viewport, { pointerId: 77, button: 2, clientX: 0, clientY: 0 });
    expect(edge?.getAttribute("stroke")).toBe("#7c3aed");
    fireEvent.pointerUp(viewport, { pointerId: 77, button: 2, clientX: 0, clientY: 0 });
    fireEvent.pointerDown(viewport, { pointerId: 78, button: 0, clientX: 0, clientY: 0 });
    expect(edge?.getAttribute("stroke")).not.toBe("#7c3aed");
    fireEvent.pointerUp(viewport, { pointerId: 78, button: 0, clientX: 0, clientY: 0 });

    expect(screen.getByRole("button", { name: "删除选中" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "撤销" })).toBeDisabled();
    expect(port.save).not.toHaveBeenCalled();
  });

  it("clears an edge selection when a newly created note becomes selected", async () => {
    const targetRef = `frame-index:${sourceSnapshot.version}:0`;
    const initialDocument: CanvasDocument = {
      ...document,
      manualEdges: [{ id: "manual-edge-a", sourceRef: "character:character-a", targetRef, relation: "manual" }],
    };
    const { container, port } = setup(undefined, false, initialDocument);
    await screen.findByLabelText("林澜");

    const edge = container.querySelector("path.is-manual");
    const viewport = screen.getByRole("region", { name: "画布工作区" });
    expect(edge).not.toBeNull();
    clickCanvasEdge(edge!, viewport, 79);
    expect(edge?.getAttribute("stroke")).toBe("#7c3aed");

    fireEvent.click(screen.getByRole("button", { name: "新增便签" }));

    expect(container.querySelector(".canvas-node.is-selected")).not.toBeNull();
    expect(edge?.getAttribute("stroke")).not.toBe("#7c3aed");
    expect(screen.getByRole("button", { name: "撤销" })).toBeEnabled();
    expect(port.save).not.toHaveBeenCalled();
  });

  it("resolves a selected video's stable owning frame without adding a graph edge", async () => {
    const snapshot = mediaSnapshot("e".repeat(64), "/uploads/preview.png");
    const sourcePort: CanvasSourcePort = {
      read: vi.fn(async () => snapshot),
      write: vi.fn<CanvasSourcePort["write"]>(() => new Promise(() => undefined)),
    };
    setup(undefined, false, document, sourcePort);
    fireEvent.click(await screen.findByLabelText("视频 1"));

    let information = screen.getByRole("region", { name: "选中内容信息" });
    expect(within(information).getAllByText("所属镜头")).toHaveLength(2);
    expect(within(information).getAllByText("分镜 1")).toHaveLength(2);
    fireEvent.click(within(information).getByRole("button", { name: "编辑媒体来源" }));
    expect(await screen.findByRole("dialog", { name: "编辑视频槽 1" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "关闭媒体来源编辑" }));

    information = screen.getByRole("region", { name: "选中内容信息" });
    fireEvent.click(within(information).getByRole("button", { name: "定位" }));
    const frameArticle = screen.getByRole("article", { name: "分镜 1" });
    expect(frameArticle.className).toContain("is-selected");
    information = screen.getByRole("region", { name: "选中内容信息" });
    expect(within(information).getByRole("button", { name: "编辑分镜文字" })).toBeEnabled();
    const videoRelation = within(information).getAllByRole("listitem")
      .find((item) => item.textContent?.includes("视频 1"));
    expect(videoRelation).toBeTruthy();
    fireEvent.click(within(videoRelation!).getByRole("button", { name: "定位" }));
    expect(screen.getByLabelText("视频 1").className).toContain("is-selected");
  });

  it("reads selected video media through the chapter history run and renders a video player", async () => {
    const baseSnapshot = mediaSnapshot("f".repeat(64), "/uploads/preview.png");
    const snapshot: CanvasSourceSnapshot = {
      ...baseSnapshot,
      chapter: {
        ...baseSnapshot.chapter,
        content: [{ ...baseSnapshot.chapter.content![0]!, videos: ["/uploads/clip.mp4"] }],
      },
    };
    const sourcePort: CanvasSourcePort = {
      read: vi.fn(async () => snapshot),
      write: vi.fn<CanvasSourcePort["write"]>(() => new Promise(() => undefined)),
    };
    const readMedia = vi.fn(async (_url: string, _signal: AbortSignal) => new Blob(["video-bytes"], { type: "video/mp4" }));
    const history = stubChapterGenerationHistoryMediaReader(readMedia);
    const workspace = setup(undefined, false, document, sourcePort, {
      apiBaseUrl: "https://api.example.test/api",
      chapterGenerationHistory: history.port,
    });
    const originalCreateObjectURL = Object.getOwnPropertyDescriptor(URL, "createObjectURL");
    const originalRevokeObjectURL = Object.getOwnPropertyDescriptor(URL, "revokeObjectURL");
    const createObjectURL = vi.fn(() => "blob:canvas-video-preview");
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectURL });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revokeObjectURL });

    try {
      fireEvent.click(await screen.findByLabelText("视频 1"));
      expect(await screen.findByLabelText("视频 1生成视频")).toBeTruthy();
      expect(history.port.beginRun).toHaveBeenCalledOnce();
      expect(history.captured.context).toMatchObject({
        actorId: "actor-a",
        seriesId: "series-a",
        chapterId: "chapter-a",
        targetKind: "frame-preview",
        storyboardAssetId: "board-a",
        frameIndex: 0,
        mode: "video",
      });
      expect(history.captured.context?.isContextCurrent()).toBe(true);
      expect(readMedia).toHaveBeenCalledWith("/uploads/clip.mp4", expect.any(AbortSignal));
      expect(createObjectURL).toHaveBeenCalledWith(expect.any(Blob));

      workspace.unmount();
      expect(revokeObjectURL).toHaveBeenCalledWith("blob:canvas-video-preview");
    } finally {
      if (originalCreateObjectURL === undefined) delete (URL as Partial<typeof URL>).createObjectURL;
      else Object.defineProperty(URL, "createObjectURL", originalCreateObjectURL);
      if (originalRevokeObjectURL === undefined) delete (URL as Partial<typeof URL>).revokeObjectURL;
      else Object.defineProperty(URL, "revokeObjectURL", originalRevokeObjectURL);
    }
  });

  it("resolves the video reader mode from a legacy video frame preview URL", async () => {
    const snapshot = mediaSnapshot("9".repeat(64), "https://media.example.invalid/frame-preview.mp4?token=signature");
    const sourcePort: CanvasSourcePort = {
      read: vi.fn(async () => snapshot),
      write: vi.fn<CanvasSourcePort["write"]>(() => new Promise(() => undefined)),
    };
    const readMedia = vi.fn(async (_url: string, _signal: AbortSignal) => new Blob(["preview-video"], { type: "video/mp4" }));
    const history = stubChapterGenerationHistoryMediaReader(readMedia);
    const workspace = setup(undefined, false, document, sourcePort, {
      apiBaseUrl: "https://api.example.test/api",
      chapterGenerationHistory: history.port,
    });
    const originalCreateObjectURL = Object.getOwnPropertyDescriptor(URL, "createObjectURL");
    const originalRevokeObjectURL = Object.getOwnPropertyDescriptor(URL, "revokeObjectURL");
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:legacy-frame-video") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });

    try {
      fireEvent.click(await screen.findByLabelText("分镜 1"));
      expect(await screen.findByLabelText("分镜预览生成视频")).toBeTruthy();
      expect(history.captured.context).toMatchObject({ targetKind: "frame-preview", mode: "video" });
      expect(readMedia).toHaveBeenCalledWith("https://media.example.invalid/frame-preview.mp4?token=signature", expect.any(AbortSignal));
      workspace.unmount();
    } finally {
      if (originalCreateObjectURL === undefined) delete (URL as Partial<typeof URL>).createObjectURL;
      else Object.defineProperty(URL, "createObjectURL", originalCreateObjectURL);
      if (originalRevokeObjectURL === undefined) delete (URL as Partial<typeof URL>).revokeObjectURL;
      else Object.defineProperty(URL, "revokeObjectURL", originalRevokeObjectURL);
    }
  });

  it("resolves a referenced secondary storyboard image to its unique owning frame", async () => {
    const baseSnapshot = mediaSnapshot("b".repeat(64), "/uploads/preview.png");
    const snapshot: CanvasSourceSnapshot = {
      ...baseSnapshot,
      chapter: {
        ...baseSnapshot.chapter,
        content: [{ ...baseSnapshot.chapter.content![0]!, storyboard: ["board-a", "board-b"] }],
      },
      storyboard_assets: [
        baseSnapshot.storyboard_assets[0]!,
        {
          id: "board-b",
          series_id: "series-a",
          chapter_id: "chapter-a",
          frame_index: 0,
          name: "次要构图",
          description: "第二个故事板引用",
          image_url: "/uploads/secondary-board.png",
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        },
      ],
    };
    const sourcePort: CanvasSourcePort = {
      read: vi.fn(async () => snapshot),
      write: vi.fn<CanvasSourcePort["write"]>(() => new Promise(() => undefined)),
    };
    setup(undefined, false, document, sourcePort);

    fireEvent.click(await screen.findByLabelText("次要构图"));
    const information = screen.getByRole("region", { name: "选中内容信息" });
    expect(within(information).getAllByText("分镜 1")).toHaveLength(2);
    expect(within(information).getByRole("button", { name: "编辑所属镜头" })).toBeEnabled();
    fireEvent.click(within(information).getByRole("button", { name: "定位" }));
    expect(screen.getByRole("article", { name: "分镜 1" }).className).toContain("is-selected");
  });

  it("keeps a dirty canvas draft when selected information offers a guarded source edit", async () => {
    const workspace = setup();
    await screen.findByText("门前的来信");
    fireEvent.click(screen.getByRole("button", { name: "新增便签" }));
    const noteText = await screen.findByLabelText("便签文字");
    fireEvent.change(noteText, { target: { value: "保留中的便签草稿" } });

    fireEvent.click(screen.getByLabelText("分镜 1"));
    let information = screen.getByRole("region", { name: "选中内容信息" });
    expect(within(information).getByText("门前的来信")).toBeTruthy();
    expect(within(information).getByRole("button", { name: "编辑分镜文字" })).toBeDisabled();
    expect(within(information).getAllByText("请先保存画布草稿，再编辑章节来源。")).toHaveLength(2);

    fireEvent.click(within(information).getByRole("button", { name: "定位" }));
    information = screen.getByRole("region", { name: "选中内容信息" });
    expect(information.querySelector(".canvas-selected-information-kind")?.textContent).toBe("角色素材");
    expect(within(information).getByRole("button", { name: "编辑素材" })).toBeDisabled();
    expect(screen.getByLabelText("便签文字")).toHaveValue("保留中的便签草稿");
    expect(workspace.services.canvasSource?.write).not.toHaveBeenCalled();
  });

  it("keeps selected source edits blocked after an unknown canvas save", async () => {
    const save = vi.fn<CanvasDataPort["save"]>().mockRejectedValue(new CanvasDataError("unknown", "unknown"));
    const workspace = setup(save);
    await screen.findByText("门前的来信");
    fireEvent.click(screen.getByRole("button", { name: "新增便签" }));
    fireEvent.change(await screen.findByLabelText("便签文字"), { target: { value: "待核对的草稿" } });
    fireEvent.click(screen.getByLabelText("分镜 1"));
    fireEvent.click(screen.getByRole("button", { name: "保存布局" }));
    await screen.findByText(/保存结果暂时无法确认/);

    const information = screen.getByRole("region", { name: "选中内容信息" });
    expect(within(information).getByRole("button", { name: "编辑分镜文字" })).toBeDisabled();
    expect(within(information).getAllByText("画布保存结果待核对，草稿已保留；核对前不能切换来源编辑。")).toHaveLength(2);
    expect((screen.getByLabelText("便签文字") as HTMLTextAreaElement).value).toBe("待核对的草稿");
    expect(workspace.services.canvasSource?.write).not.toHaveBeenCalled();
  });

  it("preserves the legacy frame preparation progress weighting", () => {
    const preparedSnapshot: CanvasSourceSnapshot = {
      ...sourceSnapshot,
      characters: [{ ...sourceSnapshot.characters[0]!, image_url: "/uploads/character.png" }],
      storyboard_assets: [{
        id: "board-ready", series_id: series.id, chapter_id: chapter.id, frame_index: 0,
        name: "已准备故事板", description: null, image_url: "/uploads/storyboard.png",
        created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
      }],
    };
    expect(canvasFramePreparationProgress({
      preview: "/uploads/preview.png",
      character: ["character-a", "missing-character"],
      scene: [],
      prop: [],
      storyboard: ["board-ready"],
    }, preparedSnapshot)).toBe(95);
  });

  it.each(["chapter list", "legacy source reads"] as const)(
    "calls the unauthorized handler when initial %s return 401",
    async (source) => {
      const observedPort: CanvasSourcePort = {
        read: vi.fn(async () => sourceSnapshot),
        write: vi.fn<CanvasSourcePort["write"]>(() => new Promise(() => undefined)),
      };
      const unauthorized = new ApiError("http", "登录状态已失效。", 401);
      const overrides: WorkspaceServiceOverrides = source === "chapter list"
        ? { listChapters: vi.fn(async () => { throw unauthorized; }) }
        : {
          canvasSource: undefined,
          listStoryboardAssets: vi.fn(async () => { throw unauthorized; }),
        };
      const workspace = setup(undefined, false, document, observedPort, overrides);

      await waitFor(() => expect(workspace.onUnauthorized).toHaveBeenCalledOnce());
      expect(observedPort.read).not.toHaveBeenCalled();
      expect(observedPort.write).not.toHaveBeenCalled();
    },
  );

  it("ignores an initial chapter-list 401 after the canvas services have been replaced", async () => {
    let rejectStale: ((error: ApiError) => void) | null = null;
    const staleChapters = new Promise<Chapter[]>((_resolve, reject) => { rejectStale = reject; });
    const staleList = vi.fn(() => staleChapters);
    const sourcePort: CanvasSourcePort = {
      read: vi.fn(async () => sourceSnapshot),
      write: vi.fn<CanvasSourcePort["write"]>(() => new Promise(() => undefined)),
    };
    const workspace = setup(undefined, false, document, sourcePort, { listChapters: staleList });
    await waitFor(() => expect(staleList).toHaveBeenCalledOnce());

    const replacementServices = {
      ...workspace.services,
      listChapters: vi.fn(async () => [chapter]),
    } as unknown as WorkspaceServices;
    workspace.rerender(workspaceElement(replacementServices, {
      port: workspace.port,
      onClose: workspace.onClose,
      onUnauthorized: workspace.onUnauthorized,
      onSourceChanged: workspace.onSourceChanged,
    }));
    await waitFor(() => expect(replacementServices.canvasSource?.read).toHaveBeenCalledOnce());

    await act(async () => { rejectStale?.(new ApiError("http", "旧来源读取已失效。", 401)); });
    expect(workspace.onUnauthorized).not.toHaveBeenCalled();
    expect(replacementServices.canvasSource?.read).toHaveBeenCalledOnce();
  });

  it("loads the selected chapter sources, saves a local note through the guarded port, then closes", async () => {
    const { port, services, onClose } = setup();
    expect(await screen.findByText("门前的来信")).toBeTruthy();
    expect(screen.getByLabelText("林澜")).toBeTruthy();
    expect(port.get).toHaveBeenCalledWith("chapter-a", expect.any(AbortSignal));
    expect(services.canvasSource?.read).toHaveBeenCalledWith("chapter-a", expect.any(AbortSignal));

    fireEvent.click(screen.getByRole("button", { name: "新增便签" }));
    const note = await screen.findByLabelText("便签文字");
    fireEvent.change(note, { target: { value: "雨夜后的线索" } });
    fireEvent.click(screen.getByRole("button", { name: "保存布局" }));

    await waitFor(() => expect(port.save).toHaveBeenCalledOnce());
    const [, savedDocument, guard] = vi.mocked(port.save).mock.calls[0] ?? [];
    expect(guard).toEqual({ mode: "guarded-v1", id: "canvas-a", version: 2 });
    expect(savedDocument?.notes).toEqual([expect.objectContaining({ id: expect.any(String), text: "雨夜后的线索" })]);
    expect(savedDocument?.unknown).toEqual({ preserve: true });

    fireEvent.click(screen.getByRole("button", { name: "关闭创作画布" }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("keeps proven preview and duplicate-video bindings through source edits, history, save, and reopen", async () => {
    const firstVersion = "a".repeat(64);
    const secondVersion = "b".repeat(64);
    let current = mediaSnapshot(firstVersion, "/uploads/preview.png");
    const sourcePort: CanvasSourcePort = {
      read: vi.fn(async () => current),
      write: vi.fn(async (_chapterId, _frozen, command) => {
        if (command.type === "frame.update") {
          const preview = current.chapter.content?.[0]?.preview;
          const updated = mediaSnapshot(secondVersion, typeof preview === "string" ? preview : "");
          current = {
            ...updated,
            chapter: {
              ...updated.chapter,
              content: [{ ...updated.chapter.content![0]!, text: command.text }],
            },
          };
        }
        return {
          capability: CANVAS_SOURCE_CAPABILITY,
          request_id: "controlled-write",
          command_type: command.type,
          snapshot: current,
          creation_receipt: null,
        };
      }),
    };
    const oldPreview = `media:${firstVersion}:frame:board-a:preview`;
    const initialDocument: CanvasDocument = {
      ...document,
      nodeLayouts: {
        [oldPreview]: { x: 2000, y: 300, vendor: "keep" },
        [`media:${firstVersion}:frame:board-a:video:0`]: { x: 2300, y: 400 },
        [`media:${firstVersion}:frame:board-a:video:1`]: { x: 2600, y: 500 },
      },
      manualEdges: [{
        id: "future-dangling-edge",
        sourceRef: "removed-node",
        targetRef: "removed-target",
        relation: "future",
        vendor: { keep: true },
      }],
    };
    const workspace = setup(undefined, false, initialDocument, sourcePort);
    expect(await screen.findByLabelText("分镜预览")).toBeTruthy();
    const viewport = screen.getByRole("region", { name: "画布工作区" });
    const preview = screen.getByLabelText("分镜预览");
    fireEvent.pointerDown(preview, { pointerId: 20, button: 0, clientX: 0, clientY: 0 });
    fireEvent.pointerMove(viewport, { pointerId: 20, clientX: 80, clientY: 40 });
    fireEvent.pointerUp(viewport, { pointerId: 20 });
    await waitFor(() => expect((screen.getByLabelText("分镜预览") as HTMLElement).style.left).toBe("2080px"));

    fireEvent.click(screen.getByRole("button", { name: "手动连线" }));
    fireEvent.pointerDown(screen.getByLabelText("分镜预览"), { pointerId: 21, button: 0 });
    fireEvent.pointerDown(screen.getByLabelText("分镜 1"), { pointerId: 22, button: 0 });
    expect(workspace.container.querySelector("path.is-manual")).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "保存布局" }));
    await waitFor(() => expect(screen.getByText("已保存")).toBeTruthy());

    fireEvent.click(screen.getByLabelText("分镜 1"));
    const selectedInformation = screen.getByRole("region", { name: "选中内容信息" });
    fireEvent.click(within(selectedInformation).getByRole("button", { name: "编辑分镜文字" }));
    const frameEditor = await screen.findByRole("dialog", { name: "编辑分镜文字" });
    fireEvent.change(within(frameEditor).getByLabelText("镜头文字"), { target: { value: "更新文字但保留媒体绑定" } });
    fireEvent.click(within(frameEditor).getByRole("button", { name: "保存文字" }));
    await waitFor(() => expect(sourcePort.write).toHaveBeenCalledOnce());
    await screen.findByText("章节来源写入已获服务器回执，独立读取也确认了同一版本。");

    const updatedPreview = screen.getByLabelText("分镜预览");
    expect((updatedPreview as HTMLElement).style.left).toBe("2080px");
    expect((screen.getByLabelText("视频 1") as HTMLElement).style.left).toBe("2300px");
    expect((screen.getByLabelText("视频 2") as HTMLElement).style.left).toBe("2600px");
    fireEvent.click(screen.getByRole("button", { name: "保存布局" }));
    await waitFor(() => expect(workspace.port.save).toHaveBeenCalledTimes(2));
    const transitionDocument = vi.mocked(workspace.port.save).mock.calls[1]?.[1];
    const newPreview = `canvas-source-binding:${encodeURIComponent(oldPreview)}`;
    expect(transitionDocument?.manualEdges).toEqual(expect.arrayContaining([
      expect.objectContaining({ sourceRef: newPreview, targetRef: "frame:board-a", relation: "manual" }),
      expect.objectContaining({ id: "future-dangling-edge", vendor: { keep: true } }),
    ]));
    expect(workspace.container.querySelector("path.is-manual")).not.toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "撤销" }));
    fireEvent.click(screen.getByRole("button", { name: "撤销" }));
    await waitFor(() => expect((screen.getByLabelText("分镜预览") as HTMLElement).style.left).toBe("2000px"));
    fireEvent.click(screen.getByRole("button", { name: "重做" }));
    fireEvent.click(screen.getByRole("button", { name: "重做" }));
    await waitFor(() => expect((screen.getByLabelText("分镜预览") as HTMLElement).style.left).toBe("2080px"));
    expect(workspace.container.querySelector("path.is-manual")).not.toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "保存布局" }));
    await waitFor(() => expect(workspace.port.save).toHaveBeenCalledTimes(3));
    const savedDocument = vi.mocked(workspace.port.save).mock.calls[2]?.[1];
    const newVideo0 = `canvas-source-binding:${encodeURIComponent(`media:${firstVersion}:frame:board-a:video:0`)}`;
    const newVideo1 = `canvas-source-binding:${encodeURIComponent(`media:${firstVersion}:frame:board-a:video:1`)}`;
    expect(savedDocument?.nodeLayouts).toMatchObject({
      [newPreview]: { x: 2080, y: 340, vendor: "keep" },
      [newVideo0]: { x: 2300, y: 400 },
      [newVideo1]: { x: 2600, y: 500 },
    });
    expect(savedDocument?.manualEdges).toEqual(expect.arrayContaining([
      expect.objectContaining({ sourceRef: newPreview, targetRef: "frame:board-a", relation: "manual" }),
      expect.objectContaining({ id: "future-dangling-edge", vendor: { keep: true } }),
    ]));

    workspace.unmount();
    const reopened = render(
      <CanvasWorkspace
        chapter={chapter}
        onClose={workspace.onClose}
        onUnauthorized={workspace.onUnauthorized}
        onSourceChanged={workspace.onSourceChanged}
        port={workspace.port}
        series={series}
        services={workspace.services}
        sessionGeneration={4}
        userId="actor-a"
      />,
    );
    const reopenedPreview = await screen.findByLabelText("分镜预览");
    expect((reopenedPreview as HTMLElement).style.left).toBe("2080px");
    expect((screen.getByLabelText("视频 1") as HTMLElement).style.left).toBe("2300px");
    expect((screen.getByLabelText("视频 2") as HTMLElement).style.left).toBe("2600px");
    expect(reopened.container.querySelector("path.is-manual")).not.toBeNull();
  });

  it("closes a legacy frame editor during a pending save and ignores its late child response", async () => {
    let rejectPendingWrite: ((cause: Error) => void) | null = null;
    const snapshot = mediaSnapshot("a".repeat(64), "/uploads/preview.png");
    const sourcePort: CanvasSourcePort = {
      read: vi.fn(async () => snapshot),
      write: vi.fn<CanvasSourcePort["write"]>((_chapterId, _frozen, _command, _signal) => {
        return new Promise<CanvasSourceWriteReceipt>((_resolve, reject) => {
          rejectPendingWrite = reject;
        });
      }),
    };
    const workspace = setup(undefined, false, document, sourcePort);
    await screen.findByText("含重复视频 URL 的镜头");
    fireEvent.click(screen.getByLabelText("分镜 1"));
    fireEvent.click(screen.getByRole("button", { name: "编辑分镜文字" }));
    const editor = await screen.findByRole("dialog", { name: "编辑分镜文字" });
    const textbox = within(editor).getByLabelText("镜头文字");
    const leftArrow = createEvent.keyDown(textbox, { key: "ArrowLeft", ctrlKey: true, cancelable: true });
    fireEvent(textbox, leftArrow);
    expect(leftArrow.defaultPrevented).toBe(false);

    fireEvent.change(textbox, { target: { value: "等待写入的分镜文字" } });
    fireEvent.click(within(editor).getByRole("button", { name: "保存文字" }));
    await waitFor(() => expect(sourcePort.write).toHaveBeenCalledOnce());
    const readsBeforeLateResponse = vi.mocked(sourcePort.read).mock.calls.length;

    fireEvent.click(within(editor).getByRole("button", { name: "取消" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "编辑分镜文字" })).toBeNull());
    expect(vi.mocked(sourcePort.write).mock.calls[0]?.[3].aborted).toBe(true);
    expect(workspace.onClose).not.toHaveBeenCalled();

    await act(async () => {
      rejectPendingWrite?.(new ApiError("aborted", "Editor closed while the source write was pending."));
    });
    expect(screen.queryByRole("dialog", { name: "编辑分镜文字" })).toBeNull();
    expect(screen.getByText(/写入结果尚未确认/)).toBeInTheDocument();
    expect(vi.mocked(sourcePort.read).mock.calls).toHaveLength(readsBeforeLateResponse);
    expect(sourcePort.write).toHaveBeenCalledOnce();
    expect(workspace.onUnauthorized).not.toHaveBeenCalled();
  });

  it.each([
    {
      label: "additional storyboard references",
      references: ["board-a", "legacy-reference"],
      assets: [{
        id: "board-a", series_id: "series-a", chapter_id: "chapter-a", frame_index: 0,
        name: "分镜", description: null, image_url: null,
        created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
      }],
      expectedStoryboardId: "board-a",
    },
    {
      label: "same-index orphan storyboard asset",
      references: [null, ""],
      assets: [{
        id: "orphan-board", series_id: "series-a", chapter_id: "chapter-a", frame_index: 0,
        name: "孤立素材", description: null, image_url: null,
        created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
      }],
      expectedStoryboardId: null,
    },
  ])("sends the backend frame target for $label without changing unrelated rows", async ({ references, assets, expectedStoryboardId }) => {
    const targetSnapshot: CanvasSourceSnapshot = {
      ...sourceSnapshot,
      version: "c".repeat(64),
      chapter: {
        ...chapter,
        content: [{
          text: "原镜头",
          storyboard: references as unknown as string[],
          character: [], scene: [], prop: [],
          orphan_vendor_field: { preserve: true },
        }],
      },
      storyboard_assets: assets,
    };
    const sourcePort: CanvasSourcePort = {
      read: vi.fn(async () => targetSnapshot),
      write: vi.fn<CanvasSourcePort["write"]>(() => new Promise(() => undefined)),
    };
    setup(undefined, false, document, sourcePort);
    await screen.findByText("原镜头");
    fireEvent.click(screen.getByLabelText("分镜 1"));
    fireEvent.click(screen.getByRole("button", { name: "编辑分镜文字" }));
    fireEvent.change(screen.getByLabelText("镜头文字"), { target: { value: "更新后镜头" } });
    fireEvent.click(screen.getByRole("button", { name: "保存文字" }));

    await waitFor(() => expect(sourcePort.write).toHaveBeenCalledOnce());
    const [, frozen, command] = vi.mocked(sourcePort.write).mock.calls[0]!;
    expect(command).toMatchObject({
      type: "frame.update",
      target: { index: 0, storyboard_id: expectedStoryboardId },
    });
    expect(frozen.chapter.content?.[0]).toMatchObject({ orphan_vendor_field: { preserve: true } });
  });

  it.each([
    { references: [" "] },
    { references: [null, "\t"] },
  ])("does not enable a null frame target for whitespace references $references", async ({ references }) => {
    const targetSnapshot: CanvasSourceSnapshot = {
      ...sourceSnapshot,
      version: "d".repeat(64),
      chapter: {
        ...chapter,
        content: [{
          text: "空白引用不能当作无 ID",
          storyboard: references as unknown as string[],
          character: [], scene: [], prop: [],
        }],
      },
    };
    const sourcePort: CanvasSourcePort = {
      read: vi.fn(async () => targetSnapshot),
      write: vi.fn<CanvasSourcePort["write"]>(() => new Promise(() => undefined)),
    };
    setup(undefined, false, document, sourcePort);
    await screen.findByText("空白引用不能当作无 ID");
    fireEvent.click(screen.getByLabelText("分镜 1"));
    expect(screen.getByRole("button", { name: "编辑分镜文字" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "追加视频 URL 或 pending" })).toBeDisabled();
    expect(sourcePort.write).not.toHaveBeenCalled();
  });

  it("keeps a draft on unknown results and stops ordinary saving", async () => {
    const save = vi.fn<CanvasDataPort["save"]>().mockRejectedValue(
      new CanvasDataError("unknown", "unknown"),
    );
    const { onClose } = setup(save);
    await screen.findByText("门前的来信");
    fireEvent.click(screen.getByRole("button", { name: "新增便签" }));
    fireEvent.change(await screen.findByLabelText("便签文字"), { target: { value: "未确认草稿" } });
    fireEvent.click(screen.getByRole("button", { name: "保存布局" }));
    expect(await screen.findByText(/保存结果暂时无法确认/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "保存布局" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "关闭创作画布" }));
    expect(screen.getByRole("heading", { name: "关闭创作画布" })).toBeTruthy();
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "放弃本地草稿并关闭" }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it.each(["unknown", "conflict"] as const)("keeps %s writes blocked after further local edits", async (kind) => {
    const save = vi.fn<CanvasDataPort["save"]>().mockRejectedValue(new CanvasDataError(kind, kind));
    const { port } = setup(save);
    await screen.findByText("门前的来信");
    fireEvent.click(screen.getByRole("button", { name: "新增便签" }));
    const note = await screen.findByLabelText("便签文字");
    fireEvent.change(note, { target: { value: "原始草稿" } });
    fireEvent.click(screen.getByRole("button", { name: "保存布局" }));
    await screen.findByText(/保存结果暂时无法确认|已被其他成员更新/);

    vi.useFakeTimers();
    fireEvent.change(note, { target: { value: "核对前继续编辑的草稿" } });
    expect(screen.getByRole("button", { name: "保存布局" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "撤销" }));
    expect((screen.getByLabelText("便签文字") as HTMLTextAreaElement).value).toBe("原始草稿");
    expect(screen.getByRole("button", { name: "保存布局" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "重做" }));
    fireEvent.keyDown(window.document, { key: "s", metaKey: true });
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(save).toHaveBeenCalledOnce();
    expect((screen.getByLabelText("便签文字") as HTMLTextAreaElement).value).toBe("核对前继续编辑的草稿");
    const attempted = save.mock.calls[0]?.[1] as { notes: Array<Record<string, unknown>> };
    vi.mocked(port.get).mockResolvedValueOnce({
      id: "canvas-a", chapter_id: chapter.id, version: 3,
      document_json: {
        ...attempted,
        notes: attempted.notes.map((item) => ({ ...item, text: "核对前继续编辑的草稿" })),
      },
      updated_by: "actor-a", updated_at: "2026-01-03T00:00:00Z", save_capability: "guarded-v1",
    });

    fireEvent.click(screen.getByRole("button", { name: "关闭创作画布" }));
    expect(screen.getByRole("button", { name: "保存并关闭" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "继续编辑" }));
    vi.useRealTimers();
    fireEvent.click(screen.getByRole("button", { name: "核对服务器内容" }));
    await waitFor(() => expect(port.get).toHaveBeenCalledTimes(2));
    expect((screen.getByLabelText("便签文字") as HTMLTextAreaElement).value).toBe("核对前继续编辑的草稿");
    expect(save).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "基于服务器版本继续保存草稿" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "基于服务器版本继续保存草稿" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(2));
    expect(save.mock.calls[1]?.[2]).toEqual({ mode: "guarded-v1", id: "canvas-a", version: 3 });
  });

  it("uses service identity in addition to port identity along the canvas parent boundary", async () => {
    const { port, services, onClose, onUnauthorized, onSourceChanged, rerender } = setup();
    await screen.findByText("门前的来信");
    expect(services.listChapters).toHaveBeenCalledOnce();
    expect(services.canvasSource?.read).toHaveBeenCalledOnce();
    const replacementServices = {
      ...services,
      listChapters: vi.fn(async () => [chapter]),
    } as unknown as WorkspaceServices;
    rerender(
      <CanvasWorkspace chapter={chapter} onClose={onClose} onUnauthorized={onUnauthorized} onSourceChanged={onSourceChanged} port={port}
        series={series} services={replacementServices} sessionGeneration={4} userId="actor-a" />,
    );
    await waitFor(() => expect(port.get).toHaveBeenCalledTimes(2));
    expect(replacementServices.listChapters).toHaveBeenCalledOnce();
    expect(replacementServices.canvasSource?.read).toHaveBeenCalledTimes(2);
  });

  it("does not let a replaced source operation's late 401 clear or log out the new operation", async () => {
    const firstVersion = "a".repeat(64);
    const secondVersion = "b".repeat(64);
    let current = mediaSnapshot(firstVersion, "/uploads/preview.png");
    let rejectOldWrite: ((cause: Error) => void) | null = null;
    let resolveCurrentWrite: (() => void) | null = null;
    const sourcePort: CanvasSourcePort = {
      read: vi.fn(async () => current),
      write: vi.fn<CanvasSourcePort["write"]>((_chapterId, _frozen, command) => {
        if (vi.mocked(sourcePort.write).mock.calls.length === 1) {
          return new Promise<CanvasSourceWriteReceipt>((_resolve, reject) => { rejectOldWrite = reject; });
        }
        if (command.type !== "frame.update") throw new Error("Unexpected source command in this test.");
        current = {
          ...current,
          version: secondVersion,
          chapter: {
            ...current.chapter,
            content: [{ ...current.chapter.content![0]!, text: command.text }],
          },
        };
        return new Promise<CanvasSourceWriteReceipt>((resolve) => {
          resolveCurrentWrite = () => resolve({
            capability: CANVAS_SOURCE_CAPABILITY,
            request_id: "current-write",
            command_type: "frame.update",
            snapshot: current,
            creation_receipt: null,
          });
        });
      }),
    };
    const workspace = setup(undefined, false, document, sourcePort);
    await screen.findByText("含重复视频 URL 的镜头");
    fireEvent.click(screen.getByLabelText("分镜 1"));
    fireEvent.click(screen.getByRole("button", { name: "编辑分镜文字" }));
    let editor = await screen.findByRole("dialog", { name: "编辑分镜文字" });
    fireEvent.change(within(editor).getByLabelText("镜头文字"), { target: { value: "旧scope延迟操作" } });
    fireEvent.click(within(editor).getByRole("button", { name: "保存文字" }));
    await waitFor(() => expect(sourcePort.write).toHaveBeenCalledTimes(1));

    const replacementServices = {
      ...workspace.services,
      listChapters: vi.fn(async () => [chapter]),
    } as unknown as WorkspaceServices;
    workspace.rerender(
      <CanvasWorkspace
        chapter={chapter}
        onClose={workspace.onClose}
        onUnauthorized={workspace.onUnauthorized}
        onSourceChanged={workspace.onSourceChanged}
        port={workspace.port}
        series={series}
        services={replacementServices}
        sessionGeneration={4}
        userId="actor-a"
      />,
    );
    await screen.findByText("含重复视频 URL 的镜头");
    fireEvent.click(screen.getByLabelText("分镜 1"));
    fireEvent.click(screen.getByRole("button", { name: "编辑分镜文字" }));
    editor = await screen.findByRole("dialog", { name: "编辑分镜文字" });
    fireEvent.change(within(editor).getByLabelText("镜头文字"), { target: { value: "新scope仍在操作" } });
    fireEvent.click(within(editor).getByRole("button", { name: "保存文字" }));
    await waitFor(() => expect(sourcePort.write).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("button", { name: "新增角色素材" })).toBeDisabled();

    await act(async () => {
      rejectOldWrite?.(new ApiError("http", "旧scope令牌已失效。", 401));
    });
    expect(workspace.onUnauthorized).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "新增角色素材" })).toBeDisabled();

    await act(async () => { resolveCurrentWrite?.(); });
    await screen.findByText("章节来源写入已获服务器回执，独立读取也确认了同一版本。");
    expect(screen.getByText("新scope仍在操作")).toBeTruthy();
    expect(workspace.onUnauthorized).not.toHaveBeenCalled();
  });

  it("does not apply a foreign-series source returned by readback or a later verification", async () => {
    const version = "c".repeat(64);
    let readCount = 0;
    const acknowledged: CanvasSourceSnapshot = {
      ...mediaSnapshot("d".repeat(64), "/uploads/preview.png"),
      chapter: {
        ...mediaSnapshot("d".repeat(64), "/uploads/preview.png").chapter,
        content: [{
          ...mediaSnapshot("d".repeat(64), "/uploads/preview.png").chapter.content![0]!,
          text: "本剧集已确认的回执",
        }],
      },
    };
    const foreign: CanvasSourceSnapshot = {
      ...acknowledged,
      series_id: "series-foreign",
      chapter_id: "chapter-foreign",
      version: "e".repeat(64),
      chapter: {
        ...acknowledged.chapter,
        id: "chapter-foreign",
        series_id: "series-foreign",
        title: "其他剧集章节",
        content: [{ ...acknowledged.chapter.content![0]!, text: "不得并入的其他剧集内容" }],
      },
      storyboard_assets: [],
    };
    const initial = mediaSnapshot(version, "/uploads/preview.png");
    const sourcePort: CanvasSourcePort = {
      read: vi.fn(async () => {
        readCount += 1;
        return readCount === 1 ? initial : foreign;
      }),
      write: vi.fn(async () => ({
        capability: CANVAS_SOURCE_CAPABILITY,
        request_id: "write-before-foreign-read",
        command_type: "frame.update" as const,
        snapshot: acknowledged,
        creation_receipt: null,
      })),
    };
    setup(undefined, false, document, sourcePort);
    await screen.findByText("含重复视频 URL 的镜头");
    fireEvent.click(screen.getByLabelText("分镜 1"));
    fireEvent.click(screen.getByRole("button", { name: "编辑分镜文字" }));
    const editor = await screen.findByRole("dialog", { name: "编辑分镜文字" });
    fireEvent.change(within(editor).getByLabelText("镜头文字"), { target: { value: "本剧集已确认的回执" } });
    fireEvent.click(within(editor).getByRole("button", { name: "保存文字" }));

    await screen.findByText("服务器已确认来源写入，但独立读取指向其他剧集或章节。原回执与意图已保留，请在当前权限范围内重新核对；不会重发。");
    expect(screen.getByText("本剧集已确认的回执")).toBeTruthy();
    expect(screen.queryByText("不得并入的其他剧集内容")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "只读核对当前来源" }));
    await screen.findByText("只读响应指向其他剧集或章节，没有并入当前画布。原回执或意图仍保留。");
    expect(screen.getByText("本剧集已确认的回执")).toBeTruthy();
    expect(screen.queryByText("不得并入的其他剧集内容")).toBeNull();
    expect(sourcePort.write).toHaveBeenCalledOnce();
    expect(sourcePort.read).toHaveBeenCalledTimes(3);
  });

  it("closes a clean canvas on Escape without opening a discard prompt", async () => {
    const { onClose } = setup();
    await screen.findByText("门前的来信");
    fireEvent.keyDown(window.document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledOnce();
    expect(screen.queryByRole("heading", { name: "关闭创作画布" })).toBeNull();
  });

  it("clears selected nodes and manual edges before the next Escape closes a clean canvas", async () => {
    const targetRef = `frame-index:${sourceSnapshot.version}:0`;
    const { container, onClose, port } = setup(undefined, false, {
      ...document,
      manualEdges: [{ id: "manual-edge-a", sourceRef: "character:character-a", targetRef, relation: "manual" }],
    });
    await screen.findByText("门前的来信");
    const undo = screen.getByRole("button", { name: "撤销" });
    expect(undo).toBeDisabled();

    const character = screen.getByLabelText("林澜");
    fireEvent.click(character);
    expect(character.classList).toContain("is-selected");
    fireEvent.keyDown(window.document, { key: "Escape" });
    expect(character.classList).not.toContain("is-selected");
    expect(onClose).not.toHaveBeenCalled();

    const manualEdge = container.querySelector("path.is-manual");
    expect(manualEdge).not.toBeNull();
    fireEvent.click(manualEdge!);
    expect(manualEdge?.getAttribute("stroke")).toBe("#7c3aed");
    fireEvent.keyDown(window.document, { key: "Escape" });
    expect(manualEdge?.getAttribute("stroke")).not.toBe("#7c3aed");
    expect(onClose).not.toHaveBeenCalled();
    expect(undo).toBeDisabled();
    expect(port.save).not.toHaveBeenCalled();

    fireEvent.keyDown(window.document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledOnce();
    expect(screen.queryByRole("heading", { name: "关闭创作画布" })).toBeNull();
  });

  it("cancels link and port gestures on Escape without losing a dirty draft or writing on pointerup", async () => {
    const initialDocument: CanvasDocument = {
      ...document,
      notes: [{ id: "note-a", text: "原稿", x: 400, y: 300, width: 230, height: 145 }],
    };
    const { container, onClose, port } = setup(undefined, false, initialDocument);
    await screen.findByText("门前的来信");
    const note = screen.getByLabelText("便签文字") as HTMLTextAreaElement;
    fireEvent.change(note, { target: { value: "待保存草稿" } });
    const undo = screen.getByRole("button", { name: "撤销" });
    const undoWasDisabled = (undo as HTMLButtonElement).disabled;

    fireEvent.click(screen.getByRole("button", { name: "手动连线" }));
    fireEvent.pointerDown(screen.getByLabelText("林澜"), { pointerId: 61, button: 0 });
    expect(screen.getByText("已选起点，请选择终点。")).toBeTruthy();
    fireEvent.keyDown(window.document, { key: "Escape" });
    expect(screen.queryByText("已选起点，请选择终点。")).toBeNull();
    expect(screen.getByRole("button", { name: "手动连线" }).getAttribute("aria-pressed")).toBe("false");
    expect(onClose).not.toHaveBeenCalled();
    expect(note.value).toBe("待保存草稿");
    expect(port.save).not.toHaveBeenCalled();

    const viewport = screen.getByRole("region", { name: "画布工作区" });
    fireEvent.pointerDown(screen.getByRole("button", { name: "从林澜右侧端口拖动连线" }), {
      pointerId: 62, button: 0, clientX: 0, clientY: 0,
    });
    expect(container.querySelector(".canvas-edge-temporary")).not.toBeNull();
    fireEvent.keyDown(window.document, { key: "Escape" });
    expect(container.querySelector(".canvas-edge-temporary")).toBeNull();
    pointerUpOn(screen.getByRole("button", { name: "从分镜 1左侧端口拖动连线" }), viewport, 62);
    expect(container.querySelectorAll("path.is-manual")).toHaveLength(0);
    expect(note.value).toBe("待保存草稿");
    expect(onClose).not.toHaveBeenCalled();
    expect(port.save).not.toHaveBeenCalled();
    expect((undo as HTMLButtonElement).disabled).toBe(undoWasDisabled);

    fireEvent.keyDown(window.document, { key: "Escape" });
    expect(screen.getByRole("heading", { name: "关闭创作画布" })).toBeTruthy();
    expect(screen.getByText(/还有未确认的画布编辑/)).toBeTruthy();
    expect(onClose).not.toHaveBeenCalled();
  });

  it.each(["text", "asset", "media"] as const)(
    "keeps Escape, focus, and canvas shortcuts inside the %s source child dialog",
    async (childKind) => {
      const user = userEvent.setup();
      const initialDocument: CanvasDocument = {
        ...document,
        notes: [{ id: "note-a", text: "保留的便签", x: 400, y: 300, width: 230, height: 145 }],
      };
      const snapshot = childKind === "asset"
        ? sourceSnapshot
        : mediaSnapshot("c".repeat(64), "preview.mp4");
      const sourcePort = {
        read: vi.fn(async () => snapshot),
        write: vi.fn(),
      } as unknown as CanvasSourcePort;
      const { port, onClose } = setup(undefined, false, initialDocument, sourcePort);
      await screen.findByText("保留的便签");

      if (childKind === "asset") {
        await user.click(screen.getByRole("article", { name: "便签" }));
        await user.click(screen.getByRole("button", { name: "新增角色素材" }));
      } else {
        await user.click(screen.getByLabelText(childKind === "text" ? "分镜 1" : "分镜预览"));
        const selectedInformation = screen.getByRole("region", { name: "选中内容信息" });
        await user.click(within(selectedInformation).getByRole("button", {
          name: childKind === "text" ? "编辑分镜文字" : "编辑媒体来源",
        }));
      }

      const childDialogName = childKind === "text" ? "编辑分镜文字"
        : childKind === "asset" ? "新增角色" : "编辑预览 URL";
      const childDialog = await screen.findByRole("dialog", { name: childDialogName });
      expect(screen.getAllByRole("dialog")).toHaveLength(2);
      expect(childDialog.contains(window.document.activeElement)).toBe(true);

      const childFocusables = Array.from(childDialog.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ));
      expect(window.document.activeElement).toBe(childFocusables[0]);
      await user.tab({ shift: true });
      expect(window.document.activeElement).toBe(childFocusables.at(-1));
      await user.tab();
      expect(window.document.activeElement).toBe(childFocusables[0]);

      await user.keyboard("{Meta>}s{/Meta}");
      await user.keyboard("{Delete}");
      await user.keyboard("f");
      expect(port.save).not.toHaveBeenCalled();
      expect(sourcePort.write).not.toHaveBeenCalled();
      expect(screen.getAllByText("保留的便签").length).toBeGreaterThan(0);
      expect(screen.getByText("100%")).toBeTruthy();

      await user.keyboard("{Escape}");
      await waitFor(() => expect(screen.queryByRole("dialog", { name: childDialogName })).toBeNull());
      expect(screen.getAllByRole("dialog")).toHaveLength(1);
      expect(onClose).not.toHaveBeenCalled();
      expect(screen.getAllByText("保留的便签").length).toBeGreaterThan(0);

      const selectedNode = childKind === "asset"
        ? screen.getByRole("article", { name: "便签" })
        : screen.getByLabelText(childKind === "media" ? "分镜预览" : "分镜 1");
      expect(selectedNode.classList).toContain("is-selected");
      await user.keyboard("{Escape}");
      expect(selectedNode.classList).not.toContain("is-selected");
      expect(onClose).not.toHaveBeenCalled();
      await user.keyboard("{Escape}");
      expect(onClose).toHaveBeenCalledOnce();
    },
  );

  it("toggles a known hidden relation through undo and guarded save while keeping unknown values", async () => {
    const { port, container } = setup(undefined, false, { ...document, hiddenRelations: ["uses", "future-relationship"] });
    await screen.findByText("门前的来信");
    expect(container.querySelectorAll("svg path")).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "显示 uses 关系" }));
    await waitFor(() => expect(container.querySelectorAll("svg path")).toHaveLength(1));
    fireEvent.click(screen.getByRole("button", { name: "撤销" }));
    await waitFor(() => expect(container.querySelectorAll("svg path")).toHaveLength(0));
    fireEvent.click(screen.getByRole("button", { name: "重做" }));
    await waitFor(() => expect(container.querySelectorAll("svg path")).toHaveLength(1));
    fireEvent.click(screen.getByRole("button", { name: "保存布局" }));
    await waitFor(() => expect(port.save).toHaveBeenCalledOnce());
    expect(vi.mocked(port.save).mock.calls[0]?.[1].hiddenRelations).toEqual(["future-relationship"]);
  });

  it("revokes an image URL minted before the state update commits and the preview unmounts", async () => {
    let resolveRead: ((blob: Blob) => void) | null = null;
    const port: CanvasDataPort = {
      get: vi.fn(), save: vi.fn(),
      readImage: vi.fn(() => new Promise<Blob>((resolve) => { resolveRead = resolve; })),
    };
    const create = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:canvas-preview");
    const revoke = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
    const view = render(<CanvasMediaPreview imageUrl="/uploads/storyboard.png" port={port} scopeKey="scope-a" />);
    await act(async () => {
      resolveRead?.(new Blob([new Uint8Array([1])], { type: "image/png" }));
      await Promise.resolve();
      view.unmount();
    });
    expect(create).toHaveBeenCalledOnce();
    expect(revoke).toHaveBeenCalledWith("blob:canvas-preview");
    create.mockRestore();
    revoke.mockRestore();
  });

  it("releases a failed image decode and reports the preview as unavailable", async () => {
    const port: CanvasDataPort = {
      get: vi.fn(), save: vi.fn(),
      readImage: vi.fn(async () => new Blob([new Uint8Array([1])], { type: "image/png" })),
    };
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:decode-failure");
    const revoke = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
    const view = render(<CanvasMediaPreview imageUrl="/uploads/broken.png" port={port} scopeKey="scope-a" />);
    await waitFor(() => expect(view.container.querySelector("img")).not.toBeNull());
    fireEvent.error(view.container.querySelector("img")!);
    expect(revoke).toHaveBeenCalledWith("blob:decode-failure");
    expect(screen.getByText("图片暂不可读取")).toBeTruthy();
    view.unmount();
    vi.restoreAllMocks();
  });

  it("explains dirty and in-flight close states before allowing the draft to be dropped", async () => {
    const save = vi.fn<CanvasDataPort["save"]>(() => new Promise(() => undefined));
    const { onClose } = setup(save);
    await screen.findByText("门前的来信");
    fireEvent.click(screen.getByRole("button", { name: "新增便签" }));
    fireEvent.change(await screen.findByLabelText("便签文字"), { target: { value: "待保存草稿" } });

    fireEvent.click(screen.getByRole("button", { name: "关闭创作画布" }));
    expect(screen.getByText(/还有未确认的画布编辑/)).toBeTruthy();
    const closePrompt = screen.getByRole("dialog", { name: "关闭创作画布" });
    expect(closePrompt.contains(window.document.activeElement)).toBe(true);
    fireEvent.keyDown(window.document, { key: "s", metaKey: true });
    expect(save).not.toHaveBeenCalled();
    fireEvent.keyDown(window.document, { key: "Escape" });
    expect(screen.queryByRole("heading", { name: "关闭创作画布" })).toBeNull();
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "保存布局" }));
    await waitFor(() => expect(save).toHaveBeenCalledOnce());

    fireEvent.click(screen.getByRole("button", { name: "关闭创作画布" }));
    expect(screen.getByText(/保存请求正在进行/)).toBeTruthy();
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "放弃本地草稿并关闭" }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("uses the first receipt as the base for edits made while that save is in flight", async () => {
    let releaseFirst: ((result: Awaited<ReturnType<CanvasDataPort["save"]>>) => void) | null = null;
    const save = vi.fn<CanvasDataPort["save"]>();
    save.mockImplementationOnce((_chapterId, nextDocument, guard) => new Promise((resolve) => {
      releaseFirst = () => resolve({
        id: guard.id ?? "canvas-a",
        chapter_id: chapter.id,
        version: guard.id === null ? 1 : guard.version + 1,
        document_json: nextDocument,
        updated_by: "actor-a",
        updated_at: "2026-01-02T00:00:00Z",
        save_receipt: {
          mode: "guarded-v1",
          id: guard.id ?? "canvas-a",
          version: guard.id === null ? 1 : guard.version + 1,
        },
      });
    }));
    save.mockImplementation(async (_chapterId, nextDocument, guard) => ({
      id: guard.id ?? "canvas-a",
      chapter_id: chapter.id,
      version: guard.id === null ? 1 : guard.version + 1,
      document_json: nextDocument,
      updated_by: "actor-a",
      updated_at: "2026-01-03T00:00:00Z",
      save_receipt: {
        mode: "guarded-v1",
        id: guard.id ?? "canvas-a",
        version: guard.id === null ? 1 : guard.version + 1,
      },
    }));
    setup(save);
    await screen.findByText("门前的来信");
    fireEvent.click(screen.getByRole("button", { name: "新增便签" }));
    const note = await screen.findByLabelText("便签文字");
    fireEvent.change(note, { target: { value: "第一次保存内容" } });
    fireEvent.click(screen.getByRole("button", { name: "保存布局" }));
    await waitFor(() => expect(save).toHaveBeenCalledOnce());

    vi.useFakeTimers();
    fireEvent.change(note, { target: { value: "保存期间继续编辑" } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    const firstCall = save.mock.calls[0]!;
    await act(async () => {
      releaseFirst?.({
        id: "canvas-a",
        chapter_id: chapter.id,
        version: firstCall[2].version + 1,
        document_json: firstCall[1],
        updated_by: "actor-a",
        updated_at: "2026-01-02T00:00:00Z",
        save_receipt: {
          mode: "guarded-v1",
          id: "canvas-a",
          version: firstCall[2].version + 1,
        },
      });
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(save).toHaveBeenCalledTimes(2);
    const secondCall = save.mock.calls[1]!;
    expect(secondCall[2]).toEqual({ mode: "guarded-v1", id: "canvas-a", version: 3 });
    expect(secondCall[1]?.notes).toEqual([expect.objectContaining({ text: "保存期间继续编辑" })]);
  });

  it("ignores the first effect replay under StrictMode and loads once for the live scope", async () => {
    const { port } = setup(undefined, true);
    expect(await screen.findByText("门前的来信")).toBeTruthy();
    expect(port.get).toHaveBeenCalledTimes(2);
    expect(port.save).not.toHaveBeenCalled();
    expect(screen.getByText("已保存")).toBeTruthy();
  });

  it("records a node drag as one undoable local edit", async () => {
    const { port } = setup();
    await screen.findByText("门前的来信");
    const node = screen.getByLabelText("林澜");
    const viewport = screen.getByRole("region", { name: "画布工作区" });
    expect((node as HTMLElement).style.left).toBe("40px");
    fireEvent.pointerDown(node, { pointerId: 7, button: 0, clientX: 0, clientY: 0 });
    fireEvent.pointerMove(viewport, { pointerId: 7, clientX: 80, clientY: 40 });
    fireEvent.pointerUp(viewport, { pointerId: 7 });

    await waitFor(() => expect((screen.getByLabelText("林澜") as HTMLElement).style.left).toBe("120px"));
    fireEvent.click(screen.getByRole("button", { name: "撤销" }));
    await waitFor(() => expect((screen.getByLabelText("林澜") as HTMLElement).style.left).toBe("40px"));
    expect(screen.getByRole("button", { name: "重做" })).toBeEnabled();
    expect(port.save).not.toHaveBeenCalled();
  });

  it("saves the first canvas drag in the legacy layout-array format", async () => {
    const freshDocument: CanvasDocument = {
      notes: [],
      cards: [],
      manualEdges: [],
      hiddenRelations: [],
      viewport: { x: 0, y: 0, zoom: 1 },
      unknown: { preserve: true },
    };
    const { port } = setup(undefined, false, freshDocument);
    await screen.findByText("门前的来信");
    expect(port.save).not.toHaveBeenCalled();

    const node = screen.getByLabelText("林澜");
    const viewport = screen.getByRole("region", { name: "画布工作区" });
    fireEvent.pointerDown(node, { pointerId: 8, button: 0, clientX: 0, clientY: 0 });
    fireEvent.pointerMove(viewport, { pointerId: 8, clientX: 80, clientY: 40 });
    fireEvent.pointerUp(viewport, { pointerId: 8 });
    fireEvent.click(screen.getByRole("button", { name: "保存布局" }));

    await waitFor(() => expect(port.save).toHaveBeenCalledOnce());
    expect(vi.mocked(port.save).mock.calls[0]?.[1].nodeLayouts).toEqual([
      { refId: "character:character-a", x: 120, y: 90 },
    ]);
    expect(vi.mocked(port.save).mock.calls[0]?.[1].unknown).toEqual({ preserve: true });
  });

  it("does not let a replaced port's late unauthorized save affect the current canvas", async () => {
    let rejectFirst: ((error: Error) => void) | null = null;
    const save = vi.fn<CanvasDataPort["save"]>(() => new Promise((_resolve, reject) => {
      rejectFirst = reject;
    }));
    const { services, onClose, onUnauthorized, rerender } = setup(save);
    await screen.findByText("门前的来信");
    fireEvent.click(screen.getByRole("button", { name: "新增便签" }));
    fireEvent.change(await screen.findByLabelText("便签文字"), { target: { value: "旧端口草稿" } });
    fireEvent.click(screen.getByRole("button", { name: "保存布局" }));
    await waitFor(() => expect(save).toHaveBeenCalledOnce());

    const replacementPort: CanvasDataPort = {
      get: vi.fn(async () => ({
        id: "canvas-b",
        chapter_id: chapter.id,
        version: 1,
        document_json: { ...document, notes: [] },
        updated_by: "actor-a",
        updated_at: "2026-01-04T00:00:00Z",
        save_capability: "guarded-v1" as const,
      })),
      save: vi.fn(),
      readImage: vi.fn(),
    };
    rerender(
      <CanvasWorkspace
        chapter={chapter}
        onClose={onClose}
        onUnauthorized={onUnauthorized}
        onSourceChanged={() => undefined}
        port={replacementPort}
        series={series}
        services={services}
        sessionGeneration={4}
        userId="actor-a"
      />,
    );
    await waitFor(() => expect(screen.getByText("已保存")).toBeTruthy());

    await act(async () => {
      rejectFirst?.(new CanvasDataError("unauthorized", "旧请求已失效。", 401));
    });
    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(screen.getByText("已保存")).toBeTruthy();
    expect(replacementPort.save).not.toHaveBeenCalled();
  });
});
