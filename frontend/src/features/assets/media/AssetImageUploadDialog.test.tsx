// @vitest-environment jsdom
import { StrictMode, useRef, useState } from "react";
import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AssetImageUploadError, type AssetImageDirectory, type AssetImageUploadRun } from "../../../shared/api/assetImageUpload";
import type { Character } from "../../../shared/api/contracts";
import { AssetImageUploadDialog, type AssetImageUploadAttempt } from "./AssetImageUploadDialog";

const character: Character = {
  id: "asset-dialog-558",
  series_id: "series-dialog-558",
  name: "顾行舟",
  gender: "男",
  age: "31",
  role: "档案管理员",
  appearance: "深色外套。",
  description: "保管旧城档案。",
  image_url: null,
  audio_url: null,
  voice_ref: null,
  aliases: null,
  canonical_key: "dialog-key",
  created_at: "2026-10-01T00:00:00Z",
  updated_at: "2026-10-02T00:00:00Z",
};

const receipt = Object.freeze({
  kind: "asset-image-upload-receipt" as const,
  url: "http://127.0.0.1:4175/uploads/dialog.png",
});

function makeDirectory(imageUrl: string | null, overrides: Partial<Character> = {}): AssetImageDirectory {
  return { category: "characters", items: [{ ...character, ...overrides, image_url: imageUrl }], imageSources: new Map() };
}

function Harness({
  run,
  onUnauthorized = vi.fn(),
  onDirectoryUpdated = vi.fn(),
}: {
  run: AssetImageUploadRun;
  onUnauthorized?: () => void;
  onDirectoryUpdated?: (directory: AssetImageDirectory) => void;
}) {
  const [attempts, setAttempts] = useState<AssetImageUploadAttempt[]>([]);
  const nextId = useRef(0);
  return (
    <AssetImageUploadDialog
      allocateAttemptId={() => ++nextId.current}
      asset={character}
      attempts={attempts}
      category="characters"
      isCurrent={() => true}
      onAttemptsChange={setAttempts}
      onClose={vi.fn()}
      onDirectoryUpdated={onDirectoryUpdated}
      onUnauthorized={onUnauthorized}
      run={run}
      runId={5}
      seriesName="夜航录"
    />
  );
}

function createRun(overrides: Partial<AssetImageUploadRun> = {}): AssetImageUploadRun {
  return {
    isCurrent: () => true,
    canSaveImage: (candidate) => candidate === receipt,
    canReadImage: (candidate) => candidate === receipt,
    initialImageSource: null,
    uploadImage: vi.fn(async () => receipt),
    saveImage: vi.fn(async () => ({ ...character, image_url: receipt.url })),
    readDirectory: vi.fn(async () => makeDirectory(receipt.url)),
    readImage: vi.fn(async () => new Blob([new Uint8Array([1, 2])], { type: "image/png" })),
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("asset image upload dialog", () => {
  it("keeps a pre-dispatch validation failure separate from an unknown upload result", async () => {
    const run = createRun({
      uploadImage: vi.fn(async () => { throw new AssetImageUploadError("not-sent", "图片类型不支持，未发送请求。"); }),
    });
    const user = userEvent.setup();
    render(<Harness run={run} />);
    await screen.findByText("已读取当前素材目录，图片上传记录已就绪。");
    await user.upload(screen.getByLabelText("选择图片文件"), new File(["x"], "portrait.png", { type: "image/png" }));
    await user.click(screen.getByRole("button", { name: "上传所选图片" }));

    const message = await screen.findByText("图片类型不支持，未发送请求。");
    expect(message).toHaveAttribute("data-state", "upload-not-sent");
    expect(run.saveImage).not.toHaveBeenCalled();
  });

  it("requires a separate explicit confirmation before one image_url update and directory read", async () => {
    const calls: string[] = [];
    const run = createRun({
      uploadImage: vi.fn(async () => { calls.push("upload"); return receipt; }),
      saveImage: vi.fn(async () => { calls.push("save"); return { ...character, image_url: receipt.url }; }),
      readDirectory: vi.fn(async () => { calls.push("directory"); return makeDirectory(receipt.url); }),
    });
    const user = userEvent.setup();
    render(<Harness run={run} />);
    await screen.findByText("已读取当前素材目录，图片上传记录已就绪。");
    const file = new File([new Uint8Array([137, 80])], "portrait.png", { type: "image/png" });
    await user.upload(screen.getByLabelText("选择图片文件"), file);
    await user.click(screen.getByRole("button", { name: "上传所选图片" }));

    await screen.findByText("图片上传已收到服务器回执；尚未修改素材目录。");
    expect(calls).toEqual(["directory", "upload"]);
    expect(run.saveImage).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "确认更新素材图片" }));
    await screen.findByText("更新回执与独立目录读取一致。");

    expect(calls).toEqual(["directory", "upload", "directory", "save", "directory"]);
    expect(run.saveImage).toHaveBeenCalledOnce();
    expect(run.readDirectory).toHaveBeenCalledTimes(3);
  });

  it("keeps an unknown save result unknown when a read-only observation matches", async () => {
    const run = createRun({
      saveImage: vi.fn(async () => { throw new AssetImageUploadError("unknown", "回执尚未确认。", 503); }),
    });
    const user = userEvent.setup();
    render(<Harness run={run} />);
    await screen.findByText("已读取当前素材目录，图片上传记录已就绪。");
    await user.upload(screen.getByLabelText("选择图片文件"), new File(["x"], "portrait.png", { type: "image/png" }));
    await user.click(screen.getByRole("button", { name: "上传所选图片" }));
    await screen.findByText("图片上传已收到服务器回执；尚未修改素材目录。");
    await user.click(screen.getByRole("button", { name: "确认更新素材图片" }));
    await screen.findByText("回执尚未确认。");

    expect(run.readDirectory).toHaveBeenCalledTimes(2);
    await user.click(screen.getByRole("button", { name: "只读重新核对目录" }));
    await screen.findByText("目录读取到本次图片地址，但原更新回执仍未知；观察结果不等于服务器确认。");
    expect(run.saveImage).toHaveBeenCalledOnce();
    expect(run.readDirectory).toHaveBeenCalledTimes(3);
  });

  it("recovers an acknowledged update with read-only GET after the first directory read fails", async () => {
    const readDirectory = vi.fn()
      .mockResolvedValueOnce(makeDirectory(receipt.url))
      .mockResolvedValueOnce(makeDirectory(receipt.url))
      .mockRejectedValueOnce(new AssetImageUploadError("read-error", "目录读取失败。"))
      .mockResolvedValueOnce(makeDirectory(receipt.url));
    const run = createRun({ readDirectory });
    const user = userEvent.setup();
    render(<Harness run={run} />);
    await screen.findByText("已读取当前素材目录，图片上传记录已就绪。");
    await user.upload(screen.getByLabelText("选择图片文件"), new File(["x"], "portrait.png", { type: "image/png" }));
    await user.click(screen.getByRole("button", { name: "上传所选图片" }));
    await screen.findByText("图片上传已收到服务器回执；尚未修改素材目录。");
    await user.click(screen.getByRole("button", { name: "确认更新素材图片" }));
    await screen.findByText("服务器已确认更新，但独立目录读取失败；可重新执行只读核对。");
    await user.click(screen.getByRole("button", { name: "只读重新核对目录" }));
    await screen.findByText("更新回执与独立目录读取一致。");

    expect(run.saveImage).toHaveBeenCalledOnce();
    expect(run.readDirectory).toHaveBeenCalledTimes(4);
  });

  it("reads fresh confirmation material on entry and again before the one update", async () => {
    const readDirectory = vi.fn()
      .mockResolvedValueOnce(makeDirectory(null))
      .mockResolvedValueOnce(makeDirectory(null, { name: "顾行舟（已更新）" }));
    const run = createRun({ readDirectory });
    const user = userEvent.setup();
    render(<Harness run={run} />);
    await screen.findByText("已读取当前素材目录，图片上传记录已就绪。");
    await user.upload(screen.getByLabelText("选择图片文件"), new File(["x"], "portrait.png", { type: "image/png" }));
    await user.click(screen.getByRole("button", { name: "上传所选图片" }));
    await screen.findByText("图片上传已收到服务器回执；尚未修改素材目录。");
    await user.click(screen.getByRole("button", { name: "确认更新素材图片" }));

    expect(await screen.findByText("确认前素材信息已变化；上传回执已保留，本次没有提交目录更新。请关闭并重新开始。"))
      .toHaveAttribute("data-state", "confirmation-stale");
    expect(run.readDirectory).toHaveBeenCalledTimes(2);
    expect(run.saveImage).not.toHaveBeenCalled();
  });

  it("blocks image actions unless entry GET finds one same-series asset with the requested identity", async () => {
    const directory: AssetImageDirectory = {
      category: "characters",
      items: [character, { ...character }],
      imageSources: new Map(),
    };
    const run = createRun({ readDirectory: vi.fn(async () => directory) });
    const onDirectoryUpdated = vi.fn();
    render(<Harness run={run} onDirectoryUpdated={onDirectoryUpdated} />);

    expect(await screen.findByText("当前目录中没有唯一且属于本剧集的素材记录，不能继续图片操作。"))
      .toBeInTheDocument();
    expect(screen.getByRole("button", { name: "上传所选图片" })).toBeDisabled();
    expect(run.uploadImage).not.toHaveBeenCalled();
    expect(run.saveImage).not.toHaveBeenCalled();
    expect(onDirectoryUpdated).not.toHaveBeenCalled();
  });

  it("does not report a stale run 401 or publish a late upload result after the session changes", async () => {
    let sessionCurrent = true;
    let rejectUpload!: (error: unknown) => void;
    const uploadImage = vi.fn(() => new Promise<typeof receipt>((_resolve, reject) => { rejectUpload = reject; }));
    const run = createRun({
      isCurrent: () => sessionCurrent,
      uploadImage,
    });
    const onUnauthorized = vi.fn();
    const user = userEvent.setup();
    render(<Harness run={run} onUnauthorized={onUnauthorized} />);
    await screen.findByText("已读取当前素材目录，图片上传记录已就绪。");
    await user.upload(screen.getByLabelText("选择图片文件"), new File(["x"], "portrait.png", { type: "image/png" }));
    await user.click(screen.getByRole("button", { name: "上传所选图片" }));
    await screen.findByText("正在发送本次图片上传。");

    sessionCurrent = false;
    await act(async () => {
      rejectUpload(new AssetImageUploadError("rejected", "会话已替换。", 401));
      await Promise.resolve();
    });

    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(screen.queryByText("会话已替换。")).not.toBeInTheDocument();
    expect(run.saveImage).not.toHaveBeenCalled();
    expect(run.readDirectory).toHaveBeenCalledOnce();
  });

  it("starts a fresh directory read after StrictMode cleanup and ignores the stale success", async () => {
    let resolveFirstRead!: (directory: AssetImageDirectory) => void;
    const staleDirectory = makeDirectory(null, { name: "过期目录名称" });
    const freshDirectory = makeDirectory(null, { name: "当前目录名称" });
    const firstSignal = { current: null as AbortSignal | null };
    let readCount = 0;
    const readDirectory = vi.fn((_signal: AbortSignal) => {
      readCount += 1;
      if (readCount === 1) {
        firstSignal.current = _signal;
        return new Promise<AssetImageDirectory>((resolve) => { resolveFirstRead = resolve; });
      }
      return Promise.resolve(freshDirectory);
    });
    const run = createRun({ readDirectory });
    const onDirectoryUpdated = vi.fn();

    render(<StrictMode><Harness run={run} onDirectoryUpdated={onDirectoryUpdated} /></StrictMode>);

    expect(await screen.findByText("已读取当前素材目录，图片上传记录已就绪。"))
      .toBeInTheDocument();
    expect(readDirectory).toHaveBeenCalledTimes(2);
    expect(firstSignal.current?.aborted).toBe(true);
    expect(screen.getByRole("heading", { name: "上传或更换图片：当前目录名称" })).toBeInTheDocument();

    await act(async () => {
      resolveFirstRead(staleDirectory);
      await Promise.resolve();
    });

    expect(screen.getByRole("heading", { name: "上传或更换图片：当前目录名称" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "上传或更换图片：过期目录名称" })).not.toBeInTheDocument();
    expect(onDirectoryUpdated).toHaveBeenCalledOnce();
    expect(onDirectoryUpdated).toHaveBeenCalledWith(freshDirectory);
  });

  it("keeps the StrictMode replay read usable when the aborted generation later rejects unauthorized", async () => {
    let rejectFirstRead!: (error: unknown) => void;
    const firstSignal = { current: null as AbortSignal | null };
    const freshDirectory = makeDirectory(null, { name: "当前目录名称" });
    const readDirectory = vi.fn((signal: AbortSignal) => {
      if (readDirectory.mock.calls.length === 1) {
        firstSignal.current = signal;
        return new Promise<AssetImageDirectory>((_resolve, reject) => { rejectFirstRead = reject; });
      }
      if (readDirectory.mock.calls.length === 2 || readDirectory.mock.calls.length === 3) {
        return Promise.resolve(freshDirectory);
      }
      return Promise.resolve(makeDirectory(receipt.url));
    });
    const run = createRun({ readDirectory });
    const onUnauthorized = vi.fn();
    const user = userEvent.setup();

    render(<StrictMode><Harness run={run} onUnauthorized={onUnauthorized} /></StrictMode>);

    expect(await screen.findByText("已读取当前素材目录，图片上传记录已就绪。"))
      .toBeInTheDocument();
    expect(readDirectory).toHaveBeenCalledTimes(2);
    expect(firstSignal.current?.aborted).toBe(true);
    await act(async () => {
      rejectFirstRead(new AssetImageUploadError("rejected", "旧目录读取会话已失效。", 401));
      await Promise.resolve();
    });
    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(screen.queryByText("旧目录读取会话已失效。")).not.toBeInTheDocument();

    await user.upload(screen.getByLabelText("选择图片文件"), new File(["x"], "strict-mode.png", { type: "image/png" }));
    await user.click(screen.getByRole("button", { name: "上传所选图片" }));
    expect(await screen.findByText("图片上传已收到服务器回执；尚未修改素材目录。"))
      .toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认更新素材图片" }));
    expect(await screen.findByText("更新回执与独立目录读取一致。")).toBeInTheDocument();

    expect(run.uploadImage).toHaveBeenCalledOnce();
    expect(run.saveImage).toHaveBeenCalledOnce();
    expect(readDirectory).toHaveBeenCalledTimes(4);
  });
});
