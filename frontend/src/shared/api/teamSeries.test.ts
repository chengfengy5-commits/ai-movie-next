import { describe, expect, it } from "vitest";
import {
  parseCreatedTeamSeries,
  parseTeamSeriesPage,
  parseTeamSeriesShareReceipt,
  parseTeamSeriesTransferReceipt,
  parseTeamSeriesUnclaimReceipt,
  type TeamSeriesRecord,
} from "./teamSeries";

const now = "2026-10-19T12:00:00";

const row: TeamSeriesRecord = {
  id: "series/opaque",
  name: "",
  description: "",
  image_url: null,
  user_id: "creator-id",
  owner_name: "",
  owner_avatar_url: null,
  claimed_by: "former-member",
  claimed_by_username: "",
  claimed_by_avatar_url: null,
  created_at: now,
  updated_at: now,
  chapter_count: 0,
  asset_counts: { characters: 0, scenes: 1, props: 0, storyboard: 0 },
};

describe("team series wire projections", () => {
  it("accepts an empty final page, preserves zero and empty strings, and never invents can_enter", () => {
    const parsed = parseTeamSeriesPage({
      items: [row], total: 1, page: 1, page_size: 50, total_pages: 1,
    }, 0, 80);

    expect(parsed).toEqual({ items: [row], total: 1, page: 1, page_size: 50, total_pages: 1 });
    expect("can_enter" in parsed.items[0]!).toBe(false);
    expect(parseTeamSeriesPage({ items: [], total: 0, page: 3, page_size: 10, total_pages: 1 }, 3, 10).items).toEqual([]);
  });

  it("binds create ACK to actor, team, source strip result, and nullable style without requiring claim echo", () => {
    const input = { name: "\u001c剧集\u001c", description: "", image_url: "", style_prompt_id: null, claim: false };
    const ack = { id: "new-id", name: "剧集", team_id: "team/one", user_id: "actor", style_prompt_id: null };
    expect(parseCreatedTeamSeries(ack, "team/one", "actor", input)).toEqual(ack);
    expect(() => parseCreatedTeamSeries({ ...ack, user_id: "other" }, "team/one", "actor", input)).toThrow();
  });

  it("accepts empty team names only with the matching share message and both unclaim receipts", () => {
    expect(parseTeamSeriesShareReceipt({
      message: "已分享到团队「」", team_id: "team", team_name: "",
    }, "team").team_name).toBe("");
    expect(() => parseTeamSeriesShareReceipt({ message: "已分享到团队「别的」", team_id: "team", team_name: "" }, "team")).toThrow();
    expect(parseTeamSeriesUnclaimReceipt({ message: "已取消认领" }).message).toBe("已取消认领");
    expect(parseTeamSeriesUnclaimReceipt({ message: "该剧集尚未被认领" }).message).toBe("该剧集尚未被认领");
  });

  it("requires transfer ACK to name the frozen recipient", () => {
    expect(parseTeamSeriesTransferReceipt({ message: "已转交认领", claimed_by: "target" }, "target").claimed_by).toBe("target");
    expect(() => parseTeamSeriesTransferReceipt({ message: "已转交认领", claimed_by: "other" }, "target")).toThrow();
  });
});
