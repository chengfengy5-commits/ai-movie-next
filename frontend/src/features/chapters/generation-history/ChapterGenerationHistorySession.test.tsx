// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  ChapterGenerationHistoryContext,
  ChapterGenerationHistoryMessage,
  ChapterGenerationHistoryPort,
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

function snapshot(context: ChapterGenerationHistoryContext, mediaUrl: string | null): ChapterGenerationHistorySnapshot {
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
    messages: [message],
  };
}

function createOwner(reads: Array<ChapterGenerationHistorySnapshot | Error>) {
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
        readMedia: async () => new Blob(),
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
    prepareWrite: async () => ({ identity: null, release: async () => undefined }),
    refreshAfterAdoption: vi.fn(async () => undefined),
    onUnauthorized: vi.fn(),
  };
  return { owner, adopt, readSnapshot, port };
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
});
