export interface FrameOptimizeConcurrencyPreferenceScope {
  readonly apiBaseUrl: string | null;
  readonly userId: string;
}

type PreferenceStorage = Pick<Storage, "getItem" | "setItem">;

const defaultConcurrency = 5;
const minimumConcurrency = 3;
const maximumConcurrency = 10;
const storageKeyPrefix = "haoai:frame-optimize-concurrency:v1";

export function isFrameOptimizeConcurrency(value: unknown): value is number {
  return Number.isSafeInteger(value)
    && (value as number) >= minimumConcurrency
    && (value as number) <= maximumConcurrency;
}

export function frameOptimizeConcurrencyPreferenceKey(
  scope: FrameOptimizeConcurrencyPreferenceScope,
): string {
  return `${storageKeyPrefix}:${encodeURIComponent(scope.apiBaseUrl ?? "")}:${encodeURIComponent(scope.userId)}`;
}

function restoredConcurrency(value: unknown): number {
  if (typeof value !== "number" && typeof value !== "string") return defaultConcurrency;
  const parsed = Number.parseInt(String(value), 10);
  if (!Number.isFinite(parsed)) return defaultConcurrency;
  return Math.max(minimumConcurrency, Math.min(parsed, maximumConcurrency));
}

export function readFrameOptimizeConcurrencyPreference(
  scope: FrameOptimizeConcurrencyPreferenceScope,
  storage?: PreferenceStorage,
): number {
  try {
    const source = storage ?? globalThis.localStorage;
    const stored = source.getItem(frameOptimizeConcurrencyPreferenceKey(scope));
    if (stored === null) return defaultConcurrency;
    return restoredConcurrency(JSON.parse(stored) as unknown);
  } catch {
    return defaultConcurrency;
  }
}

export function writeFrameOptimizeConcurrencyPreference(
  scope: FrameOptimizeConcurrencyPreferenceScope,
  value: number,
  storage?: PreferenceStorage,
): boolean {
  if (!isFrameOptimizeConcurrency(value)) return false;
  try {
    const destination = storage ?? globalThis.localStorage;
    destination.setItem(frameOptimizeConcurrencyPreferenceKey(scope), JSON.stringify(value));
    return true;
  } catch {
    return false;
  }
}
