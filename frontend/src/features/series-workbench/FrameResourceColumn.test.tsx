import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Character, Prop, StoryboardAsset, StoryboardFrame } from "../../shared/api/contracts";
import { FrameResourceColumn } from "./FrameResourceColumn";

const character: Character = {
  id: "character-a", series_id: "series-a", name: "角色甲", aliases: null, canonical_key: null,
  description: null, image_url: "/character.png", created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
  gender: null, age: null, role: null, appearance: null, audio_url: null, voice_ref: null,
};

const prop: Prop = {
  id: "prop-a", series_id: "series-a", name: "道具甲", aliases: null, canonical_key: null,
  description: null, image_url: "/prop.png", created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
};

const storyboard: StoryboardAsset = {
  id: "frame-a", series_id: "series-a", chapter_id: "chapter-a", frame_index: 0, name: "镜头甲",
  description: null, image_url: null, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
};

function renderColumn(frame: StoryboardFrame, onFuseMemberType = vi.fn()) {
  return {
    onFuseMemberType,
    ...render(
      <FrameResourceColumn
        apiBaseUrl="http://localhost/api"
        chapterFrames={[frame]}
        chapterId="chapter-a"
        directories={{
          storyboard: { status: "ready", items: [storyboard] },
          characters: { status: "ready", items: [character] },
          scenes: { status: "ready", items: [] },
          props: { status: "ready", items: [prop] },
          fused: { status: "ready", items: [] },
        }}
        frame={frame}
        frameIndex={0}
        onFuseMemberType={onFuseMemberType}
        seriesId="series-a"
      />,
    ),
  };
}

describe("FrameResourceColumn fused source actions", () => {
  afterEach(cleanup);

  it("offers separate role and prop actions while preserving the original source selection", () => {
    const frame: StoryboardFrame = {
      storyboard: ["frame-a"],
      character: ["character-a"],
      prop: ["prop-a"],
    };
    const onFuse = vi.fn();
    const onSelect = vi.fn();
    const view = renderColumn(frame, onFuse);
    view.rerender(
      <FrameResourceColumn
        apiBaseUrl="http://localhost/api"
        chapterFrames={[frame]}
        chapterId="chapter-a"
        directories={{
          storyboard: { status: "ready", items: [storyboard] },
          characters: { status: "ready", items: [character] },
          scenes: { status: "ready", items: [] },
          props: { status: "ready", items: [prop] },
          fused: { status: "ready", items: [] },
        }}
        frame={frame}
        frameIndex={0}
        onFuseMemberType={onFuse}
        onSelect={onSelect}
        seriesId="series-a"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "融合镜头角色" }));
    fireEvent.click(screen.getByRole("button", { name: "融合镜头道具" }));
    fireEvent.click(screen.getByRole("button", { name: "角色甲" }));

    expect(onFuse).toHaveBeenNthCalledWith(1, "character");
    expect(onFuse).toHaveBeenNthCalledWith(2, "prop");
    expect(onSelect).toHaveBeenCalledWith({
      kind: "asset", chapterId: "chapter-a", category: "characters", assetId: "character-a",
    });
  });

  it("disables a member action when the real linked directory item cannot be uniquely resolved", () => {
    const view = renderColumn({ storyboard: ["frame-a"], character: ["missing-character"] });
    const actionRegion = screen.getByLabelText("镜头 1 的融合入口");
    expect(within(actionRegion).getByRole("button", { name: "融合镜头角色" })).toBeDisabled();
    expect(within(actionRegion).getByText("当前镜头的关联素材无法唯一核对。")).toBeInTheDocument();
    expect(view.onFuseMemberType).not.toHaveBeenCalled();
  });

  it("disables a member action when a linked asset has no image for fusion", () => {
    const frame: StoryboardFrame = { storyboard: ["frame-a"], character: ["character-a"] };
    render(
      <FrameResourceColumn
        apiBaseUrl="http://localhost/api"
        chapterFrames={[frame]}
        chapterId="chapter-a"
        directories={{
          storyboard: { status: "ready", items: [storyboard] },
          characters: { status: "ready", items: [{ ...character, image_url: null }] },
          scenes: { status: "ready", items: [] },
          props: { status: "ready", items: [prop] },
          fused: { status: "ready", items: [] },
        }}
        frame={frame}
        frameIndex={0}
        onFuseMemberType={vi.fn()}
        seriesId="series-a"
      />,
    );
    expect(screen.getByRole("button", { name: "融合镜头角色" })).toBeDisabled();
    expect(screen.getByText("当前镜头的关联素材缺少可用图片。")).toBeInTheDocument();
  });

  it("keeps fused actions unavailable until the storyboard directory is ready", () => {
    const frame: StoryboardFrame = { storyboard: ["frame-a"], character: ["character-a"] };
    render(
      <FrameResourceColumn
        apiBaseUrl="http://localhost/api"
        chapterFrames={[frame]}
        chapterId="chapter-a"
        directories={{
          storyboard: { status: "loading" },
          characters: { status: "ready", items: [character] },
          scenes: { status: "ready", items: [] },
          props: { status: "ready", items: [prop] },
          fused: { status: "ready", items: [] },
        }}
        frame={frame}
        frameIndex={0}
        onFuseMemberType={vi.fn()}
        seriesId="series-a"
      />,
    );
    expect(screen.getByRole("button", { name: "融合镜头角色" })).toBeDisabled();
    expect(screen.getAllByText("正在读取分镜目录。")).toHaveLength(2);
  });

  it("allows opening a valid frame target with no linked members for manual selection", () => {
    const frame: StoryboardFrame = { storyboard: ["frame-a"], character: [] };
    const view = renderColumn(frame);
    const roleAction = screen.getByRole("button", { name: "融合镜头角色" });
    expect(roleAction).toBeEnabled();
    expect(screen.getAllByText("当前镜头未关联素材，可进入后手动选择。")).toHaveLength(2);
    fireEvent.click(roleAction);
    expect(view.onFuseMemberType).toHaveBeenCalledWith("character");
  });
});
