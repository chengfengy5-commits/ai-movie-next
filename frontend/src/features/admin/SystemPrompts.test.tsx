import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import type { AdminFetcher } from "./adminApi";
import { createSystemPromptsApi } from "./systemPromptsApi";
import { SystemPrompts } from "./SystemPrompts";
import type { SystemPrompt, SystemPromptsApi } from "./systemPromptsTypes";

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function prompt(overrides: Partial<SystemPrompt> = {}): SystemPrompt {
  return {
    id: "prompt-1",
    category: "storyboard",
    name: "分镜提示词",
    system_prompt: "旧系统提示",
    user_prompt: "旧用户提示",
    sort_order: 3,
    is_active: true,
    created_at: "2026-10-01T00:00:00",
    updated_at: "2026-10-01T00:00:00",
    ...overrides,
  };
}

function requestPath(input: RequestInfo | URL | undefined): string {
  if (input === undefined) return "";
  const url = new URL(String(input), "http://localhost");
  return url.pathname + url.search;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((finish) => { resolve = finish; });
  return { promise, resolve: (value: T) => resolve(value) };
}

interface HarnessProps {
  api: SystemPromptsApi;
  onAuthenticationError(): void;
}

function Harness({ api, onAuthenticationError }: HarnessProps) {
  const [mode, setMode] = useState<"list" | "create" | "edit">("list");
  const [promptId, setPromptId] = useState<string>();
  const [reloadVersion, setReloadVersion] = useState(0);

  return (
    <SystemPrompts
      api={api}
      onAuthenticationError={onAuthenticationError}
      mode={mode}
      promptId={promptId}
      reloadVersion={reloadVersion}
      onCreate={() => setMode("create")}
      onEdit={(id) => {
        setPromptId(id);
        setMode("edit");
      }}
      onCancel={() => {
        setMode("list");
        setPromptId(undefined);
      }}
      onSaved={() => {
        setMode("list");
        setPromptId(undefined);
        setReloadVersion((value) => value + 1);
      }}
    />
  );
}

afterEach(() => cleanup());

describe("system prompts views", () => {
  it("creates and edits a prompt, returning to the refreshed list after each save", async () => {
    const user = userEvent.setup();
    let currentPrompts: SystemPrompt[] = [];
    const fetcher = vi.fn<AdminFetcher>(async (input, init) => {
      const path = requestPath(input);
      if (path.startsWith("/api/admin/system-prompts?")) {
        return jsonResponse({ data: currentPrompts, total: currentPrompts.length });
      }
      if (path === "/api/admin/system-prompts" && init?.method === "POST") {
        const body = JSON.parse(String(init.body)) as Omit<SystemPrompt, "id" | "created_at" | "updated_at">;
        const created = prompt({ ...body, id: "created-1" });
        currentPrompts = [created];
        return jsonResponse(created);
      }
      if (path === "/api/admin/system-prompts/created-1" && init?.method === undefined) {
        return jsonResponse(currentPrompts[0]);
      }
      if (path === "/api/admin/system-prompts/created-1" && init?.method === "PUT") {
        const body = JSON.parse(String(init.body)) as Partial<SystemPrompt>;
        const updated = prompt({ ...currentPrompts[0], ...body });
        currentPrompts = [updated];
        return jsonResponse(updated);
      }
      throw new Error("Unexpected request: " + path);
    });
    const api = createSystemPromptsApi(fetcher, () => "admin-token", "/api");

    render(<Harness api={api} onAuthenticationError={vi.fn()} />);
    expect(await screen.findByText("没有符合条件的提示词")).toBeInTheDocument();
    expect(screen.getByText("共 0 个提示词")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "新建提示词" }));
    await user.selectOptions(screen.getByLabelText("分类"), "storyboard");
    await user.type(screen.getByLabelText("名称"), "新建分镜");
    await user.type(screen.getByLabelText("系统提示词"), "新的系统内容");
    await user.type(screen.getByLabelText("用户提示词"), "新的用户内容");
    await user.clear(screen.getByLabelText("排序权重"));
    await user.type(screen.getByLabelText("排序权重"), "8");
    await user.click(screen.getByRole("button", { name: "保存" }));

    expect(await screen.findByRole("button", { name: "新建分镜" })).toBeInTheDocument();
    expect(screen.getByText("共 1 个提示词")).toBeInTheDocument();
    expect(fetcher.mock.calls.filter(([input]) => requestPath(input).startsWith("/api/admin/system-prompts?")))
      .toHaveLength(2);

    await user.click(screen.getByRole("button", { name: "编辑" }));
    expect(await screen.findByDisplayValue("新建分镜")).toBeInTheDocument();
    await user.clear(screen.getByLabelText("名称"));
    await user.type(screen.getByLabelText("名称"), "更新后的分镜");
    await user.click(screen.getByRole("button", { name: "保存" }));

    expect(await screen.findByRole("button", { name: "更新后的分镜" })).toBeInTheDocument();
    expect(screen.getByText("共 1 个提示词")).toBeInTheDocument();
    expect(fetcher.mock.calls.filter(([input]) => requestPath(input).startsWith("/api/admin/system-prompts?")))
      .toHaveLength(3);
    expect(fetcher.mock.calls.some(([input, init]) => (
      requestPath(input) === "/api/admin/system-prompts/created-1"
        && init?.method === "PUT"
        && JSON.parse(String(init.body)).name === "更新后的分镜"
    ))).toBe(true);
  });

  it("does not let a late edit response replace the prompt currently being edited", async () => {
    const oldRequest = deferred<SystemPrompt>();
    const api: SystemPromptsApi = {
      list: vi.fn().mockResolvedValue({ data: [], total: 0 }),
      get: vi.fn((id: string) => id === "old" ? oldRequest.promise : Promise.resolve(
        prompt({ id: "new", name: "当前提示词", system_prompt: "当前系统内容" }),
      )),
      create: vi.fn(),
      update: vi.fn(),
      delete: vi.fn(),
    };
    const onAuthenticationError = vi.fn();
    const first = render(
      <SystemPrompts
        api={api}
        onAuthenticationError={onAuthenticationError}
        mode="edit"
        promptId="old"
        reloadVersion={0}
      />,
    );
    expect(await screen.findByText("正在读取提示词…")).toBeInTheDocument();

    first.rerender(
      <SystemPrompts
        api={api}
        onAuthenticationError={onAuthenticationError}
        mode="edit"
        promptId="new"
        reloadVersion={0}
      />,
    );
    expect(await screen.findByDisplayValue("当前提示词")).toBeInTheDocument();

    await act(async () => {
      oldRequest.resolve(prompt({ id: "old", name: "迟到的提示词" }));
      await oldRequest.promise;
    });

    expect(screen.getByDisplayValue("当前提示词")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("迟到的提示词")).not.toBeInTheDocument();
    expect(api.get).toHaveBeenCalledTimes(2);
  });
});
