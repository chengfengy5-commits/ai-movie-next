// @vitest-environment node
import { describe, expect, it, vi } from "vitest";
import {
  CharacterAudioError,
  type CharacterAudioRunContext,
} from "./characterAudio";
import { createCharacterAudioHttpPort } from "./characterAudioHttp";
import type { Character } from "./contracts";
import { API_TOKEN_KEY, API_USER_KEY, type StorageLike } from "./storage";

class MemoryStorage implements StorageLike {
  private readonly values = new Map<string, string>();

  getItem(key: string): string | null { return this.values.get(key) ?? null; }
  setItem(key: string, value: string): void { this.values.set(key, value); }
  removeItem(key: string): void { this.values.delete(key); }
}

const apiBaseUrl = "http://127.0.0.1:4175/api";
const actorId = "character-audio-http-user";
const seriesId = "character-audio-http-series";
const characterId = "character-audio-http-id";
const uploadedUrl = "/uploads/audio/character-audio-http.mp3";

function makeCharacter(
  audioUrl: string | null = null,
  voiceRef: string | null = "清冷",
  id = characterId,
): Character {
  return {
    id,
    series_id: seriesId,
    name: "顾宁",
    gender: "女",
    age: "32",
    role: "记者",
    appearance: "短发",
    description: "保留原有角色资料。",
    image_url: "https://images.example.invalid/character.png",
    audio_url: audioUrl,
    voice_ref: voiceRef,
    aliases: ["宁宁"],
    canonical_key: "gu-ning",
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
  };
}

function makeStorage(): MemoryStorage {
  const storage = new MemoryStorage();
  storage.setItem(API_TOKEN_KEY, "captured-audio-token");
  storage.setItem(API_USER_KEY, JSON.stringify({ id: actorId }));
  return storage;
}

function makeContext(overrides: Partial<CharacterAudioRunContext> = {}): CharacterAudioRunContext {
  return {
    actorId,
    seriesId,
    characterId,
    directoryEpoch: 4,
    portEpoch: 9,
    character: makeCharacter(),
    isContextCurrent: () => true,
    ...overrides,
  };
}

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), { status, headers: { "content-type": "application/json" } });
}

function audioFile(
  name = "voice.mp3",
  type = "audio/mpeg",
  bytes: Uint8Array = new Uint8Array([1, 2, 3]),
): File {
  return new File([new Uint8Array(bytes).buffer], name, { type });
}

function requestHeaders(init: RequestInit): Headers {
  return new Headers(init.headers);
}

describe("character audio HTTP port", () => {
  it("uploads once, saves only the opaque audio receipt, then reads the persisted directory", async () => {
    const storage = makeStorage();
    const calls: Array<{ url: URL; init: RequestInit }> = [];
    let persisted = makeCharacter();
    const fetcher: typeof fetch = async (input, init = {}) => {
      const url = input instanceof URL ? input : new URL(input instanceof Request ? input.url : input);
      calls.push({ url, init });
      if (init.method === "POST" && url.pathname === "/api/upload/audio") {
        expect(init.body).toBeInstanceOf(FormData);
        expect((init.body as FormData).get("file")).toBeInstanceOf(File);
        return jsonResponse({ url: uploadedUrl });
      }
      if (init.method === "PUT" && url.pathname === `/api/characters/${characterId}`) {
        expect(JSON.parse(String(init.body))).toEqual({ audio_url: uploadedUrl });
        persisted = makeCharacter(uploadedUrl);
        return jsonResponse(persisted);
      }
      if (init.method === "GET" && url.pathname === `/api/series/${seriesId}/characters`) {
        return jsonResponse([persisted]);
      }
      throw new Error(`Unexpected request ${init.method} ${url.pathname}`);
    };
    const run = createCharacterAudioHttpPort(apiBaseUrl, { storage, fetcher }).beginRun(makeContext());
    const signal = new AbortController().signal;
    const receipt = await run.uploadAudio(audioFile(), signal);

    expect(run.canSaveAudio(receipt)).toBe(true);
    expect(JSON.stringify(receipt)).not.toContain("uploads");
    const updated = await run.save({ audio: receipt }, signal);
    const directory = await run.readDirectory(signal);

    expect(updated).toEqual(persisted);
    expect(directory.items).toEqual([persisted]);
    expect(directory.audioSources.has(characterId)).toBe(true);
    expect(run.canSaveAudio(receipt)).toBe(false);
    expect(calls.map(({ init, url }) => `${init.method} ${url.pathname}`)).toEqual([
      "POST /api/upload/audio",
      `PUT /api/characters/${characterId}`,
      `GET /api/series/${seriesId}/characters`,
    ]);
    expect(calls.every(({ init }) => requestHeaders(init).get("authorization") === "Bearer captured-audio-token")).toBe(true);
    expect(calls.every(({ init }) => init.redirect === "error" && init.credentials === "omit")).toBe(true);
  });

  it("keeps voice-only and removal payloads limited to explicitly changed fields", async () => {
    const storage = makeStorage();
    const payloads: unknown[] = [];
    const fetcher: typeof fetch = async (input, init = {}) => {
      const url = input instanceof URL ? input : new URL(input instanceof Request ? input.url : input);
      if (init.method === "PUT" && url.pathname === `/api/characters/${characterId}`) {
        const payload = JSON.parse(String(init.body)) as unknown;
        payloads.push(payload);
        return jsonResponse(makeCharacter(
          Object.prototype.hasOwnProperty.call(payload, "audio_url") ? (payload as { audio_url: string | null }).audio_url : null,
          Object.prototype.hasOwnProperty.call(payload, "voice_ref") ? (payload as { voice_ref: string }).voice_ref : null,
        ));
      }
      throw new Error(`Unexpected request ${init.method} ${url.pathname}`);
    };
    const run = createCharacterAudioHttpPort(apiBaseUrl, { storage, fetcher }).beginRun(makeContext());
    const signal = new AbortController().signal;
    const voiceTarget = Object.freeze({ voice_ref: "低沉、平静" });
    const removeTarget = Object.freeze({ audio: null });
    await run.save(voiceTarget, signal);
    await run.save(removeTarget, signal);

    expect(payloads).toEqual([{ voice_ref: "低沉、平静" }, { audio_url: null }]);
  });

  it("reads owned uploads with the captured token and public audio without credentials", async () => {
    const storage = makeStorage();
    const readCalls: Array<{ url: URL; headers: Headers; init: RequestInit }> = [];
    const publicUrl = "https://cdn.example.com/voice.mp3?sig=private-query";
    const persisted = makeCharacter("http://127.0.0.1:4175/uploads/audio/saved.mp3");
    const publicCharacter = makeCharacter(publicUrl, "清冷", characterId + "-second");
    const fetcher: typeof fetch = async (input, init = {}) => {
      const url = input instanceof URL ? input : new URL(input instanceof Request ? input.url : input);
      readCalls.push({ url, headers: requestHeaders(init), init });
      if (init.method === "GET" && url.pathname === `/api/series/${seriesId}/characters`) {
        return jsonResponse([persisted, publicCharacter]);
      }
      if (init.method === "GET" && url.pathname.startsWith("/uploads/")) {
        return new Response(new Uint8Array([4, 5, 6]), { headers: { "content-type": "audio/mpeg" } });
      }
      if (init.method === "GET" && url.origin === "https://cdn.example.com") {
        return new Response(new Uint8Array([7, 8, 9]), { headers: { "content-type": "audio/mpeg" } });
      }
      throw new Error(`Unexpected request ${init.method} ${url.pathname}`);
    };
    const run = createCharacterAudioHttpPort(apiBaseUrl, { storage, fetcher }).beginRun(makeContext());
    const directory = await run.readDirectory(new AbortController().signal);
    const ownedSource = directory.audioSources.get(characterId);
    const publicSource = directory.audioSources.get(characterId + "-second");
    expect(ownedSource).toBeDefined();
    expect(publicSource).toBeDefined();
    expect(directory.items).toHaveLength(2);
    const sources = [...directory.audioSources.values()];
    await run.readAudio(sources[0]!, new AbortController().signal);
    await run.readAudio(sources[1]!, new AbortController().signal);

    const ownedCall = readCalls.find(({ url }) => url.pathname === "/uploads/audio/saved.mp3");
    const publicCall = readCalls.find(({ url }) => url.origin === "https://cdn.example.com");
    expect(ownedCall?.headers.get("authorization")).toBe("Bearer captured-audio-token");
    expect(publicCall?.headers.has("authorization")).toBe(false);
    expect(publicCall?.init.credentials).toBe("omit");
    expect(publicCall?.init.redirect).toBe("error");
    expect(publicCall?.url.search).toBe("?sig=private-query");
  });

  it("rejects non-audio and streamed audio larger than the playback limit", async () => {
    const storage = makeStorage();
    let mediaResponse = new Response(new Uint8Array([1]), { headers: { "content-type": "text/plain" } });
    const fetcher: typeof fetch = async (input, init = {}) => {
      const url = input instanceof URL ? input : new URL(input instanceof Request ? input.url : input);
      if (init.method === "GET" && url.pathname === `/api/series/${seriesId}/characters`) {
        return jsonResponse([makeCharacter("/uploads/audio/saved.mp3")]);
      }
      if (init.method === "GET" && url.pathname === "/uploads/audio/saved.mp3") return mediaResponse;
      throw new Error(`Unexpected request ${init.method} ${url.pathname}`);
    };
    const run = createCharacterAudioHttpPort(apiBaseUrl, { storage, fetcher }).beginRun(makeContext());
    const directory = await run.readDirectory(new AbortController().signal);
    const source = directory.audioSources.get(characterId);
    expect(source).toBeDefined();

    await expect(run.readAudio(source!, new AbortController().signal)).rejects.toMatchObject({ kind: "read-error" });
    mediaResponse = new Response(new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new Uint8Array(10 * 1024 * 1024 + 1));
        controller.close();
      },
    }), { headers: { "content-type": "audio/mpeg" } });
    await expect(run.readAudio(source!, new AbortController().signal)).rejects.toMatchObject({ kind: "read-error" });
  });

  it("rejects MIME, extension, size, identity, and context mismatches before writes", async () => {
    const storage = makeStorage();
    const fetcher = vi.fn<typeof fetch>(async () => jsonResponse({ url: uploadedUrl }));
    const port = createCharacterAudioHttpPort(apiBaseUrl, { storage, fetcher });
    const run = port.beginRun(makeContext());
    const signal = new AbortController().signal;

    await expect(run.uploadAudio(audioFile("voice.mp3", "audio/wav"), signal))
      .rejects.toMatchObject({ kind: "not-sent" } satisfies Partial<CharacterAudioError>);
    await expect(run.uploadAudio(audioFile("voice.mp3", "audio/mpeg", new Uint8Array(10 * 1024 * 1024 + 1)), signal))
      .rejects.toMatchObject({ kind: "not-sent" } satisfies Partial<CharacterAudioError>);
    await expect(run.uploadAudio(audioFile("voice.mp3", "audio/mpeg", new Uint8Array(10 * 1024 * 1024)), signal))
      .resolves.toBeDefined();
    await expect(run.uploadAudio(audioFile("voice.wma", "audio/x-ms-wma"), signal))
      .resolves.toBeDefined();
    await expect(run.uploadAudio(audioFile("voice.wma", "audio/wma"), signal)).resolves.toBeDefined();
    await expect(run.uploadAudio(audioFile("voice.wma", ""), signal)).resolves.toBeDefined();
    await expect(run.uploadAudio(audioFile("voice.txt", "text/plain"), signal))
      .rejects.toMatchObject({ kind: "not-sent" } satisfies Partial<CharacterAudioError>);
    expect(fetcher).toHaveBeenCalledTimes(4);
    expect(() => port.beginRun(makeContext({ character: makeCharacter(), characterId: "different-id" })))
      .toThrowError(CharacterAudioError);

    storage.setItem(API_TOKEN_KEY, "replacement-token");
    await expect(run.readDirectory(signal)).rejects.toMatchObject({ kind: "read-error" });
    expect(fetcher).toHaveBeenCalledTimes(4);
  });

  it("does not read or write after the captured series or character identity changes", async () => {
    const storage = makeStorage();
    const fetcher = vi.fn<typeof fetch>(async () => jsonResponse([makeCharacter()]));
    let activeSeriesId = seriesId;
    let activeCharacterId = characterId;
    const run = createCharacterAudioHttpPort(apiBaseUrl, { storage, fetcher }).beginRun(makeContext({
      isContextCurrent: () => activeSeriesId === seriesId && activeCharacterId === characterId,
    }));

    activeSeriesId = "another-series";
    await expect(run.readDirectory(new AbortController().signal)).rejects.toMatchObject({ kind: "read-error" });
    activeSeriesId = seriesId;
    activeCharacterId = "another-character";
    await expect(run.save(Object.freeze({ voice_ref: "不可提交" }), new AbortController().signal))
      .rejects.toMatchObject({ kind: "not-sent" });

    expect(fetcher).not.toHaveBeenCalled();
  });

  it("keeps an ambiguous write unknown and refuses automatic replay", async () => {
    const storage = makeStorage();
    const fetcher = vi.fn<typeof fetch>(async (input, init = {}) => {
      const url = input instanceof URL ? input : new URL(input instanceof Request ? input.url : input);
      if (init.method === "POST" && url.pathname === "/api/upload/audio") return jsonResponse({ url: uploadedUrl });
      if (init.method === "PUT") return jsonResponse({ detail: "temporary failure" }, 503);
      throw new Error(`Unexpected request ${init.method} ${url.pathname}`);
    });
    const run = createCharacterAudioHttpPort(apiBaseUrl, { storage, fetcher }).beginRun(makeContext());
    const signal = new AbortController().signal;
    const receipt = await run.uploadAudio(audioFile(), signal);
    const target = Object.freeze({ audio: receipt });
    await expect(run.save(target, signal)).rejects.toMatchObject({ kind: "unknown", status: 503 });
    await expect(run.save(target, signal)).rejects.toMatchObject({ kind: "not-sent" });
    expect(fetcher.mock.calls.filter(([, init]) => init?.method === "PUT")).toHaveLength(1);
  });

  it("does not read same-origin non-upload URLs as an authenticated media request", async () => {
    const storage = makeStorage();
    const unsafeCharacter = makeCharacter("http://127.0.0.1:4175/private-audio/voice.mp3");
    const fetcher = vi.fn<typeof fetch>(async (input, init = {}) => {
      const url = input instanceof URL ? input : new URL(input instanceof Request ? input.url : input);
      if (init.method === "GET" && url.pathname === `/api/series/${seriesId}/characters`) {
        return jsonResponse([unsafeCharacter]);
      }
      throw new Error(`Unexpected request ${init.method} ${url.pathname}`);
    });
    const run = createCharacterAudioHttpPort(apiBaseUrl, { storage, fetcher }).beginRun(makeContext());
    const directory = await run.readDirectory(new AbortController().signal);
    const source = directory.audioSources.get(characterId);
    expect(source).toBeDefined();
    await expect(run.readAudio(source!, new AbortController().signal)).rejects.toMatchObject({ kind: "read-error" });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});
