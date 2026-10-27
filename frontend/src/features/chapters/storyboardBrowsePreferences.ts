export const storyboardPageSizes = [10, 20, 50, 100] as const;
export type StoryboardPageSize = (typeof storyboardPageSizes)[number];

export const defaultStoryboardPageSize: StoryboardPageSize = 20;

export interface StoryboardBrowsePreferenceScope {
  userId: string;
  apiBaseUrl: string;
  seriesId: string;
  chapterId: string;
}

export interface StoryboardBrowsePreferences {
  pageSize: StoryboardPageSize;
  page: number;
  scrollTop: number;
}

export interface StoryboardBrowseStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export type StoryboardBrowseReadResult =
  | { available: true; preferences: StoryboardBrowsePreferences }
  | { available: false; preferences: StoryboardBrowsePreferences };

export function storyboardPageSizeStorageKey(scope: Pick<StoryboardBrowsePreferenceScope, "userId" | "apiBaseUrl">): string {
  return ["haoai", "storyboard-browse", "v1", scope.userId, scope.apiBaseUrl, "page-size"]
    .map(encodeURIComponent)
    .join(":");
}

export function storyboardChapterStorageKey(scope: StoryboardBrowsePreferenceScope): string {
  return [
    "haoai",
    "storyboard-browse",
    "v1",
    scope.userId,
    scope.apiBaseUrl,
    scope.seriesId,
    scope.chapterId,
    "chapter",
  ].map(encodeURIComponent).join(":");
}

export function clampStoryboardPage(page: number, pageCount: number): number {
  const safePageCount = Number.isSafeInteger(pageCount) && pageCount > 0 ? pageCount : 1;
  const safePage = Number.isSafeInteger(page) && page > 0 ? page : 1;
  return Math.min(safePage, safePageCount);
}

export function storyboardPageCount(frameCount: number, pageSize: StoryboardPageSize): number {
  if (!Number.isSafeInteger(frameCount) || frameCount <= 0) return 1;
  return Math.ceil(frameCount / pageSize);
}

export function storyboardPageForPosition(position: number, pageSize: StoryboardPageSize, pageCount: number): number | null {
  if (!Number.isSafeInteger(position) || position <= 0) return null;
  return clampStoryboardPage(Math.ceil(position / pageSize), pageCount);
}

function parsePageSize(value: string | null): StoryboardPageSize {
  if (value === null) return defaultStoryboardPageSize;
  const parsed = Number(value);
  return storyboardPageSizes.includes(parsed as StoryboardPageSize)
    ? parsed as StoryboardPageSize
    : defaultStoryboardPageSize;
}

function parseChapterPreferences(value: string | null): Pick<StoryboardBrowsePreferences, "page" | "scrollTop"> {
  if (value === null) return { page: 1, scrollTop: 0 };
  try {
    const parsed: unknown = JSON.parse(value);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      return { page: 1, scrollTop: 0 };
    }
    const record = parsed as Record<string, unknown>;
    const page = typeof record.page === "number" && Number.isSafeInteger(record.page) && record.page > 0
      ? record.page
      : 1;
    const scrollTop = typeof record.scrollTop === "number" && Number.isFinite(record.scrollTop) && record.scrollTop >= 0
      ? Math.min(Math.floor(record.scrollTop), 10_000_000)
      : 0;
    return { page, scrollTop };
  } catch {
    return { page: 1, scrollTop: 0 };
  }
}

function defaultPreferences(): StoryboardBrowsePreferences {
  return { pageSize: defaultStoryboardPageSize, page: 1, scrollTop: 0 };
}

function browserStorage(): StoryboardBrowseStorage {
  return window.localStorage;
}

export function readStoryboardBrowsePreferences(
  scope: StoryboardBrowsePreferenceScope,
  getStorage: () => StoryboardBrowseStorage = browserStorage,
): StoryboardBrowseReadResult {
  try {
    const storage = getStorage();
    const pageSize = parsePageSize(storage.getItem(storyboardPageSizeStorageKey(scope)));
    const chapterPreferences = parseChapterPreferences(storage.getItem(storyboardChapterStorageKey(scope)));
    return { available: true, preferences: { pageSize, ...chapterPreferences } };
  } catch {
    return { available: false, preferences: defaultPreferences() };
  }
}

export function writeStoryboardBrowsePreferences(
  scope: StoryboardBrowsePreferenceScope,
  preferences: StoryboardBrowsePreferences,
  getStorage: () => StoryboardBrowseStorage = browserStorage,
): boolean {
  try {
    const storage = getStorage();
    storage.setItem(storyboardPageSizeStorageKey(scope), String(preferences.pageSize));
    storage.setItem(storyboardChapterStorageKey(scope), JSON.stringify({
      page: preferences.page,
      scrollTop: preferences.scrollTop,
    }));
    return true;
  } catch {
    return false;
  }
}
