import {
  InvalidResponseError,
  parseCharacterList,
  parsePropList,
  parseSceneList,
  type Character,
  type Prop,
  type Scene,
} from "./contracts";

export interface CharacterCreateInput {
  name: string;
  gender?: string;
  age?: string;
  role?: string;
  appearance?: string;
  description?: string;
  image_url?: string;
  audio_url?: string;
  voice_ref?: string;
}

export interface CharacterUpdateInput {
  name?: string;
  gender?: string;
  age?: string;
  role?: string;
  appearance?: string;
  description?: string;
  image_url?: string;
  audio_url?: string | null;
  voice_ref?: string;
  aliases?: string[];
}

export interface SceneCreateInput {
  title: string;
  description?: string;
  image_url?: string;
}

export interface SceneUpdateInput {
  title?: string;
  description?: string;
  image_url?: string;
  aliases?: string[];
}

export interface PropCreateInput {
  name: string;
  description?: string;
  image_url?: string;
}

export interface PropUpdateInput {
  name?: string;
  description?: string;
  image_url?: string;
  aliases?: string[];
}

export type ManagedAsset = Character | Scene | Prop;

export function parseCreatedCharacter(value: unknown, expectedSeriesId: string): Character {
  return parseSingleAsset(parseCharacterList(value === null ? null : [value], expectedSeriesId), "角色");
}

export function parseUpdatedCharacter(
  value: unknown,
  expectedSeriesId: string,
  expectedAssetId: string,
): Character {
  return parseExpectedIdentity(
    parseCharacterList(value === null ? null : [value], expectedSeriesId),
    expectedAssetId,
    "角色",
  );
}

export function parseCreatedScene(value: unknown, expectedSeriesId: string): Scene {
  return parseSingleAsset(parseSceneList(value === null ? null : [value], expectedSeriesId), "场景");
}

export function parseUpdatedScene(
  value: unknown,
  expectedSeriesId: string,
  expectedAssetId: string,
): Scene {
  return parseExpectedIdentity(
    parseSceneList(value === null ? null : [value], expectedSeriesId),
    expectedAssetId,
    "场景",
  );
}

export function parseCreatedProp(value: unknown, expectedSeriesId: string): Prop {
  return parseSingleAsset(parsePropList(value === null ? null : [value], expectedSeriesId), "道具");
}

export function parseUpdatedProp(
  value: unknown,
  expectedSeriesId: string,
  expectedAssetId: string,
): Prop {
  return parseExpectedIdentity(
    parsePropList(value === null ? null : [value], expectedSeriesId),
    expectedAssetId,
    "道具",
  );
}

function parseSingleAsset<T extends ManagedAsset>(items: T[], label: string): T {
  const item = items[0];
  if (item === undefined || items.length !== 1) {
    throw new InvalidResponseError(`服务器返回的${label}格式无效。`);
  }
  return item;
}

function parseExpectedIdentity<T extends ManagedAsset>(
  items: T[],
  expectedAssetId: string,
  label: string,
): T {
  const item = parseSingleAsset(items, label);
  if (item.id !== expectedAssetId) {
    throw new InvalidResponseError(`服务器返回了身份不匹配的${label}。`);
  }
  return item;
}
