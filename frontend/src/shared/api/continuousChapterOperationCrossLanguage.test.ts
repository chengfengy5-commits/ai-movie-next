// @vitest-environment node
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import type { Chapter, StoryboardFrame } from "./contracts";
import {
  continuousOperationResultDigest,
  continuousSourceSnapshotBinding,
  parseContinuousChapterOperationResponse,
} from "./continuousChapterOperations";
import { freezeContinuousOperationRequest } from "../../features/chapters/continuousChapterOperationJournal";
import { buildGuardedChapterUpdate, type ContinuousChapterDraftSnapshot, type ContinuousDraftFrame } from "../../features/chapters/continuousChapterEditing";

const repositoryRoot = fileURLToPath(new URL("../../../../", import.meta.url));
const backendBridgePath = fileURLToPath(new URL("../../../../backend/tests/continuous_chapter_operation_bridge_1164.py", import.meta.url));
const pythonExecutable = process.env.HAOAI_CROSS_LANGUAGE_PYTHON;

interface BridgeResult {
  readonly bridge_pid: number;
  readonly http_status: number;
  readonly payload: unknown;
}

function runBackendBridge(input: { readonly body_json: string; readonly operation_id: string }): Promise<BridgeResult> {
  if (pythonExecutable === undefined || pythonExecutable.trim() === "") throw new Error("Set HAOAI_CROSS_LANGUAGE_PYTHON for this cross-language test.");
  const child = spawn(pythonExecutable, [backendBridgePath], {
    cwd: repositoryRoot,
    env: { ...process.env, PYTHONPATH: "backend/src:backend/tests:." },
    stdio: ["pipe", "pipe", "pipe"],
  });
  let stdout = "";
  let stderr = "";
  child.stdout.setEncoding("utf8");
  child.stderr.setEncoding("utf8");
  child.stdout.on("data", (chunk: string) => { stdout += chunk; });
  child.stderr.on("data", (chunk: string) => { stderr += chunk; });
  child.stdin.end(JSON.stringify(input));
  return new Promise((resolve, reject) => {
    child.once("error", reject);
    child.once("close", (exitCode) => {
      if (exitCode !== 0) {
        reject(new Error(`Backend bridge exit=${String(exitCode)} pid=${String(child.pid)} stderr=${stderr}`));
        return;
      }
      try {
        const result = JSON.parse(stdout) as BridgeResult;
        if (result.bridge_pid !== child.pid) throw new Error("backend bridge PID evidence did not match spawned process");
        resolve(result);
      } catch (error) {
        reject(new Error(`Backend bridge returned invalid output; pid=${String(child.pid)} stdout=${stdout} stderr=${stderr}; ${String(error)}`));
      }
    });
  });
}

describe("frontend frozen request to backend receipt protocol", () => {
  it.skipIf(pythonExecutable === undefined || pythonExecutable.trim() === "")(
    "sends exact frontend JSON bytes through the real SQLite-backed ASGI endpoint and validates its original receipt",
    async () => {
      const originalFrame = {
        text: "打开时原文",
        original_text: "打开时原文",
        character: [],
        scene: [],
        prop: [],
        storyboard: [],
        unknown: { ratio: 1e-7, label: "雪だるま🧊", nested: { keep: true } },
      } as StoryboardFrame;
      const chapter: Chapter = {
        id: "chapter-a",
        series_id: "series-a",
        title: "跨语言协议",
        order: 1,
        content: [originalFrame],
      } as Chapter;
      const frames: ContinuousDraftFrame[] = [{
        draftId: "source:0",
        sourceIndex: 0,
        storyboardId: null,
        originalFrame,
        text: "已保存的分镜正文",
        characterIds: [],
        sceneIds: [],
        propIds: [],
      }];
      const snapshot: ContinuousChapterDraftSnapshot = {
        chapter,
        frames,
        expectedStoryboards: [],
        lockIdentity: null,
      };
      const update = buildGuardedChapterUpdate(snapshot, frames);
      const editorSessionId = "00000000-0000-4000-8000-000000000021";
      const operationId = "00000000-0000-4000-8000-000000000022";
      const frozen = await freezeContinuousOperationRequest({
        protocol_version: 1,
        editor_session_id: editorSessionId,
        command: { kind: "save-chapter", ...update, draft_frame_ids: frames.map((frame) => frame.draftId) },
      });

      const bridge = await runBackendBridge({ body_json: frozen.bodyJson, operation_id: operationId });
      const parsed = await parseContinuousChapterOperationResponse(bridge.payload, bridge.http_status);
      expect(parsed.receipt.request_body_sha256).toBe(frozen.bodySha256);
      expect(parsed.receipt.source_snapshot_digest).toBe(await continuousSourceSnapshotBinding(frozen.bodySha256));
      expect(parsed.receipt.result_digest).toBe(await continuousOperationResultDigest(parsed.receipt.canonical_response_json));
      expect(parsed.receipt.canonical_response_json).toContain("雪だるま🧊");
      const savedContent = parsed.result?.content;
      expect(Array.isArray(savedContent)).toBe(true);
      expect((savedContent as Array<Record<string, unknown>>)[0]?.unknown).toEqual({
        ratio: 1e-7,
        label: "雪だるま🧊",
        nested: { keep: true },
      });
      process.stdout.write(`cross-language-bridge-pid=${bridge.bridge_pid}\n`);
    },
  );
});
