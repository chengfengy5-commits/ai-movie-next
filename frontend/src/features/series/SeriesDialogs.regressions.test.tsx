import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState, type ComponentProps } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Series, User } from "../../shared/api/contracts";
import { ApiError } from "../../shared/api/errors";
import type { WorkspaceServices } from "../../shared/api/services";
import type { CreateSeriesInput, UpdateSeriesInput } from "../../shared/api/seriesManagement";
import { demoSeries } from "./demoSeries";
import { SeriesDeleteDialog } from "./SeriesDeleteDialog";
import { SeriesEditorDialog } from "./SeriesEditorDialog";

afterEach(() => cleanup());

const currentUser: User = {
  id: "user-current",
  username: "current-user",
  email: "current@example.test",
  is_superuser: false,
  membership_type: "free",
  membership_expires_at: null,
  avatar_url: null,
  bio: null,
  created_at: "2026-10-01T00:00:00",
};

const seriesA: Series = {
  ...demoSeries[0]!,
  id: "series-a",
  name: "目标剧集 A",
  description: "A 的简介",
};

const seriesB: Series = {
  ...demoSeries[0]!,
  id: "series-b",
  name: "目标剧集 B",
  description: "B 的简介",
};

function createServices(overrides: Partial<WorkspaceServices> = {}): WorkspaceServices {
  return {
    mode: "api",
    apiBaseUrl: "http://127.0.0.1:4175/api",
    restore: async () => null,
    login: async () => currentUser,
    listSeries: async () => [],
    getSeries: async (id) => ({ ...seriesA, id }),
    createSeries: async (input) => ({
      ...seriesA,
      id: "series-created",
      name: input.name,
      description: input.description,
      image_url: input.image_url,
      style_prompt_id: input.style_prompt_id,
    }),
    updateSeries: async (id, input) => ({ ...seriesA, ...input, id }),
    deleteSeries: async () => undefined,
    listSeriesStylePrompts: async () => [
      { id: "style-new", name: "新风格", is_system: true, user_id: null },
    ],
    uploadSeriesCover: async () => "https://example.test/cover.png",
    listMyTeams: async () => [],
    listChapters: async () => [],
    listStoryboardAssets: async () => [],
    listCharacters: async () => [],
    listScenes: async () => [],
    listProps: async () => [],
    getPersonalProductionNotes: async () => {
      throw new Error("not used by series dialog tests");
    },
    getPersonalRoughCut: async () => {
      throw new Error("not used by series dialog tests");
    },
    listMyTasks: async (page) => ({ total: 0, page, page_size: 10, tasks: [] }),
    logout: () => undefined,
    ...overrides,
  };
}

type EditorProps = ComponentProps<typeof SeriesEditorDialog>;
type DeleteProps = ComponentProps<typeof SeriesDeleteDialog>;

function editorProps(overrides: Partial<EditorProps> = {}): EditorProps {
  return {
    mode: "create",
    active: true,
    services: createServices(),
    userId: currentUser.id,
    onClose: vi.fn(),
    onUnauthorized: vi.fn(),
    onSaved: vi.fn(),
    ...overrides,
  };
}

function deleteProps(overrides: Partial<DeleteProps> = {}): DeleteProps {
  return {
    series: seriesA,
    active: true,
    services: createServices(),
    userId: currentUser.id,
    onClose: vi.fn(),
    onUnauthorized: vi.fn(),
    onDeleted: vi.fn(),
    onMissingConfirmed: vi.fn(),
    ...overrides,
  };
}

function ClosingEditorHarness({ props }: { props: EditorProps }) {
  const [open, setOpen] = useState(true);
  if (!open) return null;
  return (
    <SeriesEditorDialog
      {...props}
      onSaved={(saved, mode) => {
        props.onSaved(saved, mode);
        setOpen(false);
      }}
    />
  );
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

type DialogKind = "editor" | "delete";

function ModalFocusHarness({ kind, onBackground }: { kind: DialogKind; onBackground: () => void }) {
  const [open, setOpen] = useState(false);
  const [services] = useState(() => createServices());

  return (
    <main>
      <button type="button" onClick={() => setOpen(true)}>
        打开弹窗
      </button>
      <button type="button" onClick={onBackground}>
        背景操作
      </button>
      {open &&
        (kind === "editor" ? (
          <SeriesEditorDialog
            mode="create"
            active
            services={services}
            userId={currentUser.id}
            onClose={() => setOpen(false)}
            onUnauthorized={() => undefined}
            onSaved={() => undefined}
          />
        ) : (
          <SeriesDeleteDialog
            series={seriesA}
            active
            services={services}
            userId={currentUser.id}
            onClose={() => setOpen(false)}
            onUnauthorized={() => undefined}
            onDeleted={() => setOpen(false)}
            onMissingConfirmed={() => setOpen(false)}
          />
        ))}
    </main>
  );
}

describe("series dialog source-review regressions", () => {
  it.each(["editor", "delete"] as const)(
    "USERIES-02 %s dialog keeps keyboard focus inside across Tab and Shift+Tab",
    async (kind) => {
      const user = userEvent.setup();
      render(<ModalFocusHarness kind={kind} onBackground={vi.fn()} />);

      await user.click(screen.getByRole("button", { name: "打开弹窗" }));
      const dialog = screen.getByRole("dialog");
      expect(dialog).toContainElement(document.activeElement as HTMLElement);

      await user.tab({ shift: true });
      expect(dialog).toContainElement(document.activeElement as HTMLElement);
      await user.tab();
      expect(dialog).toContainElement(document.activeElement as HTMLElement);
    },
  );

  it.each(["editor", "delete"] as const)(
    "USERIES-02 %s dialog prevents keyboard activation of background actions",
    async (kind) => {
      const user = userEvent.setup();
      const onBackground = vi.fn();
      render(<ModalFocusHarness kind={kind} onBackground={onBackground} />);

      await user.click(screen.getByRole("button", { name: "打开弹窗" }));
      await user.tab();
      await user.keyboard("{Enter}");

      expect(onBackground).not.toHaveBeenCalled();
    },
  );

  it.each(["editor", "delete"] as const)(
    "USERIES-02 %s dialog closes safely on Escape and restores opener focus",
    async (kind) => {
      const user = userEvent.setup();
      render(<ModalFocusHarness kind={kind} onBackground={vi.fn()} />);
      const opener = screen.getByRole("button", { name: "打开弹窗" });

      await user.click(opener);
      expect(screen.getByRole("dialog")).toBeInTheDocument();
      await user.keyboard("{Escape}");

      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(opener).toHaveFocus();
    },
  );

  it("USERIES-01 create readback started for an older draft cannot authorize retry", async () => {
    const user = userEvent.setup();
    const pendingRead = deferred<Series[]>();
    let readCount = 0;
    const listSeries = vi.fn(async (_signal: AbortSignal) => {
      readCount += 1;
      if (readCount === 1) return pendingRead.promise;
      return [];
    });
    let createCount = 0;
    const createSeries = vi.fn(async () => {
      createCount += 1;
      if (createCount === 1) throw new ApiError("network", "提交结果暂时无法确认");
      return { ...seriesA, id: "series-created", name: "当前草稿" };
    });
    const services = createServices({ listSeries, createSeries });
    render(<SeriesEditorDialog {...editorProps({ services })} />);

    await user.type(screen.getByLabelText("剧集名称"), "旧草稿");
    await user.click(screen.getByRole("button", { name: "保存剧集" }));
    await screen.findByText(/提交结果暂时无法确认/);
    await user.click(screen.getByRole("button", { name: "重新读取核对" }));
    await waitFor(() => expect(listSeries).toHaveBeenCalledTimes(1));

    const nameInput = screen.getByLabelText("剧集名称");
    await user.clear(nameInput);
    await user.type(nameInput, "当前草稿");
    await act(async () => {
      pendingRead.resolve([]);
      await pendingRead.promise;
    });

    expect.soft(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重新读取核对" }));
    await waitFor(() => expect(listSeries).toHaveBeenCalledTimes(2));
    await user.click(await screen.findByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "确认重新提交" }));
    await waitFor(() => expect(createSeries).toHaveBeenCalledTimes(2));
  });

  it("USERIES-01 edit detail readback started for an older draft cannot authorize retry", async () => {
    const user = userEvent.setup();
    const pendingRead = deferred<Series>();
    let readCount = 0;
    const getSeries = vi.fn(async (id: string, _signal: AbortSignal) => {
      readCount += 1;
      if (readCount === 1) return seriesA;
      if (readCount === 2) return pendingRead.promise;
      return { ...seriesA, id };
    });
    let updateCount = 0;
    const updateSeries = vi.fn(async (id: string, input: UpdateSeriesInput) => {
      updateCount += 1;
      if (updateCount === 1) throw new ApiError("network", "保存结果暂时无法确认");
      return { ...seriesA, ...input, id };
    });
    const services = createServices({ getSeries, updateSeries });
    render(<SeriesEditorDialog {...editorProps({ mode: "edit", series: seriesA, services })} />);

    const description = await screen.findByLabelText("剧集简介");
    await user.clear(description);
    await user.type(description, "旧草稿简介");
    await user.click(screen.getByRole("button", { name: "保存剧集" }));
    await screen.findByText(/提交结果暂时无法确认/);
    await user.click(screen.getByRole("button", { name: "重新读取核对" }));
    await waitFor(() => expect(getSeries).toHaveBeenCalledTimes(2));

    await user.clear(screen.getByLabelText("剧集简介"));
    await user.type(screen.getByLabelText("剧集简介"), "当前草稿简介");
    await act(async () => {
      pendingRead.resolve(seriesA);
      await pendingRead.promise;
    });

    expect.soft(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重新读取核对" }));
    await waitFor(() => expect(getSeries).toHaveBeenCalledTimes(3));
    await user.click(await screen.findByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "确认重新提交" }));
    await waitFor(() => expect(updateSeries).toHaveBeenCalledTimes(2));
  });

  it("USERIES-01 a verification 401 for a superseded create draft does not log out the current context", async () => {
    const user = userEvent.setup();
    const pendingRead = deferred<Series[]>();
    const listSeries = vi.fn((_signal: AbortSignal) => pendingRead.promise);
    const createSeries = vi.fn(async () => {
      throw new ApiError("network", "创建结果暂时无法确认");
    });
    const onUnauthorized = vi.fn();
    render(<SeriesEditorDialog {...editorProps({ services: createServices({ listSeries, createSeries }), onUnauthorized })} />);

    const nameInput = screen.getByLabelText("剧集名称");
    await user.type(nameInput, "第一版草稿");
    await user.click(screen.getByRole("button", { name: "保存剧集" }));
    await screen.findByText(/提交结果暂时无法确认/);
    await user.click(screen.getByRole("button", { name: "重新读取核对" }));
    await waitFor(() => expect(listSeries).toHaveBeenCalledTimes(1));
    await user.clear(nameInput);
    await user.type(nameInput, "已更新草稿");

    await act(async () => {
      pendingRead.reject(new ApiError("http", "登录已失效", 401));
      await pendingRead.promise.catch(() => undefined);
    });

    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(screen.getByLabelText("剧集名称")).toHaveValue("已更新草稿");
  });

  it.each(["success", "401"] as const)(
    "USERIES-02 editor does not route an older target save %s to replacement callbacks",
    async (outcome) => {
      const user = userEvent.setup();
      const pendingSave = deferred<Series>();
      const getSeries = vi.fn(async (id: string, _signal: AbortSignal) => (id === seriesA.id ? seriesA : seriesB));
      const updateSeries = vi.fn((_id: string, _input: UpdateSeriesInput, _signal: AbortSignal) => pendingSave.promise);
      const onSavedB = vi.fn();
      const onUnauthorizedB = vi.fn();
      const services = createServices({ getSeries, updateSeries });
      const view = render(
        <SeriesEditorDialog
          {...editorProps({ mode: "edit", series: seriesA, services })}
        />,
      );

      const nameInput = await screen.findByLabelText("剧集名称");
      await user.clear(nameInput);
      await user.type(nameInput, "A 的修改");
      await user.click(screen.getByRole("button", { name: "保存剧集" }));
      await waitFor(() => expect(updateSeries).toHaveBeenCalledTimes(1));

      view.rerender(
        <SeriesEditorDialog
          {...editorProps({
            mode: "edit",
            series: seriesB,
            services,
            onSaved: onSavedB,
            onUnauthorized: onUnauthorizedB,
          })}
        />,
      );
      await screen.findByDisplayValue(seriesB.name);

      if (outcome === "success") {
        await act(async () => {
          pendingSave.resolve({ ...seriesA, name: "A 的修改" });
          await pendingSave.promise;
        });
      } else {
        await act(async () => {
          pendingSave.reject(new ApiError("http", "登录已失效", 401));
          await pendingSave.promise.catch(() => undefined);
        });
      }

      expect(onSavedB).not.toHaveBeenCalled();
      expect(onUnauthorizedB).not.toHaveBeenCalled();
      expect(screen.getByDisplayValue(seriesB.name)).toBeInTheDocument();
    },
  );

  it.each(["success", "401"] as const)(
    "USERIES-02 editor ignores an older detail %s after the selected record changes",
    async (outcome) => {
      const pendingRead = deferred<Series>();
      const getSeries = vi.fn((id: string, _signal: AbortSignal) => (id === seriesA.id ? pendingRead.promise : Promise.resolve(seriesB)));
      const onSavedB = vi.fn();
      const onUnauthorizedB = vi.fn();
      const services = createServices({ getSeries });
      const view = render(
        <SeriesEditorDialog {...editorProps({ mode: "edit", series: seriesA, services })} />,
      );
      await waitFor(() => expect(getSeries).toHaveBeenCalledTimes(1));

      view.rerender(
        <SeriesEditorDialog
          {...editorProps({ mode: "edit", series: seriesB, services, onSaved: onSavedB, onUnauthorized: onUnauthorizedB })}
        />,
      );
      await screen.findByDisplayValue(seriesB.name);

      if (outcome === "success") {
        await act(async () => {
          pendingRead.resolve(seriesA);
          await pendingRead.promise;
        });
      } else {
        await act(async () => {
          pendingRead.reject(new ApiError("http", "登录已失效", 401));
          await pendingRead.promise.catch(() => undefined);
        });
      }

      expect(screen.getByDisplayValue(seriesB.name)).toBeInTheDocument();
      expect(onSavedB).not.toHaveBeenCalled();
      expect(onUnauthorizedB).not.toHaveBeenCalled();
    },
  );

  it("USERIES-02 delete confirmation resets when the dialog target is replaced", async () => {
    const user = userEvent.setup();
    const services = createServices();
    const view = render(<SeriesDeleteDialog {...deleteProps({ series: seriesA, services })} />);

    const confirmation = screen.getByLabelText("输入剧集名称以确认");
    await user.type(confirmation, seriesA.name);
    view.rerender(<SeriesDeleteDialog {...deleteProps({ series: seriesB, services })} />);

    expect(screen.getByLabelText("输入剧集名称以确认")).toHaveValue("");
  });

  it.each(["success", "401"] as const)(
    "USERIES-02 delete does not route an older target %s to replacement callbacks",
    async (outcome) => {
      const user = userEvent.setup();
      const pendingDelete = deferred<void>();
      const deleteSeries = vi.fn((_id: string, _signal: AbortSignal) => pendingDelete.promise);
      const services = createServices({ deleteSeries });
      const onDeletedB = vi.fn();
      const onUnauthorizedB = vi.fn();
      const view = render(<SeriesDeleteDialog {...deleteProps({ series: seriesA, services })} />);

      await user.type(screen.getByLabelText("输入剧集名称以确认"), seriesA.name);
      await user.click(screen.getByRole("button", { name: "删除剧集" }));
      await waitFor(() => expect(deleteSeries).toHaveBeenCalledTimes(1));
      view.rerender(
        <SeriesDeleteDialog
          {...deleteProps({ series: seriesB, services, onDeleted: onDeletedB, onUnauthorized: onUnauthorizedB })}
        />,
      );

      if (outcome === "success") {
        await act(async () => {
          pendingDelete.resolve(undefined);
          await pendingDelete.promise;
        });
      } else {
        await act(async () => {
          pendingDelete.reject(new ApiError("http", "登录已失效", 401));
          await pendingDelete.promise.catch(() => undefined);
        });
      }

      expect(onDeletedB).not.toHaveBeenCalled();
      expect(onUnauthorizedB).not.toHaveBeenCalled();
      const deletionCopy = screen.getByRole("dialog").querySelector(".series-delete-copy");
      expect(deletionCopy).toHaveTextContent("将删除《目标剧集 B》及其章节、分镜和关联素材。此操作无法撤销。");
    },
  );

  it("USERIES-03 whitespace around untouched legacy fields is absent from a style-only patch", async () => {
    const user = userEvent.setup();
    const legacy = { ...seriesA, name: "  原始剧名  ", description: "  原始简介  ", style_prompt_id: "old-style" };
    const updateSeries = vi.fn(async (id: string, input: UpdateSeriesInput) => ({ ...legacy, ...input, id }));
    const services = createServices({
      getSeries: async () => legacy,
      updateSeries,
      listSeriesStylePrompts: async () => [
        { id: "style-new", name: "新风格", is_system: true, user_id: null },
      ],
    });
    render(<SeriesEditorDialog {...editorProps({ mode: "edit", series: legacy, services })} />);

    const nameInput = await screen.findByLabelText("剧集名称");
    expect(nameInput).toHaveValue(legacy.name);
    expect(screen.getByLabelText("剧集简介")).toHaveValue(legacy.description);
    await screen.findByRole("option", { name: "新风格 · 系统" });
    await user.selectOptions(screen.getByLabelText("风格提示词"), "style-new");
    await user.click(screen.getByRole("button", { name: "保存剧集" }));

    await waitFor(() => expect(updateSeries).toHaveBeenCalledTimes(1));
    expect(updateSeries).toHaveBeenCalledWith(legacy.id, { style_prompt_id: "style-new" }, expect.any(AbortSignal));
  });

  it("USERIES-03 untouched overlength legacy name and description do not block a style-only edit", async () => {
    const user = userEvent.setup();
    const legacy = {
      ...seriesA,
      name: `  ${"旧剧名".repeat(12)}  `,
      description: `  ${"旧简介".repeat(180)}  `,
      style_prompt_id: "old-style",
    };
    const updateSeries = vi.fn(async (id: string, input: UpdateSeriesInput) => ({ ...legacy, ...input, id }));
    const services = createServices({
      getSeries: async () => legacy,
      updateSeries,
      listSeriesStylePrompts: async () => [
        { id: "style-new", name: "新风格", is_system: true, user_id: null },
      ],
    });
    render(<SeriesEditorDialog {...editorProps({ mode: "edit", series: legacy, services })} />);

    const nameInput = await screen.findByLabelText("剧集名称");
    expect(nameInput).toHaveValue(legacy.name);
    expect(screen.getByLabelText("剧集简介")).toHaveValue(legacy.description);
    await screen.findByRole("option", { name: "新风格 · 系统" });
    await user.selectOptions(screen.getByLabelText("风格提示词"), "style-new");
    await user.click(screen.getByRole("button", { name: "保存剧集" }));

    await waitFor(() => expect(updateSeries).toHaveBeenCalledTimes(1));
    expect(updateSeries).toHaveBeenCalledWith(legacy.id, { style_prompt_id: "style-new" }, expect.any(AbortSignal));
  });

  it("USERIES-03 an explicitly edited name is still trimmed in the patch", async () => {
    const user = userEvent.setup();
    const updateSeries = vi.fn(async (id: string, input: UpdateSeriesInput) => ({ ...seriesA, ...input, id }));
    const services = createServices({ getSeries: async () => seriesA, updateSeries });
    render(<SeriesEditorDialog {...editorProps({ mode: "edit", series: seriesA, services })} />);

    const nameInput = await screen.findByLabelText("剧集名称");
    await user.clear(nameInput);
    await user.type(nameInput, "  更新剧名  ");
    await user.click(screen.getByRole("button", { name: "保存剧集" }));

    await waitFor(() => expect(updateSeries).toHaveBeenCalledTimes(1));
    expect(updateSeries).toHaveBeenCalledWith(seriesA.id, { name: "更新剧名" }, expect.any(AbortSignal));
  });

  it.each(["create", "edit"] as const)(
    "USERIES-04 keeps the %s draft locked while its save is pending",
    async (mode) => {
      const user = userEvent.setup();
      const pendingSave = deferred<Series>();
      const createSeries = vi.fn((_input: CreateSeriesInput, _signal: AbortSignal) => pendingSave.promise);
      const updateSeries = vi.fn((_id: string, _input: UpdateSeriesInput, _signal: AbortSignal) => pendingSave.promise);
      const services = createServices({
        createSeries,
        updateSeries,
        getSeries: async (id) => ({ ...seriesA, id }),
        listSeriesStylePrompts: async () => [
          { id: "style-a", name: "风格 A", is_system: true, user_id: null },
          { id: "style-b", name: "风格 B", is_system: true, user_id: null },
        ],
      });
      const onSaved = vi.fn();
      const props = editorProps({
        mode,
        ...(mode === "edit" ? { series: seriesA } : {}),
        services,
        onSaved,
      });
      render(<ClosingEditorHarness props={props} />);

      const nameInput = await screen.findByLabelText("剧集名称");
      const descriptionInput = screen.getByLabelText("剧集简介");
      if (mode === "edit") {
        await user.clear(nameInput);
        await user.clear(descriptionInput);
      }
      await user.type(nameInput, "请求名称 A");
      await user.type(descriptionInput, "请求简介 A");
      const styleSelect = screen.getByLabelText("风格提示词");
      await screen.findByRole("option", { name: "风格 A · 系统" });
      await user.selectOptions(styleSelect, "style-a");
      await user.click(screen.getByRole("button", { name: "保存剧集" }));

      const write = mode === "create" ? createSeries : updateSeries;
      await waitFor(() => expect(write).toHaveBeenCalledTimes(1));
      expect.soft(nameInput).toBeDisabled();
      expect.soft(descriptionInput).toBeDisabled();
      expect.soft(styleSelect).toBeDisabled();

      fireEvent.change(nameInput, { target: { value: "请求名称 B" } });
      fireEvent.change(descriptionInput, { target: { value: "请求简介 B" } });
      fireEvent.change(styleSelect, { target: { value: "style-b" } });
      expect.soft(nameInput).toHaveValue("请求名称 A");
      expect.soft(descriptionInput).toHaveValue("请求简介 A");
      expect.soft(styleSelect).toHaveValue("style-a");

      if (mode === "create") {
        expect.soft(createSeries).toHaveBeenCalledWith({
          name: "请求名称 A",
          description: "请求简介 A",
          image_url: null,
          style_prompt_id: "style-a",
        }, expect.any(AbortSignal));
      } else {
        expect.soft(updateSeries).toHaveBeenCalledWith(seriesA.id, {
          name: "请求名称 A",
          description: "请求简介 A",
          style_prompt_id: "style-a",
        }, expect.any(AbortSignal));
      }

      const form = screen.getByRole("dialog").querySelector("form");
      if (form === null) throw new Error("Expected the editor form to remain mounted during save.");
      fireEvent.submit(form);
      fireEvent.submit(form);
      expect(write).toHaveBeenCalledTimes(1);

      const saved = {
        ...seriesA,
        id: mode === "create" ? "series-created" : seriesA.id,
        name: "请求名称 A",
        description: "请求简介 A",
        style_prompt_id: "style-a",
      };
      await act(async () => {
        pendingSave.resolve(saved);
        await pendingSave.promise;
      });

      expect(onSaved).toHaveBeenCalledTimes(1);
      expect(onSaved).toHaveBeenCalledWith(saved, mode);
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    },
  );
});
