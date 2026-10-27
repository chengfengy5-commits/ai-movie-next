import { fireEvent, render, within } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { SeriesWorkbenchSurface } from "./SeriesWorkbenchSurface";

describe("SeriesWorkbenchSurface", () => {
  it("commits a completed pointer resize and restores the starting width on pointer cancel", () => {
    const commits: Array<[string, number, string]> = [];

    function Harness() {
      const [leftWidth, setLeftWidth] = useState(70);
      return (
        <SeriesWorkbenchSurface
          chat={<div>聊天区</div>}
          leftView="storyboard"
          leftWidth={leftWidth}
          media={<div>媒体区</div>}
          mode="workbench"
          ownerKey="scope-a"
          onLeftViewChange={() => undefined}
          onRouteChange={() => undefined}
          onWidthChange={(view, width) => { if (view === "storyboard") setLeftWidth(width); }}
          onWidthCommit={(view, width, ownerKey) => commits.push([view, width, ownerKey])}
          preferenceNotice={null}
          route="chat"
          storyboard={<div>分镜区</div>}
          assets={<div>素材库</div>}
        />
      );
    }

    const { container } = render(<Harness />);
    const surface = container.querySelector(".series-workbench-surface");
    if (!(surface instanceof HTMLElement)) throw new Error("The workbench surface did not render.");
    vi.spyOn(surface, "getBoundingClientRect").mockReturnValue({ width: 1_000 } as DOMRect);

    const separator = within(container).getByRole("separator", { name: "调整剧集内容与创作区宽度" });
    const setCapture = vi.fn();
    const hasCapture = vi.fn(() => true);
    const releaseCapture = vi.fn();
    Object.defineProperties(separator, {
      setPointerCapture: { configurable: true, value: setCapture },
      hasPointerCapture: { configurable: true, value: hasCapture },
      releasePointerCapture: { configurable: true, value: releaseCapture },
    });

    fireEvent.pointerDown(separator, { pointerId: 41, button: 0, isPrimary: true, clientX: 100 });
    fireEvent.pointerMove(separator, { pointerId: 41, clientX: 200 });
    expect(separator).toHaveAttribute("aria-valuenow", "80");
    fireEvent.pointerCancel(separator, { pointerId: 41 });
    expect(separator).toHaveAttribute("aria-valuenow", "70");
    expect(commits).toEqual([]);

    fireEvent.pointerDown(separator, { pointerId: 42, button: 0, isPrimary: true, clientX: 100 });
    fireEvent.pointerMove(separator, { pointerId: 42, clientX: 180 });
    fireEvent.pointerUp(separator, { pointerId: 42 });
    expect(separator).toHaveAttribute("aria-valuenow", "78");
    expect(commits).toEqual([["storyboard", 78, "scope-a"]]);
    expect(setCapture).toHaveBeenCalledTimes(2);
    expect(releaseCapture).toHaveBeenCalledTimes(2);

    fireEvent.pointerDown(separator, { pointerId: 43, button: 0, isPrimary: true, clientX: 100 });
    fireEvent.pointerMove(separator, { pointerId: 43, clientX: 200 });
    fireEvent.lostPointerCapture(separator, { pointerId: 43 });
    expect(separator).toHaveAttribute("aria-valuenow", "78");
    expect(commits).toEqual([["storyboard", 78, "scope-a"]]);
  });

  it("cancels stale drags across owner and view changes and lets keyboard sizing supersede a drag", () => {
    const changes: Array<[string, number, string]> = [];
    const commits: Array<[string, number, string]> = [];
    const onWidthChange = (view: string, width: number, ownerKey: string) => changes.push([view, width, ownerKey]);
    const onWidthCommit = (view: string, width: number, ownerKey: string) => commits.push([view, width, ownerKey]);
    const shared = {
      chat: <div>聊天区</div>,
      media: <div>媒体区</div>,
      mode: "workbench" as const,
      onLeftViewChange: () => undefined,
      onRouteChange: () => undefined,
      onWidthChange,
      onWidthCommit,
      preferenceNotice: null,
      route: "chat" as const,
      storyboard: <div>分镜区</div>,
      assets: <div>素材库</div>,
    };
    const { container, rerender } = render(
      <SeriesWorkbenchSurface {...shared} ownerKey="scope-a" leftView="storyboard" leftWidth={70} />,
    );
    const separator = within(container).getByRole("separator", { name: "调整剧集内容与创作区宽度" });
    const setCapture = vi.fn();
    const hasCapture = vi.fn(() => true);
    const releaseCapture = vi.fn();
    Object.defineProperties(separator, {
      setPointerCapture: { configurable: true, value: setCapture },
      hasPointerCapture: { configurable: true, value: hasCapture },
      releasePointerCapture: { configurable: true, value: releaseCapture },
    });
    const surface = container.querySelector(".series-workbench-surface");
    if (!(surface instanceof HTMLElement)) throw new Error("The workbench surface did not render.");
    vi.spyOn(surface, "getBoundingClientRect").mockReturnValue({ width: 1_000 } as DOMRect);

    fireEvent.pointerDown(separator, { pointerId: 51, button: 0, isPrimary: true, clientX: 100 });
    fireEvent.pointerMove(separator, { pointerId: 51, clientX: 200 });
    expect(changes.at(-1)).toEqual(["storyboard", 80, "scope-a"]);
    const beforeScopeChange = changes.length;
    rerender(<SeriesWorkbenchSurface {...shared} ownerKey="scope-b" leftView="assets" leftWidth={70} />);
    expect(changes).toHaveLength(beforeScopeChange);
    fireEvent.pointerUp(separator, { pointerId: 51 });
    expect(commits).toEqual([]);

    fireEvent.pointerDown(separator, { pointerId: 52, button: 0, isPrimary: true, clientX: 100 });
    fireEvent.pointerMove(separator, { pointerId: 52, clientX: 180 });
    rerender(<SeriesWorkbenchSurface {...shared} ownerKey="scope-b" leftView="storyboard" leftWidth={70} />);
    expect(changes.at(-1)).toEqual(["assets", 70, "scope-b"]);
    fireEvent.pointerCancel(separator, { pointerId: 52 });
    expect(commits).toEqual([]);

    fireEvent.pointerDown(separator, { pointerId: 53, button: 0, isPrimary: true, clientX: 100 });
    fireEvent.pointerMove(separator, { pointerId: 53, clientX: 200 });
    fireEvent.keyDown(separator, { key: "ArrowRight" });
    expect(changes.slice(-2)).toEqual([
      ["storyboard", 70, "scope-b"],
      ["storyboard", 72, "scope-b"],
    ]);
    expect(commits).toEqual([["storyboard", 72, "scope-b"]]);
    fireEvent.pointerCancel(separator, { pointerId: 53 });
    expect(commits).toEqual([["storyboard", 72, "scope-b"]]);

    fireEvent.pointerDown(separator, { pointerId: 54, button: 0, isPrimary: true, clientX: 100 });
    fireEvent.pointerMove(separator, { pointerId: 54, clientX: 200 });
    rerender(<SeriesWorkbenchSurface {...shared} mode="hidden" ownerKey="scope-b" leftView="storyboard" leftWidth={72} />);
    fireEvent.pointerUp(separator, { pointerId: 54 });
    expect(commits).toEqual([["storyboard", 72, "scope-b"]]);
  });
});
