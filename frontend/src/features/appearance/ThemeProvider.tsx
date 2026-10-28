import {
  createContext,
  useCallback,
  useContext,
  useLayoutEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  readThemePreference,
  writeThemePreference,
  type ThemePreference,
} from "./themePreference";

export type ResolvedTheme = "light" | "dark";

interface AppearanceContextValue {
  preference: ThemePreference;
  storageAvailable: boolean;
  setPreference(preference: ThemePreference): void;
}

const AppearanceContext = createContext<AppearanceContextValue | null>(null);
const SYSTEM_DARK_QUERY = "(prefers-color-scheme: dark)";

export function resolveTheme(preference: ThemePreference, systemPrefersDark: boolean): ResolvedTheme {
  if (preference === "auto") {
    return systemPrefersDark ? "dark" : "light";
  }
  return preference;
}

export function AppearanceProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState(() => readThemePreference());

  useLayoutEffect(() => {
    const root = document.documentElement;
    let mediaQuery: MediaQueryList | null = null;
    let listeningWithEventTarget = false;

    if (state.preference === "auto" && typeof window.matchMedia === "function") {
      try {
        mediaQuery = window.matchMedia(SYSTEM_DARK_QUERY);
      } catch {
        mediaQuery = null;
      }
    }

    const applyTheme = () => {
      const resolved = resolveTheme(state.preference, mediaQuery?.matches ?? false);
      root.dataset.theme = resolved;
      root.style.colorScheme = resolved;
    };
    const onSystemThemeChange = () => applyTheme();

    if (state.preference === "auto" && mediaQuery !== null) {
      if (typeof mediaQuery.addEventListener === "function") {
        mediaQuery.addEventListener("change", onSystemThemeChange);
        listeningWithEventTarget = true;
      } else if (typeof mediaQuery.addListener === "function") {
        mediaQuery.addListener(onSystemThemeChange);
      }
    }
    applyTheme();

    return () => {
      if (mediaQuery === null) return;
      if (listeningWithEventTarget) {
        mediaQuery.removeEventListener("change", onSystemThemeChange);
      } else if (typeof mediaQuery.removeListener === "function") {
        mediaQuery.removeListener(onSystemThemeChange);
      }
    };
  }, [state.preference]);

  const setPreference = useCallback((preference: ThemePreference) => {
    const storageAvailable = writeThemePreference(preference);
    setState({ preference, storageAvailable });
  }, []);

  const value = useMemo<AppearanceContextValue>(() => ({
    preference: state.preference,
    storageAvailable: state.storageAvailable,
    setPreference,
  }), [setPreference, state.preference, state.storageAvailable]);

  return (
    <AppearanceContext.Provider value={value}>
      {children}
    </AppearanceContext.Provider>
  );
}

export function useAppearance(): AppearanceContextValue {
  const value = useContext(AppearanceContext);
  if (value === null) {
    throw new Error("useAppearance must be used within AppearanceProvider.");
  }
  return value;
}
