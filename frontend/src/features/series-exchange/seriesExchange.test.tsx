import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Chapter, Series } from "../../shared/api/contracts";
import { ApiError } from "../../shared/api/errors";
import type { WorkspaceServices } from "../../shared/api/services";
import type { ImportSeriesResponse, SeriesExchangePort } from "../../shared/api/seriesExchange";
import { demoSeries } from "../series/demoSeries";
import { SeriesExportButton } from "./SeriesExportButton";
import { SeriesExchangeDialog } from "./SeriesExchangeDialog";
import {
  compareNaturalFileNames,
  JSON_IMPORT_MAX_BYTES,
  MARKDOWN_FILE_MAX_BYTES,
  MARKDOWN_IMPORT_MAX_FILES,
  MARKDOWN_IMPORT_MAX_TOTAL_BYTES,
  prepareMarkdownFiles,
  readJsonSeriesExport,
  readMarkdownChapterFiles,
  safeExportFileName,
  validateSeriesExportV1,
} from "./fileImport";

const baseSeries: Series = {
  ...demoSeries[0]!,
  id: "series-existing",
  name: "既有剧集",
  user_id: "user-one",
  can_enter: true,
};

function makeChapter(id: string, seriesId: string, title: string, text: string): Chapter {
  return {
    id,
    series_id: seriesId,
    title,
    content: [{ text }],
    order: 0,
    created_at: "2026-10-15T00:00:00",
    updated_at: "2026-10-15T00:00:00",
    lock: null,
  };
}

function services(overrides: Partial<WorkspaceServices> = {}): WorkspaceServices {
  return {
    mode: "api",
    apiBaseUrl: null,
    restore: async () => null,
    login: async () => { throw new Error("Unexpected login request."); },
    listSeries: async () => [baseSeries],
    listChapters: async () => [],
    listStoryboardAssets: async () => [],
    listCharacters: async () => [],
    listScenes: async () => [],
    listProps: async () => [],
    listMyTeams: async () => [],
    listMyTasks: async () => ({ total: 0, page: 1, page_size: 10, tasks: [] }),
    logout: () => undefined,
    ...overrides,
  } as unknown as WorkspaceServices;
}

function port(overrides: Partial<SeriesExchangePort> = {}): SeriesExchangePort {
  return {
    exportSeries: async () => ({}),
    importSeries: async () => ({ id: "imported-series", name: "导入剧集", message: "ok" }),
    ...overrides,
  };
}

function renderDialog(options: {
  service?: WorkspaceServices;
  exchange?: SeriesExchangePort;
  series?: Series[];
  userId?: string;
  onClose?: () => void;
  onImported?: (result: { seriesId: string; name: string; chapterIds: string[]; kind: "json" | "markdown" }) => void;
} = {}) {
  const props = {
    active: true,
    open: true,
    services: options.service ?? services(),
    port: options.exchange ?? port(),
    series: options.series ?? [baseSeries],
    userId: options.userId ?? "user-one",
    onClose: options.onClose ?? vi.fn(),
    onUnauthorized: vi.fn(),
    ...(options.onImported === undefined ? {} : { onImported: options.onImported }),
  };
  const view = render(<SeriesExchangeDialog {...props} />);
  return { ...view, props };
}

async function chooseMarkdown(user: ReturnType<typeof userEvent.setup>, files: File[]) {
  await user.click(screen.getByRole("button", { name: "Markdown 章节" }));
  const input = screen.getByLabelText("选择 Markdown 文件") as HTMLInputElement;
  await user.upload(input, files);
  await screen.findByRole("list", { name: "Markdown 章节导入顺序" });
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("series exchange file handling", () => {
  it("validates v1 chapter shapes and preserves unknown JSON fields by copy", () => {
    const source = {
      version: 1,
      series: { name: "剧集", vendor_extension: { kept: true } },
      chapters: [{
        title: "第一章",
        frames: [{ text: "镜头", duration: 8, vendor_frame_field: { kept: true } }],
        original_text: "原文",
      }],
      storyboard_assets: [{ image_url: "https://example.invalid/a.png", extra: "keep" }],
      unknown_directory: [{ id: "keep" }],
    };

    const validated = validateSeriesExportV1(source);
    expect(validated).toEqual(source);
    expect(validated).not.toBe(source);
    expect(validated.chapters).not.toBe(source.chapters);
    expect(() => validateSeriesExportV1({ version: "1", chapters: [] })).toThrow("版本号为 1");
    expect(() => validateSeriesExportV1({ version: 1, chapters: [{ frames: ["bad"] }] })).toThrow("对象数组");
    expect(() => validateSeriesExportV1({ version: 1, chapters: [], characters: [null] })).toThrow("characters");
  });

  it("rejects an oversized JSON file before reading its contents", async () => {
    const oversized = {
      name: "large.json",
      size: JSON_IMPORT_MAX_BYTES + 1,
      text: vi.fn(async () => "{}"),
    } as unknown as File;

    await expect(readJsonSeriesExport(oversized)).rejects.toThrow("文件尚未读取");
    expect(oversized.text).not.toHaveBeenCalled();
  });

  it("checks Markdown count, extension and byte limits before reading", () => {
    const makeFile = (name: string, size: number) => ({ name, size, text: vi.fn() }) as unknown as File;
    expect(() => prepareMarkdownFiles(Array.from({ length: MARKDOWN_IMPORT_MAX_FILES + 1 }, (_, index) => makeFile(index + ".md", 1))))
      .toThrow("50 个");
    expect(() => prepareMarkdownFiles([makeFile("ok.md", 1), makeFile("mixed.json", 1)]))
      .toThrow("不能混合导入");
    expect(() => prepareMarkdownFiles([makeFile("large.md", MARKDOWN_FILE_MAX_BYTES + 1)]))
      .toThrow("每个 Markdown 文件");
    const fullFileCount = MARKDOWN_IMPORT_MAX_TOTAL_BYTES / MARKDOWN_FILE_MAX_BYTES;
    const tooLargeTotal = [
      ...Array.from({ length: fullFileCount }, (_, index) => makeFile("part-" + index + ".md", MARKDOWN_FILE_MAX_BYTES)),
      makeFile("part-last.md", 1),
    ];
    expect(() => prepareMarkdownFiles(tooLargeTotal)).toThrow("总量不能超过 20 MiB");
  });

  it("sorts Arabic and Chinese chapter numbers naturally and preserves full Markdown text", async () => {
    const files = [
      new File(["第十章\n原样内容"], "第十章.md"),
      new File(["二"], "第2章.md"),
      new File(["一"], "第1章.md"),
      new File(["三"], "第三章.md"),
      new File(["十"], "第二章.md"),
    ];
    expect(compareNaturalFileNames("第2章.md", "第10章.md")).toBeLessThan(0);
    expect(compareNaturalFileNames("第二章.md", "第十章.md")).toBeLessThan(0);
    const chapters = await readMarkdownChapterFiles(files);
    expect(chapters.map((item) => item.title)).toEqual(["第1章", "第2章", "第二章", "第三章", "第十章"]);
    expect(chapters.at(-1)?.text).toBe("第十章\n原样内容");
  });

  it("cleans path separators and control characters from export filenames", () => {
    const fileName = safeExportFileName("../故事/\u0000|: ");
    expect(fileName).toMatch(/^\.\._故事_+\.json$/);
    expect(fileName).not.toMatch(/[\\/:*?"<>|\u0000-\u001f]/);
    expect(safeExportFileName("  ")).toBe("haoai-series-export.json");
  });
});

describe("Markdown chapter import", () => {
  it("creates chapters in reverse request order so the visible list matches the queue", async () => {
    const user = userEvent.setup();
    const chapters: Chapter[] = [];
    const createChapter = vi.fn(async (seriesId: string, input: { title: string; content: Array<{ text: string }>; order: number }) => {
      const chapter = makeChapter("chapter-" + input.title, seriesId, input.title, input.content[0]!.text);
      chapters.unshift(chapter);
      return chapter;
    });
    const service = services({
      createSeries: async (input) => ({ ...baseSeries, id: "series-created", name: input.name }),
      createChapter,
      listChapters: async () => [...chapters],
    });
    renderDialog({ service });

    await chooseMarkdown(user, [
      new File(["章十文本"], "第十章.md"),
      new File(["章二文本"], "第二章.md"),
      new File(["章一文本"], "第一章.md"),
    ]);
    await user.click(screen.getByRole("radio", { name: "新建剧集" }));
    await user.click(screen.getByRole("button", { name: "开始导入章节" }));

    await screen.findByText("导入结果已保存，章节目录已刷新。");
    expect(createChapter.mock.calls.map((call) => call[1].title)).toEqual(["第十章", "第二章", "第一章"]);
    expect(createChapter.mock.calls.every((call) => call[1].order === 0)).toBe(true);
    expect(chapters.map((chapter) => chapter.title)).toEqual(["第一章", "第二章", "第十章"]);
  });

  it("honors a user's queue reordering when the visible chapter list is refreshed", async () => {
    const user = userEvent.setup();
    const chapters: Chapter[] = [];
    const createCalls: string[] = [];
    const service = services({
      createSeries: async (input) => ({ ...baseSeries, id: "series-created", name: input.name }),
      createChapter: async (seriesId, input) => {
        createCalls.push(input.title);
        const chapter = makeChapter("chapter-" + input.title, seriesId, input.title, (input.content[0] as { text: string }).text);
        chapters.unshift(chapter);
        return chapter;
      },
      listChapters: async () => [...chapters],
    });
    renderDialog({ service });
    await chooseMarkdown(user, [
      new File(["A"], "第一章.md"),
      new File(["B"], "第二章.md"),
      new File(["C"], "第三章.md"),
    ]);
    await user.click(screen.getByRole("button", { name: "下移 第一章" }));
    expect(screen.getByText("第一章").closest("li")).toHaveTextContent("2");
    await user.click(screen.getByRole("radio", { name: "新建剧集" }));
    await user.click(screen.getByRole("button", { name: "开始导入章节" }));
    await screen.findByText("导入结果已保存，章节目录已刷新。");
    expect(chapters.map((chapter) => chapter.title)).toEqual(["第二章", "第一章", "第三章"]);
    expect(createCalls).toEqual(["第三章", "第一章", "第二章"]);
  });

  it("stops at a known failure and resumes without reposting saved rows", async () => {
    const user = userEvent.setup();
    const chapters: Chapter[] = [];
    const calls: string[] = [];
    let secondAttempt = 0;
    const createChapter = vi.fn(async (seriesId: string, input: { title: string; content: Array<{ text: string }>; order: number }) => {
      calls.push(input.title);
      if (input.title === "第二章" && secondAttempt++ === 0) {
        throw new ApiError("http", "validation failed", 422, "章节内容校验失败");
      }
      const chapter = makeChapter("chapter-" + input.title, seriesId, input.title, input.content[0]!.text);
      chapters.unshift(chapter);
      return chapter;
    });
    const service = services({
      createSeries: async (input) => ({ ...baseSeries, id: "series-created", name: input.name }),
      createChapter,
      listChapters: async () => [...chapters],
    });
    renderDialog({ service });
    await chooseMarkdown(user, [
      new File(["A"], "第一章.md"),
      new File(["B"], "第二章.md"),
      new File(["C"], "第三章.md"),
    ]);
    await user.click(screen.getByRole("radio", { name: "新建剧集" }));
    await user.click(screen.getByRole("button", { name: "开始导入章节" }));
    expect(await screen.findByText("失败，可重试")).toBeInTheDocument();
    expect(calls).toEqual(["第三章", "第二章"]);
    expect(screen.getAllByText("已保存")).toHaveLength(1);

    await user.click(screen.getByRole("button", { name: "继续导入 / 重试失败项" }));
    await screen.findByText("导入结果已保存，章节目录已刷新。");
    expect(calls).toEqual(["第三章", "第二章", "第二章", "第一章"]);
    expect(calls.filter((title) => title === "第三章")).toHaveLength(1);
  });

  it("reconciles an unknown write by GET and never posts that file again", async () => {
    const user = userEvent.setup();
    const chapters: Chapter[] = [];
    const createChapter = vi.fn(async (seriesId: string, input: { title: string; content: Array<{ text: string }>; order: number }) => {
      const chapter = makeChapter("chapter-ambiguous", seriesId, input.title, input.content[0]!.text);
      chapters.unshift(chapter);
      throw new ApiError("network", "connection lost");
    });
    const listChapters = vi.fn(async () => [...chapters]);
    const service = services({
      createSeries: async (input) => ({ ...baseSeries, id: "series-created", name: input.name }),
      createChapter,
      listChapters,
    });
    renderDialog({ service });
    await chooseMarkdown(user, [new File(["正文保留\n不转换"], "序章.md")]);
    await user.click(screen.getByRole("radio", { name: "已有剧集" }));
    await user.selectOptions(screen.getByLabelText("选择可进入的剧集"), "series-existing");
    await user.click(screen.getByRole("button", { name: "开始导入章节" }));

    expect(await screen.findByText("已保存")).toBeInTheDocument();
    expect(createChapter).toHaveBeenCalledTimes(1);
    expect(listChapters.mock.calls.length).toBeGreaterThanOrEqual(2);
    expect(createChapter.mock.calls[0]?.[1]).toEqual({
      title: "序章",
      content: [{ text: "正文保留\n不转换" }],
      order: 0,
    });
  });

  it("keeps an unresolved write unknown and only rechecks chapters on continuation", async () => {
    const user = userEvent.setup();
    const createChapter = vi.fn(async () => { throw new ApiError("network", "connection lost"); });
    const service = services({ createChapter, listChapters: async () => [] });
    renderDialog({ service });
    await chooseMarkdown(user, [new File(["正文"], "序章.md")]);
    await user.click(screen.getByRole("radio", { name: "已有剧集" }));
    await user.selectOptions(screen.getByLabelText("选择可进入的剧集"), "series-existing");
    await user.click(screen.getByRole("button", { name: "开始导入章节" }));
    expect(await screen.findByText("结果待核对")).toBeInTheDocument();
    expect(createChapter).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "核对结果并继续" }));
    await waitFor(() => expect(createChapter).toHaveBeenCalledTimes(1));
    expect(screen.getByText("部分文件的保存结果仍无法唯一确认；不会自动重发，请先检查章节列表。")).toBeInTheDocument();
  });

  it("does not start any write if reading the selected series chapters fails", async () => {
    const user = userEvent.setup();
    const createChapter = vi.fn();
    const service = services({
      createChapter,
      listChapters: async () => { throw new ApiError("network", "offline"); },
    });
    renderDialog({ service });
    await chooseMarkdown(user, [new File(["正文"], "序章.md")]);
    await user.click(screen.getByRole("radio", { name: "已有剧集" }));
    await user.selectOptions(screen.getByLabelText("选择可进入的剧集"), "series-existing");
    await user.click(screen.getByRole("button", { name: "开始导入章节" }));

    expect(await screen.findByText("无法连接本地服务，请确认服务正在运行后重试。")).toBeInTheDocument();
    expect(createChapter).not.toHaveBeenCalled();
  });

  it("does not select a concurrent same-name series after an unknown Markdown series creation", async () => {
    const user = userEvent.setup();
    const concurrentSeries = { ...baseSeries, id: "concurrent-series", name: "同名剧集" };
    let listSeriesCall = 0;
    const listSeries = vi.fn(async () => {
      listSeriesCall += 1;
      return listSeriesCall === 1 ? [baseSeries] : [baseSeries, concurrentSeries];
    });
    const createSeries = vi.fn(async () => {
      throw new ApiError("network", "response lost");
    });
    const listChapters = vi.fn(async () => []);
    const createChapter = vi.fn();
    const service = services({
      listSeries,
      createSeries,
      listChapters,
      createChapter,
    });
    renderDialog({ service });
    await chooseMarkdown(user, [new File(["正文"], "同名剧集.md")]);
    await user.click(screen.getByRole("radio", { name: "新建剧集" }));
    await user.click(screen.getByRole("button", { name: "开始导入章节" }));

    await user.click(await screen.findByRole("button", { name: "核对结果并继续" }));
    expect(await screen.findByText("新剧集创建结果仍无法确认来源；不会选取同名剧集或继续导入章节。请在剧集列表核实后重新选择目标。")).toBeInTheDocument();
    expect(listSeries).toHaveBeenCalledTimes(2);
    expect(createSeries).toHaveBeenCalledTimes(1);
    expect(listChapters).not.toHaveBeenCalled();
    expect(createChapter).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "核对结果并继续" })).toBeInTheDocument();
  });

  it("isolates an in-flight result after the account scope changes", async () => {
    const user = userEvent.setup();
    let resolveChapter!: (value: Chapter) => void;
    const pending = new Promise<Chapter>((resolve) => { resolveChapter = resolve; });
    const createChapter = vi.fn(() => pending);
    const oldServices = services({
      createSeries: async (input) => ({ ...baseSeries, id: "old-created", name: input.name }),
      createChapter,
      listChapters: async () => [],
    });
    const { rerender, props } = renderDialog({ service: oldServices });
    await chooseMarkdown(user, [new File(["旧账号正文"], "旧章节.md")]);
    await user.click(screen.getByRole("radio", { name: "新建剧集" }));
    await user.click(screen.getByRole("button", { name: "开始导入章节" }));
    await waitFor(() => expect(createChapter).toHaveBeenCalledTimes(1));

    const nextServices = services();
    rerender(<SeriesExchangeDialog {...props} userId="user-two" services={nextServices} />);
    await act(async () => {
      resolveChapter(makeChapter("old-chapter", "old-created", "旧章节", "旧账号正文"));
    });
    expect(screen.queryByText("旧章节")).not.toBeInTheDocument();
    expect(screen.getByText("导入剧集或章节")).toBeInTheDocument();
  });

  it("stops the queue on close and resumes only after a GET confirms the in-flight chapter", async () => {
    const user = userEvent.setup();
    let resolveChapter!: (value: Chapter) => void;
    let chapters: Chapter[] = [];
    const pending = new Promise<Chapter>((resolve) => { resolveChapter = resolve; });
    const createChapter = vi.fn(async (seriesId: string, input: { title: string; content: Array<{ text: string }>; order: number }) => {
      const chapter = makeChapter("chapter-" + input.title, seriesId, input.title, input.content[0]!.text);
      const saved = await pending;
      chapters.unshift(saved);
      return chapter;
    });
    const service = services({
      createSeries: async (input) => ({ ...baseSeries, id: "series-created", name: input.name }),
      createChapter,
      listChapters: async () => [...chapters],
    });
    const { rerender, props } = renderDialog({ service });
    await chooseMarkdown(user, [new File(["A"], "第一章.md"), new File(["B"], "第二章.md")]);
    await user.click(screen.getByRole("radio", { name: "新建剧集" }));
    await user.click(screen.getByRole("button", { name: "开始导入章节" }));
    await waitFor(() => expect(createChapter).toHaveBeenCalledTimes(1));
    const inFlight = createChapter.mock.calls[0]![1];

    await user.click(screen.getByRole("button", { name: "关闭导入导出窗口" }));
    rerender(<SeriesExchangeDialog {...props} open={false} />);
    await act(async () => {
      resolveChapter(makeChapter("chapter-" + inFlight.title, "series-created", inFlight.title, inFlight.content[0]!.text));
      await pending;
    });
    expect(createChapter).toHaveBeenCalledTimes(1);

    rerender(<SeriesExchangeDialog {...props} open={true} />);
    expect(await screen.findByText("结果待核对")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "核对结果并继续" }));
    await screen.findByText("导入结果已保存，章节目录已刷新。");
    expect(createChapter).toHaveBeenCalledTimes(2);
    expect(createChapter.mock.calls.map((call) => call[1].title)).toEqual(["第二章", "第一章"]);
  });

  it("aborts the active request on unmount and does not start the next chapter", async () => {
    const user = userEvent.setup();
    let resolveChapter!: (value: Chapter) => void;
    let requestSignal: AbortSignal | undefined;
    const pending = new Promise<Chapter>((resolve) => { resolveChapter = resolve; });
    const createChapter = vi.fn((_seriesId: string, _input: { title: string; content: Array<{ text: string }>; order: number }, signal: AbortSignal) => {
      requestSignal = signal;
      return pending;
    });
    const service = services({
      createSeries: async (input) => ({ ...baseSeries, id: "series-created", name: input.name }),
      createChapter,
      listChapters: async () => [],
    });
    const view = renderDialog({ service });
    await chooseMarkdown(user, [new File(["A"], "第一章.md"), new File(["B"], "第二章.md")]);
    await user.click(screen.getByRole("radio", { name: "新建剧集" }));
    await user.click(screen.getByRole("button", { name: "开始导入章节" }));
    await waitFor(() => expect(createChapter).toHaveBeenCalledTimes(1));

    view.unmount();
    expect(requestSignal?.aborted).toBe(true);
    await act(async () => {
      resolveChapter(makeChapter("chapter-second", "series-created", "第二章", "B"));
      await pending;
    });
    expect(createChapter).toHaveBeenCalledTimes(1);
  });
});

describe("JSON import and export", () => {
  it("keeps a same-name JSON series unknown because a GET cannot identify this import", async () => {
    const user = userEvent.setup();
    const imported = { ...baseSeries, id: "imported-series", name: "备份剧集" };
    let rows = [baseSeries];
    const listSeries = vi.fn(async () => rows);
    const importSeries = vi.fn(async () => {
      rows = [...rows, imported];
      throw new ApiError("network", "response lost");
    });
    const onImported = vi.fn();
    renderDialog({ service: services({ listSeries }), exchange: port({ importSeries }), onImported });
    await user.click(screen.getByRole("button", { name: "JSON 剧集" }));
    const input = screen.getByLabelText("选择 JSON 文件（最大 20 MiB）") as HTMLInputElement;
    await user.upload(input, new File([JSON.stringify({ version: 1, series: { name: "备份剧集" }, chapters: [] })], "backup.json"));
    await user.click(await screen.findByRole("button", { name: "导入为新剧集" }));

    expect(await screen.findByText("结果仍待核对；请先检查剧集列表，不会自动重发。")).toBeInTheDocument();
    expect(listSeries).toHaveBeenCalledTimes(1);
    expect(importSeries).toHaveBeenCalledTimes(1);
    expect(onImported).not.toHaveBeenCalled();
  });

  it("ignores a late unauthorized response after the account scope changes", async () => {
    const user = userEvent.setup();
    let rejectImport!: (error: unknown) => void;
    const pendingImport = new Promise<ImportSeriesResponse>((_resolve, reject) => { rejectImport = reject; });
    const onUnauthorized = vi.fn();
    const initial = renderDialog({ exchange: port({ importSeries: () => pendingImport }) });
    initial.rerender(
      <SeriesExchangeDialog
        {...initial.props}
        onUnauthorized={onUnauthorized}
      />,
    );
    await user.click(screen.getByRole("button", { name: "JSON 剧集" }));
    const input = screen.getByLabelText("选择 JSON 文件（最大 20 MiB）") as HTMLInputElement;
    await user.upload(input, new File([JSON.stringify({ version: 1, series: { name: "备份剧集" }, chapters: [] })], "backup.json"));
    await user.click(await screen.findByRole("button", { name: "导入为新剧集" }));

    const nextServices = services();
    initial.rerender(
      <SeriesExchangeDialog
        {...initial.props}
        services={nextServices}
        userId="user-two"
        onUnauthorized={onUnauthorized}
      />,
    );
    await act(async () => {
      rejectImport(new ApiError("http", "expired", 401));
      await pendingImport.catch(() => undefined);
    });
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("downloads the complete server payload and revokes its object URL", async () => {
    const user = userEvent.setup();
    const payload = {
      version: 1,
      series: { name: "../剧集" },
      chapters: [{ title: "第一章", frames: [{ text: "正文", vendor_field: { preserved: true } }] }],
      custom_server_field: { preserved: true },
    };
    const createObjectURL = vi.fn((_blob: Blob) => "blob:series-export");
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectURL });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revokeObjectURL });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    const exportSeries = vi.fn(async () => payload);
    const exchange = port({ exportSeries });
    render(<SeriesExportButton port={exchange} seriesId="series-one" seriesName="fallback" />);

    await user.click(screen.getByRole("button", { name: "导出剧集 JSON：fallback" }));
    expect(await screen.findByText(/剧集 JSON 已导出/)).toBeInTheDocument();
    expect(exchange.exportSeries).toHaveBeenCalledTimes(1);
    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(createObjectURL.mock.calls[0]?.[0]).toBeInstanceOf(Blob);
    expect(await (createObjectURL.mock.calls[0]![0] as Blob).text()).toContain("vendor_field");
    expect(await (createObjectURL.mock.calls[0]![0] as Blob).text()).toContain("custom_server_field");
    expect(click).toHaveBeenCalledTimes(1);
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:series-export");
  });
});
