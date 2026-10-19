import type { WorkspaceServices } from "../shared/api/services";
import type { TeamManagementServicePort } from "../shared/api/teamManagement";
import type { TeamSeriesServicePort } from "../shared/api/teamSeries";

export interface TeamCollaborationWriteScope {
  actorId: string | null;
  sessionGeneration: number;
  services: WorkspaceServices;
  teamManagementPort: TeamManagementServicePort | undefined;
  teamSeriesPort: TeamSeriesServicePort | undefined;
}

export interface TeamCollaborationWriteOwner {
  kind: "team-management" | "team-series" | "series-share";
  label: string;
}

export interface TeamCollaborationWriteLease {
  readonly id: number;
  readonly scope: TeamCollaborationWriteScope;
  readonly owner: TeamCollaborationWriteOwner;
}

export interface TeamCollaborationWriteSnapshot {
  revision: number;
  scope: TeamCollaborationWriteScope | null;
  phase: "idle" | "in-flight" | "unknown";
  leaseId: number | null;
  owner: TeamCollaborationWriteOwner | null;
  lastAbandonedLeaseId: number | null;
}

interface ActiveLease {
  lease: TeamCollaborationWriteLease;
  phase: "in-flight" | "unknown";
}

export function sameTeamCollaborationWriteScope(
  left: TeamCollaborationWriteScope,
  right: TeamCollaborationWriteScope,
): boolean {
  return left.actorId === right.actorId
    && left.sessionGeneration === right.sessionGeneration
    && left.services === right.services
    && left.teamManagementPort === right.teamManagementPort
    && left.teamSeriesPort === right.teamSeriesPort;
}

export function createTeamCollaborationWriteScope(
  actorId: string | null,
  sessionGeneration: number,
  services: WorkspaceServices,
): TeamCollaborationWriteScope {
  return {
    actorId,
    sessionGeneration,
    services,
    teamManagementPort: services.teamManagement,
    teamSeriesPort: services.teamSeries,
  };
}

export class TeamCollaborationWritePermit {
  private currentScope: TeamCollaborationWriteScope | null = null;
  private activeLease: ActiveLease | null = null;
  private nextLeaseId = 0;
  private snapshot: TeamCollaborationWriteSnapshot = {
    revision: 0,
    scope: null,
    phase: "idle",
    leaseId: null,
    owner: null,
    lastAbandonedLeaseId: null,
  };
  private readonly listeners = new Set<() => void>();

  readonly getSnapshot = (): TeamCollaborationWriteSnapshot => this.snapshot;

  readonly subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  synchronizeScope(scope: TeamCollaborationWriteScope): void {
    if (this.currentScope !== null && sameTeamCollaborationWriteScope(this.currentScope, scope)) return;
    this.currentScope = scope;
    this.activeLease = null;
    this.publish({
      scope,
      phase: "idle",
      leaseId: null,
      owner: null,
      lastAbandonedLeaseId: null,
    });
  }

  acquire(
    scope: TeamCollaborationWriteScope,
    owner: TeamCollaborationWriteOwner,
  ): TeamCollaborationWriteLease | null {
    if (this.currentScope === null || !sameTeamCollaborationWriteScope(this.currentScope, scope) || this.activeLease !== null) return null;
    const lease: TeamCollaborationWriteLease = {
      id: ++this.nextLeaseId,
      scope: this.currentScope,
      owner,
    };
    this.activeLease = { lease, phase: "in-flight" };
    this.publish({
      scope: this.currentScope,
      phase: "in-flight",
      leaseId: lease.id,
      owner,
      lastAbandonedLeaseId: this.snapshot.lastAbandonedLeaseId,
    });
    return lease;
  }

  isScopeCurrent(scope: TeamCollaborationWriteScope): boolean {
    return this.currentScope !== null && sameTeamCollaborationWriteScope(this.currentScope, scope);
  }

  isLeaseCurrent(lease: TeamCollaborationWriteLease): boolean {
    return this.isScopeCurrent(lease.scope) && this.activeLease?.lease.id === lease.id;
  }

  complete(lease: TeamCollaborationWriteLease): boolean {
    if (!this.isLeaseCurrent(lease) || this.activeLease?.phase !== "in-flight") return false;
    this.activeLease = null;
    this.publish({
      scope: this.currentScope,
      phase: "idle",
      leaseId: null,
      owner: null,
      lastAbandonedLeaseId: this.snapshot.lastAbandonedLeaseId,
    });
    return true;
  }

  markUnknown(lease: TeamCollaborationWriteLease): boolean {
    if (!this.isLeaseCurrent(lease) || this.activeLease?.phase !== "in-flight") return false;
    this.activeLease = { lease, phase: "unknown" };
    this.publish({
      scope: this.currentScope,
      phase: "unknown",
      leaseId: lease.id,
      owner: lease.owner,
      lastAbandonedLeaseId: this.snapshot.lastAbandonedLeaseId,
    });
    return true;
  }

  abandonUnknown(scope: TeamCollaborationWriteScope, expectedLeaseId?: number): boolean {
    if (!this.isScopeCurrent(scope)
      || this.activeLease?.phase !== "unknown"
      || (expectedLeaseId !== undefined && this.activeLease.lease.id !== expectedLeaseId)) return false;
    const abandonedLeaseId = this.activeLease.lease.id;
    this.activeLease = null;
    this.publish({
      scope: this.currentScope,
      phase: "idle",
      leaseId: null,
      owner: null,
      lastAbandonedLeaseId: abandonedLeaseId,
    });
    return true;
  }

  hasUnresolvedWrite(): boolean {
    return this.activeLease !== null;
  }

  private publish(next: Omit<TeamCollaborationWriteSnapshot, "revision">): void {
    this.snapshot = { ...next, revision: this.snapshot.revision + 1 };
    for (const listener of this.listeners) listener();
  }
}
