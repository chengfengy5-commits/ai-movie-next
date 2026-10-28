// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppearanceControl } from "./AppearanceControl";
import { AppearanceProvider } from "./ThemeProvider";
import { THEME_PREFERENCE_STORAGE_KEY } from "./themePreference";

const SYSTEM_DARK_QUERY = "(prefers-color-scheme: dark)";

function createMatchMedia(initialMatches: boolean) {
  let systemPrefersDark = initialMatches;
  const listeners = new Set<(event: MediaQueryListEvent) => void>();
  const query = {
    media: SYSTEM_DARK_QUERY,
    onchange: null,
    get matches() {
      return systemPrefersDark;
    },
    addEventListener: vi.fn((_type: string, listener: EventListenerOrEventListenerObject) => {
      listeners.add(listener as (event: MediaQueryListEvent) => void);
    }),
    removeEventListener: vi.fn((_type: string, listener: EventListenerOrEventListenerObject) => {
      listeners.delete(listener as (event: MediaQueryListEvent) => void);
    }),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(() => true),
  } as unknown as MediaQueryList;

  return {
    query,
    listeners,
    setSystemPrefersDark(value: boolean) {
      systemPrefersDark = value;
      const event = new Event("change") as MediaQueryListEvent;
      for (const listener of [...listeners]) listener(event);
    },
  };
}

describe("AppearanceProvider", () => {
  let originalMatchMedia: PropertyDescriptor | undefined;

  beforeEach(() => {
    window.localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
    document.documentElement.style.colorScheme = "";
    originalMatchMedia = Object.getOwnPropertyDescriptor(window, "matchMedia");
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    window.localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
    document.documentElement.style.colorScheme = "";
    if (originalMatchMedia === undefined) {
      Reflect.deleteProperty(window, "matchMedia");
    } else {
      Object.defineProperty(window, "matchMedia", originalMatchMedia);
    }
  });

  it("restores auto, follows system changes, and removes the listener on unmount", async () => {
    window.localStorage.setItem(THEME_PREFERENCE_STORAGE_KEY, "auto");
    const system = createMatchMedia(true);
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi.fn(() => system.query),
    });
    const view = render(
      <AppearanceProvider>
        <AppearanceControl />
      </AppearanceProvider>,
    );

    await waitFor(() => expect(document.documentElement).toHaveAttribute("data-theme", "dark"));
    expect(screen.getByRole("combobox", { name: "外观" })).toHaveValue("auto");
    expect(system.query.addEventListener).toHaveBeenCalledOnce();

    act(() => system.setSystemPrefersDark(false));
    await waitFor(() => expect(document.documentElement).toHaveAttribute("data-theme", "light"));

    view.unmount();
    expect(system.query.removeEventListener).toHaveBeenCalledOnce();
    expect(system.listeners.size).toBe(0);
  });

  it("keeps a manual dark choice when the system mode changes", async () => {
    window.localStorage.setItem(THEME_PREFERENCE_STORAGE_KEY, "dark");
    const matchMedia = vi.fn(() => createMatchMedia(false).query);
    Object.defineProperty(window, "matchMedia", { configurable: true, value: matchMedia });
    render(
      <AppearanceProvider>
        <AppearanceControl />
      </AppearanceProvider>,
    );

    await waitFor(() => expect(document.documentElement).toHaveAttribute("data-theme", "dark"));
    expect(matchMedia).not.toHaveBeenCalled();
  });

  it("applies choices in the current page and reports that storage did not save them", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("Storage is disabled", "SecurityError");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("Storage is disabled", "SecurityError");
    });
    render(
      <AppearanceProvider>
        <AppearanceControl />
      </AppearanceProvider>,
    );

    expect(await screen.findByRole("status")).toHaveTextContent("浏览器无法保存外观偏好；当前页面仍会应用选择。");
    fireEvent.change(screen.getByRole("combobox", { name: "外观" }), { target: { value: "dark" } });

    await waitFor(() => expect(document.documentElement).toHaveAttribute("data-theme", "dark"));
    expect(screen.getByRole("status")).toHaveTextContent("当前页面仍会应用选择");
  });
});
