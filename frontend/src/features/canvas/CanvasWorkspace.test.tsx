// @vitest-environment jsdom
import { StrictMode } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Chapter, Series } from "../../shared/api/contracts";
import { CanvasDataError, type CanvasDataPort } from "../../shared/api/canvasData";
import type { WorkspaceServices } from "../../shared/api/services";
import type { CanvasDocument } from "./canvasDocument";
import { CanvasMediaPreview } from "./CanvasMediaPreview";
import { CanvasWorkspace } from "./CanvasWorkspace";

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

function setup(save?: CanvasDataPort["save"], strictMode = false, initialDocument = document) {
  const port: CanvasDataPort = {
    get: vi.fn(async () => ({
      id: "canvas-a",
      chapter_id: chapter.id,
      version: 2,
      document_json: initialDocument,
      updated_by: "actor-a",
      updated_at: "2026-01-01T00:00:00Z",
      save_capability: "guarded-v1" as const,
    })),
    save: save ?? vi.fn(async (_chapterId, nextDocument, guard) => ({
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
    })),
    readImage: vi.fn(),
  };
  const services = {
    listChapters: vi.fn(async () => [chapter]),
    listStoryboardAssets: vi.fn(async () => []),
    listCharacters: vi.fn(async () => [{
      id: "character-a", series_id: "series-a", name: "林澜", description: "当前角色", image_url: null,
    }]),
    listScenes: vi.fn(async () => []),
    listProps: vi.fn(async () => []),
  } as unknown as WorkspaceServices;
  const onClose = vi.fn();
  const onUnauthorized = vi.fn();
  const canvas = (
    <CanvasWorkspace
      chapter={chapter}
      onClose={onClose}
      onUnauthorized={onUnauthorized}
      port={port}
      series={series}
      services={services}
      sessionGeneration={4}
      userId="actor-a"
    />
  );
  const view = render(strictMode ? <StrictMode>{canvas}</StrictMode> : canvas);
  return { port, services, onClose, onUnauthorized, rerender: view.rerender, container: view.container };
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("CanvasWorkspace", () => {
  it("loads the selected chapter sources, saves a local note through the guarded port, then closes", async () => {
    const { port, services, onClose } = setup();
    expect(await screen.findByText("门前的来信")).toBeTruthy();
    expect(screen.getByLabelText("林澜")).toBeTruthy();
    expect(port.get).toHaveBeenCalledWith("chapter-a", expect.any(AbortSignal));
    expect(services.listChapters).toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "新增便签" }));
    const note = await screen.findByLabelText("便签文字");
    fireEvent.change(note, { target: { value: "雨夜后的线索" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(port.save).toHaveBeenCalledOnce());
    const [, savedDocument, guard] = vi.mocked(port.save).mock.calls[0] ?? [];
    expect(guard).toEqual({ mode: "guarded-v1", id: "canvas-a", version: 2 });
    expect(savedDocument?.notes).toEqual([expect.objectContaining({ id: expect.any(String), text: "雨夜后的线索" })]);
    expect(savedDocument?.unknown).toEqual({ preserve: true });

    fireEvent.click(screen.getByRole("button", { name: "关闭创作画布" }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("keeps a draft on unknown results and stops ordinary saving", async () => {
    const save = vi.fn<CanvasDataPort["save"]>().mockRejectedValue(
      new CanvasDataError("unknown", "unknown"),
    );
    const { onClose } = setup(save);
    await screen.findByText("门前的来信");
    fireEvent.click(screen.getByRole("button", { name: "新增便签" }));
    fireEvent.change(await screen.findByLabelText("便签文字"), { target: { value: "未确认草稿" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    expect(await screen.findByText(/保存结果暂时无法确认/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "保存" })).toBeDisabled();
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
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByText(/保存结果暂时无法确认|已被其他成员更新/);

    vi.useFakeTimers();
    fireEvent.change(note, { target: { value: "核对前继续编辑的草稿" } });
    expect(screen.getByRole("button", { name: "保存" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "撤销" }));
    expect((screen.getByLabelText("便签文字") as HTMLTextAreaElement).value).toBe("原始草稿");
    expect(screen.getByRole("button", { name: "保存" })).toBeDisabled();
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
    const { port, services, onClose, onUnauthorized, rerender } = setup();
    await screen.findByText("门前的来信");
    const replacementServices = {
      ...services,
      listChapters: vi.fn(async () => [chapter]),
    } as unknown as WorkspaceServices;
    rerender(
      <CanvasWorkspace chapter={chapter} onClose={onClose} onUnauthorized={onUnauthorized} port={port}
        series={series} services={replacementServices} sessionGeneration={4} userId="actor-a" />,
    );
    await waitFor(() => expect(port.get).toHaveBeenCalledTimes(2));
    expect(replacementServices.listChapters).toHaveBeenCalledOnce();
  });

  it("closes a clean canvas on Escape without opening a discard prompt", async () => {
    const { onClose } = setup();
    await screen.findByText("门前的来信");
    fireEvent.keyDown(window.document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledOnce();
    expect(screen.queryByRole("heading", { name: "关闭创作画布" })).toBeNull();
  });

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
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
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
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
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
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
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
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

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
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
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
