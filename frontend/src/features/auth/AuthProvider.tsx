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
  const generation = useRef(0);
  const loginInFlight = useRef(false);
  const loginAttempt = useRef(0);
  const pendingControllers = useRef(new Set<AbortController>());

  const abortPending = useCallback(() => {
    for (const controller of pendingControllers.current) {
      controller.abort();
    }
    pendingControllers.current.clear();
  }, []);

  const runRestore = useCallback(() => {
    const ticket = ++generation.current;
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
  }, [abortPending, services]);

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
  }, [abortPending, services]);

  const logout = useCallback(() => {
    generation.current += 1;
    loginAttempt.current += 1;
    loginInFlight.current = false;
    abortPending();
    services.logout();
    setState({ status: "anonymous", error: null });
  }, [abortPending, services]);

  const value: AuthContextValue = {
    services,
    state,
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
