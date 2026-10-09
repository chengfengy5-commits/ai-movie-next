export interface User {
  id: string;
  username: string;
  email: string;
  is_superuser: boolean;
  membership_type: string;
  membership_expires_at: string | null;
  avatar_url: string | null;
  bio: string | null;
  created_at: string;
}

export interface Credentials {
  username: string;
  password: string;
}

export interface Series {
  id: string;
  user_id: string;
  name: string;
  description: string | null;
  image_url: string | null;
  style_prompt_id: string | null;
  style_prompt: string | null;
  style_prompt_name: string | null;
  style_prompt_owner_name: string | null;
  team_id: string | null;
  team_name: string | null;
  owner_name: string | null;
  claimed_by: string | null;
  claimed_by_username: string | null;
  claimed_by_avatar_url: string | null;
  can_enter: boolean;
  created_at: string;
  updated_at: string;
}

export type SeriesFilter = "all" | "mine" | "team" | "claimed";

export interface StoryboardFrame {
  readonly [key: string]: unknown;
}

export interface Chapter {
  id: string;
  series_id: string;
  title: string;
  content: StoryboardFrame[] | null;
  order: number;
  created_at: string;
  updated_at: string;
  lock: Record<string, unknown> | null;
}

export interface StoryboardAsset {
  id: string;
  series_id: string;
  chapter_id: string;
  frame_index: number;
  name: string;
  description: string | null;
  image_url: string | null;
  created_at: string;
  updated_at: string;
}

export interface AssetNamingFields {
  aliases: string[] | null;
  canonical_key: string | null;
}

export interface SeriesAssetFields {
  id: string;
  series_id: string;
  description: string | null;
  image_url: string | null;
  created_at: string;
  updated_at: string;
}

export interface Character extends AssetNamingFields, SeriesAssetFields {
  name: string;
  gender: string | null;
  age: string | null;
  role: string | null;
  appearance: string | null;
  audio_url: string | null;
  voice_ref: string | null;
}

export interface Scene extends AssetNamingFields, SeriesAssetFields {
  title: string;
}

export interface Prop extends AssetNamingFields, SeriesAssetFields {
  name: string;
}

export type AssetLibraryType = "characters" | "scenes" | "props";

export class InvalidResponseError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "InvalidResponseError";
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requiredString(
  record: Record<string, unknown>,
  key: string,
  label: string,
): string {
  const value = record[key];
  if (typeof value !== "string" || value.length === 0) {
    throw new InvalidResponseError(`${label}缺少有效的 ${key} 字段。`);
  }
  return value;
}

function schemaString(
  record: Record<string, unknown>,
  key: string,
  label: string,
): string {
  const value = record[key];
  if (typeof value !== "string") {
    throw new InvalidResponseError(`${label}中的 ${key} 字段格式无效。`);
  }
  return value;
}

function nullableString(record: Record<string, unknown>, key: string, label: string): string | null {
  const value = record[key];
  if (value === undefined || value === null || value === "") {
    return null;
  }
  if (typeof value !== "string") {
    throw new InvalidResponseError(`${label}中的 ${key} 字段格式无效。`);
  }
  return value;
}

function requiredTimestamp(
  record: Record<string, unknown>,
  key: string,
  label: string,
): string {
  const value = requiredString(record, key, label);
  if (!isValidIsoTimestamp(value)) {
    throw new InvalidResponseError(label + "中的 " + key + " 日期格式无效。");
  }
  if (Number.isNaN(Date.parse(value))) {
    throw new InvalidResponseError(`${label}中的 ${key} 日期格式无效。`);
  }
  return value;
}

function isValidIsoTimestamp(value: string): boolean {
  const match = value.match(
    /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,9}))?)?)?(?:([zZ])|([+-])(\d{2}):?(\d{2}))?$/,
  );
  if (match === null) {
    return false;
  }

  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const hasTime = match[4] !== undefined;
  if (!hasTime) {
    return false;
  }

  const calendar = new Date(0);
  calendar.setUTCFullYear(year, month - 1, day);
  calendar.setUTCHours(0, 0, 0, 0);
  if (
    calendar.getUTCFullYear() !== year
    || calendar.getUTCMonth() !== month - 1
    || calendar.getUTCDate() !== day
  ) {
    return false;
  }

  if (hasTime) {
    const hour = Number(match[4]);
    const minute = Number(match[5]);
    const second = match[6] === undefined ? 0 : Number(match[6]);
    if (hour > 23 || minute > 59 || second > 59) {
      return false;
    }
  }
  if (match[9] !== undefined) {
    const offsetHour = Number(match[10]);
    const offsetMinute = Number(match[11]);
    if (offsetHour > 23 || offsetMinute > 59) {
      return false;
    }
  }

  const normalized = /(?:[zZ]|[+-]\d{2}:?\d{2})$/.test(value) ? value : value + "Z";
  return !Number.isNaN(Date.parse(normalized));
}

export function parseUser(value: unknown): User {
  if (!isRecord(value)) {
    throw new InvalidResponseError("服务器返回的用户信息格式无效。");
  }

  const createdAt = requiredTimestamp(value, "created_at", "用户信息");
  const isSuperuser = value.is_superuser;
  if (isSuperuser !== undefined && typeof isSuperuser !== "boolean") {
    throw new InvalidResponseError("用户信息中的 is_superuser 字段格式无效。");
  }
  const membershipType = value.membership_type;
  if (membershipType !== undefined && typeof membershipType !== "string") {
    throw new InvalidResponseError("用户信息中的 membership_type 字段格式无效。");
  }

  return {
    id: requiredString(value, "id", "用户信息"),
    username: requiredString(value, "username", "用户信息"),
    email: requiredString(value, "email", "用户信息"),
    is_superuser: isSuperuser ?? false,
    membership_type: membershipType ?? "free",
    membership_expires_at: nullableString(value, "membership_expires_at", "用户信息"),
    avatar_url: nullableString(value, "avatar_url", "用户信息"),
    bio: nullableString(value, "bio", "用户信息"),
    created_at: createdAt,
  };
}

export function parseSeries(value: unknown): Series {
  if (!isRecord(value)) {
    throw new InvalidResponseError("剧集列表中包含格式无效的项目。");
  }

  const canEnter = value.can_enter;
  if (canEnter !== undefined && typeof canEnter !== "boolean") {
    throw new InvalidResponseError("剧集列表中的 can_enter 字段格式无效。");
  }

  return {
    id: requiredString(value, "id", "剧集"),
    user_id: requiredString(value, "user_id", "剧集"),
    name: requiredString(value, "name", "剧集"),
    description: nullableString(value, "description", "剧集"),
    image_url: nullableString(value, "image_url", "剧集"),
    style_prompt_id: nullableString(value, "style_prompt_id", "剧集"),
    style_prompt: nullableString(value, "style_prompt", "剧集"),
    style_prompt_name: nullableString(value, "style_prompt_name", "剧集"),
    style_prompt_owner_name: nullableString(value, "style_prompt_owner_name", "剧集"),
    team_id: nullableString(value, "team_id", "剧集"),
    team_name: nullableString(value, "team_name", "剧集"),
    owner_name: nullableString(value, "owner_name", "剧集"),
    claimed_by: nullableString(value, "claimed_by", "剧集"),
    claimed_by_username: nullableString(value, "claimed_by_username", "剧集"),
    claimed_by_avatar_url: nullableString(value, "claimed_by_avatar_url", "剧集"),
    can_enter: canEnter ?? true,
    created_at: requiredTimestamp(value, "created_at", "剧集"),
    updated_at: requiredTimestamp(value, "updated_at", "剧集"),
  };
}

export function parseSeriesList(value: unknown): Series[] {
  if (!Array.isArray(value)) {
    throw new InvalidResponseError("服务器返回的剧集列表格式无效。");
  }
  return value.map(parseSeries);
}

function requiredInteger(
  record: Record<string, unknown>,
  key: string,
  label: string,
): number {
  const value = record[key];
  if (typeof value !== "number" || !Number.isInteger(value)) {
    throw new InvalidResponseError(`${label}中的 ${key} 字段格式无效。`);
  }
  return value;
}

function schemaNullableString(
  record: Record<string, unknown>,
  key: string,
  label: string,
): string | null {
  const value = record[key];
  if (value === undefined || value === null) {
    return null;
  }
  if (typeof value !== "string") {
    throw new InvalidResponseError(`${label}中的 ${key} 字段格式无效。`);
  }
  return value;
}

function parseChapter(value: unknown): Chapter {
  if (!isRecord(value)) {
    throw new InvalidResponseError("章节列表中包含格式无效的项目。");
  }

  const rawContent = value.content;
  let content: StoryboardFrame[] | null;
  if (rawContent === null) {
    content = null;
  } else if (Array.isArray(rawContent)) {
    content = rawContent.map((frame) => {
      if (!isRecord(frame)) {
        throw new InvalidResponseError("章节中的分镜内容格式无效。");
      }
      return frame;
    });
  } else {
    throw new InvalidResponseError("章节中的 content 字段格式无效。");
  }

  const rawLock = value.lock;
  if (rawLock !== undefined && rawLock !== null && !isRecord(rawLock)) {
    throw new InvalidResponseError("章节中的 lock 字段格式无效。");
  }
  const title = value.title;
  if (typeof title !== "string") {
    throw new InvalidResponseError("章节中的 title 字段格式无效。");
  }

  return {
    id: requiredString(value, "id", "章节"),
    series_id: requiredString(value, "series_id", "章节"),
    title,
    content,
    order: requiredInteger(value, "order", "章节"),
    created_at: requiredTimestamp(value, "created_at", "章节"),
    updated_at: requiredTimestamp(value, "updated_at", "章节"),
    lock: rawLock === undefined || rawLock === null ? null : rawLock,
  };
}

export function parseChapterList(value: unknown, expectedSeriesId: string): Chapter[] {
  if (!Array.isArray(value)) {
    throw new InvalidResponseError("服务器返回的章节列表格式无效。");
  }
  const chapters = value.map(parseChapter);
  const ids = new Set<string>();
  for (const chapter of chapters) {
    if (chapter.series_id !== expectedSeriesId) {
      throw new InvalidResponseError("服务器返回了不属于当前剧集的章节。");
    }
    if (ids.has(chapter.id)) {
      throw new InvalidResponseError("服务器返回了重复的章节身份。");
    }
    ids.add(chapter.id);
  }
  return chapters;
}

function parseStoryboardAsset(value: unknown): StoryboardAsset {
  if (!isRecord(value)) {
    throw new InvalidResponseError("原图列表中包含格式无效的项目。");
  }
  const name = value.name;
  if (typeof name !== "string") {
    throw new InvalidResponseError("原图信息中的 name 字段格式无效。");
  }

  return {
    id: requiredString(value, "id", "原图信息"),
    series_id: requiredString(value, "series_id", "原图信息"),
    chapter_id: requiredString(value, "chapter_id", "原图信息"),
    frame_index: requiredInteger(value, "frame_index", "原图信息"),
    name,
    description: schemaNullableString(value, "description", "原图信息"),
    image_url: schemaNullableString(value, "image_url", "原图信息"),
    created_at: requiredTimestamp(value, "created_at", "原图信息"),
    updated_at: requiredTimestamp(value, "updated_at", "原图信息"),
  };
}

export function parseStoryboardAssetList(value: unknown): StoryboardAsset[] {
  if (!Array.isArray(value)) {
    throw new InvalidResponseError("服务器返回的原图列表格式无效。");
  }
  return value.map(parseStoryboardAsset);
}

function requiredNullableString(
  record: Record<string, unknown>,
  key: string,
  label: string,
): string | null {
  if (!Object.prototype.hasOwnProperty.call(record, key)) {
    throw new InvalidResponseError(`${label}缺少必需的 ${key} 字段。`);
  }
  const value = record[key];
  if (value !== null && typeof value !== "string") {
    throw new InvalidResponseError(`${label}中的 ${key} 字段格式无效。`);
  }
  return value;
}

function optionalAliases(record: Record<string, unknown>, label: string): string[] | null {
  if (!Object.prototype.hasOwnProperty.call(record, "aliases") || record.aliases === null) {
    return null;
  }
  if (!Array.isArray(record.aliases) || record.aliases.some((alias) => typeof alias !== "string")) {
    throw new InvalidResponseError(`${label}中的 aliases 字段格式无效。`);
  }
  return [...record.aliases];
}

function optionalCanonicalKey(record: Record<string, unknown>, label: string): string | null {
  const value = record.canonical_key;
  if (value === undefined || value === null) {
    return null;
  }
  if (typeof value !== "string") {
    throw new InvalidResponseError(`${label}中的 canonical_key 字段格式无效。`);
  }
  return value;
}

function parseSeriesAssetFields(
  record: Record<string, unknown>,
  label: string,
): SeriesAssetFields {
  return {
    id: requiredString(record, "id", label),
    series_id: requiredString(record, "series_id", label),
    description: requiredNullableString(record, "description", label),
    image_url: requiredNullableString(record, "image_url", label),
    created_at: requiredTimestamp(record, "created_at", label),
    updated_at: requiredTimestamp(record, "updated_at", label),
  };
}

function parseAssetNamingFields(
  record: Record<string, unknown>,
  label: string,
): AssetNamingFields {
  return {
    aliases: optionalAliases(record, label),
    canonical_key: optionalCanonicalKey(record, label),
  };
}

function parseCharacter(value: unknown): Character {
  if (!isRecord(value)) {
    throw new InvalidResponseError("角色列表中包含格式无效的项目。");
  }
  return {
    ...parseSeriesAssetFields(value, "角色"),
    ...parseAssetNamingFields(value, "角色"),
    name: schemaString(value, "name", "角色"),
    gender: requiredNullableString(value, "gender", "角色"),
    age: requiredNullableString(value, "age", "角色"),
    role: requiredNullableString(value, "role", "角色"),
    appearance: requiredNullableString(value, "appearance", "角色"),
    audio_url: requiredNullableString(value, "audio_url", "角色"),
    voice_ref: requiredNullableString(value, "voice_ref", "角色"),
  };
}

function parseScene(value: unknown): Scene {
  if (!isRecord(value)) {
    throw new InvalidResponseError("场景列表中包含格式无效的项目。");
  }
  return {
    ...parseSeriesAssetFields(value, "场景"),
    ...parseAssetNamingFields(value, "场景"),
    title: schemaString(value, "title", "场景"),
  };
}

function parseProp(value: unknown): Prop {
  if (!isRecord(value)) {
    throw new InvalidResponseError("道具列表中包含格式无效的项目。");
  }
  return {
    ...parseSeriesAssetFields(value, "道具"),
    ...parseAssetNamingFields(value, "道具"),
    name: schemaString(value, "name", "道具"),
  };
}

function parseAssetList<T extends SeriesAssetFields>(
  value: unknown,
  expectedSeriesId: string,
  label: string,
  parseItem: (item: unknown) => T,
): T[] {
  if (!Array.isArray(value)) {
    throw new InvalidResponseError(`服务器返回的${label}列表格式无效。`);
  }
  const assets = value.map(parseItem);
  const ids = new Set<string>();
  for (const asset of assets) {
    if (asset.series_id !== expectedSeriesId) {
      throw new InvalidResponseError(`服务器返回了不属于当前剧集的${label}。`);
    }
    if (ids.has(asset.id)) {
      throw new InvalidResponseError(`服务器返回了重复的${label}身份。`);
    }
    ids.add(asset.id);
  }
  return assets;
}

export function parseCharacterList(value: unknown, expectedSeriesId: string): Character[] {
  return parseAssetList(value, expectedSeriesId, "角色", parseCharacter);
}

export function parseSceneList(value: unknown, expectedSeriesId: string): Scene[] {
  return parseAssetList(value, expectedSeriesId, "场景", parseScene);
}

export function parsePropList(value: unknown, expectedSeriesId: string): Prop[] {
  return parseAssetList(value, expectedSeriesId, "道具", parseProp);
}

export function parseLoginResponse(value: unknown): { token: string; user: User } {
  if (!isRecord(value)) {
    throw new InvalidResponseError("服务器返回的登录信息格式无效。");
  }
  const token = requiredString(value, "access_token", "登录响应");
  if (value.token_type !== undefined && (
    typeof value.token_type !== "string" || value.token_type.toLowerCase() !== "bearer"
  )) {
    throw new InvalidResponseError("服务器返回了不支持的 token_type。");
  }
  return { token, user: parseUser(value.user) };
}

export function parseErrorDetail(value: unknown): string | null {
  if (!isRecord(value) || !("detail" in value)) {
    return null;
  }
  const detail = value.detail;
  if (typeof detail === "string") {
    return detail.slice(0, 400);
  }
  if (Array.isArray(detail)) {
    const messages = detail.flatMap((item) => {
      if (!isRecord(item) || typeof item.msg !== "string") {
        return [];
      }
      return [item.msg.slice(0, 160)];
    });
    return messages.length > 0 ? messages.join("；").slice(0, 400) : null;
  }
  return null;
}
