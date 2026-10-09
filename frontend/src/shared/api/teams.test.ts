import { describe, expect, it } from "vitest";
import { InvalidResponseError } from "./contracts";
import { parseMyTeamList, type MyTeam } from "./teams";

const baseTeam: MyTeam = {
  id: "team-1",
  name: "工作室",
  owner_id: "owner-1",
  created_at: "2026-10-05T12:00:00",
  member_count: 2,
  my_role: "writer",
};

describe("my teams response contract", () => {
  it("preserves response order, empty names, unknown roles, and arbitrary unique IDs", () => {
    const teams = parseMyTeamList([
      { ...baseTeam, id: "all", name: "同名" },
      { ...baseTeam, id: "__proto__", name: "", my_role: "" },
      { ...baseTeam, id: "custom/id", name: "同名", my_role: "future-role" },
    ]);

    expect(teams.map(({ id }) => id)).toEqual(["all", "__proto__", "custom/id"]);
    expect(teams.map(({ name }) => name)).toEqual(["同名", "", "同名"]);
    expect(teams[2]?.my_role).toBe("future-role");
  });

  it("accepts an empty directory and valid offset timestamps", () => {
    expect(parseMyTeamList([])).toEqual([]);
    expect(parseMyTeamList([{ ...baseTeam, created_at: "2026-10-05T12:00:00+08:00" }])[0]?.created_at)
      .toBe("2026-10-05T12:00:00+08:00");
  });

  const malformedResponses: Array<[unknown, string]> = [
    [null, "outer shape"],
    [[{ ...baseTeam, id: "" }], "empty ID"],
    [[{ ...baseTeam, owner_id: "" }], "empty owner"],
    [[{ ...baseTeam, name: null }], "nullable name"],
    [[{ ...baseTeam, my_role: 3 }], "non-string role"],
    [[{ ...baseTeam, member_count: -1 }], "negative member count"],
    [[{ ...baseTeam, member_count: Number.MAX_SAFE_INTEGER + 1 }], "unsafe member count"],
    [[{ ...baseTeam, created_at: "2026-02-30T10:00:00" }], "invalid calendar date"],
    [[{ ...baseTeam, member_count: undefined }], "undefined member count"],
  ];

  it.each(malformedResponses)("rejects malformed team data (%s)", (value) => {
    expect(() => parseMyTeamList(value)).toThrow(InvalidResponseError);
  });

  it("requires own fields and rejects duplicate IDs rather than silently merging", () => {
    const inherited = Object.create({ ...baseTeam }) as Record<string, unknown>;
    expect(() => parseMyTeamList([inherited])).toThrow(InvalidResponseError);
    expect(() => parseMyTeamList([baseTeam, { ...baseTeam }])).toThrow(/重复/);
  });

  it.each(["id", "name", "owner_id", "created_at", "member_count", "my_role"] as const)(
    "rejects a required %s value inherited from the prototype",
    (field) => {
      const inherited = Object.create({ [field]: baseTeam[field] }) as Record<string, unknown>;
      for (const [key, value] of Object.entries(baseTeam)) {
        if (key !== field) {
          inherited[key] = value;
        }
      }
      expect(() => parseMyTeamList([inherited])).toThrow(InvalidResponseError);
    },
  );
});
