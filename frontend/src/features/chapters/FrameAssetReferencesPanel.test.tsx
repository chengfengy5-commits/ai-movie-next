import { StrictMode, useLayoutEffect, useState } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Character, Chapter, Prop, Scene, StoryboardFrame } from "../../shared/api/contracts";
import { ApiError } from "../../shared/api/errors";
import type { WorkspaceServices } from "../../shared/api/services";
import {
  FrameAssetReferencesPanel,
  type FrameAssetReferenceAssetTarget,
  type FrameAssetReferenceOwner,
  type FrameAssetReferenceScope,
} from "./FrameAssetReferencesPanel";
import {
  readFrameAssetReferenceScrollTop,
  uniqueStoryboardFrameId,
  writeFrameAssetReferenceScrollTop,
} from "./frameAssetReferenceScrollPreferences";
import {
  createFrameAssetReferencePanelPositionScope,
  defaultFrameAssetReferencePanelPosition,
  readFrameAssetReferencePanelPosition,
  writeFrameAssetReferencePanelPosition,
} from "./frameAssetReferencePanelPositionPreferences";

const seriesId = "reference-panel-series";

function frame(overrides: Record<string, unknown> = {}): StoryboardFrame {
  return {
    text: "镜头内正文",
    character: ["character-1"],
    scene: ["scene-1"],
    prop: ["prop-1"],
    ...overrides,
  };
}

function chapter(frameValue = frame()): Chapter {
  return {
    id: "reference-panel-chapter",
    series_id: seriesId,
    title: "局部引用章节",
    content: [frameValue],
    order: 1,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-02T00:00:00Z",
    lock: null,
  };
}

function character(overrides: Partial<Character> = {}): Character {
  return {
    id: "character-1",
    series_id: seriesId,
    name: "<b>沈照</b>",
    gender: "女",
    age: "二十岁",
    role: "守门人",
    appearance: "青衣短斗篷",
    description: "<script>window.bad = true</script>",
    image_url: "https://media.invalid/character.png",
    audio_url: "https://media.invalid/voice.mp3",
    voice_ref: "private-voice-reference",
    aliases: ["阿照"],
    canonical_key: "internal-canonical-value",
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    ...overrides,
  };
}

function scene(overrides: Partial<Scene> = {}): Scene {
  return {
    id: "scene-1",
    series_id: seriesId,
    title: "盐仓码头",
    description: "夜雾中的旧码头。",
    image_url: null,
    aliases: null,
    canonical_key: null,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    ...overrides,
  };
}

function prop(overrides: Partial<Prop> = {}): Prop {
  return {
    id: "prop-1",
    series_id: seriesId,
    name: "旧铜钥匙",
    description: "齿口有磨损。",
    image_url: null,
    aliases: [],
    canonical_key: null,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    ...overrides,
  };
}

function services(overrides: Partial<WorkspaceServices> = {}): WorkspaceServices {
  return {
    mode: "api",
    apiBaseUrl: "http://127.0.0.1:4175/api",
    restore: async () => null,
    login: async () => { throw new Error("Not used by this panel test."); },
    listSeries: async () => [],
    listMyTeams: async () => [],
    listChapters: async () => [],
    listStoryboardAssets: async () => [],
    listCharacters: vi.fn(async () => [character()]),
    listScenes: vi.fn(async () => [scene()]),
    listProps: vi.fn(async () => [prop()]),
    getPersonalProductionNotes: async () => { throw new Error("Unexpected personal notes request."); },
    getPersonalRoughCut: async () => { throw new Error("Unexpected rough-cut request."); },
    listMyTasks: async () => ({ total: 0, page: 1, page_size: 10, tasks: [] }),
    logout: vi.fn(),
    ...overrides,
  };
}

function createOwner(
  currentChapter = chapter(),
  currentFrame = currentChapter.content?.[0] ?? frame(),
  currentServices = services(),
  openEpoch = 1,
): FrameAssetReferenceOwner {
  return {
    userId: "reference-panel-user",
    seriesId,
    services: currentServices,
    chapter: currentChapter,
    frame: currentFrame,
    position: 1,
    openEpoch,
  };
}

function currentScope(owner: FrameAssetReferenceOwner): FrameAssetReferenceScope {
  return {
    userId: owner.userId,
    seriesId: owner.seriesId,
    services: owner.services,
    chapter: owner.chapter,
    frame: owner.frame,
    position: owner.position,
  };
}

function renderPanel(
  owner: FrameAssetReferenceOwner,
  scope: FrameAssetReferenceScope = currentScope(owner),
  onUnauthorized = vi.fn(),
) {
  return render(
    <FrameAssetReferencesPanel
      currentScope={scope}
      onClose={vi.fn()}
      onUnauthorized={onUnauthorized}
      owner={owner}
    />,
  );
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((complete, fail) => {
    resolve = complete;
    reject = fail;
  });
  return { promise, resolve, reject };
}

function stableStoryboardFrame(id: string, overrides: Record<string, unknown> = {}): StoryboardFrame {
  return frame({ storyboard: [id], ...overrides });
}

function scrollScopeFor(
  owner: FrameAssetReferenceOwner,
  category: "characters" | "scenes" | "props",
) {
  const storyboardId = uniqueStoryboardFrameId(owner.chapter, owner.frame);
  const apiBaseUrl = owner.services.apiBaseUrl;
  if (storyboardId === null || apiBaseUrl === null || apiBaseUrl.trim() === "") {
    throw new Error("Test owner must have a stable storyboard ID and API scope.");
  }
  return {
    userId: owner.userId,
    apiBaseUrl,
    seriesId: owner.seriesId,
    chapterId: owner.chapter.id,
    storyboardId,
    category,
  };
}

function stubAnimationFrames() {
  let nextId = 0;
  const callbacks = new Map<number, FrameRequestCallback>();
  const cancelled = new Set<number>();
  vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
    nextId += 1;
    callbacks.set(nextId, callback);
    return nextId;
  });
  vi.spyOn(window, "cancelAnimationFrame").mockImplementation((id) => {
    cancelled.add(id);
  });
  return {
    latestId: () => nextId,
    isCancelled: (id: number) => cancelled.has(id),
    flush(id: number) {
      const callback = callbacks.get(id);
      if (callback === undefined) return;
      callbacks.delete(id);
      callback(0);
    },
  };
}

function setScrollBounds(element: HTMLElement, scrollHeight: number, clientHeight: number): void {
  Object.defineProperties(element, {
    scrollHeight: { configurable: true, value: scrollHeight },
    clientHeight: { configurable: true, value: clientHeight },
  });
}

function setViewportSize(width: number, height: number): void {
  Object.defineProperty(window, "innerWidth", { configurable: true, value: width });
  Object.defineProperty(window, "innerHeight", { configurable: true, value: height });
}

function setPanelBounds(element: HTMLElement, width: number, height: number): void {
  vi.spyOn(element, "getBoundingClientRect").mockReturnValue(new DOMRect(0, 0, width, height));
}

function stubPointerCapture(element: HTMLElement) {
  const captured = new Set<number>();
  const setPointerCapture = vi.fn((pointerId: number) => captured.add(pointerId));
  const hasPointerCapture = vi.fn((pointerId: number) => captured.has(pointerId));
  const releasePointerCapture = vi.fn((pointerId: number) => { captured.delete(pointerId); });
  Object.defineProperties(element, {
    setPointerCapture: { configurable: true, value: setPointerCapture },
    hasPointerCapture: { configurable: true, value: hasPointerCapture },
    releasePointerCapture: { configurable: true, value: releasePointerCapture },
  });
  return { setPointerCapture, hasPointerCapture, releasePointerCapture };
}

function panelPositionScope(owner: FrameAssetReferenceOwner) {
  const scope = createFrameAssetReferencePanelPositionScope(owner.userId, owner.services.apiBaseUrl);
  if (scope === null) throw new Error("Test owner must have a stable account and API scope.");
  return scope;
}

function LayoutCommitObserver({ onCommit }: { onCommit(): void }) {
  useLayoutEffect(() => {
    onCommit();
  });
  return null;
}

describe("frame asset references panel", () => {
  afterEach(() => {
    cleanup();
    window.localStorage.clear();
    setViewportSize(1024, 768);
  });

  it("does not fetch on open, shows only text, and reads only the selected category", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    const listCharacters = vi.fn(async () => [character()]);
    const listScenes = vi.fn(async () => [scene()]);
    const listProps = vi.fn(async () => [prop()]);
    const serviceSet = services({ listCharacters, listScenes, listProps });
    const owner = createOwner(chapter(), undefined, serviceSet);
    const { container } = renderPanel(owner);

    expect(screen.getByRole("region", { name: "镜头 1 的关联素材" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("请选择素材分类");
    expect(listCharacters).not.toHaveBeenCalled();
    expect(listScenes).not.toHaveBeenCalled();
    expect(listProps).not.toHaveBeenCalled();
    expect(container.querySelector("img, audio, video, a")).toBeNull();
    expect(container.textContent).not.toMatch(/character-1|scene-1|prop-1|canonical|voice|https?:/i);

    await user.click(screen.getByRole("button", { name: "角色" }));
    expect(await screen.findByText("<b>沈照</b>", { selector: "strong" })).toBeInTheDocument();
    expect(screen.getByText("<script>window.bad = true</script>")).toBeInTheDocument();
    expect(screen.getByText("阿照")).toBeInTheDocument();
    expect(screen.getByText("守门人")).toBeInTheDocument();
    expect(listCharacters).toHaveBeenCalledTimes(1);
    expect(listScenes).not.toHaveBeenCalled();
    expect(listProps).not.toHaveBeenCalled();
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(container.querySelector("img, audio, video, a, script")).toBeNull();
    expect(container.textContent).not.toMatch(/character-1|canonical|voice|https?:/i);

    await user.click(screen.getByRole("button", { name: "角色" }));
    expect(listCharacters).toHaveBeenCalledTimes(1);
  });

  it("caches successful categories only for this open and rereads just the current category", async () => {
    const user = userEvent.setup();
    const listCharacters = vi.fn(async () => [character()]);
    const listScenes = vi.fn(async () => [scene()]);
    const listProps = vi.fn(async () => [prop()]);
    const serviceSet = services({ listCharacters, listScenes, listProps });
    const owner = createOwner(chapter(), undefined, serviceSet);
    renderPanel(owner);

    await user.click(screen.getByRole("button", { name: "角色" }));
    expect(await screen.findByText("<b>沈照</b>", { selector: "strong" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "场景" }));
    expect(await screen.findByText("盐仓码头", { selector: "strong" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "道具" }));
    expect(await screen.findByText("旧铜钥匙", { selector: "strong" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "角色" }));

    expect(listCharacters).toHaveBeenCalledTimes(1);
    expect(listScenes).toHaveBeenCalledTimes(1);
    expect(listProps).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "重新读取角色" }));
    expect(await screen.findByText("<b>沈照</b>", { selector: "strong" })).toBeInTheDocument();
    expect(listCharacters).toHaveBeenCalledTimes(2);
    expect(listScenes).toHaveBeenCalledTimes(1);
    expect(listProps).toHaveBeenCalledTimes(1);
  });

  it("shows unlinked and malformed reference states without requesting a directory", async () => {
    const user = userEvent.setup();
    const listCharacters = vi.fn(async () => [character()]);
    const owner = createOwner(chapter(frame({ character: { id: "not-an-array" } })), undefined, services({ listCharacters }));
    renderPanel(owner);

    await user.click(screen.getByRole("button", { name: "角色" }));
    expect(screen.getByRole("status")).toHaveTextContent("引用暂时无法识别");
    expect(listCharacters).not.toHaveBeenCalled();

    const unlinkedOwner = createOwner(chapter(frame({ character: null })), undefined, services({ listCharacters }));
    cleanup();
    renderPanel(unlinkedOwner);
    await user.click(screen.getByRole("button", { name: "角色" }));
    expect(screen.getByRole("status")).toHaveTextContent("未关联角色");
    expect(listCharacters).not.toHaveBeenCalled();
  });

  it("keeps a rejected category local and recovers only after an explicit retry", async () => {
    const user = userEvent.setup();
    const onUnauthorized = vi.fn();
    const listCharacters = vi.fn()
      .mockRejectedValueOnce(new ApiError("http", "failed", 500))
      .mockResolvedValueOnce([character()]);
    const owner = createOwner(chapter(), undefined, services({ listCharacters }));
    renderPanel(owner, currentScope(owner), onUnauthorized);

    await user.click(screen.getByRole("button", { name: "角色" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("暂时无法读取素材");
    expect(onUnauthorized).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "重试读取" }));
    expect(await screen.findByText("<b>沈照</b>", { selector: "strong" })).toBeInTheDocument();
    expect(listCharacters).toHaveBeenCalledTimes(2);
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it.each(["success", "401"] as const)("ignores a stale category %s instead of caching or signing out", async (lateResult) => {
    const user = userEvent.setup();
    const lateCharacters = deferred<Character[]>();
    const listCharacters = vi.fn()
      .mockReturnValueOnce(lateCharacters.promise)
      .mockResolvedValueOnce([character({ name: "当前角色" })]);
    const listScenes = vi.fn(async () => [scene()]);
    const onUnauthorized = vi.fn();
    const owner = createOwner(chapter(), undefined, services({ listCharacters, listScenes }));
    renderPanel(owner, currentScope(owner), onUnauthorized);

    await user.click(screen.getByRole("button", { name: "角色" }));
    expect(screen.getByRole("status")).toHaveTextContent("正在读取角色");
    await user.click(screen.getByRole("button", { name: "场景" }));
    expect(await screen.findByText("盐仓码头", { selector: "strong" })).toBeInTheDocument();
    await act(async () => {
      if (lateResult === "success") {
        lateCharacters.resolve([character({ name: "迟到旧角色" })]);
      } else {
        lateCharacters.reject(new ApiError("http", "expired", 401));
      }
      await lateCharacters.promise.catch(() => undefined);
      await Promise.resolve();
    });
    expect(screen.queryByText("迟到旧角色", { selector: "strong" })).not.toBeInTheDocument();
    expect(onUnauthorized).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "角色" }));
    expect(await screen.findByText("当前角色", { selector: "strong" })).toBeInTheDocument();
    expect(listCharacters).toHaveBeenCalledTimes(2);
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("creates a current category target only for a ready resolved reference", async () => {
    const user = userEvent.setup();
    const onNavigateToAsset = vi.fn();
    const listCharacters = vi.fn(async () => [character()]);
    const listScenes = vi.fn(async () => [scene()]);
    const owner = createOwner(chapter(), undefined, services({ listCharacters, listScenes }));
    render(
      <FrameAssetReferencesPanel
        currentScope={currentScope(owner)}
        onClose={vi.fn()}
        onNavigateToAsset={onNavigateToAsset}
        onUnauthorized={vi.fn()}
        owner={owner}
      />,
    );

    await user.click(screen.getByRole("button", { name: "角色" }));
    await user.click(await screen.findByRole("button", { name: "查看素材：<b>沈照</b>" }));
    expect(onNavigateToAsset).toHaveBeenCalledTimes(1);
    const target = onNavigateToAsset.mock.calls[0]?.[0] as FrameAssetReferenceAssetTarget;
    expect(target).toMatchObject({ seriesId, category: "characters", assetId: "character-1" });
    expect(target.isCurrent()).toBe(true);

    await user.click(screen.getByRole("button", { name: "场景" }));
    await screen.findByText("盐仓码头", { selector: "strong" });
    expect(target.isCurrent()).toBe(false);
    expect(listCharacters).toHaveBeenCalledTimes(1);
    expect(listScenes).toHaveBeenCalledTimes(1);
    expect(onNavigateToAsset).toHaveBeenCalledTimes(1);
  });

  it("keeps a mixed-series duplicate unresolved and does not expose a navigation action", async () => {
    const user = userEvent.setup();
    const owner = createOwner(chapter(), undefined, services({
      listCharacters: vi.fn(async () => [
        character(),
        character({ series_id: "another-series", name: "同 ID 的外剧集素材" }),
      ]),
    }));
    render(
      <FrameAssetReferencesPanel
        currentScope={currentScope(owner)}
        onClose={vi.fn()}
        onNavigateToAsset={vi.fn()}
        onUnauthorized={vi.fn()}
        owner={owner}
      />,
    );

    await user.click(screen.getByRole("button", { name: "角色" }));
    expect(await screen.findByText("无法对应当前素材", { selector: "strong" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /查看素材/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/character-1|another-series/)).not.toBeInTheDocument();
  });

  it("sends only a current 401 to the existing session handler", async () => {
    const user = userEvent.setup();
    const onUnauthorized = vi.fn();
    const listCharacters = vi.fn().mockRejectedValue(new ApiError("http", "expired", 401));
    const owner = createOwner(chapter(), undefined, services({ listCharacters }));
    renderPanel(owner, currentScope(owner), onUnauthorized);

    await user.click(screen.getByRole("button", { name: "角色" }));
    await waitFor(() => expect(onUnauthorized).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(listCharacters).toHaveBeenCalledTimes(1);
  });

  it("ignores a late category 401 after the panel closes", async () => {
    const user = userEvent.setup();
    const pending = deferred<Character[]>();
    const onUnauthorized = vi.fn();
    const listCharacters = vi.fn().mockReturnValue(pending.promise);
    const owner = createOwner(chapter(), undefined, services({ listCharacters }));
    const onClose = vi.fn();
    const view = render(
      <FrameAssetReferencesPanel
        currentScope={currentScope(owner)}
        onClose={onClose}
        onUnauthorized={onUnauthorized}
        owner={owner}
      />,
    );

    await user.click(screen.getByRole("button", { name: "角色" }));
    expect(screen.getByRole("status")).toHaveTextContent("正在读取角色");
    await user.click(screen.getByRole("button", { name: "关闭" }));
    expect(onClose).toHaveBeenCalledWith(owner);
    await act(async () => {
      pending.reject(new ApiError("http", "expired", 401));
      await pending.promise.catch(() => undefined);
      await Promise.resolve();
    });
    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(view.container.querySelector(".frame-asset-references-panel")).toBeNull();
  });

  it.each(["success", "401"] as const)(
    "permanently closes on a user A→B→A scope change and ignores the old %s",
    async (lateResult) => {
      const user = userEvent.setup();
      const pending = deferred<Character[]>();
      const listCharacters = vi.fn(() => pending.promise);
      const serviceSet = services({ listCharacters });
      const ownerA = createOwner(chapter(), undefined, serviceSet, 1);
      const onUnauthorized = vi.fn();
      const commits: boolean[] = [];
      const observeCommit = () => {
        commits.push(screen.queryByRole("region", { name: "镜头 1 的关联素材" }) !== null);
      };
      const renderForUser = (userId: string) => (
        <>
          <FrameAssetReferencesPanel
            currentScope={{ ...currentScope(ownerA), userId }}
            onClose={vi.fn()}
            onUnauthorized={onUnauthorized}
            owner={ownerA}
          />
          <LayoutCommitObserver onCommit={observeCommit} />
        </>
      );
      const view = render(renderForUser("reference-panel-user"));

      await user.click(screen.getByRole("button", { name: "角色" }));
      expect(screen.getByRole("status")).toHaveTextContent("正在读取角色");

      const beforeUserChange = commits.length;
      view.rerender(renderForUser("another-user"));
      expect(commits[beforeUserChange]).toBe(false);
      expect(screen.queryByRole("region", { name: "镜头 1 的关联素材" })).not.toBeInTheDocument();

      const beforeReturn = commits.length;
      view.rerender(renderForUser("reference-panel-user"));
      expect(commits[beforeReturn]).toBe(false);
      expect(screen.queryByRole("button", { name: "角色" })).not.toBeInTheDocument();

      await act(async () => {
        if (lateResult === "success") {
          pending.resolve([character({ name: "旧用户迟到角色" })]);
          await pending.promise;
        } else {
          pending.reject(new ApiError("http", "expired", 401));
          await pending.promise.catch(() => undefined);
        }
        await Promise.resolve();
      });

      expect(screen.queryByText("旧用户迟到角色", { selector: "strong" })).not.toBeInTheDocument();
      expect(screen.queryByRole("region", { name: "镜头 1 的关联素材" })).not.toBeInTheDocument();
      expect(listCharacters).toHaveBeenCalledTimes(1);
      expect(onUnauthorized).not.toHaveBeenCalled();
    },
  );

  it.each(["success", "401"] as const)(
    "does not let an older reread %s replace the latest category result",
    async (lateResult) => {
      const user = userEvent.setup();
      const staleReread = deferred<Character[]>();
      const latestReread = deferred<Character[]>();
      const listCharacters = vi.fn()
        .mockResolvedValueOnce([character({ name: "初次角色" })])
        .mockReturnValueOnce(staleReread.promise)
        .mockReturnValueOnce(latestReread.promise);
      const onUnauthorized = vi.fn();
      const owner = createOwner(chapter(), undefined, services({ listCharacters }));
      renderPanel(owner, currentScope(owner), onUnauthorized);

      await user.click(screen.getByRole("button", { name: "角色" }));
      expect(await screen.findByText("初次角色", { selector: "strong" })).toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "重新读取角色" }));
      await waitFor(() => expect(listCharacters).toHaveBeenCalledTimes(2));
      await user.click(screen.getByRole("button", { name: "重新读取角色" }));
      await waitFor(() => expect(listCharacters).toHaveBeenCalledTimes(3));

      await act(async () => {
        latestReread.resolve([character({ name: "最新角色" })]);
        await latestReread.promise;
      });
      expect(await screen.findByText("最新角色", { selector: "strong" })).toBeInTheDocument();

      await act(async () => {
        if (lateResult === "success") {
          staleReread.resolve([character({ name: "较早重读角色" })]);
          await staleReread.promise;
        } else {
          staleReread.reject(new ApiError("http", "expired", 401));
          await staleReread.promise.catch(() => undefined);
        }
        await Promise.resolve();
      });
      expect(screen.getByText("最新角色", { selector: "strong" })).toBeInTheDocument();
      expect(screen.queryByText("较早重读角色", { selector: "strong" })).not.toBeInTheDocument();
      expect(onUnauthorized).not.toHaveBeenCalled();

      await user.click(screen.getByRole("button", { name: "场景" }));
      expect(await screen.findByText("盐仓码头", { selector: "strong" })).toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "角色" }));
      expect(screen.getByText("最新角色", { selector: "strong" })).toBeInTheDocument();
      expect(screen.queryByText("较早重读角色", { selector: "strong" })).not.toBeInTheDocument();
      expect(listCharacters).toHaveBeenCalledTimes(3);
    },
  );

  it.each(["success", "401"] as const)(
    "starts a fresh category request after close and reopen despite a late %s",
    async (lateResult) => {
      const user = userEvent.setup();
      const oldRequest = deferred<Character[]>();
      const listCharacters = vi.fn()
        .mockReturnValueOnce(oldRequest.promise)
        .mockResolvedValueOnce([character({ name: "新打开后的角色" })]);
      const onUnauthorized = vi.fn();
      const owner = createOwner(chapter(), undefined, services({ listCharacters }));

      function ReopenablePanel() {
        const [open, setOpen] = useState(true);
        return open ? (
          <FrameAssetReferencesPanel
            currentScope={currentScope(owner)}
            onClose={() => setOpen(false)}
            onUnauthorized={onUnauthorized}
            owner={owner}
          />
        ) : (
          <button onClick={() => setOpen(true)} type="button">重新打开关联素材</button>
        );
      }

      render(<ReopenablePanel />);
      await user.click(screen.getByRole("button", { name: "角色" }));
      await waitFor(() => expect(listCharacters).toHaveBeenCalledTimes(1));
      await user.click(screen.getByRole("button", { name: "关闭" }));
      await user.click(screen.getByRole("button", { name: "重新打开关联素材" }));
      await user.click(screen.getByRole("button", { name: "角色" }));
      expect(await screen.findByText("新打开后的角色", { selector: "strong" })).toBeInTheDocument();
      expect(listCharacters).toHaveBeenCalledTimes(2);

      await act(async () => {
        if (lateResult === "success") {
          oldRequest.resolve([character({ name: "旧面板迟到角色" })]);
          await oldRequest.promise;
        } else {
          oldRequest.reject(new ApiError("http", "expired", 401));
          await oldRequest.promise.catch(() => undefined);
        }
        await Promise.resolve();
      });

      expect(screen.getByText("新打开后的角色", { selector: "strong" })).toBeInTheDocument();
      expect(screen.queryByText("旧面板迟到角色", { selector: "strong" })).not.toBeInTheDocument();
      expect(listCharacters).toHaveBeenCalledTimes(2);
      expect(onUnauthorized).not.toHaveBeenCalled();
    },
  );

  it("remains usable when effects are replayed by React StrictMode", async () => {
    const user = userEvent.setup();
    const listCharacters = vi.fn(async () => [character()]);
    const owner = createOwner(chapter(), undefined, services({ listCharacters }));
    render(
      <StrictMode>
        <FrameAssetReferencesPanel
          currentScope={currentScope(owner)}
          onClose={vi.fn()}
          onUnauthorized={vi.fn()}
          owner={owner}
        />
      </StrictMode>,
    );

    expect(screen.getByRole("region", { name: "镜头 1 的关联素材" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "角色" }));
    expect(await screen.findByText("<b>沈照</b>", { selector: "strong" })).toBeInTheDocument();
    expect(listCharacters).toHaveBeenCalledTimes(1);
  });

  it.each(["services", "chapter"] as const)(
    "hides the panel on the first %s scope commit and remains closed through A→B→A",
    async (changedScope) => {
      const user = userEvent.setup();
      const serviceSet = services();
      const owner = createOwner(chapter(), undefined, serviceSet);
      const commits: boolean[] = [];
      const observeCommit = () => commits.push(screen.queryByRole("region", { name: "镜头 1 的关联素材" }) !== null);
      const view = render(
        <>
          <FrameAssetReferencesPanel
            currentScope={currentScope(owner)}
            onClose={vi.fn()}
            onUnauthorized={vi.fn()}
            owner={owner}
          />
          <LayoutCommitObserver onCommit={observeCommit} />
        </>,
      );
      await user.click(screen.getByRole("button", { name: "角色" }));
      expect(await screen.findByText("<b>沈照</b>", { selector: "strong" })).toBeInTheDocument();
      const commitsBeforeChange = commits.length;

      const changed = changedScope === "services"
        ? { ...currentScope(owner), services: services() }
        : { ...currentScope(owner), chapter: { ...owner.chapter } };
      view.rerender(
        <>
          <FrameAssetReferencesPanel
            currentScope={changed}
            onClose={vi.fn()}
            onUnauthorized={vi.fn()}
            owner={owner}
          />
          <LayoutCommitObserver onCommit={observeCommit} />
        </>,
      );
      expect(commits[commitsBeforeChange]).toBe(false);
      expect(screen.queryByRole("region", { name: "镜头 1 的关联素材" })).not.toBeInTheDocument();

      const commitsBeforeReturn = commits.length;
      view.rerender(
        <>
          <FrameAssetReferencesPanel
            currentScope={currentScope(owner)}
            onClose={vi.fn()}
            onUnauthorized={vi.fn()}
            owner={owner}
          />
          <LayoutCommitObserver onCommit={observeCommit} />
        </>,
      );
      expect(commits[commitsBeforeReturn]).toBe(false);
      expect(screen.queryByRole("button", { name: "角色" })).not.toBeInTheDocument();
    },
  );

  it("restores the selected frame list after reopening and clamps a stable frame after reordering", async () => {
    const user = userEvent.setup();
    const characterIds = Array.from({ length: 12 }, (_, index) => `scroll-character-${index + 1}`);
    const targetFrame = stableStoryboardFrame("scroll-frame-stable", { character: characterIds });
    const otherFrame = stableStoryboardFrame("scroll-frame-other", { character: [], scene: [], prop: [] });
    const firstChapter = { ...chapter(targetFrame), content: [targetFrame, otherFrame] };
    const serviceSet = services({
      listCharacters: vi.fn(async () => characterIds.map((id, index) => character({ id, name: `角色 ${index + 1}` }))),
    });
    const firstOwner = createOwner(firstChapter, targetFrame, serviceSet, 1);
    const animationFrames = stubAnimationFrames();
    const firstPanel = renderPanel(firstOwner);

    await user.click(screen.getByRole("button", { name: "角色" }));
    expect(await screen.findByText("角色 12", { selector: "strong" })).toBeInTheDocument();
    const firstViewport = screen.getByRole("region", { name: "关联素材列表" });
    setScrollBounds(firstViewport, 760, 180);
    firstViewport.scrollTop = 240;
    fireEvent.scroll(firstViewport);
    expect(readFrameAssetReferenceScrollTop(scrollScopeFor(firstOwner, "characters")))
      .toEqual({ kind: "stored", scrollTop: 240 });
    firstPanel.unmount();

    const reorderedChapter = { ...firstChapter, content: [otherFrame, targetFrame] };
    const reopenedOwner = { ...createOwner(reorderedChapter, targetFrame, serviceSet, 2), position: 2 };
    const reopenedPanel = renderPanel(reopenedOwner);
    await user.click(screen.getByRole("button", { name: "角色" }));
    expect(await screen.findByText("角色 12", { selector: "strong" })).toBeInTheDocument();
    const reopenedViewport = screen.getByRole("region", { name: "关联素材列表" });
    setScrollBounds(reopenedViewport, 340, 180);
    const restoreFrame = animationFrames.latestId();
    expect(restoreFrame).toBeGreaterThan(0);

    act(() => animationFrames.flush(restoreFrame));
    expect(reopenedViewport.scrollTop).toBe(160);
    expect(readFrameAssetReferenceScrollTop(scrollScopeFor(reopenedOwner, "characters")))
      .toEqual({ kind: "stored", scrollTop: 240 });
    reopenedPanel.unmount();
  });

  it("keeps manual scroll and category positions when a prior restore callback arrives late", async () => {
    const user = userEvent.setup();
    const sourceFrame = stableStoryboardFrame("scroll-frame-category", {
      character: ["character-1"],
      scene: ["scene-1"],
    });
    const owner = createOwner(chapter(sourceFrame), sourceFrame, services());
    const characterScope = scrollScopeFor(owner, "characters");
    const sceneScope = scrollScopeFor(owner, "scenes");
    expect(writeFrameAssetReferenceScrollTop(characterScope, 140)).toBe("saved");
    expect(writeFrameAssetReferenceScrollTop(sceneScope, 60)).toBe("saved");
    const animationFrames = stubAnimationFrames();
    renderPanel(owner);

    await user.click(screen.getByRole("button", { name: "角色" }));
    expect(await screen.findByText("<b>沈照</b>", { selector: "strong" })).toBeInTheDocument();
    const characterViewport = screen.getByRole("region", { name: "关联素材列表" });
    setScrollBounds(characterViewport, 500, 160);
    const characterRestoreFrame = animationFrames.latestId();

    fireEvent.wheel(characterViewport);
    characterViewport.scrollTop = 42;
    fireEvent.scroll(characterViewport);
    expect(readFrameAssetReferenceScrollTop(characterScope)).toEqual({ kind: "stored", scrollTop: 42 });
    act(() => animationFrames.flush(characterRestoreFrame));
    expect(characterViewport.scrollTop).toBe(42);

    await user.click(screen.getByRole("button", { name: "场景" }));
    expect(await screen.findByText("盐仓码头", { selector: "strong" })).toBeInTheDocument();
    const sceneViewport = screen.getByRole("region", { name: "关联素材列表" });
    setScrollBounds(sceneViewport, 500, 160);
    const sceneRestoreFrame = animationFrames.latestId();
    act(() => animationFrames.flush(characterRestoreFrame));
    expect(sceneViewport.scrollTop).toBe(0);
    act(() => animationFrames.flush(sceneRestoreFrame));
    expect(sceneViewport.scrollTop).toBe(60);
  });

  it.each(["missing", "duplicate"] as const)(
    "does not save or restore scroll when the stable frame ID is %s",
    async (identityState) => {
      const user = userEvent.setup();
      const targetFrame = identityState === "missing"
        ? frame({ character: ["character-1"] })
        : stableStoryboardFrame("duplicate-frame-id", { character: ["character-1"] });
      const frames = identityState === "duplicate"
        ? [targetFrame, stableStoryboardFrame("duplicate-frame-id", { character: [] })]
        : [targetFrame];
      const owner = createOwner({ ...chapter(targetFrame), content: frames }, targetFrame, services());
      renderPanel(owner);

      await user.click(screen.getByRole("button", { name: "角色" }));
      expect(await screen.findByText("<b>沈照</b>", { selector: "strong" })).toBeInTheDocument();
      const viewport = screen.getByRole("region", { name: "关联素材列表" });
      setScrollBounds(viewport, 500, 160);
      viewport.scrollTop = 75;
      fireEvent.scroll(viewport);

      expect(await screen.findByText("当前账号、接口或镜头身份不完整，列表仍可滚动，但不会记住位置。"))
        .toBeInTheDocument();
      expect(window.localStorage.length).toBe(0);
    },
  );

  it("keeps the selected list usable without a stable API scope", async () => {
    const user = userEvent.setup();
    const sourceFrame = stableStoryboardFrame("scroll-frame-without-api", { character: ["character-1"] });
    const owner = createOwner(chapter(sourceFrame), sourceFrame, services({ apiBaseUrl: null }));
    renderPanel(owner);

    await user.click(screen.getByRole("button", { name: "角色" }));
    expect(await screen.findByText("<b>沈照</b>", { selector: "strong" })).toBeInTheDocument();
    const viewport = screen.getByRole("region", { name: "关联素材列表" });
    setScrollBounds(viewport, 500, 160);
    viewport.scrollTop = 75;
    fireEvent.scroll(viewport);

    expect(await screen.findByText("当前账号、接口或镜头身份不完整，列表仍可滚动，但不会记住位置。"))
      .toBeInTheDocument();
    expect(viewport.scrollTop).toBe(75);
    expect(window.localStorage.length).toBe(0);
  });

  it("does not apply a noncooperative restore after the owner scope changes", async () => {
    const user = userEvent.setup();
    const sourceFrame = stableStoryboardFrame("scroll-frame-stale", { character: ["character-1"] });
    const pendingCharacters = deferred<Character[]>();
    const owner = createOwner(chapter(sourceFrame), sourceFrame, services({
      listCharacters: vi.fn(() => pendingCharacters.promise),
    }));
    const preferenceScope = scrollScopeFor(owner, "characters");
    expect(writeFrameAssetReferenceScrollTop(preferenceScope, 180)).toBe("saved");
    const animationFrames = stubAnimationFrames();
    const panel = renderPanel(owner);

    await user.click(screen.getByRole("button", { name: "角色" }));
    expect(screen.getByRole("status")).toHaveTextContent("正在读取角色");
    await act(async () => {
      pendingCharacters.resolve([character()]);
      await pendingCharacters.promise;
    });
    expect(await screen.findByText("<b>沈照</b>", { selector: "strong" })).toBeInTheDocument();
    const oldViewport = screen.getByRole("region", { name: "关联素材列表" });
    setScrollBounds(oldViewport, 500, 160);
    const restoreFrame = animationFrames.latestId();

    panel.rerender(
      <FrameAssetReferencesPanel
        currentScope={{ ...currentScope(owner), userId: "other-scroll-owner" }}
        onClose={vi.fn()}
        onUnauthorized={vi.fn()}
        owner={owner}
      />,
    );
    expect(screen.queryByRole("region", { name: "镜头 1 的关联素材" })).not.toBeInTheDocument();
    expect(animationFrames.isCancelled(restoreFrame)).toBe(true);
    act(() => animationFrames.flush(restoreFrame));

    expect(oldViewport.scrollTop).toBe(0);
    expect(readFrameAssetReferenceScrollTop(preferenceScope)).toEqual({ kind: "stored", scrollTop: 180 });
  });

  it("switches presentation without remounting the panel or rereading its selected category", async () => {
    const user = userEvent.setup();
    const sourceFrame = stableStoryboardFrame("floating-frame-inline", { character: ["character-1"] });
    const listCharacters = vi.fn(async () => [character()]);
    const owner = createOwner(chapter(sourceFrame), sourceFrame, services({ listCharacters }));
    const rendered = renderPanel(owner);

    await user.click(screen.getByRole("button", { name: "角色" }));
    expect(await screen.findByText("<b>沈照</b>", { selector: "strong" })).toBeInTheDocument();
    const panelElement = screen.getByRole("region", { name: "镜头 1 的关联素材" });
    const viewport = screen.getByRole("region", { name: "关联素材列表" });
    setPanelBounds(panelElement, 360, 320);
    setScrollBounds(viewport, 600, 180);
    viewport.scrollTop = 92;
    fireEvent.scroll(viewport);

    await user.click(screen.getByRole("button", { name: "浮动查看" }));
    expect(screen.getByRole("region", { name: "镜头 1 的关联素材" })).toBe(panelElement);
    expect(screen.getByRole("region", { name: "关联素材列表" })).toBe(viewport);
    expect(panelElement).toHaveClass("is-floating");
    expect(viewport.scrollTop).toBe(92);

    await user.click(screen.getByRole("button", { name: "回到镜头内" }));
    expect(screen.getByRole("region", { name: "镜头 1 的关联素材" })).toBe(panelElement);
    expect(panelElement).not.toHaveClass("is-floating");
    expect(viewport.scrollTop).toBe(92);
    expect(listCharacters).toHaveBeenCalledOnce();
    expect(rendered.container.querySelectorAll(".frame-asset-reference-scroll-viewport")).toHaveLength(1);
  });

  it("reads saved coordinates only after explicit floating and clamps to measured bounds without autosaving", async () => {
    const user = userEvent.setup();
    setViewportSize(1280, 720);
    const sourceFrame = stableStoryboardFrame("floating-frame-clamp");
    const owner = createOwner(chapter(sourceFrame), sourceFrame, services());
    const scope = panelPositionScope(owner);
    expect(writeFrameAssetReferencePanelPosition(scope, { left: 2000, top: 1000 })).toBe("saved");
    renderPanel(owner);
    const panelElement = screen.getByRole("region", { name: "镜头 1 的关联素材" });
    setPanelBounds(panelElement, 360, 600);

    expect(panelElement).not.toHaveClass("is-floating");
    await user.click(screen.getByRole("button", { name: "浮动查看" }));
    expect(panelElement).toHaveClass("is-floating");
    expect(panelElement.style.left).toBe("900px");
    expect(panelElement.style.top).toBe("100px");
    expect(readFrameAssetReferencePanelPosition(scope)).toEqual({
      kind: "stored",
      position: { left: 2000, top: 1000 },
    });

    await user.click(screen.getByRole("button", { name: "回到镜头内" }));
    await user.click(screen.getByRole("button", { name: "浮动查看" }));
    expect(panelElement.style.left).toBe("900px");
    expect(panelElement.style.top).toBe("100px");
  });

  it("restores the original desktop position after a narrower viewport temporarily clamps it", async () => {
    const user = userEvent.setup();
    setViewportSize(1400, 900);
    const sourceFrame = stableStoryboardFrame("floating-frame-resize-projection");
    const owner = createOwner(chapter(sourceFrame), sourceFrame, services());
    const scope = panelPositionScope(owner);
    expect(writeFrameAssetReferencePanelPosition(scope, { left: 900, top: 100 })).toBe("saved");
    renderPanel(owner);
    const panelElement = screen.getByRole("region", { name: "镜头 1 的关联素材" });
    setPanelBounds(panelElement, 360, 300);

    await user.click(screen.getByRole("button", { name: "浮动查看" }));
    expect(panelElement.style.left).toBe("900px");
    act(() => {
      setViewportSize(1000, 900);
      window.dispatchEvent(new Event("resize"));
    });
    expect(panelElement.style.left).toBe("620px");
    expect(readFrameAssetReferencePanelPosition(scope)).toEqual({
      kind: "stored",
      position: { left: 900, top: 100 },
    });

    act(() => {
      setViewportSize(1400, 900);
      window.dispatchEvent(new Event("resize"));
    });
    expect(panelElement.style.left).toBe("900px");
    expect(panelElement.style.top).toBe("100px");
    expect(readFrameAssetReferencePanelPosition(scope)).toEqual({
      kind: "stored",
      position: { left: 900, top: 100 },
    });
  });

  it("reclamps visually when selected category data makes the floating panel taller", async () => {
    const user = userEvent.setup();
    setViewportSize(1400, 900);
    const sourceFrame = stableStoryboardFrame("floating-frame-content-growth", { character: ["character-1"] });
    const pendingCharacters = deferred<Character[]>();
    const owner = createOwner(chapter(sourceFrame), sourceFrame, services({
      listCharacters: vi.fn(() => pendingCharacters.promise),
    }));
    const scope = panelPositionScope(owner);
    expect(writeFrameAssetReferencePanelPosition(scope, { left: 600, top: 500 })).toBe("saved");
    renderPanel(owner);
    const panelElement = screen.getByRole("region", { name: "镜头 1 的关联素材" });
    let measuredHeight = 300;
    vi.spyOn(panelElement, "getBoundingClientRect").mockImplementation(() => new DOMRect(0, 0, 360, measuredHeight));

    await user.click(screen.getByRole("button", { name: "浮动查看" }));
    expect(panelElement.style.top).toBe("500px");
    const dragHandle = screen.getByRole("button", { name: "拖动关联素材浮窗；也可按方向键移动" });
    stubPointerCapture(dragHandle);
    fireEvent.pointerDown(dragHandle, { pointerId: 9, button: 0, clientX: 10, clientY: 10 });
    fireEvent.pointerMove(dragHandle, { pointerId: 9, clientX: 10, clientY: 30 });
    await user.click(screen.getByRole("button", { name: "角色" }));
    expect(screen.getByRole("status")).toHaveTextContent("正在读取角色");
    measuredHeight = 700;
    await act(async () => {
      pendingCharacters.resolve([character()]);
      await pendingCharacters.promise;
    });

    expect(await screen.findByText("<b>沈照</b>", { selector: "strong" })).toBeInTheDocument();
    expect(panelElement.style.top).toBe("180px");
    fireEvent.pointerCancel(dragHandle, { pointerId: 9 });
    expect(panelElement.style.top).toBe("180px");
    expect(readFrameAssetReferencePanelPosition(scope)).toEqual({
      kind: "stored",
      position: { left: 600, top: 500 },
    });
  });

  it("saves only on a captured pointerup and restores the position when explicitly floated again", async () => {
    const user = userEvent.setup();
    setViewportSize(1400, 900);
    const sourceFrame = stableStoryboardFrame("floating-frame-drag");
    const owner = createOwner(chapter(sourceFrame), sourceFrame, services());
    const scope = panelPositionScope(owner);
    renderPanel(owner);
    const panelElement = screen.getByRole("region", { name: "镜头 1 的关联素材" });
    setPanelBounds(panelElement, 360, 300);

    await user.click(screen.getByRole("button", { name: "浮动查看" }));
    expect(panelElement.style.left).toBe("640px");
    expect(panelElement.style.top).toBe("60px");
    const handle = screen.getByRole("button", { name: "拖动关联素材浮窗；也可按方向键移动" });
    const capture = stubPointerCapture(handle);
    const resetButton = screen.getByRole("button", { name: "恢复默认位置" });
    fireEvent.pointerDown(resetButton, { pointerId: 1, button: 0, clientX: 10, clientY: 10 });
    expect(capture.setPointerCapture).not.toHaveBeenCalled();

    fireEvent.pointerDown(handle, { pointerId: 7, button: 0, clientX: 100, clientY: 80 });
    fireEvent.pointerMove(handle, { pointerId: 7, clientX: 200, clientY: 160 });
    expect(panelElement.style.left).toBe("740px");
    expect(panelElement.style.top).toBe("140px");
    expect(readFrameAssetReferencePanelPosition(scope)).toEqual({ kind: "missing" });
    fireEvent.pointerUp(handle, { pointerId: 7, clientX: 200, clientY: 160 });
    expect(capture.setPointerCapture).toHaveBeenCalledWith(7);
    expect(capture.releasePointerCapture).toHaveBeenCalledWith(7);
    expect(readFrameAssetReferencePanelPosition(scope)).toEqual({
      kind: "stored",
      position: { left: 740, top: 140 },
    });

    await user.click(screen.getByRole("button", { name: "回到镜头内" }));
    await user.click(screen.getByRole("button", { name: "浮动查看" }));
    expect(panelElement.style.left).toBe("740px");
    expect(panelElement.style.top).toBe("140px");
  });

  it("keeps floating usable without account or API identity and explains why it will not persist", async () => {
    const user = userEvent.setup();
    setViewportSize(1400, 900);
    const sourceFrame = stableStoryboardFrame("floating-frame-unscoped");
    const owner = {
      ...createOwner(chapter(sourceFrame), sourceFrame, services({ apiBaseUrl: null })),
      userId: " ",
    };
    renderPanel(owner);
    const panelElement = screen.getByRole("region", { name: "镜头 1 的关联素材" });
    setPanelBounds(panelElement, 360, 300);
    await user.click(screen.getByRole("button", { name: "浮动查看" }));
    expect(screen.getByText("当前账号或接口身份不完整，浮窗仍可使用，但位置不会记住。"))
      .toBeInTheDocument();

    const handle = screen.getByRole("button", { name: "拖动关联素材浮窗；也可按方向键移动" });
    stubPointerCapture(handle);
    fireEvent.pointerDown(handle, { pointerId: 21, button: 0, clientX: 0, clientY: 0 });
    fireEvent.pointerMove(handle, { pointerId: 21, clientX: 40, clientY: 50 });
    expect(panelElement.style.left).toBe("680px");
    expect(panelElement.style.top).toBe("110px");
    fireEvent.pointerUp(handle, { pointerId: 21, clientX: 40, clientY: 50 });
    expect(window.localStorage.length).toBe(0);
  });

  it("keeps floating usable and reports local-storage read and write failures", async () => {
    const user = userEvent.setup();
    setViewportSize(1400, 900);
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("Storage blocked."); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("Storage blocked."); });
    const sourceFrame = stableStoryboardFrame("floating-frame-storage-denied");
    const owner = createOwner(chapter(sourceFrame), sourceFrame, services());
    renderPanel(owner);
    const panelElement = screen.getByRole("region", { name: "镜头 1 的关联素材" });
    setPanelBounds(panelElement, 360, 300);
    await user.click(screen.getByRole("button", { name: "浮动查看" }));
    expect(screen.getByText("浏览器未能读取已保存位置；已使用默认位置，可继续移动。"))
      .toBeInTheDocument();

    const handle = screen.getByRole("button", { name: "拖动关联素材浮窗；也可按方向键移动" });
    stubPointerCapture(handle);
    fireEvent.pointerDown(handle, { pointerId: 22, button: 0, clientX: 0, clientY: 0 });
    fireEvent.pointerMove(handle, { pointerId: 22, clientX: 40, clientY: 50 });
    fireEvent.pointerUp(handle, { pointerId: 22, clientX: 40, clientY: 50 });
    expect(panelElement.style.left).toBe("680px");
    expect(panelElement.style.top).toBe("110px");
    expect(screen.getByText("浏览器未能记住浮窗位置；仍可继续使用和移动。"))
      .toBeInTheDocument();
  });

  it("cancels a mobile drag without overwriting the desktop position and restores it on resize", async () => {
    const user = userEvent.setup();
    setViewportSize(1400, 900);
    const sourceFrame = stableStoryboardFrame("floating-frame-mobile");
    const owner = createOwner(chapter(sourceFrame), sourceFrame, services());
    const scope = panelPositionScope(owner);
    expect(writeFrameAssetReferencePanelPosition(scope, { left: 600, top: 100 })).toBe("saved");
    renderPanel(owner);
    const panelElement = screen.getByRole("region", { name: "镜头 1 的关联素材" });
    setPanelBounds(panelElement, 360, 300);
    await user.click(screen.getByRole("button", { name: "浮动查看" }));
    const handle = screen.getByRole("button", { name: "拖动关联素材浮窗；也可按方向键移动" });
    const capture = stubPointerCapture(handle);
    fireEvent.pointerDown(handle, { pointerId: 3, button: 0, clientX: 10, clientY: 10 });
    fireEvent.pointerMove(handle, { pointerId: 3, clientX: 90, clientY: 70 });
    expect(panelElement.style.left).toBe("680px");

    act(() => {
      setViewportSize(700, 900);
      window.dispatchEvent(new Event("resize"));
    });
    expect(panelElement).not.toHaveClass("is-floating");
    expect(screen.getByText("小屏幕下暂时显示在镜头内，桌面位置已保留。")).toBeInTheDocument();
    expect(capture.releasePointerCapture).toHaveBeenCalledWith(3);
    expect(readFrameAssetReferencePanelPosition(scope)).toEqual({
      kind: "stored",
      position: { left: 600, top: 100 },
    });

    act(() => {
      setViewportSize(1400, 900);
      window.dispatchEvent(new Event("resize"));
    });
    expect(panelElement).toHaveClass("is-floating");
    expect(panelElement.style.left).toBe("600px");
    expect(panelElement.style.top).toBe("100px");
    expect(readFrameAssetReferencePanelPosition(scope)).toEqual({
      kind: "stored",
      position: { left: 600, top: 100 },
    });
  });

  it.each(["pointercancel", "close", "scope change"] as const)(
    "does not persist a canceled drag or accept its late pointerup after %s",
    async (cancelReason) => {
      const user = userEvent.setup();
      setViewportSize(1400, 900);
      const sourceFrame = stableStoryboardFrame(`floating-frame-cancel-${cancelReason}`);
      const owner = createOwner(chapter(sourceFrame), sourceFrame, services());
      const scope = panelPositionScope(owner);
      expect(writeFrameAssetReferencePanelPosition(scope, { left: 320, top: 80 })).toBe("saved");
      const panel = renderPanel(owner);
      const panelElement = screen.getByRole("region", { name: "镜头 1 的关联素材" });
      setPanelBounds(panelElement, 360, 300);
      await user.click(screen.getByRole("button", { name: "浮动查看" }));
      const handle = screen.getByRole("button", { name: "拖动关联素材浮窗；也可按方向键移动" });
      const capture = stubPointerCapture(handle);
      fireEvent.pointerDown(handle, { pointerId: 5, button: 0, clientX: 10, clientY: 10 });
      fireEvent.pointerMove(handle, { pointerId: 5, clientX: 100, clientY: 90 });

      if (cancelReason === "pointercancel") {
        fireEvent.pointerCancel(handle, { pointerId: 5 });
        expect(panelElement.style.left).toBe("320px");
      } else if (cancelReason === "close") {
        await user.click(screen.getByRole("button", { name: "关闭" }));
        expect(screen.queryByRole("region", { name: "镜头 1 的关联素材" })).not.toBeInTheDocument();
      } else {
        panel.rerender(
          <FrameAssetReferencesPanel
            currentScope={{ ...currentScope(owner), userId: "another-position-owner" }}
            onClose={vi.fn()}
            onUnauthorized={vi.fn()}
            owner={owner}
          />,
        );
        expect(screen.queryByRole("region", { name: "镜头 1 的关联素材" })).not.toBeInTheDocument();
      }

      fireEvent.pointerUp(handle, { pointerId: 5, clientX: 800, clientY: 600 });
      expect(readFrameAssetReferencePanelPosition(scope)).toEqual({
        kind: "stored",
        position: { left: 320, top: 80 },
      });
      expect(capture.releasePointerCapture).toHaveBeenCalledWith(5);
    },
  );

  it("lets keyboard movement and explicit reset supersede a pointer drag", async () => {
    const user = userEvent.setup();
    setViewportSize(1400, 900);
    const sourceFrame = stableStoryboardFrame("floating-frame-keyboard");
    const owner = createOwner(chapter(sourceFrame), sourceFrame, services());
    const scope = panelPositionScope(owner);
    expect(writeFrameAssetReferencePanelPosition(scope, { left: 300, top: 80 })).toBe("saved");
    renderPanel(owner);
    const panelElement = screen.getByRole("region", { name: "镜头 1 的关联素材" });
    setPanelBounds(panelElement, 360, 300);
    await user.click(screen.getByRole("button", { name: "浮动查看" }));
    const handle = screen.getByRole("button", { name: "拖动关联素材浮窗；也可按方向键移动" });
    const capture = stubPointerCapture(handle);

    fireEvent.pointerDown(handle, { pointerId: 11, button: 0, clientX: 0, clientY: 0 });
    fireEvent.pointerMove(handle, { pointerId: 11, clientX: 100, clientY: 100 });
    fireEvent.keyDown(handle, { key: "ArrowRight" });
    expect(readFrameAssetReferencePanelPosition(scope)).toEqual({
      kind: "stored",
      position: { left: 416, top: 180 },
    });
    fireEvent.pointerUp(handle, { pointerId: 11, clientX: 900, clientY: 700 });
    expect(readFrameAssetReferencePanelPosition(scope)).toEqual({
      kind: "stored",
      position: { left: 416, top: 180 },
    });

    fireEvent.pointerDown(handle, { pointerId: 12, button: 0, clientX: 0, clientY: 0 });
    fireEvent.pointerMove(handle, { pointerId: 12, clientX: 80, clientY: 80 });
    await user.click(screen.getByRole("button", { name: "恢复默认位置" }));
    const defaultPosition = defaultFrameAssetReferencePanelPosition(
      { width: 360, height: 300 },
      { width: 1400, height: 900 },
    );
    expect(readFrameAssetReferencePanelPosition(scope)).toEqual({ kind: "stored", position: defaultPosition });
    fireEvent.pointerUp(handle, { pointerId: 12, clientX: 900, clientY: 700 });
    expect(readFrameAssetReferencePanelPosition(scope)).toEqual({ kind: "stored", position: defaultPosition });
    expect(capture.releasePointerCapture).toHaveBeenCalledWith(11);
    expect(capture.releasePointerCapture).toHaveBeenCalledWith(12);
  });
});
