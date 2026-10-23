export type FrameOptimizeChapterAttempt =
  | { kind: "submit-preflight" }
  | { kind: "submit-unknown" }
  | { kind: "task"; taskId: string }
  | { kind: "restore" }
  | { kind: "restore-unknown" };

export interface FrameOptimizeChapterLease {
  key: string;
  token: number;
}

interface OwnedAttempt {
  token: number;
  attempt: FrameOptimizeChapterAttempt;
}

const attempts = new Map<string, OwnedAttempt>();
let nextToken = 1;

function chapterKey(userId: string, seriesId: string, chapterId: string): string {
  return JSON.stringify([userId, seriesId, chapterId]);
}

function beginChapterOperation(
  userId: string,
  seriesId: string,
  chapterId: string,
  attempt: FrameOptimizeChapterAttempt,
): FrameOptimizeChapterLease | null {
  const key = chapterKey(userId, seriesId, chapterId);
  if (attempts.has(key)) return null;
  const lease = { key, token: nextToken++ };
  attempts.set(key, { token: lease.token, attempt });
  return lease;
}

function updateOwnedAttempt(
  lease: FrameOptimizeChapterLease,
  isExpected: (attempt: FrameOptimizeChapterAttempt) => boolean,
  attempt: FrameOptimizeChapterAttempt,
): boolean {
  const current = attempts.get(lease.key);
  if (current?.token !== lease.token || !isExpected(current.attempt)) return false;
  attempts.set(lease.key, { token: lease.token, attempt });
  return true;
}

function clearOwnedAttempt(
  lease: FrameOptimizeChapterLease,
  isExpected: (attempt: FrameOptimizeChapterAttempt) => boolean,
): boolean {
  const current = attempts.get(lease.key);
  if (current?.token !== lease.token || !isExpected(current.attempt)) return false;
  attempts.delete(lease.key);
  return true;
}

export function frameOptimizeChapterAttempt(
  userId: string,
  seriesId: string,
  chapterId: string,
): FrameOptimizeChapterAttempt | null {
  return attempts.get(chapterKey(userId, seriesId, chapterId))?.attempt ?? null;
}

export function beginFrameOptimizeSubmit(
  userId: string,
  seriesId: string,
  chapterId: string,
): FrameOptimizeChapterLease | null {
  return beginChapterOperation(userId, seriesId, chapterId, { kind: "submit-preflight" });
}

export function markFrameOptimizeSubmitSent(lease: FrameOptimizeChapterLease): boolean {
  return updateOwnedAttempt(lease, (attempt) => attempt.kind === "submit-preflight", { kind: "submit-unknown" });
}

export function recordFrameOptimizeTaskForLease(lease: FrameOptimizeChapterLease, taskId: string): boolean {
  return updateOwnedAttempt(
    lease,
    (attempt) => attempt.kind === "submit-unknown",
    { kind: "task", taskId },
  );
}

export function recordFrameOptimizeTask(
  userId: string,
  seriesId: string,
  chapterId: string,
  taskId: string,
): void {
  const lease = beginChapterOperation(userId, seriesId, chapterId, { kind: "task", taskId });
  if (lease !== null) return;
  const key = chapterKey(userId, seriesId, chapterId);
  const current = attempts.get(key);
  if (current?.attempt.kind === "submit-unknown" || current?.attempt.kind === "task") {
    attempts.set(key, { token: current.token, attempt: { kind: "task", taskId } });
  }
}

export function clearFrameOptimizeTask(userId: string, seriesId: string, chapterId: string, taskId: string): void {
  const key = chapterKey(userId, seriesId, chapterId);
  const current = attempts.get(key);
  if (current?.attempt.kind === "task" && current.attempt.taskId === taskId) attempts.delete(key);
}

export function clearFrameOptimizeSubmitPreflight(lease: FrameOptimizeChapterLease): boolean {
  return clearOwnedAttempt(lease, (attempt) => attempt.kind === "submit-preflight");
}

export function clearRejectedFrameOptimizeSubmit(lease: FrameOptimizeChapterLease): boolean {
  return clearOwnedAttempt(lease, (attempt) => attempt.kind === "submit-unknown");
}

export function beginFrameOptimizeRestore(
  userId: string,
  seriesId: string,
  chapterId: string,
): FrameOptimizeChapterLease | null {
  return beginChapterOperation(userId, seriesId, chapterId, { kind: "restore" });
}

export function finishFrameOptimizeRestore(lease: FrameOptimizeChapterLease): boolean {
  return clearOwnedAttempt(lease, (attempt) => attempt.kind === "restore");
}

export function freezeUnknownFrameOptimizeRestore(lease: FrameOptimizeChapterLease): boolean {
  return updateOwnedAttempt(lease, (attempt) => attempt.kind === "restore", { kind: "restore-unknown" });
}

export function frameOptimizeRestoreIsBusy(userId: string, seriesId: string, chapterId: string): boolean {
  return attempts.has(chapterKey(userId, seriesId, chapterId));
}
