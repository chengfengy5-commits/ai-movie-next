import { describe, expect, it } from "vitest";
import type { PersonalProductionFrameView } from "./personal-production/projection";
import {
  countProductionNoteStatuses,
  filterProductionNoteFrames,
  productionNoteStatusFilterOptions,
} from "./productionNoteStatusFilter";

function frame(position: number, status: PersonalProductionFrameView["status"]): PersonalProductionFrameView {
  return {
    position,
    status,
    note: `note-${position}`,
    hasSavedNote: true,
    verified: status !== "unverifiable" && status !== "unreadable",
  };
}

describe("production note status filtering", () => {
  it("defines all seven local options, including zero-count states", () => {
    const rows = [
      frame(2, "needs_reconfirmation"),
      frame(4, "needs_revision"),
      frame(5, "approved"),
      frame(7, "unmarked"),
      frame(9, "unverifiable"),
      frame(11, "unreadable"),
    ];

    expect(productionNoteStatusFilterOptions.map((option) => option.value)).toEqual([
      "all",
      "needs_revision",
      "needs_reconfirmation",
      "unmarked",
      "approved",
      "unverifiable",
      "unreadable",
    ]);
    expect(countProductionNoteStatuses(rows)).toEqual({
      all: 6,
      needs_revision: 1,
      needs_reconfirmation: 1,
      unmarked: 1,
      approved: 1,
      unverifiable: 1,
      unreadable: 1,
    });
  });

  it.each([
    ["needs_revision", 4],
    ["needs_reconfirmation", 2],
    ["unmarked", 7],
    ["approved", 5],
    ["unverifiable", 9],
    ["unreadable", 11],
  ] as const)("selects only the original %s rows and preserves zero counts", (status, position) => {
    const rows = [
      frame(2, "needs_reconfirmation"),
      frame(4, "needs_revision"),
      frame(5, "approved"),
      frame(7, "unmarked"),
      frame(9, "unverifiable"),
      frame(11, "unreadable"),
    ];
    const expected = rows.find((row) => row.position === position);
    const filtered = filterProductionNoteFrames(rows, status);

    expect(filtered).toHaveLength(1);
    expect(filtered[0]).toBe(expected);
    expect(filtered[0]?.status).toBe(status);
    expect(countProductionNoteStatuses(rows).all).toBe(6);
    expect(countProductionNoteStatuses(rows).approved).toBe(1);
  });

  it("returns an empty local result for a state with no matching rows", () => {
    const rows = [frame(1, "needs_revision"), frame(3, "unmarked")];

    expect(filterProductionNoteFrames(rows, "approved")).toEqual([]);
    expect(countProductionNoteStatuses(rows)).toEqual({
      all: 2,
      needs_revision: 1,
      needs_reconfirmation: 0,
      unmarked: 1,
      approved: 0,
      unverifiable: 0,
      unreadable: 0,
    });
  });

  it("preserves full-list order, original positions, and row object identities", () => {
    const needsReconfirmation = frame(2, "needs_reconfirmation");
    const needsRevision = frame(4, "needs_revision");
    const approved = frame(5, "approved");
    const rows = [needsReconfirmation, needsRevision, approved];

    const all = filterProductionNoteFrames(rows, "all");
    const filtered = filterProductionNoteFrames(rows, "needs_revision");

    expect(all).toBe(rows);
    expect(filtered).toEqual([needsRevision]);
    expect(filtered[0]).toBe(needsRevision);
    expect(filtered[0]?.position).toBe(4);
    expect(countProductionNoteStatuses(rows).needs_reconfirmation).toBe(1);
    expect(countProductionNoteStatuses(rows).approved).toBe(1);
  });
});
