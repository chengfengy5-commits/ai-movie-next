import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthProvider, useAuth } from "./AuthProvider";
import type { User } from "../../shared/api/contracts";
import { ApiError } from "../../shared/api/errors";
import type { WorkspaceServices } from "../../shared/api/services";

function user(id: string, username: string): User {
  return {
    id,
    username,
    email: `${id}@example.invalid`,
    is_superuser: false,
    membership_type: "free",
    membership_expires_at: null,
    avatar_url: null,
    bio: null,
    created_at: "2026-10-01T00:00:00",
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

function AuthProbe() {
  const { state, login, logout, refreshCurrentUser, sessionGeneration } = useAuth();
  const label = state.status === "authenticated"
    ? `${state.status}:${state.user.username}`
    : state.status;

  return (
    <div>
      <output data-testid="auth-state">{label}</output>
      <output data-testid="auth-email">{state.status === "authenticated" ? state.user.email : ""}</output>
      <output data-testid="session-generation">{sessionGeneration}</output>
      <button onClick={() => { void login({ username: "first", password: "pass" }); }} type="button">
        登录第一个账号
      </button>
      <button onClick={() => { void login({ username: "second", password: "pass" }); }} type="button">
        登录第二个账号
      </button>
      <button onClick={logout} type="button">退出</button>
      <button onClick={() => { void refreshCurrentUser(new AbortController().signal).catch(() => undefined); }} type="button">
        刷新当前账户资料
      </button>
    </div>
  );
}

function renderAuthProbe(services: WorkspaceServices) {
  return render(
    <AuthProvider services={services}>
      <AuthProbe />
    </AuthProvider>,
  );
}

describe("AuthProvider request generations", () => {
  afterEach(() => cleanup());

  it("ignores a late 401 from an earlier login after a newer account is authenticated", async () => {
    const userInteraction = userEvent.setup();
    const firstLogin = deferred<User>();
    const login = vi.fn(({ username }: { username: string }) => (
      username === "first" ? firstLogin.promise : Promise.resolve(user("second-user", "第二个账号"))
    ));
    const services: WorkspaceServices = {
      mode: "demo",
      apiBaseUrl: null,
      restore: async () => null,
      login,
      listSeries: async () => [],
      listMyTeams: async () => [],
      listChapters: async () => [],
      listStoryboardAssets: async () => [],
      listCharacters: async () => [],
      listScenes: async () => [],
      listProps: async () => [],
        getPersonalProductionNotes: async () => { throw new Error("Unexpected personal notes request."); },
        getPersonalRoughCut: async () => { throw new Error("Unexpected personal rough-cut request."); },
        listMyTasks: async () => ({ total: 0, page: 1, page_size: 10, tasks: [] }),
      logout: vi.fn(),
    };
    renderAuthProbe(services);

    await waitFor(() => expect(screen.getByTestId("auth-state")).toHaveTextContent("anonymous"));
    await userInteraction.click(screen.getByRole("button", { name: "登录第一个账号" }));
    await userInteraction.click(screen.getByRole("button", { name: "退出" }));
    await userInteraction.click(screen.getByRole("button", { name: "登录第二个账号" }));
    await waitFor(() => expect(screen.getByTestId("auth-state")).toHaveTextContent("authenticated:第二个账号"));

    await act(async () => {
      firstLogin.reject(new ApiError("http", "expired", 401));
      await firstLogin.promise.catch(() => undefined);
    });

    expect(screen.getByTestId("auth-state")).toHaveTextContent("authenticated:第二个账号");
    expect(services.logout).toHaveBeenCalledTimes(1);
  });

  it("blocks duplicate submits and ignores a late login after the user switches accounts", async () => {
    const userInteraction = userEvent.setup();
    const firstLogin = deferred<User>();
    const login = vi.fn(({ username }: { username: string }) => (
      username === "first" ? firstLogin.promise : Promise.resolve(user("second-user", "第二个账号"))
    ));
    let refreshedUser: User | null = null;
    const restore = vi.fn(async () => refreshedUser);
    const services: WorkspaceServices = {
      mode: "demo",
      apiBaseUrl: null,
      restore,
      login,
      listSeries: async () => [],
      listMyTeams: async () => [],
      listChapters: async () => [],
      listStoryboardAssets: async () => [],
      listCharacters: async () => [],
      listScenes: async () => [],
      listProps: async () => [],
        getPersonalProductionNotes: async () => { throw new Error("Unexpected personal notes request."); },
        getPersonalRoughCut: async () => { throw new Error("Unexpected personal rough-cut request."); },
        listMyTasks: async () => ({ total: 0, page: 1, page_size: 10, tasks: [] }),
      logout: vi.fn(),
    };
    renderAuthProbe(services);

    await waitFor(() => expect(screen.getByTestId("auth-state")).toHaveTextContent("anonymous"));
    const firstButton = screen.getByRole("button", { name: "登录第一个账号" });
    await userInteraction.click(firstButton);
    await userInteraction.click(firstButton);
    expect(login).toHaveBeenCalledTimes(1);

    await userInteraction.click(screen.getByRole("button", { name: "退出" }));
    await userInteraction.click(screen.getByRole("button", { name: "登录第二个账号" }));
    await waitFor(() => expect(screen.getByTestId("auth-state")).toHaveTextContent("authenticated:第二个账号"));

    const sessionBeforeRefresh = screen.getByTestId("session-generation").textContent;
    refreshedUser = { ...user("second-user", "第二个账号"), email: "updated@example.invalid", bio: "完整资料" };
    await userInteraction.click(screen.getByRole("button", { name: "刷新当前账户资料" }));
    await waitFor(() => expect(screen.getByTestId("auth-email")).toHaveTextContent("updated@example.invalid"));
    expect(screen.getByTestId("auth-state")).toHaveTextContent("authenticated:第二个账号");
    expect(screen.getByTestId("session-generation").textContent).toBe(sessionBeforeRefresh);
    expect(restore).toHaveBeenCalledTimes(2);

    firstLogin.resolve(user("first-user", "第一个账号"));
    await waitFor(() => expect(screen.getByTestId("auth-state")).toHaveTextContent("authenticated:第二个账号"));
    expect(services.logout).toHaveBeenCalledTimes(1);
  });

  it("aborts an older full-user refresh before a newer refresh can publish", async () => {
    const interaction = userEvent.setup();
    const initialUser = user("refresh-order-user", "刷新顺序作者");
    const oldRefresh = deferred<User | null>();
    const newRefresh = deferred<User | null>();
    const refreshSignals: AbortSignal[] = [];
    let restoreCount = 0;
    const restore = vi.fn((signal: AbortSignal) => {
      restoreCount += 1;
      refreshSignals.push(signal);
      if (restoreCount === 1) return Promise.resolve(initialUser);
      if (restoreCount === 2) return oldRefresh.promise;
      return newRefresh.promise;
    });
    const services: WorkspaceServices = {
      mode: "demo",
      apiBaseUrl: null,
      restore,
      login: async () => initialUser,
      listSeries: async () => [],
      listMyTeams: async () => [],
      listChapters: async () => [],
      listStoryboardAssets: async () => [],
      listCharacters: async () => [],
      listScenes: async () => [],
      listProps: async () => [],
      getPersonalProductionNotes: async () => { throw new Error("Unexpected personal notes request."); },
      getPersonalRoughCut: async () => { throw new Error("Unexpected personal rough-cut request."); },
      listMyTasks: async () => ({ total: 0, page: 1, page_size: 10, tasks: [] }),
      logout: vi.fn(),
    };
    renderAuthProbe(services);

    await waitFor(() => expect(screen.getByTestId("auth-state")).toHaveTextContent("authenticated:刷新顺序作者"));
    const refreshButton = screen.getByRole("button", { name: "刷新当前账户资料" });
    await interaction.click(refreshButton);
    await waitFor(() => expect(restore).toHaveBeenCalledTimes(2));
    const firstRefreshSignal = refreshSignals[1];
    expect(firstRefreshSignal?.aborted).toBe(false);

    await interaction.click(refreshButton);
    await waitFor(() => expect(restore).toHaveBeenCalledTimes(3));
    expect(firstRefreshSignal?.aborted).toBe(true);

    const freshUser = { ...initialUser, email: "fresh@example.invalid", bio: "最新完整简介" };
    await act(async () => {
      newRefresh.resolve(freshUser);
      await newRefresh.promise;
    });
    await waitFor(() => expect(screen.getByTestId("auth-state")).toHaveTextContent("authenticated:刷新顺序作者"));
    expect(screen.getByTestId("auth-email")).toHaveTextContent(freshUser.email);

    await act(async () => {
      oldRefresh.resolve(initialUser);
      await oldRefresh.promise;
      await Promise.resolve();
    });

    expect(screen.getByTestId("auth-email")).toHaveTextContent(freshUser.email);
    expect(screen.getByTestId("auth-state")).toHaveTextContent("authenticated:刷新顺序作者");
  });
});
