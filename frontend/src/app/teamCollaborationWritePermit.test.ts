import { describe, expect, it } from "vitest";
import { createDemoServices } from "../shared/api/services";
import type { TeamSeriesServicePort } from "../shared/api/teamSeries";
import type { TeamCollaborationWriteScope } from "./teamCollaborationWritePermit";
import { TeamCollaborationWritePermit } from "./teamCollaborationWritePermit";

function createScope(services = createDemoServices()): TeamCollaborationWriteScope {
  return {
    actorId: "actor-a",
    sessionGeneration: 1,
    services,
    teamManagementPort: services.teamManagement,
    teamSeriesPort: services.teamSeries,
  };
}

describe("TeamCollaborationWritePermit", () => {
  it("grants one synchronous write lease across team and series writers", () => {
    const permit = new TeamCollaborationWritePermit();
    const scope = createScope();
    permit.synchronizeScope(scope);

    const share = permit.acquire(scope, { kind: "series-share", label: "分享剧集" });
    expect(share).not.toBeNull();
    expect(permit.getSnapshot().phase).toBe("in-flight");
    expect(permit.acquire(scope, { kind: "team-management", label: "创建团队" })).toBeNull();
    expect(permit.acquire(scope, { kind: "team-series", label: "认领剧集" })).toBeNull();
  });

  it("keeps an unknown write exclusive until the user explicitly abandons it", () => {
    const permit = new TeamCollaborationWritePermit();
    const scope = createScope();
    permit.synchronizeScope(scope);
    const lease = permit.acquire(scope, { kind: "series-share", label: "分享剧集" });
    expect(lease).not.toBeNull();
    expect(permit.markUnknown(lease!)).toBe(true);

    expect(permit.getSnapshot().phase).toBe("unknown");
    expect(permit.acquire(scope, { kind: "team-management", label: "生成邀请码" })).toBeNull();
    expect(permit.complete(lease!)).toBe(false);
    expect(permit.abandonUnknown(scope)).toBe(true);
    expect(permit.getSnapshot().phase).toBe("idle");
    expect(permit.getSnapshot().lastAbandonedLeaseId).toBe(lease!.id);
    expect(permit.acquire(scope, { kind: "team-management", label: "生成邀请码" })).not.toBeNull();
  });

  it("does not abandon a newer unknown lease for a stale dialog attempt", () => {
    const permit = new TeamCollaborationWritePermit();
    const scope = createScope();
    permit.synchronizeScope(scope);
    const oldLease = permit.acquire(scope, { kind: "series-share", label: "旧分享" });
    expect(oldLease).not.toBeNull();
    expect(permit.markUnknown(oldLease!)).toBe(true);
    expect(permit.abandonUnknown(scope, oldLease!.id)).toBe(true);

    const currentLease = permit.acquire(scope, { kind: "series-share", label: "新分享" });
    expect(currentLease).not.toBeNull();
    expect(permit.markUnknown(currentLease!)).toBe(true);

    expect(permit.abandonUnknown(scope, oldLease!.id)).toBe(false);
    expect(permit.getSnapshot().phase).toBe("unknown");
    expect(permit.getSnapshot().leaseId).toBe(currentLease!.id);
    expect(permit.abandonUnknown(scope, currentLease!.id)).toBe(true);
  });

  it("retires a lease when a port owner changes and rejects the old finally", () => {
    const permit = new TeamCollaborationWritePermit();
    const services = createDemoServices();
    const oldSeriesPort = {} as TeamSeriesServicePort;
    const oldScope = { ...createScope(services), teamSeriesPort: oldSeriesPort };
    permit.synchronizeScope(oldScope);
    const oldLease = permit.acquire(oldScope, { kind: "team-series", label: "认领剧集" });
    expect(oldLease).not.toBeNull();
    const newScope = { ...oldScope, teamSeriesPort: {} as TeamSeriesServicePort };
    permit.synchronizeScope(newScope);
    const newLease = permit.acquire(newScope, { kind: "team-management", label: "创建团队" });

    expect(newLease).not.toBeNull();
    expect(permit.complete(oldLease!)).toBe(false);
    expect(permit.isLeaseCurrent(newLease!)).toBe(true);
  });
});
