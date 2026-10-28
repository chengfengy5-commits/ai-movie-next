// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  ChapterGenerationHistoryContext,
  ChapterGenerationHistoryMessage,
  ChapterGenerationHistoryPort,
  ChapterGenerationHistoryRun,
  ChapterGenerationHistorySnapshot,
} from "../../../shared/api/chapterGenerationHistory";
import { ChapterGenerationHistoryError } from "../../../shared/api/chapterGenerationHistory";
import { ChapterGenerationHistorySession } from "./ChapterGenerationHistorySession";
import type { ChapterGenerationHistoryOwner } from "./useChapterGenerationHistoryOwner";

const candidateUrl = "https://cdn.example.test/history/candidate.png?signature=retained";
const message: ChapterGenerationHistoryMessage = {
  id: "history-session-message-1200",
  chapter_id: "history-session-chapter-1200",
  frame_index: 0,
  asset_type: null,
  asset_id: null,
  chat_mode: "image",
  role: "assistant",
  content: candidateUrl,
  model_name: "history-session-model",
  created_at: "2026-10-28T10:00:00Z",
  message_digest: "d".repeat(64),
};

function createContext(): ChapterGenerationHistoryContext {
  return {
    actorId: "history-session-user-1200",
    seriesId: "history-session-series-1200",
    chapterId: "history-session-chapter-1200",
    targetKind: "frame-preview",
    storyboardAssetId: "history-session-frame-1200",
    frameIndex: 0,
    mode: "image",
    isContextCurrent: () => true,
  };
}

function snapshot(
  context: ChapterGenerationHistoryContext,
  mediaUrl: string | null,
  messages: readonly ChapterGenerationHistoryMessage[] = [message],
): ChapterGenerationHistorySnapshot {
  return {
    protocol_version: 1,
    series_id: context.seriesId,
    chapter_id: context.chapterId,
    target_kind: context.targetKind,
    storyboard_asset_id: context.storyboardAssetId,
    frame_index: context.frameIndex,
    chat_mode: context.mode,
    writable: true,
    read_only_reason: null,
    target_proof: { mapping_digest: "a".repeat(64), target_digest: mediaUrl === null ? "b".repeat(64) : "c".repeat(64), media_url: mediaUrl },
    messages,
  };
}

type RunOverrides = Partial<Pick<ChapterGenerationHistoryRun,
  "retryOssUpload" | "updateMessageContent" | "deleteMessage" | "readMedia"
>>;

function createOwner(reads: Array<ChapterGenerationHistorySnapshot | Error>, runOverrides: RunOverrides = {}) {
  const adopt = vi.fn(async (context: ChapterGenerationHistoryContext) => ({
    protocol_version: 1 as const,
    outcome: "applied" as const,
    series_id: context.seriesId,
    chapter_id: context.chapterId,
    target_kind: context.targetKind,
    storyboard_asset_id: context.storyboardAssetId!,
    frame_index: context.frameIndex,
    target_proof: { mapping_digest: "a".repeat(64), target_digest: "c".repeat(64), media_url: candidateUrl },
  }));
  const readSnapshot = vi.fn(async () => {
    const next = reads.shift();
    if (next instanceof Error) throw next;
    if (next === undefined) throw new Error("No snapshot fixture remains.");
    return next;
  });
  const retryOssUpload = vi.fn(runOverrides.retryOssUpload ?? (async () => candidateUrl));
  const updateMessageContent = vi.fn(runOverrides.updateMessageContent ?? (async () => undefined));
  const deleteMessage = vi.fn(runOverrides.deleteMessage ?? (async () => undefined));
  const readMedia = vi.fn(runOverrides.readMedia ?? (async () => new Blob()));
  const release = vi.fn(async () => undefined);
  const prepareWrite = vi.fn(async () => ({ identity: null, release }));
  const port: ChapterGenerationHistoryPort = {
    beginRun(context) {
      return {
        isCurrent: context.isContextCurrent,
        readSnapshot: async (signal) => {
          if (signal.aborted) throw new DOMException("Aborted", "AbortError");
          return await readSnapshot();
        },
        adopt: async (_input, signal) => {
          if (signal.aborted) throw new DOMException("Aborted", "AbortError");
          return await adopt(context);
        },
        retryOssUpload,
        updateMessageContent,
        deleteMessage,
        readMedia,
      };
    },
  };
  const owner: ChapterGenerationHistoryOwner = {
    ownerKey: "history-session-owner-1200",
    actorId: "history-session-user-1200",
    seriesId: "history-session-series-1200",
    chapterId: "history-session-chapter-1200",
    chapterTitle: "历史核对章节",
    frameIndex: 0,
    storyboardAssetId: "history-session-frame-1200",
    apiBaseUrl: "http://127.0.0.1:4175/api",
    sessionGeneration: 1,
    requestGeneration: 1,
    port,
    isOwnerCurrent: () => true,
    isAdoptionAllowed: () => true,
    adoptionBlockMessage: () => null,
    prepareWrite,
    refreshAfterAdoption: vi.fn(async () => undefined),
    onUnauthorized: vi.fn(),
  };
  return { owner, adopt, readSnapshot, retryOssUpload, updateMessageContent, deleteMessage, readMedia, prepareWrite, release, port };
}

afterEach(() => cleanup());

describe("ChapterGenerationHistorySession", () => {
  it("keeps a successful POST unknown when its independent GET fails, then verifies by GET without reposting", async () => {
    const initial = snapshot({
      actorId: "history-session-user-1200",
      seriesId: "history-session-series-1200",
      chapterId: "history-session-chapter-1200",
      targetKind: "frame-preview",
      storyboardAssetId: "history-session-frame-1200",
      frameIndex: 0,
      mode: "image",
      isContextCurrent: () => true,
    }, null);
    const applied = { ...initial, target_proof: { ...initial.target_proof!, media_url: candidateUrl } };
    const fixture = createOwner([
      initial,
      initial,
      initial,
      new ChapterGenerationHistoryError("read-error", "暂时无法读取", 503),
      applied,
    ]);
    render(<ChapterGenerationHistorySession owner={fixture.owner} onClose={vi.fn()} />);

    await screen.findByText("history-session-model");
    fireEvent.click(screen.getByRole("button", { name: "选用此图片结果" }));
    await screen.findByRole("button", { name: "确认替换镜头预览" });
    fireEvent.click(screen.getByRole("button", { name: "确认替换镜头预览" }));

    const verifyButton = await screen.findByRole("button", { name: "只读核对当前值" });
    expect(screen.getByRole("button", { name: "确认替换镜头预览" })).toBeDisabled();
    expect(fixture.adopt).toHaveBeenCalledTimes(1);
    fireEvent.click(verifyButton);

    await screen.findByText(/无法证明此前请求是否完成/u);
    expect(fixture.adopt).toHaveBeenCalledTimes(1);
    expect(fixture.readSnapshot).toHaveBeenCalledTimes(5);
    expect(fixture.owner.refreshAfterAdoption).toHaveBeenCalledTimes(1);
    expect(fixture.owner.refreshAfterAdoption).toHaveBeenCalledWith(
      expect.any(AbortSignal),
      expect.any(Function),
      applied,
    );
  });

  it("refreshes lists when the preflight GET already shows the selected media", async () => {
    const context: ChapterGenerationHistoryContext = {
      actorId: "history-session-user-1200",
      seriesId: "history-session-series-1200",
      chapterId: "history-session-chapter-1200",
      targetKind: "frame-preview",
      storyboardAssetId: "history-session-frame-1200",
      frameIndex: 0,
      mode: "image",
      isContextCurrent: () => true,
    };
    const alreadyApplied = snapshot(context, candidateUrl);
    const fixture = createOwner([alreadyApplied, alreadyApplied, alreadyApplied]);
    render(<ChapterGenerationHistorySession owner={fixture.owner} onClose={vi.fn()} />);

    await screen.findByText("history-session-model");
    fireEvent.click(screen.getByRole("button", { name: "选用此图片结果" }));
    const finishCheck = await screen.findByRole("button", { name: "完成核对" });
    fireEvent.click(finishCheck);

    await waitFor(() => expect(fixture.owner.refreshAfterAdoption).toHaveBeenCalledTimes(1));
    expect(fixture.adopt).not.toHaveBeenCalled();
  });

  it("downloads only after a fresh message check and reports that disk persistence is unknown", async () => {
    const context = createContext();
    const fixture = createOwner([snapshot(context, null), snapshot(context, null)]);
    const blob = new Blob(["image-bytes"], { type: "image/png" });
    fixture.readMedia.mockResolvedValue(blob);
    const createObjectUrlDescriptor = Object.getOwnPropertyDescriptor(URL, "createObjectURL");
    const revokeObjectUrlDescriptor = Object.getOwnPropertyDescriptor(URL, "revokeObjectURL");
    const createObjectURL = vi.fn(() => "blob:history-download-1200");
    const revokeObjectURL = vi.fn();
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectURL });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revokeObjectURL });

    try {
      render(<ChapterGenerationHistorySession owner={fixture.owner} onClose={vi.fn()} />);
      await screen.findByText("history-session-model");
      expect(fixture.readMedia).not.toHaveBeenCalled();
      fireEvent.click(screen.getByRole("button", { name: "下载" }));

      await screen.findByText("已交给浏览器下载；本地无法确认文件是否已写入磁盘。");
      expect(fixture.readSnapshot).toHaveBeenCalledTimes(2);
      expect(fixture.readMedia).toHaveBeenCalledWith(candidateUrl, expect.any(AbortSignal));
      expect(fixture.prepareWrite).not.toHaveBeenCalled();
      expect(createObjectURL).toHaveBeenCalledWith(blob);
      expect(click).toHaveBeenCalledTimes(1);
      const link = document.querySelector<HTMLAnchorElement>('a[download="历史核对章节-镜头1-history-session-message-1200.png"]');
      expect(link).not.toBeNull();
    } finally {
      await new Promise((resolve) => globalThis.setTimeout(resolve, 1_050));
      if (createObjectUrlDescriptor === undefined) Reflect.deleteProperty(URL, "createObjectURL");
      else Object.defineProperty(URL, "createObjectURL", createObjectUrlDescriptor);
      if (revokeObjectUrlDescriptor === undefined) Reflect.deleteProperty(URL, "revokeObjectURL");
      else Object.defineProperty(URL, "revokeObjectURL", revokeObjectUrlDescriptor);
    }
  });

  it("requires confirmation before deleting a user history message and verifies removal", async () => {
    const context = createContext();
    const prompt = { ...message, role: "user", content: "请生成海边镜头" };
    const fixture = createOwner([
      snapshot(context, null, [prompt]),
      snapshot(context, null, [prompt]),
      snapshot(context, null, []),
    ]);
    render(<ChapterGenerationHistorySession owner={fixture.owner} onClose={vi.fn()} />);

    await screen.findByText("请生成海边镜头");
    fireEvent.click(screen.getByRole("button", { name: "删除记录" }));
    expect(screen.getByRole("group", { name: "确认删除历史消息" })).toBeInTheDocument();
    expect(fixture.deleteMessage).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认删除历史消息" }));

    await screen.findByText("删除已响应，独立读取确认这条历史消息已不存在。");
    expect(fixture.prepareWrite).toHaveBeenCalledTimes(1);
    expect(fixture.deleteMessage).toHaveBeenCalledTimes(1);
    expect(fixture.release).toHaveBeenCalledTimes(1);
    expect(fixture.readSnapshot).toHaveBeenCalledTimes(3);
  });

  it("does not resend an unknown OSS transfer when the user verifies the original message", async () => {
    const context = createContext();
    const fixture = createOwner([
      snapshot(context, null),
      snapshot(context, null),
      snapshot(context, null),
      snapshot(context, null),
    ], {
      retryOssUpload: async () => {
        throw new ChapterGenerationHistoryError("unknown", "转存结果未知。", 503);
      },
    });
    render(<ChapterGenerationHistorySession owner={fixture.owner} onClose={vi.fn()} />);

    await screen.findByText("history-session-model");
    fireEvent.click(screen.getByRole("button", { name: "转存到 OSS" }));
    await screen.findByText("转存请求结果未知，原消息仍是原地址；无法核对转存后的地址，不会重发或写回。");
    fireEvent.click(screen.getByRole("button", { name: "只读核对当前值" }));
    await waitFor(() => expect(fixture.readSnapshot).toHaveBeenCalledTimes(4));

    expect(fixture.retryOssUpload).toHaveBeenCalledTimes(1);
    expect(fixture.updateMessageContent).not.toHaveBeenCalled();
    expect(fixture.prepareWrite).toHaveBeenCalledTimes(1);
  });

  it("checks an unknown PUT by GET without repeating the transfer or message update", async () => {
    const context = createContext();
    const transferredUrl = "https://storage.example.test/history/result.png?signature=kept";
    const updatedMessage = { ...message, content: transferredUrl, message_digest: "e".repeat(64) };
    const fixture = createOwner([
      snapshot(context, null),
      snapshot(context, null),
      snapshot(context, null),
      snapshot(context, null, [updatedMessage]),
    ], {
      retryOssUpload: async () => transferredUrl,
      updateMessageContent: async () => {
        throw new ChapterGenerationHistoryError("unknown", "消息PUT结果未知。", 503);
      },
    });
    render(<ChapterGenerationHistorySession owner={fixture.owner} onClose={vi.fn()} />);

    await screen.findByText("history-session-model");
    fireEvent.click(screen.getByRole("button", { name: "转存到 OSS" }));
    await screen.findByText("当前原消息已核对为转存地址；无法证明先前写回是否完成，没有重复写入。");

    expect(fixture.retryOssUpload).toHaveBeenCalledTimes(1);
    expect(fixture.updateMessageContent).toHaveBeenCalledTimes(1);
    expect(fixture.readSnapshot).toHaveBeenCalledTimes(4);
  });

  it("does not repeat an unknown delete when read-only verification still finds the message", async () => {
    const context = createContext();
    const fixture = createOwner([
      snapshot(context, null),
      snapshot(context, null),
      snapshot(context, null),
      snapshot(context, null),
    ], {
      deleteMessage: async () => {
        throw new ChapterGenerationHistoryError("unknown", "删除结果未知。", 503);
      },
    });
    render(<ChapterGenerationHistorySession owner={fixture.owner} onClose={vi.fn()} />);

    await screen.findByText("history-session-model");
    fireEvent.click(screen.getByRole("button", { name: "删除记录" }));
    fireEvent.click(screen.getByRole("button", { name: "确认删除历史消息" }));
    await screen.findByText("删除结果仍未确认，消息仍可见；不会自动再次删除。");
    fireEvent.click(screen.getByRole("button", { name: "只读核对当前值" }));
    await waitFor(() => expect(fixture.readSnapshot).toHaveBeenCalledTimes(4));

    expect(fixture.deleteMessage).toHaveBeenCalledTimes(1);
    expect(fixture.prepareWrite).toHaveBeenCalledTimes(1);
  });
});
