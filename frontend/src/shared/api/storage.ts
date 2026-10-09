export interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export const MOCK_SESSION_KEY = "haoai_next_mock_session";
export const MOCK_USER_KEY = "haoai_next_mock_user";
export const API_TOKEN_KEY = "muse_auth_token";
export const API_USER_KEY = "muse_auth_user";

export function browserStorage(): StorageLike {
  return window.localStorage;
}

export function clearMockSession(storage: StorageLike): void {
  storage.removeItem(MOCK_SESSION_KEY);
  storage.removeItem(MOCK_USER_KEY);
}

export function clearApiSession(storage: StorageLike): void {
  storage.removeItem(API_TOKEN_KEY);
  storage.removeItem(API_USER_KEY);
}
