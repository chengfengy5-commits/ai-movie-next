import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChatAssistantSplitPane } from "./ChatAssistantSplitPane";
import {
  chatAssistantSplitPanePreferenceKey,
  writeChatAssistantSplitPanePreference,
} from "./chatAssistantSplitPanePreferences";

const actorScope = { userId: "actor-split-1070", apiBaseUrl: "https://api.example.test/api" };

function renderPane(
  scope: { readonly userId: string; readonly apiBaseUrl: string },
  sessionGeneration = 1,
) {
  return render(
    <ChatAssistantSplitPane
      apiBaseUrl={scope.apiBaseUrl}
      input={<form aria-label="聊天输入区域"><textarea aria-label="测试输入框" /></form>}
      messages={<div aria-label="聊天记录演示">聊天记录</div>}
      sessionGeneration={sessionGeneration}
      userId={scope.userId}
    />,
  );
}

function setRectangle(element: HTMLElement, top: number, height: number): void {
  vi.spyOn(element, "getBoundingClientRect").mockReturnValue({
    x: 0,
    y: top,
    top,
    left: 0,
    right: 500,
    bottom: top + height,
    width: 500,
    height,
    toJSON: () => ({}),
  } as DOMRect);
}

function pointerEvent(
  target: EventTarget,
  type: "pointerdown" | "pointermove" | "pointerup",
  options: { readonly pointerId: number; readonly clientY: number; readonly pointerType?: string },
): void {
  const event = new Event(type, { bubbles: true, cancelable: true });
  Object.defineProperties(event, {
    button: { value: 0 },
    clientY: { value: options.clientY },
    isPrimary: { value: true },
    pointerId: { value: options.pointerId },
    pointerType: { value: options.pointerType ?? "mouse" },
  });
  act(() => target.dispatchEvent(event));
}

function setPointerCaptureMocks(separator: HTMLElement) {
  const setPointerCapture = vi.fn();
  const hasPointerCapture = vi.fn(() => true);
  const releasePointerCapture = vi.fn();
  Object.defineProperties(separator, {
    setPointerCapture: { configurable: true, value: setPointerCapture },
    hasPointerCapture: { configurable: true, value: hasPointerCapture },
    releasePointerCapture: { configurable: true, value: releasePointerCapture },
  });
  return { setPointerCapture, hasPointerCapture, releasePointerCapture };
}

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.restoreAllMocks();
});

describe("ChatAssistantSplitPane", () => {
  it("changes the real pane ratio by pointer and restores it after reopening", async () => {
    const rendered = renderPane(actorScope);
    const root = screen.getByTestId("chat-assistant-split-pane");
    const separator = await screen.findByRole("separator", { name: "调整聊天记录与输入区域比例" });
    setRectangle(root, 100, 500);
    setRectangle(separator, 0, 12);
    const capture = setPointerCaptureMocks(separator);

    pointerEvent(separator, "pointerdown", { pointerId: 7, clientY: 200 });
    pointerEvent(window, "pointermove", { pointerId: 7, clientY: 100 + (500 - 12) * 0.73 });
    expect(separator).toHaveAttribute("aria-valuenow", "73");
    pointerEvent(window, "pointerup", { pointerId: 7, clientY: 456 });

    const key = chatAssistantSplitPanePreferenceKey(actorScope)!;
    expect(window.localStorage.getItem(key)).toBe("73");
    expect(capture.setPointerCapture).toHaveBeenCalledWith(7);
    expect(capture.releasePointerCapture).toHaveBeenCalledWith(7);

    rendered.unmount();
    renderPane(actorScope);
    await waitFor(() => expect(screen.getByRole("separator", { name: "调整聊天记录与输入区域比例" }))
      .toHaveAttribute("aria-valuenow", "73"));
    expect(screen.getByLabelText("聊天记录演示")).toBeInTheDocument();
    expect(screen.getByLabelText("测试输入框")).toBeInTheDocument();
  });

  it("keeps keyboard changes accessible, bounded, persisted, and resettable", async () => {
    renderPane(actorScope);
    const separator = await screen.findByRole("separator", { name: "调整聊天记录与输入区域比例" });
    expect(separator).toHaveAttribute("aria-orientation", "horizontal");
    expect(separator).toHaveAttribute("aria-valuemin", "10");
    expect(separator).toHaveAttribute("aria-valuemax", "90");
    expect(separator).toHaveAttribute("tabindex", "0");

    fireEvent.keyDown(separator, { key: "ArrowUp" });
    expect(separator).toHaveAttribute("aria-valuenow", "67");
    expect(window.localStorage.getItem(chatAssistantSplitPanePreferenceKey(actorScope)!)).toBe("67");

    fireEvent.keyDown(separator, { key: "End" });
    expect(separator).toHaveAttribute("aria-valuenow", "90");
    fireEvent.click(screen.getByRole("button", { name: "重置分隔比例" }));
    expect(separator).toHaveAttribute("aria-valuenow", "62");
    expect(window.localStorage.getItem(chatAssistantSplitPanePreferenceKey(actorScope)!)).toBe("62");
  });

  it("isolates restoration across actors and API bases", async () => {
    const otherActor = { ...actorScope, userId: "actor-split-other" };
    const otherApi = { ...actorScope, apiBaseUrl: "https://other.example.test/api" };
    writeChatAssistantSplitPanePreference(actorScope, 72);
    writeChatAssistantSplitPanePreference(otherActor, 43);
    writeChatAssistantSplitPanePreference(otherApi, 31);
    const rendered = renderPane(actorScope);
    const separator = await screen.findByRole("separator", { name: "调整聊天记录与输入区域比例" });
    await waitFor(() => expect(separator).toHaveAttribute("aria-valuenow", "72"));

    rendered.rerender(
      <ChatAssistantSplitPane apiBaseUrl={otherActor.apiBaseUrl} input={<textarea />} messages={<p>记录</p>}
        sessionGeneration={1} userId={otherActor.userId} />,
    );
    await waitFor(() => expect(separator).toHaveAttribute("aria-valuenow", "43"));

    rendered.rerender(
      <ChatAssistantSplitPane apiBaseUrl={otherApi.apiBaseUrl} input={<textarea />} messages={<p>记录</p>}
        sessionGeneration={1} userId={otherApi.userId} />,
    );
    await waitFor(() => expect(separator).toHaveAttribute("aria-valuenow", "31"));
  });

  it("keeps manual adjustment available with malformed or unavailable storage", async () => {
    const key = chatAssistantSplitPanePreferenceKey(actorScope)!;
    window.localStorage.setItem(key, "99");
    const originalSetItem = Storage.prototype.setItem;
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(function setItem(this: Storage, itemKey: string, value: string) {
      if (itemKey.startsWith("haoai:chat-assistant-split:v1:")) throw new Error("storage blocked");
      return originalSetItem.call(this, itemKey, value);
    });
    renderPane(actorScope);
    const separator = await screen.findByRole("separator", { name: "调整聊天记录与输入区域比例" });
    expect(separator).toHaveAttribute("aria-valuenow", "62");
    expect(screen.getByRole("status")).toHaveTextContent("比例无效");

    fireEvent.keyDown(separator, { key: "ArrowDown" });
    expect(separator).toHaveAttribute("aria-valuenow", "57");
    expect(screen.getByRole("status")).toHaveTextContent("存储不可用");
    expect(window.localStorage.getItem(key)).toBe("99");
  });

  it("handles a throwing storage getter and still applies keyboard input", async () => {
    const descriptor = Object.getOwnPropertyDescriptor(window, "localStorage");
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      get() { throw new Error("storage getter blocked"); },
    });
    try {
      const rendered = renderPane(actorScope);
      const separator = await screen.findByRole("separator", { name: "调整聊天记录与输入区域比例" });
      expect(screen.getByRole("status")).toHaveTextContent("存储不可用");
      fireEvent.keyDown(separator, { key: "ArrowUp" });
      expect(separator).toHaveAttribute("aria-valuenow", "67");
      expect(screen.getByRole("status")).toHaveTextContent("未保存");
      rendered.unmount();
    } finally {
      if (descriptor !== undefined) Object.defineProperty(window, "localStorage", descriptor);
    }
  });

  it("cancels an old pointer owner on scope change and ignores later pointer events", async () => {
    const otherActor = { ...actorScope, userId: "actor-split-next" };
    writeChatAssistantSplitPanePreference(otherActor, 45);
    const rendered = renderPane(actorScope);
    const root = screen.getByTestId("chat-assistant-split-pane");
    const separator = await screen.findByRole("separator", { name: "调整聊天记录与输入区域比例" });
    setRectangle(root, 100, 500);
    setRectangle(separator, 0, 12);
    const capture = setPointerCaptureMocks(separator);
    pointerEvent(separator, "pointerdown", { pointerId: 9, clientY: 150 });
    pointerEvent(window, "pointermove", { pointerId: 9, clientY: 100 + (500 - 12) * 0.77 });
    expect(separator).toHaveAttribute("aria-valuenow", "77");

    rendered.rerender(
      <ChatAssistantSplitPane apiBaseUrl={otherActor.apiBaseUrl} input={<textarea />} messages={<p>记录</p>}
        sessionGeneration={2} userId={otherActor.userId} />,
    );
    await waitFor(() => expect(separator).toHaveAttribute("aria-valuenow", "45"));
    pointerEvent(window, "pointermove", { pointerId: 9, clientY: 100 + (500 - 12) * 0.2 });
    pointerEvent(window, "pointerup", { pointerId: 9, clientY: 198 });
    expect(separator).toHaveAttribute("aria-valuenow", "45");
    expect(window.localStorage.getItem(chatAssistantSplitPanePreferenceKey(otherActor)!)).toBe("45");
    expect(capture.releasePointerCapture).toHaveBeenCalledWith(9);
  });

  it("cancels a drag when hidden or inert so old pointer events cannot save", async () => {
    const rendered = renderPane(actorScope);
    const root = screen.getByTestId("chat-assistant-split-pane");
    const separator = await screen.findByRole("separator", { name: "调整聊天记录与输入区域比例" });
    setRectangle(root, 100, 500);
    setRectangle(separator, 0, 12);
    pointerEvent(separator, "pointerdown", { pointerId: 11, clientY: 150 });
    pointerEvent(window, "pointermove", { pointerId: 11, clientY: 100 + (500 - 12) * 0.74 });
    expect(separator).toHaveAttribute("aria-valuenow", "74");

    root.setAttribute("inert", "");
    pointerEvent(window, "pointermove", { pointerId: 11, clientY: 100 + (500 - 12) * 0.2 });
    pointerEvent(window, "pointerup", { pointerId: 11, clientY: 200 });
    expect(separator).toHaveAttribute("aria-valuenow", "62");
    expect(window.localStorage.getItem(chatAssistantSplitPanePreferenceKey(actorScope)!)).toBeNull();

    rendered.unmount();
    const hiddenPage = renderPane(actorScope);
    const hiddenRoot = screen.getByTestId("chat-assistant-split-pane");
    const hiddenDivider = screen.getByRole("separator", { name: "调整聊天记录与输入区域比例" });
    setRectangle(hiddenRoot, 100, 500);
    setRectangle(hiddenDivider, 0, 12);
    pointerEvent(hiddenDivider, "pointerdown", { pointerId: 12, clientY: 150 });
    pointerEvent(window, "pointermove", { pointerId: 12, clientY: 100 + (500 - 12) * 0.74 });
    hiddenRoot.setAttribute("hidden", "");
    pointerEvent(window, "pointermove", { pointerId: 12, clientY: 100 + (500 - 12) * 0.2 });
    expect(hiddenDivider).toHaveAttribute("aria-valuenow", "62");
    expect(window.localStorage.getItem(chatAssistantSplitPanePreferenceKey(actorScope)!)).toBeNull();
    hiddenPage.unmount();
  });

  it("lets keyboard and reset actions supersede an active pointer drag", async () => {
    const rendered = renderPane(actorScope);
    const root = screen.getByTestId("chat-assistant-split-pane");
    const separator = await screen.findByRole("separator", { name: "调整聊天记录与输入区域比例" });
    setRectangle(root, 100, 500);
    setRectangle(separator, 0, 12);
    pointerEvent(separator, "pointerdown", { pointerId: 13, clientY: 150 });
    pointerEvent(window, "pointermove", { pointerId: 13, clientY: 100 + (500 - 12) * 0.72 });
    fireEvent.keyDown(separator, { key: "ArrowUp" });
    expect(separator).toHaveAttribute("aria-valuenow", "77");
    pointerEvent(window, "pointermove", { pointerId: 13, clientY: 100 + (500 - 12) * 0.2 });
    pointerEvent(window, "pointerup", { pointerId: 13, clientY: 200 });
    expect(separator).toHaveAttribute("aria-valuenow", "77");
    expect(window.localStorage.getItem(chatAssistantSplitPanePreferenceKey(actorScope)!)).toBe("77");

    pointerEvent(separator, "pointerdown", { pointerId: 14, clientY: 150 });
    pointerEvent(window, "pointermove", { pointerId: 14, clientY: 100 + (500 - 12) * 0.75 });
    fireEvent.click(screen.getByRole("button", { name: "重置分隔比例" }));
    pointerEvent(window, "pointermove", { pointerId: 14, clientY: 100 + (500 - 12) * 0.2 });
    pointerEvent(window, "pointerup", { pointerId: 14, clientY: 200 });
    expect(separator).toHaveAttribute("aria-valuenow", "62");
    expect(window.localStorage.getItem(chatAssistantSplitPanePreferenceKey(actorScope)!)).toBe("62");
    rendered.unmount();
  });
});
