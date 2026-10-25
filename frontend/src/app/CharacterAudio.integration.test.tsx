import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../features/auth/AuthProvider";
import { demoSeries } from "../features/series/demoSeries";
import type { Character, Series, User } from "../shared/api/contracts";
import { createApiServices } from "../shared/api/services";
import { API_TOKEN_KEY, API_USER_KEY } from "../shared/api/storage";
import { Workspace } from "./Workspace";

const apiBaseUrl = "http://127.0.0.1:4175/api";
const sessionToken = "character-audio-session-token";
const user: User = {
  id: "character-audio-user",
  username: "音频角色作者",
  email: "character-audio@example.invalid",
  is_superuser: false,
  membership_type: "free",
  membership_expires_at: null,
  avatar_url: null,
  bio: null,
  created_at: "2026-10-01T00:00:00Z",
};
const series: Series = {
  ...demoSeries[0]!,
  id: "character-audio-series",
  user_id: user.id,
  name: "角色声音测试剧集",
  team_id: null,
  team_name: null,
  owner_name: user.username,
  claimed_by: null,
  claimed_by_username: null,
  claimed_by_avatar_url: null,
  can_enter: true,
};

function makeCharacter(overrides: Partial<Character> = {}): Character {
  return {
    id: "character-audio-main",
    series_id: series.id,
    name: "顾宁",
    gender: "女",
    age: "32",
    role: "记者",
    appearance: "短发",
    description: "旧图文资料保持不变。",
    image_url: "https://images.example.invalid/character.png",
    audio_url: null,
    voice_ref: "清冷、克制",
    aliases: ["宁宁"],
    canonical_key: "gu-ning",
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    ...overrides,
  };
}

interface RequestRecord {
  readonly url: URL;
  readonly method: string;
  readonly headers: Headers;
  readonly body: unknown;
  readonly signal: AbortSignal | null;
}

interface ScenarioOptions {
  readonly characters?: Character[];
  readonly characterReadResponse?: (readNumber: number, characters: Character[]) => Response | null;
  readonly uploadResponse?: (uploadNumber: number) => Response | Promise<Response>;
  readonly updateResponse?: (body: Record<string, unknown>, character: Character) => Response;
}

interface CharacterAudioScenario {
  readonly requests: RequestRecord[];
  readonly fetcher: typeof fetch;
  readonly getCharacters: () => Character[];
  readonly getStoredActorId: () => string | null;
  readonly mount: () => void;
  readonly replaceServices: (nextUser?: User) => void;
  readonly unmount: () => void;
}

const originalCreateObjectUrl = Object.getOwnPropertyDescriptor(URL, "createObjectURL");
const originalRevokeObjectUrl = Object.getOwnPropertyDescriptor(URL, "revokeObjectURL");

function installObjectUrlSupport() {
  let sequence = 0;
  const revoked: string[] = [];
  Object.defineProperty(URL, "createObjectURL", {
    configurable: true,
    value: vi.fn(() => `blob:character-audio-test/${++sequence}`),
  });
  Object.defineProperty(URL, "revokeObjectURL", {
    configurable: true,
    value: vi.fn((url: string) => { revoked.push(url); }),
  });
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
  return { revoked };
}

function createScenario(options: ScenarioOptions = {}): CharacterAudioScenario {
  const requests: RequestRecord[] = [];
  let characters = [...(options.characters ?? [makeCharacter()])];
  let currentUser = user;
  let characterReadCount = 0;
  let uploadCount = 0;
  let rerender: ReturnType<typeof render>["rerender"] | null = null;
  let unmount: (() => void) | null = null;

  const fetcher = vi.fn<typeof fetch>(async (input, init = {}) => {
    const url = input instanceof URL ? input : new URL(input instanceof Request ? input.url : input);
    const method = init.method ?? "GET";
    const headers = new Headers(init.headers);
    const body = typeof init.body === "string" ? JSON.parse(init.body) as unknown : init.body ?? null;
    const signal = init.signal instanceof AbortSignal ? init.signal : null;
    requests.push({ url, method, headers, body, signal });

    if (url.pathname === "/api/auth/me") return Response.json(currentUser);
    if (url.pathname === "/api/series" && method === "GET") return Response.json([series]);
    if (url.pathname === "/api/teams/my" && method === "GET") return Response.json([]);
    if (url.pathname === `/api/series/${series.id}/chapters` && method === "GET") return Response.json([]);
    if (url.pathname === `/api/series/${series.id}/characters` && method === "GET") {
      characterReadCount += 1;
      const override = options.characterReadResponse?.(characterReadCount, characters);
      if (override !== null && override !== undefined) return override;
      return Response.json(characters);
    }
    if (url.pathname === `/api/series/${series.id}/scenes` || url.pathname === `/api/series/${series.id}/props`) {
      return Response.json([]);
    }
    if (url.pathname === "/api/upload/audio" && method === "POST") {
      uploadCount += 1;
      return options.uploadResponse?.(uploadCount) ?? Response.json({ url: `/uploads/audio/voice-${uploadCount}.mp3` });
    }
    if (url.pathname.startsWith("/uploads/audio/") && method === "GET") {
      return new Response(new Uint8Array([1, 2, 3, 4]), {
        headers: { "Content-Type": "audio/mpeg", "Content-Length": "4" },
      });
    }
    if (url.pathname.startsWith("/api/characters/") && method === "PUT") {
      const characterId = decodeURIComponent(url.pathname.slice("/api/characters/".length));
      const current = characters.find((candidate) => candidate.id === characterId);
      if (current === undefined) return Response.json({ detail: "missing" }, { status: 404 });
      const update = body as Record<string, unknown>;
      characters = characters.map((candidate) => candidate.id === characterId
        ? { ...candidate, ...update, updated_at: "2026-10-25T00:00:00Z" }
        : candidate);
      const updated = characters.find((candidate) => candidate.id === characterId)!;
      return options.updateResponse?.(update, updated) ?? Response.json(updated);
    }
    if (method === "GET") return Response.json([]);
    throw new Error(`Unexpected request: ${method} ${url.pathname}`);
  });

  function makeServices() {
    return createApiServices(apiBaseUrl, { storage: window.localStorage, fetcher });
  }

  return {
    requests,
    fetcher,
    getCharacters: () => characters,
    getStoredActorId: () => {
      const serialized = window.localStorage.getItem(API_USER_KEY);
      if (serialized === null) return null;
      try {
        const stored = JSON.parse(serialized) as { id?: unknown };
        return typeof stored.id === "string" ? stored.id : null;
      } catch {
        return null;
      }
    },
    mount() {
      window.localStorage.clear();
      window.localStorage.setItem(API_TOKEN_KEY, sessionToken);
      const view = render(
        <AuthProvider services={makeServices()}>
          <Workspace />
        </AuthProvider>,
      );
      rerender = view.rerender;
      unmount = view.unmount;
    },
    replaceServices(nextUser) {
      if (rerender === null) throw new Error("The workspace scenario has not been mounted.");
      if (nextUser !== undefined) currentUser = nextUser;
      rerender(
        <AuthProvider services={makeServices()}>
          <Workspace />
        </AuthProvider>,
      );
    },
    unmount() {
      unmount?.();
      unmount = null;
    },
  };
}

async function enterCharacterAudio(userEvents: ReturnType<typeof userEvent.setup>) {
  const seriesHeading = await screen.findByRole("heading", { name: series.name });
  const seriesCard = seriesHeading.closest("article");
  if (!(seriesCard instanceof HTMLElement)) throw new Error("The series card was not rendered.");
  await userEvents.click(within(seriesCard).getByRole("button", { name: /只读查看素材库/ }));
  const library = await screen.findByRole("region", { name: `${series.name} · 素材库` });
  const characterHeading = await within(library).findByRole("heading", { name: "顾宁" });
  const characterCard = characterHeading.closest("article");
  if (!(characterCard instanceof HTMLElement)) throw new Error("The character card was not rendered.");
  await userEvents.click(within(characterCard).getByRole("button", { name: "音频与音色" }));
  const dialog = await screen.findByRole("dialog", { name: /音频与音色/ });
  await within(dialog).findByText(/已读取完整角色目录/);
  return { library, characterCard, dialog };
}

async function uploadAudio(userEvents: ReturnType<typeof userEvent.setup>, dialog: HTMLElement): Promise<void> {
  const file = new File([new Uint8Array([1, 2, 3])], "voice.mp3", { type: "audio/mpeg" });
  await userEvents.upload(within(dialog).getByLabelText("选择音频文件"), file);
  await userEvents.click(within(dialog).getByRole("button", { name: "上传音频" }));
  await findLatestAttemptMessage(dialog, /服务器已返回本次上传回执/);
}

async function findLatestAttemptMessage(dialog: HTMLElement, matcher: RegExp): Promise<HTMLElement> {
  const attemptRegion = within(dialog).getByRole("region", { name: "本次操作记录" });
  return within(attemptRegion).findByText(matcher);
}

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.restoreAllMocks();
  if (originalCreateObjectUrl === undefined) delete (URL as unknown as { createObjectURL?: unknown }).createObjectURL;
  else Object.defineProperty(URL, "createObjectURL", originalCreateObjectUrl);
  if (originalRevokeObjectUrl === undefined) delete (URL as unknown as { revokeObjectURL?: unknown }).revokeObjectURL;
  else Object.defineProperty(URL, "revokeObjectURL", originalRevokeObjectUrl);
});

describe("character audio workspace integration", () => {
  it("uploads, explicitly saves audio, saves voice-only, removes with null, and preserves character text/image", async () => {
    const userEvents = userEvent.setup();
    const urls = installObjectUrlSupport();
    const scenario = createScenario();
    scenario.mount();
    const { dialog } = await enterCharacterAudio(userEvents);

    await uploadAudio(userEvents, dialog);
    expect(scenario.requests.some((request) => request.method === "PUT")).toBe(false);
    await userEvents.click(within(dialog).getByRole("button", { name: "明确保存音频与音色" }));
    expect(await findLatestAttemptMessage(dialog, /与独立角色目录读取一致/)).toBeInTheDocument();

    const voiceField = within(dialog).getByLabelText("音色描述");
    await userEvents.clear(voiceField);
    await userEvents.type(voiceField, "低沉、平静");
    await userEvents.click(within(dialog).getByRole("button", { name: "明确保存音频与音色" }));
    expect(await findLatestAttemptMessage(dialog, /与独立角色目录读取一致/)).toBeInTheDocument();

    await userEvents.click(within(dialog).getByRole("button", { name: "移除已保存音频" }));
    await userEvents.click(within(dialog).getByRole("button", { name: "明确保存音频与音色" }));
    await findLatestAttemptMessage(dialog, /与独立角色目录读取一致/);

    const writes = scenario.requests.filter((request) => request.method === "PUT");
    expect(writes.map((request) => request.body)).toEqual([
      { audio_url: "/uploads/audio/voice-1.mp3" },
      { voice_ref: "低沉、平静" },
      { audio_url: null },
    ]);
    expect(scenario.requests.filter((request) => request.method === "POST").map((request) => request.url.pathname))
      .toEqual(["/api/upload/audio"]);
    expect(scenario.requests.filter((request) => request.method === "DELETE")).toHaveLength(0);
    expect(scenario.requests.filter((request) => request.url.pathname === `/api/series/${series.id}/characters`
      && request.method === "GET").length).toBeGreaterThanOrEqual(8);
    expect(scenario.getCharacters()[0]).toMatchObject({
      image_url: "https://images.example.invalid/character.png",
      description: "旧图文资料保持不变。",
      aliases: ["宁宁"],
      canonical_key: "gu-ning",
      audio_url: null,
      voice_ref: "低沉、平静",
    });
    const upload = scenario.requests.find((request) => request.method === "POST");
    expect(upload?.body).toBeInstanceOf(FormData);
    expect(upload?.headers.get("authorization")).toBe(`Bearer ${sessionToken}`);
    expect(urls.revoked.length).toBeGreaterThan(0);
  });

  it("exposes only the real character-card entry and refuses duplicate identities", async () => {
    const userEvents = userEvent.setup();
    const duplicate = makeCharacter({ id: "character-audio-main", name: "顾宁副本" });
    const scenario = createScenario({ characters: [makeCharacter(), duplicate] });
    scenario.mount();
    const seriesHeading = await screen.findByRole("heading", { name: series.name });
    const seriesCard = seriesHeading.closest("article");
    if (!(seriesCard instanceof HTMLElement)) throw new Error("The series card was not rendered.");
    await userEvents.click(within(seriesCard).getByRole("button", { name: /只读查看素材库/ }));
    const library = await screen.findByRole("region", { name: `${series.name} · 素材库` });
    expect(await within(library).findByRole("alert")).toHaveTextContent(/服务器返回了重复的角色身份/);
    expect(within(library).queryByRole("button", { name: "音频与音色" })).not.toBeInTheDocument();
    expect(scenario.requests.filter((request) => request.url.pathname === "/api/upload/audio")).toHaveLength(0);
  });

  it("does not submit a stale save after the fresh role baseline changes", async () => {
    const userEvents = userEvent.setup();
    let reads = 0;
    const scenario = createScenario({
      characterReadResponse: (readNumber) => {
        reads = readNumber;
        if (readNumber === 3) return Response.json([makeCharacter({ voice_ref: "新音色" })]);
        return null;
      },
    });
    scenario.mount();
    const { dialog } = await enterCharacterAudio(userEvents);
    await userEvents.clear(within(dialog).getByLabelText("音色描述"));
    await userEvents.type(within(dialog).getByLabelText("音色描述"), "要保存的新音色");
    await userEvents.click(within(dialog).getByRole("button", { name: "明确保存音频与音色" }));

    expect(await findLatestAttemptMessage(dialog, /确认前角色音频或音色已变化/)).toBeInTheDocument();
    expect(scenario.requests.filter((request) => request.method === "PUT")).toHaveLength(0);
    expect(reads).toBeGreaterThanOrEqual(3);
  });

  it("preserves an unknown save and performs only explicit read-only recovery", async () => {
    const userEvents = userEvent.setup();
    let appliedCharacter = makeCharacter();
    const scenario = createScenario({
      updateResponse: (_body, updated) => {
        appliedCharacter = updated;
        return Response.json({ detail: "response was lost" }, { status: 503 });
      },
      characterReadResponse: (readNumber) => readNumber >= 4 ? Response.json([appliedCharacter]) : null,
    });
    scenario.mount();
    const { dialog } = await enterCharacterAudio(userEvents);
    await userEvents.clear(within(dialog).getByLabelText("音色描述"));
    await userEvents.type(within(dialog).getByLabelText("音色描述"), "远处的低声");
    await userEvents.click(within(dialog).getByRole("button", { name: "明确保存音频与音色" }));
    expect(await findLatestAttemptMessage(dialog, /结果无法确认|结果.*未知|服务器响应状态无法确认/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "明确保存音频与音色" })).toBeDisabled();
    expect(scenario.requests.filter((request) => request.method === "PUT")).toHaveLength(1);

    await userEvents.click(within(dialog).getByRole("button", { name: "只读核对保存结果" }));
    expect(await findLatestAttemptMessage(dialog, /原保存回执仍未知/)).toBeInTheDocument();
    expect(scenario.requests.filter((request) => request.method === "PUT")).toHaveLength(1);
  });

  it("keeps an acknowledged save separate from a failed read, then recovers by a read-only check", async () => {
    const userEvents = userEvent.setup();
    let characterReads = 0;
    const scenario = createScenario({
      characterReadResponse: (readNumber) => {
        characterReads = readNumber;
        if (readNumber === 4) return Response.json({ detail: "directory unavailable" }, { status: 503 });
        return null;
      },
    });
    scenario.mount();
    const { dialog } = await enterCharacterAudio(userEvents);
    await userEvents.clear(within(dialog).getByLabelText("音色描述"));
    await userEvents.type(within(dialog).getByLabelText("音色描述"), "已确认的音色");
    await userEvents.click(within(dialog).getByRole("button", { name: "明确保存音频与音色" }));
    expect(await findLatestAttemptMessage(dialog, /独立目录读取失败/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "明确保存音频与音色" })).toBeDisabled();
    expect(scenario.requests.filter((request) => request.method === "PUT")).toHaveLength(1);

    await userEvents.click(within(dialog).getByRole("button", { name: "只读核对保存结果" }));
    expect(await findLatestAttemptMessage(dialog, /独立只读目录读取与已确认的音频、音色变更一致/)).toBeInTheDocument();
    expect(scenario.requests.filter((request) => request.method === "PUT")).toHaveLength(1);
    expect(characterReads).toBeGreaterThanOrEqual(5);
  });

  it("does not turn a late 401 into a logout after the captured token changes", async () => {
    const userEvents = userEvent.setup();
    let completeUpload: ((response: Response) => void) | null = null;
    const scenario = createScenario({
      uploadResponse: () => new Promise<Response>((resolve) => { completeUpload = resolve; }),
    });
    scenario.mount();
    const { dialog } = await enterCharacterAudio(userEvents);
    const file = new File([new Uint8Array([1])], "voice.mp3", { type: "audio/mpeg" });
    await userEvents.upload(within(dialog).getByLabelText("选择音频文件"), file);
    await userEvents.click(within(dialog).getByRole("button", { name: "上传音频" }));
    await waitFor(() => expect(completeUpload).not.toBeNull());
    window.localStorage.setItem(API_TOKEN_KEY, "new-session-token");
    await act(async () => {
      completeUpload?.(Response.json({ detail: "late unauthorized" }, { status: 401 }));
    });

    expect(await findLatestAttemptMessage(dialog, /结果尚未确认|结果.*未知|当前账号或角色目录状态已变化/)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "欢迎回来" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "音频与音色" })).toBeInTheDocument();
    expect(scenario.requests.filter((request) => request.method === "POST")).toHaveLength(1);
  });

  it("aborts pending work on close, restores focus, and revokes local playback URLs", async () => {
    const userEvents = userEvent.setup();
    const urls = installObjectUrlSupport();
    const scenario = createScenario({ characters: [makeCharacter({ audio_url: "/uploads/audio/saved.mp3" })] });
    scenario.mount();
    const { characterCard, dialog } = await enterCharacterAudio(userEvents);
    const opener = within(characterCard).getByRole("button", { name: "音频与音色" });
    await waitFor(() => expect(dialog.querySelector("audio[aria-label='顾宁音频试听']")).not.toBeNull());
    const audio = dialog.querySelector("audio[aria-label='顾宁音频试听']");
    expect(audio?.getAttribute("src")).toMatch(/^blob:character-audio-test\//);

    await userEvents.click(within(dialog).getByRole("button", { name: "关闭音频与音色" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: /音频与音色/ })).not.toBeInTheDocument());
    expect(document.activeElement).toBe(opener);
    expect(urls.revoked).toHaveLength(1);
    expect(scenario.requests.some((request) => request.url.pathname === "/uploads/audio/saved.mp3"
      && request.headers.get("authorization") === `Bearer ${sessionToken}`)).toBe(true);
  });

  it("marks an upload interrupted by a closed window unknown and does not retry it", async () => {
    const userEvents = userEvent.setup();
    const scenario = createScenario({ uploadResponse: () => new Promise<Response>(() => undefined) });
    scenario.mount();
    const { characterCard, dialog } = await enterCharacterAudio(userEvents);
    const file = new File([new Uint8Array([1])], "voice.mp3", { type: "audio/mpeg" });
    await userEvents.upload(within(dialog).getByLabelText("选择音频文件"), file);
    await userEvents.click(within(dialog).getByRole("button", { name: "上传音频" }));
    await waitFor(() => expect(scenario.requests.some((request) => request.method === "POST")).toBe(true));
    const uploadSignal = scenario.requests.find((request) => request.method === "POST")?.signal;

    await userEvents.click(within(dialog).getByRole("button", { name: "关闭音频与音色" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: /音频与音色/ })).not.toBeInTheDocument());
    expect(uploadSignal?.aborted).toBe(true);
    expect(scenario.requests.filter((request) => request.method === "POST")).toHaveLength(1);
    expect(scenario.requests.filter((request) => request.method === "PUT")).toHaveLength(0);

    await userEvents.click(within(characterCard).getByRole("button", { name: "音频与音色" }));
    const reopened = await screen.findByRole("dialog", { name: /音频与音色/ });
    expect(await findLatestAttemptMessage(reopened, /上传已发出；结果未知，不会自动重发/)).toBeInTheDocument();
    expect(scenario.requests.filter((request) => request.method === "POST")).toHaveLength(1);
    expect(scenario.requests.filter((request) => request.method === "PUT")).toHaveLength(0);
  });

  it("reopens under a new services object without allowing stale callbacks to write", async () => {
    const userEvents = userEvent.setup();
    const scenario = createScenario();
    scenario.mount();
    const { dialog } = await enterCharacterAudio(userEvents);
    await userEvents.clear(within(dialog).getByLabelText("音色描述"));
    await userEvents.type(within(dialog).getByLabelText("音色描述"), "等待保存");
    scenario.replaceServices();
    await waitFor(() => expect(screen.queryByRole("dialog", { name: /音频与音色/ })).not.toBeInTheDocument());
    expect(scenario.requests.filter((request) => request.method === "PUT")).toHaveLength(0);
  });

  it("invalidates an active upload when AuthProvider restores a different user", async () => {
    const userEvents = userEvent.setup();
    let completeUpload: ((response: Response) => void) | null = null;
    const scenario = createScenario({
      uploadResponse: () => new Promise<Response>((resolve) => { completeUpload = resolve; }),
    });
    scenario.mount();
    const { dialog } = await enterCharacterAudio(userEvents);
    const file = new File([new Uint8Array([1])], "voice.mp3", { type: "audio/mpeg" });
    await userEvents.upload(within(dialog).getByLabelText("选择音频文件"), file);
    await userEvents.click(within(dialog).getByRole("button", { name: "上传音频" }));
    await waitFor(() => expect(completeUpload).not.toBeNull());

    const nextUser = { ...user, id: "character-audio-next-user", username: "新登录用户" };
    scenario.replaceServices(nextUser);
    await waitFor(() => expect(scenario.getStoredActorId()).toBe(nextUser.id));
    await act(async () => {
      completeUpload?.(Response.json({ url: "/uploads/audio/late.mp3" }));
    });

    expect(await screen.findByText(nextUser.username)).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: /音频与音色/ })).not.toBeInTheDocument();
    expect(scenario.requests.filter((request) => request.method === "POST")).toHaveLength(1);
    expect(scenario.requests.filter((request) => request.method === "PUT")).toHaveLength(0);
  });
});
