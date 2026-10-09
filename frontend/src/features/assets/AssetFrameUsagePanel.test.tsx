import { StrictMode } from "react";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Character, Chapter } from "../../shared/api/contracts";
import { ApiError } from "../../shared/api/errors";
import type { WorkspaceServices as Services } from "../../shared/api/services";
import {
  AssetFrameUsagePanel,
  type AssetFrameUsageOwner,
} from "./AssetFrameUsagePanel";

const seriesId = "usage-panel-series";

function character(): Character {
  return {
    id: "private-character-id",
    series_id: seriesId,
    name: "沈照",
    gender: null,
    age: null,
    role: null,
    appearance: null,
    description: null,
    image_url: "https://private.invalid/image.png",
    audio_url: "https://private.invalid/voice.mp3",
    voice_ref: "private-voice-ref",
    aliases: null,
    canonical_key: "private-canonical-key",
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-02T00:00:00Z",
  };
}

function chapter(id = "usage-chapter", content: Chapter["content"] = [
  { character: ["private-character-id"], text: "<script>literal</script>", original_text: null },
]): Chapter {
  return {
    id,
    series_id: seriesId,
    title: "只读关联镜头",
    content,
    order: 9,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-02T00:00:00Z",
    lock: null,
  };
}

function services(overrides: Partial<Services> = {}): Services {
  return {
    mode: "api",
    apiBaseUrl: "http://127.0.0.1:4175/api",
    restore: async () => null,
    login: async () => { throw new Error("Not used by this panel test."); },
    listSeries: async () => [],
    listMyTeams: async () => [],
    listChapters: vi.fn(async () => [chapter()]),
    listStoryboardAssets: async () => [],
    listCharacters: async () => [character()],
    listScenes: async () => [],
    listProps: async () => [],
    getPersonalProductionNotes: async () => { throw new Error("Unexpected notes request."); },
    getPersonalRoughCut: async () => { throw new Error("Unexpected rough-cut request."); },
    listMyTasks: async () => ({ total: 0, page: 1, page_size: 10, tasks: [] }),
    logout: vi.fn(),
    ...overrides,
  };
}

function owner(currentServices = services(), openEpoch = 1): AssetFrameUsageOwner {
  const asset = character();
  return {
    userId: "usage-user",
    services: currentServices,
    seriesId,
    category: "characters",
    asset,
    directorySnapshot: [asset],
    openEpoch,
  };
}

function renderPanel(
  currentOwner: AssetFrameUsageOwner,
  onUnauthorized = vi.fn(),
) {
  return render(
    <AssetFrameUsagePanel
      assetLabel="沈照"
      currentScope={currentOwner}
      onClose={vi.fn()}
      onUnauthorized={onUnauthorized}
      owner={currentOwner}
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

describe("asset frame usage panel", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("reads only chapters, renders literal text, and survives a StrictMode mount", async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    const listChapters = vi.fn(async () => [chapter()]);
    const serviceSet = services({ listChapters });
    const currentOwner = owner(serviceSet);
    const { container } = render(
      <StrictMode>
        <AssetFrameUsagePanel
          assetLabel="沈照"
          currentScope={currentOwner}
          onClose={vi.fn()}
          onUnauthorized={vi.fn()}
          owner={currentOwner}
        />
      </StrictMode>,
    );

    expect(await screen.findByText("<script>literal</script>")).toBeInTheDocument();
    expect(screen.getByText("尚未填写原文。")).toBeInTheDocument();
    expect(listChapters).toHaveBeenCalledTimes(1);
    expect(listChapters).toHaveBeenCalledWith(seriesId, expect.any(AbortSignal));
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(container.querySelector("img, audio, video, a, script")).toBeNull();
    expect(container.textContent).not.toMatch(/private-character-id|private-canonical|private-voice|https?:/);
  });

  it("creates a chapter target from the exact ready snapshot and invalidates it on reread", async () => {
    const user = userEvent.setup();
    const first = {
      ...chapter("chapter-first", [{
        character: ["private-character-id"],
        storyboard: ["storyboard-first"],
        text: "首章镜头",
      }]),
      title: "同名章节",
    };
    const target = {
      ...chapter("chapter-target", [{
        character: ["private-character-id", "private-character-id"],
        storyboard: ["storyboard-target", "ignored-storyboard-id"],
        text: "目标章镜头",
      }]),
      title: "目标章节",
    };
    const listChapters = vi.fn()
      .mockResolvedValueOnce([first, target])
      .mockResolvedValueOnce([first, target]);
    const onNavigateToChapter = vi.fn();
    const onNavigateToFrame = vi.fn();
    const currentOwner = owner(services({ listChapters }));

    render(
      <AssetFrameUsagePanel
        assetLabel="沈照"
        currentScope={currentOwner}
        onClose={vi.fn()}
        onNavigateToChapter={onNavigateToChapter}
        onNavigateToFrame={onNavigateToFrame}
        onUnauthorized={vi.fn()}
        owner={currentOwner}
      />,
    );

    expect(await screen.findByText("目标章镜头")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /^查看对应章节：/ }).map((button) => button.textContent))
      .toEqual(["查看对应章节：同名章节", "查看对应章节：目标章节"]);
    await user.click(screen.getByRole("button", { name: "查看对应章节：目标章节" }));
    expect(onNavigateToChapter).toHaveBeenCalledTimes(1);
    const targetReference = onNavigateToChapter.mock.calls[0]?.[0];
    expect(targetReference).toMatchObject({ chapterId: target.id, seriesId });
    expect(targetReference?.isCurrent()).toBe(true);

    const frameTargetButton = screen.getByRole("button", {
      name: "定位对应镜头：目标章节 · 镜头1",
    });
    await user.click(frameTargetButton);
    expect(onNavigateToFrame).toHaveBeenCalledTimes(1);
    const frameTargetReference = onNavigateToFrame.mock.calls[0]?.[0];
    expect(frameTargetReference).toMatchObject({
      chapterId: target.id,
      seriesId,
      storyboardAssetId: "storyboard-target",
      category: "characters",
      assetId: "private-character-id",
    });
    expect(frameTargetReference?.isCurrent()).toBe(true);

    await user.click(screen.getByRole("button", { name: "重新读取章节" }));
    expect(await screen.findByText("目标章镜头")).toBeInTheDocument();
    expect(listChapters).toHaveBeenCalledTimes(2);
    expect(targetReference?.isCurrent()).toBe(false);
    expect(frameTargetReference?.isCurrent()).toBe(false);
  });

  it("does not refetch when only the unauthorized callback changes", async () => {
    const pending = deferred<Chapter[]>();
    const listChapters = vi.fn(() => pending.promise);
    const currentOwner = owner(services({ listChapters }));
    const firstUnauthorized = vi.fn();
    const secondUnauthorized = vi.fn();
    const view = render(
      <AssetFrameUsagePanel
        assetLabel="沈照"
        currentScope={currentOwner}
        onClose={vi.fn()}
        onUnauthorized={firstUnauthorized}
        owner={currentOwner}
      />,
    );
    view.rerender(
      <AssetFrameUsagePanel
        assetLabel="沈照"
        currentScope={currentOwner}
        onClose={vi.fn()}
        onUnauthorized={secondUnauthorized}
        owner={currentOwner}
      />,
    );

    await act(async () => {
      pending.resolve([chapter()]);
      await pending.promise;
    });
    expect(await screen.findByText("<script>literal</script>")).toBeInTheDocument();
    expect(listChapters).toHaveBeenCalledTimes(1);
    expect(firstUnauthorized).not.toHaveBeenCalled();
    expect(secondUnauthorized).not.toHaveBeenCalled();
  });

  it("rereads explicitly and ignores an older request that settles with 401", async () => {
    const user = userEvent.setup();
    const first = deferred<Chapter[]>();
    const second = deferred<Chapter[]>();
    const listChapters = vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const onUnauthorized = vi.fn();
    renderPanel(owner(services({ listChapters })), onUnauthorized);

    expect(await screen.findByText("正在读取当前剧集章节…")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重新读取章节" }));
    await waitFor(() => expect(listChapters).toHaveBeenCalledTimes(2));
    await act(async () => {
      second.resolve([chapter("current-chapter", [{ character: ["private-character-id"], text: "当前快照" }])]);
      await second.promise;
    });
    expect(await screen.findByText("当前快照")).toBeInTheDocument();

    await act(async () => {
      first.reject(new ApiError("http", "expired", 401));
      await first.promise.catch(() => undefined);
    });
    expect(screen.getByText("当前快照")).toBeInTheDocument();
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("shows an empty snapshot after a retryable error and keeps the session", async () => {
    const user = userEvent.setup();
    const listChapters = vi.fn()
      .mockRejectedValueOnce(new ApiError("http", "unavailable", 500))
      .mockResolvedValueOnce([]);
    const onUnauthorized = vi.fn();
    renderPanel(owner(services({ listChapters })), onUnauthorized);

    expect(await screen.findByRole("alert")).toHaveTextContent("暂时无法读取关联镜头");
    await user.click(screen.getByRole("button", { name: "重试读取" }));
    expect(await screen.findByText("当前章节快照未找到关联镜头。")).toBeInTheDocument();
    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(listChapters).toHaveBeenCalledTimes(2);
  });

  it("shows confirmed rows while marking malformed sibling references uncertain", async () => {
    const malformedContent = [{
      character: ["private-character-id", null],
      text: "仍可确认的文字",
    }] as unknown as Chapter["content"];
    const listChapters = vi.fn(async () => [chapter("mixed-refs", malformedContent)]);
    const { container } = renderPanel(owner(services({ listChapters })));

    expect(await screen.findByText("仍可确认的文字")).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent("部分引用无法核对");
    expect(container.textContent).not.toMatch(/private-character-id|https?:|canonical|voice/);
  });

  it.each([
    ["null content", null as Chapter["content"]],
    ["empty content", [] as Chapter["content"]],
  ])("treats a chapter with %s as an empty result", async (_label, content) => {
    const listChapters = vi.fn(async () => [chapter("empty-chapter", content)]);
    renderPanel(owner(services({ listChapters })));

    expect(await screen.findByText("当前章节快照未找到关联镜头。")).toBeInTheDocument();
    expect(screen.queryByText("部分引用无法核对")).not.toBeInTheDocument();
    expect(listChapters).toHaveBeenCalledTimes(1);
  });

  it("does not cache a superseded success after reread", async () => {
    const user = userEvent.setup();
    const first = deferred<Chapter[]>();
    const second = deferred<Chapter[]>();
    const listChapters = vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const onUnauthorized = vi.fn();
    renderPanel(owner(services({ listChapters })), onUnauthorized);

    expect(await screen.findByText("正在读取当前剧集章节…")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重新读取章节" }));
    await waitFor(() => expect(listChapters).toHaveBeenCalledTimes(2));
    await act(async () => {
      second.resolve([chapter("newer", [{ character: ["private-character-id"], text: "新章节结果" }])]);
      await second.promise;
    });
    expect(await screen.findByText("新章节结果")).toBeInTheDocument();

    await act(async () => {
      first.resolve([chapter("older", [{ character: ["private-character-id"], text: "旧章节结果" }])]);
      await first.promise;
    });
    expect(screen.getByText("新章节结果")).toBeInTheDocument();
    expect(screen.queryByText("旧章节结果")).not.toBeInTheDocument();
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("logs out when the current chapter request settles with 401", async () => {
    const onUnauthorized = vi.fn();
    const listChapters = vi.fn(async () => {
      throw new ApiError("http", "expired", 401);
    });
    renderPanel(owner(services({ listChapters })), onUnauthorized);

    await waitFor(() => expect(onUnauthorized).toHaveBeenCalledTimes(1));
    expect(listChapters).toHaveBeenCalledTimes(1);
  });

  it("permanently hides the old snapshot after an A to B to A scope change", async () => {
    const pending = deferred<Chapter[]>();
    const listChapters = vi.fn(() => pending.promise);
    const currentOwner = owner(services({ listChapters }));
    const view = renderPanel(currentOwner);
    expect(await screen.findByText("正在读取当前剧集章节…")).toBeInTheDocument();

    view.rerender(
      <AssetFrameUsagePanel
        assetLabel="沈照"
        currentScope={{ ...currentOwner, userId: "other-user" }}
        onClose={vi.fn()}
        onUnauthorized={vi.fn()}
        owner={currentOwner}
      />,
    );
    expect(screen.queryByRole("region", { name: "关联镜头" })).not.toBeInTheDocument();
    view.rerender(
      <AssetFrameUsagePanel
        assetLabel="沈照"
        currentScope={currentOwner}
        onClose={vi.fn()}
        onUnauthorized={vi.fn()}
        owner={currentOwner}
      />,
    );
    await act(async () => {
      pending.resolve([chapter("late-chapter", [{ character: ["private-character-id"], text: "迟到快照" }])]);
      await pending.promise;
    });
    expect(screen.queryByRole("region", { name: "关联镜头" })).not.toBeInTheDocument();
    expect(screen.queryByText("迟到快照")).not.toBeInTheDocument();
    expect(listChapters).toHaveBeenCalledTimes(1);
  });

  it("does not let an invalidated scope 401 log out after an A to B to A return", async () => {
    const pending = deferred<Chapter[]>();
    const serviceSet = services({ listChapters: vi.fn(() => pending.promise) });
    const currentOwner = owner(serviceSet);
    const onUnauthorized = vi.fn();
    const view = render(
      <AssetFrameUsagePanel
        assetLabel="沈照"
        currentScope={currentOwner}
        onClose={vi.fn()}
        onUnauthorized={onUnauthorized}
        owner={currentOwner}
      />,
    );

    await waitFor(() => expect(serviceSet.listChapters).toHaveBeenCalledTimes(1));
    view.rerender(
      <AssetFrameUsagePanel
        assetLabel="沈照"
        currentScope={{ ...currentOwner, userId: "other-user" }}
        onClose={vi.fn()}
        onUnauthorized={onUnauthorized}
        owner={currentOwner}
      />,
    );
    view.rerender(
      <AssetFrameUsagePanel
        assetLabel="沈照"
        currentScope={currentOwner}
        onClose={vi.fn()}
        onUnauthorized={onUnauthorized}
        owner={currentOwner}
      />,
    );
    await act(async () => {
      pending.reject(new ApiError("http", "expired", 401));
      await pending.promise.catch(() => undefined);
    });

    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(screen.queryByRole("region", { name: "关联镜头" })).not.toBeInTheDocument();
    expect(serviceSet.listChapters).toHaveBeenCalledTimes(1);
  });
});
