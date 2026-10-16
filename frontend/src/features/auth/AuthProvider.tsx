import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { ApiError } from "../../shared/api/errors";
import {
  isCancelled,
  normalizeApiError,
  type WorkspaceServices,
} from "../../shared/api/services";
import type { Credentials, User } from "../../shared/api/contracts";

type AuthState =
  | { status: "restoring" }
  | { status: "anonymous"; error: ApiError | null }
  | { status: "restore-error"; error: ApiError }
  | { status: "authenticated"; user: User };

interface AuthContextValue {
  services: WorkspaceServices;
  state: AuthState;
  sessionGeneration: number;
  refreshCurrentUser(signal: AbortSignal): Promise<User>;
  login(credentials: Credentials): Promise<void>;
  logout(): void;
  retryRestore(): void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

interface AuthProviderProps {
  services: WorkspaceServices;
  children: ReactNode;
}

export function AuthProvider({ services, children }: AuthProviderProps) {
  const [state, setState] = useState<AuthState>({ status: "restoring" });
  const [sessionGeneration, setSessionGeneration] = useState(0);
  const stateRef = useRef(state);
  stateRef.current = state;
  const servicesRef = useRef(services);
  servicesRef.current = services;
  const generation = useRef(0);
  const sessionGenerationRef = useRef(0);
  const refreshSequence = useRef(0);
  const latestRefresh = useRef<{ sequence: number; user: User } | null>(null);
  const loginInFlight = useRef(false);
  const loginAttempt = useRef(0);
  const pendingControllers = useRef(new Set<AbortController>());
  const activeRefreshController = useRef<AbortController | null>(null);

  const advanceSessionGeneration = useCallback(() => {
    const next = sessionGenerationRef.current + 1;
    sessionGenerationRef.current = next;
    latestRefresh.current = null;
    setSessionGeneration(next);
  }, []);

  const abortPending = useCallback(() => {
    activeRefreshController.current = null;
    for (const controller of pendingControllers.current) {
      controller.abort();
    }
    pendingControllers.current.clear();
  }, []);

  const runRestore = useCallback(() => {
    const ticket = ++generation.current;
    advanceSessionGeneration();
    abortPending();
    const controller = new AbortController();
    pendingControllers.current.add(controller);
    setState({ status: "restoring" });

    void services.restore(controller.signal).then((user) => {
      if (ticket !== generation.current || controller.signal.aborted) {
        return;
      }
      setState(user ? { status: "authenticated", user } : { status: "anonymous", error: null });
    }).catch((error: unknown) => {
      if (ticket !== generation.current || controller.signal.aborted || isCancelled(error)) {
        return;
      }
      setState({ status: "restore-error", error: normalizeApiError(error) });
    }).finally(() => {
      pendingControllers.current.delete(controller);
    });
  }, [abortPending, advanceSessionGeneration, services]);

  useEffect(() => {
    runRestore();
    return () => {
      generation.current += 1;
      abortPending();
    };
  }, [abortPending, runRestore]);

  const login = useCallback(async (credentials: Credentials) => {
    if (loginInFlight.current) {
      return;
    }
    loginInFlight.current = true;
    const attempt = ++loginAttempt.current;
    const ticket = ++generation.current;
    advanceSessionGeneration();
    abortPending();
    const controller = new AbortController();
    pendingControllers.current.add(controller);
    setState({ status: "restoring" });

    try {
      const user = await services.login(credentials, controller.signal);
      if (ticket === generation.current && !controller.signal.aborted) {
        setState({ status: "authenticated", user });
      }
    } catch (error) {
      if (ticket === generation.current && !controller.signal.aborted && !isCancelled(error)) {
        setState({ status: "anonymous", error: normalizeApiError(error) });
      }
    } finally {
      pendingControllers.current.delete(controller);
      if (loginAttempt.current === attempt) {
        loginInFlight.current = false;
      }
    }
  }, [abortPending, advanceSessionGeneration, services]);

  const logout = useCallback(() => {
    generation.current += 1;
    advanceSessionGeneration();
    loginAttempt.current += 1;
    loginInFlight.current = false;
    abortPending();
    services.logout();
    setState({ status: "anonymous", error: null });
  }, [abortPending, advanceSessionGeneration, services]);

  const refreshCurrentUser = useCallback(async (signal: AbortSignal): Promise<User> => {
    const current = stateRef.current;
    if (current.status !== "authenticated") {
      throw new ApiError("aborted", "当前登录会话已结束。");
    }
    if (signal.aborted) {
      throw new ApiError("aborted", "Request was cancelled.");
    }

    const captured = {
      generation: generation.current,
      sessionGeneration: sessionGenerationRef.current,
      services,
      userId: current.user.id,
      sequence: ++refreshSequence.current,
    };
    activeRefreshController.current?.abort();
    const controller = new AbortController();
    activeRefreshController.current = controller;
    const abortFromCaller = () => controller.abort();
    signal.addEventListener("abort", abortFromCaller, { once: true });
    pendingControllers.current.add(controller);

    try {
      const fresh = await services.restore(controller.signal);
      const latest = stateRef.current;
      if (controller.signal.aborted
        || captured.generation !== generation.current
        || captured.sessionGeneration !== sessionGenerationRef.current
        || captured.services !== servicesRef.current
        || latest.status !== "authenticated"
        || latest.user.id !== captured.userId) {
        throw new ApiError("aborted", "Request was cancelled.");
      }

      if (fresh === null || fresh.id !== captured.userId) {
        runRestore();
        throw new ApiError("aborted", "当前登录会话需要重新验证。");
      }

      const applied = latestRefresh.current;
      if (applied === null || captured.sequence > applied.sequence) {
        latestRefresh.current = { sequence: captured.sequence, user: fresh };
        setState({ status: "authenticated", user: fresh });
        return fresh;
      }
      return applied.user;
    } finally {
      pendingControllers.current.delete(controller);
      if (activeRefreshController.current === controller) activeRefreshController.current = null;
      signal.removeEventListener("abort", abortFromCaller);
    }
  }, [runRestore, services]);

  const value: AuthContextValue = {
    services,
    state,
    sessionGeneration,
    refreshCurrentUser,
    login,
    logout,
    retryRestore: runRestore,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (value === null) {
    throw new Error("useAuth must be used within AuthProvider.");
  }
  return value;
}
