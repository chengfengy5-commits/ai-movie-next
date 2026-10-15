import { describe, expect, it } from "vitest";
import { InvalidResponseError } from "../../shared/api/contracts";
import { parseTwoPassSse } from "./sse";

const receipt = {
  submission_id: "submission-1",
  operation: "storyboard.two_pass",
  task_id: "task-1",
  status: "processing",
  quoted_amount: 12,
  billing_status: "charged",
};

function streamFromChunks(chunks: Uint8Array[]): ReadableStreamDefaultReader<Uint8Array> {
  let index = 0;
  const stream = new ReadableStream<Uint8Array>({
    pull(controller) {
      const chunk = chunks[index];
      index += 1;
      if (chunk === undefined) controller.close();
      else controller.enqueue(chunk);
    },
  });
  return stream.getReader();
}

async function collect(reader: ReadableStreamDefaultReader<Uint8Array>): Promise<unknown[]> {
  const events: unknown[] = [];
  for await (const event of parseTwoPassSse(reader, new AbortController().signal)) events.push(event);
  return events;
}

function byteChunks(value: string, chunkSizes: number[]): Uint8Array[] {
  const bytes = new TextEncoder().encode(value);
  const chunks: Uint8Array[] = [];
  let offset = 0;
  for (const size of chunkSizes) {
    if (offset >= bytes.length) break;
    chunks.push(bytes.slice(offset, offset + size));
    offset += size;
  }
  while (offset < bytes.length) {
    chunks.push(bytes.slice(offset, offset + 1));
    offset += 1;
  }
  return chunks;
}

describe("two-pass SSE parser", () => {
  it("handles split UTF-8, CRLF and the existing data.type JSON wire format", async () => {
    const source = [
      `data: ${JSON.stringify({ type: "receipt", ...receipt })}\r\n\r\n`,
      `data: ${JSON.stringify({ type: "status", message: "正在分段" })}\r\n\r\n`,
      `data: ${JSON.stringify({ type: "payload", data: { api_key: "never-render" } })}\r\n\r\n`,
      `data: ${JSON.stringify({ type: "complete", frames: [{ text: "你好，世界", duration: 5 }] })}\r\n\r\n`,
    ].join("");
    const events = await collect(streamFromChunks(byteChunks(source, [1, 2, 1, 3, 4, 2, 1, 5])));

    expect(events).toEqual([
      { type: "receipt", data: receipt },
      { type: "status", data: { type: "status", message: "正在分段" } },
      { type: "complete", data: { type: "complete", frames: [{ text: "你好，世界", duration: 5 }] } },
    ]);
  });

  it("accepts named events while requiring the receipt first", async () => {
    const source = [
      `event: receipt\ndata: ${JSON.stringify(receipt)}\n\n`,
      `event: complete\ndata: ${JSON.stringify({ frames: [{ text: "完成" }] })}`,
    ].join("");
    const events = await collect(streamFromChunks(byteChunks(source, [7, 11, 3])));
    expect(events.map((event) => (event as { type: string }).type)).toEqual(["receipt", "complete"]);
  });

  it("rejects malformed JSON and invalid completed frames", async () => {
    const malformed = `data: ${JSON.stringify({ type: "receipt", ...receipt })}\n\ndata: {bad}\n\n`;
    await expect(collect(streamFromChunks(byteChunks(malformed, [100])))).rejects.toMatchObject({
      kind: "invalid-response",
    });
    const badFrames = [
      `data: ${JSON.stringify({ type: "receipt", ...receipt })}\n\n`,
      `data: ${JSON.stringify({ type: "complete", frames: [{ text: 4 }] })}\n\n`,
    ].join("");
    await expect(collect(streamFromChunks(byteChunks(badFrames, [1024])))).rejects.toBeInstanceOf(InvalidResponseError);
  });

  it("treats EOF without complete or pending as an unresolved request", async () => {
    const source = `data: ${JSON.stringify({ type: "receipt", ...receipt })}\n\n`;
    await expect(collect(streamFromChunks(byteChunks(source, [1024])))).rejects.toMatchObject({
      kind: "network",
      message: expect.stringContaining("可查询任务状态"),
    });
  });

  it("rejects data.type and named event disagreement", async () => {
    const source = `event: status\ndata: ${JSON.stringify({ type: "complete", frames: [{ text: "x" }] })}\n\n`;
    await expect(collect(streamFromChunks(byteChunks(source, [1024])))).rejects.toMatchObject({
      kind: "invalid-response",
    });
  });
});
