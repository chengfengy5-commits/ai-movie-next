// @vitest-environment node
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { InvalidResponseError } from "./contracts";
import { createApiServices, type WorkspaceServices } from "./services";
import { API_TOKEN_KEY, type StorageLike } from "./storage";

const fixturePath = fileURLToPath(new URL("../../../../tools/api-fixture/server.mjs", import.meta.url));

class MemoryStorage implements StorageLike {
  private readonly values = new Map<string, string>();

  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }

  removeItem(key: string): void {
    this.values.delete(key);
  }
}

interface FixtureProcess {
  baseUrl: string;
  output(): string;
  stop(): Promise<void>;
}

async function startFixture(overrides: Record<string, string> = {}): Promise<FixtureProcess> {
  const inheritedEnvironment = Object.fromEntries(
    Object.entries(process.env).filter(([key]) => !key.startsWith("FIXTURE_")),
  );
  const child = spawn(process.execPath, [fixturePath], {
    env: { ...inheritedEnvironment, ...overrides, FIXTURE_PORT: "0" },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let captured = "";
  child.stdout.setEncoding("utf8");
  child.stderr.setEncoding("utf8");
  child.stdout.on("data", (chunk: string) => { captured += chunk; });
  child.stderr.on("data", (chunk: string) => { captured += chunk; });

  let baseUrl: string;
  try {
    baseUrl = await new Promise<string>((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`Fixture startup timed out: ${captured}`)), 5_000);
      const onData = (chunk: string) => {
        const match = chunk.match(/http:\/\/127\.0\.0\.1:(\d+)\/api/);
        if (match?.[1]) {
          clearTimeout(timer);
          child.stdout.off("data", onData);
          resolve(`http://127.0.0.1:${match[1]}/api`);
        }
      };
      child.stdout.on("data", onData);
      child.once("error", (error) => {
        clearTimeout(timer);
        reject(error);
      });
      child.once("close", (code) => {
        clearTimeout(timer);
        reject(new Error(`Fixture exited before startup (${code}): ${captured}`));
      });
    });
  } catch (error) {
    child.kill("SIGTERM");
    throw error;
  }

  return {
    baseUrl,
    output: () => captured,
    async stop() {
      if (child.exitCode !== null || child.signalCode !== null) {
        return;
      }
      child.kill("SIGTERM");
      await new Promise<void>((resolve) => {
        const timer = setTimeout(resolve, 1_000);
        child.once("close", () => {
          clearTimeout(timer);
          resolve();
        });
      });
    },
  };
}

interface RequestRecord {
  method: string;
  pathname: string;
  authorization: string | null;
}

function recordedFetcher(records: RequestRecord[]): typeof fetch {
  return async (input, init) => {
    const url = input instanceof URL
      ? input
      : input instanceof Request
        ? new URL(input.url)
        : new URL(input);
    records.push({
      method: init?.method ?? (input instanceof Request ? input.method : "GET"),
      pathname: `${url.pathname}${url.search}`,
      authorization: new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined))
        .get("Authorization"),
    });
    return globalThis.fetch(input, init);
  };
}

function createServices(
  baseUrl: string,
  options: { timeoutMs?: number; fetcher?: typeof fetch } = {},
) {
  const storage = new MemoryStorage();
  storage.setItem(API_TOKEN_KEY, "fixture-demo-token");
  const services = createApiServices(baseUrl, {
    storage,
    fetcher: options.fetcher ?? globalThis.fetch,
    ...(options.timeoutMs === undefined ? {} : { timeoutMs: options.timeoutMs }),
  });
  return { services, storage };
}

type AssetType = "characters" | "scenes" | "props";

function listAssetType(
  services: WorkspaceServices,
  type: AssetType,
  seriesId: string,
  signal: AbortSignal,
) {
  switch (type) {
    case "characters":
      return services.listCharacters(seriesId, signal);
    case "scenes":
      return services.listScenes(seriesId, signal);
    case "props":
      return services.listProps(seriesId, signal);
  }
}

function signal(): AbortSignal {
  return new AbortController().signal;
}

const collectionStatusCases = [401, 403, 404, 422, 500] as const;

describe("asset collection API services over real HTTP", () => {
  it("reads all eight allowed routes with Bearer auth and preserves the complete typed DTOs", async () => {
    const fixture = await startFixture();
    const requests: RequestRecord[] = [];
    const { services } = createServices(fixture.baseUrl, { fetcher: recordedFetcher(requests) });

    try {
      await services.login({ username: "demo", password: "demo123" }, signal());
      const restored = await services.restore(signal());
      const series = await services.listSeries(signal());
      const firstSeries = series[0];
      if (!firstSeries) {
        throw new Error("Fixture did not return its expected first series.");
      }
      const chapters = await services.listChapters(firstSeries.id, signal());
      const firstChapter = chapters[0];
      if (!firstChapter) {
        throw new Error("Fixture did not return its expected first chapter.");
      }
      const storyboardAssets = await services.listStoryboardAssets(firstSeries.id, firstChapter.id, signal());
      const characters = await services.listCharacters(firstSeries.id, signal());
      const scenes = await services.listScenes(firstSeries.id, signal());
      const props = await services.listProps(firstSeries.id, signal());

      expect(restored?.id).toBe("demo-user");
      expect(storyboardAssets).toHaveLength(2);
      expect(characters.map(({ name }) => name)).toEqual(["", "沈照", "林序"]);
      expect(scenes.map(({ title }) => title)).toEqual(["", "盐仓码头", "潮汐巷"]);
      expect(props.map(({ name }) => name)).toEqual(["", "旧铜钥匙", "蓝布行李箱"]);

      const emptyCharacter = characters[0];
      const sharedCharacter = characters[1];
      const omittedNamingCharacter = characters[2];
      if (!emptyCharacter || !sharedCharacter || !omittedNamingCharacter) {
        throw new Error("Fixture did not return its three character examples.");
      }
      expect(emptyCharacter.name).toBe("");
      expect(emptyCharacter.aliases).toBeNull();
      expect(emptyCharacter.canonical_key).toBeNull();
      expect([
        emptyCharacter.gender,
        emptyCharacter.age,
        emptyCharacter.role,
        emptyCharacter.appearance,
        emptyCharacter.description,
        emptyCharacter.image_url,
        emptyCharacter.audio_url,
        emptyCharacter.voice_ref,
      ]).toEqual([null, null, null, null, null, null, null, null]);
      expect(omittedNamingCharacter.aliases).toBeNull();
      expect(omittedNamingCharacter.canonical_key).toBeNull();
      expect(sharedCharacter.aliases).toEqual(["阿照", "照姐"]);
      expect(sharedCharacter.gender).toBe("女");
      expect(sharedCharacter.audio_url).toBe("https://audio.example.invalid/fixture-character.mp3");

      const sharedId = sharedCharacter.id;
      const sharedScene = scenes.find(({ id }) => id === sharedId);
      const sharedProp = props.find(({ id }) => id === sharedId);
      expect(sharedScene?.aliases).toEqual(["老盐仓外", "仓库码头"]);
      expect(sharedScene?.canonical_key).toBe("盐仓|夜");
      expect(sharedProp?.name).toBe("旧铜钥匙");
      expect(new Set([
        sharedCharacter.image_url,
        sharedScene?.image_url,
        sharedProp?.image_url,
      ]).size).toBe(3);
      expect(scenes[2]?.aliases).toBeNull();
      expect(scenes[2]?.canonical_key).toBeNull();
      expect(props[2]?.aliases).toBeNull();
      expect(props[2]?.canonical_key).toBeNull();

      expect(requests.map(({ method, pathname }) => [method, pathname])).toEqual([
        ["POST", "/api/auth/login"],
        ["GET", "/api/auth/me"],
        ["GET", "/api/series"],
        ["GET", "/api/series/fixture-series-01/chapters"],
        ["GET", "/api/series/fixture-series-01/storyboard-assets?chapter_id=fixture-series-01-chapter-02"],
        ["GET", "/api/series/fixture-series-01/characters"],
        ["GET", "/api/series/fixture-series-01/scenes"],
        ["GET", "/api/series/fixture-series-01/props"],
      ]);
      expect(requests[0]?.authorization).toBeNull();
      expect(requests.slice(1).every(({ authorization }) => authorization === "Bearer fixture-demo-token")).toBe(true);

      const fixtureRequests = fixture.output().split("\n")
        .filter((line) => line.startsWith("{"))
        .map((line) => JSON.parse(line) as { method: string; pathname: string; status: number });
      expect(fixtureRequests.map(({ method, pathname }) => [method, pathname])).toEqual(
        requests.map(({ method, pathname }) => [method, pathname]),
      );
      expect(fixture.output()).not.toContain("demo123");
      expect(fixture.output()).not.toContain("fixture-demo-token");
    } finally {
      await fixture.stop();
    }
  });

  it("encodes each series id as one path segment and rejects empty or dot identities before fetch", async () => {
    const fixture = await startFixture();
    const requests: RequestRecord[] = [];
    const { services } = createServices(fixture.baseUrl, { fetcher: recordedFetcher(requests) });
    const specialId = "series /?#&雪";

    try {
      for (const type of ["characters", "scenes", "props"] as const) {
        await expect(listAssetType(services, type, specialId, signal()))
          .rejects.toMatchObject({ kind: "http", status: 404 });
      }
      expect(requests.map(({ pathname }) => pathname)).toEqual([
        "/api/series/series%20%2F%3F%23%26%E9%9B%AA/characters",
        "/api/series/series%20%2F%3F%23%26%E9%9B%AA/scenes",
        "/api/series/series%20%2F%3F%23%26%E9%9B%AA/props",
      ]);

      const requestCount = requests.length;
      for (const invalidId of ["", ".", ".."] as const) {
        for (const type of ["characters", "scenes", "props"] as const) {
          await expect(listAssetType(services, type, invalidId, signal()))
            .rejects.toMatchObject({ kind: "invalid-response" });
        }
      }
      expect(requests).toHaveLength(requestCount);
    } finally {
      await fixture.stop();
    }
  });

  it.each(collectionStatusCases)("preserves HTTP %s from the real characters endpoint", async (status) => {
    const fixture = await startFixture({ FIXTURE_CHARACTERS_STATUS: String(status) });
    const { services, storage } = createServices(fixture.baseUrl);
    try {
      await expect(services.listCharacters("fixture-series-01", signal()))
        .rejects.toMatchObject({ kind: "http", status });
      expect(storage.getItem(API_TOKEN_KEY)).toBe("fixture-demo-token");
    } finally {
      await fixture.stop();
    }
  });

  it.each(["timeout", "body-timeout"] as const)("classifies a real scenes %s as timeout", async (mode) => {
    const fixture = await startFixture({ FIXTURE_SCENES_MODE: mode });
    const { services } = createServices(fixture.baseUrl, { timeoutMs: 50 });
    try {
      await expect(services.listScenes("fixture-series-01", signal()))
        .rejects.toMatchObject({ kind: "timeout" });
    } finally {
      await fixture.stop();
    }
  });

  it.each([
    { type: "characters", mode: "invalid-json", errorKind: "invalid-response" },
    { type: "props", mode: "invalid-structure", errorKind: "invalid-response" },
  ] as const)("rejects a real $type response with $mode", async ({ type, mode, errorKind }) => {
    const endpoint = type.toUpperCase();
    const fixture = await startFixture({ [`FIXTURE_${endpoint}_MODE`]: mode });
    const { services } = createServices(fixture.baseUrl);
    try {
      const pending = listAssetType(services, type, "fixture-series-01", signal());
      if (mode === "invalid-json") {
        await expect(pending).rejects.toMatchObject({ kind: errorKind });
      } else {
        await expect(pending).rejects.toBeInstanceOf(InvalidResponseError);
      }
    } finally {
      await fixture.stop();
    }
  });

  it("treats a successful empty scene list as valid empty data", async () => {
    const fixture = await startFixture({ FIXTURE_SCENES_EMPTY: "true" });
    const { services } = createServices(fixture.baseUrl);
    try {
      await expect(services.listScenes("fixture-series-01", signal())).resolves.toEqual([]);
    } finally {
      await fixture.stop();
    }
  });

  it("classifies cancellation of a real in-flight prop request as aborted", async () => {
    const fixture = await startFixture({ FIXTURE_PROPS_DELAY_MS: "500" });
    const { services } = createServices(fixture.baseUrl);
    const controller = new AbortController();
    try {
      const pending = services.listProps("fixture-series-01", controller.signal);
      await new Promise((resolve) => setTimeout(resolve, 50));
      controller.abort();
      await expect(pending).rejects.toMatchObject({ kind: "aborted" });
    } finally {
      await fixture.stop();
    }
  });
});
