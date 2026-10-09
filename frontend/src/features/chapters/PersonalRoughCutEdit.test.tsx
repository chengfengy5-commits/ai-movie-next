import { useLayoutEffect } from "react";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Chapter, StoryboardAsset } from "../../shared/api/contracts";
import { ApiError } from "../../shared/api/errors";
import type {
  PersonalRoughCutSnapshot,
  PersonalRoughCutUpdate,
} from "../../shared/api/personalRoughCut";
import type { WorkspaceServices } from "../../shared/api/services";
import { PersonalRoughCutPanel, type PersonalRoughCutFrameTarget } from "./PersonalRoughCutPanel";

const jsxRuntimeProbe = vi.hoisted(() => ({
  buttonProps: [] as Array<Record<string, unknown>>,
  capture(type: unknown, props: unknown) {
    if (type === "button" && typeof props === "object" && props !== null) {
      const value = props as Record<string, unknown>;
      const children = value.children;
      const accessibleName = value["aria-label"];
      const text = Array.isArray(children) ? children.join("") : String(children ?? "");
      const searchableText = String(accessibleName ?? "") + " " + text;
      if (
        searchableText.includes("编排")
        || searchableText.includes("镜头")
        || searchableText.includes("取消编辑")
        || searchableText.includes("排除")
        || searchableText.includes("纳入")
        || searchableText.includes("重新读取")
        || searchableText.includes("关闭")
      ) {
        this.buttonProps.push(value);
      }
    }
  },
}));

vi.mock("react/jsx-runtime", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react/jsx-runtime")>();
  const jsx: typeof actual.jsx = (type, props, key) => {
    jsxRuntimeProbe.capture(type, props);
    return actual.jsx(type, props, key);
  };
  const jsxs: typeof actual.jsxs = (type, props, key) => {
    jsxRuntimeProbe.capture(type, props);
    return actual.jsxs(type, props, key);
  };
  return { ...actual, jsx, jsxs };
});

vi.mock("react/jsx-dev-runtime", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react/jsx-dev-runtime")>();
  const jsxDEV: typeof actual.jsxDEV = (type, props, key, isStaticChildren, source, self) => {
    jsxRuntimeProbe.capture(type, props);
    return actual.jsxDEV(type, props, key, isStaticChildren, source, self);
  };
  return { ...actual, jsxDEV };
});

function textOf(props: Record<string, unknown>): string {
  const label = props["aria-label"];
  if (typeof label === "string") {
    return label;
  }
  const children = props.children;
  return Array.isArray(children) ? children.join("") : String(children ?? "");
}

function capturedButton(label: string, reverseOffset = 0): Record<string, unknown> {
  const matches = jsxRuntimeProbe.buttonProps.filter((props) => textOf(props).includes(label));
  const props = matches[matches.length - 1 - reverseOffset];
  if (props === undefined || typeof props.onClick !== "function") {
    throw new Error(`No captured public JSX button handler matching ${label}.`);
  }
  return props;
}

function invokeCaptured(props: Record<string, unknown>): void {
  const handler = props.onClick;
  if (typeof handler !== "function") {
    throw new Error("Captured element did not have an onClick handler.");
  }
  handler({ preventDefault() {} });
}

function makeChapter(ids = ["rough-a", "rough-b", "rough-c"]): Chapter {
  return {
    id: "chapter-rough-edit",
    series_id: "series-rough-edit",
    title: "粗剪编排章节",
    content: ids.map((id) => ({ storyboard: [id], text: `章节正文 ${id}` })),
    order: 1,
    created_at: "2026-10-01T00:00:00",
    updated_at: "2026-10-02T00:00:00",
    lock: null,
  };
}

function makeAssets(ids = ["rough-a", "rough-b", "rough-c"]): StoryboardAsset[] {
  return ids.map((id, index) => ({
    id,
    series_id: "series-rough-edit",
    chapter_id: "chapter-rough-edit",
    frame_index: index,
    name: `素材 ${id}`,
    description: null,
    image_url: null,
    created_at: "2026-10-01T00:00:00",
    updated_at: "2026-10-02T00:00:00",
  }));
}

function makeSnapshot(
  ids = ["rough-a", "rough-b", "rough-c"],
  overrides: Partial<PersonalRoughCutSnapshot> = {},
): PersonalRoughCutSnapshot {
  return {
    chapter_id: "chapter-rough-edit",
    revision: 0,
    saved: false,
    frames: ids.map((asset_id, frame_index) => ({
      asset_id,
      frame_index,
      text: `读取正文 ${asset_id}`,
      preview_url: null,
      missing_reason: "当前分镜没有可用视频",
      included: true,
      pending: false,
    })),
    removed_asset_ids: [],
    ...overrides,
  };
}

function saveResponse(
  snapshot: PersonalRoughCutSnapshot,
  update: PersonalRoughCutUpdate,
): PersonalRoughCutSnapshot {
  const framesById = new Map(snapshot.frames.map((frame) => [frame.asset_id, frame]));
  return {
    chapter_id: snapshot.chapter_id,
    revision: snapshot.revision + 1,
    saved: true,
    frames: update.frames.map((submitted) => {
      const source = framesById.get(submitted.asset_id);
      if (source === undefined) {
        throw new Error("test fixture update had an unknown stable ID");
      }
      return {
        ...source,
        frame_index: source.frame_index,
        text: `服务器维护正文 ${submitted.asset_id}`,
        included: submitted.included,
        pending: false,
      };
    }),
    removed_asset_ids: [],
  };
}

function makeServices(
  getPersonalRoughCut: WorkspaceServices["getPersonalRoughCut"],
  savePersonalRoughCut?: WorkspaceServices["savePersonalRoughCut"],
): WorkspaceServices {
  return {
    mode: "api",
    apiBaseUrl: "http://127.0.0.1:4175/api",
    restore: async () => null,
    login: async () => { throw new Error("Not used by this component test."); },
    listSeries: async () => [],
    listMyTeams: async () => [],
    listChapters: async () => [],
    listStoryboardAssets: async () => [],
    listCharacters: async () => [],
    listScenes: async () => [],
    listProps: async () => [],
    getPersonalProductionNotes: async () => { throw new Error("Unexpected personal notes request."); },
    getPersonalRoughCut,
    ...(savePersonalRoughCut === undefined ? {} : { savePersonalRoughCut }),
    listMyTasks: async () => ({ total: 0, page: 1, page_size: 10, tasks: [] }),
    logout: vi.fn(),
  };
}

interface RenderPanelOptions {
  chapter?: Chapter;
  assets?: StoryboardAsset[] | null;
  assetSnapshotToken?: object | null;
  assetRequestGeneration?: number;
  assetDirectoryStatus?: "loading" | "ready" | "error";
  onClose?: () => void;
  onLocateFrame?: (target: PersonalRoughCutFrameTarget) => boolean;
  onFrameNavigationInvalidated?: () => void;
}

function renderPanel(
  services: WorkspaceServices,
  userId = "user-one",
  onUnauthorized = vi.fn(),
  options: RenderPanelOptions = {},
) {
  const chapter = options.chapter ?? makeChapter();
  const defaultAssets = makeAssets();
  const assets = options.assets === undefined ? defaultAssets : options.assets;
  const view = render(
    <PersonalRoughCutPanel
      chapter={chapter}
      seriesId={chapter.series_id}
      services={services}
      userId={userId}
      onClose={options.onClose ?? vi.fn()}
      onUnauthorized={onUnauthorized}
      assets={assets}
      assetSnapshotToken={options.assetSnapshotToken === undefined ? { owner: userId } : options.assetSnapshotToken}
      assetRequestGeneration={options.assetRequestGeneration ?? 1}
      assetDirectoryStatus={options.assetDirectoryStatus ?? "ready"}
      {...(options.onLocateFrame === undefined ? {} : { onLocateFrame: options.onLocateFrame })}
      {...(options.onFrameNavigationInvalidated === undefined
        ? {}
        : { onFrameNavigationInvalidated: options.onFrameNavigationInvalidated })}
    />,
  );
  return { ...view, chapter, assets, onUnauthorized };
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

describe("personal rough-cut editing panel", () => {
  afterEach(() => {
    cleanup();
    jsxRuntimeProbe.buttonProps.length = 0;
  });

  it.each(["user", "services", "chapter"] as const)(
    "hides a ready panel on the first parent layout commit and never revives it after %s scope ABA",
    async (changedField) => {
      const snapshot = makeSnapshot(["rough-a"]);
      const getRoughCut = vi.fn(async () => snapshot);
      const servicesA = makeServices(getRoughCut);
      const servicesB = makeServices(getRoughCut);
      const chapterA = makeChapter(["rough-a"]);
      const chapterB = { ...chapterA };
      type Scope = { userId: string; services: WorkspaceServices; chapter: Chapter };
      const scopeA: Scope = { userId: "user-one", services: servicesA, chapter: chapterA };
      const scopeB: Scope = {
        userId: changedField === "user" ? "user-two" : scopeA.userId,
        services: changedField === "services" ? servicesB : scopeA.services,
        chapter: changedField === "chapter" ? chapterB : scopeA.chapter,
      };
      const layoutVisibility: boolean[] = [];
      const tokenA = { scope: "A" };
      const tokenB = { scope: "B" };
      function ParentLayoutObserver({ scope }: { scope: Scope }) {
        useLayoutEffect(() => {
          layoutVisibility.push(screen.queryByRole("heading", { name: "我的粗剪草稿" }) !== null);
        });
        return (
          <PersonalRoughCutPanel
            chapter={scope.chapter}
            seriesId={scope.chapter.series_id}
            services={scope.services}
            userId={scope.userId}
            onClose={vi.fn()}
            onUnauthorized={vi.fn()}
            assets={makeAssets(["rough-a"])}
            assetSnapshotToken={scope === scopeA ? tokenA : tokenB}
            assetRequestGeneration={1}
            assetDirectoryStatus="ready"
          />
        );
      }

      const view = render(<ParentLayoutObserver scope={scopeA} />);
      expect(await screen.findByRole("group", { name: "按草稿状态筛选" })).toBeInTheDocument();
      layoutVisibility.length = 0;

      view.rerender(<ParentLayoutObserver scope={scopeB} />);
      expect(layoutVisibility[0]).toBe(false);
      expect(screen.queryByRole("heading", { name: "我的粗剪草稿" })).not.toBeInTheDocument();

      layoutVisibility.length = 0;
      view.rerender(<ParentLayoutObserver scope={scopeA} />);
      expect(layoutVisibility[0]).toBe(false);
      expect(screen.queryByRole("button", { name: "编辑编排" })).not.toBeInTheDocument();
      expect(getRoughCut).toHaveBeenCalledTimes(1);

      view.unmount();
      renderPanel(servicesA, "user-one", vi.fn(), { chapter: chapterA, assets: makeAssets(["rough-a"]) });
      expect(await screen.findByRole("group", { name: "按草稿状态筛选" })).toBeInTheDocument();
      expect(getRoughCut).toHaveBeenCalledTimes(2);
    },
  );

  it("rejects each captured R1 and R2 public handler against non-default R2 and R3 drafts", async () => {
    const user = userEvent.setup();
    const snapshot = makeSnapshot();
    const getRoughCut = vi.fn(async () => snapshot);
    const saveRoughCut = vi.fn(async (_chapterId: string, update: PersonalRoughCutUpdate) => (
      saveResponse(snapshot, update)
    ));
    const onClose = vi.fn();
    renderPanel(makeServices(getRoughCut, saveRoughCut), "user-one", vi.fn(), { onClose });
    expect(await screen.findByRole("group", { name: "按草稿状态筛选" })).toBeInTheDocument();

    const captureActions = () => ({
      open: capturedButton("编辑编排"),
      move: capturedButton("下移镜头 1"),
      include: capturedButton("排除粗剪：镜头 1"),
      save: capturedButton("保存粗剪编排"),
      cancel: capturedButton("取消编辑"),
      read: capturedButton("重新读取"),
      close: capturedButton("关闭"),
    });
    const openAndChangeDraft = async (kind: "exclude-one" | "exclude-two" | "exclude-three" | "move-one") => {
      await user.click(screen.getByRole("button", { name: "编辑编排" }));
      if (kind === "move-one") {
        await user.click(screen.getByRole("button", { name: "下移镜头 1" }));
        return;
      }
      const position = kind === "exclude-one" ? 1 : kind === "exclude-two" ? 2 : 3;
      await user.click(screen.getByRole("button", { name: "排除粗剪：镜头 " + position }));
    };
    const replayOldActions = async (
      actions: ReturnType<typeof captureActions>,
      getCount: number,
      excluded: string,
      notExcluded: string,
      firstIncluded: string,
      secondIncluded: string,
    ) => {
      for (const handler of Object.values(actions)) {
        await act(async () => invokeCaptured(handler));
        expect(screen.getByRole("region", { name: "编辑粗剪编排" })).toBeInTheDocument();
        const excludedText = screen.getByRole("list", { name: "已排除镜头" }).textContent ?? "";
        expect(excludedText).toContain(excluded);
        expect(excludedText).not.toContain(notExcluded);
        const includedText = screen.getByRole("list", { name: "已纳入镜头" }).textContent ?? "";
        expect(includedText.indexOf(firstIncluded)).toBeLessThan(includedText.indexOf(secondIncluded));
        expect(getRoughCut).toHaveBeenCalledTimes(getCount);
        expect(saveRoughCut).not.toHaveBeenCalled();
        expect(onClose).not.toHaveBeenCalled();
      }
    };

    await openAndChangeDraft("move-one");
    const r1Move = capturedButton("下移镜头 1");
    await user.click(screen.getByRole("button", { name: "排除粗剪：镜头 1" }));
    const r1 = { ...captureActions(), move: r1Move };

    await user.click(screen.getByRole("button", { name: "重新读取" }));
    expect(await screen.findByText("未保存的初始投影")).toBeInTheDocument();
    await openAndChangeDraft("exclude-two");
    const r2 = captureActions();
    expect(screen.getByRole("list", { name: "已排除镜头" })).toHaveTextContent("读取正文 rough-b");

    await replayOldActions(r1, 2, "读取正文 rough-b", "读取正文 rough-a", "读取正文 rough-a", "读取正文 rough-c");

    await user.click(screen.getByRole("button", { name: "重新读取" }));
    expect(await screen.findByText("未保存的初始投影")).toBeInTheDocument();
    await openAndChangeDraft("exclude-three");
    expect(screen.getByRole("list", { name: "已排除镜头" })).toHaveTextContent("读取正文 rough-c");

    await replayOldActions(r1, 3, "读取正文 rough-c", "读取正文 rough-a", "读取正文 rough-a", "读取正文 rough-b");
    await replayOldActions(r2, 3, "读取正文 rough-c", "读取正文 rough-b", "读取正文 rough-a", "读取正文 rough-b");
  });

  it("keeps a full ordered draft through cancel and accepts maintained source fields after one save", async () => {
    const user = userEvent.setup();
    const initial = makeSnapshot();
    const getRoughCut = vi.fn(async () => initial);
    const saveRoughCut = vi.fn((
      _chapterId: string,
      update: PersonalRoughCutUpdate,
      _signal: AbortSignal,
    ) => Promise.resolve(saveResponse(initial, update)));
    const services = makeServices(getRoughCut, saveRoughCut);
    renderPanel(services);

    expect(await screen.findByRole("group", { name: "按草稿状态筛选" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "编辑编排" }));
    expect(screen.getByRole("region", { name: "编辑粗剪编排" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "下移镜头 1" }));
    await user.click(screen.getByRole("button", { name: "排除粗剪：镜头 3" }));

    const oldSaveButton = capturedButton("保存粗剪编排");
    const oldCancelButton = capturedButton("取消编辑");
    await user.click(screen.getByRole("button", { name: "取消编辑" }));
    expect(screen.queryByRole("region", { name: "编辑粗剪编排" })).not.toBeInTheDocument();
    expect(getRoughCut).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "编辑编排" }));
    const includedList = screen.getByRole("list", { name: "已纳入镜头" });
    const includedText = includedList.textContent ?? "";
    expect(includedText.indexOf("读取正文 rough-b")).toBeLessThan(includedText.indexOf("读取正文 rough-a"));
    expect(screen.getByRole("list", { name: "已排除镜头" })).toHaveTextContent("读取正文 rough-c");
    expect(screen.getByRole("button", { name: "纳入粗剪：镜头 3" })).toHaveAttribute("aria-pressed", "false");

    await act(async () => {
      invokeCaptured(oldSaveButton);
      invokeCaptured(oldCancelButton);
    });
    expect(saveRoughCut).not.toHaveBeenCalled();
    expect(screen.getByRole("region", { name: "编辑粗剪编排" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "纳入粗剪：镜头 3" })).toHaveAttribute("aria-pressed", "false");

    const saveButton = capturedButton("保存粗剪编排");
    await act(async () => {
      invokeCaptured(saveButton);
      invokeCaptured(saveButton);
    });
    await waitFor(() => expect(saveRoughCut).toHaveBeenCalledTimes(1));
    const [savedChapterId, savedUpdate, saveSignal] = saveRoughCut.mock.calls[0] ?? [];
    expect(savedChapterId).toBe("chapter-rough-edit");
    expect(savedUpdate).toEqual({
      expected_revision: 0,
      frames: [
        { asset_id: "rough-b", included: true },
        { asset_id: "rough-a", included: true },
        { asset_id: "rough-c", included: false },
      ],
    });
    expect(saveSignal).toBeInstanceOf(AbortSignal);
    expect(getRoughCut).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("服务器维护正文 rough-c")).toBeInTheDocument();
    expect(screen.getByText("已保存草稿的当前投影 · 版本 1")).toBeInTheDocument();
  });

  it("preserves submitted intent after conflict and unlocks only on a successful explicit read", async () => {
    const user = userEvent.setup();
    const first = makeSnapshot(["rough-a"], { revision: 3, saved: true });
    const second = makeSnapshot(["rough-a"], { revision: 4, saved: true });
    const getRoughCut = vi.fn()
      .mockResolvedValueOnce(first)
      .mockRejectedValueOnce(new ApiError("http", "读取暂时失败。", 503))
      .mockResolvedValueOnce(second);
    const saveRoughCut = vi.fn(async () => {
      throw new ApiError("http", "粗剪版本已变化。", 409);
    });
    renderPanel(makeServices(getRoughCut, saveRoughCut), "user-one", vi.fn(), {
      chapter: makeChapter(["rough-a"]),
      assets: makeAssets(["rough-a"]),
    });

    expect(await screen.findByRole("group", { name: "按草稿状态筛选" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "编辑编排" }));
    await user.click(screen.getByRole("button", { name: "排除粗剪：镜头 1" }));
    await user.click(screen.getByRole("button", { name: "保存粗剪编排" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("粗剪版本已变化。");
    expect(screen.getByRole("region", { name: "本次粗剪保存意图" })).toHaveTextContent("排除");
    expect(screen.queryByRole("region", { name: "编辑粗剪编排" })).not.toBeInTheDocument();
    expect(getRoughCut).toHaveBeenCalledTimes(1);
    expect(saveRoughCut).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "重新读取" }));
    expect(await screen.findByRole("heading", { name: "暂时无法读取粗剪" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "本次粗剪保存意图" })).toHaveTextContent("排除");
    expect(getRoughCut).toHaveBeenCalledTimes(2);
    await user.click(screen.getByRole("button", { name: "重新读取" }));
    expect(await screen.findByText("已保存草稿的当前投影 · 版本 4")).toBeInTheDocument();
    expect(getRoughCut).toHaveBeenCalledTimes(3);
    expect(saveRoughCut).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "编辑编排" }));
    expect(screen.getByRole("button", { name: "排除粗剪：镜头 1" })).toHaveAttribute("aria-pressed", "true");
  });

  it.each([
    ["late success", "success"],
    ["late 401", "unauthorized"],
  ] as const)("ignores a settled old-user save after logout and a new panel opens (%s)", async (_label, outcome) => {
    const user = userEvent.setup();
    const oldInitial = makeSnapshot(["rough-a"], { revision: 0, saved: false });
    const oldSave = deferred<PersonalRoughCutSnapshot>();
    const oldUnauthorized = vi.fn();
    const oldGet = vi.fn(async () => oldInitial);
    const oldSaveMethod = vi.fn((
      _chapterId: string,
      _update: PersonalRoughCutUpdate,
      _signal: AbortSignal,
    ) => oldSave.promise);
    const oldView = renderPanel(makeServices(oldGet, oldSaveMethod), "user-one", oldUnauthorized, {
      chapter: makeChapter(["rough-a"]),
      assets: makeAssets(["rough-a"]),
    });

    expect(await screen.findByRole("group", { name: "按草稿状态筛选" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "编辑编排" }));
    const oldToggleHandler = capturedButton("排除粗剪：镜头 1");
    await user.click(screen.getByRole("button", { name: "保存粗剪编排" }));
    expect(oldSaveMethod).toHaveBeenCalledTimes(1);
    oldView.unmount();

    const newSnapshot = makeSnapshot(["rough-b"], { revision: 2, saved: true });
    const newGet = vi.fn(async () => newSnapshot);
    const newSave = vi.fn(async () => newSnapshot);
    renderPanel(makeServices(newGet, newSave), "user-two", vi.fn(), {
      chapter: makeChapter(["rough-b"]),
      assets: makeAssets(["rough-b"]),
    });
    expect(await screen.findByText("读取正文 rough-b")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "编辑编排" }));
    expect(screen.getByRole("button", { name: "排除粗剪：镜头 1" })).toHaveAttribute("aria-pressed", "true");
    await act(async () => invokeCaptured(oldToggleHandler));
    expect(screen.getByRole("button", { name: "排除粗剪：镜头 1" })).toHaveAttribute("aria-pressed", "true");

    if (outcome === "success") {
      const oldUpdate = oldSaveMethod.mock.calls[0]?.[1];
      if (oldUpdate === undefined) {
        throw new Error("old save did not receive its submitted update");
      }
      await act(async () => {
        oldSave.resolve(saveResponse(oldInitial, oldUpdate));
        await oldSave.promise;
      });
    } else {
      await act(async () => {
        oldSave.reject(new ApiError("http", "登录已失效。", 401));
        await oldSave.promise.catch(() => undefined);
      });
    }

    expect(screen.getByRole("list", { name: "当前粗剪草稿条目" })).toHaveTextContent("读取正文 rough-b");
    expect(screen.getByRole("list", { name: "当前粗剪草稿条目" })).not.toHaveTextContent("读取正文 rough-a");
    expect(oldUnauthorized).not.toHaveBeenCalled();
    expect(newSave).not.toHaveBeenCalled();
  });

  it("rejects old read/open/close callbacks after a settled same-snapshot reread", async () => {
    const user = userEvent.setup();
    const snapshot = makeSnapshot(["rough-a"]);
    const nextRead = deferred<PersonalRoughCutSnapshot>();
    const getRoughCut = vi.fn()
      .mockResolvedValueOnce(snapshot)
      .mockImplementationOnce(() => nextRead.promise);
    const onClose = vi.fn();
    const view = renderPanel(makeServices(getRoughCut, vi.fn()), "user-one", vi.fn(), { onClose });

    expect(await screen.findByRole("group", { name: "按草稿状态筛选" })).toBeInTheDocument();
    const oldOpenHandler = capturedButton("编辑编排");
    const oldReadHandler = capturedButton("重新读取");
    const oldCloseHandler = capturedButton("关闭");

    await user.click(screen.getByRole("button", { name: "重新读取" }));
    await waitFor(() => expect(getRoughCut).toHaveBeenCalledTimes(2));
    await act(async () => {
      nextRead.resolve(snapshot);
      await nextRead.promise;
    });
    expect(await screen.findByText("读取正文 rough-a")).toBeInTheDocument();

    await act(async () => invokeCaptured(oldOpenHandler));
    expect(screen.queryByRole("region", { name: "编辑粗剪编排" })).not.toBeInTheDocument();
    await act(async () => invokeCaptured(oldReadHandler));
    expect(getRoughCut).toHaveBeenCalledTimes(2);
    await act(async () => invokeCaptured(oldCloseHandler));
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByText("读取正文 rough-a")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "编辑编排" }));
    const oldUnmountedToggle = capturedButton("排除粗剪：镜头 1");
    view.unmount();

    renderPanel(makeServices(vi.fn(async () => snapshot), vi.fn()), "user-one", vi.fn(), {
      chapter: makeChapter(["rough-a"]),
      assets: makeAssets(["rough-a"]),
    });
    expect(await screen.findByText("读取正文 rough-a")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "编辑编排" }));
    await act(async () => invokeCaptured(oldUnmountedToggle));
    expect(screen.getByRole("button", { name: "排除粗剪：镜头 1" })).toHaveAttribute("aria-pressed", "true");
  });

  it("rejects read and close handlers captured before an editor draft changes", async () => {
    const user = userEvent.setup();
    const snapshot = makeSnapshot(["rough-a"]);
    const getRoughCut = vi.fn(async () => snapshot);
    const onClose = vi.fn();
    renderPanel(makeServices(getRoughCut, vi.fn()), "user-one", vi.fn(), { onClose });

    expect(await screen.findByRole("group", { name: "按草稿状态筛选" })).toBeInTheDocument();
    const oldReadHandler = capturedButton("重新读取");
    const oldCloseHandler = capturedButton("关闭");

    await user.click(screen.getByRole("button", { name: "编辑编排" }));
    await user.click(screen.getByRole("button", { name: "排除粗剪：镜头 1" }));
    await user.click(screen.getByRole("button", { name: "取消编辑" }));

    await act(async () => invokeCaptured(oldReadHandler));
    expect(getRoughCut).toHaveBeenCalledTimes(1);
    await act(async () => invokeCaptured(oldCloseHandler));
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "编辑编排" })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "编辑编排" }));
    expect(screen.getByRole("button", { name: "纳入粗剪：镜头 1" })).toHaveAttribute("aria-pressed", "false");
  });

  it("keeps a dirty draft invalid across asset-source A-to-B-to-A until a successful read", async () => {
    const user = userEvent.setup();
    const chapter = makeChapter(["rough-a"]);
    const firstAssets = makeAssets(["rough-a"]);
    const otherAssets = makeAssets(["rough-a"]);
    const firstToken = { generation: "A" };
    const otherToken = { generation: "B" };
    const firstSnapshot = makeSnapshot(["rough-a"]);
    const freshSnapshot = makeSnapshot(["rough-a"]);
    const getRoughCut = vi.fn()
      .mockResolvedValueOnce(firstSnapshot)
      .mockResolvedValueOnce(freshSnapshot);
    const services = makeServices(getRoughCut, vi.fn());
    const renderWithSource = (assets: StoryboardAsset[], token: object, requestGeneration: number) => (
      <PersonalRoughCutPanel
        chapter={chapter}
        seriesId={chapter.series_id}
        services={services}
        userId="user-one"
        onClose={vi.fn()}
        onUnauthorized={vi.fn()}
        assets={assets}
        assetSnapshotToken={token}
        assetRequestGeneration={requestGeneration}
        assetDirectoryStatus="ready"
      />
    );
    const view = render(renderWithSource(firstAssets, firstToken, 1));

    expect(await screen.findByRole("group", { name: "按草稿状态筛选" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "编辑编排" }));
    await user.click(screen.getByRole("button", { name: "排除粗剪：镜头 1" }));
    await user.click(screen.getByRole("button", { name: "取消编辑" }));
    const oldOpenHandler = capturedButton("编辑编排");

    view.rerender(renderWithSource(otherAssets, otherToken, 2));
    expect(screen.getByRole("alert")).toHaveTextContent("素材目录已变化");
    view.rerender(renderWithSource(firstAssets, firstToken, 1));
    expect(screen.getByRole("alert")).toHaveTextContent("素材目录已变化");
    expect(getRoughCut).toHaveBeenCalledTimes(1);
    await act(async () => invokeCaptured(oldOpenHandler));
    expect(screen.getByRole("button", { name: "编辑编排" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "重新读取" }));
    expect(await screen.findByText("未保存的初始投影")).toBeInTheDocument();
    expect(getRoughCut).toHaveBeenCalledTimes(2);
    expect(screen.queryByText("素材目录已变化")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "编辑编排" }));
    expect(screen.getByRole("button", { name: "排除粗剪：镜头 1" })).toHaveAttribute("aria-pressed", "true");
  });

  it("invalidates a located frame ticket as soon as saving starts", async () => {
    const user = userEvent.setup();
    const chapter = makeChapter(["rough-a"]);
    const assets = makeAssets(["rough-a"]);
    const snapshot = makeSnapshot(["rough-a"], { saved: false });
    const pendingSave = deferred<PersonalRoughCutSnapshot>();
    const saveRoughCut = vi.fn((
      _chapterId: string,
      _update: PersonalRoughCutUpdate,
      _signal: AbortSignal,
    ) => pendingSave.promise);
    const onLocateFrame = vi.fn((_target: PersonalRoughCutFrameTarget) => true);
    const services = makeServices(vi.fn(async () => snapshot), saveRoughCut);
    renderPanel(services, "user-one", vi.fn(), { chapter, assets, onLocateFrame });

    expect(await screen.findByRole("button", { name: "定位到对应镜头 1" })).toBeInTheDocument();
    const oldLocateHandler = capturedButton("定位到对应镜头 1");
    await user.click(screen.getByRole("button", { name: "定位到对应镜头 1" }));
    expect(onLocateFrame).toHaveBeenCalledTimes(1);
    const locatedTarget = onLocateFrame.mock.calls[0]?.[0];
    if (locatedTarget === undefined) {
      throw new Error("the current locate callback did not receive a frame ticket");
    }
    expect(locatedTarget.isCurrent()).toBe(true);

    await user.click(screen.getByRole("button", { name: "编辑编排" }));
    await user.click(screen.getByRole("button", { name: "排除粗剪：镜头 1" }));
    await user.click(screen.getByRole("button", { name: "保存粗剪编排" }));
    expect(await screen.findByRole("status")).toHaveTextContent("正在保存粗剪编排");
    expect(locatedTarget.isCurrent()).toBe(false);

    await act(async () => invokeCaptured(oldLocateHandler));
    expect(onLocateFrame).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["late success", "success"],
    ["late unauthorized", "unauthorized"],
  ] as const)("locks a pending write after the asset snapshot changes and ignores a settled old result (%s)", async (_label, outcome) => {
    const user = userEvent.setup();
    const chapter = makeChapter(["rough-a"]);
    const firstAssets = makeAssets(["rough-a"]);
    const nextAssets = makeAssets(["rough-a"]);
    const firstToken = { snapshot: 1 };
    const nextToken = { snapshot: 2 };
    const firstSnapshot = makeSnapshot(["rough-a"], { saved: false });
    const rereadSnapshot = makeSnapshot(["rough-a"], { saved: false });
    const pendingSave = deferred<PersonalRoughCutSnapshot>();
    const getRoughCut = vi.fn()
      .mockResolvedValueOnce(firstSnapshot)
      .mockResolvedValueOnce(rereadSnapshot);
    const saveRoughCut = vi.fn((
      _chapterId: string,
      _update: PersonalRoughCutUpdate,
      _signal: AbortSignal,
    ) => pendingSave.promise);
    const onUnauthorized = vi.fn();
    const onLocateFrame = vi.fn((_target: PersonalRoughCutFrameTarget) => true);
    const onClose = vi.fn();
    const services = makeServices(getRoughCut, saveRoughCut);
    const renderWithAssets = (
      currentAssets: StoryboardAsset[],
      token: object,
      generation: number,
    ) => (
      <PersonalRoughCutPanel
        chapter={chapter}
        seriesId={chapter.series_id}
        services={services}
        userId="user-one"
        onClose={onClose}
        onUnauthorized={onUnauthorized}
        assets={currentAssets}
        assetSnapshotToken={token}
        assetRequestGeneration={generation}
        assetDirectoryStatus="ready"
        onLocateFrame={onLocateFrame}
      />
    );
    const view = render(renderWithAssets(firstAssets, firstToken, 1));

    expect(await screen.findByRole("button", { name: "定位到对应镜头 1" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "定位到对应镜头 1" }));
    const oldTarget = onLocateFrame.mock.calls[0]?.[0];
    if (oldTarget === undefined) {
      throw new Error("the initial locate callback did not receive a frame ticket");
    }
    const oldLocateHandler = capturedButton("定位到对应镜头 1");
    const oldCloseHandler = capturedButton("关闭");

    await user.click(screen.getByRole("button", { name: "编辑编排" }));
    await user.click(screen.getByRole("button", { name: "排除粗剪：镜头 1" }));
    await user.click(screen.getByRole("button", { name: "保存粗剪编排" }));
    expect(saveRoughCut).toHaveBeenCalledTimes(1);

    view.rerender(renderWithAssets(nextAssets, nextToken, 2));
    expect(await screen.findByRole("alert")).toHaveTextContent("素材目录已变化");
    expect(oldTarget.isCurrent()).toBe(false);
    await act(async () => invokeCaptured(oldLocateHandler));
    expect(onLocateFrame).toHaveBeenCalledTimes(1);

    if (outcome === "success") {
      const submitted = saveRoughCut.mock.calls[0]?.[1];
      if (submitted === undefined) {
        throw new Error("the pending save did not capture its submitted update");
      }
      await act(async () => {
        pendingSave.resolve(saveResponse(firstSnapshot, submitted));
        await pendingSave.promise;
      });
    } else {
      await act(async () => {
        pendingSave.reject(new ApiError("http", "保存授权已失效。", 401));
        await pendingSave.promise.catch(() => undefined);
      });
    }

    expect(screen.getByRole("alert")).toHaveTextContent("素材目录已变化");
    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(getRoughCut).toHaveBeenCalledTimes(1);
    await act(async () => invokeCaptured(oldCloseHandler));
    expect(onClose).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "重新读取" }));
    expect(await screen.findByText("未保存的初始投影")).toBeInTheDocument();
    expect(getRoughCut).toHaveBeenCalledTimes(2);
    expect(screen.queryByText("素材目录已变化")).not.toBeInTheDocument();
  });

  it("can explicitly save a valid empty chapter without loading asset metadata", async () => {
    const user = userEvent.setup();
    const emptyChapter = makeChapter([]);
    const emptySnapshot = makeSnapshot([], { saved: false });
    const savedEmpty = makeSnapshot([], { revision: 1, saved: true });
    const getRoughCut = vi.fn(async () => emptySnapshot);
    const saveRoughCut = vi.fn((
      _chapterId: string,
      _update: PersonalRoughCutUpdate,
      _signal: AbortSignal,
    ) => Promise.resolve(savedEmpty));
    renderPanel(makeServices(getRoughCut, saveRoughCut), "user-one", vi.fn(), {
      chapter: emptyChapter,
      assets: null,
      assetSnapshotToken: null,
      assetDirectoryStatus: "loading",
    });

    expect(await screen.findByText("当前投影没有镜头")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "编辑编排" }));
    expect(screen.getByRole("button", { name: "保存粗剪编排" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "保存粗剪编排" }));
    expect(await screen.findByText("已保存草稿的当前投影 · 版本 1")).toBeInTheDocument();
    expect(getRoughCut).toHaveBeenCalledTimes(1);
    expect(saveRoughCut).toHaveBeenCalledTimes(1);
    expect(saveRoughCut.mock.calls[0]?.[1]).toEqual({ expected_revision: 0, frames: [] });
  });

  it.each([
    ["current PUT 401", "unauthorized"],
    ["unverifiable successful response", "invalid-response"],
  ] as const)("preserves the exact submitted intent after %s until an explicit successful read", async (_label, outcome) => {
    const user = userEvent.setup();
    const initial = makeSnapshot(["rough-a", "rough-b"], { revision: 4, saved: true });
    const recovered = makeSnapshot(["rough-a", "rough-b"], { revision: 6, saved: true });
    const getRoughCut = vi.fn()
      .mockResolvedValueOnce(initial)
      .mockRejectedValueOnce(new ApiError("http", "读取暂时失败。", 503))
      .mockResolvedValueOnce(recovered);
    const onUnauthorized = vi.fn();
    const saveRoughCut = vi.fn((
      _chapterId: string,
      _update: PersonalRoughCutUpdate,
      _signal: AbortSignal,
    ) => outcome === "unauthorized"
      ? Promise.reject(new ApiError("http", "保存授权已失效。", 401))
      : Promise.resolve({ ...initial, chapter_id: "unexpected-chapter" }));
    renderPanel(makeServices(getRoughCut, saveRoughCut), "user-one", onUnauthorized, {
      chapter: makeChapter(["rough-a", "rough-b"]),
      assets: makeAssets(["rough-a", "rough-b"]),
    });

    expect(await screen.findByRole("button", { name: "编辑编排" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "编辑编排" }));
    await user.click(screen.getByRole("button", { name: "下移镜头 1" }));
    await user.click(screen.getByRole("button", { name: "排除粗剪：镜头 2" }));
    const oldSaveHandler = capturedButton("保存粗剪编排");

    await user.click(screen.getByRole("button", { name: "保存粗剪编排" }));
    expect(await screen.findByText("粗剪编排已锁定")).toBeInTheDocument();
    expect(getRoughCut).toHaveBeenCalledTimes(1);
    expect(saveRoughCut).toHaveBeenCalledTimes(1);
    expect(onUnauthorized).toHaveBeenCalledTimes(outcome === "unauthorized" ? 1 : 0);
    expect(saveRoughCut.mock.calls[0]?.[1]).toEqual({
      expected_revision: 4,
      frames: [
        { asset_id: "rough-b", included: false },
        { asset_id: "rough-a", included: true },
      ],
    });
    const submittedIntent = screen.getByRole("region", { name: "本次粗剪保存意图" });
    const submittedRows = Array.from(submittedIntent.querySelectorAll("li"))
      .map((row) => row.textContent?.replace(/\s+/g, " ").trim() ?? "");
    expect(submittedRows).toHaveLength(2);
    expect(submittedRows[0]).toContain("提交顺序第 1 项 · 原章节第 2 个镜头");
    expect(submittedRows[0]).toContain("排除");
    expect(submittedRows[1]).toContain("提交顺序第 2 项 · 原章节第 1 个镜头");
    expect(submittedRows[1]).toContain("纳入");

    await act(async () => invokeCaptured(oldSaveHandler));
    expect(saveRoughCut).toHaveBeenCalledTimes(1);
    expect(getRoughCut).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "重新读取" }));
    expect(await screen.findByRole("heading", { name: "暂时无法读取粗剪" })).toBeInTheDocument();
    expect(getRoughCut).toHaveBeenCalledTimes(2);
    expect(saveRoughCut).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("region", { name: "本次粗剪保存意图" })).toHaveTextContent("排除");
    expect(screen.queryByRole("region", { name: "编辑粗剪编排" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "重新读取" }));
    expect(await screen.findByText("已保存草稿的当前投影 · 版本 6")).toBeInTheDocument();
    expect(getRoughCut).toHaveBeenCalledTimes(3);
    expect(saveRoughCut).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("region", { name: "本次粗剪保存意图" })).not.toBeInTheDocument();
    expect(onUnauthorized).toHaveBeenCalledTimes(outcome === "unauthorized" ? 1 : 0);
    expect(screen.getByRole("button", { name: "编辑编排" })).toBeEnabled();
  });

  it("routes a current GET 401 through the active unauthorized callback", async () => {
    const onUnauthorized = vi.fn();
    const getRoughCut = vi.fn(async () => {
      throw new ApiError("http", "登录已失效。", 401);
    });
    const services = makeServices(getRoughCut);
    renderPanel(services, "user-one", onUnauthorized);

    await waitFor(() => expect(onUnauthorized).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("group", { name: "按草稿状态筛选" })).not.toBeInTheDocument();
    expect(getRoughCut).toHaveBeenCalledTimes(1);
  });

  it("keeps the read-only panel available without an optional save service", async () => {
    const snapshot = makeSnapshot(["rough-a"]);
    renderPanel(makeServices(async () => snapshot));

    expect(await screen.findByRole("group", { name: "按草稿状态筛选" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "编辑编排" })).not.toBeInTheDocument();
    expect(screen.getByText("读取正文 rough-a")).toBeInTheDocument();
  });
});
