import { afterEach, describe, expect, it, vi } from "vitest";
import { API_TOKEN_KEY, type StorageLike } from "./storage";
import { createTeamSeriesHttpPort } from "./teamSeriesHttp";

function memoryStorage(initialToken = "token-one"): StorageLike {
  const values = new Map([[API_TOKEN_KEY, initialToken]]);
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => { values.set(key, value); },
    removeItem: (key) => { values.delete(key); },
  };
}

const emptyPage = { items: [], total: 0, page: 1, page_size: 50, total_pages: 1 };
const responses = [
  [200, { ...emptyPage, page: 1 }],
  [201, { id: "s-new", name: "Story", team_id: "team/id", user_id: "actor", style_prompt_id: null }],
  [200, { message: "已分享到团队「协作组」", team_id: "team/id", team_name: "协作组" }],
  [200, { message: "已移出团队，恢复为个人剧集" }],
  [200, { message: "认领成功", claimed_by: "actor", claimed_at: "2026-10-19T12:00:00" }],
  [200, { message: "该剧集尚未被认领" }],
  [200, { message: "已转交认领", claimed_by: "target/id" }],
] as const;

afterEach(() => vi.restoreAllMocks());

describe("team series HTTP adapter", () => {
  it("uses all seven exact routes, statuses, bodies, encoded opaque IDs, and authenticated no-cookie requests", async () => {
    const storage = memoryStorage();
    let responseIndex = 0;
    const fetcher = vi.fn<typeof fetch>(async () => {
      const [status, payload] = responses[responseIndex++] ?? responses[0];
      return new Response(JSON.stringify(payload), { status });
    });
    const port = createTeamSeriesHttpPort("http://127.0.0.1:8000/api", { storage, fetcher });
    const signal = new AbortController().signal;

    await port.listTeamSeries("team/id", 0, 80, signal);
    await port.createTeamSeries("team/id", {
      name: "Story", description: "", image_url: "", style_prompt_id: null, claim: false,
    }, "actor", signal);
    await port.shareSeries("s/id", "team/id", false, signal);
    await port.unshareSeries("s/id", signal);
    await port.claimTeamSeries("team/id", "s/id", "actor", signal);
    await port.unclaimTeamSeries("team/id", "s/id", signal);
    await port.transferTeamSeries("team/id", "s/id", "target/id", signal);

    expect(fetcher).toHaveBeenCalledTimes(7);
    const calls = fetcher.mock.calls.map(([input, init]) => ({
      url: String(input),
      method: init?.method,
      body: init?.body,
      credentials: init?.credentials,
      authorization: new Headers(init?.headers).get("Authorization"),
    }));
    expect(calls.map(({ url, method }) => [new URL(url).pathname + new URL(url).search, method])).toEqual([
      ["/api/teams/team%2Fid/series?page=0&page_size=80", "GET"],
      ["/api/teams/team%2Fid/series", "POST"],
      ["/api/series/s%2Fid/share", "POST"],
      ["/api/series/s%2Fid/share", "DELETE"],
      ["/api/teams/team%2Fid/series/s%2Fid/claim", "POST"],
      ["/api/teams/team%2Fid/series/s%2Fid/claim", "DELETE"],
      ["/api/teams/team%2Fid/series/s%2Fid/transfer", "POST"],
    ]);
    expect(JSON.parse(String(calls[1]?.body ?? "{}"))).toEqual({
      name: "Story", description: "", image_url: "", style_prompt_id: null, claim: false,
    });
    expect(JSON.parse(String(calls[2]?.body ?? "{}"))).toEqual({ team_id: "team/id", claim: false });
    expect(JSON.parse(String(calls[6]?.body ?? "{}"))).toEqual({ user_id: "target/id" });
    expect(calls.every((call) => call.credentials === "omit" && call.authorization === "Bearer token-one")).toBe(true);
  });

  it("reads the current bearer for each call and retains status/detail on HTTP rejection", async () => {
    const storage = memoryStorage();
    const fetcher = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ ...emptyPage, page_size: 10 }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "不允许此操作" }), { status: 403 }));
    const port = createTeamSeriesHttpPort("http://127.0.0.1:8000/api", { storage, fetcher });
    const controller = new AbortController();
    await port.listTeamSeries("team", 1, 10, controller.signal);
    storage.setItem(API_TOKEN_KEY, "token-two");
    await expect(port.unshareSeries("series", controller.signal)).rejects.toMatchObject({
      kind: "http", status: 403, detail: "不允许此操作",
    });
    expect(new Headers(fetcher.mock.calls[1]?.[1]?.headers).get("Authorization")).toBe("Bearer token-two");
  });

  it("keeps an unexpected successful status unknown and retains its actual status", async () => {
    const storage = memoryStorage();
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ message: "已移出团队，恢复为个人剧集" }), { status: 202 }),
    );
    const port = createTeamSeriesHttpPort("http://127.0.0.1:8000/api", { storage, fetcher });
    await expect(port.unshareSeries("series", new AbortController().signal)).rejects.toMatchObject({
      kind: "invalid-response", status: 202,
    });
  });

  it("ignores a late response after the bearer token changes", async () => {
    const storage = memoryStorage();
    let resolveFetch!: (response: Response) => void;
    const fetcher = vi.fn<typeof fetch>(() => new Promise<Response>((resolve) => { resolveFetch = resolve; }));
    const port = createTeamSeriesHttpPort("http://127.0.0.1:8000/api", { storage, fetcher });
    const pending = port.unshareSeries("series", new AbortController().signal);
    storage.setItem(API_TOKEN_KEY, "token-two");
    resolveFetch(new Response(JSON.stringify({ detail: "expired" }), { status: 401 }));
    await expect(pending).rejects.toMatchObject({ kind: "aborted" });
  });
});
