import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useLayoutEffect, useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Chapter, StoryboardAsset } from "../../shared/api/contracts";
import { ApiError } from "../../shared/api/errors";
import type { PersonalRoughCutSnapshot } from "../../shared/api/personalRoughCut";
import type { WorkspaceServices } from "../../shared/api/services";
import { PersonalRoughCutPanel, type PersonalRoughCutFrameTarget } from "./PersonalRoughCutPanel";

const jsxRuntimeProbe = vi.hoisted(() => ({
  buttonProps: [] as Array<Record<string, unknown>>,
  capture(type: unknown, props: unknown) {
    if (
      type === "button"
      && typeof props === "object"
      && props !== null
      && "className" in props
      && props.className === "personal-rough-cut-filter-button"
    ) {
      this.buttonProps.push(props as Record<string, unknown>);
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

function getCapturedFilterHandler(label: string): () => void {
  const button = [...jsxRuntimeProbe.buttonProps].reverse().find((props) => {
    const children = props.children;
    const text = Array.isArray(children) ? children.join("") : String(children ?? "");
    return text.trim() === label;
  });
  if (button === undefined || typeof button.onClick !== "function") {
    throw new Error(`No captured rough-cut filter handler for ${label}.`);
  }
  return button.onClick as () => void;
}

function makeChapter(id = "chapter-rough-cut"): Chapter {
  return {
    id,
    series_id: "series-rough-cut",
    title: "粗剪测试章节",
    content: [{ text: "章节正文", storyboard: ["source-frame"] }],
    order: 1,
    created_at: "2026-10-01T00:00:00",
    updated_at: "2026-10-02T00:00:00",
    lock: null,
  };
}

function makeSnapshot(chapterId = "chapter-rough-cut"): PersonalRoughCutSnapshot {
  return {
    chapter_id: chapterId,
    revision: 3,
    saved: true,
    frames: [
      {
        asset_id: "private-asset-id",
        frame_index: 1,
        text: "第二项先显示，来源章节第 2 个镜头",
        preview_url: "http://127.0.0.1:4175/media/private-preview.mp4",
        missing_reason: null,
        included: true,
        pending: false,
      },
      {
        asset_id: "",
        frame_index: 0,
        text: "还在等待安排",
        preview_url: null,
        missing_reason: "当前没有可用视频。",
        included: false,
        pending: true,
      },
    ],
    removed_asset_ids: ["removed-secret-id", "removed-secret-id"],
  };
}

function makeServices(getPersonalRoughCut: WorkspaceServices["getPersonalRoughCut"]): WorkspaceServices {
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
    listMyTasks: async () => ({ total: 0, page: 1, page_size: 10, tasks: [] }),
    logout: vi.fn(),
  };
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

function renderPanel(
  services: WorkspaceServices,
  overrides: Partial<{
    chapter: Chapter;
    seriesId: string;
    userId: string;
    onClose: () => void;
    onUnauthorized: () => void;
  }> = {},
) {
  const props = {
    chapter: overrides.chapter ?? makeChapter(),
    seriesId: overrides.seriesId ?? "series-rough-cut",
    services,
    userId: overrides.userId ?? "demo-user",
    onClose: overrides.onClose ?? vi.fn(),
    onUnauthorized: overrides.onUnauthorized ?? vi.fn(),
  };
  const view = render(<PersonalRoughCutPanel {...props} />);
  return { ...view, props };
}

describe("personal rough-cut panel", () => {
  afterEach(() => {
    cleanup();
    jsxRuntimeProbe.buttonProps.length = 0;
  });

  it("keeps filters hidden while the initial read is pending", async () => {
    const request = deferred<PersonalRoughCutSnapshot>();
    renderPanel(makeServices(() => request.promise));

    expect(screen.getByText("正在读取粗剪草稿…")).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "按草稿状态筛选" })).not.toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "当前粗剪草稿条目" })).not.toBeInTheDocument();

    await act(async () => {
      request.resolve(makeSnapshot());
      await request.promise;
    });
    expect(await screen.findByRole("group", { name: "按草稿状态筛选" })).toBeInTheDocument();
  });

  it("renders saved rows in response order with source positions and never exposes media or stable IDs", async () => {
    const getRoughCut = vi.fn(async () => makeSnapshot());
    const services = makeServices(getRoughCut);
    const { container } = renderPanel(services);

    expect(await screen.findByText("已保存草稿的当前投影 · 版本 3")).toBeInTheDocument();
    const rows = screen.getAllByRole("listitem");
    expect(rows.map((row) => row.textContent)).toEqual([
      expect.stringContaining("草稿列表第 1 项 · 读取时章节第 2 个镜头"),
      expect.stringContaining("草稿列表第 2 项 · 读取时章节第 1 个镜头"),
    ]);
    expect(rows[0]).toHaveTextContent("第二项先显示，来源章节第 2 个镜头");
    expect(rows[1]).toHaveTextContent("待安排");
    expect(screen.getByText("2 个失效旧引用，仅显示数量。")).toBeInTheDocument();
    expect(container.textContent).not.toContain("private-asset-id");
    expect(container.textContent).not.toContain("removed-secret-id");
    expect(container.textContent).not.toContain("private-preview.mp4");
    expect(container.querySelector("img, video, audio, source, a")).toBeNull();
    expect(container.innerHTML).not.toContain("private-asset-id");
    expect(container.innerHTML).not.toContain("private-preview.mp4");
  });

  it("distinguishes a valid empty saved draft from an initial unsaved projection", async () => {
    const getRoughCut = vi.fn(async () => ({
      chapter_id: "chapter-rough-cut",
      revision: 1,
      saved: true,
      frames: [],
      removed_asset_ids: [],
    }));
    renderPanel(makeServices(getRoughCut));

    expect(await screen.findByText("已保存草稿的当前投影 · 版本 1")).toBeInTheDocument();
    expect(screen.getByText("当前投影没有镜头")).toBeInTheDocument();
    expect(screen.getByText("这是一次有效读取的空列表。")).toBeInTheDocument();
    expect(screen.queryByText("未保存的初始投影")).not.toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "按草稿状态筛选" })).not.toBeInTheDocument();
  });

  it("renders independent counts and keeps original rows and positions while filtering", async () => {
    const chapter = makeChapter();
    chapter.content = [
      { text: "第一帧", storyboard: ["frame-a"] },
      { text: "第二帧", storyboard: ["frame-b"] },
      { text: "第三帧", storyboard: ["frame-c"] },
    ];
    const frames: PersonalRoughCutSnapshot["frames"] = [
      {
        asset_id: "frame-c",
        frame_index: 2,
        text: "第三章内镜头",
        preview_url: null,
        missing_reason: "当前没有可用视频。",
        included: true,
        pending: true,
      },
      {
        asset_id: "frame-a",
        frame_index: 0,
        text: "第一章内镜头",
        preview_url: null,
        missing_reason: "当前没有可用视频。",
        included: true,
        pending: false,
      },
      {
        asset_id: "frame-b",
        frame_index: 1,
        text: "第二章内镜头",
        preview_url: null,
        missing_reason: "当前没有可用视频。",
        included: false,
        pending: true,
      },
    ];
    const snapshot: PersonalRoughCutSnapshot = {
      chapter_id: chapter.id,
      revision: 3,
      saved: true,
      frames,
      removed_asset_ids: ["removed-one", "removed-one"],
    };
    const assets: StoryboardAsset[] = ["frame-a", "frame-b", "frame-c"].map((id, index) => ({
      id,
      series_id: chapter.series_id,
      chapter_id: chapter.id,
      frame_index: index,
      name: `素材${index}`,
      description: null,
      image_url: null,
      created_at: "2026-10-01T00:00:00Z",
      updated_at: "2026-10-02T00:00:00Z",
    }));
    const getRoughCut = vi.fn(async () => snapshot);
    const onLocateFrame = vi.fn((target: PersonalRoughCutFrameTarget) => target.isCurrent());
    render(
      <PersonalRoughCutPanel
        assets={assets}
        assetDirectoryStatus="ready"
        assetRequestGeneration={4}
        assetSnapshotToken={assets}
        chapter={chapter}
        onClose={vi.fn()}
        onLocateFrame={onLocateFrame}
        onUnauthorized={vi.fn()}
        seriesId={chapter.series_id}
        services={makeServices(getRoughCut)}
        userId="demo-user"
      />,
    );

    const filterGroup = await screen.findByRole("group", { name: "按草稿状态筛选" });
    expect(within(filterGroup).getByRole("button", { name: "全部 3" })).toHaveAttribute("aria-pressed", "true");
    expect(within(filterGroup).getByRole("button", { name: "已纳入 2" })).toBeInTheDocument();
    expect(within(filterGroup).getByRole("button", { name: "已排除 1" })).toBeInTheDocument();
    expect(within(filterGroup).getByRole("button", { name: "待安排 2" })).toBeInTheDocument();
    expect(screen.getByText("待安排单独计数，可与已纳入或已排除重叠。")).toBeInTheDocument();
    expect(screen.getByText("2 个失效旧引用，仅显示数量。")).toBeInTheDocument();

    fireEvent.click(within(filterGroup).getByRole("button", { name: "待安排 2" }));
    const visibleRows = screen.getAllByRole("listitem");
    expect(visibleRows).toHaveLength(2);
    expect(visibleRows[0]).toHaveTextContent("草稿列表第 1 项 · 读取时章节第 3 个镜头");
    expect(visibleRows[1]).toHaveTextContent("草稿列表第 3 项 · 读取时章节第 2 个镜头");

    fireEvent.click(within(filterGroup).getByRole("button", { name: "已排除 1" }));
    expect(within(filterGroup).getByRole("button", { name: "已排除 1" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
    expect(screen.getByText("草稿列表第 3 项 · 读取时章节第 2 个镜头")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "定位到对应镜头 2" }));

    expect(onLocateFrame).toHaveBeenCalledTimes(1);
    expect(onLocateFrame.mock.calls[0]?.[0]).toMatchObject({
      position: 2,
      chapter,
      snapshot,
      row: frames[2],
      assets,
      assetSnapshotToken: assets,
      assetRequestGeneration: 4,
    });
    expect(onLocateFrame.mock.calls[0]?.[0].snapshot).toBe(snapshot);
    expect(onLocateFrame.mock.calls[0]?.[0].row).toBe(frames[2]);
    expect(onLocateFrame.mock.calls[0]?.[0].snapshot.frames).toBe(frames);
    expect(getRoughCut).toHaveBeenCalledTimes(1);
  });

  it("renders an unsaved non-empty draft as plain text and labels empty text conservatively", async () => {
    const getRoughCut = vi.fn(async () => ({
      chapter_id: "chapter-rough-cut",
      revision: 0,
      saved: false,
      frames: [
        {
          asset_id: "stable-id-hidden",
          frame_index: 0,
          text: "<script>window.roughCut = true</script>",
          preview_url: null,
          missing_reason: "当前分镜没有可用视频",
          included: true,
          pending: false,
        },
        {
          asset_id: "",
          frame_index: 1,
          text: "",
          preview_url: null,
          missing_reason: "缺少唯一有效的稳定分镜 ID，暂不可编排；当前分镜没有可用视频",
          included: false,
          pending: false,
        },
      ],
      removed_asset_ids: [],
    }));
    const { container } = renderPanel(makeServices(getRoughCut));

    expect(await screen.findByText("未保存的初始投影")).toBeInTheDocument();
    const filterGroup = await screen.findByRole("group", { name: "按草稿状态筛选" });
    expect(within(filterGroup).getByRole("button", { name: "全部 2" })).toHaveAttribute("aria-pressed", "true");
    expect(within(filterGroup).getByRole("button", { name: "待安排 0" })).toBeInTheDocument();
    fireEvent.click(within(filterGroup).getByRole("button", { name: "待安排 0" }));
    expect(await screen.findByText("这份当前草稿投影中没有符合该条件的条目。")).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "当前粗剪草稿条目" })).not.toBeInTheDocument();
    expect(screen.queryByText("<script>window.roughCut = true</script>")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看全部条目" }));
    expect(screen.getByText("<script>window.roughCut = true</script>")).toBeInTheDocument();
    expect(screen.getByText("未提供正文")).toBeInTheDocument();
    expect(container.querySelector("script, img, video, audio, source, a")).toBeNull();
    expect(container.textContent).not.toContain("stable-id-hidden");
  });

  it("keeps a membership error visible and retries only after an explicit action", async () => {
    const getRoughCut = vi.fn()
      .mockRejectedValueOnce(new ApiError("http", "membership expired", 403, "会员资格已过期"))
      .mockResolvedValueOnce(makeSnapshot());
    const onUnauthorized = vi.fn();
    renderPanel(makeServices(getRoughCut), { onUnauthorized });

    expect(await screen.findByRole("heading", { name: "当前账号暂不可查看粗剪" })).toBeInTheDocument();
    expect(screen.getByText("会员资格已过期")).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "按草稿状态筛选" })).not.toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "当前粗剪草稿条目" })).not.toBeInTheDocument();
    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(getRoughCut).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "重新读取" }));
    expect(await screen.findByText("已保存草稿的当前投影 · 版本 3")).toBeInTheDocument();
    expect(getRoughCut).toHaveBeenCalledTimes(2);
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("reports a current 401 to the session owner", async () => {
    const getRoughCut = vi.fn().mockRejectedValue(new ApiError("http", "expired", 401));
    const onUnauthorized = vi.fn();
    renderPanel(makeServices(getRoughCut), { onUnauthorized });

    await waitFor(() => expect(onUnauthorized).toHaveBeenCalledTimes(1));
    expect(getRoughCut).toHaveBeenCalledTimes(1);
  });

  it.each(["success", "unauthorized"] as const)(
    "ignores a late %s from the superseded request after refresh settles successfully",
    async (lateResult) => {
      const stale = deferred<PersonalRoughCutSnapshot>();
      const getRoughCut = vi.fn()
        .mockReturnValueOnce(stale.promise)
        .mockResolvedValueOnce(makeSnapshot());
      const onUnauthorized = vi.fn();
      renderPanel(makeServices(getRoughCut), { onUnauthorized });

      await waitFor(() => expect(getRoughCut).toHaveBeenCalledTimes(1));
      fireEvent.click(screen.getByRole("button", { name: "重新读取" }));
      expect(screen.queryByRole("group", { name: "按草稿状态筛选" })).not.toBeInTheDocument();
      expect(screen.queryByRole("list", { name: "当前粗剪草稿条目" })).not.toBeInTheDocument();
      expect(await screen.findByText("已保存草稿的当前投影 · 版本 3")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "全部 2" })).toHaveAttribute("aria-pressed", "true");

      await act(async () => {
        if (lateResult === "unauthorized") {
          stale.reject(new ApiError("http", "expired", 401));
          await stale.promise.catch(() => undefined);
        } else {
          stale.resolve({ ...makeSnapshot(), revision: 9 });
          await stale.promise;
        }
      });

      expect(screen.getByText("已保存草稿的当前投影 · 版本 3")).toBeInTheDocument();
      expect(screen.queryByText("已保存草稿的当前投影 · 版本 9")).not.toBeInTheDocument();
      expect(onUnauthorized).not.toHaveBeenCalled();
      expect(getRoughCut).toHaveBeenCalledTimes(2);
    },
  );

  it("keeps the selected filter when the ordinary asset directory becomes ready", async () => {
    const chapter = makeChapter();
    chapter.content = [{ text: "镜头正文", storyboard: ["private-asset-id"] }];
    const snapshot = makeSnapshot();
    const firstAsset: StoryboardAsset = {
      id: "private-asset-id",
      series_id: chapter.series_id,
      chapter_id: chapter.id,
      frame_index: 0,
      name: "素材名称不显示",
      description: null,
      image_url: null,
      created_at: "2026-10-01T00:00:00Z",
      updated_at: "2026-10-02T00:00:00Z",
    };
    const firstAssetSnapshotToken = {};
    const readyAssetSnapshotToken = {};
    const getRoughCut = vi.fn(async () => snapshot);
    const onLocateFrame = vi.fn();
    const onFrameNavigationInvalidated = vi.fn();
    const services = makeServices(getRoughCut);
    const props = {
      chapter,
      onClose: vi.fn(),
      onFrameNavigationInvalidated,
      onLocateFrame,
      onUnauthorized: vi.fn(),
      seriesId: chapter.series_id,
      services,
      userId: "demo-user",
    };
    const { rerender } = render(
      <PersonalRoughCutPanel
        {...props}
        assetDirectoryStatus="loading"
        assetRequestGeneration={2}
        assetSnapshotToken={firstAssetSnapshotToken}
        assets={[]}
      />,
    );

    const filterGroup = await screen.findByRole("group", { name: "按草稿状态筛选" });
    fireEvent.click(within(filterGroup).getByRole("button", { name: "已纳入 1" }));
    expect(within(filterGroup).getByRole("button", { name: "已纳入 1" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByText("还在等待安排")).not.toBeInTheDocument();

    rerender(
      <PersonalRoughCutPanel
        {...props}
        assetDirectoryStatus="ready"
        assetRequestGeneration={3}
        assetSnapshotToken={readyAssetSnapshotToken}
        assets={[firstAsset]}
      />,
    );

    expect(within(screen.getByRole("group", { name: "按草稿状态筛选" }))
      .getByRole("button", { name: "已纳入 1" })).toHaveAttribute("aria-pressed", "true");
    expect(await screen.findByRole("button", { name: "定位到对应镜头 1" })).toBeInTheDocument();
    expect(getRoughCut).toHaveBeenCalledTimes(1);
    expect(onLocateFrame).not.toHaveBeenCalled();
    expect(onFrameNavigationInvalidated).not.toHaveBeenCalled();
  });

  it("ignores an old filter callback after rereading the same snapshot in a new generation", async () => {
    const snapshot = makeSnapshot();
    const getRoughCut = vi.fn()
      .mockResolvedValueOnce(snapshot)
      .mockResolvedValueOnce(snapshot)
      .mockResolvedValueOnce(snapshot);
    const services = makeServices(getRoughCut);
    const onClose = vi.fn();
    const onUnauthorized = vi.fn();
    const onFrameNavigationInvalidated = vi.fn();
    const panelProps = {
      chapter: makeChapter(),
      onClose,
      onFrameNavigationInvalidated,
      onUnauthorized,
      seriesId: "series-rough-cut",
      services,
      userId: "demo-user",
    };
    const { rerender } = render(<PersonalRoughCutPanel {...panelProps} key="read-r1" />);

    const firstFilterGroup = await screen.findByRole("group", { name: "按草稿状态筛选" });
    const oldPendingHandler = getCapturedFilterHandler("待安排 1");
    fireEvent.click(within(firstFilterGroup).getByRole("button", { name: "待安排 1" }));
    expect(within(firstFilterGroup).getByRole("button", { name: "待安排 1" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getAllByRole("listitem")).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "重新读取" }));
    expect(screen.queryByRole("group", { name: "按草稿状态筛选" })).not.toBeInTheDocument();
    expect(await screen.findByText("已保存草稿的当前投影 · 版本 3")).toBeInTheDocument();
    const secondFilterGroup = screen.getByRole("group", { name: "按草稿状态筛选" });
    expect(within(secondFilterGroup).getByRole("button", { name: "全部 2" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(within(secondFilterGroup).getByRole("button", { name: "已纳入 1" }));
    expect(within(secondFilterGroup).getByRole("button", { name: "已纳入 1" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getAllByRole("listitem")).toHaveLength(1);

    const invalidationsBeforeReplay = onFrameNavigationInvalidated.mock.calls.length;
    await act(async () => oldPendingHandler());

    expect(within(secondFilterGroup).getByRole("button", { name: "已纳入 1" })).toHaveAttribute("aria-pressed", "true");
    expect(within(secondFilterGroup).getByRole("button", { name: "待安排 1" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
    expect(screen.getByText("第二项先显示，来源章节第 2 个镜头")).toBeInTheDocument();
    expect(screen.queryByText("还在等待安排")).not.toBeInTheDocument();
    expect(onFrameNavigationInvalidated).toHaveBeenCalledTimes(invalidationsBeforeReplay);
    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    expect(getRoughCut).toHaveBeenCalledTimes(2);

    rerender(<PersonalRoughCutPanel {...panelProps} key="read-r3" />);
    await waitFor(() => expect(getRoughCut).toHaveBeenCalledTimes(3));
    const thirdFilterGroup = await screen.findByRole("group", { name: "按草稿状态筛选" });
    expect(within(thirdFilterGroup).getByRole("button", { name: "全部 2" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(within(thirdFilterGroup).getByRole("button", { name: "已纳入 1" }));
    expect(within(thirdFilterGroup).getByRole("button", { name: "已纳入 1" })).toHaveAttribute("aria-pressed", "true");
    const invalidationsBeforeR3Replay = onFrameNavigationInvalidated.mock.calls.length;

    await act(async () => oldPendingHandler());

    expect(within(thirdFilterGroup).getByRole("button", { name: "已纳入 1" })).toHaveAttribute("aria-pressed", "true");
    expect(within(thirdFilterGroup).getByRole("button", { name: "待安排 1" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
    expect(screen.getByText("第二项先显示，来源章节第 2 个镜头")).toBeInTheDocument();
    expect(screen.queryByText("还在等待安排")).not.toBeInTheDocument();
    expect(onFrameNavigationInvalidated).toHaveBeenCalledTimes(invalidationsBeforeR3Replay);
    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    expect(getRoughCut).toHaveBeenCalledTimes(3);
  });

  it.each(["user", "services", "chapter"] as const)(
    "hides the old panel immediately when only the %s scope changes and ignores a settled old request",
    async (changedScope) => {
      const stale = deferred<PersonalRoughCutSnapshot>();
      const oldGet = vi.fn(() => stale.promise);
      const oldServices = makeServices(oldGet);
      const newGet = vi.fn(async () => makeSnapshot());
      const newServices = makeServices(newGet);
      const currentChapter = makeChapter();
      const onUnauthorized = vi.fn();
      const { rerender } = renderPanel(oldServices, { onUnauthorized, chapter: currentChapter });
      await waitFor(() => expect(oldGet).toHaveBeenCalledTimes(1));

      const nextChapter = changedScope === "chapter" ? makeChapter("chapter-rough-cut") : currentChapter;
      const nextServices = changedScope === "services" ? newServices : oldServices;
      const nextUserId = changedScope === "user" ? "another-user" : "demo-user";
      rerender(
        <PersonalRoughCutPanel
          chapter={nextChapter}
          onClose={vi.fn()}
          onUnauthorized={onUnauthorized}
          seriesId="series-rough-cut"
          services={nextServices}
          userId={nextUserId}
        />,
      );

      expect(screen.queryByRole("region", { name: "我的粗剪草稿" })).not.toBeInTheDocument();
      expect(oldGet).toHaveBeenCalledTimes(1);
      expect(newGet).not.toHaveBeenCalled();
      await act(async () => {
        if (changedScope === "services") {
          stale.reject(new ApiError("http", "expired", 401));
          await stale.promise.catch(() => undefined);
        } else {
          stale.resolve(makeSnapshot());
          await stale.promise;
        }
      });

      expect(screen.queryByRole("region", { name: "我的粗剪草稿" })).not.toBeInTheDocument();
      expect(onUnauthorized).not.toHaveBeenCalled();
      expect(oldGet).toHaveBeenCalledTimes(1);
      expect(newGet).not.toHaveBeenCalled();
    },
  );
  it("issues a snapshot-bound locate ticket and explains a failed focus until the draft is reread", async () => {
    const chapter = makeChapter();
    chapter.content = [{ text: "同一镜头", storyboard: ["private-asset-id"] }];
    const assets: StoryboardAsset[] = [{
      id: "private-asset-id",
      series_id: chapter.series_id,
      chapter_id: chapter.id,
      frame_index: 42,
      name: "不要展示素材名",
      description: null,
      image_url: null,
      created_at: "2026-10-01T00:00:00Z",
      updated_at: "2026-10-02T00:00:00Z",
    }];
    const snapshot = makeSnapshot();
    const getRoughCut = vi.fn(async () => snapshot);
    const token = {};
    const onLocateFrame = vi.fn((_target: PersonalRoughCutFrameTarget) => false);
    const services = makeServices(getRoughCut);
    render(
      <PersonalRoughCutPanel
        assets={assets}
        assetDirectoryStatus="ready"
        assetRequestGeneration={7}
        assetSnapshotToken={token}
        chapter={chapter}
        onClose={vi.fn()}
        onLocateFrame={onLocateFrame}
        onUnauthorized={vi.fn()}
        seriesId={chapter.series_id}
        services={services}
        userId="demo-user"
      />,
    );

    const locateButton = await screen.findByRole("button", { name: "定位到对应镜头 1" });
    await act(async () => fireEvent.click(locateButton));
    expect(onLocateFrame).toHaveBeenCalledTimes(1);
    const firstTicket = onLocateFrame.mock.calls[0]?.[0];
    expect(firstTicket).toMatchObject({
      position: 1,
      chapter,
      snapshot,
      row: snapshot.frames[0],
      assets,
      assetSnapshotToken: token,
      assetRequestGeneration: 7,
    });
    expect(firstTicket?.isCurrent()).toBe(true);
    expect(screen.getByText("暂时无法定位到对应镜头，请确认该镜头仍在当前章节中后重试。")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "重新读取" }));
    expect(firstTicket?.isCurrent()).toBe(false);
    expect(await screen.findByRole("button", { name: "定位到对应镜头 1" })).toBeInTheDocument();
    expect(getRoughCut).toHaveBeenCalledTimes(2);
    expect(screen.queryByText("暂时无法定位到对应镜头，请确认该镜头仍在当前章节中后重试。")).not.toBeInTheDocument();
  });

  it.each(["user", "services", "chapter"] as const)(
    "hides a ready locate entry on the first parent layout after %s changes and does not revive it through A→B→A",
    async (changedScope) => {
      const user = userEvent.setup();
      const chapterA = makeChapter();
      chapterA.content = [{ text: "身份核验镜头", storyboard: ["private-asset-id"] }];
      const chapterB = { ...chapterA };
      const assets: StoryboardAsset[] = [{
        id: "private-asset-id",
        series_id: chapterA.series_id,
        chapter_id: chapterA.id,
        frame_index: 0,
        name: "内部素材名不显示",
        description: null,
        image_url: null,
        created_at: "2026-10-01T00:00:00Z",
        updated_at: "2026-10-02T00:00:00Z",
      }];
      const snapshot = makeSnapshot();
      const getA = vi.fn(async () => snapshot);
      const getB = vi.fn(async () => snapshot);
      const servicesA = makeServices(getA);
      const servicesB = makeServices(getB);
      const locate = vi.fn((target: PersonalRoughCutFrameTarget) => target.isCurrent());
      const commits: Array<{ panel: boolean; locate: boolean }> = [];

      function ScopeHarness({
        chapter,
        services,
        userId,
      }: {
        chapter: Chapter;
        services: WorkspaceServices;
        userId: string;
      }) {
        const [panelEpoch, setPanelEpoch] = useState(0);
        useLayoutEffect(() => {
          commits.push({
            panel: document.querySelector(".personal-rough-cut-panel") !== null,
            locate: document.querySelector(".rough-cut-locate-button") !== null,
          });
        });
        return (
          <>
            <PersonalRoughCutPanel
              key={panelEpoch}
              assets={assets}
              assetDirectoryStatus="ready"
              assetRequestGeneration={2}
              assetSnapshotToken={assets}
              chapter={chapter}
              onClose={vi.fn()}
              onLocateFrame={locate}
              onUnauthorized={vi.fn()}
              seriesId={chapter.series_id}
              services={services}
              userId={userId}
            />
            <button onClick={() => setPanelEpoch((epoch) => epoch + 1)} type="button">
              显式重开粗剪面板
            </button>
          </>
        );
      }

      const initial = { chapter: chapterA, services: servicesA, userId: "demo-user" };
      const view = render(<ScopeHarness {...initial} />);
      await user.click(await screen.findByRole("button", { name: "定位到对应镜头 1" }));
      const oldTicket = locate.mock.calls[0]?.[0];
      expect(oldTicket?.isCurrent()).toBe(true);
      expect(getA).toHaveBeenCalledTimes(1);

      const changed = changedScope === "user"
        ? { ...initial, userId: "another-user" }
        : changedScope === "services"
          ? { ...initial, services: servicesB }
          : { ...initial, chapter: chapterB };
      const beforeChange = commits.length;
      view.rerender(<ScopeHarness {...changed} />);
      expect(commits[beforeChange]).toEqual({ panel: false, locate: false });
      expect(screen.queryByRole("button", { name: "定位到对应镜头 1" })).not.toBeInTheDocument();
      expect(oldTicket?.isCurrent()).toBe(false);

      const returnedToA = { ...initial };
      const beforeReturn = commits.length;
      view.rerender(<ScopeHarness {...returnedToA} />);
      expect(commits[beforeReturn]).toEqual({ panel: false, locate: false });
      expect(screen.queryByRole("button", { name: "定位到对应镜头 1" })).not.toBeInTheDocument();
      expect(oldTicket?.isCurrent()).toBe(false);
      expect(getA).toHaveBeenCalledTimes(1);
      expect(getB).not.toHaveBeenCalled();

      await user.click(screen.getByRole("button", { name: "显式重开粗剪面板" }));
      const currentLocate = await screen.findByRole("button", { name: "定位到对应镜头 1" });
      await user.click(currentLocate);
      expect(getA).toHaveBeenCalledTimes(2);
      expect(locate).toHaveBeenCalledTimes(2);
      expect(locate.mock.calls[1]?.[0].isCurrent()).toBe(true);
    },
  );

});
