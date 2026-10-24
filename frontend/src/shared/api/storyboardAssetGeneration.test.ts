import { describe, expect, it } from "vitest";
import storyboardAssetWire from "../../testing/fixtures/storyboardAssetWire.json";
import { ApiError } from "./errors";
import {
  buildStoryboardAssetSaveInput,
  InvalidStoryboardAssetResponseError,
  parseStoryboardAssetAppearances,
  parseStoryboardAssetCandidates,
  parseStoryboardAssetSavePlan,
  type StoryboardAssetStreamEvent,
} from "./storyboardAssetGeneration";
import { parseStoryboardAssetSse } from "./storyboardAssetGenerationSse";

function streamReader(bytes: Uint8Array[]): ReadableStreamDefaultReader<Uint8Array> {
  let offset = 0;
  return new ReadableStream<Uint8Array>({
    pull(controller) {
      const value = bytes[offset++];
      if (value === undefined) controller.close();
      else controller.enqueue(value);
    },
  }).getReader();
}

function sse(events: ReadonlyArray<Record<string, unknown>>, ending = "\n\n"): Uint8Array {
  const value = events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join("").replace(/\n\n$/, ending);
  return new TextEncoder().encode(value);
}

async function consume(bytes: Uint8Array[], action: "extract" | "judge" | "save", kind: "character" | "scene" | "prop") {
  const events: StoryboardAssetStreamEvent[] = [];
  for await (const event of parseStoryboardAssetSse(streamReader(bytes), new AbortController().signal, action, kind)) {
    events.push(event);
  }
  return events;
}

const start = { type: "start", message: "开始提取素材..." };
const status = { type: "status", message: "正在解析素材..." };

describe("storyboard asset wire contracts", () => {
  it.each(["character", "scene", "prop"] as const)("parses actual %s extraction and appearance producer shapes", (kind) => {
    const extracted = extractedCandidates(kind);
    const judged = judgedCandidates(kind);

    expect(parseStoryboardAssetCandidates(extracted, kind)).toHaveLength(1);
    expect(parseStoryboardAssetCandidates(judged.candidates, kind)).toHaveLength(1);
    expect(parseStoryboardAssetAppearances(judged.appearances, kind, 1)).toHaveLength(1);
    expect(() => parseStoryboardAssetCandidates([{ name: "" }], kind)).toThrow(InvalidStoryboardAssetResponseError);
  });

  it("accepts a UTF-8 split within Chinese characters, CRLF, and an unterminated final line", async () => {
    const complete = storyboardAssetWire.producer.extract_complete.character;
    const raw = [start, status, complete, { type: "done" }]
      .map((event) => `data: ${JSON.stringify(event)}\r\n\r\n`)
      .join("");
    const encoded = new TextEncoder().encode(raw.slice(0, -2));
    const chunks = Array.from(encoded, (_byte, index) => encoded.slice(index, index + 1));

    const events = await consume(chunks, "extract", "character");
    expect(events.map((event) => event.type)).toEqual(["start", "status", "complete", "done"]);
    expect(events[2]?.data.characters).toEqual(complete.characters);
  });

  it.each([
    [[start, status, storyboardAssetWire.producer.extract_complete.character]],
    [[start, status, storyboardAssetWire.producer.extract_complete.character, { type: "done" }, status]],
    [[start, status, storyboardAssetWire.producer.extract_complete.character, storyboardAssetWire.producer.extract_complete.character, { type: "done" }]],
  ])("rejects truncated or out-of-order extraction streams", async (events) => {
    await expect(consume([sse(events)], "extract", "character")).rejects.toBeInstanceOf(ApiError);
  });

  it("rejects backend error events and incomplete candidate fields", async () => {
    await expect(consume([sse([start, { type: "error", message: "provider failed" }])], "extract", "character"))
      .rejects.toThrow("provider failed");
    await expect(consume([sse([start, { ...status, type: "complete", characters: [{}], raw_response: "" }, { type: "done" }])], "extract", "character"))
      .rejects.toBeInstanceOf(InvalidStoryboardAssetResponseError);
  });

  it("preserves null versus empty bindings and selected zero-based save frames", () => {
    expect(parseStoryboardAssetAppearances([null, [], ["顾宁"]], "character", 3)).toEqual([null, [], ["顾宁"]]);
    expect(parseStoryboardAssetAppearances([null, ""], "scene", 2)).toEqual([null, ""]);
    expect(() => parseStoryboardAssetAppearances([["书房", "走廊"]], "scene", 1)).toThrow(InvalidStoryboardAssetResponseError);
    const candidates = parseStoryboardAssetCandidates([{ title: "夜色书房" }], "scene");
    const input = buildStoryboardAssetSaveInput("scene", "chapter-1", [1, 4], candidates, ["夜色书房", ""], true);
    expect(input).toMatchObject({ target_frames: [1, 4], appearances: ["夜色书房", ""], dry_run: true });

    const source = [{ text: "原始镜头" }];
    const guardedInput = buildStoryboardAssetSaveInput(
      "scene", "chapter-1", [1], candidates, ["夜色书房"], true, source, "series-1",
    );
    expect(guardedInput).toMatchObject({ expected_content: source, expected_series_id: "series-1" });
  });

  it("validates the actual scene dry-run plan shape", () => {
    expect(parseStoryboardAssetSavePlan(storyboardAssetWire.save_plan, "scene"))
      .toMatchObject({ asset_type: "scene", frame_map: storyboardAssetWire.save_plan.frame_map });
    expect(() => parseStoryboardAssetSavePlan({ ...storyboardAssetWire.save_plan, asset_type: "prop" }, "scene"))
      .toThrow(InvalidStoryboardAssetResponseError);
  });
});

function extractedCandidates(kind: "character" | "scene" | "prop"): unknown {
  if (kind === "character") return storyboardAssetWire.producer.extract_complete.character.characters;
  if (kind === "scene") return storyboardAssetWire.producer.extract_complete.scene.scenes;
  return storyboardAssetWire.producer.extract_complete.prop.props;
}

function judgedCandidates(kind: "character" | "scene" | "prop"): { candidates: unknown; appearances: unknown } {
  if (kind === "character") {
    const event = storyboardAssetWire.producer.judge_complete.character;
    return { candidates: event.merged_characters, appearances: event.appearances };
  }
  if (kind === "scene") {
    const event = storyboardAssetWire.producer.judge_complete.scene;
    return { candidates: event.scenes, appearances: event.appearances };
  }
  const event = storyboardAssetWire.producer.judge_complete.prop;
  return { candidates: event.props, appearances: event.appearances };
}
